"""Cryptographically Bound Human Mandate Envelope & Replay Defense.

Addresses the institutional authority gap where a self-reported `approved_by`
string cannot prove that a real human operator reviewed and authorized an exact
strategy + notional envelope + expiry window.

Design guarantees (fail-closed):
  - Deterministic canonical SHA-256 digest over all binding fields.
  - Strict `signer_actor_kind == "HUMAN"` enforcement (agents/systems rejected).
  - Single-use nonce registry (`NonceRegistry`) prevents mandate replay.
  - Pluggable `ExternalSignatureVerifier` protocol for out-of-band hardware/Ed25519
    human key custody (e.g., YubiKey / offline KMS / hardware wallet on DGX Spark).
  - If `verifier is None`, verification ALWAYS fails closed (`verifier_not_integrated`).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Mapping, Optional, Protocol, Tuple


class ExternalSignatureVerifier(Protocol):
    """Protocol implemented by an independently controlled cryptographic verifier."""

    def verify(self, *, key_id: str, payload_sha256: str, signature_hex: str) -> bool:
        """Return True iff `signature_hex` is a valid signature by `key_id` over `payload_sha256`."""
        ...


def _is_finite_positive(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) > 0.0


@dataclass(frozen=True)
class MandateEnvelopePayload:
    mandate_id: str
    strategy_id: str
    strategy_version_hash: str
    venue: str
    exchange: str
    allowed_instruments: Tuple[str, ...]
    max_single_order_notional: float
    max_gross_notional: float
    max_net_notional: float
    per_instrument_max_notional: Tuple[Tuple[str, float], ...]
    issued_at_ms: int
    expires_at_ms: int
    nonce: str

    def canonical_dict(self) -> dict[str, object]:
        return {
            "mandate_id": self.mandate_id,
            "strategy_id": self.strategy_id,
            "strategy_version_hash": self.strategy_version_hash,
            "venue": self.venue,
            "exchange": self.exchange,
            "allowed_instruments": list(sorted(self.allowed_instruments)),
            "max_single_order_notional": float(self.max_single_order_notional),
            "max_gross_notional": float(self.max_gross_notional),
            "max_net_notional": float(self.max_net_notional),
            "per_instrument_max_notional": [
                [inst, float(cap)] for inst, cap in sorted(self.per_instrument_max_notional)
            ],
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "nonce": self.nonce,
        }

    @property
    def payload_sha256(self) -> str:
        raw = json.dumps(
            self.canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class SignedMandateEnvelope:
    payload: MandateEnvelopePayload
    claimed_payload_sha256: str
    signer_id: str
    signer_actor_kind: str                 # Must be "HUMAN"
    key_id: str
    signature_hex: str


@dataclass(frozen=True)
class EnvelopeVerificationResult:
    valid: bool
    reasons: Tuple[str, ...]
    computed_sha256: str


class NonceRegistry:
    """In-memory single-use nonce tracker to block mandate replay attacks."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def is_used(self, nonce: str) -> bool:
        return nonce in self._used

    def consume(self, nonce: str) -> bool:
        """Record nonce as consumed. Returns False if already used."""
        if nonce in self._used:
            return False
        self._used.add(nonce)
        return True


