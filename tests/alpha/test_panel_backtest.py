"""Phase 6 step 5a — dollar-neutral net-of-cost cross-sectional long/short
panel backtest (critique P0-4)."""
import os
import subprocess
import sys

import pytest

from services.alpha.panel_backtest import (
    PanelPeriod,
    _dollar_neutral_weights,
    _run_panel_backtest_legacy_diagnostic as run_panel_backtest,
    run_panel_backtest as public_run_panel_backtest,
)
from services.validation.selection_authority import SelectionAuthorityBlocked
from services.research.backtest_engine.costs import CostModel


def _period(signals, fwd, adv=None):
    insts = tuple(f"i{i}" for i in range(len(signals)))
    return PanelPeriod(instruments=insts, signals=tuple(signals),
                       forward_returns=tuple(fwd), adv_notional=tuple(adv or ()))


def test_public_panel_backtest_is_quarantined_before_reading_periods():
    with pytest.raises(SelectionAuthorityBlocked, match="INVALIDATED_PERP_LEDGER_V1"):
        public_run_panel_backtest(object())


def test_weights_are_dollar_neutral():
    insts = tuple("abcde")
    w = _dollar_neutral_weights(insts, long_idx=[4], short_idx=[0], gross=1.0)
    assert abs(sum(w.values())) < 1e-12                 # net zero (dollar-neutral)
    assert sum(v for v in w.values() if v > 0) == 0.5   # long notional
    assert sum(v for v in w.values() if v < 0) == -0.5  # short notional


def test_long_winners_short_losers_is_profitable_gross():
    # signal ranks names; top goes up, bottom goes down
    p = _period(signals=[1, 2, 3, 4, 5], fwd=[-0.05, -0.02, 0.0, 0.02, 0.05])
    res = run_panel_backtest([p], cost=CostModel(0, 0), quantile=0.2)
    assert res.gross_returns[0] > 0                     # long winners / short losers
    assert res.reconciliation_residual() < 1e-12        # net == gross - cost


def test_costs_make_net_strictly_worse_and_reconcile():
    periods = [
        _period([1, 2, 3, 4, 5], [0.02, -0.01, 0.0, 0.01, 0.03]),
        _period([5, 4, 3, 2, 1], [0.01, 0.0, -0.01, 0.02, -0.02]),  # full flip -> turnover
    ]
    free = run_panel_backtest(periods, cost=CostModel(0, 0))
    costed = run_panel_backtest(periods, cost=CostModel(fee_bps=5, slippage_bps=5))
    assert costed.total_net_return() < free.total_net_return()
    assert all(c > 0 for c in costed.costs)
    assert costed.reconciliation_residual() < 1e-12


def test_market_impact_increases_cost_when_adv_is_small():
    p = _period([1, 2, 3, 4, 5], [0.0] * 5, adv=[1e5, 1e5, 1e5, 1e5, 1e5])  # tiny ADV
    no_impact = run_panel_backtest([p], cost=CostModel(1, 1, impact_coef=0), target_aum=1e8)
    with_impact = run_panel_backtest([p], cost=CostModel(1, 1, impact_coef=50), target_aum=1e8)
    assert with_impact.costs[0] > no_impact.costs[0]


def test_realized_max_participation_uses_exact_weight_changes() -> None:
    periods = [
        _period([1, 2, 3, 4, 5], [0.0] * 5, adv=[1_000.0] * 5),
        _period([5, 2, 3, 4, 1], [0.0] * 5, adv=[1_000.0] * 5),
    ]

    result = run_panel_backtest(
        periods,
        cost=CostModel(0, 0),
        quantile=0.2,
        target_aum=100.0,
    )

    assert result.max_participations == pytest.approx((0.05, 0.10))


def test_participation_is_unassessed_without_exact_adv_vector() -> None:
    result = run_panel_backtest(
        [_period([1, 2, 3], [0.0] * 3)],
        cost=CostModel(0, 0),
    )

    assert result.max_participations == (None,)


