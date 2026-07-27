"""Audit roadmap — sqlite experiment store: dedup'd trials, write-once evaluations,
fail-closed on an uninitialized/corrupt store (never reads as empty)."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest
import services.research.experiment_registry.store as experiment_store
from services.research.experiment_registry.store import (
    SqliteExperimentStore, ExperimentStoreError, ImmutableArtifactError,
)
from services.research.ports.persistence import EvaluationAuditIntent


def _store():
    return SqliteExperimentStore(":memory:", create=True)


def _audit_intent(run_id: str, *, timestamp: int) -> EvaluationAuditIntent:
    return EvaluationAuditIntent(
        event_id=f"exp-{run_id}",
        actor="experiment-registry",
        action="EXPERIMENT_RECORDED",
        timestamp=timestamp,
        target="a1",
        metadata={"run_id": run_id, "content_hash": f"content-{run_id}"},
    )


def test_trials_dedup_by_hash_and_count():
    s = _store()
    s.add_trials(["h1", "h2"], first_seen_ts=1)
    s.add_trials(["h2", "h3"], first_seen_ts=2)   # h2 already present
    assert s.trial_count() == 3
    assert sorted(s.all_trial_hashes()) == ["h1", "h2", "h3"]


def test_evaluation_is_write_once():
    s = _store()
    s.insert_evaluation(run_id="r1", alpha_id="a1", dataset_version="v1",
                        recorded_ts=1, verdict="PASS", content_hash="c1",
                        evaluation_json='{"x":1}')
    s.insert_evaluation(run_id="r1", alpha_id="a1", dataset_version="v1",
                        recorded_ts=1, verdict="PASS", content_hash="c1",
                        evaluation_json='{"x":1}')          # identical -> no-op
    assert s.get_evaluation("r1")["content_hash"] == "c1"
    with pytest.raises(ImmutableArtifactError):
        s.insert_evaluation(run_id="r1", alpha_id="a1", dataset_version="v1",
                            recorded_ts=2, verdict="FAIL", content_hash="c2",
                            evaluation_json='{"x":2}')       # conflicting -> raise
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        s._conn.execute(
            "UPDATE evaluations SET verdict='FAIL' WHERE run_id='r1'"
        )


def test_evaluation_catalog_rejects_conflicting_definition_for_alpha_id():
    s = _store()
    base_row = {
        "alpha_id": "a1",
        "definition_json": '{"alpha_id":"a1","name":"original"}',
        "state": "TESTING",
        "returns_json": "[]",
        "sharpe": None,
        "pvalue": None,
    }
    s.save_evaluation_catalog_snapshot(
        config_hashes=("h1",),
        alphas=(base_row,),
        first_seen_ts=1,
    )

    conflicting_row = {
        **base_row,
        "definition_json": '{"alpha_id":"a1","name":"conflict"}',
    }
    with pytest.raises(ImmutableArtifactError, match="different definition"):
        s.save_evaluation_catalog_snapshot(
            config_hashes=("h2",),
            alphas=(conflicting_row,),
            first_seen_ts=2,
        )


def test_uninitialized_store_fails_closed(tmp_path):
    # a path with no schema (create=False) must RAISE on read, never read as empty
    p = str(tmp_path / "empty.db")
    sqlite3.connect(p).close()                               # a file, but no tables
    s = SqliteExperimentStore(p, create=False)
    with pytest.raises(ExperimentStoreError):
        s.trial_count()


def test_created_empty_store_reads_zero_legitimately():
    s = _store()                                             # create=True -> schema exists
    assert s.trial_count() == 0 and s.all_alphas() == []


def _create_legacy_store(path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE trials(
                config_hash TEXT PRIMARY KEY,
                first_seen_ts INTEGER NOT NULL
            );
            CREATE TABLE alpha_state(
                alpha_id TEXT PRIMARY KEY,
                definition_json TEXT NOT NULL,
                state TEXT NOT NULL,
                returns_json TEXT NOT NULL,
                sharpe REAL,
                pvalue REAL
            );
            CREATE TABLE evaluations(
                run_id TEXT PRIMARY KEY,
                alpha_id TEXT NOT NULL,
                dataset_version TEXT NOT NULL,
                recorded_ts INTEGER NOT NULL,
                verdict TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                evaluation_json TEXT NOT NULL
            );
            """
        )


