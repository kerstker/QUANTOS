import base64
import hashlib

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from services.governance.oidc_key_registry import (
    OidcKeyRegistry,
    OidcVerificationKey,
)


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
        key_registry=OidcKeyRegistry(
            issuer="https://identity.example.test",
            generation=1,
            keys=(
                OidcVerificationKey(
                    kid="key-1",
                    algorithm="EdDSA",
                    public_key_pem=public_pem,
                    status="ACTIVE",
                    not_before=NOW - 100,
                    not_after=NOW + 1_000,
                ),
            ),
        ),
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


def _token(private_key, claims=None, *, algorithm="EdDSA", typ="at+jwt", kid="key-1"):
    return jwt.encode(
        claims or _claims(),
        private_key,
        algorithm=algorithm,
        headers={"typ": typ, "kid": kid},
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
        headers={"typ": "at+jwt", "kid": "key-1"},
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


def test_verifier_requires_registered_kid_and_rejects_revoked_rotation_key():
    from services.governance.strong_identity import (
        OidcMtlsVerifier,
        OidcTrustPolicy,
        StrongIdentityError,
    )

    old_private = Ed25519PrivateKey.generate()
    new_private = Ed25519PrivateKey.generate()

    def public_pem(private_key: Ed25519PrivateKey) -> str:
        return private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")

    registry = OidcKeyRegistry(
        issuer="https://identity.example.test",
        generation=2,
        keys=(
            OidcVerificationKey(
                kid="new",
                algorithm="EdDSA",
                public_key_pem=public_pem(new_private),
                status="ACTIVE",
                not_before=NOW - 100,
                not_after=NOW + 1_000,
            ),
            OidcVerificationKey(
                kid="old",
                algorithm="EdDSA",
                public_key_pem=public_pem(old_private),
                status="REVOKED",
                not_before=NOW - 100,
                not_after=NOW + 1_000,
            ),
        ),
    )
    verifier = OidcMtlsVerifier(
        OidcTrustPolicy(
            issuer="https://identity.example.test",
            audience="quantos-governance",
            key_registry=registry,
            allowed_algorithms=("EdDSA",),
            required_acr_values=("urn:quantos:aal2",),
            required_amr_values=("mfa",),
            maximum_authentication_age_seconds=300,
            maximum_token_lifetime_seconds=600,
            clock_skew_seconds=0,
        )
    )

    identity = verifier.verify(
        _token(new_private, kid="new"),
        client_certificate_der=CERTIFICATE_DER,
        now=NOW,
    )
    assert identity.subject == "human-alice-immutable-id"

    cases = (
        (_token(new_private, kid=""), "OIDC_KEY_ID_REQUIRED"),
        (_token(new_private, kid="unknown"), "OIDC_KEY_NOT_TRUSTED"),
        (_token(old_private, kid="old"), "OIDC_KEY_REVOKED"),
    )
    for token, code in cases:
        with pytest.raises(StrongIdentityError) as error:
            verifier.verify(
                token,
                client_certificate_der=CERTIFICATE_DER,
                now=NOW,
            )
        assert error.value.code == code


def test_environment_loader_requires_digest_and_generation_pinned_registry(tmp_path):
    from services.governance.strong_identity import (
        StrongIdentityUnavailable,
        load_oidc_policy_from_environment,
    )

    private_key = Ed25519PrivateKey.generate()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    registry = OidcKeyRegistry(
        issuer="https://identity.example.test",
        generation=3,
        keys=(
            OidcVerificationKey(
                kid="current",
                algorithm="EdDSA",
                public_key_pem=public_pem,
                status="ACTIVE",
                not_before=NOW - 100,
                not_after=NOW + 1_000,
            ),
        ),
    )
    registry_bytes = (registry.model_dump_json(indent=2) + "\n").encode("utf-8")
    registry_path = tmp_path / "oidc-key-registry.json"
    registry_path.write_bytes(registry_bytes)
    environ = {
        "QUANTOS_OIDC_ISSUER": registry.issuer,
        "QUANTOS_OIDC_AUDIENCE": "quantos-governance",
        "QUANTOS_OIDC_KEY_REGISTRY_FILE": str(registry_path),
        "QUANTOS_OIDC_KEY_REGISTRY_SHA256": hashlib.sha256(registry_bytes).hexdigest(),
        "QUANTOS_OIDC_KEY_REGISTRY_MIN_GENERATION": "3",
        "QUANTOS_OIDC_ALGORITHMS": "EdDSA",
    }

    assert load_oidc_policy_from_environment(environ).key_registry == registry

    bad_digest = {**environ, "QUANTOS_OIDC_KEY_REGISTRY_SHA256": "0" * 64}
    with pytest.raises(StrongIdentityUnavailable) as digest_error:
        load_oidc_policy_from_environment(bad_digest)
    assert digest_error.value.code == "OIDC_KEY_REGISTRY_DIGEST_MISMATCH"

    rollback = {**environ, "QUANTOS_OIDC_KEY_REGISTRY_MIN_GENERATION": "4"}
    with pytest.raises(StrongIdentityUnavailable) as rollback_error:
        load_oidc_policy_from_environment(rollback)
    assert rollback_error.value.code == "OIDC_KEY_REGISTRY_ROLLBACK"

    legacy_static_key_only = {
        "QUANTOS_OIDC_ISSUER": registry.issuer,
        "QUANTOS_OIDC_AUDIENCE": "quantos-governance",
        "QUANTOS_OIDC_PUBLIC_KEY_FILE": str(registry_path),
    }
    with pytest.raises(StrongIdentityUnavailable) as legacy_error:
        load_oidc_policy_from_environment(legacy_static_key_only)
    assert legacy_error.value.code == "IDENTITY_POLICY_UNAVAILABLE"


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
