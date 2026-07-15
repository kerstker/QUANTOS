"""Deterministic policy evaluation for externally verified build provenance.

This module has no signing, network, credential, deployment, or execution path.
The caller must first verify a Sigstore bundle at an independent boundary and
pass only the verified DSSE payload into ``evaluate_verified_provenance``.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_DIGEST = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
MAX_ARTIFACT_AGE_MS = 90 * 86_400_000
MAX_EVIDENCE_AGE_MS = 24 * 60 * 60_000
MAX_CLOCK_SKEW_MS = 5 * 60_000


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


class _ProvenanceContract(BaseModel):
    # Signed payload values must remain byte-semantically exact. In particular,
    # do not normalize surrounding whitespace before policy comparison.
    model_config = ConfigDict(extra="allow")


class DuplicateJsonKeyError(ValueError):
    """A trust document contains an ambiguous duplicate object member."""


def load_json_without_duplicate_keys(content: bytes | str) -> Any:
    """Parse standards-compliant JSON while rejecting duplicate object keys."""

    if isinstance(content, bytes):
        text = content.decode("utf-8")
    elif isinstance(content, str):
        text = content
    else:
        raise TypeError("JSON content must be bytes or text")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise DuplicateJsonKeyError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    return json.loads(
        text,
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )


class ArtifactAdmissionPolicy(_FrozenContract):
    schema_version: Literal[3] = 3
    policy_id: str = Field(min_length=1)
    artifact_name: str = Field(min_length=1)
    artifact_sha256: str
    certificate_identity: str = Field(min_length=1)
    certificate_oidc_issuer: str = Field(min_length=1)
    trusted_root_sha256: str
    payload_type: str = Field(min_length=1)
    statement_type: str = Field(min_length=1)
    predicate_type: str = Field(min_length=1)
    subject_name: str = Field(min_length=1)
    builder_id: str = Field(min_length=1)
    build_type: str = Field(min_length=1)
    source_uri: str = Field(min_length=1)
    source_digest_algorithm: str = Field(min_length=1)
    source_digest: str
    required_receipt_authority_id: str = Field(min_length=1)
    required_receipt_key_id: str = Field(min_length=1)
    required_receipt_public_key_sha256: str
    required_verifier_id: str = Field(min_length=1)
    required_revocation_source: str = Field(min_length=1)
    max_artifact_age_ms: int = Field(gt=0, le=MAX_ARTIFACT_AGE_MS)
    max_verification_age_ms: int = Field(gt=0, le=MAX_EVIDENCE_AGE_MS)
    max_checkpoint_age_ms: int = Field(gt=0, le=MAX_EVIDENCE_AGE_MS)
    max_revocation_age_ms: int = Field(gt=0, le=MAX_EVIDENCE_AGE_MS)
    clock_skew_ms: int = Field(ge=0, le=MAX_CLOCK_SKEW_MS)

    @field_validator(
        "artifact_sha256",
        "trusted_root_sha256",
        "required_receipt_public_key_sha256",
        mode="before",
    )
    @classmethod
    def _validate_sha256(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a SHA-256 digest")
        normalized = value.strip().lower()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError("must be a SHA-256 digest")
        return normalized

    @field_validator("source_digest", mode="before")
    @classmethod
    def _validate_source_digest(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a 40- or 64-character hexadecimal digest")
        normalized = value.strip().lower()
        if _SOURCE_DIGEST.fullmatch(normalized) is None:
            raise ValueError("must be a 40- or 64-character hexadecimal digest")
        return normalized


class VerifiedSigstoreFacts(_FrozenContract):
    """Facts extracted only after the pinned Sigstore verifier succeeds."""

    schema_version: Literal[1] = 1
    verifier_id: str = Field(min_length=1)
    artifact_sha256: str
    bundle_sha256: str
    trusted_root_sha256: str
    certificate_sha256: str
    certificate_identity: str = Field(min_length=1)
    certificate_oidc_issuer: str = Field(min_length=1)
    certificate_not_before_ms: int = Field(ge=0)
    certificate_not_after_ms: int = Field(ge=0)
    transparency_integrated_at_ms: int = Field(ge=0)
    transparency_time_source: Literal["REKOR_V1_INTEGRATED_TIME"] = "REKOR_V1_INTEGRATED_TIME"
    transparency_log_verified: Literal[True]
    signed_certificate_timestamp_verified: Literal[True]

    @field_validator(
        "artifact_sha256",
        "bundle_sha256",
        "trusted_root_sha256",
        "certificate_sha256",
        mode="before",
    )
    @classmethod
    def _validate_sha256(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a SHA-256 digest")
        normalized = value.strip().lower()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError("must be a SHA-256 digest")
        return normalized

    @model_validator(mode="after")
    def _validate_certificate_window(self) -> VerifiedSigstoreFacts:
        if self.certificate_not_before_ms > self.certificate_not_after_ms:
            raise ValueError("certificate validity window is inverted")
        return self


class ArtifactVerificationReceipt(_FrozenContract):
    """Externally attestable freshness and revocation evidence for one bundle."""

    schema_version: Literal[1] = 1
    receipt_id: str = Field(min_length=1)
    verifier_id: str = Field(min_length=1)
    artifact_sha256: str
    bundle_sha256: str
    trusted_root_sha256: str
    certificate_sha256: str
    verified_at_ms: int = Field(ge=0)
    transparency_checkpoint_at_ms: int = Field(ge=0)
    revocation_status: Literal["GOOD", "REVOKED", "UNKNOWN"]
    revocation_checked_at_ms: int = Field(ge=0)
    revocation_source: str = Field(min_length=1)

    @field_validator(
        "artifact_sha256",
        "bundle_sha256",
        "trusted_root_sha256",
        "certificate_sha256",
        mode="before",
    )
    @classmethod
    def _validate_sha256(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("must be a SHA-256 digest")
        normalized = value.strip().lower()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError("must be a SHA-256 digest")
        return normalized


class VerifiedReceiptAuthentication(_FrozenContract):
    """Facts returned only after an external receipt signature verifies."""

    schema_version: Literal[1] = 1
    authority_id: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    algorithm: Literal["Ed25519"] = "Ed25519"
    public_key_sha256: str
    envelope_sha256: str
    receipt_sha256: str
    signature_verified: Literal[True] = True

    @field_validator("public_key_sha256", "envelope_sha256", "receipt_sha256")
    @classmethod
    def _validate_sha256(cls, value: object) -> str:
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ValueError("must be a lowercase SHA-256 digest")
        return value


def artifact_verification_receipt_sha256(
    receipt: ArtifactVerificationReceipt,
) -> str:
    if not isinstance(receipt, ArtifactVerificationReceipt):
        raise TypeError("receipt must be an ArtifactVerificationReceipt")
    canonical = json.dumps(
        receipt.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class ProvenanceSubject(_ProvenanceContract):
    name: StrictStr = Field(min_length=1)
    digest: dict[StrictStr, StrictStr]


class ResolvedDependency(_ProvenanceContract):
    uri: StrictStr = Field(min_length=1)
    digest: dict[StrictStr, StrictStr]


class BuildDefinition(_ProvenanceContract):
    build_type: StrictStr = Field(alias="buildType", min_length=1)
    resolved_dependencies: tuple[ResolvedDependency, ...] = Field(alias="resolvedDependencies")


class Builder(_ProvenanceContract):
    builder_id: StrictStr = Field(alias="id", min_length=1)


class RunDetails(_ProvenanceContract):
    builder: Builder


class SlsaProvenanceV1(_ProvenanceContract):
    build_definition: BuildDefinition = Field(alias="buildDefinition")
    run_details: RunDetails = Field(alias="runDetails")


class InTotoStatement(_ProvenanceContract):
    statement_type: StrictStr = Field(alias="_type", min_length=1)
    subject: tuple[ProvenanceSubject, ...]
    predicate_type: StrictStr = Field(alias="predicateType", min_length=1)
    predicate: SlsaProvenanceV1


class ArtifactAdmissionDecision(_FrozenContract):
    schema_version: Literal[4] = 4
    verdict: Literal["ALLOW", "DENY"]
    reason_code: str = Field(min_length=1)
    policy_id: str = ""
    policy_sha256: str = ""
    policy_registry_sha256: str = ""
    policy_registry_generation: int | None = None
    policy_registry_event_seq: int | None = Field(default=None, ge=0)
    policy_registry_event_hash: str = ""
    artifact_name: str = ""
    artifact_sha256: str = ""
    bundle_sha256: str = ""
    trusted_root_sha256: str = ""
    certificate_identity: str = ""
    certificate_oidc_issuer: str = ""
    subject_name: str = ""
    predicate_type: str = ""
    builder_id: str = ""
    build_type: str = ""
    source_uri: str = ""
    source_digest: str = ""
    verification_receipt_sha256: str = ""
    receipt_envelope_sha256: str = ""
    receipt_authority_registry_sha256: str = ""
    receipt_authority_registry_generation: int | None = None
    receipt_authority_registry_event_seq: int | None = Field(default=None, ge=0)
    receipt_authority_registry_event_hash: str = ""
    receipt_authority_id: str = ""
    receipt_key_id: str = ""
    receipt_public_key_sha256: str = ""
    receipt_signature_algorithm: str = ""
    receipt_signature_verified: bool = False
    receipt_id: str = ""
    verifier_id: str = ""
    verifier_runtime_id: str = ""
    certificate_sha256: str = ""
    verified_at_ms: int | None = None
    transparency_integrated_at_ms: int | None = None
    transparency_checkpoint_at_ms: int | None = None
    revocation_status: str = ""
    revocation_checked_at_ms: int | None = None
    revocation_source: str = ""
    admission_at_ms: int | None = None
    policy_max_artifact_age_ms: int | None = None
    policy_max_verification_age_ms: int | None = None
    policy_max_checkpoint_age_ms: int | None = None
    policy_max_revocation_age_ms: int | None = None
    policy_clock_skew_ms: int | None = None
    authority_store_kind: str = ""
    authority_store_schema_version: int | None = None
    authority_decision_id: str = ""
    execution_authority_granted: Literal[False] = False
    deployment_authority_granted: Literal[False] = False
    live_trading_authority_granted: Literal[False] = False

    @field_validator(
        "policy_registry_event_hash",
        "receipt_authority_registry_event_hash",
    )
    @classmethod
    def _validate_optional_registry_event_hash(cls, value: str) -> str:
        if value and _SHA256.fullmatch(value) is None:
            raise ValueError("must be an empty value or a lowercase SHA-256 digest")
        return value


def denied_admission_decision(
    reason_code: str,
    *,
    policy: ArtifactAdmissionPolicy | None = None,
    policy_sha256: str = "",
    policy_registry_sha256: str = "",
    policy_registry_generation: int | None = None,
    policy_registry_event_seq: int | None = None,
    policy_registry_event_hash: str = "",
    verifier_runtime_id: str = "",
    artifact_sha256: str = "",
    bundle_sha256: str = "",
    trusted_root_sha256: str = "",
    verification: VerifiedSigstoreFacts | None = None,
    receipt: ArtifactVerificationReceipt | None = None,
    receipt_authentication: VerifiedReceiptAuthentication | None = None,
    receipt_authority_registry_sha256: str = "",
    receipt_authority_registry_generation: int | None = None,
    receipt_authority_registry_event_seq: int | None = None,
    receipt_authority_registry_event_hash: str = "",
    admission_at_ms: int | None = None,
) -> ArtifactAdmissionDecision:
    """Create a fail-closed decision without adding any authority."""
    return ArtifactAdmissionDecision(
        verdict="DENY",
        reason_code=reason_code,
        policy_id=policy.policy_id if policy else "",
        policy_sha256=policy_sha256,
        policy_registry_sha256=policy_registry_sha256,
        policy_registry_generation=policy_registry_generation,
        policy_registry_event_seq=policy_registry_event_seq,
        policy_registry_event_hash=policy_registry_event_hash,
        artifact_name=policy.artifact_name if policy else "",
        artifact_sha256=artifact_sha256,
        bundle_sha256=bundle_sha256,
        trusted_root_sha256=trusted_root_sha256,
        certificate_identity=policy.certificate_identity if policy else "",
        certificate_oidc_issuer=(policy.certificate_oidc_issuer if policy else ""),
        verification_receipt_sha256=(artifact_verification_receipt_sha256(receipt) if receipt else ""),
        receipt_envelope_sha256=(receipt_authentication.envelope_sha256 if receipt_authentication else ""),
        receipt_authority_registry_sha256=receipt_authority_registry_sha256,
        receipt_authority_registry_generation=(receipt_authority_registry_generation),
        receipt_authority_registry_event_seq=(receipt_authority_registry_event_seq),
        receipt_authority_registry_event_hash=(receipt_authority_registry_event_hash),
        receipt_authority_id=(receipt_authentication.authority_id if receipt_authentication else ""),
        receipt_key_id=(receipt_authentication.key_id if receipt_authentication else ""),
        receipt_public_key_sha256=(receipt_authentication.public_key_sha256 if receipt_authentication else ""),
        receipt_signature_algorithm=(receipt_authentication.algorithm if receipt_authentication else ""),
        receipt_signature_verified=(receipt_authentication.signature_verified if receipt_authentication else False),
        receipt_id=receipt.receipt_id if receipt else "",
        verifier_id=(verification.verifier_id if verification else receipt.verifier_id if receipt else ""),
        verifier_runtime_id=verifier_runtime_id,
        certificate_sha256=(
            verification.certificate_sha256 if verification else receipt.certificate_sha256 if receipt else ""
        ),
        verified_at_ms=receipt.verified_at_ms if receipt else None,
        transparency_integrated_at_ms=(verification.transparency_integrated_at_ms if verification else None),
        transparency_checkpoint_at_ms=(receipt.transparency_checkpoint_at_ms if receipt else None),
        revocation_status=receipt.revocation_status if receipt else "",
        revocation_checked_at_ms=(receipt.revocation_checked_at_ms if receipt else None),
        revocation_source=receipt.revocation_source if receipt else "",
        admission_at_ms=admission_at_ms,
        policy_max_artifact_age_ms=(policy.max_artifact_age_ms if policy else None),
        policy_max_verification_age_ms=(policy.max_verification_age_ms if policy else None),
        policy_max_checkpoint_age_ms=(policy.max_checkpoint_age_ms if policy else None),
        policy_max_revocation_age_ms=(policy.max_revocation_age_ms if policy else None),
        policy_clock_skew_ms=policy.clock_skew_ms if policy else None,
    )


def evaluate_verified_provenance(
    *,
    payload_type: str,
    payload: bytes,
    artifact_sha256: str,
    bundle_sha256: str,
    trusted_root_sha256: str,
    policy_sha256: str,
    policy_registry_sha256: str,
    policy_registry_generation: int,
    policy_registry_event_seq: int,
    policy_registry_event_hash: str,
    verifier_runtime_id: str,
    receipt_authority_registry_sha256: str,
    receipt_authority_registry_generation: int,
    receipt_authority_registry_event_seq: int,
    receipt_authority_registry_event_hash: str,
    policy: ArtifactAdmissionPolicy,
    verification: VerifiedSigstoreFacts | None,
    receipt: ArtifactVerificationReceipt | None,
    receipt_authentication: VerifiedReceiptAuthentication | None,
    admission_at_ms: int | None,
) -> ArtifactAdmissionDecision:
    """Evaluate verified provenance plus fresh, non-revoked verification evidence."""

    def valid_event_seq(value: object) -> bool:
        return not isinstance(value, bool) and isinstance(value, int) and value >= 0

    def valid_event_hash(value: object) -> bool:
        return isinstance(value, str) and _SHA256.fullmatch(value) is not None

    def deny(reason_code: str) -> ArtifactAdmissionDecision:
        return denied_admission_decision(
            reason_code,
            policy=policy,
            policy_sha256=policy_sha256,
            policy_registry_sha256=policy_registry_sha256,
            policy_registry_generation=policy_registry_generation,
            policy_registry_event_seq=(
                policy_registry_event_seq if valid_event_seq(policy_registry_event_seq) else None
            ),
            policy_registry_event_hash=(
                policy_registry_event_hash if valid_event_hash(policy_registry_event_hash) else ""
            ),
            verifier_runtime_id=verifier_runtime_id,
            artifact_sha256=artifact_sha256,
            bundle_sha256=bundle_sha256,
            trusted_root_sha256=trusted_root_sha256,
            verification=verification,
            receipt=receipt,
            receipt_authentication=receipt_authentication,
            receipt_authority_registry_sha256=(receipt_authority_registry_sha256),
            receipt_authority_registry_generation=(receipt_authority_registry_generation),
            receipt_authority_registry_event_seq=(
                receipt_authority_registry_event_seq if valid_event_seq(receipt_authority_registry_event_seq) else None
            ),
            receipt_authority_registry_event_hash=(
                receipt_authority_registry_event_hash if valid_event_hash(receipt_authority_registry_event_hash) else ""
            ),
            admission_at_ms=admission_at_ms,
        )

    if _SHA256.fullmatch(artifact_sha256) is None:
        return deny("ARTIFACT_DIGEST_INVALID")
    if artifact_sha256 != policy.artifact_sha256:
        return deny("ARTIFACT_DIGEST_MISMATCH")
    if _SHA256.fullmatch(bundle_sha256) is None:
        return deny("BUNDLE_DIGEST_INVALID")
    if trusted_root_sha256 != policy.trusted_root_sha256:
        return deny("TRUSTED_ROOT_DIGEST_MISMATCH")
    if _SHA256.fullmatch(policy_sha256) is None:
        return deny("POLICY_DIGEST_INVALID")
    if _SHA256.fullmatch(policy_registry_sha256) is None:
        return deny("POLICY_REGISTRY_DIGEST_INVALID")
    if (
        isinstance(policy_registry_generation, bool)
        or not isinstance(policy_registry_generation, int)
        or policy_registry_generation < 0
    ):
        return deny("POLICY_REGISTRY_GENERATION_INVALID")
    if not valid_event_seq(policy_registry_event_seq):
        return deny("POLICY_REGISTRY_EVENT_SEQ_INVALID")
    if not valid_event_hash(policy_registry_event_hash):
        return deny("POLICY_REGISTRY_EVENT_HASH_INVALID")
    if _SHA256.fullmatch(receipt_authority_registry_sha256) is None:
        return deny("RECEIPT_AUTHORITY_REGISTRY_DIGEST_INVALID")
    if (
        isinstance(receipt_authority_registry_generation, bool)
        or not isinstance(receipt_authority_registry_generation, int)
        or receipt_authority_registry_generation < 0
    ):
        return deny("RECEIPT_AUTHORITY_REGISTRY_GENERATION_INVALID")
    if not valid_event_seq(receipt_authority_registry_event_seq):
        return deny("RECEIPT_AUTHORITY_REGISTRY_EVENT_SEQ_INVALID")
    if not valid_event_hash(receipt_authority_registry_event_hash):
        return deny("RECEIPT_AUTHORITY_REGISTRY_EVENT_HASH_INVALID")
    if not verifier_runtime_id:
        return deny("VERIFIER_RUNTIME_ID_REQUIRED")
    if verifier_runtime_id != policy.required_verifier_id:
        return deny("VERIFIER_RUNTIME_MISMATCH")
    if verification is None:
        return deny("SIGSTORE_FACTS_REQUIRED")
    if receipt is None:
        return deny("VERIFICATION_RECEIPT_REQUIRED")
    if receipt_authentication is None:
        return deny("RECEIPT_AUTHENTICATION_REQUIRED")
    if (
        admission_at_ms is None
        or isinstance(admission_at_ms, bool)
        or not isinstance(admission_at_ms, int)
        or admission_at_ms < 0
    ):
        return deny("ADMISSION_TIME_REQUIRED")

    if verification.verifier_id != verifier_runtime_id:
        return deny("VERIFIER_ID_MISMATCH")
    if verification.artifact_sha256 != artifact_sha256:
        return deny("SIGSTORE_ARTIFACT_MISMATCH")
    if verification.bundle_sha256 != bundle_sha256:
        return deny("SIGSTORE_BUNDLE_MISMATCH")
    if verification.trusted_root_sha256 != trusted_root_sha256:
        return deny("SIGSTORE_TRUSTED_ROOT_MISMATCH")
    if (
        verification.certificate_identity != policy.certificate_identity
        or verification.certificate_oidc_issuer != policy.certificate_oidc_issuer
    ):
        return deny("SIGSTORE_IDENTITY_MISMATCH")

    receipt_sha256 = artifact_verification_receipt_sha256(receipt)
    if receipt_authentication.receipt_sha256 != receipt_sha256:
        return deny("RECEIPT_AUTHENTICATION_DIGEST_MISMATCH")
    if (
        receipt_authentication.authority_id != policy.required_receipt_authority_id
        or receipt_authentication.key_id != policy.required_receipt_key_id
        or receipt_authentication.public_key_sha256 != policy.required_receipt_public_key_sha256
        or receipt_authentication.algorithm != "Ed25519"
        or receipt_authentication.signature_verified is not True
    ):
        return deny("RECEIPT_AUTHENTICATION_POLICY_MISMATCH")
    if receipt.verifier_id != policy.required_verifier_id:
        return deny("RECEIPT_VERIFIER_MISMATCH")
    if receipt.artifact_sha256 != artifact_sha256:
        return deny("RECEIPT_ARTIFACT_MISMATCH")
    if receipt.bundle_sha256 != bundle_sha256:
        return deny("RECEIPT_BUNDLE_MISMATCH")
    if receipt.trusted_root_sha256 != trusted_root_sha256:
        return deny("RECEIPT_TRUSTED_ROOT_MISMATCH")
    if receipt.certificate_sha256 != verification.certificate_sha256:
        return deny("RECEIPT_CERTIFICATE_MISMATCH")
    if receipt.revocation_source != policy.required_revocation_source:
        return deny("REVOCATION_SOURCE_MISMATCH")
    if receipt.revocation_status == "REVOKED":
        return deny("CERTIFICATE_REVOKED")

    if verification.transparency_log_verified is not True:
        return deny("TRANSPARENCY_VERIFICATION_REQUIRED")
    if verification.signed_certificate_timestamp_verified is not True:
        return deny("SCT_VERIFICATION_REQUIRED")

    skew = policy.clock_skew_ms
    integrated_at = verification.transparency_integrated_at_ms
    if integrated_at > admission_at_ms + skew:
        return deny("TRANSPARENCY_TIME_FUTURE")
    if admission_at_ms - integrated_at > policy.max_artifact_age_ms:
        return deny("SIGNED_PROVENANCE_STALE")
    if not (
        verification.certificate_not_before_ms - skew <= integrated_at <= verification.certificate_not_after_ms + skew
    ):
        return deny("SIGNING_TIME_OUTSIDE_CERTIFICATE_VALIDITY")

    if receipt.verified_at_ms > admission_at_ms + skew:
        return deny("VERIFICATION_RECEIPT_FUTURE")
    if admission_at_ms - receipt.verified_at_ms > policy.max_verification_age_ms:
        return deny("VERIFICATION_RECEIPT_STALE")
    if receipt.verified_at_ms + skew < integrated_at:
        return deny("VERIFICATION_PRECEDES_TRANSPARENCY_INTEGRATION")

    checkpoint_at = receipt.transparency_checkpoint_at_ms
    if checkpoint_at > admission_at_ms + skew:
        return deny("TRANSPARENCY_CHECKPOINT_FUTURE")
    if admission_at_ms - checkpoint_at > policy.max_checkpoint_age_ms:
        return deny("TRANSPARENCY_CHECKPOINT_STALE")
    if checkpoint_at + skew < integrated_at:
        return deny("TRANSPARENCY_CHECKPOINT_PRECEDES_INTEGRATION")
    if receipt.verified_at_ms + skew < checkpoint_at:
        return deny("VERIFICATION_PRECEDES_TRANSPARENCY_CHECKPOINT")

    revocation_at = receipt.revocation_checked_at_ms
    if revocation_at > admission_at_ms + skew:
        return deny("REVOCATION_EVIDENCE_FUTURE")
    if admission_at_ms - revocation_at > policy.max_revocation_age_ms:
        return deny("REVOCATION_EVIDENCE_STALE")
    if revocation_at + skew < integrated_at:
        return deny("REVOCATION_CHECK_PRECEDES_SIGNING")
    if receipt.revocation_status != "GOOD":
        return deny("REVOCATION_STATUS_UNKNOWN")

    if payload_type != policy.payload_type:
        return deny("PAYLOAD_TYPE_MISMATCH")
    try:
        raw = load_json_without_duplicate_keys(payload)
        statement = InTotoStatement.model_validate(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError, ValueError):
        return deny("PROVENANCE_PAYLOAD_INVALID")
    if statement.statement_type != policy.statement_type:
        reason = "STATEMENT_TYPE_MISMATCH"
    elif statement.predicate_type != policy.predicate_type:
        reason = "PREDICATE_TYPE_MISMATCH"
    elif (
        len(statement.subject) != 1
        or statement.subject[0].name != policy.subject_name
        or statement.subject[0].digest != {"sha256": artifact_sha256}
    ):
        reason = "SUBJECT_MISMATCH"
    elif statement.predicate.run_details.builder.builder_id != policy.builder_id:
        reason = "BUILDER_MISMATCH"
    elif statement.predicate.build_definition.build_type != policy.build_type:
        reason = "BUILD_TYPE_MISMATCH"
    else:
        source_candidates = [
            dependency
            for dependency in statement.predicate.build_definition.resolved_dependencies
            if dependency.uri == policy.source_uri
        ]
        reason = (
            ""
            if len(source_candidates) == 1
            and source_candidates[0].digest == {policy.source_digest_algorithm: policy.source_digest}
            else "SOURCE_MISMATCH"
        )
    if reason:
        return deny(reason)
    return ArtifactAdmissionDecision(
        verdict="ALLOW",
        reason_code="VERIFIED_PROVENANCE_POLICY_MATCH",
        policy_id=policy.policy_id,
        policy_sha256=policy_sha256,
        policy_registry_sha256=policy_registry_sha256,
        policy_registry_generation=policy_registry_generation,
        policy_registry_event_seq=policy_registry_event_seq,
        policy_registry_event_hash=policy_registry_event_hash,
        artifact_name=policy.artifact_name,
        artifact_sha256=artifact_sha256,
        bundle_sha256=bundle_sha256,
        trusted_root_sha256=trusted_root_sha256,
        certificate_identity=policy.certificate_identity,
        certificate_oidc_issuer=policy.certificate_oidc_issuer,
        subject_name=statement.subject[0].name,
        predicate_type=statement.predicate_type,
        builder_id=statement.predicate.run_details.builder.builder_id,
        build_type=statement.predicate.build_definition.build_type,
        source_uri=policy.source_uri,
        source_digest=policy.source_digest,
        verification_receipt_sha256=receipt_sha256,
        receipt_envelope_sha256=receipt_authentication.envelope_sha256,
        receipt_authority_registry_sha256=receipt_authority_registry_sha256,
        receipt_authority_registry_generation=(receipt_authority_registry_generation),
        receipt_authority_registry_event_seq=(receipt_authority_registry_event_seq),
        receipt_authority_registry_event_hash=(receipt_authority_registry_event_hash),
        receipt_authority_id=receipt_authentication.authority_id,
        receipt_key_id=receipt_authentication.key_id,
        receipt_public_key_sha256=receipt_authentication.public_key_sha256,
        receipt_signature_algorithm=receipt_authentication.algorithm,
        receipt_signature_verified=receipt_authentication.signature_verified,
        receipt_id=receipt.receipt_id,
        verifier_id=verification.verifier_id,
        verifier_runtime_id=verifier_runtime_id,
        certificate_sha256=verification.certificate_sha256,
        verified_at_ms=receipt.verified_at_ms,
        transparency_integrated_at_ms=integrated_at,
        transparency_checkpoint_at_ms=checkpoint_at,
        revocation_status=receipt.revocation_status,
        revocation_checked_at_ms=revocation_at,
        revocation_source=receipt.revocation_source,
        admission_at_ms=admission_at_ms,
        policy_max_artifact_age_ms=policy.max_artifact_age_ms,
        policy_max_verification_age_ms=policy.max_verification_age_ms,
        policy_max_checkpoint_age_ms=policy.max_checkpoint_age_ms,
        policy_max_revocation_age_ms=policy.max_revocation_age_ms,
        policy_clock_skew_ms=policy.clock_skew_ms,
    )


__all__ = [
    "ArtifactAdmissionDecision",
    "ArtifactAdmissionPolicy",
    "ArtifactVerificationReceipt",
    "DuplicateJsonKeyError",
    "InTotoStatement",
    "MAX_ARTIFACT_AGE_MS",
    "MAX_CLOCK_SKEW_MS",
    "MAX_EVIDENCE_AGE_MS",
    "VerifiedSigstoreFacts",
    "VerifiedReceiptAuthentication",
    "artifact_verification_receipt_sha256",
    "denied_admission_decision",
    "evaluate_verified_provenance",
    "load_json_without_duplicate_keys",
]
