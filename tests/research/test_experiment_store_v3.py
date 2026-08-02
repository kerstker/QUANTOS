"""Phase-A runtime version permissions for the experiment store."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path
from typing import Callable

import pytest

from services.research.experiment_registry import store as store_module
from services.research.experiment_registry.family_schema_spec import (
    FamilySchemaActivationInputs,
)
from services.research.experiment_registry.family_schema_v3_activation import (
    activate_family_schema_v3,
)
from services.research.experiment_registry.family_schema_v3_verifier import (
    FamilyAuthorityExternalProviders,
    FamilyAuthorityVerificationError,
)
from services.research.experiment_registry.store import SqliteExperimentStore
from services.research.ports.persistence import ExperimentStoreError


_AUTHORITY_ID = "experiment-store-v3-test"
_ACTIVATION_CODE_HASH = "d" * 64


def _transaction_control(statements: list[str]) -> list[str]:
    normalized = [" ".join(statement.upper().split()) for statement in statements]
    return [statement for statement in normalized if statement.startswith(("BEGIN", "COMMIT", "ROLLBACK"))]


def _snapshot(store: SqliteExperimentStore) -> tuple[object, ...]:
    return (
        int(store._conn.execute("PRAGMA user_version").fetchone()[0]),
        tuple(store._conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name")),
        store._conn.total_changes,
        store._conn.in_transaction,
    )


def _trace_action(
    store: SqliteExperimentStore,
    action: Callable[[], object],
    statements: list[str] | None = None,
) -> list[str]:
    statements = [] if statements is None else statements
    store._conn.set_trace_callback(statements.append)
    try:
        action()
    finally:
        store._conn.set_trace_callback(None)
    return statements


@pytest.fixture(scope="module")
def exact_v3_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("experiment-store-v3") / "authority.db"
    store = SqliteExperimentStore(str(path), create=True)
    store.close()
    connection = sqlite3.connect(path)
    try:
        activate_family_schema_v3(
            connection,
            FamilySchemaActivationInputs(
                authority_instance_id=_AUTHORITY_ID,
                activation_code_manifest_hash=_ACTIVATION_CODE_HASH,
                activated_at_us=1_700_000_000_000_003,
            ),
        )
    finally:
        connection.close()
    return path


def _copy_v3_store(
    tmp_path: Path,
    exact_v3_database: Path,
    *,
    expected_authority_instance_id: str | None = _AUTHORITY_ID,
    external_providers: FamilyAuthorityExternalProviders | None = None,
) -> SqliteExperimentStore:
    path = tmp_path / "authority-copy.db"
    shutil.copyfile(exact_v3_database, path)
    return SqliteExperimentStore(
        str(path),
        create=False,
        expected_authority_instance_id=expected_authority_instance_id,
        external_providers=external_providers,
    )


def _seed_search(store: SqliteExperimentStore, *, search_id: str = "search-v3-read") -> None:
    assert store.begin_search_attempt(
        search_id=search_id,
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=10,
    )


def _search_audit_read_actions(
    store: SqliteExperimentStore,
    *,
    search_id: str = "search-v3-read",
) -> dict[str, Callable[[], object]]:
    return {
        "search_attempt_events": lambda: store.search_attempt_events(search_id),
        "search_attempt_recoveries": lambda: store.search_attempt_recoveries(search_id),
        "search_evaluations": lambda: store.search_evaluations(search_id),
        "search_candidates": lambda: store.search_candidates(search_id),
        "search_history_valid": store.search_history_valid,
        "pending_evaluation_audits": store.pending_evaluation_audits,
        "evaluation_audit_chain_valid": store.evaluation_audit_chain_valid,
    }


def _replace_inherited_table(
    store: SqliteExperimentStore,
    table_name: str,
    create_sql: str,
) -> None:
    trigger_sql = tuple(
        row[0]
        for row in store._conn.execute(
            "SELECT sql FROM sqlite_schema WHERE type='trigger' AND tbl_name=? ORDER BY name",
            (table_name,),
        )
    )
    store._conn.execute(f'DROP TABLE "{table_name}"')
    store._conn.execute(create_sql)
    for statement in trigger_sql:
        store._conn.execute(statement)


def _tamper_search_writer_schema(store: SqliteExperimentStore, tamper: str) -> None:
    if tamper == "false_trigger":
        store._conn.execute("DROP TRIGGER search_attempt_events_no_update")
        store._conn.execute(
            "CREATE TRIGGER search_attempt_events_no_update "
            "BEFORE UPDATE ON search_attempt_events WHEN 0 "
            "BEGIN SELECT RAISE(ABORT, 'search_attempt_events is append-only'); END"
        )
    elif tamper == "missing_primary_key":
        _replace_inherited_table(
            store,
            "bandit_state",
            "CREATE TABLE bandit_state "
            "(id INTEGER CHECK (id = 1), snapshot_json TEXT NOT NULL)",
        )
    elif tamper == "missing_unique":
        _replace_inherited_table(
            store,
            "evaluation_audit_dispatch",
            "CREATE TABLE evaluation_audit_dispatch ("
            "dispatch_seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "event_id TEXT NOT NULL,"
            "dispatched_at INTEGER NOT NULL CHECK(dispatched_at >= 0),"
            "audit_entry_hash TEXT NOT NULL,"
            "prev_record_hash TEXT NOT NULL,"
            "record_hash TEXT UNIQUE NOT NULL)",
        )
    elif tamper == "missing_index":
        store._conn.execute("DROP INDEX search_candidates_by_search")
    elif tamper == "column_type":
        _replace_inherited_table(
            store,
            "bandit_state",
            "CREATE TABLE bandit_state "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), snapshot_json BLOB NOT NULL)",
        )
    elif tamper == "column_default":
        _replace_inherited_table(
            store,
            "bandit_state",
            "CREATE TABLE bandit_state "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), snapshot_json TEXT NOT NULL DEFAULT '{}')",
        )
    elif tamper == "extra_column":
        _replace_inherited_table(
            store,
            "bandit_state",
            "CREATE TABLE bandit_state "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), snapshot_json TEXT NOT NULL, extra TEXT)",
        )
    elif tamper == "unknown_search_object":
        store._conn.execute("CREATE TABLE search_writer_shadow(id INTEGER PRIMARY KEY)")
    else:
        raise AssertionError(f"unknown tamper: {tamper}")
    store._conn.commit()


def test_create_true_remains_exact_v2_and_does_not_create_family_objects() -> None:
    store = SqliteExperimentStore(
        ":memory:",
        create=True,
        expected_authority_instance_id=_AUTHORITY_ID,
        external_providers=FamilyAuthorityExternalProviders(),
    )

    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert not tuple(store._conn.execute("SELECT name FROM sqlite_schema WHERE name='family_authority_genesis'"))


@pytest.mark.parametrize("version", (0, 1, 2))
def test_search_writer_migrates_below_v2_and_preserves_exact_v2(version: int) -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    store._conn.execute(f"PRAGMA user_version={version}")
    store._conn.commit()

    assert store.begin_search_attempt(
        search_id=f"search-v{version}",
        search_kind="TEST",
        source="tests.research",
        dataset_version="dataset-v1",
        recorded_at=10,
    )
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize("version", (0, 1))
@pytest.mark.parametrize("mutator", ("trials", "bandit"))
def test_legacy_mutators_also_complete_the_existing_v2_migration(
    version: int,
    mutator: str,
) -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    store._conn.execute(f"PRAGMA user_version={version}")
    store._conn.commit()
    actions: dict[str, Callable[[], object]] = {
        "trials": lambda: store.add_trials(("config-legacy",), first_seen_ts=1),
        "bandit": lambda: store.save_bandit('{"schema_version":2}'),
    }

    actions[mutator]()

    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize("version", (0, 1))
def test_legacy_base_schema_tamper_rolls_back_the_whole_v2_migration(
    version: int,
    tmp_path: Path,
) -> None:
    path = tmp_path / f"tampered-v{version}.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(store_module._SCHEMA)
        connection.execute("ALTER TABLE trials ADD COLUMN unowned_payload BLOB")
        connection.execute(f"PRAGMA user_version={version}")
        connection.commit()
    store = SqliteExperimentStore(str(path), create=False)
    before = _snapshot(store)
    statements: list[str] = []

    with pytest.raises(ExperimentStoreError):
        _trace_action(
            store,
            lambda: store.begin_search_attempt(
                search_id=f"tampered-v{version}",
                search_kind="TEST",
                source="tests.research",
                dataset_version="dataset-v1",
                recorded_at=10,
            ),
            statements,
        )

    assert _snapshot(store) == before
    assert _transaction_control(statements) == ["BEGIN IMMEDIATE", "ROLLBACK"]


def test_v3_label_without_family_authority_fails_before_transaction_control() -> None:
    store = SqliteExperimentStore(
        ":memory:",
        create=True,
        expected_authority_instance_id=_AUTHORITY_ID,
    )
    store._conn.execute("PRAGMA user_version=3")
    store._conn.commit()
    before = _snapshot(store)
    statements: list[str] = []

    with pytest.raises(ExperimentStoreError, match="family authority"):
        _trace_action(
            store,
            lambda: store.begin_search_attempt(
                search_id="search-v3-label-only",
                search_kind="TEST",
                source="tests.research",
                dataset_version="dataset-v1",
                recorded_at=10,
            ),
            statements,
        )

    assert _snapshot(store) == before
    assert _transaction_control(statements) == []


@pytest.mark.parametrize("version", (5,))
def test_phase_a_rejects_future_versions_before_transaction_control(
    version: int,
) -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    store._conn.execute(f"PRAGMA user_version={version}")
    store._conn.commit()
    before = _snapshot(store)
    statements: list[str] = []

    with pytest.raises(ExperimentStoreError, match=f"user_version: {version}"):
        _trace_action(
            store,
            lambda: store.add_trials(("config-1",), first_seen_ts=1),
            statements,
        )

    assert _snapshot(store) == before
    assert _transaction_control(statements) == []


@pytest.mark.parametrize("version", (0, 1, 2, 4, 5))
def test_family_v3_requirement_rejects_other_versions_without_migration(
    version: int,
) -> None:
    store = SqliteExperimentStore(
        ":memory:",
        create=True,
        expected_authority_instance_id=_AUTHORITY_ID,
    )
    store._conn.execute(f"PRAGMA user_version={version}")
    store._conn.commit()
    before = _snapshot(store)
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        with pytest.raises(ExperimentStoreError, match="exact user_version 3"):
            store._require_family_schema_v3()
    finally:
        store._conn.set_trace_callback(None)

    assert _snapshot(store) == before
    assert _transaction_control(statements) == []


def test_valid_v3_family_requirement_is_read_only_and_forwards_configuration(
    tmp_path: Path,
    exact_v3_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    providers = FamilyAuthorityExternalProviders()
    store = _copy_v3_store(
        tmp_path,
        exact_v3_database,
        external_providers=providers,
    )
    observed: dict[str, object] = {}
    expected_evidence = object()

    def verify(
        connection: sqlite3.Connection,
        *,
        expected_authority_instance_id: str,
        external_providers: FamilyAuthorityExternalProviders | None = None,
    ) -> object:
        observed.update(
            connection=connection,
            expected_authority_instance_id=expected_authority_instance_id,
            external_providers=external_providers,
        )
        return expected_evidence

    monkeypatch.setattr(store_module, "verify_family_authority", verify)
    before = _snapshot(store)
    statements = _trace_action(store, store._require_family_schema_v3)

    assert observed == {
        "connection": store._conn,
        "expected_authority_instance_id": _AUTHORITY_ID,
        "external_providers": providers,
    }
    assert _snapshot(store) == before
    assert _transaction_control(statements) == []


@pytest.mark.parametrize("mode", ("missing_identity", "wrong_identity", "provider"))
def test_v3_configuration_failure_is_pre_begin_and_write_free(
    mode: str,
    tmp_path: Path,
    exact_v3_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = (
        None if mode == "missing_identity" else ("wrong-authority" if mode == "wrong_identity" else _AUTHORITY_ID)
    )
    store = _copy_v3_store(
        tmp_path,
        exact_v3_database,
        expected_authority_instance_id=expected,
    )
    if mode == "provider":
        monkeypatch.setattr(
            store_module,
            "verify_family_authority",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(FamilyAuthorityVerificationError("provider unavailable")),
        )
    before = _snapshot(store)
    statements: list[str] = []

    with pytest.raises(ExperimentStoreError, match="family authority"):
        _trace_action(
            store,
            lambda: store.begin_search_attempt(
                search_id=f"search-{mode}",
                search_kind="TEST",
                source="tests.research",
                dataset_version="dataset-v1",
                recorded_at=10,
            ),
            statements,
        )

    assert _snapshot(store) == before
    assert _transaction_control(statements) == []


def test_v3_schema_tamper_is_rejected_before_begin(
    tmp_path: Path,
    exact_v3_database: Path,
) -> None:
    store = _copy_v3_store(tmp_path, exact_v3_database)
    store._conn.execute('DROP INDEX "data_snooping_family_searches_family_search"')
    store._conn.commit()
    before = _snapshot(store)
    statements: list[str] = []

    with pytest.raises(ExperimentStoreError, match="family authority"):
        _trace_action(
            store,
            lambda: store.add_trials(("config-tampered",), first_seen_ts=1),
            statements,
        )

    assert _snapshot(store) == before
    assert _transaction_control(statements) == []


@pytest.mark.parametrize("version", (2, 3))
@pytest.mark.parametrize(
    "tamper",
    (
        "false_trigger",
        "missing_primary_key",
        "missing_unique",
        "missing_index",
        "column_type",
        "column_default",
        "extra_column",
        "unknown_search_object",
    ),
)
def test_search_writer_rejects_non_exact_inherited_schema_before_begin(
    version: int,
    tamper: str,
    tmp_path: Path,
    exact_v3_database: Path,
) -> None:
    store = (
        SqliteExperimentStore(str(tmp_path / "exact-v2.db"), create=True)
        if version == 2
        else _copy_v3_store(tmp_path, exact_v3_database)
    )
    _tamper_search_writer_schema(store, tamper)
    before = _snapshot(store)
    statements: list[str] = []

    with pytest.raises(ExperimentStoreError):
        _trace_action(
            store,
            lambda: store.add_trials((f"config-{version}-{tamper}",), first_seen_ts=1),
            statements,
        )

    assert _snapshot(store) == before
    assert _transaction_control(statements) == []


@pytest.mark.parametrize("mutator", ("search", "trials", "bandit"))
def test_v3_mutators_own_exactly_one_transaction(
    mutator: str,
    tmp_path: Path,
    exact_v3_database: Path,
) -> None:
    store = _copy_v3_store(tmp_path, exact_v3_database)
    actions: dict[str, Callable[[], object]] = {
        "search": lambda: store.begin_search_attempt(
            search_id="search-v3",
            search_kind="TEST",
            source="tests.research",
            dataset_version="dataset-v1",
            recorded_at=10,
        ),
        "trials": lambda: store.add_trials(("config-v3",), first_seen_ts=1),
        "bandit": lambda: store.save_bandit('{"schema_version":2}'),
    }

    statements = _trace_action(store, actions[mutator])

    assert _transaction_control(statements) == ["BEGIN IMMEDIATE", "COMMIT"]
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == 3


def test_v2_writer_runs_exact_gate_before_and_after_acquiring_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SqliteExperimentStore(":memory:", create=True)
    real_verify = store_module.verify_exact_v2_search_writer_source
    transaction_states: list[bool] = []

    def verify(connection: sqlite3.Connection) -> object:
        transaction_states.append(connection.in_transaction)
        return real_verify(connection)

    monkeypatch.setattr(store_module, "verify_exact_v2_search_writer_source", verify)

    store.add_trials(("config-double-v2-gate",), first_seen_ts=1)

    assert transaction_states == [False, True]


def test_v3_writer_runs_search_and_family_gates_before_and_after_lock(
    tmp_path: Path,
    exact_v3_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _copy_v3_store(tmp_path, exact_v3_database)
    real_verify_search = store_module.verify_search_state
    calls: list[tuple[str, bool]] = []

    def verify_search(connection: sqlite3.Connection) -> object:
        calls.append(("search", connection.in_transaction))
        return real_verify_search(connection)

    def verify_family(*_args: object, **_kwargs: object) -> object:
        calls.append(("family", store._conn.in_transaction))
        return object()

    monkeypatch.setattr(store_module, "verify_search_state", verify_search)
    monkeypatch.setattr(store_module, "verify_family_authority", verify_family)

    store.add_trials(("config-double-v3-gate",), first_seen_ts=1)

    assert calls == [
        ("search", False),
        ("family", False),
        ("search", True),
        ("family", True),
    ]


def test_exact_v3_search_and_audit_reads_verify_both_authorities_without_writes(
    tmp_path: Path,
    exact_v3_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _copy_v3_store(tmp_path, exact_v3_database)
    _seed_search(store)
    real_verify_search_state = store_module.verify_search_state
    calls: list[str] = []

    def verify_search_state(connection: sqlite3.Connection) -> object:
        calls.append("search")
        return real_verify_search_state(connection)

    def verify_family_authority(
        connection: sqlite3.Connection,
        *,
        expected_authority_instance_id: str,
        external_providers: FamilyAuthorityExternalProviders | None = None,
    ) -> object:
        assert connection is store._conn
        assert expected_authority_instance_id == _AUTHORITY_ID
        assert external_providers is None
        calls.append("family")
        return object()

    monkeypatch.setattr(store_module, "verify_search_state", verify_search_state)
    monkeypatch.setattr(store_module, "verify_family_authority", verify_family_authority)

    for name, action in _search_audit_read_actions(store).items():
        calls.clear()
        before = _snapshot(store)
        statements = _trace_action(store, action)

        assert calls == ["search", "family"], name
        assert _snapshot(store) == before, name
        assert _transaction_control(statements) == [], name
        assert not any(
            " ".join(statement.upper().split()).startswith(
                ("INSERT ", "UPDATE ", "DELETE ", "REPLACE ", "CREATE ", "DROP ", "ALTER ")
            )
            for statement in statements
        ), name


def test_exact_v3_search_read_preserves_caller_transaction(
    tmp_path: Path,
    exact_v3_database: Path,
) -> None:
    store = _copy_v3_store(tmp_path, exact_v3_database)
    _seed_search(store)
    store._conn.execute("BEGIN")
    before = _snapshot(store)

    statements = _trace_action(store, lambda: store.search_attempt_events("search-v3-read"))

    assert _snapshot(store) == before
    assert store._conn.in_transaction
    assert _transaction_control(statements) == []
    store._conn.rollback()


@pytest.mark.parametrize("version", (0, 1))
def test_search_and_audit_reads_do_not_migrate_uninitialized_legacy_store(version: int) -> None:
    store = SqliteExperimentStore(
        ":memory:",
        create=False,
        expected_authority_instance_id=_AUTHORITY_ID,
    )
    if version:
        store._conn.execute(f"PRAGMA user_version={version}")
        store._conn.commit()
    before = _snapshot(store)

    for name, action in _search_audit_read_actions(store).items():
        statements: list[str] = []
        if name.endswith("_valid"):
            result: list[object] = []
            _trace_action(store, lambda: result.append(action()), statements)
            assert result == [False]
        else:
            with pytest.raises(ExperimentStoreError):
                _trace_action(store, action, statements)
        assert _snapshot(store) == before, name
        assert _transaction_control(statements) == [], name


@pytest.mark.parametrize("version", (5,))
def test_search_and_audit_reads_reject_future_versions_without_writes(version: int) -> None:
    store = SqliteExperimentStore(
        ":memory:",
        create=True,
        expected_authority_instance_id=_AUTHORITY_ID,
    )
    store._conn.execute(f"PRAGMA user_version={version}")
    store._conn.commit()
    before = _snapshot(store)

    for name, action in _search_audit_read_actions(store).items():
        statements: list[str] = []
        if name.endswith("_valid"):
            result: list[object] = []
            _trace_action(store, lambda: result.append(action()), statements)
            assert result == [False]
        else:
            with pytest.raises(ExperimentStoreError, match="user_version"):
                _trace_action(store, action, statements)
        assert _snapshot(store) == before, name
        assert _transaction_control(statements) == [], name


def test_store_has_no_runtime_activation_dependency() -> None:
    source = Path(store_module.__file__).read_text(encoding="utf-8")

    assert "family_schema_v3_activation" not in source
    assert "activate_family_schema_v3" not in source