def _create_v1_search_store(path) -> None:
    """Build the deployed v1 search-ledger shape without using v2 DDL."""
    start_hash = experiment_store._search_event_hash(
        search_id="v1-search",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        status="STARTED",
        recorded_at=100,
        prev_event_hash="",
    )
    terminal_hash = experiment_store._search_event_hash(
        search_id="v1-search",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        status="COMPLETED",
        recorded_at=120,
        prev_event_hash=start_hash,
    )
    candidate_hash = experiment_store._search_candidate_hash(
        search_id="v1-search",
        config_hash="v1-candidate",
        status="REGISTERED",
        registered_at=110,
        prev_candidate_hash="",
    )
    with sqlite3.connect(path) as connection:
        connection.executescript(experiment_store._SCHEMA)
        connection.executescript(
            """
            CREATE TABLE search_attempt_events (
                event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                search_id TEXT NOT NULL,
                search_kind TEXT NOT NULL,
                source TEXT NOT NULL,
                dataset_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('STARTED','FAILED','COMPLETED')),
                recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0),
                prev_event_hash TEXT NOT NULL,
                event_hash TEXT UNIQUE NOT NULL
            );
            CREATE INDEX search_attempt_events_by_search
            ON search_attempt_events(search_id,event_seq);
            CREATE TABLE search_candidates (
                candidate_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                search_id TEXT NOT NULL,
                config_hash TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status = 'REGISTERED'),
                registered_at INTEGER NOT NULL CHECK(registered_at >= 0),
                prev_candidate_hash TEXT NOT NULL,
                candidate_hash TEXT UNIQUE NOT NULL,
                UNIQUE(search_id,config_hash)
            );
            CREATE INDEX search_candidates_by_search
            ON search_candidates(search_id,candidate_seq);
            CREATE TRIGGER search_attempt_events_no_update
            BEFORE UPDATE ON search_attempt_events
            BEGIN SELECT RAISE(ABORT, 'search_attempt_events is append-only'); END;
            CREATE TRIGGER search_attempt_events_no_delete
            BEFORE DELETE ON search_attempt_events
            BEGIN SELECT RAISE(ABORT, 'search_attempt_events is append-only'); END;
            CREATE TRIGGER search_candidates_no_update
            BEFORE UPDATE ON search_candidates
            BEGIN SELECT RAISE(ABORT, 'search_candidates is append-only'); END;
            CREATE TRIGGER search_candidates_no_delete
            BEFORE DELETE ON search_candidates
            BEGIN SELECT RAISE(ABORT, 'search_candidates is append-only'); END;
            CREATE TRIGGER trials_no_update BEFORE UPDATE ON trials
            BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;
            CREATE TRIGGER trials_no_delete BEFORE DELETE ON trials
            BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;
            CREATE TRIGGER evaluations_no_update BEFORE UPDATE ON evaluations
            BEGIN SELECT RAISE(ABORT, 'evaluations is append-only'); END;
            CREATE TRIGGER evaluations_no_delete BEFORE DELETE ON evaluations
            BEGIN SELECT RAISE(ABORT, 'evaluations is append-only'); END;
            PRAGMA user_version = 1;
            """
        )
        connection.execute(
            "INSERT INTO trials(config_hash,first_seen_ts) VALUES (?,?)",
            ("v1-candidate", 110),
        )
        connection.executemany(
            "INSERT INTO search_attempt_events"
            " (search_id,search_kind,source,dataset_version,status,recorded_at,"
            " prev_event_hash,event_hash) VALUES (?,?,?,?,?,?,?,?)",
            (
                (
                    "v1-search",
                    "TEST",
                    "tests.research",
                    "dataset-v1",
                    "STARTED",
                    100,
                    "",
                    start_hash,
                ),
                (
                    "v1-search",
                    "TEST",
                    "tests.research",
                    "dataset-v1",
                    "COMPLETED",
                    120,
                    start_hash,
                    terminal_hash,
                ),
            ),
        )
        connection.execute(
            "INSERT INTO search_candidates"
            " (search_id,config_hash,status,registered_at,prev_candidate_hash,"
            " candidate_hash) VALUES (?,?,?,?,?,?)",
            (
                "v1-search",
                "v1-candidate",
                "REGISTERED",
                110,
                "",
                candidate_hash,
            ),
        )


