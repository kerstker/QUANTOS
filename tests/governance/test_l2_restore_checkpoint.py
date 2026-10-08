from __future__ import annotations

import ast
import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from services.governance.l2_restore_checkpoint import (
    L2RestoreCheckpoint,
    L2RestoreCheckpointError,
    L2RestoreHistoryHead,
    SignedL2RestoreCheckpoint,
    TrustedL2RestoreCheckpointKey,
    l2_restore_checkpoint_signing_payload,
    load_signed_l2_restore_checkpoint_json,
    signed_l2_restore_checkpoint_sha256,
    verify_signed_l2_restore_checkpoint,
)


NOW_MS = 1_784_044_800_000
ISSUER = "operator-owned-restore-checkpoint-authority"
KEY_ID = "restore-checkpoint-2026-q3"
STORE_KIND = "QUANTOS_L2_RESTORE_HISTORY_AUTHORITY"
STORE_SCHEMA_VERSION = 1
EVENT_COUNT = 64
HEAD_HASH = "a" * 64
MAX_FUTURE_SKEW_MS = 300_000
MAX_SEAL_AGE_MS = 86_400_000
MINIMUM_RETENTION_WINDOW_MS = 90 * 86_400_000


def _private_key(seed: int = 1) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes([seed]) * 32)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _trusted_key(
    *,
    private_key: Ed25519PrivateKey | None = None,
    issuer: str = ISSUER,
    key_id: str = KEY_ID,
    status: str = "ACTIVE",
    public_key_sha256: str | None = None,
) -> TrustedL2RestoreCheckpointKey:
    key = private_key or _private_key()
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return TrustedL2RestoreCheckpointKey(
        issuer=issuer,
        key_id=key_id,
        public_key_base64url=_base64url(public),
        public_key_sha256=public_key_sha256 or hashlib.sha256(public).hexdigest(),
        status=status,
    )


def _head(**overrides: object) -> L2RestoreHistoryHead:
    values: dict[str, object] = {
        "store_kind": STORE_KIND,
        "store_schema_version": STORE_SCHEMA_VERSION,
        "event_count": EVENT_COUNT,
        "head_seq": EVENT_COUNT,
        "head_hash": HEAD_HASH,
    }
    values.update(overrides)
    return L2RestoreHistoryHead(**values)  # type: ignore[arg-type]


def _checkpoint(**overrides: object) -> L2RestoreCheckpoint:
    values: dict[str, object] = {
        "store_kind": STORE_KIND,
        "store_schema_version": STORE_SCHEMA_VERSION,
        "event_count": EVENT_COUNT,
        "head_seq": EVENT_COUNT,
        "head_hash": HEAD_HASH,
        "sealed_at_ms": NOW_MS - 1_000,
        "object_version_id": "worm-version-000042",
        "retention_until_ms": NOW_MS + MINIMUM_RETENTION_WINDOW_MS,
        "issuer": ISSUER,
        "key_id": KEY_ID,
    }
    values.update(overrides)
    return L2RestoreCheckpoint(**values)  # type: ignore[arg-type]


def _envelope(
    checkpoint: L2RestoreCheckpoint | None = None,
    *,
    private_key: Ed25519PrivateKey | None = None,
) -> SignedL2RestoreCheckpoint:
    resolved_checkpoint = checkpoint or _checkpoint()
    key = private_key or _private_key()
    signature = key.sign(
        l2_restore_checkpoint_signing_payload(resolved_checkpoint)
    )
    return SignedL2RestoreCheckpoint(
        checkpoint=resolved_checkpoint,
        signature_base64url=_base64url(signature),
    )


def _registry(
    key: TrustedL2RestoreCheckpointKey | None = None,
) -> dict[tuple[str, str], TrustedL2RestoreCheckpointKey]:
    resolved = key or _trusted_key()
    return {(resolved.issuer, resolved.key_id): resolved}


