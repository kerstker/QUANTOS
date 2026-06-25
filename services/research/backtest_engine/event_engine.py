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
from .corporate_actions import apply_corporate_action
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
    dividend_income: float = 0.0

    def final_equity(self) -> float:
        return self.equity_curve[-1] if self.equity_curve else self.initial_capital

    def reconciliation_residual(self) -> float:
        lhs = self.final_equity() - self.initial_capital
        rhs = self.realized_pnl + self.final_unrealized + self.dividend_income - self.total_cost
        return abs(lhs - rhs)


def run_event_backtest(timeline: Sequence, targets: Sequence, *, initial_capital: float,
                       cost: CostModel = CostModel(), fill=None, lot_size: int = 1,
                       financing=None, corporate_actions=None) -> EventBacktestResult:
    fill = fill or NextOpenFill()
    ca_by_ts: dict = {}
    for action in (corporate_actions or ()):
        ca_by_ts.setdefault(action.event_time, []).append(action)
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
        # 1) ex moment of bar i: apply corporate actions to the CARRIED position
        # (held going into the ex date) BEFORE this bar's fills — so a position
        # opened at the ex-bar open does NOT collect the dividend (review: ex-bar
        # free-lunch) and a fill happens at the already-adjusted (post-split) price.
        for action in ca_by_ts.get(ts, ()):
            apply_corporate_action(book, action)

        # 2) OPEN of bar i: execute orders queued at i-1
        for inst, qty in pending.items():
            if inst not in bars:
                raise ValueError(f"no bar for queued instrument {inst} at ts {ts}")
            # pass the bar's volume as the ADV proxy so the fill model's market-impact
            # term actually applies (review: impact was silently dropped — adv defaulted 0)
            filled, price, c = fill.fill(qty, bars[inst], cost, adv_shares=bars[inst].volume)
            if filled != 0:
                book.apply_fill(inst, filled, price, c)
                turnover += abs(filled) * price
        pending = {}

        # 2) CLOSE of bar i: accrue financing on the held book, mark, record equity.
        # A still-held instrument absent from this bar cannot be marked -> fail with a
        # clear error rather than a bare KeyError in equity() (review).
        marks = {inst: bar.close for inst, bar in bars.items()}
        held_unmarked = [inst for inst, lot in book.positions.items()
                         if lot.qty != 0 and inst not in marks]
        if held_unmarked:
            raise ValueError(f"held instruments missing from bar at ts {ts} (cannot mark): {held_unmarked}")
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
        dividend_income=book.dividend_income,
    )
