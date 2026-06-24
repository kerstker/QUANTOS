"""Integer-share book with signed average-cost accounting (event-driven engine).

Mirrors the long/short/flip realized-PnL rule used by paper_trading's
reconciliation, reimplemented locally so the backtest engine stays self-contained.
Deterministic, pure-Python. Cash is reduced by signed notional + cost on each fill.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Lot:
    qty: int
    avg_cost: float


@dataclass
class Book:
    cash: float
    positions: dict = field(default_factory=dict)
    realized_pnl: float = 0.0
    total_cost: float = 0.0

    def apply_fill(self, instrument_id: str, qty: int, price: float, cost: float) -> None:
        if qty == 0:
            self.cash -= cost
            self.total_cost += cost
            return
        lot = self.positions.get(instrument_id, Lot(0, 0.0))
        old = lot.qty
        new = old + qty
        if old == 0 or (old > 0) == (qty > 0):
            # opening or adding in the same direction -> re-average
            avg = (lot.avg_cost * abs(old) + price * abs(qty)) / abs(new) if new != 0 else 0.0
        else:
            # opposite direction -> realize PnL on the closed quantity
            closed = min(abs(qty), abs(old))
            self.realized_pnl += (price - lot.avg_cost) * closed * (1 if old > 0 else -1)
            avg = lot.avg_cost if abs(qty) <= abs(old) else price   # flip rebases at price
        self.positions[instrument_id] = Lot(new, avg if new != 0 else 0.0)
        self.cash -= qty * price + cost
        self.total_cost += cost

    def charge_financing(self, amount: float) -> None:
        """Charge a pure carrying cost (borrow/financing) — no position change."""
        self.cash -= amount
        self.total_cost += amount

    def equity(self, marks: dict) -> float:
        return self.cash + sum(lot.qty * marks[inst] for inst, lot in self.positions.items())

    def unrealized(self, marks: dict) -> float:
        return sum(lot.qty * (marks[inst] - lot.avg_cost) for inst, lot in self.positions.items())
