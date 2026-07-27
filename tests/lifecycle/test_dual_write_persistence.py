"""727 C2 — alpha_state lazy migration + catalog dual-write + projection fallback.

Pins the second commit of the three-orthogonal-state-machines plan
(docs/engineering/specs/2026-07-27-three-orthogonal-state-machines-design.md §6):

* the alpha_state table gains the six triple columns lazily, NULL/无 DEFAULT;
* registry save writes the legacy mixed state AND the projected triple in the
  same transaction (dual-write);
* the load path prefers the triple columns and falls back to project(legacy)
  for pre-C2 rows — never fabricating dimensions that were never recorded;
* the legacy read path stays authoritative (zero behavior change in C2).
"""

import json
import sqlite3

import pytest

from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.research import AlphaState
from services.alpha.catalog import AlphaCatalog
from services.lifecycle import (
    LOSSY_DEPLOYMENT,
    DeploymentState,
    HealthReason,
    HealthState,
    ResearchState,
    project,
)
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import (
    ExperimentStoreError,
    SqliteExperimentStore,
)


def _catalog(*states: AlphaState) -> AlphaCatalog:
    c = AlphaCatalog()
    for i, state in enumerate(states):
        aid = f"a{i}"
        c.register(AlphaDefinition(alpha_id=aid, name=aid, factor_ids=(f"f{i}",)))
        c._set_state(aid, state)
    return c


def _reg(store: SqliteExperimentStore) -> ExperimentRegistry:
    return ExperimentRegistry(store)


def _raw_row(store: SqliteExperimentStore, alpha_id: str) -> sqlite3.Row:
    return store._conn.execute(
        "SELECT * FROM alpha_state WHERE alpha_id=?", (alpha_id,)
    ).fetchone()


def test_save_dual_writes_projected_triple_in_same_row():
    store = SqliteExperimentStore(":memory:", create=True)
    reg = _reg(store)
    reg.save(_catalog(AlphaState.PAPER_TRADING, AlphaState.DEGRADED), now=1)

    row0 = _raw_row(store, "a0")
    assert row0["state"] == "PAPER_TRADING"
    assert row0["research_state"] == "VALIDATED"
    assert row0["deployment_state"] == "PAPER"
    assert row0["health_state"] == "HEALTHY"
    assert row0["health_reason"] == "NONE"

    row1 = _raw_row(store, "a1")
    assert row1["state"] == "DEGRADED"
    # The projection's honest sentinels are persisted, not smoothed over.
    assert row1["deployment_state"] == "UNKNOWN_LEGACY"
    assert row1["health_state"] == "DEGRADED"
    assert row1["health_reason"] == "UNKNOWN_LEGACY"
    # C2 writes no transition notes yet; the columns exist for C3.
    assert row1["research_reason"] is None
    assert row1["deployment_reason"] is None


def test_round_trip_restores_triple_views_and_legacy_state():
    store = SqliteExperimentStore(":memory:", create=True)
    reg = _reg(store)
    src = _catalog(
        AlphaState.DRAFT,
        AlphaState.VALIDATED,
        AlphaState.PAPER_TRADING,
        AlphaState.PAUSED,
        AlphaState.APPROVED,
        AlphaState.RETIRED,
    )
    reg.save(src, now=1)

    dst = AlphaCatalog()
    reg.load_into(dst)
    for aid in src.all_ids():
        # Legacy read path unchanged and still authoritative.
        assert dst.state(aid) == src.state(aid)
        # Triple view equals the C1 projection, loss markers included.
        assert dst.triple(aid) == project(src.state(aid))
    assert dst.by_deployment(DeploymentState.PAPER) == ["a2"]
    assert dst.by_deployment(DeploymentState.PAUSED) == ["a3"]
    assert dst.by_deployment(DeploymentState.LIVE) == ["a4"]
    assert dst.by_health(HealthState.WATCH) == ["a3"]
    assert dst.research_state("a5") is ResearchState.RESEARCH_RETIRED


