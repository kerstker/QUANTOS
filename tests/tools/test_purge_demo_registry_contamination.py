"""TDD for tools/purge_demo_registry_contamination.py — the one-off fix for the 2026-07-09
experiment_registry.db contamination bug (60 legacy demo trials inflating global_trial_count()/
catalog_var_sr() for the real carry_composite/crowd_contra registration)."""
import json
import sqlite3

import pytest

from quantos.contracts.alpha import AlphaDefinition
from services.research.experiment_registry.store import SqliteExperimentStore
from tools.purge_demo_registry_contamination import apply_purge, plan_purge


class _LegacyStoreFixture:
    """Minimal pre-provenance writer; never invokes the current v2 migrator."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path)
        self._conn.executescript(
            """
            CREATE TABLE trials (
                config_hash TEXT PRIMARY KEY,
                first_seen_ts INTEGER NOT NULL
            );
            CREATE TABLE alpha_state (
                alpha_id TEXT PRIMARY KEY,
                definition_json TEXT NOT NULL,
                state TEXT NOT NULL,
                returns_json TEXT NOT NULL,
                sharpe REAL,
                pvalue REAL
            );
            CREATE TABLE evaluations (
                run_id TEXT PRIMARY KEY,
                alpha_id TEXT NOT NULL,
                dataset_version TEXT NOT NULL,
                recorded_ts INTEGER NOT NULL,
                verdict TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                evaluation_json TEXT NOT NULL
            );
            PRAGMA user_version = 0;
            """
        )
        self._conn.commit()

    def add_trials(self, hashes, *, first_seen_ts: int) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO trials(config_hash, first_seen_ts) VALUES (?, ?)",
            ((config_hash, first_seen_ts) for config_hash in hashes),
        )
        self._conn.commit()

    def upsert_alpha(
        self,
        alpha_id: str,
        definition_json: str,
        state: str,
        returns_json: str,
        sharpe,
        pvalue,
    ) -> None:
        self._conn.execute(
            "INSERT INTO alpha_state(alpha_id, definition_json, state, "
            "returns_json, sharpe, pvalue) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(alpha_id) DO UPDATE SET "
            "definition_json=excluded.definition_json, state=excluded.state, "
            "returns_json=excluded.returns_json, sharpe=excluded.sharpe, "
            "pvalue=excluded.pvalue",
            (alpha_id, definition_json, state, returns_json, sharpe, pvalue),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _legacy_store(db_path: str) -> _LegacyStoreFixture:
    """Model the pre-provenance database for which the one-off purge existed."""
    return _LegacyStoreFixture(db_path)


def _seed(db_path: str, *, legacy_ids=("h_a", "h_b", "hx_c"), real_ids=("carry_composite",)):
    store = _legacy_store(db_path)
    now = 0
    # distinct `params` per legacy alpha (mirrors the real h_momentum_3/h_momentum_5/... rows,
    # which differ by window) so each gets its own definition_hash, like production.
    for i, aid in enumerate(legacy_ids):
        defn = AlphaDefinition(alpha_id=aid, name=aid, factor_ids=("close",), universe="",
                               params={"window": str(i)})
        store.add_trials([defn.definition_hash], first_seen_ts=now)
        store.upsert_alpha(aid, json.dumps(defn.model_dump()), "TESTING", "[]", None, None)
    for aid in real_ids:
        defn = AlphaDefinition(alpha_id=aid, name=aid, factor_ids=("funding",), universe="cs_majors")
        store.add_trials([defn.definition_hash], first_seen_ts=now)
        store.upsert_alpha(aid, json.dumps(defn.model_dump()), "TESTING", "[]", None, None)
    store.close()


def test_plan_purge_separates_legacy_from_real_and_never_mutates(tmp_path):
    db = str(tmp_path / "reg.db")
    _seed(db)
    legacy_ids, real_ids, legacy_only_hashes = plan_purge(db)
    assert legacy_ids == ["h_a", "h_b", "hx_c"]
    assert real_ids == ["carry_composite"]
    assert len(legacy_only_hashes) == 3
    # a second call gives an identical plan — plan_purge is read-only
    assert plan_purge(db) == (legacy_ids, real_ids, legacy_only_hashes)


def test_apply_purge_removes_only_legacy_rows_and_their_exclusive_trial_hashes(tmp_path):
    db = str(tmp_path / "reg.db")
    _seed(db)
    legacy_ids, _, _ = plan_purge(db)
    result = apply_purge(db, legacy_ids)
    assert result == {"alpha_state_deleted": 3, "evaluations_deleted": 0, "trials_deleted": 3}

    conn = sqlite3.connect(db)
    remaining_alphas = [r[0] for r in conn.execute("SELECT alpha_id FROM alpha_state")]
    remaining_trial_count = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
    conn.close()
    assert remaining_alphas == ["carry_composite"]
    assert remaining_trial_count == 1


def test_apply_purge_never_deletes_a_trial_hash_shared_with_a_real_alpha(tmp_path):
    """Pathological but must-not-corrupt case: a legacy and real alpha coincidentally sharing a
    definition_hash would fail plan_purge's collision check before any deletion happens — this
    test constructs that exact collision and asserts the whole operation refuses to proceed."""
    db = str(tmp_path / "reg.db")
    store = _legacy_store(db)
    shared = AlphaDefinition(alpha_id="h_shared", name="x", factor_ids=("close",), universe="cs_majors")
    real_same_hash = AlphaDefinition(alpha_id="carry_composite", name="y",
                                     factor_ids=shared.factor_ids, universe=shared.universe,
                                     params=shared.params)
    assert shared.definition_hash == real_same_hash.definition_hash   # sanity: the fixture collides
    store.add_trials([shared.definition_hash], first_seen_ts=0)
    store.upsert_alpha("h_shared", json.dumps(shared.model_dump()), "TESTING", "[]", None, None)
    store.upsert_alpha("carry_composite", json.dumps(real_same_hash.model_dump()), "TESTING", "[]", None, None)
    store.close()

    with pytest.raises(ValueError, match="refusing to purge"):
        plan_purge(db)


def test_apply_purge_deletes_evaluation_artifacts_for_legacy_alphas(tmp_path):
    db = str(tmp_path / "reg.db")
    _seed(db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO evaluations(run_id, alpha_id, dataset_version, recorded_ts, verdict, "
                "content_hash, evaluation_json) VALUES ('r1','h_a','v1',0,'FAIL','ch','{}')")
    conn.commit()
    conn.close()

    legacy_ids, _, _ = plan_purge(db)
    result = apply_purge(db, legacy_ids)
    assert result["evaluations_deleted"] == 1

    conn = sqlite3.connect(db)
    remaining = conn.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
    conn.close()
    assert remaining == 0


def test_apply_purge_is_a_noop_on_empty_legacy_list(tmp_path):
    db = str(tmp_path / "reg.db")
    _seed(db, legacy_ids=(), real_ids=("carry_composite",))
    result = apply_purge(db, [])
    assert result == {"alpha_state_deleted": 0, "evaluations_deleted": 0, "trials_deleted": 0}


def test_plan_purge_refuses_when_an_orphan_trial_shares_a_legacy_hash(tmp_path):
    """2026-07-09 review finding: a `trials` row with NO alpha_state row at all (legacy or real)
    — e.g. a real out-of-gate search candidate registered via register_trial() directly, per
    CatalogSnapshot's own 'trials is a superset' docstring — that happens to share a legacy
    alpha's definition_hash must not be silently swept up. plan_purge must fail closed rather than
    risk deleting a real orphan trial's hash just because it coincides with a legacy one."""
    db = str(tmp_path / "reg.db")
    store = _legacy_store(db)
    legacy = AlphaDefinition(alpha_id="h_a", name="h_a", factor_ids=("close",), universe="",
                             params={"window": "3"})
    store.upsert_alpha("h_a", json.dumps(legacy.model_dump()), "TESTING", "[]", None, None)
    # the legacy alpha's own hash IS in trials (normal), PLUS an unrelated extra hash that has NO
    # alpha_state row of any kind — the orphan.
    store.add_trials([legacy.definition_hash, "orphan-hash-no-alpha-state-row"], first_seen_ts=0)
    store.close()

    with pytest.raises(ValueError, match="orphan trials"):
        plan_purge(db)


def test_plan_purge_succeeds_when_every_trial_hash_is_explained_by_alpha_state(tmp_path):
    """The common, expected case (and the actual shape of the real production db at purge time,
    per the 2026-07-09 review's verification: trials count == alpha_state count, 1:1, zero
    orphans) — plan_purge must NOT spuriously refuse when there is nothing ambiguous."""
    db = str(tmp_path / "reg.db")
    _seed(db)   # every trial hash here traces to exactly one alpha_state row
    legacy_ids, real_ids, legacy_only_hashes = plan_purge(db)
    assert legacy_ids == ["h_a", "h_b", "hx_c"]
    assert len(legacy_only_hashes) == 3


def test_plan_purge_is_retired_after_search_provenance_migration(tmp_path):
    db = str(tmp_path / "reg.db")
    SqliteExperimentStore(db, create=True).close()

    with pytest.raises(ValueError, match="append-only search provenance"):
        plan_purge(db)
