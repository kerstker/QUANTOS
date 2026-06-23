"""Execution boundary (Phase 10) — DRY-RUN ONLY, fail-closed admission.

The last gate before the (simulated) execution path. It does NOT trust that the
upstream said "approved" — it INDEPENDENTLY re-verifies the pre-trade risk ref
against the gate (closing the Phase-8 review's deferred boundary-provenance gap),
requires an APPROVED RiskDecision, and enforces idempotency. Anything else is
denied. No broker SDK, no order submission.
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
        # independently re-verify provenance — a forged/empty/mismatched ref is denied
        if not self.gate.verify_ref(ref, intent, ctx):
            reasons.append("REF_UNVERIFIED")
        fp = ctx.fingerprint(intent)
        if fp in self._seen:
            reasons.append("DUPLICATE")
        if reasons:
            return AdmitDecision(admitted=False, reasons=tuple(reasons))
        self._seen.add(fp)                     # commit only on admit
        return AdmitDecision(admitted=True)
