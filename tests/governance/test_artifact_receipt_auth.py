from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from pydantic import ValidationError

from services.governance.artifact_admission import ArtifactVerificationReceipt
from services.governance.artifact_receipt_auth import (
    ArtifactReceiptAuthenticationError,
    ReceiptAuthorityKey,
    SignedArtifactVerificationReceipt,
    artifact_receipt_signing_payload,
    signed_artifact_receipt_sha256,
    verify_signed_artifact_receipt,
)


AUTHORITY_ID = "quantos-external-receipt-authority"
KEY_ID = "receipt-key-2026-q3"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _receipt() -> ArtifactVerificationReceipt:
    return ArtifactVerificationReceipt(
        schema_version=1,
        receipt_id="receipt-release-1",
        verifier_id="sigstore-python-4.4.0-offline",
        artifact_sha256="a" * 64,
        bundle_sha256="b" * 64,
        trusted_root_sha256="c" * 64,
        certificate_sha256="d" * 64,
        verified_at_ms=1_700_000_000_000,
        transparency_checkpoint_at_ms=1_699_999_990_000,
        revocation_status="GOOD",
        revocation_checked_at_ms=1_699_999_995_000,
        revocation_source="external-fulcio-status-v1",
    )


def _signed_envelope():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    receipt = _receipt()
    signature = private_key.sign(
        artifact_receipt_signing_payload(
            authority_id=AUTHORITY_ID,
            key_id=KEY_ID,
            algorithm="Ed25519",
            receipt=receipt,
        )
    )
    authority_key = ReceiptAuthorityKey(
        schema_version=1,
        authority_id=AUTHORITY_ID,
        key_id=KEY_ID,
        algorithm="Ed25519",
        public_key_base64url=_b64url(public_key),
        public_key_sha256=hashlib.sha256(public_key).hexdigest(),
        status="ACTIVE",
    )
    envelope = SignedArtifactVerificationReceipt(
        schema_version=1,
        authority_id=AUTHORITY_ID,
        key_id=KEY_ID,
        algorithm="Ed25519",
        receipt=receipt,
        signature_base64url=_b64url(signature),
    )
    return envelope, authority_key


def test_valid_external_ed25519_receipt_returns_digest_bound_facts() -> None:
    envelope, authority_key = _signed_envelope()

    facts = verify_signed_artifact_receipt(
        envelope,
        authority_key,
        required_authority_id=AUTHORITY_ID,
        required_key_id=KEY_ID,
        required_public_key_sha256=authority_key.public_key_sha256,
    )

    assert facts.signature_verified is True
    assert facts.authority_id == AUTHORITY_ID
    assert facts.key_id == KEY_ID
    assert facts.public_key_sha256 == authority_key.public_key_sha256
    assert facts.envelope_sha256 == signed_artifact_receipt_sha256(envelope)
    assert len(facts.receipt_sha256) == 64


@pytest.mark.parametrize(
    ("case_id", "expected"),
    [
        ("authority", "RECEIPT_AUTHORITY_MISMATCH"),
        ("key", "RECEIPT_KEY_ID_MISMATCH"),
        ("registry", "RECEIPT_AUTHORITY_KEY_MISMATCH"),
        ("revoked", "RECEIPT_AUTHORITY_KEY_REVOKED"),
        ("digest", "RECEIPT_PUBLIC_KEY_MISMATCH"),
    ],
)
def test_authority_key_policy_mismatches_fail_closed(
    case_id: str,
    expected: str,
) -> None:
    envelope, authority_key = _signed_envelope()
    required_authority = AUTHORITY_ID
    required_key = KEY_ID
    required_digest = authority_key.public_key_sha256
    if case_id == "authority":
        required_authority = "other-authority"
    elif case_id == "key":
        required_key = "other-key"
    elif case_id == "registry":
        authority_key = authority_key.model_copy(
            update={"authority_id": "other-authority"}
        )
    elif case_id == "revoked":
        authority_key = authority_key.model_copy(update={"status": "REVOKED"})
    else:
        required_digest = "f" * 64

    with pytest.raises(ArtifactReceiptAuthenticationError) as error:
        verify_signed_artifact_receipt(
            envelope,
            authority_key,
            required_authority_id=required_authority,
            required_key_id=required_key,
            required_public_key_sha256=required_digest,
        )

    assert error.value.reason_code == expected


def test_receipt_or_signature_tampering_is_rejected() -> None:
    envelope, authority_key = _signed_envelope()
    tampered_receipt = envelope.model_copy(
        update={
            "receipt": envelope.receipt.model_copy(
                update={"revocation_status": "REVOKED"}
            )
        }
    )
    signature_bytes = bytearray(
        base64.urlsafe_b64decode(envelope.signature_base64url + "==")
    )
    signature_bytes[0] ^= 1
    tampered_signature = envelope.model_copy(
        update={"signature_base64url": _b64url(bytes(signature_bytes))}
    )

    for candidate in (tampered_receipt, tampered_signature):
        with pytest.raises(ArtifactReceiptAuthenticationError) as error:
            verify_signed_artifact_receipt(
                candidate,
                authority_key,
                required_authority_id=AUTHORITY_ID,
                required_key_id=KEY_ID,
                required_public_key_sha256=authority_key.public_key_sha256,
            )
        assert error.value.reason_code == "RECEIPT_SIGNATURE_INVALID"


@pytest.mark.parametrize(
    "field_update",
    [
        {"signature_base64url": "abc="},
        {"signature_base64url": "abc+"},
        {"signature_base64url": "AA"},
        {"algorithm": "RSA"},
        {"extra": "forbidden"},
    ],
)
def test_envelope_contract_rejects_noncanonical_or_unsupported_input(
    field_update: dict[str, object],
) -> None:
    envelope, _ = _signed_envelope()
    raw = envelope.model_dump(mode="python")
    raw.update(field_update)

    with pytest.raises(ValidationError):
        SignedArtifactVerificationReceipt.model_validate(raw)


def test_signing_payload_and_envelope_digest_are_deterministic() -> None:
    envelope, _ = _signed_envelope()

    first_payload = artifact_receipt_signing_payload(
        authority_id=envelope.authority_id,
        key_id=envelope.key_id,
        algorithm=envelope.algorithm,
        receipt=envelope.receipt,
    )
    second_payload = artifact_receipt_signing_payload(
        authority_id=envelope.authority_id,
        key_id=envelope.key_id,
        algorithm=envelope.algorithm,
        receipt=envelope.receipt,
    )

    assert first_payload == second_payload
    assert signed_artifact_receipt_sha256(envelope) == (
        signed_artifact_receipt_sha256(envelope)
    )
