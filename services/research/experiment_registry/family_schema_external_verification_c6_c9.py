"""Typed external trust seams consumed by the C6/C9 Pass-M verifiers.

These protocols do not implement or simulate an external authority.  A caller
must supply deployment-owned providers that resolve and verify the exact
authority claim.  Named verifiers treat a missing provider, an exception, a
non-exact return type, or a mismatched claim as rejection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from quantos.contracts.research import (
    CodeManifestEvidence,
    ExecutionCodeAttestation,
    ImmutableArtifactLease,
    ObservedPitDatasetHandle,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, context: str) -> str:
    if type(value) is not str or not value:
        raise TypeError(f"{context} must be non-empty exact text")
    return value


def _sha256(value: object, context: str) -> str:
    text = _text(value, context)
    if _SHA256_RE.fullmatch(text) is None:
        raise ValueError(f"{context} must be lowercase SHA-256 text")
    return text


@dataclass(frozen=True, slots=True)
class ExecutionAttestationSubjectContext:
    """Closed family/attempt/batch role issued with an execution attestation."""

    family_id: str
    attempt_id: str
    evaluation_batch_id: str
    role: Literal["START", "TERMINAL"]

    def __post_init__(self) -> None:
        _text(self.family_id, "execution subject family_id")
        _text(self.attempt_id, "execution subject attempt_id")
        _text(self.evaluation_batch_id, "execution subject evaluation_batch_id")
        if self.role not in ("START", "TERMINAL"):
            raise ValueError("execution subject role must be START or TERMINAL")


@dataclass(frozen=True, slots=True)
class LeaseSubjectContext:
    """Closed family/attempt/batch identity issued with an immutable lease."""

    family_id: str
    attempt_id: str
    evaluation_batch_id: str

    def __post_init__(self) -> None:
        _text(self.family_id, "lease subject family_id")
        _text(self.attempt_id, "lease subject attempt_id")
        _text(self.evaluation_batch_id, "lease subject evaluation_batch_id")


def _projection_hashes(
    values: object,
    *,
    genesis_record_hash: str,
    authority_head: str,
    context: str,
) -> tuple[str, ...]:
    if type(values) is not tuple or len(values) < 2:
        raise TypeError(f"{context} must be a non-empty complete authority prefix")
    for value in values:
        _sha256(value, context)
    if len(set(values)) != len(values):
        raise ValueError(f"{context} must not repeat authority records")
    if values[0] != genesis_record_hash or values[-1] != authority_head:
        raise ValueError(f"{context} must run from exact genesis to authority head")
    return values


@dataclass(frozen=True, slots=True)
class VerifiedExecutionCodeAttestation:
    """Externally verified execution evidence plus its complete provider prefix."""

    evidence: ExecutionCodeAttestation
    authority_genesis_record_hash: str
    subject_context: ExecutionAttestationSubjectContext
    authority_projection_record_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.evidence) is not ExecutionCodeAttestation:
            raise TypeError("execution evidence must be the exact contract type")
        validated = ExecutionCodeAttestation.model_validate(
            self.evidence.model_dump(mode="python"),
            strict=True,
        )
        object.__setattr__(self, "evidence", validated)
        genesis = _sha256(
            self.authority_genesis_record_hash,
            "execution provider genesis",
        )
        if type(self.subject_context) is not ExecutionAttestationSubjectContext:
            raise TypeError("execution subject context must be exact typed metadata")
        object.__setattr__(
            self,
            "authority_projection_record_hashes",
            _projection_hashes(
                self.authority_projection_record_hashes,
                genesis_record_hash=genesis,
                authority_head=validated.authority_head,
                context="execution provider projection",
            ),
        )


@dataclass(frozen=True, slots=True)
class VerifiedImmutableArtifactLease:
    """Externally verified lease plus its closed subject and authority prefix."""

    evidence: ImmutableArtifactLease
    authority_genesis_record_hash: str
    subject_context: LeaseSubjectContext
    authority_projection_record_hashes: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.evidence) is not ImmutableArtifactLease:
            raise TypeError("lease evidence must be the exact contract type")
        validated = ImmutableArtifactLease.model_validate(
            self.evidence.model_dump(mode="python"),
            strict=True,
        )
        object.__setattr__(self, "evidence", validated)
        genesis = _sha256(
            self.authority_genesis_record_hash,
            "lease provider genesis",
        )
        if type(self.subject_context) is not LeaseSubjectContext:
            raise TypeError("lease subject context must be exact typed metadata")
        object.__setattr__(
            self,
            "authority_projection_record_hashes",
            _projection_hashes(
                self.authority_projection_record_hashes,
                genesis_record_hash=genesis,
                authority_head=validated.authority_head,
                context="lease provider projection",
            ),
        )


@runtime_checkable
class ObservedPitDatasetHandleProvider(Protocol):
    def verify_observed_pit_dataset_handle(
        self,
        authority_id: str,
        handle_id: str,
        content_hash: str,
        authority_head: str,
        /,
    ) -> ObservedPitDatasetHandle | None: ...


@runtime_checkable
class CodeManifestEvidenceProvider(Protocol):
    def verify_code_manifest_evidence(
        self,
        evidence_hash: str,
        authority_id: str,
        authority_head: str,
        /,
    ) -> CodeManifestEvidence | None: ...


@runtime_checkable
class ExecutionCodeAttestationProvider(Protocol):
    def verify_execution_code_attestation(
        self,
        attestation_hash: str,
        authority_id: str,
        authority_head: str,
        /,
    ) -> VerifiedExecutionCodeAttestation | None: ...


@runtime_checkable
class ImmutableArtifactLeaseProvider(Protocol):
    def verify_immutable_artifact_lease(
        self,
        lease_id: str,
        lease_hash: str,
        authority_id: str,
        authority_head: str,
        /,
    ) -> VerifiedImmutableArtifactLease | None: ...


@runtime_checkable
class C9ArtifactGraphProvider(Protocol):
    """Read-only exact-byte resolver for the two content-addressed C9 roots."""

    def read_content_addressed_bytes(
        self,
        path: str,
        expected_sha256: str,
        /,
    ) -> bytes | None: ...

    def read_publication_projection_ref_bytes(
        self,
        publication_receipt_id: str,
        receipt_record_hash: str,
        /,
    ) -> bytes | None: ...

    def read_data_snooping_evidence_ref_bytes(
        self,
        publication_receipt_id: str,
        receipt_record_hash: str,
        /,
    ) -> bytes | None: ...


@runtime_checkable
class C9PublicationTrustRootProvider(Protocol):
    """Deployment-owned A9 trust roots deliberately excluded from the manifest."""

    def verify_external_provider_authority_genesis(
        self,
        provider_contract_type: str,
        authority_id: str,
        genesis_record_hash: str,
        /,
    ) -> bool: ...

    def verify_trusted_scanner(
        self,
        public_artifact_scan_policy_id: str,
        dlp_policy_id: str,
        dlp_policy_version: str,
        dlp_policy_hash: str,
        scanner_implementation: str,
        scanner_version: str,
        /,
    ) -> bool: ...

    def expected_family_schema_v3_manifest_hash(self, /) -> str | None: ...

    def expected_activation_code_manifest_evidence_hash(self, /) -> str | None: ...


@dataclass(frozen=True, slots=True)
class C6C9ExternalProviderBundle:
    """Exact provider set supplied to the uniform three-argument verifier ABI."""

    observed_pit: ObservedPitDatasetHandleProvider
    code_manifest: CodeManifestEvidenceProvider
    execution_attestation: ExecutionCodeAttestationProvider
    immutable_artifact_lease: ImmutableArtifactLeaseProvider
    artifact_graph: C9ArtifactGraphProvider | None = None
    publication_trust: C9PublicationTrustRootProvider | None = None

    def __post_init__(self) -> None:
        required = (
            (
                self.observed_pit,
                ObservedPitDatasetHandleProvider,
                "observed-PIT provider",
            ),
            (
                self.code_manifest,
                CodeManifestEvidenceProvider,
                "code-manifest provider",
            ),
            (
                self.execution_attestation,
                ExecutionCodeAttestationProvider,
                "execution-attestation provider",
            ),
            (
                self.immutable_artifact_lease,
                ImmutableArtifactLeaseProvider,
                "immutable-lease provider",
            ),
        )
        for provider, protocol, label in required:
            if not isinstance(provider, protocol):
                raise TypeError(f"{label} does not implement its typed protocol")
        if (self.artifact_graph is None) != (self.publication_trust is None):
            raise ValueError("C9 artifact graph and publication trust providers must be supplied together")
        if self.artifact_graph is not None and not isinstance(
            self.artifact_graph,
            C9ArtifactGraphProvider,
        ):
            raise TypeError("artifact graph provider does not implement its typed protocol")
        if self.publication_trust is not None and not isinstance(
            self.publication_trust,
            C9PublicationTrustRootProvider,
        ):
            raise TypeError("publication trust provider does not implement its typed protocol")


__all__ = [
    "C6C9ExternalProviderBundle",
    "C9ArtifactGraphProvider",
    "C9PublicationTrustRootProvider",
    "CodeManifestEvidenceProvider",
    "ExecutionAttestationSubjectContext",
    "ExecutionCodeAttestationProvider",
    "ImmutableArtifactLeaseProvider",
    "LeaseSubjectContext",
    "ObservedPitDatasetHandleProvider",
    "VerifiedExecutionCodeAttestation",
    "VerifiedImmutableArtifactLease",
]
