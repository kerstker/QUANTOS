"""Risk-decision audit trail (Phase 8) — append-only, hash-chained, tamper-evident.

Every decision (and the granular breached-check names, since several checks share
a canonical reason — review P1-1) is recorded as a chained AuditEvent. An
audit-write failure must force the gate to REJECT (no un-audited approval escapes).
"""
from __future__ import annotations

from quantos.contracts.governance import AuditEvent


class RiskAuditTrail:
    def __init__(self):
        self.events: list[AuditEvent] = []

    def record(self, decision, intent, ctx, limits, breached: list[str]) -> AuditEvent:
        prev = self.events[-1].entry_hash if self.events else ""
        ev = AuditEvent(
            event_id=f"risk-{len(self.events) + 1}",
            actor="pre_trade_risk_gate",
            action="RISK_DECISION",
            target=intent.instrument_id,
            timestamp=ctx.now,
            metadata={
                "decision": decision.decision.value,
                "reasons": ",".join(r.value for r in decision.reasons),
                "breached": ",".join(breached),
                "request_hash": ctx.request_hash(intent),
                "limits_hash": limits.limits_hash(),
            },
            prev_hash=prev,
        )
        self.events.append(ev)
        return ev

    def chain_valid(self) -> bool:
        prev = ""
        for ev in self.events:
            if ev.prev_hash != prev:
                return False
            prev = ev.entry_hash
        return True
