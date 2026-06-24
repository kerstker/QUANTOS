"""Event-driven portfolio backtester (audit roadmap, sub-project 1).

A deterministic event loop over a time-ordered stream of multi-instrument bars.
At the CLOSE of bar i a target-weight decision sizes integer (lot-rounded) orders
on that bar's equity; those orders fill at the OPEN of bar i+1 (no lookahead) via
a pluggable FillModel. A Book evolves bar-by-bar with signed average-cost. The
net equity curve reconciles to the global identity
  final_equity - initial == realized_pnl + final_unrealized - total_cost.
Pure-Python, deterministic, fail-closed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .book import Book
from .costs import CostModel
from .fill_model import NextOpenFill


@dataclass(frozen=True)
class EventBacktestResult:
    initial_capital: float
    timestamps: list
    equity_curve: list
    cash_curve: list
    total_cost: float
    realized_pnl: float
    final_unrealized: float
    turnover: float
    final_positions: dict

    def final_equity(self) -> float:
        return self.equity_curve[-1] if self.equity_curve else self.initial_capital

    def reconciliation_residual(self) -> float:
        lhs = self.final_equity() - self.initial_capital
        rhs = self.realized_pnl + self.final_unrealized - self.total_cost
        return abs(lhs - rhs)


def run_event_backtest(timeline: Sequence, targets: Sequence, *, initial_capital: float,
                       cost: CostModel = CostModel(), fill=None, lot_size: int = 1,
                       financing=None) -> EventBacktestResult:
    fill = fill or NextOpenFill()
    if len(targets) != len(timeline):
        raise ValueError("targets and timeline must have equal length")
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    if lot_size < 1:
        raise ValueError("lot_size must be >= 1")

    book = Book(cash=float(initial_capital))
    timestamps, equity_curve, cash_curve = [], [], []
    turnover = 0.0
    pending: dict = {}            # orders decided at i-1, to fill at i open

    for i, (ts, bars) in enumerate(timeline):
        # 1) OPEN of bar i: execute orders queued at i-1
        for inst, qty in pending.items():
            if inst not in bars:
                raise ValueError(f"no bar for queued instrument {inst} at ts {ts}")
            filled, price, c = fill.fill(qty, bars[inst], cost)   # filled may be partial
            if filled != 0:
                book.apply_fill(inst, filled, price, c)
                turnover += abs(filled) * price
        pending = {}

        # 2) CLOSE of bar i: accrue financing on the held book, mark, record equity
        marks = {inst: bar.close for inst, bar in bars.items()}
        if financing is not None:
            book.charge_financing(financing.accrue(book, marks))
        equity_i = book.equity(marks)
        timestamps.append(ts)
        equity_curve.append(equity_i)
        cash_curve.append(book.cash)

        tgt = targets[i]
        for inst, w in tgt.items():
            if inst not in bars:
                raise ValueError(f"target names instrument {inst} absent at ts {ts}")
            if not math.isfinite(w):
                raise ValueError(f"non-finite target weight for {inst}")
            close = bars[inst].close
            desired = int(round(w * equity_i / close / lot_size)) * lot_size
            current = book.positions.get(inst).qty if inst in book.positions else 0
            delta = desired - current
            if delta != 0:
                pending[inst] = delta

    final_marks = {inst: bar.close for inst, bar in timeline[-1][1].items()} if timeline else {}
    return EventBacktestResult(
        initial_capital=float(initial_capital), timestamps=timestamps,
        equity_curve=equity_curve, cash_curve=cash_curve,
        total_cost=book.total_cost, realized_pnl=book.realized_pnl,
        final_unrealized=book.unrealized(final_marks), turnover=turnover,
        final_positions={inst: lot.qty for inst, lot in book.positions.items() if lot.qty != 0},
    )