def _verify(
    envelope: SignedL2RestoreCheckpoint,
    *,
    trusted_keys: dict[
        tuple[str, str], TrustedL2RestoreCheckpointKey
    ] | None = None,
    expected_local_head: L2RestoreHistoryHead | None = None,
    required_issuer: str = ISSUER,
    required_key_id: str = KEY_ID,
    now_ms: int = NOW_MS,
):
    return verify_signed_l2_restore_checkpoint(
        envelope,
        trusted_keys=_registry() if trusted_keys is None else trusted_keys,
        expected_local_head=expected_local_head or _head(),
        required_issuer=required_issuer,
        required_key_id=required_key_id,
        now_ms=now_ms,
        max_future_skew_ms=MAX_FUTURE_SKEW_MS,
        max_seal_age_ms=MAX_SEAL_AGE_MS,
        minimum_retention_window_ms=MINIMUM_RETENTION_WINDOW_MS,
    )


def _reason(error: pytest.ExceptionInfo[L2RestoreCheckpointError]) -> str:
    return error.value.reason_code


def test_valid_checkpoint_verifies_exact_external_head_and_is_frozen() -> None:
    checkpoint = _checkpoint()
    envelope = _envelope(checkpoint)

    verified = _verify(envelope)

    assert verified.signature_verified is True
    assert verified.issuer == ISSUER
    assert verified.key_id == KEY_ID
    assert verified.store_kind == STORE_KIND
    assert verified.store_schema_version == STORE_SCHEMA_VERSION
    assert verified.event_count == EVENT_COUNT
    assert verified.head_seq == EVENT_COUNT
    assert verified.head_hash == HEAD_HASH
    assert verified.sealed_at_ms == NOW_MS - 1_000
    assert verified.object_version_id == "worm-version-000042"
    assert verified.retention_until_ms == NOW_MS + MINIMUM_RETENTION_WINDOW_MS
    assert verified.envelope_sha256 == signed_l2_restore_checkpoint_sha256(
        envelope
    )
    assert len(verified.public_key_sha256) == 64
    assert l2_restore_checkpoint_signing_payload(
        checkpoint
    ) == l2_restore_checkpoint_signing_payload(checkpoint)
    with pytest.raises(ValidationError):
        checkpoint.head_hash = "b" * 64
    with pytest.raises(ValidationError):
        envelope.signature_base64url = "x"


def test_empty_trusted_registry_is_rejected_by_default() -> None:
    envelope = _envelope()

    with pytest.raises(L2RestoreCheckpointError) as error:
        verify_signed_l2_restore_checkpoint(
            envelope,
            expected_local_head=_head(),
            required_issuer=ISSUER,
            required_key_id=KEY_ID,
            now_ms=NOW_MS,
            max_future_skew_ms=MAX_FUTURE_SKEW_MS,
            max_seal_age_ms=MAX_SEAL_AGE_MS,
            minimum_retention_window_ms=MINIMUM_RETENTION_WINDOW_MS,
        )

    assert _reason(error) == "RESTORE_CHECKPOINT_TRUST_REGISTRY_EMPTY"


def test_checkpoint_mutated_after_signing_is_rejected() -> None:
    original = _checkpoint()
    envelope = _envelope(original)
    tampered = SignedL2RestoreCheckpoint(
        checkpoint=_checkpoint(object_version_id="worm-version-tampered"),
        signature_base64url=envelope.signature_base64url,
    )

    with pytest.raises(L2RestoreCheckpointError) as error:
        _verify(tampered)

    assert _reason(error) == "RESTORE_CHECKPOINT_SIGNATURE_INVALID"


def test_signature_from_another_key_is_rejected() -> None:
    envelope = _envelope(private_key=_private_key(2))

    with pytest.raises(L2RestoreCheckpointError) as error:
        _verify(envelope)

    assert _reason(error) == "RESTORE_CHECKPOINT_SIGNATURE_INVALID"