def _v1_chain_rows(connection) -> tuple[list[tuple], list[tuple]]:
    return (
        [
            tuple(row)
            for row in connection.execute(
                "SELECT search_id,status,recorded_at,prev_event_hash,event_hash"
                " FROM search_attempt_events ORDER BY event_seq"
            )
        ],
        [
            tuple(row)
            for row in connection.execute(
                "SELECT search_id,config_hash,registered_at,prev_candidate_hash,"
                " candidate_hash FROM search_candidates ORDER BY candidate_seq"
            )
        ],
    )


def test_search_schema_migration_fault_rolls_back_partial_changes(
    tmp_path, monkeypatch
):
    path = tmp_path / "legacy.db"
    _create_legacy_store(path)
    store = SqliteExperimentStore(str(path), create=False)

    def fail_mid_migration(stage: str) -> None:
        if stage == "statement:2":
            raise RuntimeError("injected migration failure")

    monkeypatch.setattr(store, "_migration_checkpoint", fail_mid_migration)
    with pytest.raises(ExperimentStoreError, match="migration failed"):
        store.begin_search_attempt(
            search_id="faulted",
            search_kind="TEST",
            source="tests.research",
            dataset_version="dataset-v1",
            recorded_at=10,
        )

    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 0
    search_objects = store._conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE name LIKE 'search_%' OR name LIKE 'trials_no_%' "
        "OR name LIKE 'evaluations_no_%'"
    ).fetchall()
    assert search_objects == []

    monkeypatch.setattr(store, "_migration_checkpoint", lambda _stage: None)
    assert store.begin_search_attempt(
        search_id="after-retry",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=10,
    )
    assert (
        store._conn.execute("PRAGMA user_version").fetchone()[0]
        == experiment_store._SEARCH_SCHEMA_VERSION
    )
    assert store.search_history_valid() is True


def test_real_v1_search_schema_migrates_to_current_without_rewriting_chains(
    tmp_path,
):
    """Pinned to the CONSTANT, not a literal: the assertion under test is
    "migration lands on the current version and rewrites no chain", which must
    keep holding across future schema bumps."""
    path = tmp_path / "schema-v1.db"
    _create_v1_search_store(path)
    store = SqliteExperimentStore(str(path), create=False)
    before = _v1_chain_rows(store._conn)

    store._ensure_search_schema()

    assert (
        store._conn.execute("PRAGMA user_version").fetchone()[0]
        == experiment_store._SEARCH_SCHEMA_VERSION
    )
    assert _v1_chain_rows(store._conn) == before
    assert {
        row["name"]
        for row in store._conn.execute("PRAGMA table_info(search_evaluations)")
    } >= {"search_id", "config_hash", "run_id", "link_hash"}
    assert store.search_history_valid()


def test_v1_migration_fault_is_atomic_and_preserves_existing_chains(
    tmp_path, monkeypatch
):
    path = tmp_path / "schema-v1-fault.db"
    _create_v1_search_store(path)
    store = SqliteExperimentStore(str(path), create=False)
    before_chains = _v1_chain_rows(store._conn)
    before_objects = {
        row["name"]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
    }

    def fail_mid_migration(stage: str) -> None:
        if stage == "statement:6":
            raise RuntimeError("injected search-schema migration failure")

    monkeypatch.setattr(store, "_migration_checkpoint", fail_mid_migration)
    with pytest.raises(ExperimentStoreError, match="migration failed"):
        store._ensure_search_schema()

    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert _v1_chain_rows(store._conn) == before_chains
    assert {
        row["name"]
        for row in store._conn.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
    } == before_objects

    monkeypatch.setattr(store, "_migration_checkpoint", lambda _stage: None)
    store._ensure_search_schema()
    assert (
        store._conn.execute("PRAGMA user_version").fetchone()[0]
        == experiment_store._SEARCH_SCHEMA_VERSION
    )
    assert _v1_chain_rows(store._conn) == before_chains
    assert store.search_history_valid()


