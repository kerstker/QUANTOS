"""Event backtester sub-project 3 — participation cap / partial fills / VWAP proxy.
Unfilled remainder is retried naturally via target-tracking on later bars."""
import pytest
from quantos.contracts.data import Bar
from services.research.backtest_engine.costs import CostModel
from services.research.backtest_engine.fill_model import NextOpenFill, ParticipationFill
from services.research.backtest_engine.event_engine import run_event_backtest


def _bar(inst, t, o, c, vol=1_000.0):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t,
               open=o, high=max(o, c) + 2, low=max(min(o, c) - 1, 0.1), close=c, volume=vol)


def test_nextopenfill_returns_filled_qty_full():
    f, p, c = NextOpenFill().fill(50, _bar("A", 0, 10.0, 10.0), CostModel(0, 0, 0))
    assert f == 50 and p == pytest.approx(10.0)


def test_participation_caps_partial_fill_and_keeps_sign():
    bar = _bar("A", 0, 10.0, 10.0, vol=1_000.0)            # cap = 0.1*1000 = 100 shares
    fp = ParticipationFill(max_participation=0.1, price_proxy="open")
    assert fp.fill(250, bar, CostModel(0, 0, 0))[0] == 100
    assert fp.fill(-250, bar, CostModel(0, 0, 0))[0] == -100
    assert fp.fill(40, bar, CostModel(0, 0, 0))[0] == 40   # below cap -> full


def test_ohlc4_vwap_price_proxy():
    bar = _bar("A", 0, 10.0, 11.0)        # O=10 H=13 L=9 C=11 -> OHLC4 = 10.75
    bar = Bar(instrument_id="A", event_time=0, knowledge_time=0,
              open=10.0, high=13.0, low=9.0, close=11.0, volume=1_000.0)
    f, price, _ = ParticipationFill(0.5, "vwap").fill(10, bar, CostModel(0, 0, 0))
    assert price == pytest.approx((10.0 + 13.0 + 9.0 + 11.0) / 4)


def test_unknown_price_proxy_fails_closed():
    with pytest.raises(ValueError):
        ParticipationFill(0.1, "bogus").fill(10, _bar("A", 0, 10.0, 10.0), CostModel(0, 0, 0))


def test_single_fill_is_capped_but_multi_bar_reaches_target():
    tl = [(t, {"A": _bar("A", t, 10.0, 10.0, vol=1_000.0)}) for t in range(3)]
    fp = ParticipationFill(max_participation=0.1, price_proxy="open")   # 100 shares/bar
    # 2 bars: only the bar-0 decision fills (at bar-1 open) -> capped at 100
    two = run_event_backtest(tl[:2], [{"A": 1.0}] * 2, initial_capital=1500.0,
                             cost=CostModel(0, 0, 0), fill=fp)
    assert two.final_positions["A"] == 100
    # 3 bars: fills at bar-1 (100) and bar-2 (50) -> reaches desired 150
    three = run_event_backtest(tl, [{"A": 1.0}] * 3, initial_capital=1500.0,
                               cost=CostModel(0, 0, 0), fill=fp)
    assert three.final_positions["A"] == 150
    assert three.reconciliation_residual() < 1e-6
