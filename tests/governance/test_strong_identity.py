import base64
import hashlib

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError


NOW = 10_000
CERTIFICATE_DER = b"verified-client-certificate-der-fixture"


def _thumbprint(certificate_der: bytes = CERTIFICATE_DER) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(certificate_der).digest()).rstrip(b"=").decode("ascii")


@pytest.fixture()
def identity_fixture():
    from services.governance.strong_identity import OidcTrustPolicy, OidcMtlsVerifier

    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    policy = OidcTrustPolicy(
        issuer="https://identity.example.test",
        audience="quantos-governance",
        public_key_pem=public_pem,
        allowed_algorithms=("EdDSA",),
        required_acr_values=("urn:quantos:aal2",),
        required_amr_values=("mfa",),
        maximum_authentication_age_seconds=300,
        maximum_token_lifetime_seconds=600,
        clock_skew_seconds=0,
    )
    return private_key, OidcMtlsVerifier(policy)


def _claims(**updates):
    from quantos.contracts.identity import GovernanceRole

    claims = {
        "iss": "https://identity.example.test",
        "sub": "human-alice-immutable-id",
        "aud": "quantos-governance",
        "iat": NOW - 10,
        "nbf": NOW - 10,
        "exp": NOW + 120,
        "auth_time": NOW - 20,
        "jti": "token-1",
        "acr": "urn:quantos:aal2",
        "amr": ["pwd", "mfa"],
        "roles": [role.value for role in GovernanceRole],
        "cnf": {"x5t#S256": _thumbprint()},
    }
    claims.update(updates)
    return claims


def _token(private_key, claims=None, *, algorithm="EdDSA", typ="at+jwt"):
    return jwt.encode(
        claims or _claims(),
        private_key,
        algorithm=algorithm,
        headers={"typ": typ},
    )


def test_oidc_mtls_verifier_returns_frozen_external_identity(identity_fixture):
    from quantos.contracts.identity import GovernanceRole, StrongIdentityAssurance

    private_key, verifier = identity_fixture
    identity = verifier.verify(
        _token(private_key),
        client_certificate_der=CERTIFICATE_DER,
        now=NOW,
    )
    assert identity.actor_id == "https://identity.example.test|human-alice-immutable-id"
    assert identity.assurance == StrongIdentityAssurance.OIDC_MTLS_MFA
    assert identity.has_role(GovernanceRole.PROMOTION_APPROVER)
    assert identity.certificate_sha256 == hashlib.sha256(CERTIFICATE_DER).hexdigest()
    with pytest.raises(ValidationError):
        identity.__class__.model_validate({**identity.model_dump(), "subject": ""})


@pytest.mark.parametrize(
    "updates",
    [
        {"iss": "https://attacker.invalid"},
        {"aud": "some-other-service"},
        {"exp": NOW},
        {"nbf": NOW + 1},
        {"iat": NOW + 1},
        {"auth_time": NOW - 301},
        {"acr": "urn:quantos:aal1"},
        {"amr": ["pwd"]},
        {"roles": "PROMOTION_APPROVER"},
        {"jti": ""},
        {"cnf": {"x5t#S256": _thumbprint(b"other-cert")}},
    ],
)
def test_verifier_fails_closed_on_invalid_claims(identity_fixture, updates):
    from services.governance.strong_identity import StrongIdentityError

    private_key, verifier = identity_fixture
    with pytest.raises(StrongIdentityError):
        verifier.verify(
            _token(private_key, _claims(**updates)),
            client_certificate_der=CERTIFICATE_DER,
            now=NOW,
        )


def test_verifier_rejects_wrong_signature_and_algorithm(identity_fixture):
    from services.governance.strong_identity import StrongIdentityError

    _private_key, verifier = identity_fixture
    attacker = Ed25519PrivateKey.generate()
    with pytest.raises(StrongIdentityError, match="signature"):
        verifier.verify(
            _token(attacker),
            client_certificate_der=CERTIFICATE_DER,
            now=NOW,
        )
    symmetric = jwt.encode(
        _claims(),
        "attacker-secret-value-at-least-32-bytes",
        algorithm="HS256",
        headers={"typ": "at+jwt"},
    )
    with pytest.raises(StrongIdentityError, match="algorithm"):
        verifier.verify(symmetric, client_certificate_der=CERTIFICATE_DER, now=NOW)


def test_verifier_rejects_wrong_token_type(identity_fixture):
    from services.governance.strong_identity import StrongIdentityError

    private_key, verifier = identity_fixture
    with pytest.raises(StrongIdentityError, match="type"):
        verifier.verify(
            _token(private_key, typ="JWT"),
            client_certificate_der=CERTIFICATE_DER,
            now=NOW,
        )


def test_trusted_tls_scope_is_required_and_headers_are_irrelevant():
    from services.governance.strong_identity import (
        StrongIdentityError,
        extract_verified_client_certificate,
    )

    scope = {
        "headers": [(b"x-client-cert", b"spoofed")],
        "extensions": {
            "tls": {
                "client_cert_chain_verified": True,
                "client_cert_chain": [CERTIFICATE_DER],
            }
        },
    }
    assert extract_verified_client_certificate(scope) == CERTIFICATE_DER
    with pytest.raises(StrongIdentityError, match="verified client certificate"):
        extract_verified_client_certificate({"headers": scope["headers"]})
    scope["extensions"]["tls"]["client_cert_chain_verified"] = False
    with pytest.raises(StrongIdentityError, match="verified client certificate"):
        extract_verified_client_certificate(scope)


def test_governance_role_enforcement_is_fail_closed(identity_fixture):
    from quantos.contracts.identity import GovernanceRole
    from services.governance.strong_identity import StrongIdentityError, require_role

    private_key, verifier = identity_fixture
    claims = _claims(roles=[GovernanceRole.PROMOTION_READER.value])
    identity = verifier.verify(
        _token(private_key, claims),
        client_certificate_der=CERTIFICATE_DER,
        now=NOW,
    )
    require_role(identity, GovernanceRole.PROMOTION_READER)
    with pytest.raises(StrongIdentityError, match="role"):
        require_role(identity, GovernanceRole.PROMOTION_APPROVER)


def test_promotion_dependency_uses_real_bearer_and_trusted_tls_scope(
    identity_fixture, monkeypatch
):
    from starlette.requests import Request
    import services.promotion_api.main as promotion_api

    private_key, verifier = identity_fixture
    monkeypatch.setattr(promotion_api, "_get_identity_verifier", lambda: verifier)
    monkeypatch.setattr(promotion_api, "_now", lambda: NOW)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "extensions": {
                "tls": {
                    "client_cert_chain_verified": True,
                    "client_cert_chain": [CERTIFICATE_DER],
                }
            },
        }
    )
    identity = promotion_api.require_governance_identity(
        request,
        authorization=f"Bearer {_token(private_key)}",
    )
    assert identity.subject == "human-alice-immutable-id"
