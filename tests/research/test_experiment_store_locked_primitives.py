from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.events import EventOutboxMessage
from quantos.contracts.research import AlphaState, ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.events import alpha_promoted_event
from services.research.experiment_registry.registry import (
    ExperimentRegistry,
    prepare_evaluation_artifact,
)
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.ports.persistence import (
    EvaluationArtifactWrite,
    ExperimentStoreError,
)


def _write(run_id: str = "run-1") -> EvaluationArtifactWrite:
    write = prepare_evaluation_artifact(
        AlphaEvaluation(
            alpha_id="alpha-1",
            verdict=ValidationVerdict.PASS,
            reproducibility_hash="repro-1",
        ),
        "dataset-v1",
        10,
    )
    return write if run_id == "run-1" else replace(write, run_id=run_id)


def _message() -> EventOutboxMessage:
    return EventOutboxMessage(
        event=alpha_promoted_event(
            request_id="request-1",
            alpha_id="alpha-1",
            src="PAPER_TRADING",
            dst="APPROVED",
            actor_id="issuer|carol",
            grant_head_hash="a" * 64,
            eval_run_id="eval-1",
            evidence_notes=(),
            at_ms=20,
        ),
        correlation_id="request-1",
        causation_id="a" * 64,
    )


def _catalog() -> AlphaCatalog:
    catalog = AlphaCatalog()
    catalog.register(
        AlphaDefinition(
            alpha_id="alpha-1",
            name="alpha-1",
            factor_ids=("factor-1",),
        )
    )
    catalog._set_state("alpha-1", AlphaState.APPROVED)
    return catalog


def _bound_store() -> SqliteExperimentStore:
    store = SqliteExperimentStore(":memory:", create=True)
    store.begin_search_attempt(
        search_id="search-1",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=10,
    )
    store.register_search_candidates(
        "search-1",
        ("config-1",),
        registered_at=10,
    )
    return store


def _transaction_statements(
    store: SqliteExperimentStore,
    action: Callable[[], object],
) -> list[str]:
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        action()
    finally:
        store._conn.set_trace_callback(None)
    return [" ".join(statement.upper().split()) for statement in statements]


def _assert_one_committed_immediate_transaction(statements: list[str]) -> None:
    control = [
        statement
        for statement in statements
        if statement.startswith(("BEGIN", "COMMIT", "ROLLBACK"))
    ]
    assert control == ["BEGIN IMMEDIATE", "COMMIT"]


def test_locked_primitive_rejects_call_outside_store_transaction() -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    with store._immediate_transaction() as (_connection, lease):
        pass

    with pytest.raises(ExperimentStoreError, match="stale write lease"):
        store._insert_evaluation_locked(store._conn, lease, _write())

    assert store.get_evaluation("run-1") is None


def test_locked_primitive_rejects_foreign_connection() -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    foreign = sqlite3.connect(":memory:")
    try:
        with store._immediate_transaction() as (connection, lease):
            assert connection is store._conn
            with pytest.raises(ExperimentStoreError, match="foreign connection"):
                store._require_write_lock(foreign, lease)
    finally:
        foreign.close()


def test_write_lease_rejects_forged_stale_generation_and_wrong_thread() -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    with store._immediate_transaction() as (connection, first_lease):
        with pytest.raises(ExperimentStoreError, match="lease"):
            store._require_write_lock(connection, object())

    with store._immediate_transaction() as (connection, second_lease):
        with pytest.raises(ExperimentStoreError, match="stale|lease"):
            store._require_write_lock(connection, first_lease)
        store._require_write_lock(connection, second_lease)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                store._require_write_lock,
                connection,
                second_lease,
            )
            with pytest.raises(ExperimentStoreError, match="thread"):
                future.result(timeout=5)


def test_write_lease_cleanup_after_base_exception() -> None:
    class AbortWrite(BaseException):
        pass

    store = SqliteExperimentStore(":memory:", create=True)
    with pytest.raises(AbortWrite):
        with store._immediate_transaction() as (connection, lease):
            connection.execute(
                "INSERT INTO trials(config_hash,first_seen_ts) VALUES (?,?)",
                ("rolled-back", 1),
            )
            raise AbortWrite()

    assert store.trial_count() == 0
    with store._immediate_transaction() as (connection, new_lease):
        with pytest.raises(ExperimentStoreError, match="stale|lease"):
            store._require_write_lock(connection, lease)
        store._require_write_lock(connection, new_lease)