def test_stale_started_recovery_is_atomic_idempotent_and_preserves_trials():
    store = _store()
    store.begin_search_attempt(
        search_id="crashed",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=100,
    )
    store.register_search_candidates(
        "crashed", ["candidate-a"], registered_at=110
    )
    store.begin_search_attempt(
        search_id="active",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=180,
    )

    assert store.recover_stale_search_attempts(
        recorded_at=200, timeout_ms=50
    ) == ("crashed",)
    assert store.recover_stale_search_attempts(
        recorded_at=201, timeout_ms=50
    ) == ()
    assert [
        row["status"] for row in store.search_attempt_events("crashed")
    ] == ["STARTED", "FAILED"]
    assert [
        row["status"] for row in store.search_attempt_events("active")
    ] == ["STARTED"]
    assert store.search_candidates("crashed")[0]["config_hash"] == "candidate-a"
    assert store.all_trial_hashes() == ["candidate-a"]
    assert store.search_history_valid() is True


def test_heartbeat_defers_timeout_and_recovery_records_explicit_reason():
    store = _store()
    store.begin_search_attempt(
        search_id="long-running",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=100,
    )
    assert store.heartbeat_search_attempt("long-running", recorded_at=180)
    assert not store.heartbeat_search_attempt("long-running", recorded_at=180)

    assert store.recover_stale_search_attempts(
        recorded_at=200, timeout_ms=50
    ) == ()
    assert store.recover_stale_search_attempts(
        recorded_at=230, timeout_ms=50
    ) == ("long-running",)
    recovery = store.search_attempt_recoveries("long-running")[0]
    assert recovery["reason"] == "STALE_STARTED_TIMEOUT"
    assert recovery["last_activity_at"] == 180
    assert recovery["timeout_ms"] == 50
    assert store.search_attempt_events("long-running")[-1][
        "recovery_reason"
    ] == "STALE_STARTED_TIMEOUT"
    with pytest.raises(ValueError, match="positive"):
        store.recover_stale_search_attempts(recorded_at=231, timeout_ms=0)
    assert store.search_history_valid()


def test_timeout_recovery_crash_rolls_back_terminal_and_reason(monkeypatch):
    store = _store()
    store.begin_search_attempt(
        search_id="crashed-recovery",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=10,
    )

    def fail_after_terminal(stage):
        if stage == "after_terminal":
            raise RuntimeError("injected recovery crash")

    monkeypatch.setattr(store, "_recovery_checkpoint", fail_after_terminal)
    with pytest.raises(RuntimeError, match="injected recovery crash"):
        store.recover_stale_search_attempts(recorded_at=100, timeout_ms=50)
    assert [
        row["status"] for row in store.search_attempt_events("crashed-recovery")
    ] == ["STARTED"]
    assert store.search_attempt_recoveries("crashed-recovery") == []

    monkeypatch.setattr(store, "_recovery_checkpoint", lambda _stage: None)
    assert store.recover_stale_search_attempts(
        recorded_at=100, timeout_ms=50
    ) == ("crashed-recovery",)
    assert store.search_history_valid()