@pytest.mark.parametrize(
    ("checkpoint_overrides", "verify_overrides", "reason_code"),
    [
        (
            {"issuer": "untrusted-issuer"},
            {},
            "RESTORE_CHECKPOINT_ISSUER_MISMATCH",
        ),
        (
            {"key_id": "unknown-key"},
            {},
            "RESTORE_CHECKPOINT_KEY_ID_MISMATCH",
        ),
        (
            {"store_kind": "OTHER_STORE"},
            {},
            "RESTORE_CHECKPOINT_LOCAL_HEAD_MISMATCH",
        ),
        (
            {"store_schema_version": 2},
            {},
            "RESTORE_CHECKPOINT_LOCAL_HEAD_MISMATCH",
        ),
        (
            {"event_count": EVENT_COUNT + 1, "head_seq": EVENT_COUNT + 1},
            {},
            "RESTORE_CHECKPOINT_LOCAL_HEAD_MISMATCH",
        ),
        (
            {"head_hash": "b" * 64},
            {},
            "RESTORE_CHECKPOINT_LOCAL_HEAD_MISMATCH",
        ),
    ],
)
def test_checkpoint_must_bind_required_identity_and_exact_local_head(
    checkpoint_overrides: dict[str, object],
    verify_overrides: dict[str, object],
    reason_code: str,
) -> None:
    envelope = _envelope(_checkpoint(**checkpoint_overrides))

    with pytest.raises(L2RestoreCheckpointError) as error:
        _verify(envelope, **verify_overrides)  # type: ignore[arg-type]

    assert _reason(error) == reason_code


@pytest.mark.parametrize(
    ("checkpoint_overrides", "now_ms", "reason_code"),
    [
        (
            {"sealed_at_ms": NOW_MS - MAX_SEAL_AGE_MS - 1},
            NOW_MS,
            "RESTORE_CHECKPOINT_STALE",
        ),
        (
            {"sealed_at_ms": NOW_MS + MAX_FUTURE_SKEW_MS + 1},
            NOW_MS,
            "RESTORE_CHECKPOINT_FROM_FUTURE",
        ),
        (
            {
                "retention_until_ms": NOW_MS
                + MINIMUM_RETENTION_WINDOW_MS
                - 1
            },
            NOW_MS,
            "RESTORE_CHECKPOINT_RETENTION_INSUFFICIENT",
        ),
    ],
)
def test_checkpoint_time_and_retention_policy_fail_closed(
    checkpoint_overrides: dict[str, object],
    now_ms: int,
    reason_code: str,
) -> None:
    envelope = _envelope(_checkpoint(**checkpoint_overrides))

    with pytest.raises(L2RestoreCheckpointError) as error:
        _verify(envelope, now_ms=now_ms)

    assert _reason(error) == reason_code


def test_time_policy_boundaries_are_inclusive() -> None:
    checkpoint = _checkpoint(
        sealed_at_ms=NOW_MS - MAX_SEAL_AGE_MS,
        retention_until_ms=NOW_MS + MINIMUM_RETENTION_WINDOW_MS,
    )
    assert _verify(_envelope(checkpoint)).signature_verified is True

    future_within_skew = _checkpoint(
        sealed_at_ms=NOW_MS + MAX_FUTURE_SKEW_MS,
        retention_until_ms=(
            NOW_MS
            + MAX_FUTURE_SKEW_MS
            + MINIMUM_RETENTION_WINDOW_MS
        ),
    )
    assert _verify(_envelope(future_within_skew)).signature_verified is True


def test_retention_window_covers_a_future_seal_within_clock_skew() -> None:
    checkpoint = _checkpoint(
        sealed_at_ms=NOW_MS + MAX_FUTURE_SKEW_MS,
        retention_until_ms=NOW_MS + MINIMUM_RETENTION_WINDOW_MS,
    )

    with pytest.raises(L2RestoreCheckpointError) as error:
        _verify(_envelope(checkpoint))

    assert _reason(error) == "RESTORE_CHECKPOINT_RETENTION_INSUFFICIENT"