def test_post_begin_lease_setup_base_exception_rolls_back_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AbortLeaseSetup(BaseException):
        pass

    store = SqliteExperimentStore(":memory:", create=True)
    statements: list[str] = []

    def abort(stage: str) -> None:
        assert stage == "after_begin"
        raise AbortLeaseSetup()

    monkeypatch.setattr(store, "_write_lease_checkpoint", abort)
    store._conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(AbortLeaseSetup):
            with store._immediate_transaction():
                raise AssertionError("unreachable")
    finally:
        store._conn.set_trace_callback(None)

    control = [
        " ".join(statement.upper().split())
        for statement in statements
        if statement.upper().startswith(("BEGIN", "COMMIT", "ROLLBACK"))
    ]
    assert control == ["BEGIN IMMEDIATE", "ROLLBACK"]
    assert store._conn.in_transaction is False
    assert store._active_write_lease is None
    monkeypatch.setattr(store, "_write_lease_checkpoint", lambda _stage: None)
    store.add_trials(("after-setup-abort",), first_seen_ts=1)
    assert store.all_trial_hashes() == ["after-setup-abort"]


def test_commit_failure_invalidates_and_closes_store_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqliteExperimentStore(":memory:", create=True)

    def fail_commit(_connection: sqlite3.Connection) -> None:
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(store, "_commit_owned_transaction", fail_commit)
    with pytest.raises(ExperimentStoreError, match="commit failed.*invalidated"):
        with store._immediate_transaction() as (connection, _lease):
            connection.execute(
                "INSERT INTO trials(config_hash,first_seen_ts) VALUES (?,?)",
                ("uncertain", 1),
            )

    with pytest.raises(ExperimentStoreError, match="invalidated"):
        store.add_trials(("later",), first_seen_ts=2)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store._conn.execute("SELECT 1")


def test_rollback_failure_invalidates_and_closes_store_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqliteExperimentStore(":memory:", create=True)

    def fail_rollback(_connection: sqlite3.Connection) -> None:
        raise RuntimeError("injected rollback failure")

    monkeypatch.setattr(store, "_rollback_owned_transaction", fail_rollback)
    with pytest.raises(ExperimentStoreError, match="rollback failed.*invalidated"):
        with store._immediate_transaction() as (connection, _lease):
            connection.execute(
                "INSERT INTO trials(config_hash,first_seen_ts) VALUES (?,?)",
                ("uncertain", 1),
            )
            raise RuntimeError("force rollback")

    with pytest.raises(ExperimentStoreError, match="invalidated"):
        store.add_trials(("later",), first_seen_ts=2)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        store._conn.execute("SELECT 1")


def test_store_owned_transaction_rejects_nested_begin() -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        with store._immediate_transaction():
            with pytest.raises(ExperimentStoreError, match="reentrant"):
                with store._immediate_transaction():
                    raise AssertionError("unreachable")
    finally:
        store._conn.set_trace_callback(None)

    _assert_one_committed_immediate_transaction(
        [" ".join(statement.upper().split()) for statement in statements]
    )


