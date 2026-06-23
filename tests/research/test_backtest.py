"""Phase 4 — backtest engine: determinism, costs, next-bar fill (no lookahead),
PnL reconciliation, cash/position accounting, metrics."""
from quantos.contracts.data import Bar
from services.data.pit_store import PITStore
from services.research.backtest_engine.costs import CostModel
from services.research.backtest_engine.engine import run_backtest
from services.research.backtest_engine.metrics import max_drawdown, metrics, sharpe, sortino
from services.research.backtest_engine.report import capacity_estimate, to_backtest_result


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


def test_market_impact_increases_cost_and_still_reconciles():
    bars = _bars([10, 11, 12, 13])
    no_impact = run_backtest(bars, [1000, 1000, 1000, 1000], cost=CostModel(1, 2, impact_coef=0))
    with_impact = run_backtest(bars, [1000, 1000, 1000, 1000], cost=CostModel(1, 2, impact_coef=50))
    assert with_impact.total_cost > no_impact.total_cost
    assert abs(with_impact.reconciliation_residual()) < 1e-6   # still net-of-cost exact


def test_backtest_runs_on_real_pit_store():
    # feed the engine from the PIT data layer (as-of view), proving integration
    store = PITStore()
    bars = _bars([10, 11, 12, 13, 14])
    store.add_many("X", bars)
    as_of_bars = store.as_of("X", as_of_time=10_000)   # all knowable
    res = run_backtest(as_of_bars, [100, 100, 100, 100, 100], cost=CostModel(0, 0))
    assert abs(res.reconciliation_residual()) < 1e-6
    assert len(res.equity_curve) == 5


def test_to_backtest_result_contract_is_reproducible():
    bars = _bars([10, 11, 12, 13])
    res = run_backtest(bars, [10, 10, 10, 10], cost=CostModel(0, 0))
    c1 = to_backtest_result("req-1", res)
    c2 = to_backtest_result("req-1", run_backtest(bars, [10, 10, 10, 10], cost=CostModel(0, 0)))
    assert c1.net_of_cost is True
    assert c1.reproducibility_hash == c2.reproducibility_hash and len(c1.reproducibility_hash) == 16


def test_capacity_estimate():
    assert capacity_estimate(1_000_000.0, max_participation=0.1) == 100_000.0


def test_max_drawdown_clamped_on_negative_equity():
    # a >100% loss (equity goes negative) clamps to 1.0, never exceeds it
    assert max_drawdown([100, 50, -10]) == 1.0


def test_sortino_infinite_when_no_downside():
    assert sortino([0.01, 0.02, 0.03]) == float("inf")


def test_calmar_is_annualized_and_finite():
    m = metrics([100, 90, 120], periods_per_year=252)  # has a drawdown
    assert m["calmar"] > 0 and m["max_drawdown"] <= 1.0