def verify_mandate_envelope(
    envelope: SignedMandateEnvelope,
    *,
    now_ms: int,
    expected_venue: str,
    expected_exchange: str,
    verifier: Optional[ExternalSignatureVerifier] = None,
    nonce_registry: Optional[NonceRegistry] = None,
    consume_nonce: bool = False,
) -> EnvelopeVerificationResult:
    """Fail-closed verification of a human-signed MandateEnvelope."""
    try:
        p = envelope.payload
        computed = p.payload_sha256
    except Exception as exc:
        return EnvelopeVerificationResult(False, (f"payload_error:{type(exc).__name__}",), "")

    reasons: list[str] = []

    if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
        reasons.append("invalid_now_ms")
    if isinstance(p.issued_at_ms, bool) or not isinstance(p.issued_at_ms, int) or p.issued_at_ms < 0:
        reasons.append("invalid_issued_at_ms")
    if isinstance(p.expires_at_ms, bool) or not isinstance(p.expires_at_ms, int) or p.expires_at_ms <= p.issued_at_ms:
        reasons.append("invalid_expires_at_ms")
    elif not isinstance(now_ms, bool) and isinstance(now_ms, int):
        if now_ms < p.issued_at_ms:
            reasons.append("mandate_not_yet_valid")
        if now_ms > p.expires_at_ms:
            reasons.append("mandate_expired")

    if not isinstance(p.mandate_id, str) or not p.mandate_id.strip():
        reasons.append("mandate_id_missing")
    if not isinstance(p.strategy_id, str) or not p.strategy_id.strip():
        reasons.append("strategy_id_missing")
    if not isinstance(p.strategy_version_hash, str) or not p.strategy_version_hash.strip():
        reasons.append("strategy_version_hash_missing")
    if not p.allowed_instruments:
        reasons.append("allowed_instruments_empty")
    if p.venue != expected_venue:
        reasons.append("venue_mismatch")
    if p.exchange != expected_exchange:
        reasons.append("exchange_mismatch")

    for name, val in (
        ("max_single_order_notional", p.max_single_order_notional),
        ("max_gross_notional", p.max_gross_notional),
        ("max_net_notional", p.max_net_notional),
    ):
        if not _is_finite_positive(val):
            reasons.append(f"invalid_{name}")

    if _is_finite_positive(p.max_single_order_notional) and _is_finite_positive(p.max_gross_notional):
        if float(p.max_single_order_notional) > float(p.max_gross_notional):
            reasons.append("single_order_exceeds_gross")
    if _is_finite_positive(p.max_net_notional) and _is_finite_positive(p.max_gross_notional):
        if float(p.max_net_notional) > float(p.max_gross_notional):
            reasons.append("net_exceeds_gross")

    for inst, cap in p.per_instrument_max_notional:
        if inst not in p.allowed_instruments:
            reasons.append(f"cap_instrument_not_allowed:{inst}")
        if not _is_finite_positive(cap):
            reasons.append(f"invalid_per_instrument_cap:{inst}")

    if not isinstance(p.nonce, str) or len(p.nonce.strip()) < 16:
        reasons.append("nonce_too_short")
    elif nonce_registry is not None and nonce_registry.is_used(p.nonce):
        reasons.append("nonce_replayed")

    if envelope.claimed_payload_sha256 != computed:
        reasons.append("payload_digest_tampered")

    if envelope.signer_actor_kind != "HUMAN":
        reasons.append(f"non_human_signer_rejected:{envelope.signer_actor_kind}")
    if not isinstance(envelope.signer_id, str) or not envelope.signer_id.strip():
        reasons.append("signer_id_missing")
    if not isinstance(envelope.key_id, str) or not envelope.key_id.strip():
        reasons.append("key_id_missing")
    if not isinstance(envelope.signature_hex, str) or len(envelope.signature_hex.strip()) < 16:
        reasons.append("signature_missing")

    if verifier is None:
        reasons.append("external_signature_verifier_not_integrated")
    elif not reasons:
        try:
            sig_ok = bool(
                verifier.verify(
                    key_id=envelope.key_id,
                    payload_sha256=computed,
                    signature_hex=envelope.signature_hex,
                )
            )
            if not sig_ok:
                reasons.append("signature_verification_failed")
        except Exception as exc:
            reasons.append(f"verifier_error:{type(exc).__name__}")

    if not reasons and consume_nonce and nonce_registry is not None:
        if not nonce_registry.consume(p.nonce):
            reasons.append("nonce_replayed")

    return EnvelopeVerificationResult(
        valid=len(reasons) == 0,
        reasons=tuple(reasons),
        computed_sha256=computed,
    )