@pytest.mark.parametrize(
    "stage",
    ("after_evaluation", "after_link", "after_audit", "before_commit"),
)
def test_evaluation_locked_write_rolls_back_after_every_insert(
    stage: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / f"evaluation-{stage}.db"
    store = SqliteExperimentStore(str(path), create=True)
    store.begin_search_attempt(
        search_id="search-1",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=10,
    )
    store.register_search_candidates("search-1", ("config-1",), registered_at=10)

    def fail(current: str) -> None:
        if current == stage:
            raise RuntimeError(f"injected {stage}")

    monkeypatch.setattr(store, "_evaluation_write_checkpoint", fail)
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(RuntimeError, match=stage):
            store.insert_evaluation(
                **_write().__dict__,
                search_id="search-1",
                config_hash="config-1",
            )
    finally:
        store._conn.set_trace_callback(None)

    assert store.get_evaluation("run-1") is None
    assert store.search_evaluations("search-1") == []
    assert store.pending_evaluation_audits() == ()
    control = [
        " ".join(statement.upper().split())
        for statement in statements
        if statement.upper().startswith(("BEGIN", "COMMIT", "ROLLBACK"))
    ]
    assert control == ["BEGIN IMMEDIATE", "ROLLBACK"]
    store.close()

    reopened = SqliteExperimentStore(str(path), create=False)
    try:
        assert reopened.get_evaluation("run-1") is None
        assert reopened.search_evaluations("search-1") == []
        assert reopened.pending_evaluation_audits() == ()
        assert reopened.search_history_valid()
    finally:
        reopened.close()


def test_event_outbox_locked_write_rolls_back_catalog_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    path = tmp_path / "outbox-fault.db"
    store = SqliteExperimentStore(str(path), create=True)
    registry = ExperimentRegistry(store)

    def fail(stage: str) -> None:
        if stage == "after_outbox":
            raise RuntimeError("injected after_outbox")

    monkeypatch.setattr(store, "_event_outbox_write_checkpoint", fail)
    with pytest.raises(RuntimeError, match="after_outbox"):
        registry.save(_catalog(), now=20, event_outbox=(_message(),))

    assert store.trial_count() == 0
    assert store.all_alphas() == []
    assert store.pending_event_outbox() == ()
    store.close()

    reopened = SqliteExperimentStore(str(path), create=False)
    try:
        assert reopened.trial_count() == 0
        assert reopened.all_alphas() == []
        assert reopened.pending_event_outbox() == ()
        assert reopened.event_outbox_chain_valid()
    finally:
        reopened.close()


def test_locked_primitives_do_not_migrate_or_issue_transaction_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _bound_store()
    monkeypatch.setattr(
        store,
        "_ensure_search_schema",
        lambda: (_ for _ in ()).throw(AssertionError("migration is forbidden")),
    )
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        with store._immediate_transaction() as (connection, lease):
            statements.clear()
            store._insert_evaluation_locked(connection, lease, _write())
            store._append_event_outbox_locked(connection, lease, ())
            body_statements = tuple(statements)
    finally:
        store._conn.set_trace_callback(None)

    assert not any(
        statement.upper().startswith(("BEGIN", "COMMIT", "ROLLBACK", "CREATE", "ALTER"))
        for statement in body_statements
    )


def test_inconsistent_caller_built_write_is_rejected_before_sql() -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    inconsistent = replace(_write(), content_hash="0" * 64)
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(ExperimentStoreError, match="evaluation artifact"):
            with store._immediate_transaction() as (connection, lease):
                statements.clear()
                store._insert_evaluation_locked(connection, lease, inconsistent)
    finally:
        store._conn.set_trace_callback(None)

    body_sql = [
        statement
        for statement in statements
        if not statement.upper().startswith(("BEGIN", "COMMIT", "ROLLBACK"))
    ]
    assert body_sql == []
    assert store.get_evaluation(inconsistent.run_id) is None


def test_legacy_evaluation_link_audit_and_outbox_match_independent_golden_bytes() -> None:
    class GoldenEvaluation:
        alpha_id = "因子-α"
        reproducibility_hash = "repro-雪"
        verdict = ValidationVerdict.REQUIRES_HUMAN_REVIEW

        def model_dump(self):
            return {
                "alpha_id": self.alpha_id,
                "verdict": self.verdict,
                "reproducibility_hash": self.reproducibility_hash,
                "precise": 0.12345678901234568,
                "negative_zero": -0.0,
                "nested": {
                    "labels": ["β", {"state": "PASS"}],
                    "value": 1.23456789012345,
                },
            }

    expected_json = (
        '{"alpha_id": "\\u56e0\\u5b50-\\u03b1", "negative_zero": -0.0, '
        '"nested": {"labels": ["\\u03b2", {"state": "PASS"}], '
        '"value": 1.23456789012345}, "precise": 0.12345678901234568, '
        '"reproducibility_hash": "repro-\\u96ea", '
        '"verdict": "REQUIRES_HUMAN_REVIEW"}'
    )
    write = prepare_evaluation_artifact(
        GoldenEvaluation(),
        "dataset-v精确",
        123_456_789,
    )
    assert write.evaluation_json.encode("utf-8") == expected_json.encode("utf-8")
    assert write.content_hash == (
        "0b64fe00078387fddd1433922ac00369d887ac0e405faf913460981c5cb6c02c"
    )
    assert write.run_id == "e90235452c835e5d"

    store = SqliteExperimentStore(":memory:", create=True)
    store.begin_search_attempt(
        search_id="搜索-1",
        search_kind="GOLDEN",
        source="tests.research.golden",
        dataset_version="dataset-v精确",
        recorded_at=123_456_789,
    )
    store.register_search_candidates(
        "搜索-1", ("配置-1",), registered_at=123_456_789
    )
    store.insert_evaluation(
        **write.__dict__,
        search_id="搜索-1",
        config_hash="配置-1",
    )
    evaluation = store._conn.execute(
        "SELECT evaluation_json,content_hash FROM evaluations WHERE run_id=?",
        (write.run_id,),
    ).fetchone()
    link = store._conn.execute(
        "SELECT link_hash FROM search_evaluations WHERE run_id=?",
        (write.run_id,),
    ).fetchone()
    audit = store._conn.execute(
        "SELECT metadata_json,record_hash FROM evaluation_audit_outbox WHERE run_id=?",
        (write.run_id,),
    ).fetchone()
    assert evaluation["evaluation_json"].encode("utf-8") == expected_json.encode("utf-8")
    assert evaluation["content_hash"] == write.content_hash
    assert link["link_hash"] == (
        "9628def9f6bd2de519a40b635759bc6ad1bb46d419313487d030f083961898df"
    )
    assert audit["metadata_json"] == (
        '{"content_hash":"0b64fe00078387fddd1433922ac00369d887ac0e405faf913460981c5cb6c02c",'
        '"run_id":"e90235452c835e5d","verdict":"REQUIRES_HUMAN_REVIEW"}'
    )
    assert audit["record_hash"] == (
        "86dbd98c7fb10703a1996fa951df1b12500cccc275788ad11de6f3cc8e5851d8"
    )

    outbox_store = SqliteExperimentStore(":memory:", create=True)
    ExperimentRegistry(outbox_store).save(
        _catalog(), now=20, event_outbox=(_message(),)
    )
    outbox = outbox_store._conn.execute(
        "SELECT outbox_id,event_json,record_hash FROM experiment_event_outbox"
    ).fetchone()
    assert outbox["outbox_id"] == (
        "alpha-promotion-d7e44ef972e2b7860dfee33a25595db8"
    )
    assert outbox["event_json"] == (
        '{"criticality":"C1","domain":"research",'
        '"event_id":"alpha-promotion-d7e44ef972e2b7860dfee33a25595db8",'
        '"event_type":"research.alpha.promoted","event_version":"1.1.0",'
        '"payload":{"actor_id":"issuer|carol","alpha_id":"alpha-1",'
        '"dst":"APPROVED","eval_run_id":"eval-1","evidence_notes":[],'
        '"grant_head_hash":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",'
        '"request_id":"request-1","src":"PAPER_TRADING"},'
        '"source":"services.alpha.promotion","timestamp":20000}'
    )
    assert outbox["record_hash"] == (
        "8ddbf6cb9b77918ec805be342890e24d661cf673f674193cc696059d0d50aa47"
    )


def test_caller_owned_transaction_is_rejected_without_interference() -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    store._conn.execute("BEGIN")
    store._conn.execute(
        "INSERT INTO trials(config_hash,first_seen_ts) VALUES (?,?)",
        ("caller-row", 1),
    )
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(ExperimentStoreError, match="caller-owned transaction"):
            store.add_trials(("store-row",), first_seen_ts=2)
        assert store._conn.in_transaction is True
        assert store._conn.execute(
            "SELECT COUNT(*) FROM trials WHERE config_hash='caller-row'"
        ).fetchone()[0] == 1
    finally:
        store._conn.set_trace_callback(None)
        store._conn.rollback()

    assert not any(
        statement.upper().startswith(("COMMIT", "ROLLBACK"))
        for statement in statements
    )


def test_public_mutator_rejects_reentrant_store_lease_without_nested_control() -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        with store._immediate_transaction():
            with pytest.raises(ExperimentStoreError, match="active store write lease"):
                store.add_trials(("nested",), first_seen_ts=1)
    finally:
        store._conn.set_trace_callback(None)

    _assert_one_committed_immediate_transaction(
        [" ".join(statement.upper().split()) for statement in statements]
    )


def test_legacy_audit_reads_have_zero_schema_or_transaction_side_effects(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE trials(config_hash TEXT PRIMARY KEY,first_seen_ts INTEGER NOT NULL);"
        "CREATE TABLE alpha_state(alpha_id TEXT PRIMARY KEY,definition_json TEXT NOT NULL,"
        "state TEXT NOT NULL,returns_json TEXT NOT NULL,sharpe REAL,pvalue REAL);"
        "CREATE TABLE evaluations(run_id TEXT PRIMARY KEY,alpha_id TEXT NOT NULL,"
        "dataset_version TEXT NOT NULL,recorded_ts INTEGER NOT NULL,verdict TEXT NOT NULL,"
        "content_hash TEXT NOT NULL,evaluation_json TEXT NOT NULL);"
        "PRAGMA user_version=0;"
    )
    connection.close()
    store = SqliteExperimentStore(str(path), create=False)

    def snapshot() -> tuple[object, ...]:
        return (
            store._conn.execute("PRAGMA user_version").fetchone()[0],
            tuple(store._conn.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
            )),
            store._conn.total_changes,
            store._conn.in_transaction,
        )

    before = snapshot()
    with pytest.raises(ExperimentStoreError, match="search provenance"):
        store.pending_evaluation_audits()
    assert snapshot() == before
    assert store.evaluation_audit_chain_valid() is False
    assert snapshot() == before

    store._conn.execute("BEGIN")
    in_transaction = snapshot()
    with pytest.raises(ExperimentStoreError, match="search provenance"):
        store.pending_evaluation_audits()
    assert store.evaluation_audit_chain_valid() is False
    assert snapshot() == in_transaction
    store._conn.rollback()


