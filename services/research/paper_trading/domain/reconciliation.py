"""Paper-trading PnL reconciliation (Phase 9) — REAL average-cost accounting.

Replaces the placeholder `notional * 0.001` attribution. Processes fills with
signed average-cost accounting (handles longs, shorts, partial closes and flips),
marks open positions, and decomposes PnL into realized + unrealized − costs per
instrument. The aggregate reconciles EXACTLY (like the backtest engine):

    equity_end − initial_capital  ==  gross_trading_pnl − total_costs

`reconciliation_residual()` is asserted ~0. Deterministic; pure.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import OrderSide, PaperFill


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
        return sum(self.positions[i] * self.marks.get(i, 0.0) for i in self.positions)

    @property
    def equity(self) -> float:
        return self.cash + self.market_value

    @property
    def total_pnl(self) -> float:
        return self.equity - self.initial_capital

    @property
    def gross_trading_pnl(self) -> float:
        return sum(p.realized + p.unrealized for p in self.by_instrument.values())

    @property
    def total_costs(self) -> float:
        return sum(p.commission + p.slippage for p in self.by_instrument.values())

    @property
    def total_net_pnl(self) -> float:
        return sum(p.net for p in self.by_instrument.values())

    @property
    def total_commission(self) -> float:
        return sum(p.commission for p in self.by_instrument.values())

    @property
    def total_slippage(self) -> float:
        return sum(p.slippage for p in self.by_instrument.values())

    def reconciliation_residual(self) -> float:
        return abs(self.total_pnl - (self.gross_trading_pnl - self.total_costs))


def _apply(qty: int, avg: float, delta: int, price: float):
    """Signed average-cost update -> (new_qty, new_avg_cost, realized)."""
    if qty == 0:
        return delta, price, 0.0
    if (qty > 0) == (delta > 0):                       # same direction: add to position
        new_q = qty + delta
        return new_q, (qty * avg + delta * price) / new_q, 0.0
    closing = min(abs(delta), abs(qty))                # opposite: reduce / close / flip
    realized = closing * (price - avg) * (1.0 if qty > 0 else -1.0)
    new_q = qty + delta
    new_avg = avg if abs(delta) <= abs(qty) else price  # flipped remainder re-bases at price
    return new_q, new_avg, realized


def reconcile(fills: List[PaperFill], marks: Optional[Dict[str, float]] = None,
              initial_capital: float = 0.0) -> ReconciliationResult:
    cash = initial_capital
    pos: Dict[str, int] = {}
    avg: Dict[str, float] = {}
    realized: Dict[str, float] = {}
    comm: Dict[str, float] = {}
    slip: Dict[str, float] = {}
    last_price: Dict[str, float] = {}
    turnover = 0.0

    for f in sorted(fills, key=lambda x: (x.timestamp, x.fill_id)):
        iid, q, p = f.instrument_id, f.quantity, f.price
        sign = 1 if f.side == OrderSide.BUY else -1
        cash -= sign * q * p
        cash -= (f.commission + f.slippage)
        comm[iid] = comm.get(iid, 0.0) + f.commission
        slip[iid] = slip.get(iid, 0.0) + f.slippage
        turnover += abs(q * p)
        last_price[iid] = p
        new_q, new_avg, r = _apply(pos.get(iid, 0), avg.get(iid, 0.0), sign * q, p)
        pos[iid], avg[iid] = new_q, new_avg
        realized[iid] = realized.get(iid, 0.0) + r

    marks = dict(marks) if marks else {}
    for iid in pos:
        marks.setdefault(iid, last_price.get(iid, avg.get(iid, 0.0)))

    by_instrument = {}
    for iid in set(pos) | set(comm):
        q = pos.get(iid, 0)
        unreal = q * (marks.get(iid, 0.0) - avg.get(iid, 0.0)) if q else 0.0
        by_instrument[iid] = InstrumentPnL(
            realized=realized.get(iid, 0.0), unrealized=unreal,
            commission=comm.get(iid, 0.0), slippage=slip.get(iid, 0.0),
        )

    return ReconciliationResult(
        by_instrument=by_instrument, cash=cash, positions={i: pos[i] for i in pos if pos[i]},
        marks=marks, initial_capital=initial_capital, turnover=turnover,
    )
