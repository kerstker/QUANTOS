from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path

import pytest

from quantos.contracts.canonical import canonical_json_bytes, canonical_sha256
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
from services.research.experiment_registry.family_schema_semantic_functions import (
    FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS,
)
from services.research.experiment_registry.family_schema_v3_verifier import (
    FamilyAuthorityExternalProviders,
    FamilyAuthorityVerificationError,
    _canonical_manifest,
    _register_semantic_functions,
    verify_family_authority,
)
from services.research.experiment_registry.store import SqliteExperimentStore


_AUTHORITY_ID = "authority-test"
_ORIGIN_HASH = "a" * 64
_ACTIVATION_CODE_HASH = "b" * 64


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


def _empty_v3_connection() -> sqlite3.Connection:
    store = SqliteExperimentStore(":memory:", create=True)
    builder = store._conn
    builder.execute("PRAGMA foreign_keys=ON")
    manifest = _canonical_manifest()
    for item in manifest.rendered_objects:
        builder.execute(item.statement_sql)
    _register_semantic_functions(builder)
    genesis = {
        "singleton_key": 1,
        "authority_instance_id": _AUTHORITY_ID,
        "v2_origin_fingerprint": _ORIGIN_HASH,
        "sealed_v3_manifest_hash": manifest.manifest_sha256,
        "activation_code_manifest_evidence_hash": _ACTIVATION_CODE_HASH,
        "migration_id": "FAMILY_SCHEMA_V2_TO_V3_V1",
        "activated_at_us": 1_000_000,
        "record_type": "FAMILY_AUTHORITY_GENESIS_V1",
    }
    genesis["record_hash"] = canonical_sha256(genesis)
    columns = tuple(genesis)
    builder.execute(
        'INSERT INTO "family_authority_genesis" ('
        + ", ".join(f'"{column}"' for column in columns)
        + ") VALUES ("
        + ", ".join("?" for _ in columns)
        + ")",
        tuple(genesis[column] for column in columns),
    )
    builder.execute("PRAGMA user_version=3")
    builder.commit()
    serialized = builder.serialize()
    store.close()
    connection = sqlite3.connect(":memory:", cached_statements=0)
    connection.deserialize(serialized)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _insert_row(
    connection: sqlite3.Connection,
    table_name: str,
    row: Mapping[str, object],
) -> None:
    table = next(table for table in compose_family_schema_v3().tables if table.table_name == table_name)
    columns = tuple(column.name for column in table.columns)
    assert set(columns) == set(row)
    connection.execute(
        f'INSERT INTO "{table_name}" ('
        + ", ".join(f'"{column}"' for column in columns)
        + ") VALUES ("
        + ", ".join("?" for _ in columns)
        + ")",
        tuple(row[column] for column in columns),
    )


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


def _clean_clone(connection: sqlite3.Connection) -> sqlite3.Connection:
    serialized = connection.serialize()
    connection.close()
    clone = sqlite3.connect(":memory:", cached_statements=0)
    clone.deserialize(serialized)
    clone.row_factory = sqlite3.Row
    clone.execute("PRAGMA foreign_keys=ON")
    return clone


def _insert_governed_question(
    connection: sqlite3.Connection,
) -> tuple[sqlite3.Connection, dict[str, object], dict[str, object]]:
    _register_semantic_functions(connection)
    genesis_hash = str(connection.execute('SELECT "record_hash" FROM "family_authority_genesis"').fetchone()[0])
    event_id = _identity_hash(
        domain_separator="QUANTOS_RESEARCH_AUTHORITY_EVENT_ID_V1",
        predecessor=genesis_hash,
        source_fields=(("authority_instance_id", _AUTHORITY_ID), ("event_ordinal", 1)),
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
        "grant_record_hash": "c" * 64,
        "grant_policy_id": "MULTI_PARTY_GRANT_POLICY",
        "grant_policy_version": "1",
        "grant_policy_hash": "d" * 64,
        "grant_authority_id": "grant-authority-1",
        "grant_authority_head": "e" * 64,
        "audit_actor_id": "human-approver-3",
        "audit_actor_kind": "HUMAN",
        "audit_actor_evidence_hash": "f" * 64,
        "audit_actor_policy_id": "ACTOR_PROVENANCE_POLICY",
        "audit_actor_policy_version": "1",
        "audit_actor_policy_hash": "1" * 64,
        "audit_actor_authority_id": "identity-authority-1",
        "audit_actor_authority_head": "2" * 64,
        "event_at_us": 2_000_000,
        "prev_record_hash": genesis_hash,
    }
    event["record_hash"] = canonical_sha256(event)
    _insert_row(connection, "research_authority_events", event)
    _insert_row(connection, "research_questions", question)
    connection.commit()
    return _clean_clone(connection), event, question


