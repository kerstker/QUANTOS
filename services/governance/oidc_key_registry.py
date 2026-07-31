"""Strict offline trust registry for rotating OIDC verification keys.

The registry is data only: it never fetches issuer metadata, stores private
keys, signs tokens, or grants application authorization. Runtime callers pin an
operator-provided registry snapshot by digest and minimum generation.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)


class OidcKeyRegistryError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ISSUER_PATH = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")


def _canonical_https_issuer(value: str) -> bool:
    """Validate a pinned issuer without importing a network namespace.

    OIDC discovery is deliberately outside ``services/``.  The trust registry
    therefore accepts only a canonical, lower-case DNS HTTPS origin plus an
    optional absolute path; credentials, query strings, fragments, IP
    literals, ambiguous separators, and non-default URL spellings fail closed.
    """
    prefix = "https://"
    if not value.startswith(prefix):
        return False
    remainder = value[len(prefix) :]
    if not remainder or any(char.isspace() for char in remainder):
        return False
    if any(char in remainder for char in ("@", "?", "#", "\\")):
        return False
    authority, separator, suffix = remainder.partition("/")
    if not authority or authority != authority.lower():
        return False
    if ":" in authority:
        host, port_text = authority.rsplit(":", 1)
        if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
            return False
    else:
        host = authority
    labels = host.split(".")
    if (
        len(labels) < 2
        or host.replace(".", "").isdigit()
        or any(_DNS_LABEL.fullmatch(label) is None for label in labels)
    ):
        return False
    if separator and _ISSUER_PATH.fullmatch("/" + suffix) is None:
        return False
    return True


class OidcVerificationKey(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    kid: StrictStr = Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")
    algorithm: Literal["EdDSA", "RS256"]
    public_key_pem: StrictStr = Field(min_length=1)
    status: Literal["ACTIVE", "REVOKED"]
    not_before: int = Field(ge=0)
    not_after: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_key(self) -> "OidcVerificationKey":
        if self.not_after <= self.not_before:
            raise ValueError("OIDC key not_after must follow not_before")
        if "PRIVATE KEY" in self.public_key_pem:
            raise ValueError("OIDC registry accepts public keys only")
        try:
            key = serialization.load_pem_public_key(self.public_key_pem.encode("ascii"))
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise ValueError("OIDC registry contains an invalid public key") from exc
        if self.algorithm == "EdDSA" and not isinstance(key, Ed25519PublicKey):
            raise ValueError("EdDSA registry entries require an Ed25519 public key")
        if self.algorithm == "RS256":
            if not isinstance(key, RSAPublicKey):
                raise ValueError("RS256 registry entries require an RSA public key")
            if key.key_size < 2048:
                raise ValueError("RS256 registry entries require at least 2048-bit RSA")
        return self


class OidcKeyRegistry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_version: Literal[1] = 1
    issuer: StrictStr = Field(min_length=1)
    generation: int = Field(ge=0)
    keys: tuple[OidcVerificationKey, ...] = ()

    @field_validator("keys", mode="before")
    @classmethod
    def _tuple_keys(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            raise ValueError("OIDC registry keys must be a list or tuple")
        return tuple(value)

    @model_validator(mode="after")
    def _validate_registry(self) -> "OidcKeyRegistry":
        if not _canonical_https_issuer(self.issuer):
            raise ValueError("OIDC registry issuer must be a canonical HTTPS URL")
        if self.generation == 0 and self.keys:
            raise ValueError("OIDC registry generation zero must be empty")
        if self.generation > 0 and not self.keys:
            raise ValueError("provisioned OIDC registry must contain at least one key")
        kids = [key.kid for key in self.keys]
        if len(set(kids)) != len(kids):
            raise ValueError("OIDC registry key IDs must be unique")
        if tuple(sorted(kids)) != tuple(kids):
            raise ValueError("OIDC registry keys must be ordered by kid")
        return self

    def key(self, kid: str) -> OidcVerificationKey | None:
        return next((key for key in self.keys if key.kid == kid), None)


def load_oidc_key_registry(content: bytes | str) -> OidcKeyRegistry:
    if isinstance(content, bytes):
        text = content.decode("utf-8")
    elif isinstance(content, str):
        text = content
    else:
        raise TypeError("OIDC key registry content must be bytes or text")

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
    return OidcKeyRegistry.model_validate(raw)


def _key_identity(key: OidcVerificationKey) -> tuple[object, ...]:
    return (
        key.kid,
        key.algorithm,
        key.public_key_pem,
        key.not_before,
        key.not_after,
    )


def validate_oidc_key_registry_successor(
    current: OidcKeyRegistry,
    candidate: OidcKeyRegistry,
) -> OidcKeyRegistry:
    if not isinstance(current, OidcKeyRegistry):
        raise TypeError("current must be an OidcKeyRegistry")
    if not isinstance(candidate, OidcKeyRegistry):
        raise TypeError("candidate must be an OidcKeyRegistry")
    if candidate == current:
        return current
    if candidate.issuer != current.issuer:
        raise OidcKeyRegistryError(
            "OIDC_KEY_REGISTRY_ISSUER_CHANGED",
            "OIDC key registry issuer is immutable",
        )
    if candidate.generation < current.generation:
        raise OidcKeyRegistryError(
            "OIDC_KEY_REGISTRY_ROLLBACK",
            "OIDC key registry generation rolled back",
        )
    if candidate.generation == current.generation:
        raise OidcKeyRegistryError(
            "OIDC_KEY_REGISTRY_CONFLICT",
            "one OIDC key registry generation cannot have different content",
        )
    if candidate.generation != current.generation + 1:
        raise OidcKeyRegistryError(
            "OIDC_KEY_REGISTRY_GENERATION_GAP",
            "OIDC key registry generations must be contiguous",
        )
    candidate_keys = {key.kid: key for key in candidate.keys}
    for old in current.keys:
        replacement = candidate_keys.get(old.kid)
        if replacement is None:
            raise OidcKeyRegistryError(
                "OIDC_KEY_REMOVAL_FORBIDDEN",
                "OIDC verification keys cannot disappear from registry history",
            )
        if _key_identity(replacement) != _key_identity(old):
            raise OidcKeyRegistryError(
                "OIDC_KEY_IDENTITY_CHANGED",
                "an existing kid cannot change key material, algorithm, or validity",
            )
        if old.status == "REVOKED" and replacement.status != "REVOKED":
            raise OidcKeyRegistryError(
                "OIDC_KEY_REACTIVATION_FORBIDDEN",
                "a revoked OIDC key cannot be reactivated",
            )
    return candidate


def select_oidc_verification_key(
    registry: OidcKeyRegistry,
    *,
    kid: str,
    algorithm: str,
    now: int,
) -> OidcVerificationKey:
    if not isinstance(registry, OidcKeyRegistry):
        raise TypeError("registry must be an OidcKeyRegistry")
    if type(now) is not int or now < 0:
        raise ValueError("OIDC key selection time must be a non-negative integer")
    if not isinstance(kid, str) or not kid:
        raise OidcKeyRegistryError(
            "OIDC_KEY_ID_REQUIRED",
            "token header kid is required",
        )
    key = registry.key(kid)
    if key is None:
        raise OidcKeyRegistryError(
            "OIDC_KEY_NOT_TRUSTED",
            "token header kid is not registered",
        )
    if key.status == "REVOKED":
        raise OidcKeyRegistryError(
            "OIDC_KEY_REVOKED",
            "token verification key is revoked",
        )
    if algorithm != key.algorithm:
        raise OidcKeyRegistryError(
            "OIDC_KEY_ALGORITHM_MISMATCH",
            "token algorithm does not match the registered key",
        )
    if now < key.not_before:
        raise OidcKeyRegistryError(
            "OIDC_KEY_NOT_YET_VALID",
            "token verification key is not yet valid",
        )
    if now >= key.not_after:
        raise OidcKeyRegistryError(
            "OIDC_KEY_EXPIRED",
            "token verification key has expired",
        )
    return key


__all__ = [
    "OidcKeyRegistry",
    "OidcKeyRegistryError",
    "OidcVerificationKey",
    "load_oidc_key_registry",
    "select_oidc_verification_key",
    "validate_oidc_key_registry_successor",
]
