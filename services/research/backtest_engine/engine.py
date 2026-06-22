"""Deterministic single-instrument backtest engine (Phase 4).

`target_shares[i]` is the position decided at bar i (using data up to close[i]);
it is executed at bar i+1's close (next-bar fill — no same-bar lookahead). The
last target is never executed (no next bar).

Mark-to-market equity = cash + position * close. By construction:
    final_equity - initial_capital  ==  mark_to_market_pnl - total_cost
(exactly, to float tolerance). `reconciliation_residual()` returns the gap and
is asserted ~0 in tests (PnL reconciliation).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from quantos.contracts.data import Bar

from .costs import CostModel


@dataclass(frozen=True)
class Trade:
    event_time: int
    delta_shares: int
    price: float
    cost: float


@dataclass
class BacktestResult:
    initial_capital: float
    equity_curve: list[float]
    trades: list[Trade]
    total_cost: float
    mark_to_market_pnl: float

    @property
    def final_equity(self) -> float:
        return self.equity_curve[-1] if self.equity_curve else self.initial_capital

    @property
    def total_pnl(self) -> float:
        return self.final_equity - self.initial_capital

    @property
    def turnover(self) -> float:
        return sum(abs(t.delta_shares) * t.price for t in self.trades)

    def reconciliation_residual(self) -> float:
        return self.total_pnl - (self.mark_to_market_pnl - self.total_cost)


def run_backtest(
    bars: Sequence[Bar],
    target_shares: Sequence[int],
    initial_capital: float = 1_000_000.0,
    cost: CostModel = CostModel(),
) -> BacktestResult:
    if len(bars) != len(target_shares):
        raise ValueError("bars and target_shares must be the same length")

    cash = float(initial_capital)
    pos = 0
    equity_curve: list[float] = []
    trades: list[Trade] = []
    total_cost = 0.0
    mtm = 0.0
    prev_close: float | None = None

    for i, bar in enumerate(bars):
        price = bar.close
        # 1) mark-to-market the position held since the previous close (pre-trade pos)
        if prev_close is not None:
            mtm += pos * (price - prev_close)
        # 2) execute the previous bar's decision at THIS close (next-bar fill)
        if i > 0:
            delta = int(target_shares[i - 1]) - pos
            if delta != 0:
                c = cost.trade_cost(abs(delta) * price, trade_shares=delta, adv_shares=bar.volume)
                cash -= delta * price        # buying (delta>0) reduces cash
                cash -= c
                pos += delta
                total_cost += c
                trades.append(Trade(event_time=bar.event_time, delta_shares=delta, price=price, cost=c))
        equity_curve.append(cash + pos * price)
        prev_close = price

    return BacktestResult(
        initial_capital=float(initial_capital),
        equity_curve=equity_curve,
        trades=trades,
        total_cost=total_cost,
        mark_to_market_pnl=mtm,
    )
