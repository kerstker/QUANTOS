"""Pluggable fill models. Each `fill(...)` returns `(filled_qty, fill_price, cost)`:
the engine applies only `filled_qty` (which may be smaller than requested under a
participation cap), and the unfilled remainder is retried naturally on later bars
via target-tracking. Slippage is embedded in the fill price; `cost` is fees +
impact ONLY (never the CostModel.trade_cost slippage, which would double-count).
"""
from __future__ import annotations

from dataclasses import dataclass

from .costs import CostModel


class NextOpenFill:
    """Baseline: full fill at the next bar's OPEN with slippage in the price."""

    def fill(self, qty: int, bar, cost_model: CostModel, adv_shares: float = 0.0) -> tuple[int, float, float]:
        if qty == 0:
            return 0, 0.0, 0.0
        sign = 1.0 if qty > 0 else -1.0
        slip = cost_model.slippage_bps / 10_000.0
        fill_price = bar.open * (1.0 + sign * slip)
        cost = abs(qty) * fill_price * (cost_model.fee_bps + cost_model.impact_bps(abs(qty), adv_shares)) / 10_000.0
        return qty, fill_price, cost


@dataclass(frozen=True)
class ParticipationFill:
    """Partial fill capped at `max_participation` of the bar's volume, priced at a
    bar-level VWAP proxy (OHLC4 by default). The unfilled remainder is picked up by
    later bars' target-tracking, no explicit order queue needed."""
    max_participation: float = 0.1
    price_proxy: str = "vwap"        # "vwap"=OHLC4 | "typical"=HLC3 | "open"

    def _ref(self, bar) -> float:
        if self.price_proxy == "open":
            return bar.open
        if self.price_proxy == "typical":
            return (bar.high + bar.low + bar.close) / 3.0
        if self.price_proxy == "vwap":
            return (bar.open + bar.high + bar.low + bar.close) / 4.0
        raise ValueError(f"unknown price_proxy: {self.price_proxy!r}")

    def fill(self, qty: int, bar, cost_model: CostModel, adv_shares: float = 0.0) -> tuple[int, float, float]:
        if qty == 0:
            return 0, 0.0, 0.0
        cap = int(self.max_participation * bar.volume)
        filled = (1 if qty > 0 else -1) * min(abs(qty), cap)
        if filled == 0:
            return 0, 0.0, 0.0
        sign = 1.0 if filled > 0 else -1.0
        slip = cost_model.slippage_bps / 10_000.0
        fill_price = self._ref(bar) * (1.0 + sign * slip)
        cost = abs(filled) * fill_price * (cost_model.fee_bps + cost_model.impact_bps(abs(filled), adv_shares)) / 10_000.0
        return filled, fill_price, cost
