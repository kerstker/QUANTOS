from __future__ import annotations

import pytest

from services.research.funding_settlement import (
    DAY_MS,
    HOUR_MS,
    SETTLEMENT_INTERVAL_MS,
    WEEKLY_SETTLEMENT_COUNT,
    FundingSettlementError,
    SettlementObservation,
    build_settlement_periods,
    build_weekly_carry_periods,
)


def _history(*, periods: int = 5):
    result = {}
    for column in range(10):
        instrument = f"S{column:02d}USDT"
        rows = []
        for index in range(periods + 1):
            settlement_time = index * 8 * HOUR_MS
            # Stable ranks make the buffered and control books identical unless
            # the caller deliberately changes the cross-sectional ordering.
            rate = (column - 4.5) * 0.00001
            rows.append(
                SettlementObservation(
                    instrument_id=instrument,
                    settlement_time_ms=settlement_time,
                    decision_time_ms=settlement_time + HOUR_MS,
                    settled_rate=rate,
                    decision_mark_price=100.0 + column + index,
                )
            )
        result[instrument] = rows
    return result


def test_period_labels_use_only_next_settlement_cashflow() -> None:
    history = _history(periods=2)

    periods = build_settlement_periods(history, mode="rank_control")

    assert len(periods) == 2
    first = periods[0]
    assert first.event_time == HOUR_MS
    btc_like = first.instruments.index("S00USDT")
    expected = 101.0 / 100.0 - 1.0 - (-4.5e-5)
    assert first.forward_returns[btc_like] == pytest.approx(expected)
    assert first.signals[btc_like] == 1.0


def test_buffered_rank_retains_names_inside_fixed_exit_band() -> None:
    history = _history(periods=3)
    # S00 starts in the long entry tail.  At the next decision it falls just
    # outside the top-20% entry tail but remains inside the top-40% exit band.
    history["S00USDT"][1] = SettlementObservation(
        instrument_id="S00USDT",
        settlement_time_ms=8 * HOUR_MS,
        decision_time_ms=9 * HOUR_MS,
        settled_rate=-1.6e-5,
        decision_mark_price=101.0,
    )

    control = build_settlement_periods(history, mode="rank_control")
    buffered = build_settlement_periods(history, mode="buffered_rank")

    control_index = control[1].instruments.index("S00USDT")
    buffered_index = buffered[1].instruments.index("S00USDT")
    assert control[1].signals[control_index] == 0.0
    assert buffered[1].signals[buffered_index] == 1.0
    assert len(buffered[1].instruments) == 10
    assert sorted(buffered[1].signals) == [-1.0, -1.0] + [0.0] * 6 + [1.0, 1.0]


def test_builder_rejects_noncausal_or_irregular_observations() -> None:
    with pytest.raises(FundingSettlementError, match="after settlement"):
        SettlementObservation(
            instrument_id="S00USDT",
            settlement_time_ms=0,
            decision_time_ms=0,
            settled_rate=0.0001,
            decision_mark_price=100.0,
        )


def test_builder_fails_closed_below_minimum_cross_section() -> None:
    history = _history(periods=2)

    with pytest.raises(FundingSettlementError, match="no causal settlement periods"):
        build_settlement_periods(
            dict(list(history.items())[:9]),
            mode="buffered_rank",
            min_cross_section=10,
        )


def _weekly_history():
    monday_ms = 4 * DAY_MS + 10 * 7 * DAY_MS
    start_ms = monday_ms - (WEEKLY_SETTLEMENT_COUNT - 1) * SETTLEMENT_INTERVAL_MS
    result = {}
    for column in range(10):
        instrument = f"S{column:02d}USDT"
        base_rate = (column + 1) * 0.00001
        rows = []
        for index in range(2 * WEEKLY_SETTLEMENT_COUNT):
            settlement_time = start_ms + index * SETTLEMENT_INTERVAL_MS
            rate = base_rate
            # This rate settles after entry.  It belongs in realized cashflow,
            # never in the formation signal at the Monday decision.
            if column == 0 and settlement_time == monday_ms + SETTLEMENT_INTERVAL_MS:
                rate = 0.001
            rows.append(
                SettlementObservation(
                    instrument_id=instrument,
                    settlement_time_ms=settlement_time,
                    decision_time_ms=settlement_time + HOUR_MS,
                    settled_rate=rate,
                    decision_mark_price=100.0 + column,
                )
            )
        result[instrument] = rows
    return monday_ms, result


def test_weekly_carry_uses_trailing_21_rates_and_next_21_cashflows() -> None:
    monday_ms, history = _weekly_history()

    periods = build_weekly_carry_periods(history)

    assert len(periods) == 1
    period = periods[0]
    assert period.event_time == monday_ms + HOUR_MS
    index = period.instruments.index("S00USDT")
    assert period.signals[index] == pytest.approx(-0.00001)
    expected_cashflow = 0.001 + (WEEKLY_SETTLEMENT_COUNT - 1) * 0.00001
    assert period.forward_returns[index] == pytest.approx(-expected_cashflow)


def test_weekly_carry_has_one_fixed_monday_schedule_and_no_overlap() -> None:
    monday_ms, history = _weekly_history()
    for rows in history.values():
        final = rows[-1]
        for offset in range(1, WEEKLY_SETTLEMENT_COUNT + 1):
            settlement_time = final.settlement_time_ms + offset * SETTLEMENT_INTERVAL_MS
            rows.append(
                SettlementObservation(
                    instrument_id=final.instrument_id,
                    settlement_time_ms=settlement_time,
                    decision_time_ms=settlement_time + HOUR_MS,
                    settled_rate=final.settled_rate,
                    decision_mark_price=final.decision_mark_price,
                )
            )

    periods = build_weekly_carry_periods(history)

    assert [period.event_time for period in periods] == [
        monday_ms + HOUR_MS,
        monday_ms + 7 * DAY_MS + HOUR_MS,
    ]
