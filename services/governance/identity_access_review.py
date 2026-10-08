"""Deterministic, fail-closed access-review attestations for governance grants.

The attestation is created only from an already verified OIDC+mTLS+MFA
identity.  It binds one independent reviewer to the exact subject, principal
class, roles, review time, and expiry consumed by application authorization.
It performs no network access and grants no execution, deployment, credential,
order-submission, or live-trading authority.
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from quantos.contracts.hashing import stable_hash
from quantos.contracts.identity import (
    GovernanceRole,
    StrongIdentityAssurance,
    VerifiedIdentity,
)


MAX_ACCESS_REVIEW_VALIDITY_MS = 90 * 24 * 60 * 60 * 1_000
_REVIEW_ID_PREFIX = "iar-"


class IdentityAccessReviewError(PermissionError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _canonical_actor_id(value: str) -> str:
    if value != value.strip() or value.count("|") != 1:
        raise ValueError("actor id must be canonical issuer|subject")
    issuer, subject = value.split("|", 1)
    if not issuer or not subject or issuer != issuer.strip() or subject != subject.strip():
        raise ValueError("actor id must be canonical issuer|subject")
    return value


def _roles_before(value: object) -> tuple[GovernanceRole, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("reviewed roles must be a list or tuple")
    roles: list[GovernanceRole] = []
    for item in value:
        if isinstance(item, GovernanceRole):
            roles.append(item)
        elif type(item) is str:
            try:
                roles.append(GovernanceRole(item))
            except ValueError as exc:
                raise ValueError("reviewed roles contain an unknown governance role") from exc
        else:
            raise ValueError("reviewed roles must contain governance role strings")
    return tuple(roles)


def _content_payload(
    *,
    subject_actor_id: str,
    subject_principal_kind: Literal["HUMAN", "WORKLOAD"],
    subject_roles: tuple[GovernanceRole, ...],
    reviewer_actor_id: str,
    reviewer_assurance: StrongIdentityAssurance,
    reviewer_token_id: str,
    reviewer_certificate_sha256: str,
    reviewed_at_ms: int,
    expires_at_ms: int,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "outcome": "APPROVED",
        "subject_actor_id": subject_actor_id,
        "subject_principal_kind": subject_principal_kind,
        "subject_roles": [role.value for role in subject_roles],
        "reviewer_actor_id": reviewer_actor_id,
        "reviewer_assurance": reviewer_assurance.value,
        "reviewer_token_id": reviewer_token_id,
        "reviewer_certificate_sha256": reviewer_certificate_sha256,
        "reviewed_at_ms": reviewed_at_ms,
        "expires_at_ms": expires_at_ms,
    }


class IdentityAccessReviewAttestation(BaseModel):
    """Content-addressed approval of one exact governance-access grant."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    outcome: Literal["APPROVED"] = "APPROVED"
    review_id: StrictStr = Field(pattern=r"^iar-[0-9a-f]{32}$")
    subject_actor_id: StrictStr = Field(min_length=3)
    subject_principal_kind: Literal["HUMAN", "WORKLOAD"]
    subject_roles: tuple[GovernanceRole, ...] = Field(min_length=1)
    reviewer_actor_id: StrictStr = Field(min_length=3)
    reviewer_assurance: StrongIdentityAssurance
    reviewer_token_id: StrictStr = Field(min_length=1)
    reviewer_certificate_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_at_ms: int = Field(ge=0)
    expires_at_ms: int = Field(ge=0)
    content_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("subject_roles", mode="before")
    @classmethod
    def _validate_role_input(cls, value: object) -> tuple[GovernanceRole, ...]:
        return _roles_before(value)

    @field_validator("reviewer_assurance", mode="before")
    @classmethod
    def _validate_assurance_input(cls, value: object) -> StrongIdentityAssurance:
        if isinstance(value, StrongIdentityAssurance):
            return value
        if type(value) is str:
            try:
                return StrongIdentityAssurance(value)
            except ValueError as exc:
                raise ValueError("reviewer assurance is unknown") from exc
        raise ValueError("reviewer assurance must be a strong-identity assurance string")

    @field_validator("subject_roles")
    @classmethod
    def _validate_unique_roles(
        cls,
        value: tuple[GovernanceRole, ...],
    ) -> tuple[GovernanceRole, ...]:
        if len(set(value)) != len(value):
            raise ValueError("reviewed roles must be unique")
        return value

    @field_validator("subject_actor_id", "reviewer_actor_id")
    @classmethod
    def _validate_actor_id(cls, value: str) -> str:
        return _canonical_actor_id(value)

    @model_validator(mode="after")
    def _validate_attestation(self) -> "IdentityAccessReviewAttestation":
        if self.subject_actor_id == self.reviewer_actor_id:
            raise ValueError("access review cannot be self-approved")
        if self.expires_at_ms <= self.reviewed_at_ms:
            raise ValueError("access review expiry must follow review time")
        if self.expires_at_ms - self.reviewed_at_ms > MAX_ACCESS_REVIEW_VALIDITY_MS:
            raise ValueError("access review validity exceeds policy")
        expected = stable_hash(
            _content_payload(
                subject_actor_id=self.subject_actor_id,
                subject_principal_kind=self.subject_principal_kind,
                subject_roles=self.subject_roles,
                reviewer_actor_id=self.reviewer_actor_id,
                reviewer_assurance=self.reviewer_assurance,
                reviewer_token_id=self.reviewer_token_id,
                reviewer_certificate_sha256=self.reviewer_certificate_sha256,
                reviewed_at_ms=self.reviewed_at_ms,
                expires_at_ms=self.expires_at_ms,
            )
        )
        if self.content_sha256 != expected:
            raise ValueError("access review content hash does not match its payload")
        if self.review_id != _REVIEW_ID_PREFIX + expected[:32]:
            raise ValueError("access review id does not match its content hash")
        return self