def test_evaluation_search_link_and_audit_intent_commit_atomically(monkeypatch):
    store = _store()
    store.begin_search_attempt(
        search_id="bound-search",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=100,
    )
    store.register_search_candidates(
        "bound-search", ["candidate-a"], registered_at=100
    )
    intent = _audit_intent("run-a", timestamp=100)

    monkeypatch.setattr(
        store,
        "_evaluation_write_checkpoint",
        lambda _stage: (_ for _ in ()).throw(RuntimeError("injected write crash")),
    )
    with pytest.raises(RuntimeError, match="injected write crash"):
        store.insert_evaluation(
            run_id="run-a",
            alpha_id="a1",
            dataset_version="dataset-v1",
            recorded_ts=100,
            verdict="PASS",
            content_hash="content-run-a",
            evaluation_json='{"alpha_id":"a1"}',
            search_id="bound-search",
            config_hash="candidate-a",
            audit_intent=intent,
        )
    assert store.get_evaluation("run-a") is None
    assert store.search_evaluations("bound-search") == []
    assert store.pending_evaluation_audits() == ()

    monkeypatch.setattr(store, "_evaluation_write_checkpoint", lambda _stage: None)
    assert store.insert_evaluation(
        run_id="run-a",
        alpha_id="a1",
        dataset_version="dataset-v1",
        recorded_ts=100,
        verdict="PASS",
        content_hash="content-run-a",
        evaluation_json='{"alpha_id":"a1"}',
        search_id="bound-search",
        config_hash="candidate-a",
        audit_intent=intent,
    )
    assert store.search_evaluations("bound-search")[0]["run_id"] == "run-a"
    assert store.pending_evaluation_audits()[0]["event_id"] == "exp-run-a"
    store.finish_search_attempt(
        "bound-search", status="COMPLETED", recorded_at=100
    )
    assert store.search_history_valid()
    assert store.evaluation_audit_chain_valid()


def test_search_evaluation_requires_exact_registered_config_and_retries_idempotently():
    store = _store()
    store.begin_search_attempt(
        search_id="exact-search",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=100,
    )
    store.register_search_candidates(
        "exact-search", ["candidate-a", "candidate-b"], registered_at=100
    )
    values = {
        "run_id": "run-a",
        "alpha_id": "a1",
        "dataset_version": "dataset-v1",
        "recorded_ts": 100,
        "verdict": "PASS",
        "content_hash": "content-run-a",
        "evaluation_json": '{"alpha_id":"a1"}',
        "search_id": "exact-search",
    }

    with pytest.raises(ValueError, match="supplied together"):
        store.insert_evaluation(**values)
    with pytest.raises(ExperimentStoreError, match="cannot bind"):
        store.insert_evaluation(**values, config_hash="not-registered")
    assert store.get_evaluation("run-a") is None

    assert store.insert_evaluation(**values, config_hash="candidate-a") is True
    assert store.insert_evaluation(**values, config_hash="candidate-a") is False
    links = store.search_evaluations("exact-search")
    assert len(links) == 1
    assert links[0]["config_hash"] == "candidate-a"

    with pytest.raises(ImmutableArtifactError, match="different search binding"):
        store.insert_evaluation(**values, config_hash="candidate-b")
    assert store.search_evaluations("exact-search") == links


def test_search_evaluation_link_is_append_only_and_tamper_evident():
    store = _store()
    store.begin_search_attempt(
        search_id="bound-search",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=100,
    )
    store.register_search_candidates(
        "bound-search", ["candidate-a"], registered_at=100
    )
    store.insert_evaluation(
        run_id="run-a",
        alpha_id="a1",
        dataset_version="dataset-v1",
        recorded_ts=100,
        verdict="PASS",
        content_hash="content-run-a",
        evaluation_json='{"alpha_id":"a1"}',
        search_id="bound-search",
        config_hash="candidate-a",
    )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("DELETE FROM search_evaluations")

    store._conn.execute("DROP TRIGGER search_evaluations_no_update")
    store._conn.execute("UPDATE search_evaluations SET config_hash='tampered'")
    store._conn.commit()
    assert not store.search_history_valid()


