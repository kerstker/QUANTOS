"""QUANTOS canonical domain contracts (Phase 1).

Single source of truth for the safety/risk/order vocabulary shared across
services. Import from here, not from per-service local copies.
"""
from .config import RESEARCH_SAFE_DEFAULTS, SafetyConfig, SafetyViolation
from .hashing import short_hash, stable_hash
from .order import (
    ALLOWED_TRANSITIONS,
    InvalidOrderTransition,
    OrderState,
    TERMINAL_STATES,
    assert_transition,
    can_transition,
    is_terminal,
)
from .rejection import LEGACY_DRY_RUN_CROSSWALK, RejectionReason, from_legacy
from .risk import RiskDecision, RiskDecisionType
from .trading import ExecutionReport, Fill, Order, OrderIntent, OrderSide, OrderType

__all__ = [
    "SafetyConfig", "SafetyViolation", "RESEARCH_SAFE_DEFAULTS",
    "RejectionReason", "LEGACY_DRY_RUN_CROSSWALK", "from_legacy",
    "OrderState", "ALLOWED_TRANSITIONS", "TERMINAL_STATES",
    "can_transition", "assert_transition", "is_terminal", "InvalidOrderTransition",
    "RiskDecision", "RiskDecisionType",
    "OrderIntent", "Order", "Fill", "ExecutionReport", "OrderSide", "OrderType",
    "stable_hash", "short_hash",
]
