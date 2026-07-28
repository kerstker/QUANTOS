from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import pytest

from quantos.contracts.alpha import AlphaDefinition, AlphaEvaluation
from quantos.contracts.canonical import canonical_sha256
from quantos.contracts.events import Event, EventOutboxMessage
from quantos.contracts.research import ValidationVerdict
from services.alpha.catalog import AlphaCatalog
from services.alpha.events import alpha_promoted_event
from services.research.experiment_registry import family_schema_v3_activation as activation
from services.research.experiment_registry import family_schema_v3_verifier as verifier
from services.research.experiment_registry.family_schema_spec import (
    ActivationStep,
    FamilySchemaActivationInputs,
)
from services.research.experiment_registry.family_schema_v2_activation import (
    ExactV2ActivationSourceError,
    verify_exact_v2_activation_source,
)
from services.research.experiment_registry.family_schema_v2_catalog import (
    EXACT_V2_INHERITED_SCHEMA_CATALOG,
)
from services.research.experiment_registry.registry import (
    ExperimentRegistry,
    prepare_evaluation_artifact,
)
from services.research.experiment_registry.store import SqliteExperimentStore


_AUTHORITY_ID = "authority-activation-test"
_ACTIVATION_CODE_HASH = "c" * 64
_INHERITED_TABLE_NAMES = {
    "trials",
    "alpha_state",
    "evaluations",
    "bandit_state",
    "experiment_event_outbox",
    "experiment_event_dispatch",
    "search_attempt_events",
    "search_candidates",
    "search_attempt_heartbeats",
    "search_attempt_recoveries",
    "search_evaluations",
    "evaluation_audit_outbox",
    "evaluation_audit_dispatch",
}


class _InjectedActivationFailure(RuntimeError):
    pass


class _ConnectionSubclass(sqlite3.Connection):
    pass


class _PopulatedBandit:
    @staticmethod
    def snapshot() -> dict[str, object]:
        return {
            "version": 1,
            "pulls": [[["operator", "guided"], 2]],
            "reward_sum": [[["operator", "guided"], 1.25]],
            "n_evaluated": 2,
            "proposed": [["operator", "guided"]],
            "regret_history": [0.5, 0.25],
        }


@dataclass(frozen=True, slots=True)
class _InheritedStateSnapshot:
    typed_ordered_rows: tuple[
        tuple[str, tuple[str, ...], tuple[tuple[object, ...], ...]], ...
    ]
    sqlite_sequence: tuple[tuple[object, ...], ...]
    alpha_state_sql: str


def _event_message(index: int) -> EventOutboxMessage:
    request_id = f"legacy-request-{index}"
    message = alpha_promoted_event(
        request_id=request_id,
        alpha_id="alpha-legacy-1",
        src="PAPER_TRADING",
        dst="APPROVED",
        actor_id="issuer|legacy-human",
        grant_head_hash=f"{index}" * 64,
        eval_run_id=f"legacy-eval-{index}",
        evidence_notes=(f"legacy-note-{index}",),
        at_ms=200 + index,
    )
    return EventOutboxMessage(
        event=message,
        correlation_id=request_id,
        causation_id=f"{index + 2}" * 64,
    )


