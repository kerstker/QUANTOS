"""Load-bearing provenance for every selection-bearing search candidate."""

from __future__ import annotations

import json
import sqlite3

import pytest

import services.research.alpha_expression as ax
import services.research.alpha_factory as alpha_factory
from services.intelligence.alpha_research import run_alpha_factory as composed_run_alpha_factory
from quantos.contracts.alpha import AlphaDefinition
from quantos.contracts.hashing import short_hash, stable_hash
from services.intelligence import research_session as intelligence_session
from services.research.experiment_registry import store as experiment_store
from services.research.experiment_registry.registry import ExperimentRegistry
from services.research.experiment_registry.store import (
    ExperimentStoreError,
    SqliteExperimentStore,
)
from tools.search_trial_registry import (
    field_panel_dataset_version,
    production_experiment_registry,
)


FIELDS = ["close", "funding"]
WINDOWS = [3, 5]


def _panel() -> ax.FieldPanel:
    close = [[100.0 + t + i for i in range(6)] for t in range(30)]
    funding = [[0.001 * (i - 2.5) for i in range(6)] for _ in range(30)]
    return ax.FieldPanel(
        [f"C{i}" for i in range(6)],
        list(range(30)),
        {"close": close, "funding": funding},
    )


def _registry(path: str = ":memory:", *, create: bool = True) -> ExperimentRegistry:
    return ExperimentRegistry(SqliteExperimentStore(path, create=create))


def test_search_attempt_and_candidates_are_append_only_and_queryable() -> None:
    registry = _registry()
    assert registry.begin_search_attempt(
        search_id="search-1",
        search_kind="ALPHA_FACTORY",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=10,
    )
    assert registry.register_search_candidates(
        "search-1", ["hash-a", "hash-b"], registered_at=11
    ) == 2
    assert registry.finish_search_attempt(
        "search-1", status="COMPLETED", recorded_at=12
    )

    assert [row["status"] for row in registry.search_attempt_events("search-1")] == [
        "STARTED",
        "COMPLETED",
    ]
    assert [row["config_hash"] for row in registry.search_candidates("search-1")] == [
        "hash-a",
        "hash-b",
    ]
    assert set(registry._store.all_trial_hashes()) == {"hash-a", "hash-b"}
    assert registry.search_history_valid() is True

    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        registry._store._conn.execute(
            "UPDATE search_attempt_events SET status='FAILED' WHERE search_id='search-1'"
        )
    # The direct SQL attack above is deliberately outside the store API and may
    # leave SQLite inside a caller-owned transaction after the trigger aborts.
    # Release that caller transaction before testing the store's terminal-state
    # invariant; public mutators correctly refuse to interfere with it.
    registry._store._conn.rollback()
    with pytest.raises(ExperimentStoreError, match="terminal"):
        registry.register_search_candidates(
            "search-1", ["hash-c"], registered_at=13
        )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        registry._store._conn.execute(
            "DELETE FROM trials WHERE config_hash='hash-a'"
        )

    registry._store._conn.execute("DROP TRIGGER trials_no_delete")
    assert registry.search_history_valid() is False
    registry._store._conn.execute(
        "DELETE FROM trials WHERE config_hash='hash-a'"
    )
    registry._store._conn.commit()
    assert registry.search_history_valid() is False


def test_failed_search_retains_started_candidates_and_failure() -> None:
    registry = _registry()
    registry.begin_search_attempt(
        search_id="search-failed",
        search_kind="ALPHA_FACTORY",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=20,
    )
    registry.register_search_candidates(
        "search-failed", ["hash-a"], registered_at=21
    )
    registry.finish_search_attempt(
        "search-failed", status="FAILED", recorded_at=22
    )

    assert [row["status"] for row in registry.search_attempt_events("search-failed")] == [
        "STARTED",
        "FAILED",
    ]
    assert registry.search_candidates("search-failed")[0]["status"] == "REGISTERED"


def test_search_schema_migrates_an_existing_valid_database(tmp_path) -> None:
    path = tmp_path / "legacy-experiments.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(experiment_store._SCHEMA)
        connection.execute(
            "INSERT INTO trials(config_hash, first_seen_ts) "
            "VALUES ('legacy-hash', 1)"
        )

    registry = _registry(str(path), create=False)
    registry.begin_search_attempt(
        search_id="migrated-search",
        search_kind="ALPHA_FACTORY",
        source="tests.migration",
        dataset_version="dataset-v1",
        recorded_at=30,
    )
    registry.register_search_candidates(
        "migrated-search", ["new-hash"], registered_at=31
    )
    registry.finish_search_attempt(
        "migrated-search", status="COMPLETED", recorded_at=32
    )

    assert set(registry._store.all_trial_hashes()) == {"legacy-hash", "new-hash"}
    assert registry.search_history_valid() is True
    assert registry._store._conn.execute("PRAGMA user_version").fetchone()[0] >= 1