def create_identity_access_review(
    *,
    subject_issuer: str,
    subject: str,
    principal_kind: Literal["HUMAN", "WORKLOAD"],
    roles: tuple[GovernanceRole, ...],
    reviewer: VerifiedIdentity,
    reviewed_at_ms: int,
    expires_at_ms: int,
) -> IdentityAccessReviewAttestation:
    """Create a review from a current independent strong reviewer identity."""

    if not isinstance(reviewer, VerifiedIdentity):
        raise TypeError("reviewer must be a VerifiedIdentity")
    if type(reviewed_at_ms) is not int or reviewed_at_ms < 0:
        raise ValueError("reviewed_at_ms must be a non-negative integer")
    if type(expires_at_ms) is not int or expires_at_ms < 0:
        raise ValueError("expires_at_ms must be a non-negative integer")
    try:
        subject_actor_id = _canonical_actor_id(f"{subject_issuer}|{subject}")
        reviewed_roles = _roles_before(roles)
    except ValueError as exc:
        raise IdentityAccessReviewError("ACCESS_REVIEW_SUBJECT_INVALID", str(exc)) from exc
    if not reviewed_roles or len(set(reviewed_roles)) != len(reviewed_roles):
        raise IdentityAccessReviewError(
            "ACCESS_REVIEW_ROLES_INVALID",
            "reviewed roles must be non-empty and unique",
        )
    if reviewer.actor_id == subject_actor_id:
        raise IdentityAccessReviewError(
            "ACCESS_REVIEW_SELF_REVIEW_FORBIDDEN",
            "a principal cannot review its own access",
        )
    if reviewer.assurance != StrongIdentityAssurance.OIDC_MTLS_MFA:
        raise IdentityAccessReviewError(
            "ACCESS_REVIEWER_STRONG_IDENTITY_REQUIRED",
            "access reviewer must have OIDC+mTLS+MFA assurance",
        )
    if not reviewer.has_role(GovernanceRole.PROMOTION_REVIEWER):
        raise IdentityAccessReviewError(
            "ACCESS_REVIEWER_ROLE_REQUIRED",
            "access reviewer must hold the reviewer role",
        )
    if reviewer.issued_at * 1_000 > reviewed_at_ms:
        raise IdentityAccessReviewError(
            "ACCESS_REVIEWER_TOKEN_ISSUED_IN_FUTURE",
            "reviewer token was issued after the review time",
        )
    if reviewer.not_before * 1_000 > reviewed_at_ms:
        raise IdentityAccessReviewError(
            "ACCESS_REVIEWER_TOKEN_NOT_YET_VALID",
            "reviewer token is not valid at the review time",
        )
    if reviewer.expires_at * 1_000 <= reviewed_at_ms:
        raise IdentityAccessReviewError(
            "ACCESS_REVIEWER_TOKEN_EXPIRED",
            "reviewer token is expired at the review time",
        )
    if expires_at_ms <= reviewed_at_ms:
        raise IdentityAccessReviewError(
            "ACCESS_REVIEW_EXPIRY_INVALID",
            "access review expiry must follow the review time",
        )
    if expires_at_ms - reviewed_at_ms > MAX_ACCESS_REVIEW_VALIDITY_MS:
        raise IdentityAccessReviewError(
            "ACCESS_REVIEW_VALIDITY_EXCEEDED",
            "access review validity exceeds the 90-day policy",
        )

    payload = _content_payload(
        subject_actor_id=subject_actor_id,
        subject_principal_kind=principal_kind,
        subject_roles=reviewed_roles,
        reviewer_actor_id=reviewer.actor_id,
        reviewer_assurance=reviewer.assurance,
        reviewer_token_id=reviewer.token_id,
        reviewer_certificate_sha256=reviewer.certificate_sha256,
        reviewed_at_ms=reviewed_at_ms,
        expires_at_ms=expires_at_ms,
    )
    digest = stable_hash(payload)
    return IdentityAccessReviewAttestation(
        review_id=_REVIEW_ID_PREFIX + digest[:32],
        subject_actor_id=subject_actor_id,
        subject_principal_kind=principal_kind,
        subject_roles=reviewed_roles,
        reviewer_actor_id=reviewer.actor_id,
        reviewer_assurance=reviewer.assurance,
        reviewer_token_id=reviewer.token_id,
        reviewer_certificate_sha256=reviewer.certificate_sha256,
        reviewed_at_ms=reviewed_at_ms,
        expires_at_ms=expires_at_ms,
        content_sha256=digest,
    )


__all__ = [
    "MAX_ACCESS_REVIEW_VALIDITY_MS",
    "IdentityAccessReviewAttestation",
    "IdentityAccessReviewError",
    "create_identity_access_review",
]