def _create_populated_exact_v2(path: Path) -> None:
    """Populate all 13 inherited tables only through official write surfaces."""

    store = SqliteExperimentStore(str(path), create=True)
    registry = ExperimentRegistry(store)
    try:
        registry.begin_search_attempt(
            search_id="legacy-stale-search",
            search_kind="TEST",
            source="tests.research.activation",
            dataset_version="dataset-v1",
            recorded_at=10,
        )
        registry.heartbeat_search_attempt(
            "legacy-stale-search",
            recorded_at=20,
        )
        assert registry.recover_stale_search_attempts(
            recorded_at=100,
            timeout_ms=50,
        ) == ("legacy-stale-search",)

        registry.begin_search_attempt(
            search_id="legacy-search-1",
            search_kind="TEST",
            source="tests.research.activation",
            dataset_version="dataset-v1",
            recorded_at=110,
        )
        registry.register_search_candidates(
            "legacy-search-1",
            ("配置-一",),
            registered_at=111,
        )
        registry.heartbeat_search_attempt("legacy-search-1", recorded_at=112)
        evaluation = AlphaEvaluation(
            alpha_id="alpha-legacy-1",
            verdict=ValidationVerdict.PASS,
            reproducibility_hash="legacy-repro-1",
        )
        write = prepare_evaluation_artifact(evaluation, "dataset-v1", 113)
        assert store.insert_evaluation(
            **write.__dict__,
            search_id="legacy-search-1",
            config_hash="配置-一",
        )
        registry.finish_search_attempt(
            "legacy-search-1",
            status="COMPLETED",
            recorded_at=114,
        )

        catalog = AlphaCatalog()
        catalog.register(
            AlphaDefinition(
                alpha_id="alpha-legacy-1",
                name="legacy-alpha",
                factor_ids=("legacy-factor",),
            )
        )
        messages = (_event_message(1), _event_message(2))
        registry.save(catalog, now=120, event_outbox=messages)
        for offset, message in enumerate(messages, start=1):
            assert registry.mark_event_dispatched(
                message.outbox_id,
                dispatched_at=120 + offset,
                event_repro_hash=message.event.reproducibility_hash,
            )
        assert store.mark_evaluation_audit_dispatched(
            f"exp-{write.run_id}",
            dispatched_at=123,
            audit_entry_hash="b" * 64,
        )
        registry.save_bandit(_PopulatedBandit(), dataset_version="dataset-v1")
    finally:
        store.close()


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _snapshot_inherited_state(
    connection: sqlite3.Connection,
) -> _InheritedStateSnapshot:
    tables: list[tuple[str, tuple[str, ...], tuple[tuple[object, ...], ...]]] = []
    for table in EXACT_V2_INHERITED_SCHEMA_CATALOG.tables:
        columns = tuple(column.name for column in table.columns)
        primary_key = tuple(
            column.name
            for column in sorted(
                table.columns,
                key=lambda item: item.primary_key_ordinal or len(table.columns) + 1,
            )
            if column.primary_key_ordinal
        )
        projections: list[str] = []
        for column in columns:
            quoted = _quote(column)
            projections.extend(
                (
                    f"typeof({quoted})",
                    quoted,
                    "CASE WHEN typeof("
                    + quoted
                    + ") IN ('text','blob') THEN CAST("
                    + quoted
                    + " AS BLOB) ELSE NULL END",
                )
            )
        statement = (
            "SELECT "
            + ", ".join(projections)
            + f" FROM {_quote(table.table_name)} ORDER BY "
            + ", ".join(
                f"{_quote(column)} COLLATE BINARY ASC" for column in primary_key
            )
        )
        tables.append(
            (
                table.table_name,
                columns,
                tuple(tuple(row) for row in connection.execute(statement)),
            )
        )
    sequence = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT name, typeof(name), CAST(name AS BLOB), "
            "seq, typeof(seq) FROM sqlite_sequence ORDER BY name COLLATE BINARY ASC"
        )
    )
    alpha_sql_row = connection.execute(
        "SELECT sql FROM sqlite_schema WHERE type='table' AND name='alpha_state'"
    ).fetchone()
    assert alpha_sql_row is not None
    return _InheritedStateSnapshot(
        typed_ordered_rows=tuple(tables),
        sqlite_sequence=sequence,
        alpha_state_sql=str(alpha_sql_row[0]),
    )


def _snapshot_rows_by_table(
    snapshot: _InheritedStateSnapshot,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    return {name: rows for name, _columns, rows in snapshot.typed_ordered_rows}


def _legacy_compatibility_golden(
    connection: sqlite3.Connection,
) -> dict[str, tuple[tuple[object, ...], ...]]:
    return {
        "evaluation": tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT evaluation_json, content_hash, run_id FROM evaluations "
                "ORDER BY run_id COLLATE BINARY ASC"
            )
        ),
        "search_link": tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT link_seq, search_id, config_hash, run_id, recorded_at, "
                "evaluation_content_hash, prev_link_hash, link_hash "
                "FROM search_evaluations ORDER BY link_seq ASC"
            )
        ),
        "audit": tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT audit_seq, event_id, timestamp, metadata_json, "
                "prev_record_hash, record_hash FROM evaluation_audit_outbox "
                "ORDER BY audit_seq ASC"
            )
        ),
        "outbox": tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT outbox_seq, event_json, repro_hash, prev_record_hash, "
                "record_hash FROM experiment_event_outbox ORDER BY outbox_seq ASC"
            )
        ),
    }


def _assert_legacy_compatibility_golden(
    golden: dict[str, tuple[tuple[object, ...], ...]],
) -> None:
    assert len(golden["evaluation"]) == 1
    evaluation_json, content_hash, run_id = golden["evaluation"][0]
    assert type(evaluation_json) is str
    assert json.loads(evaluation_json)["alpha_id"] == "alpha-legacy-1"
    assert type(content_hash) is str and len(content_hash) == 64
    assert type(run_id) is str and run_id

    assert golden["search_link"] == (
        (
            1,
            "legacy-search-1",
            "配置-一",
            run_id,
            113,
            content_hash,
            "",
            golden["search_link"][0][7],
        ),
    )
    link_hash = golden["search_link"][0][7]
    assert type(link_hash) is str and len(link_hash) == 64

    assert len(golden["audit"]) == 1
    audit_seq, event_id, timestamp, metadata_json, audit_prev, audit_hash = (
        golden["audit"][0]
    )
    assert (audit_seq, event_id, timestamp, audit_prev) == (
        1,
        f"exp-{run_id}",
        113,
        "",
    )
    assert json.loads(metadata_json) == {
        "content_hash": content_hash,
        "run_id": run_id,
        "verdict": "PASS",
    }
    assert type(audit_hash) is str and len(audit_hash) == 64

    assert tuple(row[0] for row in golden["outbox"]) == (1, 2)
    first, second = golden["outbox"]
    assert "legacy-request-1" in first[1]
    assert "legacy-request-2" in second[1]
    assert all(
        Event.model_validate(json.loads(row[1])).reproducibility_hash == row[2]
        for row in (first, second)
    )
    assert first[3] == ""
    assert second[3] == first[4]
    assert all(type(row[4]) is str and len(row[4]) == 64 for row in (first, second))