def test_verify_family_authority_accepts_empty_exact_v3_with_one_genesis() -> None:
    connection = _empty_v3_connection()
    total_changes = connection.total_changes
    in_transaction = connection.in_transaction
    try:
        evidence = verify_family_authority(
            connection,
            expected_authority_instance_id=_AUTHORITY_ID,
        )
        assert evidence.schema_version == 3
        assert evidence.authority_instance_id == _AUTHORITY_ID
        assert evidence.manifest_sha256 == _canonical_manifest().manifest_sha256
        assert dict(evidence.family_table_row_counts)["family_authority_genesis"] == 1
        assert sum(dict(evidence.family_table_row_counts).values()) == 1
        assert evidence.named_binding_count == 124
        assert evidence.named_owner_row_checks == 2
        assert evidence.structural_rule_count == 228
        assert connection.total_changes == total_changes
        assert connection.in_transaction is in_transaction
    finally:
        connection.close()


def test_verify_family_authority_rejects_deployment_identity_mismatch() -> None:
    connection = _empty_v3_connection()
    try:
        with pytest.raises(FamilyAuthorityVerificationError, match="authority instance"):
            verify_family_authority(
                connection,
                expected_authority_instance_id="different-authority",
            )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "identity",
    ("", " ", "e\u0301", "a" * 256, "invalid identity", "bad\\identity"),
)
def test_verify_family_authority_uses_activation_identity_contract(
    identity: str,
) -> None:
    connection = _empty_v3_connection()
    try:
        with pytest.raises(ValueError, match="deployment identity"):
            verify_family_authority(
                connection,
                expected_authority_instance_id=identity,
            )
    finally:
        connection.close()


@pytest.mark.parametrize("version", (0, 1, 2, 4, 5, 41))
def test_verify_family_authority_requires_exact_user_version_three(
    version: int,
) -> None:
    connection = _empty_v3_connection()
    try:
        connection.execute(f"PRAGMA user_version={version}")
        with pytest.raises(FamilyAuthorityVerificationError, match="user_version 3"):
            verify_family_authority(
                connection,
                expected_authority_instance_id=_AUTHORITY_ID,
            )
    finally:
        connection.close()


def test_verify_family_authority_rejects_canonical_schema_tamper() -> None:
    connection = _empty_v3_connection()
    try:
        index_name = connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type='index' AND name NOT LIKE 'sqlite_autoindex_%' "
            "AND tbl_name='research_questions' ORDER BY name LIMIT 1"
        ).fetchone()[0]
        connection.execute(f'DROP INDEX "{index_name}"')
        connection.commit()
        with pytest.raises(FamilyAuthorityVerificationError, match="sqlite_schema"):
            verify_family_authority(
                connection,
                expected_authority_instance_id=_AUTHORITY_ID,
            )
    finally:
        connection.close()


def test_verify_family_authority_accepts_nonempty_governed_business_rows() -> None:
    connection = _empty_v3_connection()
    try:
        connection, _, _ = _insert_governed_question(connection)
        evidence = verify_family_authority(
            connection,
            expected_authority_instance_id=_AUTHORITY_ID,
            external_providers=_external_providers(),
        )
        counts = dict(evidence.family_table_row_counts)
        assert counts["research_authority_events"] == 1
        assert counts["research_questions"] == 1
        assert evidence.named_owner_row_checks >= 7
        assert evidence.structural_row_checks > 0
    finally:
        connection.close()


def test_verify_family_authority_rejects_missing_provider_for_governed_row() -> None:
    connection = _empty_v3_connection()
    try:
        connection, _, _ = _insert_governed_question(connection)
        with pytest.raises(FamilyAuthorityVerificationError, match="named family verifier"):
            verify_family_authority(
                connection,
                expected_authority_instance_id=_AUTHORITY_ID,
            )
    finally:
        connection.close()


