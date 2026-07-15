"""Canonical strong-identity contracts for governance operations."""
from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class GovernanceRole(str, Enum):
    PROMOTION_READER = "PROMOTION_READER"
    PROMOTION_REQUESTER = "PROMOTION_REQUESTER"
    PROMOTION_REVIEWER = "PROMOTION_REVIEWER"
    PROMOTION_APPROVER = "PROMOTION_APPROVER"
    PROMOTION_EXECUTOR = "PROMOTION_EXECUTOR"


class StrongIdentityAssurance(str, Enum):
    OIDC_MTLS_MFA = "OIDC_MTLS_MFA"


class VerifiedIdentity(BaseModel):
    """Identity produced only after external-token and TLS-binding verification."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    issuer: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    roles: tuple[GovernanceRole, ...] = Field(min_length=1)
    assurance: StrongIdentityAssurance
    acr: str = Field(min_length=1)
    amr: tuple[str, ...] = Field(min_length=1)
    issued_at: int = Field(ge=0)
    not_before: int = Field(ge=0)
    authentication_time: int = Field(ge=0)
    expires_at: int = Field(ge=0)
    token_id: str = Field(min_length=1)
    certificate_sha256: str

    @field_validator("certificate_sha256", mode="before")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if _SHA256.fullmatch(normalized) is None:
            raise ValueError("certificate_sha256 must be a SHA-256 digest")
        return normalized

    @field_validator("roles")
    @classmethod
    def _validate_roles(
        cls, value: tuple[GovernanceRole, ...]
    ) -> tuple[GovernanceRole, ...]:
        if len(set(value)) != len(value):
            raise ValueError("identity roles must be unique")
        return value

    @field_validator("amr")
    @classmethod
    def _validate_amr(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value) or len(set(value)) != len(value):
            raise ValueError("amr values must be non-empty and unique")
        return value

    @model_validator(mode="after")
    def _validate_times(self) -> "VerifiedIdentity":
        if self.expires_at <= self.issued_at:
            raise ValueError("identity token must expire after it is issued")
        if self.not_before > self.expires_at:
            raise ValueError("identity token cannot become valid after expiry")
        if self.authentication_time > self.issued_at:
            raise ValueError("authentication_time cannot follow issued_at")
        return self

    @property
    def actor_id(self) -> str:
        return f"{self.issuer}|{self.subject}"

    def has_role(self, role: GovernanceRole) -> bool:
        return role in self.roles


__all__ = [
    "GovernanceRole",
    "StrongIdentityAssurance",
    "VerifiedIdentity",
]
