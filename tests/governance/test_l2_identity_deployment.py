from __future__ import annotations

import copy
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from pydantic import ValidationError

from quantos.contracts.identity import GovernanceRole
from services.governance.oidc_key_registry import (
    OidcKeyRegistry,
    OidcVerificationKey,
)


NOW = 10_000
SHA256 = "a" * 64


def _manifest_dict() -> dict[str, object]:
    return {
        "schema_version": 1,
        "readiness": "L2_DRY_RUN_ONLY",
        "topology": "SINGLE_SITE_INTERNAL",
        "oidc": {
            "provider": "KEYCLOAK",
            "image_reference": f"quay.io/keycloak/keycloak@sha256:{SHA256}",
            "issuer": "https://identity.quantos.internal/realms/quantos-l2",
            "audience": "quantos-promotion-api",
            "network_scope": "INTERNAL_ONLY",
            "public_exposure": False,
            "direct_mtls": True,
            "signing_algorithms": ["RS256"],
            "access_token_lifetime_seconds": 300,
            "maximum_authentication_age_seconds": 300,
            "required_acr": "urn:quantos:aal2",
            "required_amr": ["pwd", "mfa"],
            "roles_claim": "roles",
            "browser_client": {
                "client_id": "quantos-research-console",
                "public_client": True,
                "client_secret_present": False,
                "authorization_code_enabled": True,
                "pkce_method": "S256",
                "implicit_grant_enabled": False,
                "direct_grant_enabled": False,
                "device_grant_enabled": False,
                "ciba_grant_enabled": False,
                "token_exchange_enabled": False,
                "service_account_enabled": False,
                "offline_access_enabled": False,
                "certificate_bound_access_tokens": True,
                "redirect_uris": [
                    "https://console.quantos.internal/auth/governance/callback"
                ],
                "web_origins": ["https://console.quantos.internal"],
            },
        },
        "mtls_ingress": {
            "provider": "ENVOY",
            "image_reference": f"envoyproxy/envoy@sha256:{'b' * 64}",
            "require_client_certificate": True,
            "certificate_validation": "PRIVATE_CA_CHAIN_AND_EXPIRY",
            "allow_expired_certificate": False,
            "forward_client_cert_mode": "SANITIZE_SET",
            "forward_certificate_chain": True,
            "trust_arbitrary_client_certificate_headers": False,
            "upstream_transport": "UNIX_DOMAIN_SOCKET",
            "upstream_address": "/run/quantos/promotion-api.sock",
            "direct_application_tcp_listener": False,
            "certificate_revocation_mode": "SHORT_LIVED_PASSIVE",
            "maximum_client_certificate_lifetime_seconds": 28_800,
        },
        "authorization": {
            "identity_source": "ISSUER_SUBJECT_ONLY",
            "application_registry_required": True,
            "independent_access_review_required": True,
            "workload_human_roles_forbidden": True,
            "allowed_governance_roles": [role.value for role in GovernanceRole],
        },
        "authorities": {
            "execution_authority_granted": False,
            "deployment_authority_granted": False,
            "live_trading_authority_granted": False,
            "credential_authority_granted": False,
            "order_submission_authority_granted": False,
            "kill_switch_reset_authority_granted": False,
        },
    }


