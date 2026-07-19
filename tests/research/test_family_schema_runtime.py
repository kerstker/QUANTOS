from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from quantos.contracts.canonical import canonical_json_bytes, canonical_sha256
from services.research.experiment_registry import family_schema_runtime as runtime
from services.research.experiment_registry.family_schema_composition import (
    compose_family_schema_v3,
)
from services.research.experiment_registry.family_schema_external_verification_c4 import (
    ActorProvenanceClaims,
    ActorProvenanceEvidence,
    ActorProvenanceVerificationRequest,
    C4ExternalProviderBundle,
    MultiPartyGrantClaims,
    MultiPartyGrantEvidence,
    MultiPartyGrantVerificationRequest,
)
from services.research.experiment_registry.family_schema_spec import (
    FamilySchemaActivationInputs,
    SqliteTrustTransitionStep,
)
from services.research.experiment_registry.family_schema_v3_activation import (
    activate_family_schema_v3,
)
from services.research.experiment_registry.family_schema_v3_verifier import (
    FamilyAuthorityExternalProviders,
    verify_family_authority,
)
from services.research.experiment_registry.store import SqliteExperimentStore


_AUTHORITY_ID = "authority-runtime-test"
_ACTIVATION_CODE_HASH = "a" * 64


class _GrantProvider:
    def verify(
        self,
        request: MultiPartyGrantVerificationRequest,
        /,
    ) -> MultiPartyGrantEvidence | None:
        return MultiPartyGrantEvidence(
            grant_id=request.grant_id,
            grant_record_hash=request.grant_record_hash,
            policy_id=request.policy_id,
            policy_version=request.policy_version,
            policy_hash=request.policy_hash,
            authority_id=request.authority_id,
            authority_head=request.authority_head,
            action=request.action,
            subject_record_type=request.subject_record_type,
            subject_id=request.subject_id,
            subject_record_hash=request.subject_record_hash,
            consumed_by_actor_id=request.consumed_by_actor_id,
            consumed_at_us=request.consumed_at_us,
            claims=MultiPartyGrantClaims(
                state="CONSUMED",
                verified_human_actor_count=3,
                distinct_human_actors=True,
                complete_hash_chain=True,
                grant_not_reused=True,
                subject_action_bound=True,
                consumption_time_bound=True,
                policy_verified=True,
                authority_head_verified=True,
            ),
        )


class _ActorProvider:
    def verify(
        self,
        request: ActorProvenanceVerificationRequest,
        /,
    ) -> ActorProvenanceEvidence | None:
        return ActorProvenanceEvidence(
            actor_id=request.actor_id,
            actor_kind=request.actor_kind,
            evidence_hash=request.evidence_hash,
            policy_id=request.policy_id,
            policy_version=request.policy_version,
            policy_hash=request.policy_hash,
            authority_id=request.authority_id,
            authority_head=request.authority_head,
            action=request.action,
            subject_record_type=request.subject_record_type,
            subject_id=request.subject_id,
            subject_record_hash=request.subject_record_hash,
            event_at_us=request.event_at_us,
            claims=ActorProvenanceClaims(
                provenance_verified=True,
                actor_kind_verified=True,
                action_context_bound=True,
                subject_context_bound=True,
                event_time_bound=True,
                evidence_hash_verified=True,
                policy_verified=True,
                authority_head_verified=True,
            ),
        )


def _external_providers() -> FamilyAuthorityExternalProviders:
    return FamilyAuthorityExternalProviders(
        c4=C4ExternalProviderBundle(
            multi_party_grant_evidence=_GrantProvider(),
            actor_provenance_evidence=_ActorProvider(),
        )
    )


def _activate(path: Path, *, authority_id: str = _AUTHORITY_ID) -> None:
    store = SqliteExperimentStore(str(path), create=True)
    store.close()
    connection = sqlite3.connect(str(path), cached_statements=0)
    try:
        activate_family_schema_v3(
            connection,
            FamilySchemaActivationInputs(
                authority_instance_id=authority_id,
                activation_code_manifest_hash=_ACTIVATION_CODE_HASH,
                activated_at_us=1_700_000_000_000_001,
            ),
        )
    finally:
        connection.close()


