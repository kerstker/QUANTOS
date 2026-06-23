"""Pre-trade risk gate (Phase 8) — the fail-closed gatekeeper.

evaluate(intent, ctx) -> RiskDecision:
  * refuses to even construct under any live/AI-direct/human-approval-off config
    (assert_research_safe at __init__);
  * binds ONE SafetyConfig + the authoritative kill-switch state into the context
    (a caller-supplied config/state cannot diverge — review P1-5);
  * runs the WHOLE CHECK_REGISTRY, each wrapped so any exception -> UNKNOWN_RISK_ERROR,
    collecting+deduping ALL reasons in registry order (no masking short-circuit);
  * emits only APPROVED or REJECTED (never APPROVED_WITH_LIMITS / NEEDS_MANUAL_REVIEW —
    review P0-2); builds the canonical RiskDecision (its invariant is a second backstop);
  * audits the decision, and if the audit write fails, REJECTS — no un-audited
    approval escapes (review P0-3);
  * an outer try/except guarantees the engine can NEVER return APPROVED on error.

approval_ref() yields a usable downstream token ONLY for an APPROVED decision; a
REJECTED decision yields "", so the dry-run boundary's PRE_TRADE_MISSING check
fails it closed (review P0-1).
"""
from __future__ import annotations

from typing import Optional

from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS, SafetyConfig
from quantos.contracts.rejection import RejectionReason
from quantos.contracts.risk import RiskDecision, RiskDecisionType

from .audit import RiskAuditTrail
from .checks import CHECK_REGISTRY
from .config import RiskLimits
from .kill_switch import KillSwitch

_REJECT_UNKNOWN = (RejectionReason.UNKNOWN_RISK_ERROR,)


def _reject(reasons) -> RiskDecision:
    return RiskDecision(decision=RiskDecisionType.REJECTED, reasons=tuple(reasons))


def approval_ref(decision: RiskDecision, decision_id: str) -> str:
    """Downstream-usable risk ref — ONLY for an APPROVED decision (review P0-1)."""
    return decision_id if (decision.decision == RiskDecisionType.APPROVED) else ""


class PreTradeRiskGate:
    def __init__(self, limits: RiskLimits, kill_switch: Optional[KillSwitch] = None,
                 safety_config: SafetyConfig = RESEARCH_SAFE_DEFAULTS,
                 audit_trail: Optional[RiskAuditTrail] = None):
        safety_config.assert_research_safe()         # refuse to build an unsafe gate
        self.limits = limits
        self.kill_switch = kill_switch or KillSwitch()
        self.safety = safety_config
        self.audit = audit_trail or RiskAuditTrail()

    def evaluate(self, intent, ctx) -> RiskDecision:
        try:
            # bind authoritative safety + kill-switch state (caller's cannot diverge)
            ctx = ctx.model_copy(update={"kill_switch": self.kill_switch.state, "safety_config": self.safety})

            reasons: list[RejectionReason] = []
            breached: list[str] = []
            for name, _stage, fn in CHECK_REGISTRY:
                try:
                    rs = fn(intent, ctx, self.limits)
                except Exception:
                    rs = _REJECT_UNKNOWN
                if rs:
                    breached.append(name)
                    reasons.extend(rs)

            # dedup, preserving registry order
            seen, ordered = set(), []
            for r in reasons:
                if r not in seen:
                    seen.add(r)
                    ordered.append(r)

            decision = _reject(ordered) if ordered else RiskDecision(decision=RiskDecisionType.APPROVED)
            # P0-2: only APPROVED/REJECTED may leave the gate
            if decision.decision not in (RiskDecisionType.APPROVED, RiskDecisionType.REJECTED):
                decision = _reject(_REJECT_UNKNOWN)

            # P0-3: audit-then-or-reject — an audit failure must not let APPROVED escape
            try:
                self.audit.record(decision, intent, ctx, self.limits, breached)
            except Exception:
                return _reject(_REJECT_UNKNOWN)

            return decision
        except Exception:
            return _reject(_REJECT_UNKNOWN)        # the engine can NEVER approve on error