def _policy():
    from services.governance.strong_identity import OidcTrustPolicy

    public_pem = (
        generate_private_key(public_exponent=65_537, key_size=2_048)
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    issuer = "https://identity.quantos.internal/realms/quantos-l2"
    return OidcTrustPolicy(
        issuer=issuer,
        audience="quantos-promotion-api",
        key_registry=OidcKeyRegistry(
            issuer=issuer,
            generation=1,
            keys=(
                OidcVerificationKey(
                    kid="key-1",
                    algorithm="RS256",
                    public_key_pem=public_pem,
                    status="ACTIVE",
                    not_before=NOW - 100,
                    not_after=NOW + 1_000,
                ),
            ),
        ),
        allowed_algorithms=("RS256",),
        required_acr_values=("urn:quantos:aal2",),
        required_amr_values=("pwd", "mfa"),
        maximum_authentication_age_seconds=300,
        maximum_token_lifetime_seconds=600,
        clock_skew_seconds=30,
    )


def _set_path(payload: dict[str, object], path: tuple[str, ...], value: object) -> None:
    target: dict[str, object] = payload
    for part in path[:-1]:
        child = target[part]
        assert isinstance(child, dict)
        target = child
    target[path[-1]] = value


def test_secure_internal_manifest_is_admitted_but_not_called_deployed() -> None:
    from services.governance.l2_identity_deployment import (
        L2IdentityDeploymentManifest,
        l2_identity_design_status,
    )

    manifest = L2IdentityDeploymentManifest.model_validate(_manifest_dict())
    assert manifest.oidc.issuer.endswith("/realms/quantos-l2")
    assert manifest.authorization.allowed_governance_roles == tuple(GovernanceRole)
    assert manifest.mtls_ingress.upstream_transport == "UNIX_DOMAIN_SOCKET"

    status = l2_identity_design_status(manifest, observed_at_ms=NOW * 1_000)
    assert status.status == "PARTIAL"
    assert status.reason_code == "L2_IDENTITY_DESIGN_READY_DEPLOYMENT_PENDING"
    assert "deployment" in status.required_action.lower()
    assert status.external_authority == manifest.oidc.issuer


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("readiness",), "LIVE"),
        (("oidc", "issuer"), "http://identity.quantos.internal/realms/quantos-l2"),
        (("oidc", "image_reference"), "quay.io/keycloak/keycloak:latest"),
        (("oidc", "network_scope"), "PUBLIC"),
        (("oidc", "public_exposure"), True),
        (("oidc", "direct_mtls"), False),
        (("oidc", "access_token_lifetime_seconds"), 601),
        (("oidc", "maximum_authentication_age_seconds"), 301),
        (("oidc", "roles_claim"), "realm_access.roles"),
        (("oidc", "browser_client", "public_client"), False),
        (("oidc", "browser_client", "client_secret_present"), True),
        (("oidc", "browser_client", "authorization_code_enabled"), False),
        (("oidc", "browser_client", "pkce_method"), "plain"),
        (("oidc", "browser_client", "implicit_grant_enabled"), True),
        (("oidc", "browser_client", "direct_grant_enabled"), True),
        (("oidc", "browser_client", "device_grant_enabled"), True),
        (("oidc", "browser_client", "ciba_grant_enabled"), True),
        (("oidc", "browser_client", "token_exchange_enabled"), True),
        (("oidc", "browser_client", "service_account_enabled"), True),
        (("oidc", "browser_client", "offline_access_enabled"), True),
        (("oidc", "browser_client", "certificate_bound_access_tokens"), False),
        (("mtls_ingress", "require_client_certificate"), False),
        (("mtls_ingress", "allow_expired_certificate"), True),
        (("mtls_ingress", "forward_client_cert_mode"), "FORWARD_ONLY"),
        (("mtls_ingress", "trust_arbitrary_client_certificate_headers"), True),
        (("mtls_ingress", "upstream_transport"), "TCP"),
        (("mtls_ingress", "direct_application_tcp_listener"), True),
        (("authorization", "identity_source"), "REQUEST_HEADER"),
        (("authorization", "application_registry_required"), False),
        (("authorization", "independent_access_review_required"), False),
        (("authorization", "workload_human_roles_forbidden"), False),
        (("authorities", "execution_authority_granted"), True),
        (("authorities", "live_trading_authority_granted"), True),
        (("authorities", "order_submission_authority_granted"), True),
    ],
)
def test_manifest_rejects_every_security_downgrade(
    path: tuple[str, ...],
    value: object,
) -> None:
    from services.governance.l2_identity_deployment import L2IdentityDeploymentManifest

    payload = copy.deepcopy(_manifest_dict())
    _set_path(payload, path, value)
    with pytest.raises(ValidationError):
        L2IdentityDeploymentManifest.model_validate(payload)


def test_manifest_rejects_role_widening_secrets_and_ambiguous_urls() -> None:
    from services.governance.l2_identity_deployment import L2IdentityDeploymentManifest

    invalid = []
    extra_role = copy.deepcopy(_manifest_dict())
    roles = extra_role["authorization"]
    assert isinstance(roles, dict)
    roles["allowed_governance_roles"] = [
        *[role.value for role in GovernanceRole],
        "KILL_SWITCH_RESETTER",
    ]
    invalid.append(extra_role)

    embedded_secret = copy.deepcopy(_manifest_dict())
    client = embedded_secret["oidc"]
    assert isinstance(client, dict)
    browser = client["browser_client"]
    assert isinstance(browser, dict)
    browser["client_secret"] = "forbidden"
    invalid.append(embedded_secret)

    credentialed_issuer = copy.deepcopy(_manifest_dict())
    _set_path(
        credentialed_issuer,
        ("oidc", "issuer"),
        "https://user@identity.quantos.internal/realms/quantos-l2",
    )
    invalid.append(credentialed_issuer)

    wildcard_redirect = copy.deepcopy(_manifest_dict())
    _set_path(
        wildcard_redirect,
        ("oidc", "browser_client", "redirect_uris"),
        ["https://console.quantos.internal/*"],
    )
    invalid.append(wildcard_redirect)

    for payload in invalid:
        with pytest.raises(ValidationError):
            L2IdentityDeploymentManifest.model_validate(payload)


def test_manifest_loader_rejects_duplicate_json_members() -> None:
    from services.governance.l2_identity_deployment import (
        load_l2_identity_deployment_manifest,
    )

    encoded = json.dumps(_manifest_dict(), separators=(",", ":"))
    assert load_l2_identity_deployment_manifest(encoded).readiness == "L2_DRY_RUN_ONLY"

    duplicate = encoded.replace(
        '"schema_version":1,',
        '"schema_version":1,"schema_version":1,',
        1,
    )
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_l2_identity_deployment_manifest(duplicate)


def test_existing_verifier_policy_must_match_the_deployment_contract() -> None:
    from services.governance.l2_identity_deployment import (
        L2IdentityDeploymentManifest,
        evaluate_l2_identity_compatibility,
    )

    manifest = L2IdentityDeploymentManifest.model_validate(_manifest_dict())
    accepted = evaluate_l2_identity_compatibility(manifest, _policy())
    assert accepted.verdict == "PASS"
    assert accepted.reason_codes == ()
    assert accepted.execution_authority_granted is False
    assert accepted.live_trading_authority_granted is False

    changes = (
        {"issuer": "https://other.quantos.internal/realms/quantos-l2"},
        {"audience": "other-audience"},
        {"required_amr_values": ("mfa",)},
        {"maximum_token_lifetime_seconds": 299},
        {"maximum_authentication_age_seconds": 299},
    )
    for update in changes:
        blocked = evaluate_l2_identity_compatibility(
            manifest,
            _policy().model_copy(update=update),
        )
        assert blocked.verdict == "BLOCK"
        assert blocked.reason_codes
        assert blocked.execution_authority_granted is False
        assert blocked.live_trading_authority_granted is False
