"""Portfolio domain entities (blueprint §6.4).

Signals -> target positions -> portfolio snapshot, plus rebalance plan and
attribution. Positions/weights may be negative (shorts); exposures/leverage are
non-negative.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# P0-1 (same invariant as services/risk/pre_trade/context.py): a NESTED model applies
# its OWN config, so a non-finite value must be rejected HERE — otherwise it poisons a
# summing AccountRiskState (gross/net exposure) and every `x > limit` in the pre-trade
# checks evaluates False, silently approving.
_FROZEN_FINITE = ConfigDict(frozen=True, allow_inf_nan=False)


class Signal(BaseModel):
    model_config = _FROZEN_FINITE
    signal_id: str = Field(min_length=1)
    alpha_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    value: float = 0.0
    confidence: float = Field(default=0.5, ge=0, le=1)


class TargetPosition(BaseModel):
    model_config = ConfigDict(frozen=True)
    instrument_id: str = Field(min_length=1)
    target_weight: float = 0.0  # may be negative (short)


class Position(BaseModel):
    model_config = _FROZEN_FINITE
    instrument_id: str = Field(min_length=1)
    quantity: int = 0  # may be negative (short)
    avg_cost: float = Field(default=0.0, ge=0)
    market_value: float = 0.0


class Portfolio(BaseModel):
    model_config = ConfigDict(frozen=True)
    portfolio_id: str = Field(min_length=1)
    name: str = ""
    base_currency: str = "CNY"


class PortfolioSnapshot(BaseModel):
    model_config = _FROZEN_FINITE
    portfolio_id: str = Field(min_length=1)
    as_of: int = Field(ge=0)
    positions: tuple[Position, ...] = ()
    gross_exposure: float = Field(default=0.0, ge=0)
    net_exposure: float = 0.0
    leverage: float = Field(default=0.0, ge=0)


class RebalancePlan(BaseModel):
    model_config = ConfigDict(frozen=True)
    plan_id: str = Field(min_length=1)
    portfolio_id: str = Field(min_length=1)
    targets: tuple[TargetPosition, ...] = ()
    turnover: float = Field(default=0.0, ge=0)


class AttributionReport(BaseModel):
    model_config = _FROZEN_FINITE
    portfolio_id: str = Field(min_length=1)
    by_alpha: dict[str, float] = Field(default_factory=dict)
    by_sector: dict[str, float] = Field(default_factory=dict)
    total_pnl: float = 0.0
