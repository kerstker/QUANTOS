"""Order-state bridge (Phase 8). Drives an order VALIDATING -> RISK_APPROVED /
RISK_REJECTED via the canonical state machine. An illegal transition (e.g. the
order was not in VALIDATING) raises InvalidOrderTransition, which the gate treats
as a fail-closed error. No trading-package import."""
from __future__ import annotations

from quantos.contracts.order import OrderState, assert_transition
from quantos.contracts.risk import RiskDecision


def apply_decision(order, decision: RiskDecision):
    dst = OrderState.RISK_APPROVED if decision.approved else OrderState.RISK_REJECTED
    assert_transition(order.state, dst)
    return order.model_copy(update={"state": dst})
