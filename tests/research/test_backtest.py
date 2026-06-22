"""Phase 4 — backtest engine: determinism, costs, next-bar fill (no lookahead),
PnL reconciliation, cash/position accounting, metrics."""
from quantos.contracts.data import Bar
from services.research.backtest_engine.costs import CostModel
from services.research.backtest_engine.engine import run_backtest
from services.research.backtest_engine.metrics import max_drawdown, metrics, sharpe


def _bars(closes):
    return [Bar(instrument_id="X", event_time=i + 1, knowledge_time=i + 1,
                open=c, high=c + 1, low=max(c - 1, 0.01), close=c, volume=100)
            for i, c in enumerate(closes)]


def test_cost_model_math():
    assert CostModel(fee_bps=1.0, slippage_bps=2.0).trade_cost(10_000) == 3.0
    assert CostModel(0, 0).trade_cost(10_000) == 0.0


def test_backtest_is_deterministic():
    bars = _bars([10, 11, 12, 13])
    a = run_backtest(bars, [10, 10, 10, 10])
    b = run_backtest(bars, [10, 10, 10, 10])
    assert a.equity_curve == b.equity_curve and a.total_cost == b.total_cost


def test_next_bar_fill_no_lookahead():
    # decide 100 shares at bar 0; it must execute at bar 1 (next bar), not bar 0.
    bars = _bars([10, 11, 12])
    res = run_backtest(bars, [100, 100, 100], cost=CostModel(0, 0))
    assert len(res.trades) == 1
    assert res.trades[0].event_time == bars[1].event_time   # executed next bar
    assert res.trades[0].price == 11.0                      # at bar 1 close
    assert res.trades[0].delta_shares == 100


def test_pnl_reconciles_exactly():
    bars = _bars([10, 11, 9, 12, 13])
    # buy, then flatten, then buy again -> multiple trades + costs
    res = run_backtest(bars, [100, 100, -50, 0, 0])
    assert abs(res.reconciliation_residual()) < 1e-6


def test_cash_and_position_accounting():
    bars = _bars([10, 11, 12, 13])
    res = run_backtest(bars, [10, 10, 10, 10], initial_capital=1_000_000.0, cost=CostModel(0, 0))
    # buy 10 @ 11 (bar1), hold; final equity = 1_000_000 + 10*(13-11) = 1_000_020
    assert res.final_equity == 1_000_020.0
    assert res.total_pnl == 20.0


def test_metrics_on_rising_equity():
    m = metrics([100, 101, 102, 103, 104])
    assert m["total_return"] > 0 and m["sharpe"] > 0 and m["max_drawdown"] == 0.0


def test_max_drawdown_detects_decline():
    assert max_drawdown([100, 120, 60, 90]) == (120 - 60) / 120