def _create_exact_v2(path: Path) -> None:
    store = SqliteExperimentStore(str(path), create=True)
    store.close()


def _open_exact(
    path: Path,
    *,
    check_same_thread: bool = True,
    timeout: float = 5.0,
) -> tuple[sqlite3.Connection, list[str]]:
    connection = sqlite3.connect(
        str(path),
        cached_statements=0,
        check_same_thread=check_same_thread,
        timeout=timeout,
    )
    statements: list[str] = []
    connection.set_trace_callback(statements.append)
    return connection, statements


def _transaction_count(statements: list[str], operation: str) -> int:
    expected = operation.upper()
    normalized = tuple(statement.strip().upper() for statement in statements)
    if expected == "BEGIN":
        return sum(statement == "BEGIN" or statement.startswith("BEGIN ") for statement in normalized)
    return sum(statement == expected for statement in normalized)


def test_activation_rejects_sqlite_connection_subclasses(tmp_path: Path) -> None:
    database = tmp_path / "connection-subclass.db"
    _create_exact_v2(database)
    connection = sqlite3.connect(str(database), factory=_ConnectionSubclass)
    try:
        with pytest.raises(TypeError, match="exact sqlite3.Connection"):
            activation.activate_family_schema_v3(connection, _inputs())
    finally:
        connection.close()


def test_owned_helper_rejects_forged_owner_before_any_sql(tmp_path: Path) -> None:
    database = tmp_path / "forged-owner.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(database)
    try:
        with pytest.raises(
            activation.FamilySchemaV3ActivationError,
            match="exact owner CAS token",
        ):
            activation._activate_family_schema_v3_owned(
                connection,
                _inputs(),
                owner_token=object(),
                external_providers=None,
                fault_injector=None,
            )
        assert statements == []
        assert id(connection) not in activation._ACTIVE_ACTIVATION_OWNERS
    finally:
        connection.close()


def _inputs() -> FamilySchemaActivationInputs:
    return FamilySchemaActivationInputs(
        authority_instance_id=_AUTHORITY_ID,
        activation_code_manifest_hash=_ACTIVATION_CODE_HASH,
        activated_at_us=1_700_000_000_000_001,
    )


def test_empty_exact_v2_activates_atomically_as_verified_v3(tmp_path: Path) -> None:
    database = tmp_path / "empty-v2.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(database)
    try:
        source = verify_exact_v2_activation_source(connection)
        evidence = activation.activate_family_schema_v3(connection, _inputs())

        assert _transaction_count(statements, "BEGIN") == 1
        assert _transaction_count(statements, "COMMIT") == 1
        assert _transaction_count(statements, "ROLLBACK") == 0
        assert connection.in_transaction is False
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert evidence.source_evidence == source
        assert evidence.ddl_statement_count == 401
        assert evidence.verification_evidence.schema_version == 3
        assert evidence.verification_evidence.authority_instance_id == _AUTHORITY_ID
        assert not tuple(
            row
            for row in connection.execute("PRAGMA function_list")
            if str(row[0]).casefold().startswith("quantos_")
        )
        rendered = activation._canonical_manifest().rendered_objects
        assert tuple(
            statement.strip().removesuffix(";")
            for statement in statements
            if statement.lstrip().upper().startswith("CREATE ")
        ) == tuple(item.sqlite_schema_sql for item in rendered)
        assert {
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT type, name FROM sqlite_schema"
            )
            if row[1] is not None
        }.issuperset({(item.object_type, item.object_name) for item in rendered})

        row = connection.execute(
            'SELECT * FROM "family_authority_genesis"'
        ).fetchone()
        assert row is not None
        columns = tuple(
            item[1]
            for item in connection.execute(
                'PRAGMA table_info("family_authority_genesis")'
            )
        )
        stored = dict(zip(columns, row, strict=True))
        record_hash = stored.pop("record_hash")
        assert record_hash == canonical_sha256(stored)
        assert record_hash == evidence.genesis_record_hash
        assert stored == {
            "singleton_key": 1,
            "authority_instance_id": _AUTHORITY_ID,
            "v2_origin_fingerprint": source.origin_fingerprint,
            "sealed_v3_manifest_hash": evidence.sealed_v3_manifest_hash,
            "activation_code_manifest_evidence_hash": _ACTIVATION_CODE_HASH,
            "migration_id": "FAMILY_SCHEMA_V2_TO_V3_V1",
            "activated_at_us": _inputs().activated_at_us,
            "record_type": "FAMILY_AUTHORITY_GENESIS_V1",
        }
    finally:
        connection.close()


