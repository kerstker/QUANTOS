"""Pre-trade risk inputs (Phase 8) — frozen, and NON-FINITE-REJECTING.

Every float field uses allow_inf_nan=False so NaN/inf are rejected at construction
(review P0-5 — a single missed in-check finite guard would let `value > limit`
silently evaluate False). Exposures are COMPUTED from positions (no stored-vs-summed
divergence; review P1-4). SafetyConfig is bound at the gate, NOT here (review P1-5).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from quantos.contracts.alpha import Actor
from quantos.contracts.config import RESEARCH_SAFE_DEFAULTS, SafetyConfig
from quantos.contracts.hashing import stable_hash
from quantos.contracts.killswitch import KillSwitchState
from quantos.contracts.portfolio import Position
from quantos.contracts.trading import OrderIntent, OrderSide

_FROZEN_FINITE = ConfigDict(frozen=True, allow_inf_nan=False)


def order_fingerprint(*, instrument_id: str, side: OrderSide | str, quantity: int,
                      limit_price: Optional[float], strategy_id: str) -> str:
    """Canonical duplicate-order identity shared by the gate and ledger replay."""
    side_value = side.value if isinstance(side, OrderSide) else str(side)
    return stable_hash({
        "instrument": instrument_id,
        "side": side_value,
        "quantity": quantity,
        "limit_price": limit_price,
        "strategy": strategy_id,
    })


class AccountRiskState(BaseModel):
    model_config = _FROZEN_FINITE
    account_id: str = ""
    cash: float = 0.0
    equity: float = 0.0
    day_pnl: float = 0.0
    peak_equity: float = 0.0
    positions: tuple[Position, ...] = ()

    @property
    def gross_exposure(self) -> float:
        return sum(abs(p.market_value) for p in self.positions)

    @property
    def net_exposure(self) -> float:
        return sum(p.market_value for p in self.positions)

    def position_qty(self, instrument_id: str) -> int:
        return sum(p.quantity for p in self.positions if p.instrument_id == instrument_id)

    def position_value(self, instrument_id: str) -> float:
        return sum(p.market_value for p in self.positions if p.instrument_id == instrument_id)


class MarketContext(BaseModel):
    model_config = _FROZEN_FINITE
    instrument_id: str = ""
    known: bool = False
    reference_price: Optional[float] = None
    market_open: bool = False
    price_as_of: int = 0
    est_daily_volume: Optional[float] = None   # None = unknown -> liquidity rejects
    instrument_vol: Optional[float] = None      # None = unknown -> volatility rejects


class StrategyApproval(BaseModel):
    model_config = ConfigDict(frozen=True)
    strategy_id: str = ""
    approved: bool = False
    approval_id: str = ""


class RiskContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    account: AccountRiskState
    market: MarketContext
    strategy: StrategyApproval
    kill_switch: KillSwitchState = KillSwitchState()
    safety_config: SafetyConfig = RESEARCH_SAFE_DEFAULTS   # gate overwrites with its bound one (P1-5)
    actor: Optional[Actor] = None
    now: int = Field(default=0, ge=0)
    max_staleness: int = 86_400
    recent_order_fingerprints: tuple[str, ...] = ()
    recent_timestamps: tuple[int, ...] = ()
    recent_order_statuses: tuple[str, ...] = ()
    plan_turnover: float = 0.0

    def signed_delta(self, intent: OrderIntent) -> int:
        return intent.quantity if intent.side == OrderSide.BUY else -intent.quantity

    def projected_position(self, intent: OrderIntent) -> int:
        return self.account.position_qty(intent.instrument_id) + self.signed_delta(intent)

    def effective_price(self, intent: OrderIntent) -> Optional[float]:
        return intent.limit_price if intent.limit_price is not None else self.market.reference_price

    def order_notional(self, intent: OrderIntent) -> Optional[float]:
        p = self.effective_price(intent)
        return abs(intent.quantity * p) if p is not None else None

    def fingerprint(self, intent: OrderIntent) -> str:
        return order_fingerprint(
            instrument_id=intent.instrument_id,
            side=intent.side,
            quantity=intent.quantity,
            limit_price=intent.limit_price,
            strategy_id=intent.strategy_id,
        )

    def request_hash(self, intent: OrderIntent) -> str:
        return stable_hash({
            "fingerprint": self.fingerprint(intent),
            "account": self.account.account_id,
            "ref_price": self.market.reference_price,
            "now": self.now,
        })
