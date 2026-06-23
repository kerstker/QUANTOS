"""Execution boundary (Phase 10) — DRY-RUN ONLY, fail-closed admission.

The last gate before the (simulated) execution path. It does NOT trust that the
upstream said "approved": it re-derives and CONSISTENCY-checks the pre-trade risk
ref against the gate (verify_ref), requires an APPROVED RiskDecision, and enforces
idempotency. Anything else is denied. No broker SDK, no order submission.

Scope of verify_ref (audit honesty note): the ref is CONTENT-bound (a hash over
the intent + limits + a tag), NOT a cryptographic proof. It catches a forged-by-
guessing, empty, mismatched, or replayed-from-a-different-intent ref, but because
the hash is unkeyed it is NOT a trust boundary against TRUSTED in-process code,
which could recompute it — that is the documented build-time trusted-code surface,
deferred to out-of-process signed approval/decision refs before any move beyond L2.
"""
from __future__ import annotations

from dataclasses import dataclass

from quantos.contracts.risk import RiskDecision, RiskDecisionType


@dataclass(frozen=True)
class AdmitDecision:
    admitted: bool
    reasons: tuple[str, ...] = ()


class ExecutionBoundary:
    def __init__(self, gate):
        self.gate = gate                       # PreTradeRiskGate, used to re-verify the ref
        self._seen: set[str] = set()           # idempotency fingerprints

    def admit(self, intent, ctx, decision: RiskDecision, ref: str) -> AdmitDecision:
        reasons = []
        if decision is None or decision.decision != RiskDecisionType.APPROVED:
            reasons.append("NOT_APPROVED")
        # re-derive + consistency-check the content-bound ref — an empty / mismatched
        # / replayed-from-a-different-intent / guessed ref is denied (not a defense
        # against trusted in-process code that can recompute the unkeyed hash)
        if not self.gate.verify_ref(ref, intent, ctx):
            reasons.append("REF_UNVERIFIED")
        fp = ctx.fingerprint(intent)
        if fp in self._seen:
            reasons.append("DUPLICATE")
        if reasons:
            return AdmitDecision(admitted=False, reasons=tuple(reasons))
        self._seen.add(fp)                     # commit only on admit
        return AdmitDecision(admitted=True)