def test_evaluation_audit_outbox_failure_rolls_back_evaluation_and_link():
    store = _store()
    store.begin_search_attempt(
        search_id="bound-search",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=100,
    )
    store.register_search_candidates(
        "bound-search", ["candidate-a"], registered_at=100
    )
    store._conn.execute(
        "CREATE TRIGGER reject_evaluation_audit "
        "BEFORE INSERT ON evaluation_audit_outbox "
        "BEGIN SELECT RAISE(ABORT, 'reject evaluation audit'); END"
    )

    with pytest.raises(sqlite3.DatabaseError, match="reject evaluation audit"):
        store.insert_evaluation(
            run_id="run-a",
            alpha_id="a1",
            dataset_version="dataset-v1",
            recorded_ts=100,
            verdict="PASS",
            content_hash="content-run-a",
            evaluation_json='{"alpha_id":"a1"}',
            search_id="bound-search",
            config_hash="candidate-a",
            audit_intent=_audit_intent("run-a", timestamp=100),
        )
    assert store.get_evaluation("run-a") is None
    assert store.search_evaluations("bound-search") == []
    assert store.pending_evaluation_audits() == ()


def test_evaluation_audit_outbox_is_append_only_and_tamper_evident():
    store = _store()
    store.insert_evaluation(
        run_id="run-a",
        alpha_id="a1",
        dataset_version="dataset-v1",
        recorded_ts=100,
        verdict="PASS",
        content_hash="content-run-a",
        evaluation_json='{"alpha_id":"a1"}',
        audit_intent=_audit_intent("run-a", timestamp=100),
    )
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        store._conn.execute("DELETE FROM evaluation_audit_outbox")

    store._conn.execute("DROP TRIGGER evaluation_audit_outbox_no_update")
    store._conn.execute(
        "UPDATE evaluation_audit_outbox SET actor='tampered'"
    )
    store._conn.commit()
    assert not store.evaluation_audit_chain_valid()


def test_search_provenance_rejects_start_and_per_attempt_clock_rollback():
    store = _store()
    store.begin_search_attempt(
        search_id="first",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=100,
    )
    store.register_search_candidates("first", ["candidate-a"], registered_at=110)
    store.finish_search_attempt("first", status="COMPLETED", recorded_at=120)

    with pytest.raises(ExperimentStoreError, match="STARTED clock rollback"):
        store.begin_search_attempt(
            search_id="rolled-back",
            search_kind="TEST",
            source="tests.research",
            dataset_version="dataset-v1",
            recorded_at=99,
        )

    store.begin_search_attempt(
        search_id="second",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=120,
    )
    store.register_search_candidates("second", ["candidate-b"], registered_at=130)
    with pytest.raises(ExperimentStoreError, match="clock rollback"):
        store.register_search_candidates(
            "second", ["candidate-c"], registered_at=129
        )
    assert store.search_candidates("second")[0]["config_hash"] == "candidate-b"
    assert store.search_history_valid() is True


def test_interleaved_searches_use_sequence_order_and_per_attempt_clocks():
    store = _store()
    for search_id, recorded_at in (("older", 100), ("newer", 200)):
        store.begin_search_attempt(
            search_id=search_id,
            search_kind="TEST",
            source="tests.research",
            dataset_version="dataset-v1",
            recorded_at=recorded_at,
        )

    assert store.register_search_candidates(
        "older", ["current-candidate"], registered_at=150
    ) == 1
    assert store.finish_search_attempt(
        "older", status="COMPLETED", recorded_at=150
    )
    store.register_search_candidates(
        "newer", ["newer-candidate"], registered_at=250
    )
    with pytest.raises(ExperimentStoreError, match="terminal time precedes"):
        store.finish_search_attempt("newer", status="COMPLETED", recorded_at=249)
    assert store.search_history_valid() is True


