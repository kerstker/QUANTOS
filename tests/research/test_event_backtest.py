import pytest
from quantos.contracts.data import Bar
from services.research.backtest_engine.costs import CostModel
from services.research.backtest_engine.event_engine import run_event_backtest


def _bar(inst, t, o, c):
    return Bar(instrument_id=inst, event_time=t, knowledge_time=t,
               open=o, high=max(o, c) + 1, low=min(o, c) - 0.5 if min(o, c) > 0.5 else 0.1,
               close=c, volume=1_000_000)


def _timeline(prices):
    return [(t, {inst: _bar(inst, t, oc[0], oc[1]) for inst, oc in slc.items()})
            for t, slc in enumerate(prices)]


def test_next_bar_open_fill_no_lookahead():
    tl = _timeline([{"A": (10.0, 10.0)}, {"A": (11.0, 12.0)}, {"A": (12.0, 12.0)}])
    targets = [{"A": 1.0}, {"A": 1.0}, {"A": 1.0}]
    r = run_event_backtest(tl, targets, initial_capital=100_000.0,
                           cost=CostModel(0.0, 0.0, 0.0), lot_size=1)
    assert r.equity_curve[0] == pytest.approx(100_000.0)             # no fill yet at bar 0
    assert r.reconciliation_residual() < 1e-6


def test_reconciliation_holds_long_short_and_flip():
    tl = _timeline([
        {"A": (10.0, 10.0), "B": (20.0, 20.0)},
        {"A": (10.0, 11.0), "B": (20.0, 19.0)},
        {"A": (11.0, 9.0), "B": (19.0, 19.0)},
        {"A": (9.0, 9.0), "B": (19.0, 19.0)},
    ])
    targets = [
        {"A": 0.5, "B": -0.5}, {"A": -0.5, "B": 0.5}, {"A": 0.0, "B": 0.0}, {"A": 0.0, "B": 0.0},
    ]
    r = run_event_backtest(tl, targets, initial_capital=1_000_000.0,
                           cost=CostModel(1.0, 2.0, 0.0), lot_size=1)
    assert r.reconciliation_residual() < 1e-6


def test_costs_make_net_worse_and_still_reconcile():
    tl = _timeline([{"A": (10.0, 10.0)}, {"A": (10.0, 11.0)}, {"A": (11.0, 11.0)}])
    targets = [{"A": 1.0}, {"A": 1.0}, {"A": 0.0}]
    free = run_event_backtest(tl, targets, initial_capital=100_000.0, cost=CostModel(0, 0, 0))
    costed = run_event_backtest(tl, targets, initial_capital=100_000.0, cost=CostModel(5, 5, 0))
    assert costed.final_equity() < free.final_equity()
    assert costed.reconciliation_residual() < 1e-6


def test_lot_rounding_keeps_integer_multiples():
    tl = _timeline([{"A": (10.0, 10.0)}, {"A": (10.0, 10.0)}])
    targets = [{"A": 1.0}, {"A": 1.0}]
    r = run_event_backtest(tl, targets, initial_capital=100_000.0,
                           cost=CostModel(0, 0, 0), lot_size=100)
    assert r.reconciliation_residual() < 1e-6


def test_market_impact_is_actually_charged_and_reconciles():
    # review: the engine never passed adv -> impact silently dropped. Now bar.volume
    # is the ADV proxy, so a positive impact_coef raises cost and still reconciles.
    tl = _timeline([{"A": (10.0, 10.0)}, {"A": (10.0, 11.0)}, {"A": (10.0, 11.0)}])
    targets = [{"A": 1.0}, {"A": 1.0}, {"A": 0.0}]
    base = run_event_backtest(tl, targets, initial_capital=100_000.0, cost=CostModel(0, 0, 0))
    impact = run_event_backtest(tl, targets, initial_capital=100_000.0,
                                cost=CostModel(0, 0, impact_coef=50.0))
    assert impact.total_cost > base.total_cost
    assert impact.reconciliation_residual() < 1e-6


def test_is_deterministic():
    tl = _timeline([{"A": (10.0, 10.0)}, {"A": (10.0, 11.0)}])
    targets = [{"A": 1.0}, {"A": 1.0}]
    a = run_event_backtest(tl, targets, initial_capital=100_000.0)
    b = run_event_backtest(tl, targets, initial_capital=100_000.0)
    assert a.equity_curve == b.equity_curve and a.total_cost == b.total_cost


def test_fail_closed_inputs():
    tl = _timeline([{"A": (10.0, 10.0)}, {"A": (10.0, 11.0)}])
    with pytest.raises(ValueError):
        run_event_backtest(tl, [{"A": 1.0}], initial_capital=100_000.0)        # length mismatch
    with pytest.raises(ValueError):
        run_event_backtest(tl, [{"A": 1.0}, {"A": 1.0}], initial_capital=0.0)  # bad capital
    with pytest.raises(ValueError):
        run_event_backtest(tl, [{"B": 1.0}, {"B": 1.0}], initial_capital=1.0)  # B absent at bar
    with pytest.raises(ValueError):
        run_event_backtest(tl, [{"A": 1.0}, {"A": 1.0}], initial_capital=1.0, lot_size=0)