def test_verify_family_authority_rejects_business_row_hash_tamper() -> None:
    connection = _empty_v3_connection()
    try:
        connection, _, _ = _insert_governed_question(connection)
        trigger_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name='research_authority_events_no_update'"
            ).fetchone()[0]
        )
        connection.execute('DROP TRIGGER "research_authority_events_no_update"')
        connection.execute(
            'UPDATE "research_authority_events" SET "record_hash"=?',
            ("0" * 64,),
        )
        connection.execute(trigger_sql)
        connection.commit()
        with pytest.raises(FamilyAuthorityVerificationError, match="structural family verifier"):
            verify_family_authority(
                connection,
                expected_authority_instance_id=_AUTHORITY_ID,
                external_providers=_external_providers(),
            )
    finally:
        connection.close()


def test_verify_family_authority_rejects_cross_table_reference_tamper() -> None:
    connection = _empty_v3_connection()
    try:
        connection, _, question = _insert_governed_question(connection)
        trigger_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name='research_questions_no_update'"
            ).fetchone()[0]
        )
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute('DROP TRIGGER "research_questions_no_update"')
        mutated = dict(question)
        mutated["authority_event_id"] = "missing-authority-event"
        mutated["record_hash"] = canonical_sha256(
            {key: value for key, value in mutated.items() if key != "record_hash"}
        )
        connection.execute(
            'UPDATE "research_questions" SET "authority_event_id"=?, "record_hash"=?',
            (mutated["authority_event_id"], mutated["record_hash"]),
        )
        connection.execute(trigger_sql)
        connection.commit()
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(FamilyAuthorityVerificationError, match="foreign_key_check"):
            verify_family_authority(
                connection,
                expected_authority_instance_id=_AUTHORITY_ID,
                external_providers=_external_providers(),
            )
    finally:
        connection.close()


class _NoOpRegistrationConnection(sqlite3.Connection):
    def create_function(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def create_aggregate(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


def test_verify_family_authority_rejects_connection_subclass_with_noop_udf_registration() -> None:
    source = _empty_v3_connection()
    connection = sqlite3.connect(
        ":memory:",
        factory=_NoOpRegistrationConnection,
        cached_statements=0,
    )
    try:
        source.backup(connection)
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(TypeError, match="exact sqlite3.Connection"):
            verify_family_authority(
                connection,
                expected_authority_instance_id=_AUTHORITY_ID,
            )
    finally:
        source.close()
        connection.close()


def test_verify_family_authority_preserves_caller_owned_transaction() -> None:
    connection = _empty_v3_connection()
    try:
        connection.execute("BEGIN")
        total_changes = connection.total_changes
        evidence = verify_family_authority(
            connection,
            expected_authority_instance_id=_AUTHORITY_ID,
        )
        assert evidence.schema_version == 3
        assert connection.in_transaction is True
        assert connection.total_changes == total_changes
        connection.rollback()
    finally:
        connection.close()


def test_verify_family_authority_rejects_unreviewed_quantos_function_registration() -> None:
    connection = _empty_v3_connection()
    try:
        connection.create_function(
            "quantos_unreviewed_semantic_function",
            1,
            lambda value: value,
            deterministic=True,
        )
        with pytest.raises(FamilyAuthorityVerificationError, match="pre-existing Quantos"):
            verify_family_authority(
                connection,
                expected_authority_instance_id=_AUTHORITY_ID,
            )
    finally:
        connection.close()


def test_verify_family_authority_rejects_even_one_preexisting_canonical_quantos_function() -> None:
    connection = _empty_v3_connection()
    try:
        implementation = FAMILY_SCHEMA_SQLITE_SEMANTIC_IMPLEMENTATIONS[0]
        connection.create_function(
            implementation.sqlite_name,
            1,
            lambda value: value,
            deterministic=True,
        )
        with pytest.raises(FamilyAuthorityVerificationError, match="pre-existing Quantos"):
            verify_family_authority(
                connection,
                expected_authority_instance_id=_AUTHORITY_ID,
            )
    finally:
        connection.close()


def test_verify_family_authority_leaves_no_quantos_functions_on_success_or_error() -> None:
    connection = _empty_v3_connection()
    try:
        verify_family_authority(
            connection,
            expected_authority_instance_id=_AUTHORITY_ID,
        )
        assert not tuple(
            row
            for row in connection.execute("PRAGMA function_list")
            if str(row[0]).casefold().startswith("quantos_")
        )
        with pytest.raises(FamilyAuthorityVerificationError, match="deployment identity"):
            verify_family_authority(
                connection,
                expected_authority_instance_id="wrong-authority",
            )
        assert not tuple(
            row
            for row in connection.execute("PRAGMA function_list")
            if str(row[0]).casefold().startswith("quantos_")
        )
    finally:
        connection.close()


def test_verify_family_authority_clone_sees_uncommitted_rows_without_changing_caller() -> None:
    connection = _empty_v3_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            'INSERT INTO "bandit_state" ("id", "snapshot_json") VALUES (?, ?)',
            (1, "{}"),
        )
        before_changes = connection.total_changes
        evidence = verify_family_authority(
            connection,
            expected_authority_instance_id=_AUTHORITY_ID,
        )
        assert dict(evidence.inherited_table_row_counts)["bandit_state"] == 1
        assert connection.in_transaction is True
        assert connection.total_changes == before_changes
        assert not tuple(
            row
            for row in connection.execute("PRAGMA function_list")
            if str(row[0]).casefold().startswith("quantos_")
        )
        connection.rollback()
    finally:
        connection.close()


def test_verify_family_authority_rejects_genesis_manifest_binding_tamper() -> None:
    connection = _empty_v3_connection()
    try:
        trigger_sql = str(
            connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type='trigger' AND name='family_authority_genesis_no_update'"
            ).fetchone()[0]
        )
        row = dict(connection.execute('SELECT * FROM "family_authority_genesis"').fetchone())
        row["sealed_v3_manifest_hash"] = "3" * 64
        row["record_hash"] = canonical_sha256({key: value for key, value in row.items() if key != "record_hash"})
        connection.execute('DROP TRIGGER "family_authority_genesis_no_update"')
        connection.execute(
            'UPDATE "family_authority_genesis" SET "sealed_v3_manifest_hash"=?, "record_hash"=?',
            (row["sealed_v3_manifest_hash"], row["record_hash"]),
        )
        connection.execute(trigger_sql)
        connection.commit()
        with pytest.raises(FamilyAuthorityVerificationError, match="sealed v3 manifest"):
            verify_family_authority(
                connection,
                expected_authority_instance_id=_AUTHORITY_ID,
            )
    finally:
        connection.close()


