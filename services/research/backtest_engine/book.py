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
    dividend_income: float = 0.0   # signed cash dividends (longs receive, shorts pay)

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

    def apply_split(self, instrument_id: str, ratio: float, *, cash_in_lieu_price=None) -> None:
        """Value-neutral share/cost adjustment for a split (qty*ratio, avg_cost/ratio).
        A fractional remainder is settled as CASH-IN-LIEU at `cash_in_lieu_price` (the
        fractional shares are sold at that price, booking realized PnL like a partial
        sale); without a price it fails closed (the integer-share book stays exact)."""
        if ratio <= 0:
            raise ValueError("split ratio must be > 0")
        lot = self.positions.get(instrument_id)
        if lot is None or lot.qty == 0:
            return
        scaled = lot.qty * ratio
        new_qty = int(scaled) if scaled >= 0 else -int(-scaled)   # truncate toward zero
        frac = scaled - new_qty                                    # signed fractional remainder
        new_avg = lot.avg_cost / ratio
        if abs(frac) > 1e-9:
            if cash_in_lieu_price is None:
                raise ValueError(f"split ratio {ratio} yields fractional shares for {instrument_id}"
                                 " and no cash_in_lieu_price was supplied")
            # settle the fractional shares at market: realized PnL + cash, like a partial sale
            self.realized_pnl += frac * (cash_in_lieu_price - new_avg)
            self.cash += frac * cash_in_lieu_price
        self.positions[instrument_id] = Lot(new_qty, new_avg if new_qty != 0 else 0.0)

    def pay_dividend(self, instrument_id: str, amount_per_share: float) -> None:
        """Signed cash dividend: longs receive, shorts pay. No position change."""
        lot = self.positions.get(instrument_id)
        if lot is None or lot.qty == 0:
            return
        income = lot.qty * amount_per_share
        self.cash += income
        self.dividend_income += income

    def equity(self, marks: dict) -> float:
        return self.cash + sum(lot.qty * marks[inst] for inst, lot in self.positions.items())

    def unrealized(self, marks: dict) -> float:
        return sum(lot.qty * (marks[inst] - lot.avg_cost) for inst, lot in self.positions.items())
