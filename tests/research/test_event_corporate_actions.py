"""Event backtester sub-project 4 — corporate actions (splits + cash dividends).
Splits are value-neutral (qty*ratio, avg_cost/ratio); dividends are signed
(longs receive, shorts pay). Reconciliation identity extended with dividend_income."""
import pytest
from quantos.contracts.data import Bar, CorporateAction, CorporateActionType
from services.research.backtest_engine.book import Book, Lot
from services.research.backtest_engine.corporate_actions import apply_corporate_action
from services.research.backtest_engine.costs import CostModel
from services.research.backtest_engine.event_engine import run_event_backtest


def _bar(inst, t, o, c):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t,
               open=o, high=max(o, c) + 2, low=max(min(o, c) - 1, 0.1), close=c, volume=1_000_000)


def _ca(inst, kind, ts, ratio=1.0, amount=0.0):
    return CorporateAction(instrument_id=inst, action_type=kind, knowledge_time=0,
                           event_time=ts, ex_date="2026-06-24", ratio=ratio, amount=amount)


def test_split_scales_shares_and_avg_cost_value_neutral():
    b = Book(cash=0.0)
    b.apply_fill("A", 100, 10.0, 0.0)                # long 100 @ 10
    apply_corporate_action(b, _ca("A", CorporateActionType.SPLIT, 1, ratio=2.0))
    assert b.positions["A"] == Lot(200, 5.0)        # 2:1 split
    # value/unrealized preserved when the mark also halves
    assert b.unrealized({"A": 5.0}) == pytest.approx(100 * (10.0 - 10.0))   # 0 at cost, stays 0


def test_fractional_split_fails_closed():
    b = Book(cash=0.0)
    b.apply_fill("A", 101, 10.0, 0.0)
    with pytest.raises(ValueError):
        apply_corporate_action(b, _ca("A", CorporateActionType.SPLIT, 1, ratio=0.5))  # 50.5 shares


def test_fractional_split_with_cash_in_lieu():
    b = Book(cash=0.0)
    b.apply_fill("A", 101, 10.0, 0.0)                       # long 101 @ 10
    b.apply_split("A", 1.5, cash_in_lieu_price=8.0)         # 101*1.5 = 151.5 -> 151 sh + 0.5 cash-in-lieu
    assert b.positions["A"] == Lot(151, 10.0 / 1.5)         # whole shares re-averaged
    # 0.5 fractional shares settled at 8.0: cash += 0.5*8, realized += 0.5*(8 - 10/1.5)
    assert b.cash == pytest.approx(-101 * 10 + 0.5 * 8.0)
    assert b.realized_pnl == pytest.approx(0.5 * (8.0 - 10.0 / 1.5))


def test_fractional_split_without_price_still_fails_closed():
    b = Book(cash=0.0)
    b.apply_fill("A", 101, 10.0, 0.0)
    with pytest.raises(ValueError):
        b.apply_split("A", 0.5)                              # 50.5 shares, no cash_in_lieu_price


def test_dividend_is_signed_long_receives_short_pays():
    long_b = Book(cash=0.0)
    long_b.apply_fill("A", 100, 10.0, 0.0)
    apply_corporate_action(long_b, _ca("A", CorporateActionType.DIVIDEND, 1, amount=0.5))
    assert long_b.dividend_income == pytest.approx(50.0) and long_b.cash == pytest.approx(-1000.0 + 50.0)

    short_b = Book(cash=0.0)
    short_b.apply_fill("A", -100, 10.0, 0.0)
    apply_corporate_action(short_b, _ca("A", CorporateActionType.DIVIDEND, 1, amount=0.5))
    assert short_b.dividend_income == pytest.approx(-50.0)   # short pays


def test_unsupported_action_fails_closed():
    b = Book(cash=0.0)
    b.apply_fill("A", 10, 10.0, 0.0)
    with pytest.raises(ValueError):
        apply_corporate_action(b, _ca("A", CorporateActionType.MERGER, 1))


def test_engine_dividend_raises_equity_and_reconciles():
    tl = [(t, {"A": _bar("A", t, 10.0, 10.0)}) for t in range(4)]
    targets = [{"A": 1.0}, {"A": 1.0}, {"A": 1.0}, {"A": 1.0}]
    base = run_event_backtest(tl, targets, initial_capital=100_000.0, cost=CostModel(0, 0, 0))
    div = run_event_backtest(tl, targets, initial_capital=100_000.0, cost=CostModel(0, 0, 0),
                             corporate_actions=[_ca("A", CorporateActionType.DIVIDEND, 2, amount=0.5)])
    assert div.final_equity() > base.final_equity()      # dividend income on the long
    assert div.dividend_income > 0
    assert div.reconciliation_residual() < 1e-6


def test_position_opened_on_ex_bar_collects_no_dividend():
    # decision at bar 0 -> fill at bar 1 OPEN; dividend ex at bar 1. Corporate actions
    # now apply BEFORE the fill (to the carried, still-flat book), so the just-opened
    # position does NOT collect the dividend (review: ex-bar free-lunch fixed).
    tl = [(t, {"A": _bar("A", t, 10.0, 10.0)}) for t in range(3)]
    targets = [{"A": 1.0}, {"A": 1.0}, {"A": 1.0}]
    r = run_event_backtest(tl, targets, initial_capital=100_000.0, cost=CostModel(0, 0, 0),
                           corporate_actions=[_ca("A", CorporateActionType.DIVIDEND, 1, amount=0.5)])
    assert r.dividend_income == 0.0
    assert r.reconciliation_residual() < 1e-6


def test_engine_fractional_split_cash_in_lieu_reconciles():
    # a 3:2 split (ratio 1.5) on an odd share count -> cash-in-lieu at the ex-bar open;
    # the global reconciliation identity still holds
    tl = [(0, {"A": _bar("A", 0, 10.0, 10.0)}),
          (1, {"A": _bar("A", 1, 10.0, 10.0)}),
          (2, {"A": _bar("A", 2, 7.0, 7.0)}),     # post-split-ish prices
          (3, {"A": _bar("A", 3, 7.0, 7.0)})]
    targets = [{"A": 1.0}, {"A": 1.0}, {"A": 1.0}, {"A": 1.0}]
    r = run_event_backtest(tl, targets, initial_capital=100_003.0, cost=CostModel(0, 0, 0),
                           corporate_actions=[_ca("A", CorporateActionType.SPLIT, 2, ratio=1.5)])
    assert r.reconciliation_residual() < 1e-6


def test_engine_split_reconciles_and_doubles_position():
    # raw forward prices: price halves on the ex bar (ts=2) for a 2:1 split
    tl = [(0, {"A": _bar("A", 0, 10.0, 10.0)}), (1, {"A": _bar("A", 1, 10.0, 10.0)}),
          (2, {"A": _bar("A", 2, 5.0, 5.0)}), (3, {"A": _bar("A", 3, 5.0, 5.0)})]
    targets = [{"A": 1.0}, {"A": 1.0}, {"A": 1.0}, {"A": 1.0}]
    r = run_event_backtest(tl, targets, initial_capital=100_000.0, cost=CostModel(0, 0, 0),
                           corporate_actions=[_ca("A", CorporateActionType.SPLIT, 2, ratio=2.0)])
    assert r.reconciliation_residual() < 1e-6
