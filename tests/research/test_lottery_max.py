from __future__ import annotations

import math

import pytest

from services.research.alpha_expression import FieldPanel
from services.research.idiosyncratic_volatility import (
    IvolExperimentConfig,
    build_decision_plan,
    find_first_eligible_decision_row,
    signal_snapshot,
)
from services.research.lottery_max import (
    LOTTERY_MAX_CONFIG,
    build_lottery_max_periods,
    lottery_max_leakage_ok,
)


def _panel(rows: int = 52, names: int = 10) -> FieldPanel:
    instruments = [f"coin{index}" for index in range(names)]
    event_times = [1_700_000_000_000 + row * 86_400_000 for row in range(rows)]
    close = []
    for row in range(rows):
        values = []
        for index in range(names):
            drift = 1.0 + (index - names / 2.0) / 10_000.0
            lottery = 1.0 + (0.012 + index / 2_000.0) * (
                1.0 if row % 7 == index % 7 else 0.0
            )
            values.append(50.0 * (1.001**row) * (drift**row) * lottery)
        close.append(values)
    volume = [
        [25_000.0 * (names - index) for index in range(names)]
        for _row in range(rows)
    ]
    return FieldPanel(instruments, event_times, {"close": close, "volume": volume})


def _config() -> IvolExperimentConfig:
    return IvolExperimentConfig(
        return_lookback_rows=7,
        liquidity_lookback_rows=10,
        holding_rows=3,
        universe_size=8,
        liquid_size=5,
    )


def test_frozen_config_separates_signal_and_liquidity_windows() -> None:
    assert LOTTERY_MAX_CONFIG.return_lookback_rows == 7
    assert LOTTERY_MAX_CONFIG.effective_liquidity_lookback_rows == 28
    assert LOTTERY_MAX_CONFIG.holding_rows == 7
    assert LOTTERY_MAX_CONFIG.universe_size == 60
    assert LOTTERY_MAX_CONFIG.liquid_size == 30


def test_first_decision_waits_for_longer_liquidity_window() -> None:
    config = _config()
    assert find_first_eligible_decision_row(_panel(), config) == 10


def test_low_max_and_competing_high_max_are_exact_sign_controls() -> None:
    panel = _panel()
    config = _config()
    plan = build_decision_plan(panel, 10, config.universe_size, config)

    low = signal_snapshot(panel, plan, "low_max")
    high = signal_snapshot(panel, plan, "high_max")
    reversal = signal_snapshot(panel, plan, "short_term_reversal")

    assert low[1] == pytest.approx(tuple(-value for value in high[1]))
    assert all(math.isfinite(value) for value in high[1])
    assert high[1] == plan.maximum_daily_log_return
    assert reversal[1] != low[1]


def test_lottery_hash_is_deterministic_and_domain_separated() -> None:
    panel = _panel()
    config = _config()
    plan = build_decision_plan(panel, 10, config.universe_size, config)

    lottery = signal_snapshot(panel, plan, "lottery_hash")
    ivol = signal_snapshot(panel, plan, "hash")

    assert lottery == signal_snapshot(panel, plan, "lottery_hash")
    assert lottery[1] != ivol[1]
    assert len(set(lottery[1])) == len(lottery[1])


def test_future_extremes_cannot_change_current_max_signal() -> None:
    panel = _panel()
    config = _config()
    target = 16
    baseline_plan = build_decision_plan(panel, target, config.universe_size, config)
    baseline = signal_snapshot(panel, baseline_plan, "low_max")

    for row in range(target + 1, len(panel.event_times)):
        panel.matrices["close"][row] = [1e12 + row + column for column in range(10)]
        panel.matrices["volume"][row] = [1e18] * 10
    changed_plan = build_decision_plan(panel, target, config.universe_size, config)

    assert signal_snapshot(panel, changed_plan, "low_max") == baseline
    assert lottery_max_leakage_ok(
        panel,
        config,
        mode="low_max",
        universe_size=config.universe_size,
        start_row=10,
    ) is True


@pytest.mark.parametrize(
    "mode",
    ("low_max", "high_max", "short_term_reversal", "lottery_hash"),
)
def test_all_frozen_modes_share_one_nonoverlapping_calendar(mode: str) -> None:
    panel = _panel()
    config = _config()
    periods, plans, forced = build_lottery_max_periods(
        panel,
        config,
        mode=mode,
        universe_size=config.universe_size,
        start_row=10,
        stop_row=40,
    )

    assert [plan.decision_row for plan in plans] == list(range(10, 40, 3))
    assert len(periods) == len(plans) == 10
    assert forced == ()
