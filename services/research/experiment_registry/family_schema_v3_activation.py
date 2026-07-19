"""Offline, fail-closed activation of one exact-v2 store as exact schema v3."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from quantos.contracts.canonical import canonical_sha256

from .family_schema import FamilySchemaManifest, seal_family_schema_v3
from .family_schema_spec import (
    ActivationStep,
    FAMILY_AUTHORITY_GENESIS_CONTRACT,
    FAMILY_SCHEMA_V3_ACTIVATION_CONTRACT,
    FamilySchemaActivationInputs,
)
from .family_schema_v2_activation import (
    ExactV2ActivationSourceEvidence,
    verify_exact_v2_activation_source,
)
from services.research.ports.persistence import ExperimentStoreError

if TYPE_CHECKING:
    from .family_schema_v3_verifier import (
        FamilyAuthorityExternalProviders,
        FamilyAuthorityVerificationEvidence,
    )


class FamilySchemaV3ActivationError(ExperimentStoreError):
    """The supplied store cannot be atomically activated as exact schema v3."""


_ACTIVATION_OWNER_GUARD = threading.Lock()
_ACTIVE_ACTIVATION_OWNERS: dict[int, object] = {}


def _claim_activation_owner(connection: sqlite3.Connection) -> object:
    """CAS one non-owning process-local marker before any transaction control."""

    if type(connection) is not sqlite3.Connection:
        raise TypeError("connection must be an exact sqlite3.Connection")
    connection_id = id(connection)
    token = object()
    with _ACTIVATION_OWNER_GUARD:
        if connection_id in _ACTIVE_ACTIVATION_OWNERS:
            raise FamilySchemaV3ActivationError(
                "sqlite connection already has an active family-schema activation owner"
            )
        _ACTIVE_ACTIVATION_OWNERS[connection_id] = token
    return token


def _require_activation_owner(
    connection: sqlite3.Connection,
    token: object,
) -> None:
    with _ACTIVATION_OWNER_GUARD:
        if _ACTIVE_ACTIVATION_OWNERS.get(id(connection)) is not token:
            raise FamilySchemaV3ActivationError(
                "family-schema activation requires its exact owner CAS token"
            )


def _release_activation_owner(
    connection: sqlite3.Connection,
    token: object,
) -> None:
    connection_id = id(connection)
    with _ACTIVATION_OWNER_GUARD:
        if _ACTIVE_ACTIVATION_OWNERS.get(connection_id) is not token:
            raise FamilySchemaV3ActivationError(
                "family-schema activation owner marker lost CAS identity"
            )
        del _ACTIVE_ACTIVATION_OWNERS[connection_id]


@dataclass(frozen=True, slots=True)
class ActivationFaultPoint:
    """One deterministic post-action kill point used by atomicity tests."""

    step: ActivationStep
    ddl_ordinal: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.step, ActivationStep):
            raise TypeError("activation fault step must be ActivationStep")
        if self.step is ActivationStep.APPLY_SEALED_DDL_STATEMENTS:
            if type(self.ddl_ordinal) is not int or self.ddl_ordinal < 1:
                raise ValueError("DDL fault point requires a positive statement ordinal")
        elif self.ddl_ordinal is not None:
            raise ValueError("only a DDL fault point may carry a statement ordinal")


ActivationFaultInjector = Callable[[ActivationFaultPoint], None]


@dataclass(frozen=True, slots=True)
class FamilySchemaV3ActivationEvidence:
    """Committed activation evidence bound to the source, seal, and genesis."""

    authority_instance_id: str
    source_evidence: ExactV2ActivationSourceEvidence
    sealed_v3_manifest_hash: str
    activation_code_manifest_evidence_hash: str
    genesis_record_hash: str
    activated_at_us: int
    ddl_statement_count: int
    verification_evidence: FamilyAuthorityVerificationEvidence


@lru_cache(maxsize=1)
def _canonical_manifest() -> FamilySchemaManifest:
    """Resolve and validate the immutable canonical seal once per process."""

    return seal_family_schema_v3()


def _schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None or len(row) != 1 or type(row[0]) is not int:
        raise FamilySchemaV3ActivationError("schema version read returned an invalid shape")
    return row[0]


def _require_exact_v2_version(connection: sqlite3.Connection) -> None:
    version = _schema_version(connection)
    if version != FAMILY_SCHEMA_V3_ACTIVATION_CONTRACT.source_version:
        raise FamilySchemaV3ActivationError(
            f"family schema activation requires exact user_version 2, got {version}"
        )


def _enable_and_confirm_foreign_keys(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys = ON")
    row = connection.execute("PRAGMA foreign_keys").fetchone()
    if row is None or len(row) != 1 or row[0] != 1:
        raise FamilySchemaV3ActivationError(
            "family schema activation requires foreign_keys=ON outside a transaction"
        )


def _reject_v3_object_collisions(
    connection: sqlite3.Connection,
    manifest: FamilySchemaManifest,
) -> None:
    expected_names = {item.object_name for item in manifest.rendered_objects}
    present_names = {
        str(row[0])
        for row in connection.execute("SELECT name FROM main.sqlite_schema")
        if row[0] is not None
    }
    collisions = tuple(sorted(expected_names & present_names))
    if collisions:
        raise FamilySchemaV3ActivationError(
            f"pre-existing exact-v3 object-name collision: {collisions!r}"
        )


def _checkpoint(
    fault_injector: ActivationFaultInjector | None,
    step: ActivationStep,
    ddl_ordinal: int | None = None,
) -> None:
    if fault_injector is not None:
        fault_injector(ActivationFaultPoint(step, ddl_ordinal))


def _genesis_payload(
    inputs: FamilySchemaActivationInputs,
    source_evidence: ExactV2ActivationSourceEvidence,
    manifest: FamilySchemaManifest,
) -> dict[str, object]:
    contract = FAMILY_AUTHORITY_GENESIS_CONTRACT
    return {
        contract.singleton_key_column: contract.singleton_key_value,
        "authority_instance_id": inputs.authority_instance_id,
        "v2_origin_fingerprint": source_evidence.origin_fingerprint,
        "sealed_v3_manifest_hash": manifest.manifest_sha256,
        "activation_code_manifest_evidence_hash": inputs.activation_code_manifest_hash,
        "migration_id": contract.migration_id_literal,
        "activated_at_us": inputs.activated_at_us,
        "record_type": contract.record_type_literal,
    }


def _insert_genesis(
    connection: sqlite3.Connection,
    payload: dict[str, object],
) -> str:
    record_hash = canonical_sha256(payload)
    row = {**payload, "record_hash": record_hash}
    columns = tuple(row)
    connection.execute(
        'INSERT INTO "family_authority_genesis" ('
        + ", ".join(f'"{column}"' for column in columns)
        + ") VALUES ("
        + ", ".join("?" for _ in columns)
        + ")",
        tuple(row[column] for column in columns),
    )
    return record_hash


def _close_after_transaction_control_failure(
    connection: sqlite3.Connection,
) -> None:
    try:
        connection.close()
    except BaseException:
        pass


def activate_family_schema_v3(
    connection: sqlite3.Connection,
    inputs: FamilySchemaActivationInputs,
    *,
    external_providers: FamilyAuthorityExternalProviders | None = None,
    fault_injector: ActivationFaultInjector | None = None,
) -> FamilySchemaV3ActivationEvidence:
    """Atomically activate one caller-owned exact-v2 SQLite connection offline."""

    if type(connection) is not sqlite3.Connection:
        raise TypeError("connection must be an exact sqlite3.Connection")
    if type(inputs) is not FamilySchemaActivationInputs:
        raise TypeError("inputs must be FamilySchemaActivationInputs")
    if fault_injector is not None and not callable(fault_injector):
        raise TypeError("fault_injector must be callable")

    owner_token = _claim_activation_owner(connection)
    try:
        evidence = _activate_family_schema_v3_owned(
            connection,
            inputs,
            owner_token=owner_token,
            external_providers=external_providers,
            fault_injector=fault_injector,
        )
    except BaseException as activation_exc:
        try:
            _release_activation_owner(connection, owner_token)
        except BaseException as release_exc:
            activation_exc.add_note(
                "activation owner cleanup also failed: "
                f"{type(release_exc).__name__}: {release_exc}"
            )
        raise
    else:
        _release_activation_owner(connection, owner_token)
        return evidence


def _activate_family_schema_v3_owned(
    connection: sqlite3.Connection,
    inputs: FamilySchemaActivationInputs,
    *,
    owner_token: object,
    external_providers: FamilyAuthorityExternalProviders | None,
    fault_injector: ActivationFaultInjector | None,
) -> FamilySchemaV3ActivationEvidence:
    """Execute activation only while the public entrypoint holds its owner CAS."""

    _require_activation_owner(connection, owner_token)
    # Version and ownership failures must not take over a caller transaction.
    _require_exact_v2_version(connection)
    if connection.in_transaction:
        raise FamilySchemaV3ActivationError(
            "family schema activation rejects a caller-owned transaction"
        )
    _enable_and_confirm_foreign_keys(connection)
    source_before_lock = verify_exact_v2_activation_source(connection)
    if connection.in_transaction:
        raise FamilySchemaV3ActivationError(
            "exact-v2 source verification unexpectedly opened a transaction"
        )
    manifest = _canonical_manifest()

    transaction_owned = False
    try:
        connection.execute("BEGIN IMMEDIATE")
        transaction_owned = True
        _require_exact_v2_version(connection)
        source_locked = verify_exact_v2_activation_source(connection)
        if source_locked != source_before_lock:
            raise FamilySchemaV3ActivationError(
                "exact-v2 source changed before the activation write lock"
            )
        _reject_v3_object_collisions(connection, manifest)

        for item in manifest.rendered_objects:
            connection.execute(item.statement_sql)
            _checkpoint(
                fault_injector,
                ActivationStep.APPLY_SEALED_DDL_STATEMENTS,
                item.ordinal,
            )

        genesis_payload = _genesis_payload(inputs, source_locked, manifest)
        genesis_record_hash = _insert_genesis(connection, genesis_payload)
        _checkpoint(fault_injector, ActivationStep.INSERT_SINGLE_GENESIS)

        connection.execute(
            f"PRAGMA user_version = {FAMILY_SCHEMA_V3_ACTIVATION_CONTRACT.target_version}"
        )
        _checkpoint(fault_injector, ActivationStep.SET_EXACT_V3_VERSION)

        # Delayed deliberately: the verifier is the sole owner of the nine
        # runtime SQLite semantic-function registrations.
        from .family_schema_v3_verifier import verify_family_authority

        verification_evidence = verify_family_authority(
            connection,
            expected_authority_instance_id=inputs.authority_instance_id,
            external_providers=external_providers,
        )
        _checkpoint(
            fault_injector,
            ActivationStep.VERIFY_IN_TRANSACTION_SCHEMA_AND_GENESIS,
        )
        _checkpoint(fault_injector, ActivationStep.FINAL_FAULT_CHECKPOINT)
        if not connection.in_transaction:
            raise FamilySchemaV3ActivationError(
                "activation lost its owned transaction before commit"
            )
    except BaseException:
        if transaction_owned:
            try:
                connection.rollback()
            except BaseException as rollback_exc:
                transaction_owned = False
                _close_after_transaction_control_failure(connection)
                raise FamilySchemaV3ActivationError(
                    "activation rollback failed; connection closed fail-closed"
                ) from rollback_exc
            transaction_owned = False
        raise

    try:
        connection.commit()
        transaction_owned = False
    except BaseException as commit_exc:
        try:
            connection.rollback()
        except BaseException:
            pass
        transaction_owned = False
        _close_after_transaction_control_failure(connection)
        raise FamilySchemaV3ActivationError(
            "activation commit failed; connection closed fail-closed"
        ) from commit_exc

    return FamilySchemaV3ActivationEvidence(
        authority_instance_id=inputs.authority_instance_id,
        source_evidence=source_locked,
        sealed_v3_manifest_hash=manifest.manifest_sha256,
        activation_code_manifest_evidence_hash=inputs.activation_code_manifest_hash,
        genesis_record_hash=genesis_record_hash,
        activated_at_us=inputs.activated_at_us,
        ddl_statement_count=len(manifest.rendered_objects),
        verification_evidence=verification_evidence,
    )


__all__ = [
    "ActivationFaultInjector",
    "ActivationFaultPoint",
    "FamilySchemaV3ActivationError",
    "FamilySchemaV3ActivationEvidence",
    "activate_family_schema_v3",
]
