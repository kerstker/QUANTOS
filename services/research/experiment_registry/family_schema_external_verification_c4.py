"""Typed external-trust seams used by executable C4 named verifiers.

The family authority does not recreate grant or identity authority locally.
It constructs an exact immutable request, invokes a deployment-supplied
provider, and compares the returned evidence and semantic claims fail-closed.
These protocols are deliberately read-only and contain no provider discovery,
network client, credential, or default implementation.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, fields
from typing import Protocol, runtime_checkable


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _text(value: object, field_name: str) -> str:
    if (
        type(value) is not str
        or not value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise TypeError(f"{field_name} must be non-empty exact NFC text")
    return value


def _sha256(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if _SHA256_RE.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be lowercase SHA-256 text")
    return text


def _positive_integer(value: object, field_name: str) -> int:
    if type(value) is not int or value <= 0:
        raise TypeError(f"{field_name} must be a positive exact INTEGER")
    return value


def _exact_bool(value: object, field_name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{field_name} must be an exact bool")
    return value


@dataclass(frozen=True, slots=True)
class MultiPartyGrantClaims:
    state: str
    verified_human_actor_count: int
    distinct_human_actors: bool
    complete_hash_chain: bool
    grant_not_reused: bool
    subject_action_bound: bool
    consumption_time_bound: bool
    policy_verified: bool
    authority_head_verified: bool

    def __post_init__(self) -> None:
        _text(self.state, "grant state")
        if type(self.verified_human_actor_count) is not int or self.verified_human_actor_count < 0:
            raise TypeError("verified_human_actor_count must be a non-negative exact INTEGER")
        for field in fields(self):
            if field.name not in ("state", "verified_human_actor_count"):
                _exact_bool(getattr(self, field.name), field.name)


@dataclass(frozen=True, slots=True)
class MultiPartyGrantVerificationRequest:
    grant_id: str
    grant_record_hash: str
    policy_id: str
    policy_version: str
    policy_hash: str
    authority_id: str
    authority_head: str
    action: str
    subject_record_type: str
    subject_id: str
    subject_record_hash: str
    consumed_by_actor_id: str
    consumed_at_us: int

    def __post_init__(self) -> None:
        for name in (
            "grant_id",
            "policy_id",
            "policy_version",
            "authority_id",
            "action",
            "subject_record_type",
            "subject_id",
            "consumed_by_actor_id",
        ):
            _text(getattr(self, name), name)
        for name in (
            "grant_record_hash",
            "policy_hash",
            "authority_head",
            "subject_record_hash",
        ):
            _sha256(getattr(self, name), name)
        _positive_integer(self.consumed_at_us, "consumed_at_us")


@dataclass(frozen=True, slots=True)
class MultiPartyGrantEvidence:
    grant_id: str
    grant_record_hash: str
    policy_id: str
    policy_version: str
    policy_hash: str
    authority_id: str
    authority_head: str
    action: str
    subject_record_type: str
    subject_id: str
    subject_record_hash: str
    consumed_by_actor_id: str
    consumed_at_us: int
    claims: MultiPartyGrantClaims

    def __post_init__(self) -> None:
        MultiPartyGrantVerificationRequest(
            grant_id=self.grant_id,
            grant_record_hash=self.grant_record_hash,
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            policy_hash=self.policy_hash,
            authority_id=self.authority_id,
            authority_head=self.authority_head,
            action=self.action,
            subject_record_type=self.subject_record_type,
            subject_id=self.subject_id,
            subject_record_hash=self.subject_record_hash,
            consumed_by_actor_id=self.consumed_by_actor_id,
            consumed_at_us=self.consumed_at_us,
        )
        if type(self.claims) is not MultiPartyGrantClaims:
            raise TypeError("grant evidence claims must be exact MultiPartyGrantClaims")


@runtime_checkable
class MultiPartyGrantEvidenceProvider(Protocol):
    def verify(
        self,
        request: MultiPartyGrantVerificationRequest,
        /,
    ) -> MultiPartyGrantEvidence | None: ...


@dataclass(frozen=True, slots=True)
class ActorProvenanceClaims:
    provenance_verified: bool
    actor_kind_verified: bool
    action_context_bound: bool
    subject_context_bound: bool
    event_time_bound: bool
    evidence_hash_verified: bool
    policy_verified: bool
    authority_head_verified: bool

    def __post_init__(self) -> None:
        for field in fields(self):
            _exact_bool(getattr(self, field.name), field.name)


@dataclass(frozen=True, slots=True)
class ActorProvenanceVerificationRequest:
    actor_id: str
    actor_kind: str
    evidence_hash: str
    policy_id: str
    policy_version: str
    policy_hash: str
    authority_id: str
    authority_head: str
    action: str
    subject_record_type: str
    subject_id: str
    subject_record_hash: str
    event_at_us: int

    def __post_init__(self) -> None:
        for name in (
            "actor_id",
            "actor_kind",
            "policy_id",
            "policy_version",
            "authority_id",
            "action",
            "subject_record_type",
            "subject_id",
        ):
            _text(getattr(self, name), name)
        for name in (
            "evidence_hash",
            "policy_hash",
            "authority_head",
            "subject_record_hash",
        ):
            _sha256(getattr(self, name), name)
        _positive_integer(self.event_at_us, "event_at_us")


@dataclass(frozen=True, slots=True)
class ActorProvenanceEvidence:
    actor_id: str
    actor_kind: str
    evidence_hash: str
    policy_id: str
    policy_version: str
    policy_hash: str
    authority_id: str
    authority_head: str
    action: str
    subject_record_type: str
    subject_id: str
    subject_record_hash: str
    event_at_us: int
    claims: ActorProvenanceClaims

    def __post_init__(self) -> None:
        ActorProvenanceVerificationRequest(
            actor_id=self.actor_id,
            actor_kind=self.actor_kind,
            evidence_hash=self.evidence_hash,
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            policy_hash=self.policy_hash,
            authority_id=self.authority_id,
            authority_head=self.authority_head,
            action=self.action,
            subject_record_type=self.subject_record_type,
            subject_id=self.subject_id,
            subject_record_hash=self.subject_record_hash,
            event_at_us=self.event_at_us,
        )
        if type(self.claims) is not ActorProvenanceClaims:
            raise TypeError("actor evidence claims must be exact ActorProvenanceClaims")


@runtime_checkable
class ActorProvenanceEvidenceProvider(Protocol):
    def verify(
        self,
        request: ActorProvenanceVerificationRequest,
        /,
    ) -> ActorProvenanceEvidence | None: ...


@dataclass(frozen=True, slots=True)
class C4ExternalProviderBundle:
    """Deployment-supplied external authorities required by C4 verification."""

    multi_party_grant_evidence: MultiPartyGrantEvidenceProvider | None
    actor_provenance_evidence: ActorProvenanceEvidenceProvider | None

    def __post_init__(self) -> None:
        if self.multi_party_grant_evidence is not None and not isinstance(
            self.multi_party_grant_evidence,
            MultiPartyGrantEvidenceProvider,
        ):
            raise TypeError("multi-party grant provider does not implement verify(request)")
        if self.actor_provenance_evidence is not None and not isinstance(
            self.actor_provenance_evidence,
            ActorProvenanceEvidenceProvider,
        ):
            raise TypeError("actor provenance provider does not implement verify(request)")


__all__ = [
    "ActorProvenanceClaims",
    "ActorProvenanceEvidence",
    "ActorProvenanceEvidenceProvider",
    "ActorProvenanceVerificationRequest",
    "C4ExternalProviderBundle",
    "MultiPartyGrantClaims",
    "MultiPartyGrantEvidence",
    "MultiPartyGrantEvidenceProvider",
    "MultiPartyGrantVerificationRequest",
]
