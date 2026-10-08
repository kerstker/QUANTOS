"""Fail-closed local authorization for externally verified identities.

This module is a deterministic policy core. It does not authenticate tokens,
load runtime configuration, persist registry state, or grant execution,
deployment, credential, order-submission, or live-trading authority.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from types import MappingProxyType
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_serializer,
    field_validator,
    model_validator,
)

from quantos.contracts.identity import GovernanceRole, VerifiedIdentity

from .identity_access_review import IdentityAccessReviewAttestation


_HUMAN_ONLY_ROLES = frozenset(
    {
        GovernanceRole.PROMOTION_REQUESTER,
        GovernanceRole.PROMOTION_REVIEWER,
        GovernanceRole.PROMOTION_APPROVER,
        GovernanceRole.PROMOTION_EXECUTOR,
    }
)


class _FrozenContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class IdentityAuthorizationRegistryError(ValueError):
    """A registry transition violates monotonic history."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


def _roles_before(value: object) -> tuple[GovernanceRole, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("roles must be a list or tuple")
    resolved: list[GovernanceRole] = []
    for item in value:
        if isinstance(item, GovernanceRole):
            resolved.append(item)
        elif type(item) is str:
            try:
                resolved.append(GovernanceRole(item))
            except ValueError as exc:
                raise ValueError("roles contain an unknown governance role") from exc
        else:
            raise ValueError("roles must contain governance role strings")
    return tuple(resolved)


class IdentityAuthorizationGrant(_FrozenContract):
    """Current application-owned grant for one exact OIDC principal."""

    issuer: StrictStr = Field(min_length=1)
    subject: StrictStr = Field(min_length=1)
    principal_kind: Literal["HUMAN", "WORKLOAD"]
    enabled: bool
    revoked: bool
    roles: tuple[GovernanceRole, ...] = Field(min_length=1)
    tokens_valid_after_ms: int = Field(ge=0)
    access_review_expires_at_ms: int = Field(ge=0)
    access_review: IdentityAccessReviewAttestation | None = None

    @field_validator("roles", mode="before")
    @classmethod
    def _validate_role_input(cls, value: object) -> tuple[GovernanceRole, ...]:
        return _roles_before(value)

    @field_validator("roles")
    @classmethod
    def _validate_unique_roles(
        cls,
        value: tuple[GovernanceRole, ...],
    ) -> tuple[GovernanceRole, ...]:
        if len(set(value)) != len(value):
            raise ValueError("roles must be unique")
        return value

    @model_validator(mode="after")
    def _validate_principal(self) -> IdentityAuthorizationGrant:
        for name, value in (("issuer", self.issuer), ("subject", self.subject)):
            if value != value.strip() or "|" in value:
                raise ValueError(f"{name} must be canonical and cannot contain the actor delimiter")
        if self.enabled and self.revoked:
            raise ValueError("a revoked principal cannot remain enabled")
        if self.principal_kind == "WORKLOAD" and set(self.roles) & _HUMAN_ONLY_ROLES:
            raise ValueError("a workload cannot be granted a human governance role")
        if self.enabled and not self.revoked and self.access_review is None:
            raise ValueError("an enabled principal requires an access review attestation")
        if self.access_review is not None:
            if self.access_review.subject_actor_id != self.actor_id:
                raise ValueError("access review subject does not match the grant")
            if self.access_review.subject_principal_kind != self.principal_kind:
                raise ValueError("access review principal kind does not match the grant")
            if self.access_review.subject_roles != self.roles:
                raise ValueError("access review reviewed roles do not match the grant")
            if self.access_review.expires_at_ms != self.access_review_expires_at_ms:
                raise ValueError("access review expiry does not match the grant")
        return self

    @property
    def actor_id(self) -> str:
        return f"{self.issuer}|{self.subject}"


class IdentityAuthorizationRegistry(_FrozenContract):
    """An exact, generationed snapshot of current governance grants."""

    schema_version: Literal[1] = 1
    readiness: Literal["L2_DRY_RUN_ONLY"] = "L2_DRY_RUN_ONLY"
    generation: int = Field(default=0, ge=0)
    principals: Mapping[StrictStr, IdentityAuthorizationGrant] = Field(default_factory=lambda: MappingProxyType({}))

    @field_validator("principals")
    @classmethod
    def _freeze_principals(
        cls,
        value: Mapping[StrictStr, IdentityAuthorizationGrant],
    ) -> Mapping[StrictStr, IdentityAuthorizationGrant]:
        return MappingProxyType(dict(value))

    @field_serializer("principals")
    def _serialize_principals(
        self,
        value: Mapping[StrictStr, IdentityAuthorizationGrant],
    ) -> dict[str, IdentityAuthorizationGrant]:
        return dict(value)

    @model_validator(mode="after")
    def _validate_actor_keys(self) -> IdentityAuthorizationRegistry:
        for actor_id, grant in self.principals.items():
            if actor_id != actor_id.strip() or actor_id != grant.actor_id:
                raise ValueError("principal keys must exactly equal the canonical issuer|subject")
        return self


class IdentityAuthorizationDecision(_FrozenContract):
    """Auditable result for one governance role check only."""

    schema_version: Literal[1] = 1
    verdict: Literal["ALLOW", "DENY"]
    reason_code: StrictStr = Field(min_length=1)
    registry_generation: int = Field(ge=0)
    actor_id: StrictStr = Field(min_length=1)
    requested_role: GovernanceRole
    token_id: StrictStr = Field(min_length=1)
    certificate_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    token_roles: tuple[GovernanceRole, ...]
    granted_roles: tuple[GovernanceRole, ...] = ()
    principal_kind: Literal["HUMAN", "WORKLOAD"] | None = None
    principal_enabled: bool | None = None
    principal_revoked: bool | None = None
    token_issued_at_ms: int = Field(ge=0)
    token_not_before_ms: int = Field(ge=0)
    token_expires_at_ms: int = Field(ge=0)
    tokens_valid_after_ms: int | None = Field(default=None, ge=0)
    access_review_expires_at_ms: int | None = Field(default=None, ge=0)
    access_review_id: StrictStr | None = Field(default=None, pattern=r"^iar-[0-9a-f]{32}$")
    access_review_content_sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    access_reviewed_by_actor_id: StrictStr | None = None
    evaluated_at_ms: int = Field(ge=0)
    execution_authority_granted: Literal[False] = False
    deployment_authority_granted: Literal[False] = False
    live_trading_authority_granted: Literal[False] = False
    credential_authority_granted: Literal[False] = False
    order_submission_authority_granted: Literal[False] = False

    @model_validator(mode="after")
    def _validate_allow(self) -> IdentityAuthorizationDecision:
        if self.verdict != "ALLOW":
            return self
        if (
            self.reason_code != "GOVERNANCE_ROLE_AUTHORIZED"
            or self.principal_kind is None
            or self.principal_enabled is not True
            or self.principal_revoked is not False
            or self.requested_role not in self.token_roles
            or self.requested_role not in self.granted_roles
            or self.tokens_valid_after_ms is None
            or self.access_review_expires_at_ms is None
            or self.access_review_id is None
            or self.access_review_content_sha256 is None
            or self.access_reviewed_by_actor_id is None
            or self.token_issued_at_ms < self.tokens_valid_after_ms
            or self.evaluated_at_ms < self.token_issued_at_ms
            or self.evaluated_at_ms >= self.access_review_expires_at_ms
            or self.evaluated_at_ms < self.token_not_before_ms
            or self.evaluated_at_ms >= self.token_expires_at_ms
            or (self.principal_kind == "WORKLOAD" and self.requested_role in _HUMAN_ONLY_ROLES)
        ):
            raise ValueError("an ALLOW decision must satisfy every authorization gate")
        return self


def empty_identity_authorization_registry() -> IdentityAuthorizationRegistry:
    """Return a fresh empty production-safe registry snapshot."""

    return IdentityAuthorizationRegistry()


def load_identity_authorization_registry(
    content: bytes | str,
) -> IdentityAuthorizationRegistry:
    """Parse a strict JSON registry while rejecting ambiguous duplicate keys."""

    if isinstance(content, bytes):
        text = content.decode("utf-8")
    elif isinstance(content, str):
        text = content
    else:
        raise TypeError("registry content must be bytes or text")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    raw = json.loads(
        text,
        object_pairs_hook=object_pairs,
        parse_constant=reject_constant,
    )
    return IdentityAuthorizationRegistry.model_validate(raw)


def validate_registry_successor(
    current: IdentityAuthorizationRegistry,
    candidate: IdentityAuthorizationRegistry,
) -> IdentityAuthorizationRegistry:
    """Accept an exact replay or the next generation; reject all other history."""

    if not isinstance(current, IdentityAuthorizationRegistry):
        raise TypeError("current must be an IdentityAuthorizationRegistry")
    if not isinstance(candidate, IdentityAuthorizationRegistry):
        raise TypeError("candidate must be an IdentityAuthorizationRegistry")
    if candidate == current:
        return current
    if candidate.generation < current.generation:
        raise IdentityAuthorizationRegistryError(
            "REGISTRY_GENERATION_ROLLBACK",
            "identity authorization registry generation rolled back",
        )
    if candidate.generation == current.generation:
        raise IdentityAuthorizationRegistryError(
            "REGISTRY_GENERATION_CONFLICT",
            "one registry generation cannot contain different grants",
        )
    if candidate.generation != current.generation + 1:
        raise IdentityAuthorizationRegistryError(
            "REGISTRY_GENERATION_GAP",
            "identity authorization registry generations must be contiguous",
        )
    return candidate


def evaluate_authorization(
    registry: IdentityAuthorizationRegistry,
    identity: VerifiedIdentity,
    requested_role: GovernanceRole,
    *,
    evaluated_at_ms: int,
) -> IdentityAuthorizationDecision:
    """Evaluate one verified identity against current application authorization."""

    if not isinstance(registry, IdentityAuthorizationRegistry):
        raise TypeError("registry must be an IdentityAuthorizationRegistry")
    if not isinstance(identity, VerifiedIdentity):
        raise TypeError("identity must be a VerifiedIdentity")
    if not isinstance(requested_role, GovernanceRole):
        raise TypeError("requested_role must be a GovernanceRole")
    if isinstance(evaluated_at_ms, bool) or not isinstance(evaluated_at_ms, int) or evaluated_at_ms < 0:
        raise ValueError("evaluated_at_ms must be a non-negative integer")

    actor_id = identity.actor_id
    grant = registry.principals.get(actor_id)
    common = {
        "registry_generation": registry.generation,
        "actor_id": actor_id,
        "requested_role": requested_role,
        "token_id": identity.token_id,
        "certificate_sha256": identity.certificate_sha256,
        "token_roles": identity.roles,
        "granted_roles": grant.roles if grant else (),
        "principal_kind": grant.principal_kind if grant else None,
        "principal_enabled": grant.enabled if grant else None,
        "principal_revoked": grant.revoked if grant else None,
        "token_issued_at_ms": identity.issued_at * 1_000,
        "token_not_before_ms": identity.not_before * 1_000,
        "token_expires_at_ms": identity.expires_at * 1_000,
        "tokens_valid_after_ms": grant.tokens_valid_after_ms if grant else None,
        "access_review_expires_at_ms": (grant.access_review_expires_at_ms if grant else None),
        "access_review_id": grant.access_review.review_id if grant and grant.access_review else None,
        "access_review_content_sha256": (
            grant.access_review.content_sha256 if grant and grant.access_review else None
        ),
        "access_reviewed_by_actor_id": (
            grant.access_review.reviewer_actor_id if grant and grant.access_review else None
        ),
        "evaluated_at_ms": evaluated_at_ms,
    }

    def decide(
        verdict: Literal["ALLOW", "DENY"],
        reason_code: str,
    ) -> IdentityAuthorizationDecision:
        return IdentityAuthorizationDecision(
            verdict=verdict,
            reason_code=reason_code,
            **common,
        )

    if grant is None:
        return decide("DENY", "PRINCIPAL_NOT_REGISTERED")
    if grant.revoked:
        return decide("DENY", "PRINCIPAL_REVOKED")
    if not grant.enabled:
        return decide("DENY", "PRINCIPAL_DISABLED")
    if grant.principal_kind == "WORKLOAD" and requested_role in _HUMAN_ONLY_ROLES:
        return decide("DENY", "WORKLOAD_HUMAN_ACTION_FORBIDDEN")
    if evaluated_at_ms >= grant.access_review_expires_at_ms:
        return decide("DENY", "ACCESS_REVIEW_EXPIRED")
    if evaluated_at_ms < identity.issued_at * 1_000:
        return decide("DENY", "TOKEN_ISSUED_IN_FUTURE")
    if evaluated_at_ms < identity.not_before * 1_000:
        return decide("DENY", "TOKEN_NOT_YET_VALID")
    if evaluated_at_ms >= identity.expires_at * 1_000:
        return decide("DENY", "TOKEN_EXPIRED")
    if identity.issued_at * 1_000 < grant.tokens_valid_after_ms:
        return decide("DENY", "TOKEN_ISSUED_BEFORE_AUTHORIZATION")
    if requested_role not in grant.roles:
        return decide("DENY", "ROLE_NOT_GRANTED")
    if requested_role not in identity.roles:
        return decide("DENY", "TOKEN_ROLE_REQUIRED")
    return decide("ALLOW", "GOVERNANCE_ROLE_AUTHORIZED")


__all__ = [
    "IdentityAuthorizationDecision",
    "IdentityAuthorizationGrant",
    "IdentityAuthorizationRegistry",
    "IdentityAuthorizationRegistryError",
    "empty_identity_authorization_registry",
    "evaluate_authorization",
    "load_identity_authorization_registry",
    "validate_registry_successor",
]