def test_populated_exact_v2_activates_without_changing_inherited_bytes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "populated-v2.db"
    _create_populated_exact_v2(database)
    connection, _statements = _open_exact(database)
    try:
        source = verify_exact_v2_activation_source(connection)
        before = _snapshot_inherited_state(connection)
        golden_before = _legacy_compatibility_golden(connection)
        _assert_legacy_compatibility_golden(golden_before)
        rows_by_table = _snapshot_rows_by_table(before)

        assert set(rows_by_table) == _INHERITED_TABLE_NAMES
        assert all(rows_by_table[table] for table in _INHERITED_TABLE_NAMES)
        assert tuple(row[0] for row in before.sqlite_sequence) == tuple(
            sorted(EXACT_V2_INHERITED_SCHEMA_CATALOG.owned_sqlite_sequence_tables)
        )
        assert all(type(row[3]) is int and row[3] > 0 for row in before.sqlite_sequence)

        saw_text = False
        for _table_name, columns, rows in before.typed_ordered_rows:
            for row in rows:
                assert len(row) == len(columns) * 3
                for offset in range(0, len(row), 3):
                    storage_type, value, sql_bytes = row[offset : offset + 3]
                    if storage_type == "text":
                        saw_text = True
                        assert type(value) is str
                        assert sql_bytes == value.encode("utf-8")
                    elif storage_type == "blob":
                        assert type(value) is bytes
                        assert sql_bytes == value
        assert saw_text

        evidence = activation.activate_family_schema_v3(connection, _inputs())

        assert evidence.source_evidence == source
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert _snapshot_inherited_state(connection) == before
        assert _legacy_compatibility_golden(connection) == golden_before
        inherited_counts = dict(evidence.verification_evidence.inherited_table_row_counts)
        family_counts = dict(evidence.verification_evidence.family_table_row_counts)
        assert set(inherited_counts) == _INHERITED_TABLE_NAMES
        assert all(inherited_counts[table] > 0 for table in _INHERITED_TABLE_NAMES)
        assert len(family_counts) == 33
        assert family_counts["family_authority_genesis"] == 1
        assert sum(family_counts.values()) == 1
    finally:
        connection.close()


def test_activation_verifier_serializes_uncommitted_v3_without_dump_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "activation-serialize-snapshot.db"
    _create_exact_v2(database)
    connection, _statements = _open_exact(database)

    def forbidden_dump(_connection: sqlite3.Connection) -> sqlite3.Connection:
        raise AssertionError("activation snapshot must use serialize/deserialize")

    monkeypatch.setattr(
        verifier,
        "_clone_caller_visible_snapshot_from_dump",
        forbidden_dump,
    )
    try:
        before_changes = connection.total_changes
        evidence = activation.activate_family_schema_v3(connection, _inputs())
        assert evidence.verification_evidence.schema_version == 3
        assert connection.in_transaction is False
        assert connection.total_changes > before_changes
    finally:
        connection.close()


def test_existing_transaction_is_neither_committed_nor_rolled_back(tmp_path: Path) -> None:
    database = tmp_path / "nested.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(database)
    try:
        connection.execute("BEGIN")
        statements.clear()
        with pytest.raises(
            activation.FamilySchemaV3ActivationError,
            match="caller-owned transaction",
        ):
            activation.activate_family_schema_v3(connection, _inputs())
        assert connection.in_transaction is True
        assert _transaction_count(statements, "COMMIT") == 0
        assert _transaction_count(statements, "ROLLBACK") == 0
    finally:
        sqlite3.Connection.rollback(connection)
        connection.close()


