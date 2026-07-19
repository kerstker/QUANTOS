"""Read-only executable Pass-M implementations for C6 and C9 named rules.

Every public implementation has the uniform ``(owner_row, view, providers)``
ABI.  Local authority references are recomputed from the immutable verification
view.  External C6 claims are obtained only through typed deployment providers;
C9 graph checks additionally load exact content-addressed bytes and consult the
out-of-band A9 trust roots.  Every exception is a rejection.
"""

from __future__ import annotations

import inspect
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import FunctionType

from quantos.contracts import canonical as _canonical
from quantos.contracts import data_snooping_c9 as _c9_contracts
from quantos.contracts.research import (
    CodeManifestEvidence,
    ObservedPitDatasetHandle,
)

from .family_schema_external_verification_c6_c9 import (
    C6C9ExternalProviderBundle,
    VerifiedExecutionCodeAttestation,
    VerifiedImmutableArtifactLease,
)
from .family_schema_verification_view import (
    FamilySchemaVerificationView,
    FrozenVerificationRow,
)
from .family_schema_verifier_registry import FAMILY_SCHEMA_VERIFIER_BINDINGS


_C6_COMPONENT_ID = "C6_EXTERNAL_AUTHORITY_REFS_V1"
_C9_COMPONENT_ID = "C9_ARTIFACT_PUBLICATION_RECEIPTS_V1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EXTERNAL_C8_CODE = "c8.verify_family_assessment_v1"

_OBSERVED_DECLARATION_FIELDS = (
    ("observed_pit_authority_id", "authority_id"),
    ("observed_pit_handle_id", "handle_id"),
    ("observed_pit_content_hash", "content_hash"),
    ("knowledge_time_policy_id", "knowledge_time_policy_id"),
    ("knowledge_time_policy_version", "knowledge_time_policy_version"),
    ("observed_pit_authority_head", "authority_head"),
)
_OBSERVED_EXPOSURE_FIELDS = (
    ("observed_pit_authority_id", "authority_id"),
    ("observed_pit_handle_id", "handle_id"),
    ("observed_pit_content_hash", "content_hash"),
    ("observed_pit_authority_head", "authority_head"),
    ("dataset_id", "dataset_id"),
    ("dataset_version", "dataset_version"),
    ("universe_id", "universe_id"),
    ("universe_hash", "universe_hash"),
    ("ordered_period_calendar_hash", "ordered_period_calendar_hash"),
    ("knowledge_time_policy_id", "knowledge_time_policy_id"),
    ("knowledge_time_policy_version", "knowledge_time_policy_version"),
    ("label_horizon", "label_horizon"),
    ("purge_periods", "purge_periods"),
    ("embargo_periods", "embargo_periods"),
    ("cost_contract_hash", "cost_contract_hash"),
    ("benchmark_contract_hash", "benchmark_contract_hash"),
)
_CODE_FIELDS = (
    ("code_manifest_policy_id", ("policy_ref", "policy_id")),
    ("code_manifest_policy_version", ("policy_ref", "policy_version")),
    ("code_manifest_policy_hash", ("policy_ref", "policy_hash")),
    ("code_manifest_evidence_hash", ("evidence_hash",)),
    ("code_manifest_authority_id", ("authority_id",)),
    ("code_manifest_authority_head", ("authority_head",)),
)
_EXPOSURE_FIELDS = (
    "oos_exposure_id",
    "exposure_fingerprint",
    "exposure_authority_id",
    "exposure_authority_head",
)
_START_FIELDS = (
    "start_execution_attestation_hash",
    "execution_attestation_authority_id",
    "start_execution_attestation_authority_head",
)
_TERMINAL_FIELDS = (
    "terminal_execution_attestation_hash",
    "terminal_execution_attestation_authority_head",
)
_LEASE_FIELDS = (
    "immutable_artifact_lease_id",
    "immutable_artifact_lease_hash",
    "immutable_artifact_lease_authority_id",
    "immutable_artifact_lease_authority_head",
)
_PUBLICATION_CLOSURE_FIELDS = (
    "authority_instance_id",
    "family_id",
    "closure_status",
    "selection_scope_id",
    "selection_decision_id",
    "winner_config_hash",
    "winner_evaluation_receipt_hash",
    *(local for local, _ in _OBSERVED_DECLARATION_FIELDS),
    *_EXPOSURE_FIELDS,
    *(local for local, _ in _CODE_FIELDS),
    *_START_FIELDS,
    *_TERMINAL_FIELDS,
    *_LEASE_FIELDS,
)


class _VerificationInputError(ValueError):
    """A required local row, provider claim, or exact byte graph is invalid."""


def _owner(owner_row: object) -> FrozenVerificationRow:
    if not isinstance(owner_row, Mapping):
        raise _VerificationInputError("owner row must be a mapping")
    return FrozenVerificationRow.from_mapping(owner_row)


def _view_rows(
    view: object,
    table_name: str,
) -> tuple[FrozenVerificationRow, ...]:
    if not isinstance(view, FamilySchemaVerificationView):
        raise _VerificationInputError("verification view must implement rows()")
    rows = view.rows(table_name)
    if type(rows) is not tuple:
        raise _VerificationInputError("verification view rows must be an exact tuple")
    return tuple(FrozenVerificationRow.from_mapping(row) for row in rows)


def _providers(providers: object) -> C6C9ExternalProviderBundle:
    if type(providers) is not C6C9ExternalProviderBundle:
        raise _VerificationInputError("exact C6/C9 provider bundle is required")
    return providers