def test_production_registry_missing_path_fails_without_creating_file(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    expected = tmp_path / "data" / "cache" / "experiment_registry.db"

    with pytest.raises(ExperimentStoreError, match="missing"):
        with production_experiment_registry():
            pass

    assert not expected.exists()


def test_field_panel_dataset_version_binds_exact_inputs() -> None:
    first = _panel()
    same = _panel()
    changed = _panel()
    changed.matrices["funding"][5][2] += 0.0001

    assert field_panel_dataset_version(first) == field_panel_dataset_version(same)
    assert field_panel_dataset_version(first) != field_panel_dataset_version(changed)


def test_alpha_factory_requires_persistence_or_explicit_ephemeral() -> None:
    with pytest.raises(RuntimeError, match="INVALIDATED_PERP_LEDGER_V1"):
        composed_run_alpha_factory(
            _panel(),
            FIELDS,
            WINDOWS,
            generations=1,
            population_size=4,
            elite_k=2,
            dataset_version="dataset-v1",
            timestamp=40,
        )
    with pytest.raises(ValueError, match="search_registry.*ephemeral"):
        composed_run_alpha_factory(
            _panel(), FIELDS, WINDOWS, generations=1, population_size=4,
            elite_k=2, dataset_version="dataset-v1", timestamp=40,
            diagnostic_only=True,
        )

    result = composed_run_alpha_factory(
        _panel(),
        FIELDS,
        WINDOWS,
        generations=1,
        population_size=4,
        elite_k=2,
        dataset_version="dataset-v1",
        timestamp=40,
        ephemeral=True,
        diagnostic_only=True,
    )
    assert result["n_evaluated"] >= 1


def test_alpha_factory_persists_candidates_before_scoring(monkeypatch) -> None:
    registry = _registry()
    original = alpha_factory._score_expr
    observed_candidate_counts: list[int] = []

    def asserting_score(*args, **kwargs):
        events = registry.search_attempt_events("factory-search")
        candidates = registry.search_candidates("factory-search")
        assert [row["status"] for row in events] == ["STARTED"]
        assert candidates
        assert all(row["status"] == "REGISTERED" for row in candidates)
        observed_candidate_counts.append(len(candidates))
        return original(*args, **kwargs)

    monkeypatch.setattr(alpha_factory, "_score_expr", asserting_score)
    result = composed_run_alpha_factory(
        _panel(),
        FIELDS,
        WINDOWS,
        generations=1,
        population_size=4,
        elite_k=2,
        dataset_version="dataset-v1",
        timestamp=50,
        search_registry=registry,
        search_id="factory-search",
        search_kind="ALPHA_FACTORY",
        search_source="tests.alpha_factory",
        diagnostic_only=True,
    )

    assert observed_candidate_counts
    assert [row["status"] for row in registry.search_attempt_events("factory-search")] == [
        "STARTED",
        "COMPLETED",
    ]
    assert len(registry.search_candidates("factory-search")) == result["n_evaluated"]
    assert registry._store.trial_count() == result["n_evaluated"]
    assert result["recorded_run_ids"]
    assert {
        row["run_id"] for row in registry.search_evaluations("factory-search")
    } == set(result["recorded_run_ids"])
    definitions_by_alpha_id = {
        row["alpha_id"]: AlphaDefinition.model_validate_json(
            row["definition_json"]
        )
        for row in registry._store.all_alphas()
    }
    for link in registry.search_evaluations("factory-search"):
        evaluation_row = registry.get_evaluation(link["run_id"])
        assert link["config_hash"] == definitions_by_alpha_id[
            evaluation_row["alpha_id"]
        ].definition_hash


def test_alpha_factory_retains_failed_attempt_when_scoring_raises(monkeypatch) -> None:
    registry = _registry()

    def fail_score(*_args, **_kwargs):
        assert registry.search_candidates("factory-failed")
        raise RuntimeError("scorer failed")

    monkeypatch.setattr(alpha_factory, "_score_expr", fail_score)
    with pytest.raises(RuntimeError, match="scorer failed"):
        composed_run_alpha_factory(
            _panel(),
            FIELDS,
            WINDOWS,
            generations=1,
            population_size=4,
            elite_k=2,
            dataset_version="dataset-v1",
            timestamp=60,
            search_registry=registry,
            search_id="factory-failed",
            search_kind="ALPHA_FACTORY",
            search_source="tests.alpha_factory",
            diagnostic_only=True,
        )

    assert [row["status"] for row in registry.search_attempt_events("factory-failed")] == [
        "STARTED",
        "FAILED",
    ]
    assert registry.search_candidates("factory-failed")


def test_trial_count_persists_across_sessions(tmp_path) -> None:
    """Cross-session persistence: trial count from session 1 survives into session 2."""
    db_path = str(tmp_path / "registry.db")

    # Session 1: register candidates from first search
    registry1 = ExperimentRegistry(SqliteExperimentStore(db_path, create=True))
    registry1.begin_search_attempt(
        search_id="session-1-search",
        search_kind="ALPHA_FACTORY",
        source="tests.session1",
        dataset_version="dataset-v1",
        recorded_at=100,
    )
    registry1.register_search_candidates(
        "session-1-search", ["hash-1", "hash-2", "hash-3"], registered_at=101
    )
    registry1.finish_search_attempt(
        "session-1-search", status="COMPLETED", recorded_at=102
    )
    session1_count = registry1._store.trial_count()
    assert session1_count == 3
    registry1._store.close()

    # Session 2: new registry loads from same DB, trial count is retained
    registry2 = ExperimentRegistry(SqliteExperimentStore(db_path, create=False))
    session2_count = registry2._store.trial_count()
    assert session2_count == session1_count  # Persisted across sessions

    # Session 2 adds more trials, cumulative count increases
    registry2.begin_search_attempt(
        search_id="session-2-search",
        search_kind="ALPHA_FACTORY",
        source="tests.session2",
        dataset_version="dataset-v1",
        recorded_at=200,
    )
    registry2.register_search_candidates(
        "session-2-search", ["hash-4", "hash-5"], registered_at=201
    )
    registry2.finish_search_attempt(
        "session-2-search", status="COMPLETED", recorded_at=202
    )
    cumulative_count = registry2._store.trial_count()
    assert cumulative_count == 5  # 3 from session 1 + 2 from session 2

    # Verify search history for both sessions is intact
    assert [row["status"] for row in registry2.search_attempt_events("session-1-search")] == [
        "STARTED", "COMPLETED"
    ]
    assert [row["status"] for row in registry2.search_attempt_events("session-2-search")] == [
        "STARTED", "COMPLETED"
    ]
    assert len(registry2.search_candidates("session-1-search")) == 3
    assert len(registry2.search_candidates("session-2-search")) == 2
    registry2._store.close()


def test_search_identity_is_stable_across_sessions_and_binds_evaluations(tmp_path) -> None:
    """A durable search has one canonical identity independent of process state."""
    db_path = str(tmp_path / "identity-ledger.db")
    registry1 = ExperimentRegistry(SqliteExperimentStore(db_path, create=True))
    registry1.begin_search_attempt(
        search_id="identity-search",
        search_kind="ALPHA_FACTORY",
        source="tests.identity",
        dataset_version="dataset-v1",
        recorded_at=100,
    )
    registry1.register_search_candidates(
        "identity-search", ["candidate-a"], registered_at=100
    )
    identity1 = registry1.search_identity("identity-search")
    assert identity1["search_id"] == "identity-search"
    assert identity1["search_kind"] == "ALPHA_FACTORY"
    assert identity1["source"] == "tests.identity"
    assert identity1["dataset_version"] == "dataset-v1"
    assert len(identity1["identity_hash"]) == 64
    evaluation = {
        "alpha_id": "a1",
        "reproducibility_hash": "repro-identity",
        "verdict": "PASS",
    }
    evaluation_json = json.dumps(evaluation, sort_keys=True, default=str)
    run_id = short_hash(
        {
            "alpha_id": "a1",
            "repro": "repro-identity",
            "dataset_version": "dataset-v1",
        }
    )
    registry1._store.insert_evaluation(
        run_id=run_id,
        alpha_id="a1",
        dataset_version="dataset-v1",
        recorded_ts=100,
        verdict="PASS",
        content_hash=stable_hash(
            {"evaluation": evaluation, "dataset_version": "dataset-v1"}
        ),
        evaluation_json=evaluation_json,
        search_id="identity-search",
        config_hash="candidate-a",
    )
    binding = registry1.evaluation_binding(run_id)
    assert binding is not None
    assert binding["search_identity_hash"] == identity1["identity_hash"]
    registry1._store.close()

    registry2 = ExperimentRegistry(SqliteExperimentStore(db_path, create=False))
    assert registry2.search_identity("identity-search") == identity1
    assert registry2.evaluation_binding(run_id) == binding
    registry2._store.close()


def test_search_identity_fails_closed_when_start_metadata_is_tampered() -> None:
    registry = _registry()
    registry.begin_search_attempt(
        search_id="identity-tamper",
        search_kind="ALPHA_FACTORY",
        source="tests.identity",
        dataset_version="dataset-v1",
        recorded_at=100,
    )
    registry._store._conn.execute(
        "DROP TRIGGER search_attempt_events_no_update"
    )
    registry._store._conn.execute(
        "UPDATE search_attempt_events SET source='tampered' WHERE search_id=?",
        ("identity-tamper",),
    )
    registry._store._conn.commit()
    with pytest.raises(ExperimentStoreError):
        registry.search_identity("identity-tamper")


def test_failed_terminal_write_does_not_mask_the_scorer_error(monkeypatch) -> None:
    registry = _registry()

    def fail_score(*_args, **_kwargs):
        raise RuntimeError("original scorer failure")

    original_finish = registry.finish_search_attempt

    def fail_terminal(search_id, *, status, recorded_at):
        if status == "FAILED":
            raise ExperimentStoreError("terminal persistence failure")
        return original_finish(search_id, status=status, recorded_at=recorded_at)

    monkeypatch.setattr(alpha_factory, "_score_expr", fail_score)
    monkeypatch.setattr(registry, "finish_search_attempt", fail_terminal)
    with pytest.raises(RuntimeError, match="original scorer failure"):
        composed_run_alpha_factory(
            _panel(),
            FIELDS,
            WINDOWS,
            generations=1,
            population_size=4,
            elite_k=2,
            dataset_version="dataset-v1",
            timestamp=70,
            search_registry=registry,
            search_id="terminal-failed",
            search_kind="ALPHA_FACTORY",
            search_source="tests.alpha_factory",
            diagnostic_only=True,
        )


def test_official_research_session_requires_registry_or_explicit_ephemeral(
    monkeypatch,
) -> None:
    with pytest.raises(RuntimeError, match="INVALIDATED_PERP_LEDGER_V1"):
        intelligence_session.run_research_session(store=object())
    with pytest.raises(ValueError, match="registry.*ephemeral"):
        intelligence_session.run_research_session(
            store=object(), diagnostic_only=True
        )

    monkeypatch.setattr(
        intelligence_session,
        "_run_research_session_core",
        lambda *args, **kwargs: {"ephemeral": True, "kwargs": kwargs},
    )
    monkeypatch.setattr(
        intelligence_session, "adapt_research_market_data", lambda value: value
    )
    result = intelligence_session.run_research_session(
        store=object(), ephemeral=True, diagnostic_only=True
    )
    assert result["ephemeral"] is True
    assert "ephemeral" not in result["kwargs"]


def test_official_research_session_persists_actual_oos_access(monkeypatch) -> None:
    registry = _registry()
    definition = AlphaDefinition(
        alpha_id="oos-alpha",
        name="OOS Alpha",
        factor_ids=("close",),
        universe="u",
        params={"operator": "momentum", "window": "5"},
    )

    def core(*args, **kwargs):
        kwargs["trial_recorder"](definition.definition_hash)
        kwargs["trial_provenance_recorder"](definition)
        kwargs["oos_access_recorder"](
            {"WALK_FORWARD_NO_PEEK_SELECTION_OOS": ("index:[20,28)",)}
        )
        return {"oos": {"n_folds": 1}}

    monkeypatch.setattr(intelligence_session, "_run_research_session_core", core)
    monkeypatch.setattr(
        intelligence_session, "adapt_research_market_data", lambda value: value
    )
    result = intelligence_session.run_research_session(
        store=object(),
        instruments=("BTC",),
        event_times=tuple(range(30)),
        registry=registry,
        dataset_version="dataset-v1",
        timestamp=100,
        search_id="oos-search",
        search_kind="GUIDED",
        search_source="tests.oos",
        diagnostic_only=True,
    )

    assert result["oos"]["n_folds"] == 1
    trial_id = f"oos-search:{definition.definition_hash}"
    assert registry.trial_oos_access(trial_id)[0]["boundary"] == "index:[20,28)"
    assert registry.trial_oos_access_chain_valid() is True
    assert registry.search_attempt_events("oos-search")[-1]["status"] == "COMPLETED"
