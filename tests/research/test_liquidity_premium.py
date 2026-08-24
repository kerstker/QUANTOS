from __future__ import annotations

import math

import pytest

from services.research.alpha_expression import FieldPanel
from services.research.liquidity_premium import (
    LiquidityExperimentConfig,
    build_decision_plan,
    build_liquidity_periods,
    find_first_eligible_decision_row,
    liquidity_signal_leakage_ok,
    signal_snapshot,
)


def _panel(rows: int = 18, names: int = 8) -> FieldPanel:
    instruments = [f"c{index}" for index in range(names)]
    event_times = [1_700_000_000_000 + row * 86_400_000 for row in range(rows)]
    close = [
        [100.0 + index + row * (index + 1) / 100.0 for index in range(names)]
        for row in range(rows)
    ]
    volume = [
        [1_000.0 * (index + 1) for index in range(names)]
        for _row in range(rows)
    ]
    return FieldPanel(instruments, event_times, {"close": close, "volume": volume})


def _config() -> LiquidityExperimentConfig:
    return LiquidityExperimentConfig(
        lookback_rows=3,
        holding_rows=2,
        universe_size=6,
        large_cap_size=4,
    )


def test_config_rejects_invalid_or_non_nested_universes() -> None:
    with pytest.raises(ValueError, match="positive"):
        LiquidityExperimentConfig(lookback_rows=0)
    with pytest.raises(ValueError, match="large_cap_size"):
        LiquidityExperimentConfig(universe_size=8, large_cap_size=8)


def test_decision_uses_only_completed_trailing_volume_and_current_availability() -> None:
    panel = _panel()
    config = _config()
    decision_row = config.lookback_rows

    baseline = build_decision_plan(panel, decision_row, config.universe_size, config)
    assert baseline.ranked_universe == ("c7", "c6", "c5", "c4", "c3", "c2")

    # The decision executes at row t. Its liquidity estimate is frozen from
    # [t-lookback, t), so even an extreme row-t volume cannot leak into ranking.
    changed = _panel()
    changed.matrices["volume"][decision_row][0] = 1e18
    observed = build_decision_plan(changed, decision_row, config.universe_size, config)
    assert observed == baseline

    # Current price availability is observable at execution and fails closed.
    changed.matrices["close"][decision_row][7] = None
    observed_without_missing = build_decision_plan(
        changed, decision_row, config.universe_size, config
    )
    assert "c7" not in observed_without_missing.ranked_universe


def test_illiquidity_signal_is_exact_negative_of_liquidity_control() -> None:
    panel = _panel()
    plan = build_decision_plan(panel, 3, 6, _config())

    illiquid = signal_snapshot(panel, plan, "illiquidity")
    liquid = signal_snapshot(panel, plan, "liquidity")

    assert illiquid[0] == liquid[0]
    assert illiquid[2] == liquid[2]
    assert illiquid[1] == pytest.approx(tuple(-value for value in liquid[1]))


def test_hash_placebo_is_deterministic_and_not_a_liquidity_rank() -> None:
    panel = _panel()
    plan = build_decision_plan(panel, 3, 6, _config())

    first = signal_snapshot(panel, plan, "hash")
    second = signal_snapshot(panel, plan, "hash")

    assert first == second
    assert first[1] != signal_snapshot(panel, plan, "illiquidity")[1]
    assert len(set(first[1])) == len(first[1])


def test_period_labels_are_non_overlapping_holding_returns() -> None:
    panel = _panel()
    config = _config()
    periods, plans = build_liquidity_periods(
        panel,
        config,
        mode="illiquidity",
        universe_size=config.universe_size,
        start_row=3,
    )

    assert [period.event_time for period in periods] == [
        panel.event_times[row] for row in range(3, len(panel.event_times) - 2, 2)
    ]
    assert [plan.decision_row for plan in plans] == list(
        range(3, len(panel.event_times) - 2, 2)
    )
    first = periods[0]
    for instrument, actual in zip(
        first.instruments, first.forward_returns, strict=True
    ):
        col = panel.instruments.index(instrument)
        expected = panel.matrices["close"][5][col] / panel.matrices["close"][3][col] - 1.0
        assert actual == pytest.approx(expected)
    assert all(value > 0.0 and math.isfinite(value) for value in first.adv_notional)


def test_future_label_or_future_volume_cannot_change_current_signal() -> None:
    panel = _panel()
    config = _config()
    decision_row = 5
    plan = build_decision_plan(panel, decision_row, config.universe_size, config)
    baseline = signal_snapshot(panel, plan, "illiquidity")

    changed = _panel()
    for row in range(decision_row + 1, len(changed.event_times)):
        changed.matrices["close"][row] = [value * 100.0 for value in changed.matrices["close"][row]]
        changed.matrices["volume"][row] = [1e18] * len(changed.instruments)
    changed_plan = build_decision_plan(
        changed, decision_row, config.universe_size, config
    )
    assert signal_snapshot(changed, changed_plan, "illiquidity") == baseline
    assert liquidity_signal_leakage_ok(
        changed,
        config,
        mode="illiquidity",
        universe_size=config.universe_size,
        start_row=3,
    ) is True


def test_missing_selected_forward_price_fails_closed() -> None:
    panel = _panel()
    panel.matrices["close"][5][7] = None

    with pytest.raises(ValueError, match="missing holding return"):
        build_liquidity_periods(
            panel,
            _config(),
            mode="illiquidity",
            universe_size=6,
            start_row=3,
        )


def test_earliest_decision_requires_complete_breadth_without_future_scan() -> None:
    panel = _panel()
    config = _config()
    for row in range(config.lookback_rows):
        for column in (5, 6, 7):
            panel.matrices["volume"][row][column] = None

    assert find_first_eligible_decision_row(panel, config) == 6
