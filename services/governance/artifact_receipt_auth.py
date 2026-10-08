"""Verify externally signed artifact-admission freshness receipts.

This module contains no signer, private-key, network, deployment, or execution
capability. External authorities sign the domain-separated canonical payload;
QUANTOS only verifies retained envelopes against repository-governed public keys.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .artifact_admission import (
    ArtifactVerificationReceipt,
    VerifiedReceiptAuthentication,
    artifact_verification_receipt_sha256,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_RECEIPT_PAYLOAD_TYPE = (
    "application/vnd.quantos.artifact-verification-receipt.v1+json"
)


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode_base64url(value: str, *, expected_bytes: int) -> bytes:
    if (
        not isinstance(value, str)
        or not value
        or "=" in value
        or _BASE64URL.fullmatch(value) is None
    ):
        raise ValueError("must be unpadded canonical base64url")
    try:
        decoded = base64.urlsafe_b64decode(
            value + "=" * ((4 - len(value) % 4) % 4)
        )
    except (ValueError, base64.binascii.Error) as exc:
        raise ValueError("must be unpadded canonical base64url") from exc
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value or len(decoded) != expected_bytes:
        raise ValueError("must be unpadded canonical base64url")
    return decoded


class ReceiptAuthorityKey(_FrozenContract):
    schema_version: Literal[1] = 1
    authority_id: str = Field(min_length=1, max_length=255)
    key_id: str = Field(min_length=1, max_length=255)
    algorithm: Literal["Ed25519"] = "Ed25519"
    public_key_base64url: str
    public_key_sha256: str
    status: Literal["ACTIVE", "REVOKED"]

    @field_validator("public_key_base64url")
    @classmethod
    def _validate_public_key(cls, value: str) -> str:
        _decode_base64url(value, expected_bytes=32)
        return value

    @field_validator("public_key_sha256")
    @classmethod
    def _validate_sha256(cls, value: str) -> str:
        if _SHA256.fullmatch(value) is None:
            raise ValueError("must be a lowercase SHA-256 digest")
        return value


class SignedArtifactVerificationReceipt(_FrozenContract):
    schema_version: Literal[1] = 1
    payload_type: Literal[
        "application/vnd.quantos.artifact-verification-receipt.v1+json"
    ] = _RECEIPT_PAYLOAD_TYPE
    authority_id: str = Field(min_length=1, max_length=255)
    key_id: str = Field(min_length=1, max_length=255)
    algorithm: Literal["Ed25519"] = "Ed25519"
    receipt: ArtifactVerificationReceipt
    signature_base64url: str

    @field_validator("signature_base64url")
    @classmethod
    def _validate_signature(cls, value: str) -> str:
        _decode_base64url(value, expected_bytes=64)
        return value


class ArtifactReceiptAuthenticationError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def artifact_receipt_signing_payload(
    *,
    authority_id: str,
    key_id: str,
    algorithm: Literal["Ed25519"],
    receipt: ArtifactVerificationReceipt,
) -> bytes:
    """Return the domain-separated canonical payload an external authority signs."""
    if not isinstance(authority_id, str) or not authority_id:
        raise ValueError("authority_id must be a non-empty string")
    if not isinstance(key_id, str) or not key_id:
        raise ValueError("key_id must be a non-empty string")
    if algorithm != "Ed25519":
        raise ValueError("unsupported receipt signature algorithm")
    if not isinstance(receipt, ArtifactVerificationReceipt):
        raise TypeError("receipt must be an ArtifactVerificationReceipt")
    return _canonical(
        {
            "algorithm": algorithm,
            "authority_id": authority_id,
            "key_id": key_id,
            "payload_type": _RECEIPT_PAYLOAD_TYPE,
            "receipt": receipt.model_dump(mode="json"),
            "schema_version": 1,
        }
    )


def signed_artifact_receipt_sha256(
    envelope: SignedArtifactVerificationReceipt,
) -> str:
    if not isinstance(envelope, SignedArtifactVerificationReceipt):
        raise TypeError("envelope must be a SignedArtifactVerificationReceipt")
    return _sha256(_canonical(envelope.model_dump(mode="json")))


def verify_signed_artifact_receipt(
    envelope: SignedArtifactVerificationReceipt,
    authority_key: ReceiptAuthorityKey,
    *,
    required_authority_id: str,
    required_key_id: str,
    required_public_key_sha256: str,
) -> VerifiedReceiptAuthentication:
    """Verify one retained receipt envelope against an exact governed key."""
    if not isinstance(envelope, SignedArtifactVerificationReceipt):
        raise TypeError("envelope must be a SignedArtifactVerificationReceipt")
    if not isinstance(authority_key, ReceiptAuthorityKey):
        raise TypeError("authority_key must be a ReceiptAuthorityKey")
    if envelope.authority_id != required_authority_id:
        raise ArtifactReceiptAuthenticationError("RECEIPT_AUTHORITY_MISMATCH")
    if envelope.key_id != required_key_id:
        raise ArtifactReceiptAuthenticationError("RECEIPT_KEY_ID_MISMATCH")
    if (
        authority_key.authority_id != envelope.authority_id
        or authority_key.key_id != envelope.key_id
    ):
        raise ArtifactReceiptAuthenticationError("RECEIPT_AUTHORITY_KEY_MISMATCH")
    if authority_key.status != "ACTIVE":
        raise ArtifactReceiptAuthenticationError("RECEIPT_AUTHORITY_KEY_REVOKED")
    if authority_key.algorithm != envelope.algorithm:
        raise ArtifactReceiptAuthenticationError("RECEIPT_SIGNATURE_ALGORITHM_MISMATCH")
    public_key_bytes = _decode_base64url(
        authority_key.public_key_base64url,
        expected_bytes=32,
    )
    public_key_sha256 = _sha256(public_key_bytes)
    if (
        public_key_sha256 != authority_key.public_key_sha256
        or public_key_sha256 != required_public_key_sha256
    ):
        raise ArtifactReceiptAuthenticationError("RECEIPT_PUBLIC_KEY_MISMATCH")
    signing_payload = artifact_receipt_signing_payload(
        authority_id=envelope.authority_id,
        key_id=envelope.key_id,
        algorithm=envelope.algorithm,
        receipt=envelope.receipt,
    )
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            _decode_base64url(envelope.signature_base64url, expected_bytes=64),
            signing_payload,
        )
    except (InvalidSignature, ValueError) as exc:
        raise ArtifactReceiptAuthenticationError("RECEIPT_SIGNATURE_INVALID") from exc
    return VerifiedReceiptAuthentication(
        schema_version=1,
        authority_id=envelope.authority_id,
        key_id=envelope.key_id,
        algorithm=envelope.algorithm,
        public_key_sha256=public_key_sha256,
        envelope_sha256=signed_artifact_receipt_sha256(envelope),
        receipt_sha256=artifact_verification_receipt_sha256(envelope.receipt),
        signature_verified=True,
    )


__all__ = [
    "ArtifactReceiptAuthenticationError",
    "ReceiptAuthorityKey",
    "SignedArtifactVerificationReceipt",
    "VerifiedReceiptAuthentication",
    "artifact_receipt_signing_payload",
    "signed_artifact_receipt_sha256",
    "verify_signed_artifact_receipt",
]
