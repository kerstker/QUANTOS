from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from services.governance.oidc_key_registry import (
    OidcKeyRegistry,
    OidcKeyRegistryError,
    OidcVerificationKey,
    load_oidc_key_registry,
    select_oidc_verification_key,
    validate_oidc_key_registry_successor,
)


ISSUER = "https://identity.example.test"
NOW = 10_000


def _public_pem() -> str:
    return (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


def _key(
    kid: str,
    *,
    public_key_pem: str | None = None,
    status: str = "ACTIVE",
    not_before: int = NOW - 100,
    not_after: int = NOW + 100,
) -> OidcVerificationKey:
    return OidcVerificationKey(
        kid=kid,
        algorithm="EdDSA",
        public_key_pem=public_key_pem or _public_pem(),
        status=status,
        not_before=not_before,
        not_after=not_after,
    )


def _registry(
    generation: int,
    *keys: OidcVerificationKey,
) -> OidcKeyRegistry:
    return OidcKeyRegistry(
        issuer=ISSUER,
        generation=generation,
        keys=tuple(sorted(keys, key=lambda item: item.kid)),
    )


def test_exact_kid_algorithm_status_and_time_are_load_bearing() -> None:
    trusted = _key("key-1")
    registry = _registry(1, trusted)

    assert select_oidc_verification_key(
        registry,
        kid="key-1",
        algorithm="EdDSA",
        now=NOW,
    ) == trusted

    cases = (
        ("", "EdDSA", NOW, "OIDC_KEY_ID_REQUIRED"),
        ("unknown", "EdDSA", NOW, "OIDC_KEY_NOT_TRUSTED"),
        ("key-1", "RS256", NOW, "OIDC_KEY_ALGORITHM_MISMATCH"),
        ("key-1", "EdDSA", NOW - 101, "OIDC_KEY_NOT_YET_VALID"),
        ("key-1", "EdDSA", NOW + 100, "OIDC_KEY_EXPIRED"),
    )
    for kid, algorithm, now, code in cases:
        with pytest.raises(OidcKeyRegistryError) as error:
            select_oidc_verification_key(
                registry,
                kid=kid,
                algorithm=algorithm,
                now=now,
            )
        assert error.value.code == code

    revoked = _registry(2, trusted.model_copy(update={"status": "REVOKED"}))
    with pytest.raises(OidcKeyRegistryError) as error:
        select_oidc_verification_key(
            revoked,
            kid="key-1",
            algorithm="EdDSA",
            now=NOW,
        )
    assert error.value.code == "OIDC_KEY_REVOKED"


def test_rotation_is_contiguous_append_only_and_revocation_is_one_way() -> None:
    old = _key("old")
    current = _registry(1, old)
    new = _key("new")
    rotated = _registry(
        2,
        old.model_copy(update={"status": "REVOKED"}),
        new,
    )

    assert validate_oidc_key_registry_successor(current, current) == current
    assert validate_oidc_key_registry_successor(current, rotated) == rotated

    invalid = (
        (_registry(0), "OIDC_KEY_REGISTRY_ROLLBACK"),
        (_registry(1, new), "OIDC_KEY_REGISTRY_CONFLICT"),
        (_registry(3, old, new), "OIDC_KEY_REGISTRY_GENERATION_GAP"),
        (_registry(2, new), "OIDC_KEY_REMOVAL_FORBIDDEN"),
        (
            _registry(2, old.model_copy(update={"public_key_pem": _public_pem()}), new),
            "OIDC_KEY_IDENTITY_CHANGED",
        ),
    )
    for candidate, code in invalid:
        with pytest.raises(OidcKeyRegistryError) as error:
            validate_oidc_key_registry_successor(current, candidate)
        assert error.value.code == code

    with pytest.raises(OidcKeyRegistryError) as error:
        validate_oidc_key_registry_successor(
            rotated,
            _registry(3, old, new),
        )
    assert error.value.code == "OIDC_KEY_REACTIVATION_FORBIDDEN"


def test_registry_loader_rejects_duplicate_json_members_and_kids() -> None:
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_oidc_key_registry(
            '{"schema_version":1,"schema_version":1,"issuer":"https://identity.example.test",'
            '"generation":0,"keys":[]}'
        )

    key = _key("duplicate")
    with pytest.raises(ValidationError, match="unique"):
        OidcKeyRegistry(
            issuer=ISSUER,
            generation=1,
            keys=(key, key),
        )

    encoded = json.dumps(_registry(1, key).model_dump(mode="json"))
    assert load_oidc_key_registry(encoded) == _registry(1, key)


def test_registry_rejects_private_keys_weak_rsa_and_non_https_issuer() -> None:
    private_pem = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    with pytest.raises(ValidationError, match="public key"):
        _key("private", public_key_pem=private_pem)
    invalid_issuers = (
        "http://identity.example.test",
        "HTTPS://identity.example.test",
        "https://user@identity.example.test",
        "https://identity.example.test?tenant=one",
        "https://identity.example.test#fragment",
        "https://127.0.0.1",
        "https://identity.example.test:0",
        "https://identity.example.test:65536",
        "https://identity.example.test\\path",
        "https://identity example.test",
        "https://localhost",
    )
    for issuer in invalid_issuers:
        with pytest.raises(ValidationError, match="HTTPS"):
            OidcKeyRegistry(
                issuer=issuer,
                generation=1,
                keys=(_key("key-1"),),
            )

    assert OidcKeyRegistry(
        issuer="https://identity.example.test:8443/oidc/v1",
        generation=1,
        keys=(_key("key-1"),),
    ).issuer == "https://identity.example.test:8443/oidc/v1"