def test_history_validation_rejects_terminal_before_its_own_start():
    store = _store()
    for search_id, recorded_at in (("older", 100), ("newer", 200)):
        store.begin_search_attempt(
            search_id=search_id,
            search_kind="TEST",
            source="tests.research",
            dataset_version="dataset-v1",
            recorded_at=recorded_at,
        )
    event_head = store._conn.execute(
        "SELECT event_hash FROM search_attempt_events ORDER BY event_seq DESC LIMIT 1"
    ).fetchone()["event_hash"]
    digest = experiment_store._search_event_hash(
        search_id="older",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        status="FAILED",
        recorded_at=99,
        prev_event_hash=event_head,
    )
    store._conn.execute(
        "INSERT INTO search_attempt_events"
        " (search_id,search_kind,source,dataset_version,status,recorded_at,"
        " prev_event_hash,event_hash) VALUES (?,?,?,?,?,?,?,?)",
        (
            "older",
            "TEST",
            "tests.research",
            "dataset-v1",
            "FAILED",
            99,
            event_head,
            digest,
        ),
    )
    store._conn.commit()

    assert store.search_history_valid() is False


def test_history_validation_rejects_candidate_before_its_own_start():
    store = _store()
    store.begin_search_attempt(
        search_id="older",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=100,
    )
    store.register_search_candidates("older", ["candidate-a"], registered_at=150)
    store.begin_search_attempt(
        search_id="newer",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=200,
    )
    store.register_search_candidates("newer", ["candidate-b"], registered_at=250)

    candidate_head = store._conn.execute(
        "SELECT candidate_hash FROM search_candidates "
        "ORDER BY candidate_seq DESC LIMIT 1"
    ).fetchone()["candidate_hash"]
    digest = experiment_store._search_candidate_hash(
        search_id="older",
        config_hash="candidate-c",
        status="REGISTERED",
        registered_at=99,
        prev_candidate_hash=candidate_head,
    )
    store._conn.execute(
        "INSERT INTO trials(config_hash,first_seen_ts) VALUES (?,?)",
        ("candidate-c", 99),
    )
    store._conn.execute(
        "INSERT INTO search_candidates"
        " (search_id,config_hash,status,registered_at,prev_candidate_hash,"
        " candidate_hash) VALUES (?,?,?,?,?,?)",
        (
            "older",
            "candidate-c",
            "REGISTERED",
            99,
            candidate_head,
            digest,
        ),
    )
    store._conn.commit()

    assert store.search_history_valid() is False


def test_shared_ledger_serializes_concurrent_candidate_batches_at_scale(tmp_path):
    path = tmp_path / "shared-ledger.db"
    SqliteExperimentStore(str(path), create=True).close()
    worker_count = 6
    candidates_per_worker = 200

    def record_batch(worker: int) -> int:
        store = SqliteExperimentStore(str(path), create=False)
        search_id = f"concurrent-{worker}"
        hashes = [
            f"worker-{worker}-candidate-{candidate}"
            for candidate in range(candidates_per_worker)
        ]
        try:
            store.begin_search_attempt(
                search_id=search_id,
                search_kind="CONCURRENCY_TEST",
                source="tests.research",
                dataset_version="dataset-v1",
                recorded_at=1_000,
            )
            inserted = store.register_search_candidates(
                search_id, hashes, registered_at=1_000
            )
            store.insert_evaluation(
                run_id=f"run-{worker}",
                alpha_id=f"alpha-{worker}",
                dataset_version="dataset-v1",
                recorded_ts=1_000,
                verdict="PASS",
                content_hash=f"content-{worker}",
                evaluation_json=f'{{"worker":{worker}}}',
                search_id=search_id,
                config_hash=hashes[0],
            )
            store.finish_search_attempt(
                search_id, status="COMPLETED", recorded_at=1_000
            )
            return inserted
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        inserted_counts = list(executor.map(record_batch, range(worker_count)))

    final = SqliteExperimentStore(str(path), create=False)
    assert inserted_counts == [candidates_per_worker] * worker_count
    assert final.trial_count() == worker_count * candidates_per_worker
    assert final.search_history_valid() is True
    for worker in range(worker_count):
        search_id = f"concurrent-{worker}"
        assert len(final.search_candidates(search_id)) == candidates_per_worker
        assert [
            row["run_id"] for row in final.search_evaluations(search_id)
        ] == [f"run-{worker}"]
        assert [
            row["status"] for row in final.search_attempt_events(search_id)
        ] == ["STARTED", "COMPLETED"]