def test_revoked_or_digest_mismatched_trusted_key_is_rejected() -> None:
    envelope = _envelope()
    revoked = _trusted_key(status="REVOKED")
    with pytest.raises(L2RestoreCheckpointError) as error:
        _verify(envelope, trusted_keys=_registry(revoked))
    assert _reason(error) == "RESTORE_CHECKPOINT_TRUSTED_KEY_REVOKED"

    wrong_digest = _trusted_key(public_key_sha256="f" * 64)
    with pytest.raises(L2RestoreCheckpointError) as error:
        _verify(envelope, trusted_keys=_registry(wrong_digest))
    assert _reason(error) == "RESTORE_CHECKPOINT_PUBLIC_KEY_MISMATCH"


def test_registry_key_must_exactly_match_its_mapping_identity() -> None:
    envelope = _envelope()
    trusted = _trusted_key()
    malformed_registry = {("different-issuer", KEY_ID): trusted}

    with pytest.raises(L2RestoreCheckpointError) as error:
        _verify(envelope, trusted_keys=malformed_registry)

    assert _reason(error) == "RESTORE_CHECKPOINT_TRUST_REGISTRY_INVALID"


def test_duplicate_json_members_are_rejected_at_any_depth() -> None:
    document = json.dumps(_envelope().model_dump(mode="json"))
    duplicate_envelope = document.replace(
        '"algorithm": "Ed25519"',
        '"algorithm": "Ed25519", "algorithm": "Ed25519"',
        1,
    )
    duplicate_checkpoint = document.replace(
        f'"head_hash": "{HEAD_HASH}"',
        f'"head_hash": "{HEAD_HASH}", "head_hash": "{HEAD_HASH}"',
        1,
    )

    for payload in (duplicate_envelope, duplicate_checkpoint):
        with pytest.raises(L2RestoreCheckpointError) as error:
            load_signed_l2_restore_checkpoint_json(payload)
        assert _reason(error) == "RESTORE_CHECKPOINT_DUPLICATE_JSON_KEY"


def test_algorithm_confusion_and_extra_fields_are_rejected() -> None:
    document = _envelope().model_dump(mode="json")
    document["algorithm"] = "HS256"
    with pytest.raises(L2RestoreCheckpointError) as error:
        load_signed_l2_restore_checkpoint_json(json.dumps(document))
    assert _reason(error) == "RESTORE_CHECKPOINT_ENVELOPE_INVALID"

    document = _envelope().model_dump(mode="json")
    document["unexpected"] = True
    with pytest.raises(L2RestoreCheckpointError) as error:
        load_signed_l2_restore_checkpoint_json(json.dumps(document))
    assert _reason(error) == "RESTORE_CHECKPOINT_ENVELOPE_INVALID"


def test_empty_object_version_and_noncanonical_signature_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _checkpoint(object_version_id="   ")
    with pytest.raises(ValidationError):
        SignedL2RestoreCheckpoint(
            checkpoint=_checkpoint(),
            signature_base64url="=" * 88,
        )


def test_json_loader_accepts_utf8_bytes_and_rejects_non_objects() -> None:
    envelope = _envelope()
    encoded = json.dumps(envelope.model_dump(mode="json")).encode("utf-8")
    assert load_signed_l2_restore_checkpoint_json(encoded) == envelope

    for payload in (b"[]", b"null", b"{not-json", b"\xff"):
        with pytest.raises(L2RestoreCheckpointError):
            load_signed_l2_restore_checkpoint_json(payload)


def test_module_is_verify_only_and_has_no_external_or_execution_capability() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "governance"
        / "l2_restore_checkpoint.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    import_roots: set[str] = set()
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_roots.add(node.module.split(".", 1)[0])
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)

    assert "Ed25519PublicKey" in source
    assert "Ed25519PrivateKey" not in source
    assert not import_roots & {
        "aiohttp",
        "http",
        "httpx",
        "requests",
        "socket",
        "urllib",
        "websocket",
        "websockets",
    }
    assert not calls & {
        "connect",
        "open",
        "place_order",
        "sign",
        "submit_order",
        "transfer",
        "withdraw",
        "write_bytes",
        "write_text",
    }
    lowered = source.lower()
    for marker in (
        "broker",
        "deploy",
        "live_trading",
        "private_key",
        "submit_order",
    ):
        assert marker not in lowered