def test_audit_reads_reject_unknown_future_schema_without_side_effects() -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    store._conn.execute("PRAGMA user_version=3")
    before = (
        tuple(store._conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        )),
        store._conn.total_changes,
        store._conn.in_transaction,
    )

    with pytest.raises(ExperimentStoreError, match="family authority"):
        store.pending_evaluation_audits()
    assert store.evaluation_audit_chain_valid() is False
    assert (
        tuple(store._conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        )),
        store._conn.total_changes,
        store._conn.in_transaction,
    ) == before
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_load_bandit_is_read_only_when_optional_table_is_absent() -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    before = tuple(
        store._conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        )
    )
    changes = store._conn.total_changes

    assert store.load_bandit() is None
    assert tuple(
        store._conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        )
    ) == before
    assert store._conn.total_changes == changes


def _public_mutator_cases() -> tuple[
    tuple[str, Callable[[SqliteExperimentStore], Callable[[], object]]], ...
]:
    def add_trials(store: SqliteExperimentStore) -> Callable[[], object]:
        return lambda: store.add_trials(("config-1",), first_seen_ts=1)

    def begin_search(store: SqliteExperimentStore) -> Callable[[], object]:
        return lambda: store.begin_search_attempt(
            search_id="search-1",
            search_kind="TEST",
            source="tests.research",
            dataset_version="dataset-v1",
            recorded_at=10,
        )

    def register_candidates(store: SqliteExperimentStore) -> Callable[[], object]:
        begin_search(store)()
        return lambda: store.register_search_candidates(
            "search-1", ("config-1",), registered_at=10
        )

    def heartbeat(store: SqliteExperimentStore) -> Callable[[], object]:
        begin_search(store)()
        return lambda: store.heartbeat_search_attempt("search-1", recorded_at=11)

    def finish_search(store: SqliteExperimentStore) -> Callable[[], object]:
        begin_search(store)()
        return lambda: store.finish_search_attempt(
            "search-1", status="COMPLETED", recorded_at=11
        )

    def recover_search(store: SqliteExperimentStore) -> Callable[[], object]:
        begin_search(store)()
        return lambda: store.recover_stale_search_attempts(
            recorded_at=20, timeout_ms=5
        )

    def save_catalog(store: SqliteExperimentStore) -> Callable[[], object]:
        registry = ExperimentRegistry(store)
        return lambda: registry.save(
            _catalog(), now=20, event_outbox=(_message(),)
        )

    def save_evaluation_catalog(
        store: SqliteExperimentStore,
    ) -> Callable[[], object]:
        row = {
            "alpha_id": "alpha-1",
            "definition_json": '{"alpha_id":"alpha-1"}',
            "state": "DRAFT",
            "returns_json": "[]",
            "sharpe": None,
            "pvalue": None,
        }
        return lambda: store.save_evaluation_catalog_snapshot(
            config_hashes=("config-1",), alphas=(row,), first_seen_ts=1
        )

    def mark_event(store: SqliteExperimentStore) -> Callable[[], object]:
        ExperimentRegistry(store).save(
            _catalog(), now=20, event_outbox=(_message(),)
        )
        message = _message()
        return lambda: store.mark_event_dispatched(
            message.outbox_id,
            dispatched_at=21,
            event_repro_hash=message.event.reproducibility_hash,
        )

    def upsert_alpha(store: SqliteExperimentStore) -> Callable[[], object]:
        return lambda: store.upsert_alpha(
            "alpha-1", '{"alpha_id":"alpha-1"}', "DRAFT", "[]", None, None
        )

    def save_bandit(store: SqliteExperimentStore) -> Callable[[], object]:
        return lambda: store.save_bandit('{"schema_version":2}')

    def insert_evaluation(store: SqliteExperimentStore) -> Callable[[], object]:
        return lambda: store.insert_evaluation(**_write().__dict__)

    def mark_audit(store: SqliteExperimentStore) -> Callable[[], object]:
        write = _write()
        store.insert_evaluation(**write.__dict__)
        assert write.audit_intent is not None
        return lambda: store.mark_evaluation_audit_dispatched(
            write.audit_intent.event_id,
            dispatched_at=11,
            audit_entry_hash="audit-entry-1",
        )

    return (
        ("add_trials", add_trials),
        ("begin_search_attempt", begin_search),
        ("register_search_candidates", register_candidates),
        ("heartbeat_search_attempt", heartbeat),
        ("finish_search_attempt", finish_search),
        ("recover_stale_search_attempts", recover_search),
        ("save_catalog_snapshot", save_catalog),
        ("save_evaluation_catalog_snapshot", save_evaluation_catalog),
        ("mark_event_dispatched", mark_event),
        ("upsert_alpha", upsert_alpha),
        ("save_bandit", save_bandit),
        ("insert_evaluation", insert_evaluation),
        ("mark_evaluation_audit_dispatched", mark_audit),
    )


@pytest.mark.parametrize(
    ("name", "prepare"),
    _public_mutator_cases(),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_each_public_mutator_owns_exactly_one_immediate_transaction(
    name: str,
    prepare: Callable[[SqliteExperimentStore], Callable[[], object]],
) -> None:
    del name
    store = SqliteExperimentStore(":memory:", create=True)
    action = prepare(store)

    statements = _transaction_statements(store, action)

    _assert_one_committed_immediate_transaction(statements)


@pytest.mark.parametrize(
    ("name", "prepare"),
    _public_mutator_cases(),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_each_public_mutator_rejects_caller_owned_transaction_without_control(
    name: str,
    prepare: Callable[[SqliteExperimentStore], Callable[[], object]],
) -> None:
    del name
    store = SqliteExperimentStore(":memory:", create=True)
    action = prepare(store)
    store._conn.execute("BEGIN")
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(ExperimentStoreError, match="caller-owned transaction"):
            action()
        assert store._conn.in_transaction is True
    finally:
        store._conn.set_trace_callback(None)
        store._conn.rollback()

    assert not any(
        statement.upper().startswith(("COMMIT", "ROLLBACK"))
        for statement in statements
    )
