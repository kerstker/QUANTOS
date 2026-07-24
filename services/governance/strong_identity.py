"""Fail-closed OIDC + mTLS identity verification for governance operations.

The verifier is deterministic: time and the TLS-verified client certificate are
caller supplied. It performs no network access and holds no private key.
"""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import secrets
from collections.abc import Mapping
from typing import Any

import jwt
from pydantic import BaseModel, ConfigDict, Field, field_validator

from quantos.contracts.identity import (
    GovernanceRole,
    StrongIdentityAssurance,
    VerifiedIdentity,
)

from .external_evidence import (
    ExternalEvidenceStatus,
    build_external_blocked_status,
)


_ASYMMETRIC_ALGORITHMS = frozenset({"EdDSA", "RS256"})


class StrongIdentityError(PermissionError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


class StrongIdentityUnavailable(StrongIdentityError):
    pass


class OidcTrustPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    issuer: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    public_key_pem: str = Field(min_length=1)
    allowed_algorithms: tuple[str, ...] = Field(min_length=1)
    required_acr_values: tuple[str, ...] = Field(min_length=1)
    required_amr_values: tuple[str, ...] = Field(min_length=1)
    maximum_authentication_age_seconds: int = Field(gt=0)
    maximum_token_lifetime_seconds: int = Field(gt=0)
    clock_skew_seconds: int = Field(ge=0, le=300)

    @field_validator(
        "allowed_algorithms", "required_acr_values", "required_amr_values"
    )
    @classmethod
    def _validate_unique_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() for item in value) or len(set(value)) != len(value):
            raise ValueError("policy lists must contain unique non-empty values")
        return value

    @field_validator("allowed_algorithms")
    @classmethod
    def _validate_algorithms(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        invalid = set(value) - _ASYMMETRIC_ALGORITHMS
        if invalid:
            raise ValueError(
                "only allowlisted asymmetric algorithms are permitted: "
                + ", ".join(sorted(invalid))
            )
        return value


def _as_timestamp(claims: Mapping[str, Any], name: str) -> int:
    value = claims.get(name)
    if type(value) is not int or value < 0:
        raise StrongIdentityError("INVALID_TIME_CLAIM", f"{name} must be a non-negative integer")
    return value


def _nonempty_string(claims: Mapping[str, Any], name: str) -> str:
    value = claims.get(name)
    if not isinstance(value, str) or not value.strip():
        raise StrongIdentityError("INVALID_STRING_CLAIM", f"{name} must be a non-empty string")
    return value.strip()


def _certificate_thumbprint(certificate_der: bytes) -> str:
    digest = hashlib.sha256(certificate_der).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OidcMtlsVerifier:
    def __init__(self, policy: OidcTrustPolicy) -> None:
        self.policy = policy

    def verify(
        self,
        token: str,
        *,
        client_certificate_der: bytes,
        now: int,
    ) -> VerifiedIdentity:
        if not isinstance(token, str) or not token.strip():
            raise StrongIdentityError("MISSING_TOKEN", "bearer token is required")
        if not isinstance(client_certificate_der, bytes) or not client_certificate_der:
            raise StrongIdentityError(
                "MISSING_CLIENT_CERTIFICATE", "a verified client certificate is required"
            )
        if type(now) is not int or now < 0:
            raise StrongIdentityError("INVALID_VERIFICATION_TIME", "verification time must be non-negative")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise StrongIdentityError("MALFORMED_TOKEN", "token header is malformed") from exc
        algorithm = header.get("alg")
        if algorithm not in self.policy.allowed_algorithms:
            raise StrongIdentityError("ALGORITHM_NOT_ALLOWED", "token algorithm is not allowed")
        if header.get("typ") != "at+jwt":
            raise StrongIdentityError("INVALID_TOKEN_TYPE", "token type must be at+jwt")

        try:
            claims = jwt.decode(
                token,
                self.policy.public_key_pem,
                algorithms=list(self.policy.allowed_algorithms),
                audience=self.policy.audience,
                issuer=self.policy.issuer,
                options={
                    "require": [
                        "iss",
                        "sub",
                        "aud",
                        "iat",
                        "nbf",
                        "exp",
                        "auth_time",
                        "jti",
                        "acr",
                        "amr",
                        "roles",
                        "cnf",
                    ],
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                },
            )
        except jwt.InvalidSignatureError as exc:
            raise StrongIdentityError("INVALID_SIGNATURE", "token signature verification failed") from exc
        except jwt.InvalidAlgorithmError as exc:
            raise StrongIdentityError("ALGORITHM_NOT_ALLOWED", "token algorithm is not allowed") from exc
        except jwt.InvalidIssuerError as exc:
            raise StrongIdentityError("INVALID_ISSUER", "token issuer is not trusted") from exc
        except jwt.InvalidAudienceError as exc:
            raise StrongIdentityError("INVALID_AUDIENCE", "token audience is not accepted") from exc
        except jwt.PyJWTError as exc:
            raise StrongIdentityError("INVALID_TOKEN", "token claims are invalid") from exc
        if not isinstance(claims, dict):
            raise StrongIdentityError("INVALID_TOKEN", "token claims must be an object")

        issued_at = _as_timestamp(claims, "iat")
        not_before = _as_timestamp(claims, "nbf")
        expires_at = _as_timestamp(claims, "exp")
        authentication_time = _as_timestamp(claims, "auth_time")
        skew = self.policy.clock_skew_seconds
        if issued_at > now + skew:
            raise StrongIdentityError("TOKEN_FROM_FUTURE", "token issued_at is in the future")
        if not_before > now + skew:
            raise StrongIdentityError("TOKEN_NOT_YET_VALID", "token is not yet valid")
        if expires_at <= now - skew:
            raise StrongIdentityError("TOKEN_EXPIRED", "token has expired")
        if authentication_time > issued_at or authentication_time > now + skew:
            raise StrongIdentityError("INVALID_AUTH_TIME", "authentication time is invalid")
        if now - authentication_time > self.policy.maximum_authentication_age_seconds + skew:
            raise StrongIdentityError("STALE_AUTHENTICATION", "authentication is too old")
        if expires_at - issued_at > self.policy.maximum_token_lifetime_seconds:
            raise StrongIdentityError("TOKEN_LIFETIME_EXCEEDED", "token lifetime exceeds policy")

        subject = _nonempty_string(claims, "sub")
        token_id = _nonempty_string(claims, "jti")
        acr = _nonempty_string(claims, "acr")
        if acr not in self.policy.required_acr_values:
            raise StrongIdentityError("ACR_NOT_ALLOWED", "token assurance level is insufficient")
        amr_claim = claims.get("amr")
        if (
            not isinstance(amr_claim, list)
            or not amr_claim
            or any(not isinstance(value, str) or not value for value in amr_claim)
        ):
            raise StrongIdentityError("INVALID_AMR", "token amr claim must be a non-empty list")
        amr = tuple(amr_claim)
        if not set(self.policy.required_amr_values) <= set(amr):
            raise StrongIdentityError("AMR_REQUIRED", "token is missing a required authentication method")

        role_claim = claims.get("roles")
        if not isinstance(role_claim, list) or not role_claim:
            raise StrongIdentityError("INVALID_ROLES", "token roles claim must be a non-empty list")
        try:
            roles = tuple(GovernanceRole(value) for value in role_claim)
        except (TypeError, ValueError) as exc:
            raise StrongIdentityError("INVALID_ROLES", "token contains an unknown governance role") from exc
        if len(set(roles)) != len(roles):
            raise StrongIdentityError("INVALID_ROLES", "token roles must be unique")

        confirmation = claims.get("cnf")
        if not isinstance(confirmation, dict):
            raise StrongIdentityError("MISSING_TOKEN_BINDING", "token cnf claim is required")
        bound_thumbprint = confirmation.get("x5t#S256")
        actual_thumbprint = _certificate_thumbprint(client_certificate_der)
        if (
            not isinstance(bound_thumbprint, str)
            or not secrets.compare_digest(bound_thumbprint, actual_thumbprint)
        ):
            raise StrongIdentityError(
                "CERTIFICATE_BINDING_MISMATCH", "token is not bound to the verified client certificate"
            )

        return VerifiedIdentity(
            issuer=self.policy.issuer,
            subject=subject,
            audience=self.policy.audience,
            roles=roles,
            assurance=StrongIdentityAssurance.OIDC_MTLS_MFA,
            acr=acr,
            amr=amr,
            issued_at=issued_at,
            not_before=not_before,
            authentication_time=authentication_time,
            expires_at=expires_at,
            token_id=token_id,
            certificate_sha256=hashlib.sha256(client_certificate_der).hexdigest(),
        )


def extract_verified_client_certificate(scope: Mapping[str, Any]) -> bytes:
    """Read only a TLS-server-authenticated certificate from the ASGI scope."""

    extensions = scope.get("extensions")
    tls = extensions.get("tls") if isinstance(extensions, dict) else None
    chain = tls.get("client_cert_chain") if isinstance(tls, dict) else None
    if (
        not isinstance(tls, dict)
        or tls.get("client_cert_chain_verified") is not True
        or not isinstance(chain, (list, tuple))
        or not chain
        or not isinstance(chain[0], bytes)
        or not chain[0]
    ):
        raise StrongIdentityError(
            "MISSING_VERIFIED_CLIENT_CERTIFICATE",
            "a verified client certificate is required in the trusted TLS scope",
        )
    return chain[0]


def require_role(identity: VerifiedIdentity, role: GovernanceRole) -> None:
    if not isinstance(identity, VerifiedIdentity) or not identity.has_role(role):
        raise StrongIdentityError("ROLE_REQUIRED", f"required governance role is missing: {role.value}")


def load_oidc_policy_from_environment(
    environ: Mapping[str, str] | None = None,
) -> OidcTrustPolicy:
    values = environ if environ is not None else os.environ
    issuer = values.get("QUANTOS_OIDC_ISSUER", "").strip()
    audience = values.get("QUANTOS_OIDC_AUDIENCE", "").strip()
    public_key_file = values.get("QUANTOS_OIDC_PUBLIC_KEY_FILE", "").strip()
    if not issuer or not audience or not public_key_file:
        raise StrongIdentityUnavailable(
            "IDENTITY_POLICY_UNAVAILABLE",
            "external OIDC identity policy is not configured",
        )
    try:
        public_key_pem = Path(public_key_file).read_text(encoding="ascii")
    except OSError as exc:
        raise StrongIdentityUnavailable(
            "IDENTITY_POLICY_UNAVAILABLE",
            "external OIDC public key is unavailable",
        ) from exc

    def _csv(name: str, default: str) -> tuple[str, ...]:
        return tuple(value.strip() for value in values.get(name, default).split(",") if value.strip())

    try:
        return OidcTrustPolicy(
            issuer=issuer,
            audience=audience,
            public_key_pem=public_key_pem,
            allowed_algorithms=_csv("QUANTOS_OIDC_ALGORITHMS", "EdDSA"),
            required_acr_values=_csv("QUANTOS_OIDC_REQUIRED_ACR", "urn:quantos:aal2"),
            required_amr_values=_csv("QUANTOS_OIDC_REQUIRED_AMR", "mfa"),
            maximum_authentication_age_seconds=int(
                values.get("QUANTOS_OIDC_MAX_AUTH_AGE_SECONDS", "300")
            ),
            maximum_token_lifetime_seconds=int(
                values.get("QUANTOS_OIDC_MAX_TOKEN_LIFETIME_SECONDS", "600")
            ),
            clock_skew_seconds=int(values.get("QUANTOS_OIDC_CLOCK_SKEW_SECONDS", "30")),
        )
    except (TypeError, ValueError) as exc:
        raise StrongIdentityUnavailable(
            "IDENTITY_POLICY_UNAVAILABLE",
            "external OIDC identity policy is invalid",
        ) from exc


def oidc_policy_configuration_status(
    environ: Mapping[str, str] | None = None,
    *,
    observed_at_ms: int,
) -> ExternalEvidenceStatus:
    """Report identity configuration without claiming IdP deployment evidence.

    A valid local policy is only ``PARTIAL`` until an operator supplies proof
    of the external issuer, trusted mTLS termination, MFA policy, key custody,
    and revocation lifecycle.  Missing or invalid configuration is an explicit
    ``EXTERNAL_BLOCKED`` status consumed by release/governance tooling.
    """

    try:
        policy = load_oidc_policy_from_environment(environ)
    except StrongIdentityUnavailable as exc:
        return build_external_blocked_status(
            capability="oidc_identity",
            reason_code=exc.code,
            required_action=(
                "Provision external OIDC issuer, trusted mTLS termination, MFA, "
                "and key lifecycle evidence"
            ),
            observed_at_ms=observed_at_ms,
        )
    policy_digest = hashlib.sha256(policy.public_key_pem.encode("ascii")).hexdigest()
    return ExternalEvidenceStatus(
        capability="oidc_identity",
        status="PARTIAL",
        reason_code="OIDC_POLICY_CONFIGURED_EXTERNAL_DEPLOYMENT_UNVERIFIED",
        required_action=(
            "Retain operator-owned OIDC/mTLS/MFA deployment, revocation, and key-custody evidence"
        ),
        observed_at_ms=observed_at_ms,
        external_authority=policy.issuer,
        evidence_ref=f"local-policy-sha256:{policy_digest}",
    )


__all__ = [
    "OidcMtlsVerifier",
    "OidcTrustPolicy",
    "StrongIdentityError",
    "StrongIdentityUnavailable",
    "extract_verified_client_certificate",
    "load_oidc_policy_from_environment",
    "oidc_policy_configuration_status",
    "require_role",
]
