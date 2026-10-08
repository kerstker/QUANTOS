"""Port adapter for the deterministic single-instrument backtest engine."""

from __future__ import annotations

from typing import Sequence

from quantos.contracts.cost import CostModel
from quantos.contracts.data import Bar
from services.research.ports.backtest import BacktestOutcome

from .engine import run_backtest


class DeterministicBacktestAdapter:
    def run(
        self,
        bars: Sequence[Bar],
        target_shares: Sequence[int],
        *,
        initial_capital: float = 1_000_000.0,
        cost: CostModel = CostModel(),
    ) -> BacktestOutcome:
        result = run_backtest(
            bars,
            target_shares,
            initial_capital=initial_capital,
            cost=cost,
        )
        return BacktestOutcome(
            initial_capital=result.initial_capital,
            equity_curve=tuple(result.equity_curve),
        )


__all__ = ["DeterministicBacktestAdapter"]
