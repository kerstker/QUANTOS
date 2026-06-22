"""Transaction cost model (Phase 4).

Costs are charged on the absolute traded notional. Fee = commission; slippage =
execution shortfall. Market impact (sqrt-law, capacity-aware) is a later
addition (Phase 5/7); kept explicit so research PnL is always net-of-cost.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    fee_bps: float = 1.0       # commission, bps of notional
    slippage_bps: float = 2.0  # slippage, bps of notional

    def trade_cost(self, notional_abs: float) -> float:
        if notional_abs < 0:
            raise ValueError("notional_abs must be non-negative")
        return notional_abs * (self.fee_bps + self.slippage_bps) / 10_000.0
