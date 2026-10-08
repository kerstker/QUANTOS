"""Financing / borrow / short-cost model (event backtester sub-project 2).

Accrued ONCE per holding bar on the marked book: a borrow fee on short notional
plus a financing charge on a negative (margin-debit) cash balance. Time-dependent
via periods_per_year (each holding period accrues annual_rate/periods_per_year).
No long-cash rebate (conservative). Pure-Python, deterministic. Financing is a
cost, so it folds into total_cost and the engine's reconciliation identity holds.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FinancingModel:
    short_borrow_bps_annual: float = 0.0      # borrow fee on |short notional|
    debit_financing_bps_annual: float = 0.0   # financing on a negative cash balance
    periods_per_year: int = 252

    def accrue(self, book, marks: dict) -> float:
        """Financing cost (>= 0) for ONE holding period on the current book."""
        if self.periods_per_year < 1:
            raise ValueError("periods_per_year must be >= 1")
        per = 1.0 / self.periods_per_year
        short_notional = sum(-lot.qty * marks[inst]
                             for inst, lot in book.positions.items() if lot.qty < 0)
        borrow = self.short_borrow_bps_annual / 10_000.0 * short_notional * per
        debit = self.debit_financing_bps_annual / 10_000.0 * max(0.0, -book.cash) * per
        return borrow + debit