def _identity_hash(
    *,
    domain_separator: str,
    predecessor: str,
    source_fields: tuple[tuple[str, object], ...],
) -> str:
    return canonical_sha256(
        {
            "schema_version": "QUANTOS_AUTHORITY_DERIVED_ID_PAYLOAD_V1",
            "domain_separator": domain_separator,
            "authority_chain_predecessor": predecessor,
            "ordered_source_fields": [{"field_name": name, "field_value": value} for name, value in source_fields],
        }
    )


def _ordered_row(table_name: str, raw: dict[str, object]) -> dict[str, object]:
    table = next(item for item in compose_family_schema_v3().tables if item.table_name == table_name)
    assert {column.name for column in table.columns} == set(raw)
    return {column.name: raw[column.name] for column in table.columns}


def _governed_rows(path: Path) -> tuple[dict[str, object], dict[str, object]]:
    connection = sqlite3.connect(str(path), cached_statements=0)
    try:
        genesis_hash = str(connection.execute('SELECT "record_hash" FROM "family_authority_genesis"').fetchone()[0])
    finally:
        connection.close()
    event_id = _identity_hash(
        domain_separator="QUANTOS_RESEARCH_AUTHORITY_EVENT_ID_V1",
        predecessor=genesis_hash,
        source_fields=(
            ("authority_instance_id", _AUTHORITY_ID),
            ("event_ordinal", 1),
        ),
    )
    question_payload = {"question": "deterministic research question"}
    question_payload_json = canonical_json_bytes(question_payload).decode("utf-8")
    question_hash = canonical_sha256(question_payload)
    question_id = _identity_hash(
        domain_separator="QUANTOS_RESEARCH_QUESTION_ID_V1",
        predecessor=genesis_hash,
        source_fields=(
            ("authority_instance_id", _AUTHORITY_ID),
            ("question_hash", question_hash),
        ),
    )
    question: dict[str, object] = {
        "record_type": "RESEARCH_QUESTION_V1",
        "authority_instance_id": _AUTHORITY_ID,
        "genesis_record_hash": genesis_hash,
        "research_question_id": question_id,
        "question_hash": question_hash,
        "question_payload_json": question_payload_json,
        "authority_event_id": event_id,
        "issued_at_us": 2_000_000,
    }
    question["record_hash"] = canonical_sha256(question)
    event: dict[str, object] = {
        "record_type": "RESEARCH_AUTHORITY_EVENT_V1",
        "authority_instance_id": _AUTHORITY_ID,
        "genesis_record_hash": genesis_hash,
        "event_id": event_id,
        "event_ordinal": 1,
        "action": "QUESTION_ISSUED",
        "subject_record_type": question["record_type"],
        "subject_id": question_id,
        "subject_record_hash": question["record_hash"],
        "grant_id": "grant-1",
        "grant_record_hash": "b" * 64,
        "grant_policy_id": "MULTI_PARTY_GRANT_POLICY",
        "grant_policy_version": "1",
        "grant_policy_hash": "c" * 64,
        "grant_authority_id": "grant-authority-1",
        "grant_authority_head": "d" * 64,
        "audit_actor_id": "human-approver-3",
        "audit_actor_kind": "HUMAN",
        "audit_actor_evidence_hash": "e" * 64,
        "audit_actor_policy_id": "ACTOR_PROVENANCE_POLICY",
        "audit_actor_policy_version": "1",
        "audit_actor_policy_hash": "f" * 64,
        "audit_actor_authority_id": "identity-authority-1",
        "audit_actor_authority_head": "1" * 64,
        "event_at_us": 2_000_000,
        "prev_record_hash": genesis_hash,
    }
    event["record_hash"] = canonical_sha256(event)
    return (
        _ordered_row("research_authority_events", event),
        _ordered_row("research_questions", question),
    )


