"""Pluggable fill model. NextOpenFill is the baseline: a decision at bar i fills
at bar i+1's OPEN with slippage embedded in the price; the returned cost is fees +
impact ONLY (slippage is already in the price, so it is NOT double-counted)."""
from __future__ import annotations

from .costs import CostModel


class NextOpenFill:
    def fill(self, qty: int, bar, cost_model: CostModel, adv_shares: float = 0.0) -> tuple[float, float]:
        sign = 1.0 if qty > 0 else -1.0
        slip = cost_model.slippage_bps / 10_000.0
        fill_price = bar.open * (1.0 + sign * slip)            # buys pay up, sells receive less
        notional = abs(qty) * fill_price
        cost = notional * (cost_model.fee_bps + cost_model.impact_bps(abs(qty), adv_shares)) / 10_000.0
        return fill_price, cost
