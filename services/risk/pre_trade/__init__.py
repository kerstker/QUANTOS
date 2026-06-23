"""QUANTOS Pre-Trade Risk Gate (blueprint §07/§08 / Phase 8).

The safety-critical pre-trade gatekeeper. A clean rebuild of the quarantined
service, living under services/risk/ so the dependency arrow is one-directional
(portfolio/alpha -> risk). Pure, deterministic, FAIL-CLOSED: any error, missing
or non-finite data, unknown instrument, empty allow-list, or tripped kill-switch
=> REJECT. Runs ALL checks and collects ALL canonical §8 reasons; never mutates
intent (strategy decides intent, risk decides permission); imports nothing from
services/trading or services/execution and no broker SDK.
"""
from .config import RiskLimits
from .context import AccountRiskState, MarketContext, RiskContext, StrategyApproval
from .kill_switch import KillSwitch, UnauthorizedReset
from .gate import PreTradeRiskGate, approval_ref
from .checks import CHECK_REGISTRY

__all__ = [
    "RiskLimits", "AccountRiskState", "MarketContext", "RiskContext", "StrategyApproval",
    "KillSwitch", "UnauthorizedReset", "PreTradeRiskGate", "approval_ref", "CHECK_REGISTRY",
]