def _write_governed_rows(
    event: dict[str, object],
    question: dict[str, object],
) -> Callable[[runtime.FamilyAuthorityWriteSession], None]:
    def write(session: runtime.FamilyAuthorityWriteSession) -> None:
        session.append("research_authority_events", event)
        session.append("research_questions", question)

    return write


def _quantos_functions(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute("PRAGMA function_list")
        if str(row[0]).casefold().startswith("quantos_")
    )


def test_runtime_commits_valid_rows_and_reopens_as_fully_verified(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-success.db"
    _activate(database)
    event, question = _governed_rows(database)

    evidence = runtime.run_family_authority_transaction(
        str(database.resolve()),
        expected_authority_instance_id=_AUTHORITY_ID,
        callback=_write_governed_rows(event, question),
        external_providers=_external_providers(),
    )

    assert evidence.transition_steps == tuple(SqliteTrustTransitionStep)
    assert evidence.committed is True
    assert evidence.appended_row_counts == (
        ("research_authority_events", 1),
        ("research_questions", 1),
    )
    assert evidence.pre_verification.family_table_row_counts != (evidence.post_verification.family_table_row_counts)
    reopened = sqlite3.connect(str(database), cached_statements=0)
    reopened.execute("PRAGMA foreign_keys=ON")
    try:
        verified = verify_family_authority(
            reopened,
            expected_authority_instance_id=_AUTHORITY_ID,
            external_providers=_external_providers(),
        )
        assert dict(verified.family_table_row_counts)["research_questions"] == 1
        assert _quantos_functions(reopened) == ()
    finally:
        reopened.close()


def test_runtime_uses_fresh_exact_uncached_connection_and_closes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime-connect-profile.db"
    _activate(database)
    event, question = _governed_rows(database)
    original_connect = sqlite3.connect
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    opened: list[sqlite3.Connection] = []
    statements: list[str] = []

    def tracked_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        calls.append((args, dict(kwargs)))
        connection = original_connect(*args, **kwargs)
        assert type(connection) is sqlite3.Connection
        connection.set_trace_callback(statements.append)
        opened.append(connection)
        return connection

    monkeypatch.setattr(runtime.sqlite3, "connect", tracked_connect)
    runtime.run_family_authority_transaction(
        str(database.resolve()),
        expected_authority_instance_id=_AUTHORITY_ID,
        callback=_write_governed_rows(event, question),
        external_providers=_external_providers(),
    )

    runtime_call = calls[0]
    assert runtime_call[0] == (str(database.resolve()),)
    assert runtime_call[1]["cached_statements"] == 0
    assert runtime_call[1]["timeout"] == 0.0
    assert runtime_call[1]["isolation_level"] is None
    assert runtime_call[1]["uri"] is False
    assert "factory" not in runtime_call[1]
    normalized = tuple(statement.strip().upper() for statement in statements)
    trusted_off = tuple(index for index, statement in enumerate(normalized) if statement == "PRAGMA TRUSTED_SCHEMA=OFF")
    trusted_on = tuple(index for index, statement in enumerate(normalized) if statement == "PRAGMA TRUSTED_SCHEMA=ON")
    begin = normalized.index("BEGIN IMMEDIATE")
    commit = normalized.index("COMMIT")
    assert len(trusted_off) == 2
    assert len(trusted_on) == 1
    assert trusted_off[0] < begin < trusted_on[0] < commit < trusted_off[1]
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


class _ConnectionSubclass(sqlite3.Connection):
    pass


def test_runtime_rejects_a_connect_wrapper_returning_a_connection_subclass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime-subclass.db"
    _activate(database)
    original_connect = sqlite3.connect
    opened: list[sqlite3.Connection] = []

    def subclass_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        kwargs["factory"] = _ConnectionSubclass
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(runtime.sqlite3, "connect", subclass_connect)
    with pytest.raises(runtime.FamilyAuthorityRuntimeError, match="exact sqlite3.Connection"):
        runtime.run_family_authority_transaction(
            str(database.resolve()),
            expected_authority_instance_id=_AUTHORITY_ID,
            callback=lambda _session: None,
        )
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


def test_session_rejects_genesis_and_nonexact_columns_before_valid_append(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-column-boundary.db"
    _activate(database)
    event, question = _governed_rows(database)
    retained: list[runtime.FamilyAuthorityWriteSession] = []

    def write(session: runtime.FamilyAuthorityWriteSession) -> None:
        retained.append(session)
        with pytest.raises(runtime.FamilyAuthorityRuntimeError, match="genesis"):
            session.append("family_authority_genesis", {})
        missing = dict(event)
        missing.pop(next(reversed(missing)))
        with pytest.raises(runtime.FamilyAuthorityRuntimeError, match="exact sealed column"):
            session.append("research_authority_events", missing)
        extra = {**event, "extra": "forbidden"}
        with pytest.raises(runtime.FamilyAuthorityRuntimeError, match="exact sealed column"):
            session.append("research_authority_events", extra)
        reversed_row = dict(reversed(tuple(event.items())))
        with pytest.raises(runtime.FamilyAuthorityRuntimeError, match="exact sealed column"):
            session.append("research_authority_events", reversed_row)
        session.append("research_authority_events", event)
        session.append("research_questions", question)

    runtime.run_family_authority_transaction(
        str(database.resolve()),
        expected_authority_instance_id=_AUTHORITY_ID,
        callback=write,
        external_providers=_external_providers(),
    )
    assert not hasattr(retained[0], "connection")
    with pytest.raises(runtime.FamilyAuthorityRuntimeError, match="lease"):
        retained[0].append("research_questions", question)


def test_authorizer_denies_all_mutation_escape_classes_and_unknown_function(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime-authorizer.db"
    _activate(database)
    event, question = _governed_rows(database)
    original_connect = sqlite3.connect
    captured: list[sqlite3.Connection] = []

    def tracked_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = original_connect(*args, **kwargs)
        captured.append(connection)
        return connection

    monkeypatch.setattr(runtime.sqlite3, "connect", tracked_connect)

    def write(session: runtime.FamilyAuthorityWriteSession) -> None:
        connection = captured[0]
        assert connection.execute('SELECT COUNT(*) FROM "family_authority_genesis"').fetchone() == (1,)
        connection.create_function("runtime_unknown", 0, lambda: 1)
        attacks = (
            ('CREATE TABLE "escape" ("id" INTEGER) STRICT', "not authorized"),
            ('CREATE TEMP TABLE "temp_escape" ("id" INTEGER) STRICT', "not authorized"),
            ('DROP TABLE "research_questions"', "not authorized"),
            ('ALTER TABLE "research_questions" ADD COLUMN "escape" TEXT', "not authorized"),
            ('UPDATE "family_authority_genesis" SET "singleton_key"=2', "not authorized"),
            ('DELETE FROM "family_authority_genesis"', "not authorized"),
            ('INSERT INTO "bandit_state" ("id", "snapshot_json") VALUES (7, "{}")', "not authorized"),
            ('INSERT INTO "family_authority_genesis" DEFAULT VALUES', "not authorized"),
            ("ATTACH DATABASE ':memory:' AS \"escape\"", "not authorized"),
            ('DETACH DATABASE "temp"', "not authorized"),
            ("SELECT load_extension('escape')", "not authorized"),
            ("PRAGMA writable_schema=ON", "not authorized"),
            ("PRAGMA journal_mode=WAL", "not authorized"),
            ("PRAGMA foreign_keys=OFF", "not authorized"),
            ("ANALYZE", "not authorized"),
            ("REINDEX", "not authorized"),
            ("VACUUM", "cannot VACUUM"),
            ("BEGIN", "not authorized"),
            ('SAVEPOINT "escape"', "not authorized"),
            ("SELECT abs(1)", "not authorized"),
            ("SELECT runtime_unknown()", "not authorized"),
        )
        for statement, message in attacks:
            with pytest.raises(sqlite3.DatabaseError, match=message):
                connection.execute(statement)
        session.append("research_authority_events", event)
        session.append("research_questions", question)

    runtime.run_family_authority_transaction(
        str(database.resolve()),
        expected_authority_instance_id=_AUTHORITY_ID,
        callback=write,
        external_providers=_external_providers(),
    )


def test_callback_failure_rolls_back_releases_lease_and_closes_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime-rollback.db"
    _activate(database)
    event, _ = _governed_rows(database)
    original_connect = sqlite3.connect
    captured: list[sqlite3.Connection] = []

    def tracked_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = original_connect(*args, **kwargs)
        captured.append(connection)
        return connection

    monkeypatch.setattr(runtime.sqlite3, "connect", tracked_connect)

    def write(session: runtime.FamilyAuthorityWriteSession) -> None:
        session.append("research_authority_events", event)
        raise RuntimeError("injected callback failure")

    with pytest.raises(RuntimeError, match="injected callback failure"):
        runtime.run_family_authority_transaction(
            str(database.resolve()),
            expected_authority_instance_id=_AUTHORITY_ID,
            callback=write,
            external_providers=_external_providers(),
        )
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        captured[0].execute("SELECT 1")
    reopened = original_connect(str(database), cached_statements=0)
    try:
        assert reopened.execute('SELECT COUNT(*) FROM "research_authority_events"').fetchone() == (0,)
    finally:
        reopened.close()


def test_sqlite_writer_lock_blocks_a_second_connection(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runtime-writer-lock.db"
    _activate(database)
    event, question = _governed_rows(database)

    def write(session: runtime.FamilyAuthorityWriteSession) -> None:
        rival = sqlite3.connect(str(database), cached_statements=0, timeout=0.0)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                rival.execute("BEGIN IMMEDIATE")
        finally:
            rival.close()
        session.append("research_authority_events", event)
        session.append("research_questions", question)

    runtime.run_family_authority_transaction(
        str(database.resolve()),
        expected_authority_instance_id=_AUTHORITY_ID,
        callback=write,
        external_providers=_external_providers(),
    )


def test_owner_token_cas_rejects_same_path_even_with_independent_sqlite_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime-owner-cas.db"
    _activate(database)
    event, question = _governed_rows(database)
    original_connect = sqlite3.connect
    source = original_connect(str(database), cached_statements=0)
    try:
        serialized = source.serialize()
    finally:
        source.close()
    call_count = 0

    def split_lock_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return original_connect(*args, **kwargs)
        connection = original_connect(":memory:", cached_statements=0)
        connection.deserialize(serialized)
        return connection

    monkeypatch.setattr(runtime.sqlite3, "connect", split_lock_connect)

    def write(session: runtime.FamilyAuthorityWriteSession) -> None:
        with pytest.raises(runtime.FamilyAuthorityRuntimeError, match="owner token"):
            runtime.run_family_authority_transaction(
                str(database.resolve()),
                expected_authority_instance_id=_AUTHORITY_ID,
                callback=lambda _session: None,
            )
        session.append("research_authority_events", event)
        session.append("research_questions", question)

    runtime.run_family_authority_transaction(
        str(database.resolve()),
        expected_authority_instance_id=_AUTHORITY_ID,
        callback=write,
        external_providers=_external_providers(),
    )


@pytest.mark.parametrize(
    "path_value",
    (":memory:", "file:authority.db?mode=ro", "relative-authority.db"),
)
def test_runtime_rejects_non_file_or_nonabsolute_paths(path_value: str) -> None:
    with pytest.raises((TypeError, runtime.FamilyAuthorityRuntimeError)):
        runtime.run_family_authority_transaction(
            path_value,
            expected_authority_instance_id=_AUTHORITY_ID,
            callback=lambda _session: None,
        )


def test_runtime_rejects_wrong_identity_and_source_version(tmp_path: Path) -> None:
    wrong_identity = tmp_path / "runtime-wrong-identity.db"
    _activate(wrong_identity, authority_id="other-authority")
    with pytest.raises(runtime.FamilyAuthorityRuntimeError, match="verification"):
        runtime.run_family_authority_transaction(
            str(wrong_identity.resolve()),
            expected_authority_instance_id=_AUTHORITY_ID,
            callback=lambda _session: None,
        )

    wrong_version = tmp_path / "runtime-wrong-version.db"
    _activate(wrong_version)
    connection = sqlite3.connect(str(wrong_version), cached_statements=0)
    connection.execute("PRAGMA user_version=4")
    connection.close()
    with pytest.raises(runtime.FamilyAuthorityRuntimeError, match="version"):
        runtime.run_family_authority_transaction(
            str(wrong_version.resolve()),
            expected_authority_instance_id=_AUTHORITY_ID,
            callback=lambda _session: None,
        )


def test_catalog_source_change_between_pretrust_and_full_verification_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime-source-race.db"
    _activate(database)
    original_install = runtime._install_authorizer

    def mutate_then_install(*args: object, **kwargs: object) -> object:
        connection = args[0]
        assert type(connection) is sqlite3.Connection
        connection.execute('CREATE TABLE "source_race" ("id" INTEGER) STRICT')
        return original_install(*args, **kwargs)

    monkeypatch.setattr(runtime, "_install_authorizer", mutate_then_install)
    with pytest.raises(runtime.FamilyAuthorityRuntimeError, match="verification"):
        runtime.run_family_authority_transaction(
            str(database.resolve()),
            expected_authority_instance_id=_AUTHORITY_ID,
            callback=lambda _session: None,
        )


def test_version_double_read_rejects_change_across_trust_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime-version-race.db"
    _activate(database)
    original_read = runtime._read_user_version
    reads = 0

    def changed_second_read(connection: sqlite3.Connection) -> int:
        nonlocal reads
        reads += 1
        value = original_read(connection)
        return value if reads == 1 else value + 1

    monkeypatch.setattr(runtime, "_read_user_version", changed_second_read)
    with pytest.raises(runtime.FamilyAuthorityRuntimeError, match="changed across"):
        runtime.run_family_authority_transaction(
            str(database.resolve()),
            expected_authority_instance_id=_AUTHORITY_ID,
            callback=lambda _session: None,
        )
    assert reads == 2


def test_direct_family_insert_outside_leased_session_is_rolled_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "runtime-direct-insert.db"
    _activate(database)
    event, question = _governed_rows(database)
    original_connect = sqlite3.connect
    captured: list[sqlite3.Connection] = []

    def tracked_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = original_connect(*args, **kwargs)
        captured.append(connection)
        return connection

    monkeypatch.setattr(runtime.sqlite3, "connect", tracked_connect)

    def write(session: runtime.FamilyAuthorityWriteSession) -> None:
        columns = tuple(event)
        captured[0].execute(
            'INSERT INTO "research_authority_events" ('
            + ", ".join(f'"{column}"' for column in columns)
            + ") VALUES ("
            + ", ".join("?" for _ in columns)
            + ")",
            tuple(event[column] for column in columns),
        )
        session.append("research_questions", question)

    with pytest.raises(runtime.FamilyAuthorityRuntimeError, match="leased session"):
        runtime.run_family_authority_transaction(
            str(database.resolve()),
            expected_authority_instance_id=_AUTHORITY_ID,
            callback=write,
            external_providers=_external_providers(),
        )
    reopened = original_connect(str(database), cached_statements=0)
    try:
        assert reopened.execute('SELECT COUNT(*) FROM "research_authority_events"').fetchone() == (0,)
        assert reopened.execute('SELECT COUNT(*) FROM "research_questions"').fetchone() == (0,)
    finally:
        reopened.close()