def test_reentrant_activation_on_same_connection_is_rejected_before_sql(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reentrant-owner.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(database)
    nested_errors: list[BaseException] = []

    def attempt_reentry(point: activation.ActivationFaultPoint) -> None:
        if point == activation.ActivationFaultPoint(
            ActivationStep.APPLY_SEALED_DDL_STATEMENTS,
            1,
        ):
            try:
                activation.activate_family_schema_v3(connection, _inputs())
            except BaseException as exc:
                nested_errors.append(exc)

    try:
        evidence = activation.activate_family_schema_v3(
            connection,
            _inputs(),
            fault_injector=attempt_reentry,
        )
        assert evidence.verification_evidence.schema_version == 3
        assert len(nested_errors) == 1
        assert isinstance(
            nested_errors[0],
            activation.FamilySchemaV3ActivationError,
        )
        assert "already has an active" in str(nested_errors[0])
        assert _transaction_count(statements, "BEGIN") == 1
        assert _transaction_count(statements, "COMMIT") == 1
        assert _transaction_count(statements, "ROLLBACK") == 0
        assert id(connection) not in activation._ACTIVE_ACTIVATION_OWNERS
    finally:
        connection.close()


def test_owner_marker_releases_after_failure_and_allows_retry(tmp_path: Path) -> None:
    database = tmp_path / "owner-retry.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(database)

    def fail_once(point: activation.ActivationFaultPoint) -> None:
        if point == activation.ActivationFaultPoint(
            ActivationStep.APPLY_SEALED_DDL_STATEMENTS,
            1,
        ):
            raise _InjectedActivationFailure("release owner")

    try:
        with pytest.raises(_InjectedActivationFailure, match="release owner"):
            activation.activate_family_schema_v3(
                connection,
                _inputs(),
                fault_injector=fail_once,
            )
        assert id(connection) not in activation._ACTIVE_ACTIVATION_OWNERS

        evidence = activation.activate_family_schema_v3(connection, _inputs())
        assert evidence.verification_evidence.schema_version == 3
        assert _transaction_count(statements, "BEGIN") == 2
        assert _transaction_count(statements, "COMMIT") == 1
        assert _transaction_count(statements, "ROLLBACK") == 1
        assert id(connection) not in activation._ACTIVE_ACTIVATION_OWNERS
    finally:
        connection.close()


def test_owner_cleanup_failure_does_not_mask_activation_root_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "owner-cleanup-root-error.db"
    _create_exact_v2(database)
    connection, _statements = _open_exact(database)
    original_release = activation._release_activation_owner

    def release_then_fail(conn: sqlite3.Connection, token: object) -> None:
        original_release(conn, token)
        raise activation.FamilySchemaV3ActivationError("synthetic cleanup failure")

    def fail_activation(point: activation.ActivationFaultPoint) -> None:
        if point == activation.ActivationFaultPoint(
            ActivationStep.APPLY_SEALED_DDL_STATEMENTS,
            1,
        ):
            raise _InjectedActivationFailure("original activation root error")

    monkeypatch.setattr(
        activation,
        "_release_activation_owner",
        release_then_fail,
    )
    try:
        with pytest.raises(
            _InjectedActivationFailure,
            match="original activation root error",
        ) as captured:
            activation.activate_family_schema_v3(
                connection,
                _inputs(),
                fault_injector=fail_activation,
            )
        assert any(
            "synthetic cleanup failure" in note
            for note in getattr(captured.value, "__notes__", ())
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert id(connection) not in activation._ACTIVE_ACTIVATION_OWNERS
    finally:
        connection.close()


def test_two_threads_cannot_activate_the_same_exact_connection(
    tmp_path: Path,
) -> None:
    database = tmp_path / "same-connection-two-threads.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(
        database,
        check_same_thread=False,
        timeout=60.0,
    )
    activation._canonical_manifest()
    first_entered = threading.Event()
    release_first = threading.Event()
    first_results: list[object] = []
    second_errors: list[BaseException] = []

    def pause_first(point: activation.ActivationFaultPoint) -> None:
        if point == activation.ActivationFaultPoint(
            ActivationStep.APPLY_SEALED_DDL_STATEMENTS,
            1,
        ):
            first_entered.set()
            if not release_first.wait(timeout=30.0):
                raise AssertionError("timed out waiting to release first activation")

    def activate_first() -> None:
        try:
            first_results.append(
                activation.activate_family_schema_v3(
                    connection,
                    _inputs(),
                    fault_injector=pause_first,
                )
            )
        except BaseException as exc:
            first_results.append(exc)

    def activate_second() -> None:
        try:
            activation.activate_family_schema_v3(connection, _inputs())
        except BaseException as exc:
            second_errors.append(exc)

    first = threading.Thread(target=activate_first, name="activation-owner-first")
    second = threading.Thread(target=activate_second, name="activation-owner-second")
    try:
        first.start()
        assert first_entered.wait(timeout=30.0)
        second.start()
        second.join(timeout=30.0)
        assert not second.is_alive()
        release_first.set()
        first.join(timeout=60.0)
        assert not first.is_alive()

        assert len(first_results) == 1
        assert isinstance(
            first_results[0],
            activation.FamilySchemaV3ActivationEvidence,
        )
        assert len(second_errors) == 1
        assert isinstance(
            second_errors[0],
            activation.FamilySchemaV3ActivationError,
        )
        assert "already has an active" in str(second_errors[0])
        assert _transaction_count(statements, "BEGIN") == 1
        assert _transaction_count(statements, "COMMIT") == 1
        assert _transaction_count(statements, "ROLLBACK") == 0
        assert id(connection) not in activation._ACTIVE_ACTIVATION_OWNERS
    finally:
        release_first.set()
        first.join(timeout=30.0)
        second.join(timeout=30.0)
        assert not first.is_alive()
        assert not second.is_alive()
        connection.close()


def test_first_ddl_fault_rolls_back_once_to_exact_v2(tmp_path: Path) -> None:
    database = tmp_path / "first-ddl-fault.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(database)
    source = verify_exact_v2_activation_source(connection)

    def fail_at_first_ddl(point: activation.ActivationFaultPoint) -> None:
        if (
            point.step is ActivationStep.APPLY_SEALED_DDL_STATEMENTS
            and point.ddl_ordinal == 1
        ):
            raise _InjectedActivationFailure("kill after DDL 1")

    try:
        with pytest.raises(_InjectedActivationFailure, match="DDL 1"):
            activation.activate_family_schema_v3(
                connection,
                _inputs(),
                fault_injector=fail_at_first_ddl,
            )
        assert _transaction_count(statements, "COMMIT") == 0
        assert _transaction_count(statements, "ROLLBACK") == 1
        assert connection.in_transaction is False
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert verify_exact_v2_activation_source(connection) == source
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name='family_authority_genesis'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("version", [0, 1, 3, 4, 5, 41])
def test_only_exact_user_version_two_may_enter_activation(
    tmp_path: Path,
    version: int,
) -> None:
    database = tmp_path / f"wrong-version-{version}.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(database)
    try:
        connection.execute(f"PRAGMA user_version = {version}")
        sqlite3.Connection.commit(connection)
        statements.clear()
        with pytest.raises(
            activation.FamilySchemaV3ActivationError,
            match="requires exact user_version 2",
        ):
            activation.activate_family_schema_v3(connection, _inputs())
        assert _transaction_count(statements, "COMMIT") == 0
        assert _transaction_count(statements, "ROLLBACK") == 0
        assert all(
            statement.strip().upper() != "BEGIN IMMEDIATE"
            for statement in statements
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == version
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name='family_authority_genesis'"
        ).fetchone()[0] == 0
    finally:
        connection.close()


def test_foreign_keys_must_enable_before_owned_transaction(tmp_path: Path) -> None:
    database = tmp_path / "foreign-keys-off.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(database)

    def deny_foreign_key_enable(
        action: int,
        argument1: str | None,
        argument2: str | None,
        _database: str | None,
        _source: str | None,
    ) -> int:
        if (
            action == sqlite3.SQLITE_PRAGMA
            and (argument1 or "").lower() == "foreign_keys"
            and (argument2 or "").upper() in {"1", "ON"}
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(deny_foreign_key_enable)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            activation.activate_family_schema_v3(connection, _inputs())
        assert _transaction_count(statements, "COMMIT") == 0
        assert _transaction_count(statements, "ROLLBACK") == 0
        assert connection.in_transaction is False
        assert all(
            statement.strip().upper() != "BEGIN IMMEDIATE"
            for statement in statements
        )
        connection.set_authorizer(None)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        connection.set_authorizer(None)
        connection.close()


def test_preexisting_v3_name_collision_fails_exact_v2_preflight(
    tmp_path: Path,
) -> None:
    database = tmp_path / "preexisting-collision.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(database)
    try:
        connection.execute(
            'CREATE TABLE "family_authority_genesis" ("unexpected" INTEGER)'
        )
        sqlite3.Connection.commit(connection)
        statements.clear()
        with pytest.raises(ExactV2ActivationSourceError):
            activation.activate_family_schema_v3(connection, _inputs())
        assert _transaction_count(statements, "COMMIT") == 0
        assert _transaction_count(statements, "ROLLBACK") == 0
        assert all(
            statement.strip().upper() != "BEGIN IMMEDIATE"
            for statement in statements
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        connection.close()


def test_locked_collision_check_is_independent_defense(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "locked-collision.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(database)
    rival = sqlite3.connect(str(database), cached_statements=0)
    original_verify = activation.verify_exact_v2_activation_source
    source: object | None = None
    calls = 0

    def isolate_collision_check(conn: sqlite3.Connection) -> object:
        nonlocal calls, source
        calls += 1
        if calls == 1:
            source = original_verify(conn)
            create_racing_collision()
        assert source is not None
        return source

    def create_racing_collision() -> None:
        rival.execute(
            'CREATE TABLE "family_authority_genesis" ("unexpected" INTEGER)'
        )
        rival.commit()

    monkeypatch.setattr(
        activation,
        "verify_exact_v2_activation_source",
        isolate_collision_check,
    )
    try:
        with pytest.raises(
            activation.FamilySchemaV3ActivationError,
            match="object-name collision",
        ):
            activation.activate_family_schema_v3(connection, _inputs())
        assert calls == 2
        assert _transaction_count(statements, "COMMIT") == 0
        assert _transaction_count(statements, "ROLLBACK") == 1
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        rival.close()
        connection.close()


def test_begin_immediate_rereads_version_after_two_connection_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "version-race.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(database)
    rival = sqlite3.connect(str(database), cached_statements=0)
    original_verify = activation.verify_exact_v2_activation_source
    calls = 0

    def rival_wins_before_lock() -> None:
        rival.execute("PRAGMA user_version = 3")
        rival.commit()

    def verify_then_lose_race(conn: sqlite3.Connection) -> object:
        nonlocal calls
        calls += 1
        evidence = original_verify(conn)
        if calls == 1:
            rival_wins_before_lock()
        return evidence

    monkeypatch.setattr(
        activation,
        "verify_exact_v2_activation_source",
        verify_then_lose_race,
    )
    try:
        with pytest.raises(
            activation.FamilySchemaV3ActivationError,
            match="requires exact user_version 2",
        ):
            activation.activate_family_schema_v3(connection, _inputs())
        assert calls == 1
        assert _transaction_count(statements, "COMMIT") == 0
        assert _transaction_count(statements, "ROLLBACK") == 1
        assert connection.in_transaction is False
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name='family_authority_genesis'"
        ).fetchone()[0] == 0
    finally:
        rival.close()
        connection.close()


def test_two_exact_connections_race_under_sqlite_writer_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "two-connection-activation-race.db"
    _create_populated_exact_v2(database)
    first, first_statements = _open_exact(
        database,
        check_same_thread=False,
        timeout=60.0,
    )
    second, second_statements = _open_exact(
        database,
        check_same_thread=False,
        timeout=60.0,
    )
    activation._canonical_manifest()
    original_verify = activation.verify_exact_v2_activation_source
    preflight_barrier = threading.Barrier(2)
    seen_connections: set[int] = set()
    seen_guard = threading.Lock()
    results: list[object] = []

    def align_preflight(connection: sqlite3.Connection) -> object:
        evidence = original_verify(connection)
        with seen_guard:
            first_call = id(connection) not in seen_connections
            seen_connections.add(id(connection))
        if first_call:
            preflight_barrier.wait(timeout=30.0)
        return evidence

    monkeypatch.setattr(
        activation,
        "verify_exact_v2_activation_source",
        align_preflight,
    )

    def race(connection: sqlite3.Connection) -> None:
        try:
            results.append(activation.activate_family_schema_v3(connection, _inputs()))
        except BaseException as exc:
            results.append(exc)

    threads = (
        threading.Thread(target=race, args=(first,), name="activation-race-first"),
        threading.Thread(target=race, args=(second,), name="activation-race-second"),
    )
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60.0)
        assert all(not thread.is_alive() for thread in threads)

        successes = [
            result
            for result in results
            if isinstance(result, activation.FamilySchemaV3ActivationEvidence)
        ]
        failures = [result for result in results if isinstance(result, BaseException)]
        assert len(successes) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], activation.FamilySchemaV3ActivationError)
        assert "requires exact user_version 2, got 3" in str(failures[0])
        combined = first_statements + second_statements
        assert _transaction_count(combined, "BEGIN") == 2
        assert _transaction_count(combined, "COMMIT") == 1
        assert _transaction_count(combined, "ROLLBACK") == 1
        assert id(first) not in activation._ACTIVE_ACTIVATION_OWNERS
        assert id(second) not in activation._ACTIVE_ACTIVATION_OWNERS
    finally:
        for thread in threads:
            thread.join(timeout=30.0)
        assert all(not thread.is_alive() for thread in threads)
        first.close()
        second.close()

    reopened = sqlite3.connect(str(database), cached_statements=0)
    try:
        reopened.execute("PRAGMA foreign_keys = ON")
        evidence = verifier.verify_family_authority(
            reopened,
            expected_authority_instance_id=_AUTHORITY_ID,
        )
        assert evidence.schema_version == 3
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "target",
    (
        activation.ActivationFaultPoint(
            ActivationStep.APPLY_SEALED_DDL_STATEMENTS,
            1,
        ),
        activation.ActivationFaultPoint(
            ActivationStep.APPLY_SEALED_DDL_STATEMENTS,
            201,
        ),
        activation.ActivationFaultPoint(
            ActivationStep.APPLY_SEALED_DDL_STATEMENTS,
            401,
        ),
        activation.ActivationFaultPoint(ActivationStep.INSERT_SINGLE_GENESIS),
        activation.ActivationFaultPoint(ActivationStep.SET_EXACT_V3_VERSION),
        activation.ActivationFaultPoint(
            ActivationStep.VERIFY_IN_TRANSACTION_SCHEMA_AND_GENESIS
        ),
        activation.ActivationFaultPoint(ActivationStep.FINAL_FAULT_CHECKPOINT),
    ),
    ids=(
        "first-ddl",
        "middle-ddl",
        "last-ddl",
        "insert-genesis",
        "set-version",
        "verify-v3",
        "final-checkpoint",
    ),
)
def test_populated_representative_faults_reopen_as_byte_exact_v2(
    tmp_path: Path,
    target: activation.ActivationFaultPoint,
) -> None:
    suffix = target.step.value.lower()
    if target.ddl_ordinal is not None:
        suffix += f"-{target.ddl_ordinal}"
    database = tmp_path / f"populated-rollback-{suffix}.db"
    _create_populated_exact_v2(database)
    connection, statements = _open_exact(database)
    source = verify_exact_v2_activation_source(connection)
    before = _snapshot_inherited_state(connection)
    golden_before = _legacy_compatibility_golden(connection)
    assert all(_snapshot_rows_by_table(before).values())
    _assert_legacy_compatibility_golden(golden_before)

    def inject(point: activation.ActivationFaultPoint) -> None:
        if point == target:
            raise _InjectedActivationFailure(repr(target))

    try:
        with pytest.raises(_InjectedActivationFailure):
            activation.activate_family_schema_v3(
                connection,
                _inputs(),
                fault_injector=inject,
            )
        assert _transaction_count(statements, "COMMIT") == 0
        assert _transaction_count(statements, "ROLLBACK") == 1
        assert connection.in_transaction is False
    finally:
        connection.close()

    reopened = sqlite3.connect(str(database), cached_statements=0)
    try:
        assert reopened.execute("PRAGMA user_version").fetchone()[0] == 2
        assert verify_exact_v2_activation_source(reopened) == source
        assert _snapshot_inherited_state(reopened) == before
        assert _legacy_compatibility_golden(reopened) == golden_before
    finally:
        reopened.close()


