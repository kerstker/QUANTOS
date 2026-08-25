from __future__ import annotations

import math

import pytest

from services.research.alpha_expression import FieldPanel
from services.research.idiosyncratic_volatility import (
    IvolDataIntegrityError,
    IvolExperimentConfig,
    adverse_terminal_stress_periods,
    build_decision_plan,
    build_ivol_periods,
    find_first_eligible_decision_row,
    ivol_signal_leakage_ok,
    signal_snapshot,
)


def _panel(rows: int = 22, names: int = 8) -> FieldPanel:
    instruments = [f"c{index}" for index in range(names)]
    event_times = [1_700_000_000_000 + row * 86_400_000 for row in range(rows)]
    close = []
    for row in range(rows):
        market = 100.0 * (1.002**row)
        close.append(
            [
                market * (1.0 + ((row % (index + 2)) - index / 2.0) * index / 500.0)
                for index in range(names)
            ]
        )
    volume = [
        [10_000.0 * (index + 1) for index in range(names)]
        for _row in range(rows)
    ]
    return FieldPanel(instruments, event_times, {"close": close, "volume": volume})


def _config() -> IvolExperimentConfig:
    return IvolExperimentConfig(
        return_lookback_rows=5,
        holding_rows=2,
        universe_size=6,
        liquid_size=4,
    )


def test_config_rejects_invalid_or_non_nested_universes() -> None:
    with pytest.raises(ValueError, match="positive"):
        IvolExperimentConfig(return_lookback_rows=0)
    with pytest.raises(ValueError, match="liquid_size"):
        IvolExperimentConfig(universe_size=8, liquid_size=8)


def test_ivol_is_market_residual_volatility_not_total_volatility() -> None:
    panel = _panel()
    config = _config()
    decision_row = config.return_lookback_rows + 1
    plan = build_decision_plan(panel, decision_row, config.universe_size, config)

    instruments, ivol, adv = signal_snapshot(panel, plan, "idiosyncratic")
    _, total, _ = signal_snapshot(panel, plan, "total")

    assert instruments == plan.ranked_universe
    assert len(ivol) == config.universe_size
    assert len(adv) == config.universe_size
    assert all(value > 0.0 and math.isfinite(value) for value in ivol)
    assert ivol != total


def test_sign_control_is_exact_negative_and_hash_is_deterministic() -> None:
    panel = _panel()
    config = _config()
    plan = build_decision_plan(
        panel, config.return_lookback_rows + 1, config.universe_size, config
    )

    primary = signal_snapshot(panel, plan, "idiosyncratic")
    sign = signal_snapshot(panel, plan, "low_idiosyncratic")
    hashed = signal_snapshot(panel, plan, "hash")

    assert sign[1] == pytest.approx(tuple(-value for value in primary[1]))
    assert hashed == signal_snapshot(panel, plan, "hash")
    assert len(set(hashed[1])) == len(hashed[1])


def test_future_inputs_cannot_change_current_signal() -> None:
    panel = _panel()
    config = _config()
    decision_row = config.return_lookback_rows + 3
    plan = build_decision_plan(panel, decision_row, config.universe_size, config)
    baseline = signal_snapshot(panel, plan, "idiosyncratic")

    changed = _panel()
    for row in range(decision_row + 1, len(changed.event_times)):
        changed.matrices["close"][row] = [1e9 + row + col for col in range(8)]
        changed.matrices["volume"][row] = [1e18] * 8
    changed_plan = build_decision_plan(
        changed, decision_row, config.universe_size, config
    )

    assert signal_snapshot(changed, changed_plan, "idiosyncratic") == baseline
    assert ivol_signal_leakage_ok(
        changed,
        config,
        mode="idiosyncratic",
        universe_size=config.universe_size,
        start_row=config.return_lookback_rows + 1,
    ) is True


def test_terminal_delisting_uses_last_observed_trade_without_future_screening() -> None:
    panel = _panel()
    config = _config()
    start = config.return_lookback_rows + 1
    plan = build_decision_plan(panel, start, config.universe_size, config)
    selected = plan.ranked_universe[0]
    column = panel.instruments.index(selected)
    panel.matrices["close"][start + 2][column] = None
    for row in range(start + 3, len(panel.event_times)):
        panel.matrices["close"][row][column] = None
        panel.matrices["volume"][row][column] = None

    periods, _plans, forced = build_ivol_periods(
        panel,
        config,
        mode="idiosyncratic",
        universe_size=config.universe_size,
        start_row=start,
    )

    assert forced
    event = forced[0]
    assert event.instrument == selected
    assert event.exit_row == start + 1
    first = periods[0]
    index = first.instruments.index(selected)
    expected = (
        panel.matrices["close"][start + 1][column]
        / panel.matrices["close"][start][column]
        - 1.0
    )
    assert first.forward_returns[index] == pytest.approx(expected)


def test_missing_label_with_a_later_observation_is_data_gap_not_delisting() -> None:
    panel = _panel()
    config = _config()
    start = config.return_lookback_rows + 1
    selected = build_decision_plan(
        panel, start, config.universe_size, config
    ).ranked_universe[0]
    column = panel.instruments.index(selected)
    panel.matrices["close"][start + 2][column] = None

    with pytest.raises(IvolDataIntegrityError, match="non-terminal data gap") as captured:
        build_ivol_periods(
            panel,
            config,
            mode="idiosyncratic",
            universe_size=config.universe_size,
            start_row=start,
        )
    assert captured.value.reason_code == "MISSING_NON_TERMINAL_HOLDING_LABEL"


def test_adverse_terminal_stress_hurts_both_long_and_short_exposures() -> None:
    panel = _panel()
    config = _config()
    start = config.return_lookback_rows + 1
    periods, _plans, _forced = build_ivol_periods(
        panel,
        config,
        mode="idiosyncratic",
        universe_size=config.universe_size,
        start_row=start,
    )
    first = periods[0]
    order = sorted(range(len(first.signals)), key=lambda i: (first.signals[i], i))
    short_i, long_i = order[0], order[-1]
    from services.research.idiosyncratic_volatility import ForcedExitEvent

    events = (
        ForcedExitEvent(first.instruments[short_i], start, start + 2, start + 1),
        ForcedExitEvent(first.instruments[long_i], start, start + 2, start + 1),
    )
    stressed = adverse_terminal_stress_periods(
        periods, events, quantile=0.25, adverse_return=0.20
    )[0]

    assert stressed.forward_returns[long_i] == pytest.approx(
        first.forward_returns[long_i] - 0.20
    )
    assert stressed.forward_returns[short_i] == pytest.approx(
        first.forward_returns[short_i] + 0.20
    )


def test_first_eligible_decision_requires_complete_trailing_breadth() -> None:
    panel = _panel()
    config = _config()
    assert find_first_eligible_decision_row(panel, config) == (
        config.return_lookback_rows + 1
    )
