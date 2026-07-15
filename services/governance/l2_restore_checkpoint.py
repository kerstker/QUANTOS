"""Verify externally signed L2 restore-history checkpoints.

The module is deterministic and verify-only. It accepts strict in-memory
contracts, caller-supplied trust, local history-head state, and verification
time. It has no I/O or state mutation capability.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
import hashlib
import json
import re
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_PAYLOAD_TYPE = "application/vnd.quantos.l2-restore-history-checkpoint.v1+json"


class _FrozenContract(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha256(value: str) -> str:
    if _SHA256.fullmatch(value) is None:
        raise ValueError("must be a lowercase SHA-256 digest")
    return value


def _decode_base64url(value: str, *, expected_bytes: int) -> bytes:
    if (
        not value
        or "=" in value
        or _BASE64URL.fullmatch(value) is None
    ):
        raise ValueError("must be unpadded canonical base64url")
    try:
        decoded = base64.urlsafe_b64decode(
            value + "=" * ((4 - len(value) % 4) % 4)
        )
    except (ValueError, binascii.Error) as exc:
        raise ValueError("must be unpadded canonical base64url") from exc
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value or len(decoded) != expected_bytes:
        raise ValueError("must be unpadded canonical base64url")
    return decoded


class L2RestoreHistoryHead(_FrozenContract):
    """Exact local authority head that an external checkpoint must bind."""

    store_kind: str = Field(min_length=1, max_length=255)
    store_schema_version: int = Field(ge=1)
    event_count: int = Field(ge=1)
    head_seq: int = Field(ge=1)
    head_hash: str

    @field_validator("head_hash")
    @classmethod
    def _validate_head_hash(cls, value: str) -> str:
        return _require_sha256(value)

    @model_validator(mode="after")
    def _validate_sequence(self) -> L2RestoreHistoryHead:
        if self.event_count != self.head_seq:
            raise ValueError("event_count must equal the contiguous head_seq")
        return self


class L2RestoreCheckpoint(_FrozenContract):
    """Externally retained commitment to one exact restore-history head."""

    schema_version: Literal[1] = 1
    store_kind: str = Field(min_length=1, max_length=255)
    store_schema_version: int = Field(ge=1)
    event_count: int = Field(ge=1)
    head_seq: int = Field(ge=1)
    head_hash: str
    sealed_at_ms: int = Field(ge=0)
    object_version_id: str = Field(min_length=1, max_length=1024)
    retention_until_ms: int = Field(ge=0)
    issuer: str = Field(min_length=1, max_length=255)
    key_id: str = Field(min_length=1, max_length=255)

    @field_validator("head_hash")
    @classmethod
    def _validate_head_hash(cls, value: str) -> str:
        return _require_sha256(value)

    @model_validator(mode="after")
    def _validate_sequence_and_retention(self) -> L2RestoreCheckpoint:
        if self.event_count != self.head_seq:
            raise ValueError("event_count must equal the contiguous head_seq")
        if self.retention_until_ms < self.sealed_at_ms:
            raise ValueError("retention must not end before the checkpoint seal")
        return self


class SignedL2RestoreCheckpoint(_FrozenContract):
    """Domain-separated Ed25519 envelope for one checkpoint contract."""

    schema_version: Literal[1] = 1
    payload_type: Literal[
        "application/vnd.quantos.l2-restore-history-checkpoint.v1+json"
    ] = _PAYLOAD_TYPE
    algorithm: Literal["Ed25519"] = "Ed25519"
    checkpoint: L2RestoreCheckpoint
    signature_base64url: str

    @field_validator("signature_base64url")
    @classmethod
    def _validate_signature(cls, value: str) -> str:
        _decode_base64url(value, expected_bytes=64)
        return value


class TrustedL2RestoreCheckpointKey(_FrozenContract):
    """One exact caller-governed external verification key."""

    schema_version: Literal[1] = 1
    issuer: str = Field(min_length=1, max_length=255)
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
    def _validate_public_key_sha256(cls, value: str) -> str:
        return _require_sha256(value)


class VerifiedL2RestoreCheckpoint(_FrozenContract):
    schema_version: Literal[1] = 1
    issuer: str
    key_id: str
    algorithm: Literal["Ed25519"] = "Ed25519"
    public_key_sha256: str
    envelope_sha256: str
    checkpoint_sha256: str
    store_kind: str
    store_schema_version: int
    event_count: int
    head_seq: int
    head_hash: str
    sealed_at_ms: int
    object_version_id: str
    retention_until_ms: int
    signature_verified: Literal[True] = True


class L2RestoreCheckpointError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _DuplicateJsonKey(ValueError):
    pass


def _strict_json_object(value: str | bytes) -> dict[str, object]:
    if isinstance(value, bytes):
        try:
            text = value.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise L2RestoreCheckpointError(
                "RESTORE_CHECKPOINT_JSON_INVALID"
            ) from exc
    elif isinstance(value, str):
        text = value
    else:
        raise TypeError("checkpoint JSON must be str or bytes")

    def object_pairs(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise _DuplicateJsonKey(key)
            result[key] = item
        return result

    def reject_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON value: {value}")

    try:
        document = json.loads(
            text,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except _DuplicateJsonKey as exc:
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_DUPLICATE_JSON_KEY"
        ) from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise L2RestoreCheckpointError("RESTORE_CHECKPOINT_JSON_INVALID") from exc
    if not isinstance(document, dict):
        raise L2RestoreCheckpointError("RESTORE_CHECKPOINT_JSON_INVALID")
    return document


def load_signed_l2_restore_checkpoint_json(
    value: str | bytes,
) -> SignedL2RestoreCheckpoint:
    """Parse a strict JSON envelope without duplicate-member ambiguity."""

    document = _strict_json_object(value)
    try:
        return SignedL2RestoreCheckpoint.model_validate(document)
    except ValidationError as exc:
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_ENVELOPE_INVALID"
        ) from exc


def l2_restore_checkpoint_signing_payload(
    checkpoint: L2RestoreCheckpoint,
    *,
    algorithm: Literal["Ed25519"] = "Ed25519",
) -> bytes:
    """Return the domain-separated canonical bytes an authority authenticates."""

    if not isinstance(checkpoint, L2RestoreCheckpoint):
        raise TypeError("checkpoint must be an L2RestoreCheckpoint")
    if algorithm != "Ed25519":
        raise ValueError("unsupported checkpoint signature algorithm")
    return _canonical(
        {
            "algorithm": algorithm,
            "checkpoint": checkpoint.model_dump(mode="json"),
            "payload_type": _PAYLOAD_TYPE,
            "schema_version": 1,
        }
    )


def signed_l2_restore_checkpoint_sha256(
    envelope: SignedL2RestoreCheckpoint,
) -> str:
    if not isinstance(envelope, SignedL2RestoreCheckpoint):
        raise TypeError("envelope must be a SignedL2RestoreCheckpoint")
    return _sha256(_canonical(envelope.model_dump(mode="json")))


def _checkpoint_sha256(checkpoint: L2RestoreCheckpoint) -> str:
    return _sha256(_canonical(checkpoint.model_dump(mode="json")))


def _require_policy_integer(name: str, value: int, *, positive: bool) -> int:
    minimum = 1 if positive else 0
    if type(value) is not int or value < minimum:
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a {qualifier} integer")
    return value


def _trusted_key(
    trusted_keys: Mapping[
        tuple[str, str], TrustedL2RestoreCheckpointKey
    ] | None,
    *,
    issuer: str,
    key_id: str,
) -> TrustedL2RestoreCheckpointKey:
    if not trusted_keys:
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_TRUST_REGISTRY_EMPTY"
        )
    if not isinstance(trusted_keys, Mapping):
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_TRUST_REGISTRY_INVALID"
        )
    for identity, key in trusted_keys.items():
        if (
            not isinstance(identity, tuple)
            or len(identity) != 2
            or any(not isinstance(item, str) or not item for item in identity)
            or not isinstance(key, TrustedL2RestoreCheckpointKey)
            or identity != (key.issuer, key.key_id)
        ):
            raise L2RestoreCheckpointError(
                "RESTORE_CHECKPOINT_TRUST_REGISTRY_INVALID"
            )
    key = trusted_keys.get((issuer, key_id))
    if key is None:
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_TRUSTED_KEY_NOT_FOUND"
        )
    return key


def verify_signed_l2_restore_checkpoint(
    envelope: SignedL2RestoreCheckpoint,
    *,
    trusted_keys: Mapping[
        tuple[str, str], TrustedL2RestoreCheckpointKey
    ] | None = None,
    expected_local_head: L2RestoreHistoryHead,
    required_issuer: str,
    required_key_id: str,
    now_ms: int,
    max_future_skew_ms: int,
    max_seal_age_ms: int,
    minimum_retention_window_ms: int,
) -> VerifiedL2RestoreCheckpoint:
    """Verify signature, identity, local head, freshness, and retention."""

    if not isinstance(envelope, SignedL2RestoreCheckpoint):
        raise TypeError("envelope must be a SignedL2RestoreCheckpoint")
    if not isinstance(expected_local_head, L2RestoreHistoryHead):
        raise TypeError("expected_local_head must be an L2RestoreHistoryHead")
    if not isinstance(required_issuer, str) or not required_issuer.strip():
        raise ValueError("required_issuer must be a non-empty string")
    if not isinstance(required_key_id, str) or not required_key_id.strip():
        raise ValueError("required_key_id must be a non-empty string")
    now_ms = _require_policy_integer("now_ms", now_ms, positive=False)
    max_future_skew_ms = _require_policy_integer(
        "max_future_skew_ms",
        max_future_skew_ms,
        positive=False,
    )
    max_seal_age_ms = _require_policy_integer(
        "max_seal_age_ms",
        max_seal_age_ms,
        positive=True,
    )
    minimum_retention_window_ms = _require_policy_integer(
        "minimum_retention_window_ms",
        minimum_retention_window_ms,
        positive=True,
    )

    checkpoint = envelope.checkpoint
    if checkpoint.issuer != required_issuer:
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_ISSUER_MISMATCH"
        )
    if checkpoint.key_id != required_key_id:
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_KEY_ID_MISMATCH"
        )
    actual_head = L2RestoreHistoryHead(
        store_kind=checkpoint.store_kind,
        store_schema_version=checkpoint.store_schema_version,
        event_count=checkpoint.event_count,
        head_seq=checkpoint.head_seq,
        head_hash=checkpoint.head_hash,
    )
    if actual_head != expected_local_head:
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_LOCAL_HEAD_MISMATCH"
        )

    key = _trusted_key(
        trusted_keys,
        issuer=checkpoint.issuer,
        key_id=checkpoint.key_id,
    )
    if key.status != "ACTIVE":
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_TRUSTED_KEY_REVOKED"
        )
    if key.algorithm != envelope.algorithm:
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_SIGNATURE_ALGORITHM_MISMATCH"
        )
    public_key_bytes = _decode_base64url(
        key.public_key_base64url,
        expected_bytes=32,
    )
    public_key_sha256 = _sha256(public_key_bytes)
    if public_key_sha256 != key.public_key_sha256:
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_PUBLIC_KEY_MISMATCH"
        )
    try:
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            _decode_base64url(
                envelope.signature_base64url,
                expected_bytes=64,
            ),
            l2_restore_checkpoint_signing_payload(
                checkpoint,
                algorithm=envelope.algorithm,
            ),
        )
    except (InvalidSignature, ValueError) as exc:
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_SIGNATURE_INVALID"
        ) from exc

    if checkpoint.sealed_at_ms > now_ms + max_future_skew_ms:
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_FROM_FUTURE"
        )
    if now_ms - checkpoint.sealed_at_ms > max_seal_age_ms:
        raise L2RestoreCheckpointError("RESTORE_CHECKPOINT_STALE")
    if (
        checkpoint.retention_until_ms
        < max(now_ms, checkpoint.sealed_at_ms)
        + minimum_retention_window_ms
    ):
        raise L2RestoreCheckpointError(
            "RESTORE_CHECKPOINT_RETENTION_INSUFFICIENT"
        )

    return VerifiedL2RestoreCheckpoint(
        issuer=checkpoint.issuer,
        key_id=checkpoint.key_id,
        public_key_sha256=public_key_sha256,
        envelope_sha256=signed_l2_restore_checkpoint_sha256(envelope),
        checkpoint_sha256=_checkpoint_sha256(checkpoint),
        store_kind=checkpoint.store_kind,
        store_schema_version=checkpoint.store_schema_version,
        event_count=checkpoint.event_count,
        head_seq=checkpoint.head_seq,
        head_hash=checkpoint.head_hash,
        sealed_at_ms=checkpoint.sealed_at_ms,
        object_version_id=checkpoint.object_version_id,
        retention_until_ms=checkpoint.retention_until_ms,
    )


__all__ = [
    "L2RestoreCheckpoint",
    "L2RestoreCheckpointError",
    "L2RestoreHistoryHead",
    "SignedL2RestoreCheckpoint",
    "TrustedL2RestoreCheckpointKey",
    "VerifiedL2RestoreCheckpoint",
    "l2_restore_checkpoint_signing_payload",
    "load_signed_l2_restore_checkpoint_json",
    "signed_l2_restore_checkpoint_sha256",
    "verify_signed_l2_restore_checkpoint",
]