def test_pre_c2_null_triple_row_loads_via_projection_with_loss_markers():
    store = SqliteExperimentStore(":memory:", create=True)
    # A legacy-only caller leaves the triple columns NULL (e.g. tools that
    # upsert nothing but the mixed state).
    store.upsert_alpha(
        "legacy",
        json.dumps(
            AlphaDefinition(
                alpha_id="legacy", name="legacy", factor_ids=("f0",)
            ).model_dump()
        ),
        "DEGRADED",
        "[]",
        None,
        None,
    )
    assert _raw_row(store, "legacy")["research_state"] is None

    dst = AlphaCatalog()
    _reg(store).load_into(dst)
    triple = dst.triple("legacy")
    assert triple == project(AlphaState.DEGRADED)
    assert triple.deployment is DeploymentState.UNKNOWN_LEGACY
    assert triple.health.reason is HealthReason.UNKNOWN_LEGACY
    assert LOSSY_DEPLOYMENT in triple.lossy
    # The legacy identity is untouched.
    assert dst.state("legacy") is AlphaState.DEGRADED


def test_migration_is_lazy_idempotent_and_preserves_existing_rows(tmp_path):
    db = tmp_path / "experiment_registry.db"
    # A pre-C2 store: the full current base schema (whose alpha_state still has
    # only the legacy six columns) plus one historical row written without ever
    # running the triple migration.
    seed = SqliteExperimentStore(str(db), create=True)
    old_def = AlphaDefinition(alpha_id="old", name="old", factor_ids=("f0",))
    seed._conn.execute(
        "INSERT INTO alpha_state(alpha_id, definition_json, state, returns_json,"
        " sharpe, pvalue) VALUES (?, ?, ?, ?, ?, ?)",
        ("old", json.dumps(old_def.model_dump()), "PAUSED", "[]", None, None),
    )
    seed._conn.commit()
    seed._conn.close()

    store = SqliteExperimentStore(str(db), create=False)
    reg = _reg(store)
    # Read BEFORE any write: no migration has run, the row loads via projection.
    assert "health_state" not in store._alpha_state_columns()
    pre = AlphaCatalog()
    reg.load_into(pre)
    assert pre.triple("old") == project(AlphaState.PAUSED)

    # First write triggers the lazy migration; the historical row keeps NULLs.
    reg.save(_catalog(AlphaState.TESTING), now=1)
    assert "health_state" in store._alpha_state_columns()
    assert _raw_row(store, "old")["research_state"] is None
    assert _raw_row(store, "a0")["research_state"] == "TESTING"

    # Second write: migration is a no-op, nothing changes.
    reg.save(_catalog(AlphaState.VALIDATED), now=2)
    assert _raw_row(store, "old")["research_state"] is None

    # And the historical row still loads through the projection afterwards.
    post = AlphaCatalog()
    reg.load_into(post)
    assert post.triple("old") == project(AlphaState.PAUSED)
    assert post.deployment_state("a0") is DeploymentState.OFFLINE


def test_corrupt_triple_column_fails_closed_on_load():
    store = SqliteExperimentStore(":memory:", create=True)
    reg = _reg(store)
    reg.save(_catalog(AlphaState.VALIDATED), now=1)
    store._conn.execute(
        "UPDATE alpha_state SET research_state='NOT_A_STATE' WHERE alpha_id='a0'"
    )
    store._conn.commit()
    with pytest.raises(ExperimentStoreError, match="corrupt alpha_state row"):
        reg.load_into(AlphaCatalog())
    with pytest.raises(ExperimentStoreError, match="corrupt alpha_state row"):
        reg.load_for_evaluation(AlphaCatalog())


def test_health_reason_none_round_trips_as_enum_not_null():
    store = SqliteExperimentStore(":memory:", create=True)
    reg = _reg(store)
    reg.save(_catalog(AlphaState.VALIDATED), now=1)
    dst = AlphaCatalog()
    reg.load_into(dst)
    verdict = dst.health("a0")
    assert verdict.state is HealthState.HEALTHY
    assert verdict.reason is HealthReason.NONE
