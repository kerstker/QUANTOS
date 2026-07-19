"""Paper PnL reconciliation with signed average-cost accounting."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from quantos.contracts.paper import OrderSide, PaperFill


@dataclass(frozen=True)
class InstrumentPnL:
    realized: float = 0.0
    unrealized: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0

    @property
    def net(self) -> float:
        return self.realized + self.unrealized - self.commission - self.slippage


@dataclass
class ReconciliationResult:
    by_instrument: Dict[str, InstrumentPnL] = field(default_factory=dict)
    cash: float = 0.0
    positions: Dict[str, int] = field(default_factory=dict)
    marks: Dict[str, float] = field(default_factory=dict)
    initial_capital: float = 0.0
    turnover: float = 0.0

    @property
    def market_value(self) -> float:
        return sum(
            self.positions[instrument] * self.marks.get(instrument, 0.0)
            for instrument in self.positions
        )

    @property
    def equity(self) -> float:
        return self.cash + self.market_value

    @property
    def total_pnl(self) -> float:
        return self.equity - self.initial_capital

    @property
    def gross_trading_pnl(self) -> float:
        return sum(
            pnl.realized + pnl.unrealized for pnl in self.by_instrument.values()
        )

    @property
    def total_costs(self) -> float:
        return sum(
            pnl.commission + pnl.slippage for pnl in self.by_instrument.values()
        )

    @property
    def total_net_pnl(self) -> float:
        return sum(pnl.net for pnl in self.by_instrument.values())

    @property
    def total_commission(self) -> float:
        return sum(pnl.commission for pnl in self.by_instrument.values())

    @property
    def total_slippage(self) -> float:
        return sum(pnl.slippage for pnl in self.by_instrument.values())

    def reconciliation_residual(self) -> float:
        return abs(self.total_pnl - (self.gross_trading_pnl - self.total_costs))


def _apply(qty: int, avg: float, delta: int, price: float):
    """Return new quantity, average cost, and realized PnL."""
    if qty == 0:
        return delta, price, 0.0
    if (qty > 0) == (delta > 0):
        new_qty = qty + delta
        return new_qty, (qty * avg + delta * price) / new_qty, 0.0
    closing = min(abs(delta), abs(qty))
    realized = closing * (price - avg) * (1.0 if qty > 0 else -1.0)
    new_qty = qty + delta
    new_avg = avg if abs(delta) <= abs(qty) else price
    return new_qty, new_avg, realized


def reconcile(
    fills: List[PaperFill],
    marks: Optional[Dict[str, float]] = None,
    initial_capital: float = 0.0,
) -> ReconciliationResult:
    cash = initial_capital
    positions: Dict[str, int] = {}
    average_cost: Dict[str, float] = {}
    realized: Dict[str, float] = {}
    commissions: Dict[str, float] = {}
    slippage: Dict[str, float] = {}
    last_price: Dict[str, float] = {}
    turnover = 0.0

    for fill in sorted(fills, key=lambda item: (item.timestamp, item.fill_id)):
        instrument = fill.instrument_id
        quantity = fill.quantity
        price = fill.price
        sign = 1 if fill.side == OrderSide.BUY else -1
        cash -= sign * quantity * price
        cash -= fill.commission + fill.slippage
        commissions[instrument] = commissions.get(instrument, 0.0) + fill.commission
        slippage[instrument] = slippage.get(instrument, 0.0) + fill.slippage
        turnover += abs(quantity * price)
        last_price[instrument] = price
        new_qty, new_avg, realized_pnl = _apply(
            positions.get(instrument, 0),
            average_cost.get(instrument, 0.0),
            sign * quantity,
            price,
        )
        positions[instrument] = new_qty
        average_cost[instrument] = new_avg
        realized[instrument] = realized.get(instrument, 0.0) + realized_pnl

    resolved_marks = dict(marks) if marks else {}
    for instrument in positions:
        resolved_marks.setdefault(
            instrument,
            last_price.get(instrument, average_cost.get(instrument, 0.0)),
        )

    by_instrument = {}
    for instrument in set(positions) | set(commissions):
        quantity = positions.get(instrument, 0)
        unrealized = (
            quantity
            * (
                resolved_marks.get(instrument, 0.0)
                - average_cost.get(instrument, 0.0)
            )
            if quantity
            else 0.0
        )
        by_instrument[instrument] = InstrumentPnL(
            realized=realized.get(instrument, 0.0),
            unrealized=unrealized,
            commission=commissions.get(instrument, 0.0),
            slippage=slippage.get(instrument, 0.0),
        )

    return ReconciliationResult(
        by_instrument=by_instrument,
        cash=cash,
        positions={
            instrument: positions[instrument]
            for instrument in positions
            if positions[instrument]
        },
        marks=resolved_marks,
        initial_capital=initial_capital,
        turnover=turnover,
    )


__all__ = ["InstrumentPnL", "ReconciliationResult", "reconcile"]