def test_dynamic_universe_exit_adv_pays_impact_and_binds_participation() -> None:
    first = PanelPeriod(
        instruments=("a", "b"),
        signals=(1.0, -1.0),
        forward_returns=(0.0, 0.0),
        adv_notional=(1_000.0, 1_000.0),
    )
    second = PanelPeriod(
        instruments=("c", "d"),
        signals=(1.0, -1.0),
        forward_returns=(0.0, 0.0),
        adv_notional=(1_000.0, 1_000.0),
        exit_adv_notional=(("a", 1_000.0), ("b", 1_000.0)),
    )

    free = run_panel_backtest(
        (first, second), cost=CostModel(0, 0), quantile=0.5, target_aum=100.0
    )
    impact = run_panel_backtest(
        (first, second),
        cost=CostModel(0, 0, impact_coef=20.0),
        quantile=0.5,
        target_aum=100.0,
    )

    assert impact.costs[1] > free.costs[1]
    assert impact.max_participations[1] == pytest.approx(0.05)


def test_dynamic_universe_missing_exit_adv_fails_closed() -> None:
    first = PanelPeriod(
        instruments=("a", "b"),
        signals=(1.0, -1.0),
        forward_returns=(0.0, 0.0),
        adv_notional=(1_000.0, 1_000.0),
    )
    second = PanelPeriod(
        instruments=("c", "d"),
        signals=(1.0, -1.0),
        forward_returns=(0.0, 0.0),
        adv_notional=(1_000.0, 1_000.0),
    )

    with pytest.raises(ValueError, match="missing current exit ADV"):
        run_panel_backtest(
            (first, second),
            cost=CostModel(0, 0, impact_coef=20.0),
            quantile=0.5,
            target_aum=100.0,
        )


def test_no_integer_share_dropping_cheap_names_get_weight():
    # a cheap-but-extreme-signal name still receives a notional weight (fractional)
    p = _period(signals=[10, 1, 2, 3, 4], fwd=[0.0] * 5)
    long_idx, short_idx = ([0], [1])  # name i0 has the top signal
    w = _dollar_neutral_weights(p.instruments, long_idx, short_idx, gross=1.0)
    assert w["i0"] > 0                                   # not rounded to zero shares


def test_panel_backtest_is_deterministic():
    periods = [_period([1, 2, 3, 4, 5], [0.01, -0.01, 0.0, 0.02, -0.02])]
    a = run_panel_backtest(periods, cost=CostModel(2, 3))
    b = run_panel_backtest(periods, cost=CostModel(2, 3))
    assert a.period_returns == b.period_returns


def test_panel_backtest_is_bit_identical_across_python_hash_seeds():
    script = """
from services.alpha.panel_backtest import PanelPeriod, _run_panel_backtest_legacy_diagnostic as run_panel_backtest
from services.research.backtest_engine.costs import CostModel
names = tuple(f"asset_{index}" for index in range(17))
periods = []
for row in range(9):
    signals = tuple(float((index * (row + 3)) % 19) for index in range(17))
    returns = tuple(((-1.0) ** index) * (index + row + 1) / 10000.0 for index in range(17))
    periods.append(PanelPeriod(names, signals, returns))
result = run_panel_backtest(periods, cost=CostModel(2.3, 4.7), quantile=0.29)
print([value.hex() for value in result.period_returns])
"""
    outputs = []
    for seed in ("1", "17", "103"):
        environment = dict(os.environ)
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")),
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        outputs.append(completed.stdout)

    assert len(set(outputs)) == 1


def test_rejects_duplicate_instruments_and_nonfinite_signals():
    dup = PanelPeriod(instruments=("A", "A"), signals=(1.0, 2.0), forward_returns=(0.0, 0.0))
    with pytest.raises(ValueError):
        run_panel_backtest([dup])
    nan = PanelPeriod(instruments=("A", "B"), signals=(1.0, float("nan")), forward_returns=(0.0, 0.0))
    with pytest.raises(ValueError):
        run_panel_backtest([nan])


def test_cost_magnitude_is_correct_not_tautological():
    # full entry at period 0: turnover = gross notional 1.0; cost = 1.0 * 20bps
    p = _period([1, 2, 3, 4, 5], [0.0] * 5)
    res = run_panel_backtest([p], cost=CostModel(fee_bps=10, slippage_bps=10), quantile=0.2)
    assert res.costs[0] == pytest.approx(0.002)