def test_every_ddl_and_activation_checkpoint_rolls_back_exactly_once(
    tmp_path: Path,
) -> None:
    database = tmp_path / "complete-fault-matrix.db"
    _create_exact_v2(database)
    connection, statements = _open_exact(database)
    source = verify_exact_v2_activation_source(connection)
    targets = (
        *(
            activation.ActivationFaultPoint(
                ActivationStep.APPLY_SEALED_DDL_STATEMENTS,
                ordinal,
            )
            for ordinal in range(1, 402)
        ),
        activation.ActivationFaultPoint(ActivationStep.INSERT_SINGLE_GENESIS),
        activation.ActivationFaultPoint(ActivationStep.SET_EXACT_V3_VERSION),
        activation.ActivationFaultPoint(
            ActivationStep.VERIFY_IN_TRANSACTION_SCHEMA_AND_GENESIS
        ),
        activation.ActivationFaultPoint(ActivationStep.FINAL_FAULT_CHECKPOINT),
    )
    manifest_names = {
        item.object_name for item in activation._canonical_manifest().rendered_objects
    }
    try:
        for target in targets:
            commits_before = _transaction_count(statements, "COMMIT")
            rollbacks_before = _transaction_count(statements, "ROLLBACK")

            def inject(point: activation.ActivationFaultPoint) -> None:
                if point == target:
                    raise _InjectedActivationFailure(repr(target))

            with pytest.raises(_InjectedActivationFailure):
                activation.activate_family_schema_v3(
                    connection,
                    _inputs(),
                    fault_injector=inject,
                )
            assert _transaction_count(statements, "COMMIT") == commits_before, target
            assert _transaction_count(statements, "ROLLBACK") == rollbacks_before + 1, target
            assert connection.in_transaction is False, target
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 2, target
            assert verify_exact_v2_activation_source(connection) == source, target
            present = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_schema")
                if row[0] is not None
            }
            assert present.isdisjoint(manifest_names), target
    finally:
        connection.close()


