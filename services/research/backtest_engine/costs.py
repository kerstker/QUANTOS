"""Transaction cost model (Phase 4).

Costs are charged on the absolute traded notional:
  * fee      — commission (bps of notional),
  * slippage — execution shortfall (bps of notional),
  * impact   — optional square-root market impact, scaled by participation
               (trade size / average daily volume). Disabled by default
               (impact_coef=0) so it never silently changes existing results.
All research PnL is therefore net-of-cost.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    fee_bps: float = 1.0
    slippage_bps: float = 2.0
    impact_coef: float = 0.0   # bps per sqrt(participation); 0 disables impact

    def impact_bps(self, trade_shares: float, adv_shares: float) -> float:
        if self.impact_coef <= 0 or adv_shares <= 0 or trade_shares == 0:
            return 0.0
        participation = abs(trade_shares) / adv_shares
        return self.impact_coef * math.sqrt(participation)

    def trade_cost(self, notional_abs: float, trade_shares: float = 0.0, adv_shares: float = 0.0) -> float:
        if notional_abs < 0:
            raise ValueError("notional_abs must be non-negative")
        bps = self.fee_bps + self.slippage_bps + self.impact_bps(trade_shares, adv_shares)
        return notional_abs * bps / 10_000.0
