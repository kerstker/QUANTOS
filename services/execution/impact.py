"""Participation-driven Almgren-Chriss-style market-impact model."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlmgrenChrissImpact:
    temp_coef: float = 10.0
    perm_coef: float = 5.0
    temp_exponent: float = 0.5

    def _participation(
        self, trade_notional: float, adv_notional: float
    ) -> float:
        if adv_notional <= 0 or trade_notional == 0:
            return 0.0
        return abs(trade_notional) / adv_notional

    def temporary_bps(self, trade_notional: float, adv_notional: float) -> float:
        participation = self._participation(trade_notional, adv_notional)
        return (
            0.0
            if participation == 0
            else self.temp_coef * (participation**self.temp_exponent)
        )

    def permanent_bps(self, trade_notional: float, adv_notional: float) -> float:
        return self.perm_coef * self._participation(trade_notional, adv_notional)

    def total_cost_notional(
        self, trade_notional: float, adv_notional: float
    ) -> float:
        bps = self.temporary_bps(
            trade_notional, adv_notional
        ) + self.permanent_bps(trade_notional, adv_notional)
        return abs(trade_notional) * bps / 10_000.0


__all__ = ["AlmgrenChrissImpact"]