def _text(
    row: Mapping[str, object],
    field: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = row[field]
    if type(value) is not str or (not allow_empty and not value) or unicodedata.normalize("NFC", value) != value:
        raise _VerificationInputError(f"{field} must be exact NFC text")
    return value


def _sha256(row: Mapping[str, object], field: str) -> str:
    value = _text(row, field)
    if _SHA256_RE.fullmatch(value) is None:
        raise _VerificationInputError(f"{field} must be lowercase SHA-256 text")
    return value


def _nullable_sha256(row: Mapping[str, object], field: str) -> str | None:
    if row[field] is None:
        return None
    return _sha256(row, field)


def _integer(
    row: Mapping[str, object],
    field: str,
    *,
    minimum: int | None = None,
) -> int:
    value = row[field]
    if type(value) is not int or (minimum is not None and value < minimum):
        raise _VerificationInputError(f"{field} must be an exact INTEGER")
    return value


def _exactly_one(
    rows: tuple[FrozenVerificationRow, ...],
    predicate: Callable[[FrozenVerificationRow], bool],
    context: str,
) -> FrozenVerificationRow:
    matches = tuple(row for row in rows if predicate(row))
    if len(matches) != 1:
        raise _VerificationInputError(f"{context} must resolve exactly once")
    return matches[0]


def _same_fields(
    left: Mapping[str, object],
    right: Mapping[str, object],
    fields: tuple[str, ...],
) -> bool:
    return all(left[field] == right[field] for field in fields)


def _nested_value(value: object, path: tuple[str, ...]) -> object:
    current = value
    for part in path:
        current = getattr(current, part)
    return current


def _validated_observed_claim(
    owner: FrozenVerificationRow,
    providers: C6C9ExternalProviderBundle,
    *,
    exposure_shape: bool,
) -> ObservedPitDatasetHandle:
    claim = providers.observed_pit.verify_observed_pit_dataset_handle(
        _text(owner, "observed_pit_authority_id"),
        _text(owner, "observed_pit_handle_id"),
        _sha256(owner, "observed_pit_content_hash"),
        _sha256(owner, "observed_pit_authority_head"),
    )
    if type(claim) is not ObservedPitDatasetHandle:
        raise _VerificationInputError("observed-PIT provider did not verify the claim")
    claim = ObservedPitDatasetHandle.model_validate(
        claim.model_dump(mode="python"),
        strict=True,
    )
    fields = _OBSERVED_EXPOSURE_FIELDS if exposure_shape else _OBSERVED_DECLARATION_FIELDS
    if any(owner[local] != getattr(claim, provider) for local, provider in fields):
        raise _VerificationInputError("observed-PIT provider claim does not match the row")
    return claim


def _validated_code_claim(
    owner: FrozenVerificationRow,
    providers: C6C9ExternalProviderBundle,
) -> CodeManifestEvidence:
    claim = providers.code_manifest.verify_code_manifest_evidence(
        _sha256(owner, "code_manifest_evidence_hash"),
        _text(owner, "code_manifest_authority_id"),
        _sha256(owner, "code_manifest_authority_head"),
    )
    if type(claim) is not CodeManifestEvidence:
        raise _VerificationInputError("code-manifest provider did not verify the claim")
    claim = CodeManifestEvidence.model_validate(
        claim.model_dump(mode="python"),
        strict=True,
    )
    if any(owner[local] != _nested_value(claim, path) for local, path in _CODE_FIELDS):
        raise _VerificationInputError("code-manifest provider claim does not match the row")
    return claim


def _all_none(owner: Mapping[str, object], fields: tuple[str, ...]) -> bool:
    return all(owner[field] is None for field in fields)


def _validated_execution_claim(
    owner: FrozenVerificationRow,
    providers: C6C9ExternalProviderBundle,
    *,
    role: str,
    nullable: bool,
) -> VerifiedExecutionCodeAttestation | None:
    if role == "START":
        hash_field = "start_execution_attestation_hash"
        head_field = (
            "execution_attestation_authority_head"
            if "execution_attestation_authority_head" in owner
            else "start_execution_attestation_authority_head"
        )
        group = (
            hash_field,
            "execution_attestation_authority_id",
            head_field,
        )
    elif role == "TERMINAL":
        hash_field = "terminal_execution_attestation_hash"
        head_field = "terminal_execution_attestation_authority_head"
        group = (hash_field, "execution_attestation_authority_id", head_field)
    else:
        raise _VerificationInputError("execution role is not closed")
    if owner[hash_field] is None:
        if nullable and _all_none(owner, group):
            return None
        raise _VerificationInputError("execution reference is partially absent")
    claim = providers.execution_attestation.verify_execution_code_attestation(
        _sha256(owner, hash_field),
        _text(owner, "execution_attestation_authority_id"),
        _sha256(owner, head_field),
    )
    if type(claim) is not VerifiedExecutionCodeAttestation:
        raise _VerificationInputError("execution provider did not verify the claim")
    if claim.subject_context.role != role:
        raise _VerificationInputError("execution provider returned the wrong subject role")
    evidence = claim.evidence
    if (
        evidence.attestation_hash != owner[hash_field]
        or evidence.authority_id != owner["execution_attestation_authority_id"]
        or evidence.authority_head != owner[head_field]
    ):
        raise _VerificationInputError("execution provider claim does not match the row")
    return claim


def _validated_lease_claim(
    owner: FrozenVerificationRow,
    providers: C6C9ExternalProviderBundle,
    *,
    nullable: bool,
) -> VerifiedImmutableArtifactLease | None:
    if owner["immutable_artifact_lease_id"] is None:
        if nullable and _all_none(owner, _LEASE_FIELDS):
            return None
        raise _VerificationInputError("lease reference is partially absent")
    claim = providers.immutable_artifact_lease.verify_immutable_artifact_lease(
        _text(owner, "immutable_artifact_lease_id"),
        _sha256(owner, "immutable_artifact_lease_hash"),
        _text(owner, "immutable_artifact_lease_authority_id"),
        _sha256(owner, "immutable_artifact_lease_authority_head"),
    )
    if type(claim) is not VerifiedImmutableArtifactLease:
        raise _VerificationInputError("lease provider did not verify the claim")
    evidence = claim.evidence
    if (
        evidence.lease_id != owner["immutable_artifact_lease_id"]
        or evidence.lease_hash != owner["immutable_artifact_lease_hash"]
        or evidence.authority_id != owner["immutable_artifact_lease_authority_id"]
        or evidence.authority_head != owner["immutable_artifact_lease_authority_head"]
    ):
        raise _VerificationInputError("lease provider claim does not match the row")
    return claim


def _exact_exposure(
    owner: FrozenVerificationRow,
    view: object,
) -> FrozenVerificationRow:
    exposure_id = _text(owner, "oos_exposure_id")
    return _exactly_one(
        _view_rows(view, "global_oos_exposures"),
        lambda row: _text(row, "oos_exposure_id") == exposure_id,
        "global OOS exposure",
    )


def _verify_global_exposure_identity(
    owner: FrozenVerificationRow,
    view: object,
) -> bool:
    exposure = _exact_exposure(owner, view)
    fields = (
        "oos_exposure_id",
        "authority_instance_id",
        "selection_scope_id",
        "selection_decision_id",
        "family_id",
        "exposure_ordinal",
        "exposure_fingerprint",
        "exposure_version",
        "record_hash",
    )
    if not _same_fields(owner, exposure, fields):
        return False
    _integer(owner, "exposure_ordinal", minimum=1)
    _integer(owner, "exposure_version", minimum=1)
    identities = tuple(
        (
            _text(row, "selection_scope_id"),
            _text(row, "selection_decision_id"),
            _text(row, "family_id"),
            _sha256(row, "exposure_fingerprint"),
        )
        for row in _view_rows(view, "global_oos_exposures")
    )
    return len(identities) == len(set(identities))


def _verify_exposure_fingerprint(owner: FrozenVerificationRow) -> bool:
    payload = {
        "observed_pit_content_hash": _sha256(owner, "observed_pit_content_hash"),
        "dataset_version": _text(owner, "dataset_version"),
        "universe_id": _text(owner, "universe_id"),
        "universe_hash": _sha256(owner, "universe_hash"),
        "ordered_period_calendar_hash": _sha256(
            owner,
            "ordered_period_calendar_hash",
        ),
        "label_horizon": _text(owner, "label_horizon"),
        "purge_periods": _integer(owner, "purge_periods", minimum=0),
        "embargo_periods": _integer(owner, "embargo_periods", minimum=0),
    }
    return _sha256(owner, "exposure_fingerprint") == _canonical.canonical_sha256(payload)


def _verify_oos_release_exposure(
    owner: FrozenVerificationRow,
    view: object,
) -> bool:
    exposure = _exact_exposure(owner, view)
    return (
        owner["oos_exposure_id"] == exposure["oos_exposure_id"]
        and owner["exposure_fingerprint"] == exposure["exposure_fingerprint"]
        and owner["exposure_authority_id"] == exposure["authority_instance_id"]
        and owner["exposure_authority_head"] == exposure["record_hash"]
        and owner["family_id"] == exposure["family_id"]
    )


def _verify_release_started_identity(
    owner: FrozenVerificationRow,
    view: object,
) -> bool:
    rows = _view_rows(view, "data_snooping_oos_access_attempts")
    start = _exactly_one(
        rows,
        lambda row: (
            _text(row, "authority_instance_id") == _text(owner, "authority_instance_id")
            and _text(row, "attempt_id") == _text(owner, "attempt_id")
            and _text(row, "attempt_event_id") == _text(owner, "started_attempt_event_id")
        ),
        "started OOS attempt",
    )
    return (
        _integer(start, "sequence_no", minimum=0) == 0
        and _text(start, "status") == "STARTED"
        and _text(start, "family_id") == _text(owner, "family_id")
        and _text(start, "oos_exposure_id") == _text(owner, "oos_exposure_id")
        and _sha256(start, "start_execution_attestation_hash") == _sha256(owner, "start_execution_attestation_hash")
        and _sha256(start, "immutable_artifact_lease_hash") == _sha256(owner, "immutable_artifact_lease_hash")
    )


def _execution_branch(owner: FrozenVerificationRow, *, publication: bool) -> str:
    exposure_present = owner["oos_exposure_id"] is not None
    start_present = owner["start_execution_attestation_hash"] is not None
    terminal_present = owner["terminal_execution_attestation_hash"] is not None
    lease_present = owner["immutable_artifact_lease_id"] is not None
    groups = (
        _EXPOSURE_FIELDS,
        _START_FIELDS,
        _TERMINAL_FIELDS,
        _LEASE_FIELDS,
    )
    if any(
        any(owner[field] is None for field in group) and any(owner[field] is not None for field in group)
        for group in groups
    ):
        raise _VerificationInputError("external reference group is partial")
    presence = (exposure_present, start_present, terminal_present, lease_present)
    if publication:
        closure_status = _text(owner, "closure_status")
        attempt_status = _text(owner, "attempt_status")
        if (closure_status, attempt_status, presence) == (
            "COMPLETE",
            "COMPLETED",
            (True, True, True, True),
        ):
            return "COMPLETE"
        if (closure_status, attempt_status, presence) == (
            "NOT_ASSESSABLE",
            "NOT_STARTED",
            (False, False, False, False),
        ):
            return "NOT_STARTED"
        if (closure_status, attempt_status, presence) == (
            "NOT_ASSESSABLE",
            "INCOMPLETE",
            (True, True, False, True),
        ):
            return "INCOMPLETE"
        raise _VerificationInputError("publication external branch is inconsistent")
    closure_status = _text(owner, "closure_status")
    if closure_status == "COMPLETE" and presence == (True, True, True, True):
        return "COMPLETE"
    if closure_status == "NOT_ASSESSABLE" and presence == (
        False,
        False,
        False,
        False,
    ):
        return "NOT_STARTED"
    if closure_status == "NOT_ASSESSABLE" and presence == (
        True,
        True,
        False,
        True,
    ):
        return "INCOMPLETE"
    raise _VerificationInputError("closure external branch is inconsistent")


def _closure_for_subject(
    owner: FrozenVerificationRow,
    view: object,
    *,
    publication: bool,
) -> FrozenVerificationRow:
    if not publication:
        closure_hash = _sha256(owner, "record_hash")
        closure = _exactly_one(
            _view_rows(view, "data_snooping_family_closures"),
            lambda row: _sha256(row, "record_hash") == closure_hash,
            "subject family closure",
        )
        if not _same_fields(owner, closure, tuple(owner)):
            raise _VerificationInputError("subject closure is not the frozen view row")
        return closure
    closure_hash = _sha256(owner, "closure_manifest_hash")
    closure = _exactly_one(
        _view_rows(view, "data_snooping_family_closures"),
        lambda row: (
            _sha256(row, "record_hash") == closure_hash
            and _text(row, "authority_instance_id") == _text(owner, "authority_instance_id")
            and _text(row, "family_id") == _text(owner, "family_id")
        ),
        "publication family closure",
    )
    if not _same_fields(owner, closure, _PUBLICATION_CLOSURE_FIELDS):
        raise _VerificationInputError("publication closure summary is detached")
    return closure


def _attempt_for_closure(
    closure: FrozenVerificationRow,
    view: object,
) -> FrozenVerificationRow:
    return _exactly_one(
        _view_rows(view, "data_snooping_oos_access_attempts"),
        lambda row: (
            _text(row, "authority_instance_id") == _text(closure, "authority_instance_id")
            and _text(row, "family_id") == _text(closure, "family_id")
            and _text(row, "attempt_id") == _text(closure, "attempt_id")
            and _text(row, "attempt_event_id") == _text(closure, "terminal_attempt_event_id")
            and _sha256(row, "record_hash") == _sha256(closure, "oos_access_attempt_terminal_hash")
        ),
        "closure terminal attempt",
    )


def _verify_observed_exposure_continuity(
    owner: FrozenVerificationRow,
    view: object,
    providers: C6C9ExternalProviderBundle,
) -> bool:
    if owner["oos_exposure_id"] is None:
        return _all_none(owner, _EXPOSURE_FIELDS)
    observed = _validated_observed_claim(owner, providers, exposure_shape=False)
    exposure = _exact_exposure(owner, view)
    pairs = (
        (observed.authority_id, exposure["observed_pit_authority_id"]),
        (observed.handle_id, exposure["observed_pit_handle_id"]),
        (observed.content_hash, exposure["observed_pit_content_hash"]),
        (observed.knowledge_time_policy_id, exposure["knowledge_time_policy_id"]),
        (
            observed.knowledge_time_policy_version,
            exposure["knowledge_time_policy_version"],
        ),
        (observed.authority_head, exposure["observed_pit_authority_head"]),
    )
    return all(left == right for left, right in pairs) and _verify_oos_release_exposure(
        owner,
        view,
    )


def _verify_code_start_continuity(
    owner: FrozenVerificationRow,
    providers: C6C9ExternalProviderBundle,
) -> bool:
    if owner["start_execution_attestation_hash"] is None:
        return _all_none(owner, _START_FIELDS)
    code = _validated_code_claim(owner, providers)
    start = _validated_execution_claim(
        owner,
        providers,
        role="START",
        nullable=True,
    )
    if start is None:
        return False
    evidence = start.evidence
    return (
        code.manifest_hash == evidence.code_manifest_hash
        and code.source_commit == evidence.source_commit
        and code.runtime_identity == evidence.runtime_identity
        and code.dependency_lock_hash == evidence.dependency_lock_hash
    )


def _strict_prefix(left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    return len(right) > len(left) and right[: len(left)] == left


def _verify_start_terminal_continuity(
    owner: FrozenVerificationRow,
    view: object,
    providers: C6C9ExternalProviderBundle,
    *,
    publication: bool,
) -> bool:
    branch = _execution_branch(owner, publication=publication)
    if branch != "COMPLETE":
        return branch in ("NOT_STARTED", "INCOMPLETE")
    start = _validated_execution_claim(
        owner,
        providers,
        role="START",
        nullable=True,
    )
    terminal = _validated_execution_claim(
        owner,
        providers,
        role="TERMINAL",
        nullable=True,
    )
    if start is None or terminal is None:
        return False
    left = start.evidence
    right = terminal.evidence
    stable_fields = (
        "code_manifest_hash",
        "source_commit",
        "runtime_identity",
        "dependency_lock_hash",
        "immutable_artifact_id",
        "immutable_artifact_digest",
        "entrypoint",
        "process_image_hash",
        "lease_id",
    )
    if (
        left.attestation_hash == right.attestation_hash
        or left.authority_id != right.authority_id
        or start.authority_genesis_record_hash != terminal.authority_genesis_record_hash
        or any(getattr(left, field) != getattr(right, field) for field in stable_fields)
        or not _strict_prefix(
            start.authority_projection_record_hashes,
            terminal.authority_projection_record_hashes,
        )
        or left.issued_at_us >= right.issued_at_us
    ):
        return False
    closure = _closure_for_subject(owner, view, publication=publication)
    context = (
        _text(closure, "family_id"),
        _text(closure, "attempt_id"),
        _text(closure, "evaluation_batch_id"),
    )
    return (
        start.subject_context.family_id,
        start.subject_context.attempt_id,
        start.subject_context.evaluation_batch_id,
    ) == context and (
        terminal.subject_context.family_id,
        terminal.subject_context.attempt_id,
        terminal.subject_context.evaluation_batch_id,
    ) == context


def _verify_start_lease_field_continuity(
    start: VerifiedExecutionCodeAttestation,
    lease: VerifiedImmutableArtifactLease,
) -> bool:
    attestation = start.evidence
    evidence = lease.evidence
    return (
        attestation.lease_id == evidence.lease_id
        and attestation.code_manifest_hash == evidence.code_manifest_hash
        and attestation.immutable_artifact_id == evidence.artifact_id
        and attestation.immutable_artifact_digest == evidence.artifact_digest
        and attestation.process_image_hash == evidence.process_image_hash
    )


def _verify_start_lease_continuity(
    owner: FrozenVerificationRow,
    view: object,
    providers: C6C9ExternalProviderBundle,
    *,
    publication: bool | None,
) -> bool:
    if publication is None:
        start = _validated_execution_claim(
            owner,
            providers,
            role="START",
            nullable=False,
        )
        lease = _validated_lease_claim(owner, providers, nullable=False)
        return start is not None and lease is not None and _verify_start_lease_field_continuity(start, lease)
    branch = _execution_branch(owner, publication=publication)
    if branch == "NOT_STARTED":
        return True
    start = _validated_execution_claim(
        owner,
        providers,
        role="START",
        nullable=True,
    )
    lease = _validated_lease_claim(owner, providers, nullable=True)
    if start is None or lease is None or not _verify_start_lease_field_continuity(start, lease):
        return False
    closure = _closure_for_subject(owner, view, publication=publication)
    attempt = _attempt_for_closure(closure, view)
    if _text(attempt, "status") != ("COMPLETED" if branch == "COMPLETE" else "INCOMPLETE"):
        return False
    expected_batch = (
        _text(closure, "evaluation_batch_id") if branch == "COMPLETE" else _text(attempt, "evaluation_batch_id")
    )
    subject = (
        _text(closure, "family_id"),
        _text(closure, "attempt_id"),
        expected_batch,
    )
    if (
        start.subject_context.family_id,
        start.subject_context.attempt_id,
        start.subject_context.evaluation_batch_id,
    ) != subject or (
        lease.subject_context.family_id,
        lease.subject_context.attempt_id,
        lease.subject_context.evaluation_batch_id,
    ) != subject:
        return False
    start_at = start.evidence.issued_at_us
    acquired_at = lease.evidence.acquired_at_us
    expires_at = lease.evidence.expires_at_us
    closed_at = _integer(closure, "closed_at_us", minimum=1)
    if branch == "INCOMPLETE":
        return acquired_at <= start_at <= closed_at <= expires_at
    terminal = _validated_execution_claim(
        owner,
        providers,
        role="TERMINAL",
        nullable=True,
    )
    if terminal is None:
        return False
    batch = _exactly_one(
        _view_rows(view, "data_snooping_evaluation_batches"),
        lambda row: (
            _text(row, "authority_instance_id") == _text(closure, "authority_instance_id")
            and _text(row, "evaluation_batch_id") == expected_batch
            and _text(row, "family_id") == subject[0]
            and _text(row, "attempt_id") == subject[1]
            and _sha256(row, "record_hash") == _sha256(closure, "evaluation_batch_receipt_hash")
        ),
        "closure evaluation batch",
    )
    if (
        _sha256(batch, "start_execution_attestation_hash") != start.evidence.attestation_hash
        or _sha256(batch, "terminal_execution_attestation_hash") != terminal.evidence.attestation_hash
        or _sha256(batch, "immutable_artifact_lease_hash") != lease.evidence.lease_hash
    ):
        return False
    committed_at = _integer(batch, "committed_at_us", minimum=1)
    return acquired_at <= start_at < terminal.evidence.issued_at_us <= committed_at <= closed_at <= expires_at


def _receipt(owner: FrozenVerificationRow) -> object:
    receipt_type = _c9_contracts.ArtifactPublicationReceipt
    return receipt_type.model_validate(dict(owner), strict=True)


def _all_receipts(view: object) -> tuple[object, ...]:
    return tuple(_receipt(row) for row in _view_rows(view, "artifact_publication_receipts"))


def _receipt_in_view(receipt: object, view: object) -> bool:
    return (
        sum(
            candidate.receipt_record_hash == receipt.receipt_record_hash and candidate == receipt
            for candidate in _all_receipts(view)
        )
        == 1
    )


def _verify_publication_genesis(owner: FrozenVerificationRow, view: object) -> bool:
    receipt = _receipt(owner)
    genesis_rows = _view_rows(view, "family_authority_genesis")
    genesis = _exactly_one(
        genesis_rows,
        lambda row: (
            _text(row, "authority_instance_id") == receipt.authority_instance_id
            and _sha256(row, "record_hash") == receipt.genesis_record_hash
        ),
        "publication authority genesis",
    )
    genesis_type = getattr(_c9_contracts, "_FamilyAuthorityGenesis")
    validated = genesis_type.model_validate(dict(genesis), strict=True)
    return (
        validated.authority_instance_id == receipt.authority_instance_id
        and validated.record_hash == receipt.genesis_record_hash
        and _receipt_in_view(receipt, view)
    )


def _verify_publication_chain(owner: FrozenVerificationRow, view: object) -> bool:
    receipt = _receipt(owner)
    receipts = tuple(
        sorted(
            (item for item in _all_receipts(view) if item.authority_instance_id == receipt.authority_instance_id),
            key=lambda item: item.publication_ordinal,
        )
    )
    if not receipts or receipt not in receipts:
        return False
    previous_hash = receipt.genesis_record_hash
    prior_time = 0
    families: set[str] = set()
    receipt_ids: set[str] = set()
    envelopes: set[str] = set()
    paths: set[str] = set()
    for expected_ordinal, item in enumerate(receipts, start=1):
        if (
            item.genesis_record_hash != receipt.genesis_record_hash
            or item.publication_ordinal != expected_ordinal
            or item.previous_record_hash != previous_hash
            or item.published_at_us <= prior_time
            or item.family_id in families
            or item.publication_receipt_id in receipt_ids
            or item.published_artifact_envelope_hash in envelopes
            or item.target_content_addressed_path in paths
        ):
            return False
        families.add(item.family_id)
        receipt_ids.add(item.publication_receipt_id)
        envelopes.add(item.published_artifact_envelope_hash)
        paths.add(item.target_content_addressed_path)
        previous_hash = item.receipt_record_hash
        prior_time = item.published_at_us
    return True


def _publication_closure_row(
    receipt: object,
    view: object,
) -> FrozenVerificationRow:
    return _exactly_one(
        _view_rows(view, "data_snooping_family_closures"),
        lambda row: (
            _text(row, "authority_instance_id") == receipt.authority_instance_id
            and _text(row, "family_id") == receipt.family_id
            and _sha256(row, "record_hash") == receipt.closure_manifest_hash
        ),
        "publication closure",
    )


def _verify_publication_closure(owner: FrozenVerificationRow, view: object) -> bool:
    receipt = _receipt(owner)
    closure = _publication_closure_row(receipt, view)
    fields = (
        "authority_instance_id",
        "family_id",
        "closure_status",
        "selection_scope_id",
        "selection_decision_id",
        "winner_config_hash",
        "winner_evaluation_receipt_hash",
        *(local for local, _ in _OBSERVED_DECLARATION_FIELDS),
        *_EXPOSURE_FIELDS,
        *(local for local, _ in _CODE_FIELDS),
        *_START_FIELDS,
        *_TERMINAL_FIELDS,
        *_LEASE_FIELDS,
    )
    return _same_fields(owner, closure, fields) and (
        _integer(closure, "closed_at_us", minimum=1) <= receipt.published_at_us
    )


def _publication_assessment_row(
    receipt: object,
    view: object,
) -> FrozenVerificationRow:
    return _exactly_one(
        _view_rows(view, "data_snooping_assessments"),
        lambda row: (
            _text(row, "authority_instance_id") == receipt.authority_instance_id
            and _text(row, "family_id") == receipt.family_id
            and _sha256(row, "record_hash") == receipt.family_assessment_conclusion_hash
        ),
        "publication family conclusion",
    )


def _verify_publication_conclusion(owner: FrozenVerificationRow, view: object) -> bool:
    receipt = _receipt(owner)
    assessment = _publication_assessment_row(receipt, view)
    pairs = (
        (assessment["closure_manifest_hash"], receipt.closure_manifest_hash),
        (
            assessment["ordered_latest_grid_assessment_hashes_json"],
            receipt.ordered_grid_assessment_record_hashes_json,
        ),
        (
            assessment["ordered_latest_grid_assessment_hashes_hash"],
            receipt.ordered_grid_assessment_record_hashes_hash,
        ),
        (assessment["minimum_replication_count"], receipt.minimum_replication_count),
        (assessment["assessment_status"], receipt.assessment_status),
        (assessment["diagnostic_verdict"], receipt.diagnostic_verdict),
        (assessment["promotion_effect"], receipt.promotion_effect),
    )
    return all(left == right for left, right in pairs) and (
        _integer(assessment, "concluded_at_us", minimum=1) <= receipt.published_at_us
    )


def _verify_exporter(owner: FrozenVerificationRow, view: object) -> bool:
    receipt = _receipt(owner)
    event = _exactly_one(
        _view_rows(view, "research_authority_events"),
        lambda row: _sha256(row, "record_hash") == receipt.exporter_event_hash,
        "publication exporter event",
    )
    event_at = _integer(event, "event_at_us", minimum=1)
    return (
        _text(event, "authority_instance_id") == receipt.authority_instance_id
        and _text(event, "action") == "PUBLISH_DATA_SNOOPING_FAMILY_ARTIFACT_V1"
        and _text(event, "subject_record_type") == "PUBLISHED_ARTIFACT_ENVELOPE_V1"
        and _text(event, "subject_id") == receipt.publication_receipt_id
        and _sha256(event, "subject_record_hash") == receipt.published_artifact_envelope_hash
        and _sha256(event, "record_hash") == receipt.exporter_authority_head
        and receipt.envelope_scanned_at_us <= event_at <= receipt.published_at_us
    )


@dataclass(frozen=True, slots=True)
class _LoadedPublicationBundle:
    envelope: object
    projection: object
    projection_ref: object
    evidence_ref: object | None


def _canonical_contract_bytes(
    payload: object,
    contract_type: type[object],
) -> object:
    if type(payload) is not bytes:
        raise _VerificationInputError("artifact provider must return exact bytes")
    loaded = _canonical.load_canonical_json_bytes(payload)
    if type(loaded) is not dict:
        raise _VerificationInputError("content-addressed contract root must be an object")

    # Canonical JSON arrays decode as lists, while every closed sequence in the
    # frozen contracts is an exact tuple.  Normalize only that representation,
    # retain strict contract validation, then demand byte-for-byte round-trip.
    def tuple_arrays(value: object) -> object:
        if type(value) is list:
            return tuple(tuple_arrays(item) for item in value)
        if type(value) is dict:
            return {key: tuple_arrays(item) for key, item in value.items()}
        return value

    model = contract_type.model_validate(tuple_arrays(loaded), strict=True)
    if _canonical.canonical_json_bytes(model) != payload:
        raise _VerificationInputError("contract model changed canonical artifact bytes")
    return model


def _load_envelope(
    receipt: object,
    providers: C6C9ExternalProviderBundle,
) -> object:
    artifact = providers.artifact_graph
    if artifact is None:
        raise _VerificationInputError("C9 artifact provider is missing")
    payload = artifact.read_content_addressed_bytes(
        receipt.target_content_addressed_path,
        receipt.published_artifact_envelope_hash,
    )
    envelope = _canonical_contract_bytes(
        payload,
        _c9_contracts.PublishedArtifactEnvelope,
    )
    envelope_bytes = _canonical.canonical_json_bytes(envelope)
    if _canonical.sha256_bytes(envelope_bytes) != receipt.published_artifact_envelope_hash:
        raise _VerificationInputError("published envelope hash is detached")
    return envelope


def _load_bundle(
    receipt: object,
    providers: C6C9ExternalProviderBundle,
) -> _LoadedPublicationBundle:
    artifact = providers.artifact_graph
    if artifact is None:
        raise _VerificationInputError("C9 artifact provider is missing")
    envelope = _load_envelope(receipt, providers)
    projection_ref_bytes = artifact.read_publication_projection_ref_bytes(
        receipt.publication_receipt_id,
        receipt.receipt_record_hash,
    )
    projection_ref = _canonical_contract_bytes(
        projection_ref_bytes,
        _c9_contracts.ArtifactPublicationProjectionRef,
    )
    projection_bytes = artifact.read_content_addressed_bytes(
        projection_ref.publication_projection_path,
        projection_ref.publication_projection_hash,
    )
    projection = _canonical_contract_bytes(
        projection_bytes,
        _c9_contracts.ArtifactPublicationReceiptProjection,
    )
    if _canonical.sha256_bytes(projection_bytes) != projection_ref.publication_projection_hash:
        raise _VerificationInputError("publication projection hash is detached")
    evidence_ref_bytes = artifact.read_data_snooping_evidence_ref_bytes(
        receipt.publication_receipt_id,
        receipt.receipt_record_hash,
    )
    evidence_ref = None
    if evidence_ref_bytes is not None:
        evidence_ref = _canonical_contract_bytes(
            evidence_ref_bytes,
            _c9_contracts.DataSnoopingEvidenceRef,
        )
    if receipt.assessment_status == "ASSESSED" and evidence_ref is None:
        raise _VerificationInputError("assessed publication requires its evidence reference")
    splice = getattr(_c9_contracts, "_verify_exact_byte_graph_splice")
    if splice(envelope, receipt, projection, projection_ref, evidence_ref) is not True:
        raise _VerificationInputError("artifact byte graph splice rejected")
    return _LoadedPublicationBundle(
        envelope=envelope,
        projection=projection,
        projection_ref=projection_ref,
        evidence_ref=evidence_ref,
    )


def _scanner_trusted(
    receipt: object,
    providers: C6C9ExternalProviderBundle,
) -> bool:
    trust = providers.publication_trust
    return (
        trust is not None
        and trust.verify_trusted_scanner(
            receipt.public_artifact_scan_policy_id,
            receipt.dlp_policy_id,
            receipt.dlp_policy_version,
            receipt.dlp_policy_hash,
            receipt.scanner_implementation,
            receipt.scanner_version,
        )
        is True
    )


def _scan_receipts(bundle: _LoadedPublicationBundle) -> tuple[object, ...]:
    core = bundle.envelope.artifact_core
    typed_evidence = (
        core.selection_scope_evidence,
        core.selection_decision_evidence,
        core.trial_ledger_proof_evidence,
        core.observed_pit_evidence,
        core.activation_code_manifest_evidence,
        core.code_manifest_evidence,
        *core.ordered_candidate_disposition_evidence,
        *core.ordered_candidate_receipt_evidence,
        *((core.evaluation_batch_evidence,) if core.evaluation_batch_evidence else ()),
        *((core.benchmark_receipt_evidence,) if core.benchmark_receipt_evidence else ()),
        *((core.start_execution_attestation_evidence,) if core.start_execution_attestation_evidence else ()),
        *((core.terminal_execution_attestation_evidence,) if core.terminal_execution_attestation_evidence else ()),
        *((core.immutable_artifact_lease_evidence,) if core.immutable_artifact_lease_evidence else ()),
    )
    return (
        bundle.envelope.artifact_core_scan_receipt,
        core.family_schema_v3_manifest_sidecar.scan_receipt,
        core.trial_ledger_history_sidecar.scan_receipt,
        core.trial_ledger_snapshot_sidecar.scan_receipt,
        core.trial_ledger_seal_sidecar.scan_receipt,
        *(evidence.scan_receipt for evidence in typed_evidence),
        *(
            evidence.provider_authority_projection.scan_receipt
            for evidence in typed_evidence
            if evidence.provider_authority_projection is not None
        ),
    )


def _verify_bundle_trust(
    receipt: object,
    bundle: _LoadedPublicationBundle,
    providers: C6C9ExternalProviderBundle,
) -> bool:
    trust = providers.publication_trust
    if trust is None or not _scanner_trusted(receipt, providers):
        return False
    core = bundle.envelope.artifact_core
    projection = bundle.projection
    expected_manifest = trust.expected_family_schema_v3_manifest_hash()
    expected_activation = trust.expected_activation_code_manifest_evidence_hash()
    if (
        type(expected_manifest) is not str
        or _SHA256_RE.fullmatch(expected_manifest) is None
        or type(expected_activation) is not str
        or _SHA256_RE.fullmatch(expected_activation) is None
        or projection.authority_genesis.sealed_v3_manifest_hash != expected_manifest
        or projection.authority_genesis.activation_code_manifest_evidence_hash != expected_activation
    ):
        return False
    for scan in _scan_receipts(bundle):
        if (
            trust.verify_trusted_scanner(
                scan.public_artifact_scan_policy_id,
                scan.dlp_policy_id,
                scan.dlp_policy_version,
                scan.dlp_policy_hash,
                scan.scanner_implementation,
                scan.scanner_version,
            )
            is not True
        ):
            return False
    for evidence in (
        core.observed_pit_evidence,
        core.activation_code_manifest_evidence,
        core.code_manifest_evidence,
        core.start_execution_attestation_evidence,
        core.terminal_execution_attestation_evidence,
        core.immutable_artifact_lease_evidence,
    ):
        if evidence is None:
            continue
        authority = evidence.provider_authority_projection
        if (
            authority is None
            or trust.verify_external_provider_authority_genesis(
                evidence.contract_type,
                authority.authority_id,
                authority.genesis_record_hash,
            )
            is not True
        ):
            return False
    return True


def _verify_final_scan(
    owner: FrozenVerificationRow,
    providers: C6C9ExternalProviderBundle,
) -> bool:
    receipt = _receipt(owner)
    envelope = _load_envelope(receipt, providers)
    envelope_bytes = _canonical.canonical_json_bytes(envelope)
    expected_scan = _canonical.canonical_sha256(
        {
            "scan_target": "PUBLISHED_ARTIFACT_ENVELOPE",
            "target_sha256": receipt.published_artifact_envelope_hash,
            "target_byte_length": len(envelope_bytes),
            "public_artifact_scan_policy_id": receipt.public_artifact_scan_policy_id,
            "dlp_policy_id": receipt.dlp_policy_id,
            "dlp_policy_version": receipt.dlp_policy_version,
            "dlp_policy_hash": receipt.dlp_policy_hash,
            "scanner_implementation": receipt.scanner_implementation,
            "scanner_version": receipt.scanner_version,
            "scan_status": receipt.scan_status,
        }
    )
    return (
        expected_scan == receipt.scan_result_hash
        and envelope.artifact_core_scan_receipt.scanned_at_us
        <= receipt.envelope_scanned_at_us
        <= receipt.published_at_us
        and _scanner_trusted(receipt, providers)
    )


def _verify_envelope_graph(
    owner: FrozenVerificationRow,
    providers: C6C9ExternalProviderBundle,
) -> bool:
    receipt = _receipt(owner)
    envelope = _load_envelope(receipt, providers)
    core_bytes = _canonical.canonical_json_bytes(envelope.artifact_core)
    scan_bytes = _canonical.canonical_json_bytes(envelope.artifact_core_scan_receipt)
    if (
        _canonical.sha256_bytes(core_bytes) != receipt.artifact_core_hash
        or _canonical.sha256_bytes(scan_bytes) != receipt.artifact_core_scan_receipt_hash
        or not _verify_final_scan(owner, providers)
    ):
        return False
    # The full bundle verifier below closes external genesis and every embedded
    # scan receipt.  The envelope graph itself still requires exact root bytes.
    return True


def _verify_self_contained_bundle(
    owner: FrozenVerificationRow,
    providers: C6C9ExternalProviderBundle,
) -> bool:
    receipt = _receipt(owner)
    bundle = _load_bundle(receipt, providers)
    return _verify_final_scan(owner, providers) and _verify_bundle_trust(
        receipt,
        bundle,
        providers,
    )


_C9_PROJECTED_FAMILY_GRAPH_TABLES = (
    "data_snooping_family_searches",
    "data_snooping_proposal_registrations",
    "data_snooping_proposals",
    "data_snooping_hypotheses",
    "data_snooping_search_emission_seals",
    "data_snooping_candidate_dispositions",
)


def _verify_projected_family_graph_matches_view(
    receipt: object,
    bundle: _LoadedPublicationBundle,
    view: object,
) -> bool:
    """Reject any omitted, invented, or rewritten C5/C7 family graph row."""

    core = bundle.envelope.artifact_core
    for table_name in _C9_PROJECTED_FAMILY_GRAPH_TABLES:
        projected_rows = tuple(
            {
                **row.canonical_row_payload,
                "record_hash": row.record_hash,
            }
            for row in core.authority_projection.canonical_rows
            if row.table_name == table_name
            and row.canonical_row_payload.get("family_id") == receipt.family_id
        )
        live_rows = tuple(
            dict(row)
            for row in _view_rows(view, table_name)
            if _text(row, "family_id") == receipt.family_id
            and _text(row, "authority_instance_id") == receipt.authority_instance_id
        )
        projected_by_hash = {
            row["record_hash"]: row for row in projected_rows
        }
        live_by_hash = {row.get("record_hash"): row for row in live_rows}
        if (
            len(projected_by_hash) != len(projected_rows)
            or len(live_by_hash) != len(live_rows)
            or set(projected_by_hash) != set(live_by_hash)
            or any(
                projected_by_hash[record_hash] != live_by_hash[record_hash]
                for record_hash in projected_by_hash
            )
        ):
            return False
    return True


def _verify_projection_totality(
    owner: FrozenVerificationRow,
    view: object,
    providers: C6C9ExternalProviderBundle,
) -> bool:
    receipt = _receipt(owner)
    bundle = _load_bundle(receipt, providers)
    if not _verify_bundle_trust(receipt, bundle, providers):
        return False
    receipts = tuple(
        sorted(
            (item for item in _all_receipts(view) if item.authority_instance_id == receipt.authority_instance_id),
            key=lambda item: item.publication_ordinal,
        )
    )
    prefix = tuple(bundle.projection.publication_chain_prefix)
    expected_prefix = tuple(item for item in receipts if item.publication_ordinal <= receipt.publication_ordinal)
    if prefix != expected_prefix or prefix[-1] != receipt:
        return False
    if not _verify_projected_family_graph_matches_view(receipt, bundle, view):
        return False
    closures = tuple(
        row
        for row in _view_rows(view, "data_snooping_family_closures")
        if _text(row, "authority_instance_id") == receipt.authority_instance_id
    )
    assessments = tuple(
        row
        for row in _view_rows(view, "data_snooping_assessments")
        if _text(row, "authority_instance_id") == receipt.authority_instance_id
    )
    receipt_keys = {(item.family_id, item.closure_manifest_hash) for item in receipts}
    closure_keys = {(_text(row, "family_id"), _sha256(row, "record_hash")) for row in closures}
    assessment_keys = {
        (
            _text(row, "family_id"),
            _sha256(row, "closure_manifest_hash"),
        )
        for row in assessments
    }
    return (
        len(receipt_keys) == len(receipts)
        and len(closure_keys) == len(closures)
        and len(assessment_keys) == len(assessments)
        and receipt_keys == closure_keys == assessment_keys
        and _verify_publication_chain(owner, view)
    )


def _invoke(
    implementation: Callable[
        [FrozenVerificationRow, object, C6C9ExternalProviderBundle | None],
        bool,
    ],
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    try:
        return (
            implementation(
                _owner(owner_row),
                view,
                providers if type(providers) is C6C9ExternalProviderBundle else None,
            )
            is True
        )
    except Exception:
        return False


def verify_observed_pit_declaration_binding(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None
        and bool(_validated_observed_claim(row, bundle, exposure_shape=False)),
        owner_row,
        view,
        providers,
    )


def verify_code_manifest_declaration_binding(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None and bool(_validated_code_claim(row, bundle)),
        owner_row,
        view,
        providers,
    )


def verify_global_oos_exposure_identity(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, current, _bundle: _verify_global_exposure_identity(row, current), owner_row, view, providers
    )


def verify_observed_pit_exposure_binding(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None
        and bool(_validated_observed_claim(row, bundle, exposure_shape=True)),
        owner_row,
        view,
        providers,
    )


def verify_exposure_fingerprint(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(lambda row, _view, _bundle: _verify_exposure_fingerprint(row), owner_row, view, providers)


def verify_oos_release_exposure_binding(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(lambda row, current, _bundle: _verify_oos_release_exposure(row, current), owner_row, view, providers)


def verify_start_execution_attestation(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None
        and _validated_execution_claim(row, bundle, role="START", nullable=False) is not None,
        owner_row,
        view,
        providers,
    )


def verify_immutable_artifact_lease(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None
        and _validated_lease_claim(row, bundle, nullable=False) is not None,
        owner_row,
        view,
        providers,
    )


def verify_oos_release_started_execution_identity(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, current, _bundle: _verify_release_started_identity(row, current), owner_row, view, providers
    )


def verify_closure_external_reference_branch(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, _bundle: bool(_execution_branch(row, publication=False)), owner_row, view, providers
    )


def verify_closure_observed_pit_binding(owner_row: object, view: object, providers: object) -> bool:
    return verify_observed_pit_declaration_binding(owner_row, view, providers)


def verify_closure_oos_exposure_binding(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, current, _bundle: _all_none(row, _EXPOSURE_FIELDS)
        if row["oos_exposure_id"] is None
        else _verify_oos_release_exposure(row, current),
        owner_row,
        view,
        providers,
    )


def verify_closure_code_manifest_binding(owner_row: object, view: object, providers: object) -> bool:
    return verify_code_manifest_declaration_binding(owner_row, view, providers)


def verify_closure_start_execution_attestation(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None
        and (
            _validated_execution_claim(row, bundle, role="START", nullable=True) is not None
            or row["start_execution_attestation_hash"] is None
        ),
        owner_row,
        view,
        providers,
    )


def verify_closure_terminal_execution_attestation(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None
        and (
            _validated_execution_claim(row, bundle, role="TERMINAL", nullable=True) is not None
            or row["terminal_execution_attestation_hash"] is None
        ),
        owner_row,
        view,
        providers,
    )


def verify_closure_immutable_artifact_lease(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None
        and (
            _validated_lease_claim(row, bundle, nullable=True) is not None or row["immutable_artifact_lease_id"] is None
        ),
        owner_row,
        view,
        providers,
    )


def verify_publication_closure_binding(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(lambda row, current, _bundle: _verify_publication_closure(row, current), owner_row, view, providers)


def verify_publication_external_reference_totality(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, _bundle: bool(_execution_branch(row, publication=True)), owner_row, view, providers
    )


def verify_publication_observed_pit_binding(owner_row: object, view: object, providers: object) -> bool:
    return verify_observed_pit_declaration_binding(owner_row, view, providers)


def verify_publication_oos_exposure_binding(owner_row: object, view: object, providers: object) -> bool:
    return verify_closure_oos_exposure_binding(owner_row, view, providers)


def verify_publication_code_manifest_binding(owner_row: object, view: object, providers: object) -> bool:
    return verify_code_manifest_declaration_binding(owner_row, view, providers)


def verify_publication_start_execution_attestation(owner_row: object, view: object, providers: object) -> bool:
    return verify_closure_start_execution_attestation(owner_row, view, providers)


def verify_publication_terminal_execution_attestation(owner_row: object, view: object, providers: object) -> bool:
    return verify_closure_terminal_execution_attestation(owner_row, view, providers)


def verify_publication_immutable_artifact_lease(owner_row: object, view: object, providers: object) -> bool:
    return verify_closure_immutable_artifact_lease(owner_row, view, providers)


def verify_oos_release_start_lease_continuity(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, current, bundle: bundle is not None
        and _verify_start_lease_continuity(row, current, bundle, publication=None),
        owner_row,
        view,
        providers,
    )


def verify_closure_observed_pit_exposure_continuity(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, current, bundle: bundle is not None and _verify_observed_exposure_continuity(row, current, bundle),
        owner_row,
        view,
        providers,
    )


def verify_closure_code_start_continuity(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None and _verify_code_start_continuity(row, bundle),
        owner_row,
        view,
        providers,
    )


def verify_closure_start_terminal_continuity(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, current, bundle: bundle is not None
        and _verify_start_terminal_continuity(row, current, bundle, publication=False),
        owner_row,
        view,
        providers,
    )


def verify_closure_start_lease_continuity(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, current, bundle: bundle is not None
        and _verify_start_lease_continuity(row, current, bundle, publication=False),
        owner_row,
        view,
        providers,
    )


def verify_publication_observed_pit_exposure_continuity(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, current, bundle: bundle is not None and _verify_observed_exposure_continuity(row, current, bundle),
        owner_row,
        view,
        providers,
    )


def verify_publication_code_start_continuity(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None and _verify_code_start_continuity(row, bundle),
        owner_row,
        view,
        providers,
    )


def verify_publication_start_terminal_continuity(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, current, bundle: bundle is not None
        and _verify_start_terminal_continuity(row, current, bundle, publication=True),
        owner_row,
        view,
        providers,
    )


def verify_publication_start_lease_continuity(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, current, bundle: bundle is not None
        and _verify_start_lease_continuity(row, current, bundle, publication=True),
        owner_row,
        view,
        providers,
    )


def verify_publication_receipt_row_hash(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(lambda row, _view, _bundle: bool(_receipt(row)), owner_row, view, providers)


def verify_self_contained_artifact_bundle(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None and _verify_self_contained_bundle(row, bundle),
        owner_row,
        view,
        providers,
    )


def verify_publication_genesis_binding(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(lambda row, current, _bundle: _verify_publication_genesis(row, current), owner_row, view, providers)


def verify_complete_publication_chain(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(lambda row, current, _bundle: _verify_publication_chain(row, current), owner_row, view, providers)


def verify_publication_closure(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(lambda row, current, _bundle: _verify_publication_closure(row, current), owner_row, view, providers)


def verify_publication_family_conclusion(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, current, _bundle: _verify_publication_conclusion(row, current), owner_row, view, providers
    )


def verify_exact_envelope_scan_allow(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None and _verify_final_scan(row, bundle), owner_row, view, providers
    )


def verify_exporter_event(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(lambda row, current, _bundle: _verify_exporter(row, current), owner_row, view, providers)


def verify_publication_branch(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(lambda row, _view, _bundle: bool(_receipt(row)), owner_row, view, providers)


def verify_complete_acyclic_projection(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, current, bundle: bundle is not None and _verify_projection_totality(row, current, bundle),
        owner_row,
        view,
        providers,
    )


def verify_exact_envelope_graph(owner_row: object, view: object, providers: object) -> bool:
    return _invoke(
        lambda row, _view, bundle: bundle is not None and _verify_envelope_graph(row, bundle),
        owner_row,
        view,
        providers,
    )


@dataclass(frozen=True, slots=True)
class C6C9NamedVerifierImplementation:
    verifier_code: str
    binding_keys: tuple[str, ...]
    implementation_id: str
    implementation_requirement_id: str
    implementation: object

    def __post_init__(self) -> None:
        if type(self.verifier_code) is not str or not self.verifier_code:
            raise TypeError("C6/C9 verifier code must be exact text")
        if type(self.binding_keys) is not tuple or not self.binding_keys:
            raise TypeError("C6/C9 verifier must bind at least one descriptor")
        if len(set(self.binding_keys)) != len(self.binding_keys):
            raise ValueError("C6/C9 verifier binding keys must be unique")
        expected_id = f"FAMILY_SCHEMA_NAMED_VERIFIER::{self.verifier_code}"
        expected_requirement = f"NAMED_VERIFIER::{self.verifier_code}"
        if self.implementation_id != expected_id:
            raise ValueError("C6/C9 verifier implementation ID mismatch")
        if self.implementation_requirement_id != expected_requirement:
            raise ValueError("C6/C9 verifier requirement ID mismatch")
        if type(self.implementation) is not FunctionType:
            raise TypeError("C6/C9 verifier implementation must be a Python function")
        parameters = tuple(inspect.signature(self.implementation).parameters.values())
        if tuple(parameter.name for parameter in parameters) != (
            "owner_row",
            "view",
            "providers",
        ) or any(
            parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
            or parameter.default is not inspect.Parameter.empty
            for parameter in parameters
        ):
            raise TypeError("C6/C9 named verifier must accept exactly owner_row, view, providers")


_IMPLEMENTATION_FUNCTIONS_BY_CODE = (
    ("VERIFY_OBSERVED_PIT_DECLARATION_BINDING", verify_observed_pit_declaration_binding),
    ("VERIFY_CODE_MANIFEST_DECLARATION_BINDING", verify_code_manifest_declaration_binding),
    ("VERIFY_GLOBAL_OOS_EXPOSURE_IDENTITY", verify_global_oos_exposure_identity),
    ("VERIFY_OBSERVED_PIT_EXPOSURE_BINDING", verify_observed_pit_exposure_binding),
    ("VERIFY_EXPOSURE_FINGERPRINT", verify_exposure_fingerprint),
    ("VERIFY_OOS_RELEASE_EXPOSURE_BINDING", verify_oos_release_exposure_binding),
    ("VERIFY_START_EXECUTION_ATTESTATION", verify_start_execution_attestation),
    ("VERIFY_IMMUTABLE_ARTIFACT_LEASE", verify_immutable_artifact_lease),
    ("VERIFY_OOS_RELEASE_STARTED_EXECUTION_IDENTITY", verify_oos_release_started_execution_identity),
    ("VERIFY_CLOSURE_EXTERNAL_REFERENCE_BRANCH", verify_closure_external_reference_branch),
    ("VERIFY_CLOSURE_OBSERVED_PIT_BINDING", verify_closure_observed_pit_binding),
    ("VERIFY_CLOSURE_OOS_EXPOSURE_BINDING", verify_closure_oos_exposure_binding),
    ("VERIFY_CLOSURE_CODE_MANIFEST_BINDING", verify_closure_code_manifest_binding),
    ("VERIFY_CLOSURE_START_EXECUTION_ATTESTATION", verify_closure_start_execution_attestation),
    ("VERIFY_CLOSURE_TERMINAL_EXECUTION_ATTESTATION", verify_closure_terminal_execution_attestation),
    ("VERIFY_CLOSURE_IMMUTABLE_ARTIFACT_LEASE", verify_closure_immutable_artifact_lease),
    ("VERIFY_PUBLICATION_CLOSURE_BINDING", verify_publication_closure_binding),
    ("VERIFY_PUBLICATION_EXTERNAL_REFERENCE_TOTALITY", verify_publication_external_reference_totality),
    ("VERIFY_PUBLICATION_OBSERVED_PIT_BINDING", verify_publication_observed_pit_binding),
    ("VERIFY_PUBLICATION_OOS_EXPOSURE_BINDING", verify_publication_oos_exposure_binding),
    ("VERIFY_PUBLICATION_CODE_MANIFEST_BINDING", verify_publication_code_manifest_binding),
    ("VERIFY_PUBLICATION_START_EXECUTION_ATTESTATION", verify_publication_start_execution_attestation),
    ("VERIFY_PUBLICATION_TERMINAL_EXECUTION_ATTESTATION", verify_publication_terminal_execution_attestation),
    ("VERIFY_PUBLICATION_IMMUTABLE_ARTIFACT_LEASE", verify_publication_immutable_artifact_lease),
    ("VERIFY_OOS_RELEASE_START_LEASE_CONTINUITY", verify_oos_release_start_lease_continuity),
    ("VERIFY_CLOSURE_OBSERVED_PIT_EXPOSURE_CONTINUITY", verify_closure_observed_pit_exposure_continuity),
    ("VERIFY_CLOSURE_CODE_START_CONTINUITY", verify_closure_code_start_continuity),
    ("VERIFY_CLOSURE_START_TERMINAL_CONTINUITY", verify_closure_start_terminal_continuity),
    ("VERIFY_CLOSURE_START_LEASE_CONTINUITY", verify_closure_start_lease_continuity),
    ("VERIFY_PUBLICATION_OBSERVED_PIT_EXPOSURE_CONTINUITY", verify_publication_observed_pit_exposure_continuity),
    ("VERIFY_PUBLICATION_CODE_START_CONTINUITY", verify_publication_code_start_continuity),
    ("VERIFY_PUBLICATION_START_TERMINAL_CONTINUITY", verify_publication_start_terminal_continuity),
    ("VERIFY_PUBLICATION_START_LEASE_CONTINUITY", verify_publication_start_lease_continuity),
    ("c9.verify_publication_receipt_row_hash_v1", verify_publication_receipt_row_hash),
    ("c9.verify_self_contained_artifact_bundle_v1", verify_self_contained_artifact_bundle),
    ("c9.verify_publication_genesis_binding_v1", verify_publication_genesis_binding),
    ("c9.verify_complete_publication_chain_v1", verify_complete_publication_chain),
    ("c9.verify_publication_closure_v1", verify_publication_closure),
    ("c9.verify_publication_family_conclusion_v1", verify_publication_family_conclusion),
    ("c9.verify_exact_envelope_scan_allow_v1", verify_exact_envelope_scan_allow),
    ("c9.verify_exporter_event_v1", verify_exporter_event),
    ("c9.verify_publication_branch_v1", verify_publication_branch),
    ("c9.verify_complete_acyclic_projection_v1", verify_complete_acyclic_projection),
    ("c9.verify_exact_envelope_graph_v1", verify_exact_envelope_graph),
)


C6_C9_EXTERNAL_COMPONENT_VERIFIER_CODES = (_EXTERNAL_C8_CODE,)


def _component_bindings() -> tuple[object, ...]:
    return tuple(
        binding
        for binding in FAMILY_SCHEMA_VERIFIER_BINDINGS
        if binding.binding_key.startswith((_C6_COMPONENT_ID + ":", _C9_COMPONENT_ID + ":"))
    )


_BINDINGS = _component_bindings()
_BINDINGS_BY_CODE: dict[str, tuple[object, ...]] = {
    code: tuple(binding for binding in _BINDINGS if binding.verifier_code == code)
    for code in dict.fromkeys(binding.verifier_code for binding in _BINDINGS)
}
_FUNCTIONS_BY_CODE = dict(_IMPLEMENTATION_FUNCTIONS_BY_CODE)
if (
    len(_FUNCTIONS_BY_CODE) != len(_IMPLEMENTATION_FUNCTIONS_BY_CODE)
    or set(_FUNCTIONS_BY_CODE) != set(_BINDINGS_BY_CODE) - set(C6_C9_EXTERNAL_COMPONENT_VERIFIER_CODES)
    or set(C6_C9_EXTERNAL_COMPONENT_VERIFIER_CODES) != {_EXTERNAL_C8_CODE}
):
    raise ValueError("C6/C9 named verifier implementation coverage is not exact")

C6_C9_NAMED_VERIFIER_IMPLEMENTATIONS = tuple(
    C6C9NamedVerifierImplementation(
        verifier_code=code,
        binding_keys=tuple(binding.binding_key for binding in _BINDINGS_BY_CODE[code]),
        implementation_id=f"FAMILY_SCHEMA_NAMED_VERIFIER::{code}",
        implementation_requirement_id=f"NAMED_VERIFIER::{code}",
        implementation=implementation,
    )
    for code, implementation in _IMPLEMENTATION_FUNCTIONS_BY_CODE
)


def _validate_descriptor_coverage() -> None:
    for implementation in C6_C9_NAMED_VERIFIER_IMPLEMENTATIONS:
        descriptors = _BINDINGS_BY_CODE[implementation.verifier_code]
        if implementation.binding_keys != tuple(descriptor.binding_key for descriptor in descriptors) or any(
            descriptor.implementation_id != implementation.implementation_id
            or descriptor.implementation_requirement_id != implementation.implementation_requirement_id
            for descriptor in descriptors
        ):
            raise ValueError(f"C6/C9 descriptor mismatch: {implementation.verifier_code}")
    external = _BINDINGS_BY_CODE[_EXTERNAL_C8_CODE]
    if not external or any(
        descriptor.implementation_id != "FAMILY_SCHEMA_NAMED_VERIFIER::c8.verify_family_assessment_v1"
        or descriptor.implementation_requirement_id != "NAMED_VERIFIER::c8.verify_family_assessment_v1"
        for descriptor in external
    ):
        raise ValueError("C9 C8-semantic binding is not an exact shared dependency")


_validate_descriptor_coverage()


def verify_c6_c9_named(
    verifier_code: object,
    owner_row: object,
    view: object,
    providers: object,
) -> bool:
    """Dispatch one exact C6/C9 verifier, rejecting unknown or malformed input."""

    if type(verifier_code) is not str:
        return False
    for item in C6_C9_NAMED_VERIFIER_IMPLEMENTATIONS:
        if item.verifier_code == verifier_code:
            implementation = item.implementation
            if type(implementation) is not FunctionType:
                return False
            try:
                return implementation(owner_row, view, providers) is True
            except Exception:
                return False
    return False


__all__ = [
    "C6C9NamedVerifierImplementation",
    "C6_C9_EXTERNAL_COMPONENT_VERIFIER_CODES",
    "C6_C9_NAMED_VERIFIER_IMPLEMENTATIONS",
    "verify_c6_c9_named",
]
