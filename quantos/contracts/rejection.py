"""Canonical risk rejection reasons (blueprint §8).

This is the SINGLE authoritative RejectionReason enum. Legacy/local enums (the
dry-run adapters' RejectionReason, etc.) are mapped onto it via LEGACY_CROSSWALK
so the whole system reports one vocabulary.
"""
from __future__ import annotations

from enum import Enum


class RejectionReason(str, Enum):
    STRATEGY_NOT_APPROVED = "STRATEGY_NOT_APPROVED"
    LIVE_TRADING_DISABLED = "LIVE_TRADING_DISABLED"
    AI_DIRECT_TRADING_FORBIDDEN = "AI_DIRECT_TRADING_FORBIDDEN"
    INSTRUMENT_NOT_ALLOWED = "INSTRUMENT_NOT_ALLOWED"
    ACCOUNT_NOT_ALLOWED = "ACCOUNT_NOT_ALLOWED"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    POSITION_LIMIT_EXCEEDED = "POSITION_LIMIT_EXCEEDED"
    ORDER_SIZE_EXCEEDED = "ORDER_SIZE_EXCEEDED"
    DAILY_LOSS_LIMIT_EXCEEDED = "DAILY_LOSS_LIMIT_EXCEEDED"
    DRAWDOWN_LIMIT_EXCEEDED = "DRAWDOWN_LIMIT_EXCEEDED"
    CONCENTRATION_LIMIT_EXCEEDED = "CONCENTRATION_LIMIT_EXCEEDED"
    LIQUIDITY_LIMIT_EXCEEDED = "LIQUIDITY_LIMIT_EXCEEDED"
    TURNOVER_LIMIT_EXCEEDED = "TURNOVER_LIMIT_EXCEEDED"
    VOLATILITY_LIMIT_EXCEEDED = "VOLATILITY_LIMIT_EXCEEDED"
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    MARKET_CLOSED = "MARKET_CLOSED"
    DUPLICATE_ORDER = "DUPLICATE_ORDER"
    INVALID_PRICE = "INVALID_PRICE"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    UNKNOWN_RISK_ERROR = "UNKNOWN_RISK_ERROR"


# Crosswalk: dry-run adapters' local RejectionReason values -> canonical.
# Risk-relevant reasons map to their canonical equivalent; execution/transport
# level reasons (checksum, sequence gap, ...) fold into UNKNOWN_RISK_ERROR (they
# are not pre-trade risk reasons and will be re-homed when execution lands).
LEGACY_DRY_RUN_CROSSWALK: dict[str, RejectionReason] = {
    "REJECTED_LIVE_NOT_ALLOWED": RejectionReason.LIVE_TRADING_DISABLED,
    "LIVE_NOT_ALLOWED": RejectionReason.LIVE_TRADING_DISABLED,
    "REJECTED_KILL_SWITCH_ACTIVE": RejectionReason.KILL_SWITCH_ACTIVE,
    "KILL_SWITCH": RejectionReason.KILL_SWITCH_ACTIVE,
    "STRATEGY_SUSPENDED": RejectionReason.STRATEGY_NOT_APPROVED,
    "RISK_APPROVAL_MISSING": RejectionReason.STRATEGY_NOT_APPROVED,
    "PRE_TRADE_MISSING": RejectionReason.STRATEGY_NOT_APPROVED,
    "FORBIDDEN_INST": RejectionReason.INSTRUMENT_NOT_ALLOWED,
    "DUPLICATE": RejectionReason.DUPLICATE_ORDER,
    "IDEMPOTENCY": RejectionReason.DUPLICATE_ORDER,
    # transport / audit / mapping failures -> generic risk error for now
    "DRY_RUN_MISSING": RejectionReason.UNKNOWN_RISK_ERROR,
    "COMMAND_MISSING": RejectionReason.UNKNOWN_RISK_ERROR,
    "CHECKSUM_MISMATCH": RejectionReason.UNKNOWN_RISK_ERROR,
    "OMS_INVALID": RejectionReason.UNKNOWN_RISK_ERROR,
    "EMS_INVALID": RejectionReason.UNKNOWN_RISK_ERROR,
    "STALE": RejectionReason.UNKNOWN_RISK_ERROR,
    "AUDIT_LINEAGE": RejectionReason.UNKNOWN_RISK_ERROR,
    "UNSUPPORTED_TYPE": RejectionReason.UNKNOWN_RISK_ERROR,
    "INVALID_MAPPING": RejectionReason.UNKNOWN_RISK_ERROR,
    "SEQUENCE_GAP": RejectionReason.UNKNOWN_RISK_ERROR,
    "AUDIT_FAILURE": RejectionReason.UNKNOWN_RISK_ERROR,
    "EVENT_FAILURE": RejectionReason.UNKNOWN_RISK_ERROR,
    "UNKNOWN": RejectionReason.UNKNOWN_RISK_ERROR,
}


def from_legacy(value: str) -> RejectionReason:
    """Map a legacy reason string to the canonical reason (fail-safe to UNKNOWN)."""
    return LEGACY_DRY_RUN_CROSSWALK.get(value, RejectionReason.UNKNOWN_RISK_ERROR)
