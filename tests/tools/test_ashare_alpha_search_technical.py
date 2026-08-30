from __future__ import annotations

import numpy as np
import pytest

from tools.ashare_alpha_search_technical import (
    CandidateSpec,
    build_factor_matrix,
    make_rebalance_periods,
    simulate_long_only,
)


def test_rebalance_periods_enforce_close_t_to_open_t_plus_one():
    dates = np.array([20240102, 20240103, 20240104, 20240105, 20240108, 20240109])

    periods = make_rebalance_periods(dates, hold_days=2, phase=0)

    assert [(p.signal_row, p.entry_row, p.exit_row) for p in periods] == [
        (0, 1, 3),
        (2, 3, 5),
    ]
    assert all(p.entry_row > p.signal_row for p in periods)


def test_factor_matrix_is_unchanged_before_future_perturbation():
    close = np.array(
        [
            [10.0, 20.0],
            [11.0, 19.0],
            [12.0, 18.0],
            [13.0, 17.0],
            [14.0, 16.0],
            [15.0, 15.0],
        ]
    )
    volume = np.full_like(close, 1_000.0)
    amount = close * volume
    spec = CandidateSpec("momentum_3", "momentum", 3, 0, 5)

    base = build_factor_matrix(spec, close, volume, amount)
    perturbed_close = close.copy()
    perturbed_close[5] = [1_000.0, 0.01]
    changed = build_factor_matrix(spec, perturbed_close, volume, perturbed_close * volume)

    np.testing.assert_allclose(base[:5], changed[:5], equal_nan=True)


def test_signed_amount_flow_and_gap_factors_do_not_use_future_rows():
    close = np.array([[10.0, 20.0], [11.0, 19.0], [12.0, 18.0], [13.0, 17.0]])
    opens = np.array([[10.0, 20.0], [10.5, 19.5], [11.5, 18.5], [12.5, 17.5]])
    volume = np.full_like(close, 1_000.0)
    amount = close * volume
    specs = (
        CandidateSpec("flow", "signed_amount_flow", 2, 0, 5),
        CandidateSpec("gap", "overnight_gap_reversal", 2, 0, 5),
    )
    for spec in specs:
        base = build_factor_matrix(spec, close, volume, amount, opens)
        perturbed = close.copy()
        perturbed[-1] = [1_000.0, 0.01]
        changed = build_factor_matrix(spec, perturbed, volume, perturbed * volume, opens)
        np.testing.assert_allclose(base[:-1], changed[:-1], equal_nan=True)


def test_simulation_uses_signal_scores_but_next_open_execution():
    dates = np.array([20240102, 20240103, 20240104, 20240105])
    scores = np.array(
        [
            [2.0, 1.0],
            [2.0, 1.0],
            [2.0, 1.0],
            [2.0, 1.0],
        ]
    )
    opens = np.array(
        [
            [10.0, 10.0],
            [10.0, 10.0],
            [15.0, 10.0],
            [20.0, 10.0],
        ]
    )
    eligible = np.ones_like(scores, dtype=bool)
    periods = make_rebalance_periods(dates, hold_days=2, phase=0)

    result = simulate_long_only(
        scores,
        opens,
        eligible,
        periods,
        basket_size=1,
        cost_bps=0.0,
    )

    # Signal is observed at row 0, entry is row 1 open, and exit is row 3 open.
    assert result.period_returns == pytest.approx([1.0])
    assert result.selected_names == [[0]]


def test_simulation_charges_turnover_and_terminal_liquidation():
    dates = np.array([20240102, 20240103, 20240104, 20240105])
    scores = np.array([[2.0, 1.0]] * 4)
    opens = np.full_like(scores, 10.0)
    eligible = np.ones_like(scores, dtype=bool)
    periods = make_rebalance_periods(dates, hold_days=2, phase=0)

    result = simulate_long_only(
        scores,
        opens,
        eligible,
        periods,
        basket_size=1,
        cost_bps=10.0,
    )

    # One buy at entry plus one terminal sell: 2 * 10 bps.
    assert result.period_returns == pytest.approx([-0.002])
    assert result.turnovers == pytest.approx([2.0])


def test_simulation_reweights_across_the_actual_eligible_set():
    dates = np.array([20240102, 20240103, 20240104, 20240105])
    scores = np.array([[2.0, 1.0]] * 4)
    opens = np.array(
        [
            [10.0, 10.0],
            [10.0, 10.0],
            [15.0, 10.0],
            [20.0, 10.0],
        ]
    )
    eligible = np.array([[True, False]] * 4)

    result = simulate_long_only(
        scores,
        opens,
        eligible,
        make_rebalance_periods(dates, hold_days=2, phase=0),
        basket_size=2,
        cost_bps=0.0,
    )

    # The sole eligible name receives 100%, rather than 50% with 50% hidden cash.
    assert result.period_returns == pytest.approx([1.0])


def test_simulation_freezes_a_leg_that_crosses_a_bad_bar():
    dates = np.array([20240102, 20240103, 20240104, 20240105])
    scores = np.array([[2.0, 1.0]] * 4)
    opens = np.array([[10.0, 10.0], [10.0, 10.0], [100.0, 10.0], [20.0, 10.0]])
    eligible = np.ones_like(scores, dtype=bool)
    bad_bars = np.zeros_like(scores, dtype=bool)
    bad_bars[2, 0] = True

    result = simulate_long_only(
        scores,
        opens,
        eligible,
        make_rebalance_periods(dates, hold_days=2, phase=0),
        basket_size=1,
        cost_bps=0.0,
        bad_bars=bad_bars,
    )

    assert result.period_returns == pytest.approx([0.0])
    assert result.unpriced_legs == 1