def test_commit_failure_rolls_back_once_and_closes_connection(
    tmp_path: Path,
) -> None:
    database = tmp_path / "commit-failure.db"
    _create_exact_v2(database)
    connection, _statements = _open_exact(database)
    transaction_actions: list[str] = []

    def deny_commit(
        action: int,
        argument1: str | None,
        _argument2: str | None,
        _database: str | None,
        _source: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_TRANSACTION:
            transaction_actions.append(argument1 or "")
            if argument1 == "COMMIT":
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(deny_commit)
    with pytest.raises(
        activation.FamilySchemaV3ActivationError,
        match="commit failed; connection closed",
    ):
        activation.activate_family_schema_v3(connection, _inputs())
    assert transaction_actions.count("COMMIT") == 1
    assert transaction_actions.count("ROLLBACK") == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("PRAGMA user_version")

    reopened = sqlite3.connect(str(database), cached_statements=0)
    try:
        assert reopened.execute("PRAGMA user_version").fetchone()[0] == 2
        assert verify_exact_v2_activation_source(reopened).schema_version == 2
    finally:
        reopened.close()


def test_rollback_failure_closes_connection_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "rollback-failure.db"
    _create_exact_v2(database)
    connection, _statements = _open_exact(database)
    transaction_actions: list[str] = []

    def deny_rollback(
        action: int,
        argument1: str | None,
        _argument2: str | None,
        _database: str | None,
        _source: str | None,
    ) -> int:
        if action == sqlite3.SQLITE_TRANSACTION:
            transaction_actions.append(argument1 or "")
            if argument1 == "ROLLBACK":
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(deny_rollback)

    def fail_at_first_ddl(point: activation.ActivationFaultPoint) -> None:
        if point == activation.ActivationFaultPoint(
            ActivationStep.APPLY_SEALED_DDL_STATEMENTS,
            1,
        ):
            raise _InjectedActivationFailure("kill before rollback failure")

    with pytest.raises(
        activation.FamilySchemaV3ActivationError,
        match="rollback failed; connection closed",
    ):
        activation.activate_family_schema_v3(
            connection,
            _inputs(),
            fault_injector=fail_at_first_ddl,
        )
    assert transaction_actions.count("COMMIT") == 0
    assert transaction_actions.count("ROLLBACK") == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connection.execute("PRAGMA user_version")

    reopened = sqlite3.connect(str(database), cached_statements=0)
    try:
        assert reopened.execute("PRAGMA user_version").fetchone()[0] == 2
        assert verify_exact_v2_activation_source(reopened).schema_version == 2
    finally:
        reopened.close()
