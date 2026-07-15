"""Backtest execution interface consumed by research validation orchestration."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable

from quantos.contracts.cost import CostModel
from quantos.contracts.data import Bar


@dataclass(frozen=True)
class BacktestOutcome:
    """Minimal immutable evidence returned to validation by a backtest adapter."""

    initial_capital: float
    equity_curve: tuple[float, ...]

    def __post_init__(self) -> None:
        initial_capital = float(self.initial_capital)
        equity_curve = tuple(float(value) for value in self.equity_curve)
        if not math.isfinite(initial_capital) or initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        if not equity_curve:
            raise ValueError("equity_curve must contain at least one observation")
        if not all(math.isfinite(value) for value in equity_curve):
            raise ValueError("equity_curve must contain only finite observations")
        object.__setattr__(self, "initial_capital", initial_capital)
        object.__setattr__(self, "equity_curve", equity_curve)

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1]

    @property
    def total_pnl(self) -> float:
        return self.final_equity - self.initial_capital


@runtime_checkable
class BacktestExecutionPort(Protocol):
    def run(
        self,
        bars: Sequence[Bar],
        target_shares: Sequence[int],
        *,
        initial_capital: float = 1_000_000.0,
        cost: CostModel = CostModel(),
    ) -> BacktestOutcome: ...


__all__ = ["BacktestExecutionPort", "BacktestOutcome"]