def test_verify_family_authority_issues_only_read_statements() -> None:
    connection = _empty_v3_connection()
    statements: list[str] = []
    connection.execute("PRAGMA query_only=ON")
    connection.set_trace_callback(statements.append)
    try:
        verify_family_authority(
            connection,
            expected_authority_instance_id=_AUTHORITY_ID,
        )
    finally:
        connection.set_trace_callback(None)
        connection.close()
    forbidden = (
        "ALTER ",
        "BEGIN",
        "COMMIT",
        "CREATE ",
        "DELETE ",
        "DROP ",
        "INSERT ",
        "RELEASE ",
        "REPLACE ",
        "ROLLBACK",
        "SAVEPOINT ",
        "UPDATE ",
    )
    normalized = tuple(statement.lstrip().upper() for statement in statements)
    assert normalized
    assert not any(statement.startswith(forbidden) for statement in normalized)
    assert not any(statement.startswith("PRAGMA ") and "=" in statement for statement in normalized)


class _MutatingGrantProvider(_GrantProvider):
    def __init__(self, writer: sqlite3.Connection) -> None:
        self._writer = writer
        self._mutated = False

    def verify(
        self,
        request: MultiPartyGrantVerificationRequest,
        /,
    ) -> MultiPartyGrantEvidence | None:
        if not self._mutated:
            self._writer.execute(
                'INSERT INTO "bandit_state" ("id", "snapshot_json") VALUES (1, ?)',
                ("{}",),
            )
            self._writer.commit()
            self._mutated = True
        return super().verify(request)


def test_verify_family_authority_rejects_source_change_during_provider_verification(
    tmp_path: Path,
) -> None:
    source = _empty_v3_connection()
    source, _, _ = _insert_governed_question(source)
    database_path = tmp_path / "family-authority-v3.db"
    destination = sqlite3.connect(database_path)
    source.backup(destination)
    destination.close()
    source.close()

    reader = sqlite3.connect(database_path, cached_statements=0)
    writer = sqlite3.connect(database_path, cached_statements=0)
    reader.execute("PRAGMA foreign_keys=ON")
    providers = FamilyAuthorityExternalProviders(
        c4=C4ExternalProviderBundle(
            multi_party_grant_evidence=_MutatingGrantProvider(writer),
            actor_provenance_evidence=_ActorProvider(),
        )
    )
    try:
        with pytest.raises(FamilyAuthorityVerificationError, match="source changed"):
            verify_family_authority(
                reader,
                expected_authority_instance_id=_AUTHORITY_ID,
                external_providers=providers,
            )
        assert reader.in_transaction is False
        assert reader.total_changes == 0
    finally:
        writer.close()
        reader.close()
