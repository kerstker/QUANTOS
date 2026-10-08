"""Event backtester sub-project 2 — borrow/financing/short costs accrued per
holding bar; reconciliation identity preserved (financing is just more cost)."""
import pytest
from quantos.contracts.data import Bar
from services.research.backtest_engine.book import Book
from services.research.backtest_engine.costs import CostModel
from services.research.backtest_engine.financing import FinancingModel
from services.research.backtest_engine.event_engine import run_event_backtest


def _bar(inst, t, o, c):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t,
               open=o, high=max(o, c) + 1, low=max(min(o, c) - 0.5, 0.1), close=c, volume=1_000_000)


def _timeline(prices):
    return [(t, {inst: _bar(inst, t, oc[0], oc[1]) for inst, oc in slc.items()})
            for t, slc in enumerate(prices)]


def test_accrue_charges_short_borrow_and_debit_financing():
    fm = FinancingModel(short_borrow_bps_annual=520.0, debit_financing_bps_annual=252.0,
                        periods_per_year=252)
    b = Book(cash=-1000.0)
    b.apply_fill("S", -10, 20.0, 0.0)            # short 10 @ 20: proceeds +200 -> cash -800
    assert b.cash == pytest.approx(-800.0)
    fee = fm.accrue(b, {"S": 20.0})
    # borrow = 520bps/yr * |short notional 200| * (1/252); debit = 252bps/yr * |cash 800| * (1/252)
    assert fee == pytest.approx((0.0520 * 200 + 0.0252 * 800) / 252, rel=1e-9)


def test_long_only_positive_cash_has_zero_financing():
    fm = FinancingModel(short_borrow_bps_annual=520.0, debit_financing_bps_annual=252.0)
    b = Book(cash=1000.0)
    b.apply_fill("L", 10, 20.0, 0.0)             # cash now negative? 1000-200=800 positive
    assert fm.accrue(b, {"L": 20.0}) == 0.0      # no shorts, positive cash -> 0


def test_charge_financing_reduces_cash_and_adds_cost():
    b = Book(cash=1000.0)
    b.charge_financing(7.5)
    assert b.cash == pytest.approx(992.5) and b.total_cost == pytest.approx(7.5)


def test_financing_reduces_equity_of_a_short_book_and_reconciles():
    tl = _timeline([{"A": (20.0, 20.0)}, {"A": (20.0, 20.0)}, {"A": (20.0, 20.0)}])
    targets = [{"A": -0.5}, {"A": -0.5}, {"A": -0.5}]
    free = run_event_backtest(tl, targets, initial_capital=100_000.0, cost=CostModel(0, 0, 0))
    fin = run_event_backtest(tl, targets, initial_capital=100_000.0, cost=CostModel(0, 0, 0),
                             financing=FinancingModel(short_borrow_bps_annual=520.0))
    assert fin.final_equity() < free.final_equity()       # borrow cost on the short
    assert fin.total_cost > 0
    assert fin.reconciliation_residual() < 1e-6


def test_zero_rates_match_no_financing():
    tl = _timeline([{"A": (20.0, 21.0)}, {"A": (21.0, 22.0)}])
    targets = [{"A": 1.0}, {"A": 1.0}]
    base = run_event_backtest(tl, targets, initial_capital=100_000.0)
    zero = run_event_backtest(tl, targets, initial_capital=100_000.0,
                              financing=FinancingModel(0.0, 0.0))
    assert base.equity_curve == zero.equity_curve
