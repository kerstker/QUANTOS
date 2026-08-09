"""Fail-closed contract for an internal L2 strong-identity deployment.

This module validates secret-free deployment facts and compatibility with the
existing OIDC verifier policy.  It performs no network access, starts no
service, reads no credential, signs no token, and grants no runtime authority.
"""

from __future__ import annotations

import ipaddress
import json
from pathlib import PurePosixPath
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from quantos.contracts.hashing import stable_hash
from quantos.contracts.identity import GovernanceRole

from .external_evidence import ExternalEvidenceStatus
from .strong_identity import OidcTrustPolicy


_CONTAINER_IMAGE = re.compile(
    r"^[a-z0-9][a-z0-9._-]*(?::[0-9]+)?"
    r"(?:/[a-z0-9][a-z0-9._-]*)+@sha256:[0-9a-f]{64}$"
)
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ALLOWED_ALGORITHMS = frozenset({"EdDSA", "RS256"})
_ALL_GOVERNANCE_ROLES = tuple(GovernanceRole)


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


def _tuple_before(value: object, *, label: str) -> tuple[object, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a list or tuple")
    return tuple(value)


def _canonical_https_url(
    value: str,
    *,
    label: str,
    origin_only: bool = False,
) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a canonical HTTPS URL")
    if any(char.isspace() for char in value) or "*" in value or "\\" in value:
        raise ValueError(f"{label} must be a canonical HTTPS URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} must be a canonical HTTPS URL") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or not hostname
        or hostname != hostname.lower()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise ValueError(f"{label} must be a canonical HTTPS URL")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError(f"{label} must use an internal DNS name, not an IP literal")
    labels = hostname.split(".")
    if len(labels) < 2 or any(_DNS_LABEL.fullmatch(item) is None for item in labels):
        raise ValueError(f"{label} must use a canonical DNS name")
    if parsed.netloc != parsed.netloc.lower():
        raise ValueError(f"{label} authority must be lowercase")
    if origin_only and parsed.path not in {"", "/"}:
        raise ValueError(f"{label} must be an HTTPS origin")
    if "//" in parsed.path:
        raise ValueError(f"{label} path is not canonical")
    return value


def _validate_image_reference(value: str) -> str:
    if _CONTAINER_IMAGE.fullmatch(value) is None:
        raise ValueError("container images must be pinned by an immutable SHA-256 digest")
    return value


class OidcBrowserClientDeployment(_FrozenContract):
    client_id: StrictStr = Field(min_length=1, max_length=128)
    public_client: Literal[True]
    client_secret_present: Literal[False]
    authorization_code_enabled: Literal[True]
    pkce_method: Literal["S256"]
    implicit_grant_enabled: Literal[False]
    direct_grant_enabled: Literal[False]
    device_grant_enabled: Literal[False]
    ciba_grant_enabled: Literal[False]
    token_exchange_enabled: Literal[False]
    service_account_enabled: Literal[False]
    offline_access_enabled: Literal[False]
    certificate_bound_access_tokens: Literal[True]
    redirect_uris: tuple[StrictStr, ...] = Field(min_length=1)
    web_origins: tuple[StrictStr, ...] = Field(min_length=1)

    @field_validator("redirect_uris", "web_origins", mode="before")
    @classmethod
    def _tuple_urls(cls, value: object, info: Any) -> tuple[object, ...]:
        return _tuple_before(value, label=info.field_name)

    @field_validator("redirect_uris")
    @classmethod
    def _redirects(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("redirect URIs must be unique")
        return tuple(
            _canonical_https_url(item, label="redirect URI") for item in value
        )

    @field_validator("web_origins")
    @classmethod
    def _origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("web origins must be unique")
        return tuple(
            _canonical_https_url(item, label="web origin", origin_only=True)
            for item in value
        )

    @model_validator(mode="after")
    def _redirect_origins(self) -> "OidcBrowserClientDeployment":
        allowed_origins = {
            f"{urlsplit(origin).scheme}://{urlsplit(origin).netloc}"
            for origin in self.web_origins
        }
        for redirect in self.redirect_uris:
            parsed = urlsplit(redirect)
            if f"{parsed.scheme}://{parsed.netloc}" not in allowed_origins:
                raise ValueError("every redirect URI must use an exact configured web origin")
        return self


class L2OidcDeployment(_FrozenContract):
    provider: Literal["KEYCLOAK"]
    image_reference: StrictStr
    issuer: StrictStr
    audience: StrictStr = Field(min_length=1, max_length=255)
    network_scope: Literal["INTERNAL_ONLY"]
    public_exposure: Literal[False]
    direct_mtls: Literal[True]
    signing_algorithms: tuple[Literal["EdDSA", "RS256"], ...] = Field(min_length=1)
    access_token_lifetime_seconds: int = Field(gt=0, le=600)
    maximum_authentication_age_seconds: int = Field(gt=0, le=300)
    required_acr: Literal["urn:quantos:aal2"]
    required_amr: tuple[StrictStr, ...] = Field(min_length=2)
    roles_claim: Literal["roles"]
    browser_client: OidcBrowserClientDeployment

    @field_validator("image_reference")
    @classmethod
    def _image(cls, value: str) -> str:
        return _validate_image_reference(value)

    @field_validator("issuer")
    @classmethod
    def _issuer(cls, value: str) -> str:
        validated = _canonical_https_url(value, label="OIDC issuer")
        path = urlsplit(validated).path
        if not path.startswith("/realms/") or path.endswith("/"):
            raise ValueError("Keycloak issuer must identify one exact realm")
        return validated

    @field_validator("signing_algorithms", "required_amr", mode="before")
    @classmethod
    def _tuple_values(cls, value: object, info: Any) -> tuple[object, ...]:
        return _tuple_before(value, label=info.field_name)

    @field_validator("signing_algorithms")
    @classmethod
    def _algorithms(
        cls,
        value: tuple[Literal["EdDSA", "RS256"], ...],
    ) -> tuple[Literal["EdDSA", "RS256"], ...]:
        if len(set(value)) != len(value) or not set(value) <= _ALLOWED_ALGORITHMS:
            raise ValueError("OIDC signing algorithms must be unique and allowlisted")
        return value

    @field_validator("required_amr")
    @classmethod
    def _amr(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            len(set(value)) != len(value)
            or any(not item.strip() for item in value)
            or "mfa" not in value
        ):
            raise ValueError("AMR must be unique, non-empty, and include the MFA reference")
        return value


class L2MtlsIngressDeployment(_FrozenContract):
    provider: Literal["ENVOY"]
    image_reference: StrictStr
    require_client_certificate: Literal[True]
    certificate_validation: Literal["PRIVATE_CA_CHAIN_AND_EXPIRY"]
    allow_expired_certificate: Literal[False]
    forward_client_cert_mode: Literal["SANITIZE_SET"]
    forward_certificate_chain: Literal[True]
    trust_arbitrary_client_certificate_headers: Literal[False]
    upstream_transport: Literal["UNIX_DOMAIN_SOCKET"]
    upstream_address: StrictStr
    direct_application_tcp_listener: Literal[False]
    certificate_revocation_mode: Literal["SHORT_LIVED_PASSIVE", "CRL"]
    maximum_client_certificate_lifetime_seconds: int = Field(gt=0, le=86_400)

    @field_validator("image_reference")
    @classmethod
    def _image(cls, value: str) -> str:
        return _validate_image_reference(value)

    @field_validator("upstream_address")
    @classmethod
    def _socket_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value.startswith("/run/quantos/")
            or not value.endswith(".sock")
            or ".." in path.parts
        ):
            raise ValueError("promotion API upstream must be a restricted Unix socket")
        return value


class L2IdentityAuthorizationDeployment(_FrozenContract):
    identity_source: Literal["ISSUER_SUBJECT_ONLY"]
    application_registry_required: Literal[True]
    independent_access_review_required: Literal[True]
    workload_human_roles_forbidden: Literal[True]
    allowed_governance_roles: tuple[GovernanceRole, ...]

    @field_validator("allowed_governance_roles", mode="before")
    @classmethod
    def _roles_before(cls, value: object) -> tuple[GovernanceRole, ...]:
        items = _tuple_before(value, label="allowed_governance_roles")
        try:
            return tuple(
                item if isinstance(item, GovernanceRole) else GovernanceRole(item)
                for item in items
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("allowed governance roles contain an unknown role") from exc

    @field_validator("allowed_governance_roles")
    @classmethod
    def _roles(cls, value: tuple[GovernanceRole, ...]) -> tuple[GovernanceRole, ...]:
        if value != _ALL_GOVERNANCE_ROLES:
            raise ValueError("allowed governance roles must equal the canonical role set")
        return value


class NoRuntimeAuthorities(_FrozenContract):
    execution_authority_granted: Literal[False]
    deployment_authority_granted: Literal[False]
    live_trading_authority_granted: Literal[False]
    credential_authority_granted: Literal[False]
    order_submission_authority_granted: Literal[False]
    kill_switch_reset_authority_granted: Literal[False]


class L2IdentityDeploymentManifest(_FrozenContract):
    schema_version: Literal[1] = 1
    readiness: Literal["L2_DRY_RUN_ONLY"]
    topology: Literal["SINGLE_SITE_INTERNAL"]
    oidc: L2OidcDeployment
    mtls_ingress: L2MtlsIngressDeployment
    authorization: L2IdentityAuthorizationDeployment
    authorities: NoRuntimeAuthorities


class L2IdentityCompatibilityDecision(_FrozenContract):
    schema_version: Literal[1] = 1
    verdict: Literal["PASS", "BLOCK"]
    reason_codes: tuple[StrictStr, ...]
    execution_authority_granted: Literal[False] = False
    deployment_authority_granted: Literal[False] = False
    live_trading_authority_granted: Literal[False] = False
    credential_authority_granted: Literal[False] = False
    order_submission_authority_granted: Literal[False] = False

    @field_validator("reason_codes")
    @classmethod
    def _reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("compatibility reason codes must be unique")
        return value

    @model_validator(mode="after")
    def _verdict_reasons(self) -> "L2IdentityCompatibilityDecision":
        if self.verdict == "PASS" and self.reason_codes:
            raise ValueError("PASS compatibility cannot contain blockers")
        if self.verdict == "BLOCK" and not self.reason_codes:
            raise ValueError("BLOCK compatibility requires at least one blocker")
        return self


def load_l2_identity_deployment_manifest(
    content: bytes | str,
) -> L2IdentityDeploymentManifest:
    """Parse a strict manifest while rejecting ambiguous duplicate keys."""

    if isinstance(content, bytes):
        text = content.decode("utf-8")
    elif isinstance(content, str):
        text = content
    else:
        raise TypeError("L2 identity deployment manifest must be bytes or text")

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
    return L2IdentityDeploymentManifest.model_validate(raw)


def evaluate_l2_identity_compatibility(
    manifest: L2IdentityDeploymentManifest,
    policy: OidcTrustPolicy,
) -> L2IdentityCompatibilityDecision:
    """Bind one deployment contract to the existing runtime verifier policy."""

    if not isinstance(manifest, L2IdentityDeploymentManifest):
        raise TypeError("manifest must be an L2IdentityDeploymentManifest")
    if not isinstance(policy, OidcTrustPolicy):
        raise TypeError("policy must be an OidcTrustPolicy")
    reasons: list[str] = []

    def block(condition: bool, reason: str) -> None:
        if condition:
            reasons.append(reason)

    block(policy.issuer != manifest.oidc.issuer, "OIDC_ISSUER_MISMATCH")
    block(policy.audience != manifest.oidc.audience, "OIDC_AUDIENCE_MISMATCH")
    block(
        set(policy.allowed_algorithms) != set(manifest.oidc.signing_algorithms),
        "OIDC_ALGORITHM_POLICY_MISMATCH",
    )
    block(
        policy.required_acr_values != (manifest.oidc.required_acr,),
        "OIDC_ACR_POLICY_MISMATCH",
    )
    block(
        policy.required_amr_values != manifest.oidc.required_amr,
        "OIDC_AMR_POLICY_MISMATCH",
    )
    block(
        policy.maximum_token_lifetime_seconds
        < manifest.oidc.access_token_lifetime_seconds,
        "OIDC_TOKEN_LIFETIME_POLICY_MISMATCH",
    )
    block(
        policy.maximum_authentication_age_seconds
        < manifest.oidc.maximum_authentication_age_seconds,
        "OIDC_AUTHENTICATION_AGE_POLICY_MISMATCH",
    )
    block(
        policy.key_registry.issuer != manifest.oidc.issuer,
        "OIDC_KEY_REGISTRY_ISSUER_MISMATCH",
    )
    return L2IdentityCompatibilityDecision(
        verdict="BLOCK" if reasons else "PASS",
        reason_codes=tuple(reasons),
    )


def l2_identity_design_status(
    manifest: L2IdentityDeploymentManifest,
    *,
    observed_at_ms: int,
) -> ExternalEvidenceStatus:
    """Report design readiness without fabricating deployed identity evidence."""

    if not isinstance(manifest, L2IdentityDeploymentManifest):
        raise TypeError("manifest must be an L2IdentityDeploymentManifest")
    if isinstance(observed_at_ms, bool) or not isinstance(observed_at_ms, int) or observed_at_ms < 0:
        raise ValueError("observed_at_ms must be a non-negative integer")
    digest = stable_hash(manifest.model_dump(mode="json"))
    return ExternalEvidenceStatus(
        capability="l2_internal_strong_identity",
        status="PARTIAL",
        reason_code="L2_IDENTITY_DESIGN_READY_DEPLOYMENT_PENDING",
        required_action=(
            "Complete the internal Keycloak, private-CA, Envoy, trusted-socket, "
            "operator-enrollment, rotation, revocation, and recovery deployment evidence"
        ),
        observed_at_ms=observed_at_ms,
        external_authority=manifest.oidc.issuer,
        evidence_ref=f"local-design-sha256:{digest}",
    )


__all__ = [
    "L2IdentityCompatibilityDecision",
    "L2IdentityDeploymentManifest",
    "L2IdentityAuthorizationDeployment",
    "L2MtlsIngressDeployment",
    "L2OidcDeployment",
    "NoRuntimeAuthorities",
    "OidcBrowserClientDeployment",
    "evaluate_l2_identity_compatibility",
    "l2_identity_design_status",
    "load_l2_identity_deployment_manifest",
]
