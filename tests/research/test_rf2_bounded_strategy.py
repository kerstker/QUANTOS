"""RF2 bounded strategy signal tests: LOG_SLOPE_90_HYSTERESIS_V1.

Exercises the frozen RF2 pure signal described in
docs/engineering/plans/2026-09-20-rf2-bounded-strategy-experiment.md: the causal
90 completed-UTC-day window (+5min availability), the centred-index OLS log-price
slope t-statistic, the documented hysteresis transitions, the fail-closed
statuses and future invariance.  Nothing here reads data, network, ledger,
runner or risk gates.
"""
from __future__ import annotations

import inspect
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from math import exp

import pytest

from services.research.rf2_bounded_strategy import (
    AVAILABILITY_DELAY,
    ENTRY_THRESHOLD,
    EXIT_THRESHOLD,
    RESIDUAL_ZERO_EPSILON,
    SLOPE_ZERO_EPSILON,
    TARGET_WEIGHT,
    WINDOW_DAYS,
    DailyClose,
    MalformedInputError,
    SignalDecision,
    SignalStatus,
    available_at,
    benchmark_return_sign,
    benchmark_target_weight,
    calculate_decision,
    expected_window_last_day,
    fit_log_slope,
    held_sign_from_weight,
    next_target_weight,
    select_window,
)

UTC = timezone.utc
ZERO = Decimal("0")
LONG = Decimal("0.25")
SHORT = Decimal("-0.25")
LAST_DAY = date(2025, 12, 31)  # last completed UTC day knowable at DECISION
DECISION = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
BASE_PRICE = 30000.0


def window_days(last: date = LAST_DAY, count: int = WINDOW_DAYS) -> list[date]:
    """`count` consecutive UTC days ending at `last`."""
    return [last - timedelta(days=offset) for offset in range(count - 1, -1, -1)]


def records(days, closes) -> list[DailyClose]:
    return [DailyClose(day=day, close=Decimal(str(close))) for day, close in zip(days, closes)]


def rising(index: int) -> float:
    """+0.4%/day drift with a deterministic +-2% wiggle, so residuals are not zero."""
    return BASE_PRICE * exp(0.004 * index + 0.02 * (1.0 if index % 2 else -1.0))


def falling(index: int) -> float:
    return BASE_PRICE * exp(-0.004 * index + 0.02 * (1.0 if index % 2 else -1.0))


def noisy_flat(index: int) -> float:
    """No drift, +-2% wiggle: near-flat in slope terms but not numerically flat."""
    return BASE_PRICE * exp(0.02 * (1.0 if index % 2 else -1.0))


def perfect_exponential(index: int) -> str:
    """Exact geometric series: non-flat, essentially zero residual variance."""
    return repr(BASE_PRICE * exp(0.004 * index))


def flat_records() -> list[DailyClose]:
    return records(window_days(), [BASE_PRICE] * WINDOW_DAYS)


# --- window selection and availability -------------------------------------------------


def test_select_window_returns_last_ninety_completed_days():
    recs = records(window_days(count=WINDOW_DAYS + 30), [noisy_flat(i) for i in range(WINDOW_DAYS + 30)])
    selection = select_window(recs, DECISION)
    assert selection.status is SignalStatus.ACTIVE
    assert selection.available_days == WINDOW_DAYS + 30
    assert len(selection.days) == WINDOW_DAYS
    assert selection.days[0] == LAST_DAY - timedelta(days=WINDOW_DAYS - 1)
    assert selection.days[-1] == LAST_DAY
    assert len(selection.closes) == WINDOW_DAYS
    assert all(isinstance(close, Decimal) for close in selection.closes)


def test_exact_availability_boundary_is_day_end_plus_delay():
    assert AVAILABILITY_DELAY == timedelta(minutes=5)
    assert available_at(LAST_DAY) == DECISION  # 2026-01-01T00:00Z + 5min
    recs = records(window_days(count=WINDOW_DAYS + 1), [noisy_flat(i) for i in range(WINDOW_DAYS + 1)])

    at_boundary = calculate_decision(recs, DECISION, 0)
    assert at_boundary.window_last_day == LAST_DAY
    assert at_boundary.window_size == WINDOW_DAYS

    just_before = calculate_decision(recs, DECISION - timedelta(seconds=1), 0)
    assert just_before.window_last_day == LAST_DAY - timedelta(days=1)
    assert just_before.window_size == WINDOW_DAYS


def test_decision_day_close_is_never_selected():
    days = window_days() + [date(2026, 1, 1)]
    closes = [noisy_flat(i) for i in range(WINDOW_DAYS)] + [BASE_PRICE * 100.0]
    selection = select_window(records(days, closes), DECISION)
    assert selection.status is SignalStatus.ACTIVE
    assert selection.days[-1] == LAST_DAY
    assert date(2026, 1, 1) not in selection.days


def test_insufficient_history_is_distinct_from_malformed_data():
    short = records(window_days(count=WINDOW_DAYS - 1), [noisy_flat(i) for i in range(WINDOW_DAYS - 1)])
    insufficient = calculate_decision(short, DECISION, 1)
    assert insufficient.status is SignalStatus.INSUFFICIENT_HISTORY
    assert "insufficient" in insufficient.reason
    assert insufficient.statistic is None
    assert insufficient.target_weight == ZERO
    assert insufficient.held_sign_before == 1 and insufficient.held_sign_after == 0
    assert insufficient.available_days == WINDOW_DAYS - 1
    assert calculate_decision([], DECISION, 0).status is SignalStatus.INSUFFICIENT_HISTORY

    days = window_days(count=WINDOW_DAYS + 1)
    gap = days.pop(10)  # 90 available days spanning 91 calendar days
    malformed = calculate_decision(records(days, [noisy_flat(i) for i in range(len(days))]), DECISION, 1)
    assert malformed.status is SignalStatus.MALFORMED_INPUT
    assert malformed.status is not insufficient.status
    assert "missing" in malformed.reason and gap.isoformat() in malformed.reason
    assert malformed.statistic is None
    assert malformed.target_weight == ZERO
    assert malformed.held_sign_after == 0
    assert malformed.available_days == WINDOW_DAYS


def test_missing_day_elsewhere_does_not_invalidate_a_consecutive_window():
    days = window_days(count=WINDOW_DAYS + 5)
    days.pop(0)  # gap far outside the required 90-day window
    recs = records(days, [noisy_flat(i) for i in range(len(days))])
    selection = select_window(recs, DECISION)
    assert selection.status is SignalStatus.ACTIVE
    assert selection.days[-1] == LAST_DAY


def test_duplicate_available_day_is_malformed():
    recs = records(window_days(), [noisy_flat(i) for i in range(WINDOW_DAYS)])
    recs.append(recs[5])
    decision = calculate_decision(recs, DECISION, 1)
    assert decision.status is SignalStatus.MALFORMED_INPUT
    assert "duplicate" in decision.reason
    assert decision.target_weight == ZERO
    assert decision.held_sign_after == 0


# --- anchored window: a stale tail is a gap, never a shorter/older window --------------


def stale_long_records(count: int, end: date) -> list[DailyClose]:
    """`count` consecutive days ending at `end`, on a clean +0.4%/day uptrend."""
    return records(window_days(last=end, count=count), [rising(index) for index in range(count)])


def test_expected_window_last_day_is_anchored_to_the_decision_instant():
    assert expected_window_last_day(DECISION) == LAST_DAY
    for earlier in (DECISION - timedelta(microseconds=1), DECISION - timedelta(seconds=1), DECISION - AVAILABILITY_DELAY):
        assert expected_window_last_day(earlier) == LAST_DAY - timedelta(days=1), earlier  # not yet knowable
    assert expected_window_last_day(DECISION + timedelta(hours=18)) == LAST_DAY  # later the same UTC day
    assert expected_window_last_day(DECISION + timedelta(days=1)) == LAST_DAY + timedelta(days=1)
    assert expected_window_last_day(datetime(1, 1, 1, tzinfo=UTC)) is None  # no day is knowable that early
    for bad_moment in (datetime(2026, 1, 1, 0, 5), True, "2026-01-01T00:05Z", None):
        with pytest.raises(MalformedInputError):
            expected_window_last_day(bad_moment)

    # the anchor is exactly the newest day the availability rule admits
    for moment in (DECISION, DECISION - timedelta(seconds=1), DECISION + timedelta(hours=18)):
        anchor = expected_window_last_day(moment)
        assert available_at(anchor) <= moment
        assert available_at(anchor + timedelta(days=1)) > moment


def test_one_day_stale_tail_is_rejected_and_never_traded():
    """REGRESSION: 90 rows ending 2025-12-30 at the 2026-01-01T00:05Z decision.

    2025-12-31 closes at 2026-01-01T00:00Z and is knowable from 00:05Z, so it is a required
    member of the 90-day window.  Its absence is a data gap: the window must not slide one day
    back and report ACTIVE (a long, on rising rows) from a stale tail.
    """
    stale_end = LAST_DAY - timedelta(days=1)  # 2025-12-30
    recs = stale_long_records(WINDOW_DAYS, stale_end)
    assert len(recs) == WINDOW_DAYS
    assert LAST_DAY not in [record.day for record in recs]

    selection = select_window(recs, DECISION)
    assert selection.status is SignalStatus.MALFORMED_INPUT
    assert not selection.ok
    assert selection.days == () and selection.closes == ()

    decision = calculate_decision(recs, DECISION, 1)
    assert decision.status is SignalStatus.MALFORMED_INPUT
    assert decision.status is not SignalStatus.ACTIVE
    assert decision.statistic is None
    assert decision.target_weight == ZERO  # never the ACTIVE long a shortened window would emit
    assert decision.held_sign_before == 1 and decision.held_sign_after == 0
    assert decision.window_size == 0 and decision.window_last_day is None
    assert decision.benchmark_return_sign is None and decision.benchmark_target_weight == ZERO
    assert f"missing day {LAST_DAY.isoformat()}" in decision.reason
    assert decision.available_days == WINDOW_DAYS

    # one second before 00:05Z the required window still ends 2025-12-30: these rows are complete
    just_before = calculate_decision(recs, DECISION - timedelta(seconds=1), 1)
    assert just_before.status is SignalStatus.ACTIVE
    assert just_before.window_last_day == stale_end
    assert just_before.window_size == WINDOW_DAYS
    assert just_before.target_weight == LONG


def test_multi_day_stale_tail_is_rejected_despite_ample_rows():
    stale_end = LAST_DAY - timedelta(days=6)  # 2025-12-25; 2025-12-26..31 are all absent
    recs = stale_long_records(WINDOW_DAYS + 30, stale_end)
    assert LAST_DAY not in [record.day for record in recs]

    assert select_window(recs, DECISION).status is SignalStatus.MALFORMED_INPUT

    decision = calculate_decision(recs, DECISION, 0)
    assert decision.status is SignalStatus.MALFORMED_INPUT
    assert decision.statistic is None
    assert decision.target_weight == ZERO
    assert decision.held_sign_before == 0 and decision.held_sign_after == 0
    assert decision.window_size == 0 and decision.window_last_day is None
    assert decision.available_days == WINDOW_DAYS + 30  # plenty of older rows, still stale
    assert f"missing day {(LAST_DAY - timedelta(days=5)).isoformat()}" in decision.reason
    assert LAST_DAY.isoformat() in decision.reason  # the anchored window end is reported

    # a later decision moves the required window further away from the stale tail: still rejected
    assert select_window(recs, DECISION + timedelta(days=1)).status is SignalStatus.MALFORMED_INPUT


def test_stale_tail_offers_no_closes_to_the_fit_or_the_benchmark():
    recs = stale_long_records(WINDOW_DAYS, LAST_DAY - timedelta(days=1))
    selection = select_window(recs, DECISION)
    assert selection.closes == ()
    with pytest.raises(MalformedInputError):
        fit_log_slope(selection.closes)  # a rejected window never reaches the estimate


# --- malformed fields ------------------------------------------------------------------


def test_bool_timestamp_is_rejected():
    days = window_days()
    recs = records(days, [rising(i) for i in range(WINDOW_DAYS)])
    recs[0] = DailyClose(day=True, close=Decimal("30000"))
    decision = calculate_decision(recs, DECISION, 0)
    assert decision.status is SignalStatus.MALFORMED_INPUT
    assert "bool" in decision.reason
    assert decision.target_weight == ZERO
    assert decision.statistic is None


@pytest.mark.parametrize(
    "bad_day",
    [
        datetime(2025, 12, 31, 12, 0, tzinfo=UTC),  # not a whole UTC day
        datetime(2025, 12, 31, 0, 0),  # naive datetime
        "2025-12-31",  # not a date object
        1767139200,  # raw epoch integer
        None,
    ],
)
def test_non_integral_or_non_date_day_is_rejected(bad_day):
    recs = records(window_days(), [rising(i) for i in range(WINDOW_DAYS)])
    recs[0] = DailyClose(day=bad_day, close=recs[0].close)
    decision = calculate_decision(recs, DECISION, 0)
    assert decision.status is SignalStatus.MALFORMED_INPUT
    assert decision.target_weight == ZERO


def test_midnight_utc_datetime_day_is_accepted():
    days = window_days()
    recs = records(days, [rising(i) for i in range(WINDOW_DAYS)])
    first = days[0]
    recs[0] = DailyClose(day=datetime(first.year, first.month, first.day, tzinfo=UTC), close=recs[0].close)
    assert calculate_decision(recs, DECISION, 0).status is SignalStatus.ACTIVE


def test_non_daily_close_records_are_rejected():
    recs = records(window_days(), [rising(i) for i in range(WINDOW_DAYS)])
    decision = calculate_decision([(LAST_DAY, Decimal("30000"))] + recs, DECISION, 0)
    assert decision.status is SignalStatus.MALFORMED_INPUT
    assert "DailyClose" in decision.reason


@pytest.mark.parametrize(
    "bad_close",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal("0"),
        Decimal("-1"),
        Decimal("1e-999"),  # positive but underflows to zero as a float
        "30000",
        True,
        None,
    ],
)
def test_malformed_close_inside_the_window_is_rejected(bad_close):
    days = window_days()
    recs = records(days, [rising(i) for i in range(WINDOW_DAYS)])
    recs[45] = DailyClose(day=days[45], close=bad_close)
    decision = calculate_decision(recs, DECISION, 1)
    assert decision.status is SignalStatus.MALFORMED_INPUT
    assert decision.statistic is None
    assert decision.target_weight == ZERO
    assert decision.held_sign_before == 1 and decision.held_sign_after == 0


@pytest.mark.parametrize("bad_time", [True, 0, "2026-01-01T00:05Z", datetime(2026, 1, 1, 0, 5), None])
def test_decision_time_must_be_an_aware_utc_datetime(bad_time):
    recs = records(window_days(), [rising(i) for i in range(WINDOW_DAYS)])
    with pytest.raises(MalformedInputError):
        select_window(recs, bad_time)
    with pytest.raises(MalformedInputError):
        calculate_decision(recs, bad_time, 0)


# --- causal invariance -----------------------------------------------------------------


def test_unselected_prices_are_not_validated():
    recs = records(window_days(), [rising(i) for i in range(WINDOW_DAYS)])
    baseline = calculate_decision(recs, DECISION, 0)

    # (a) records that are not yet knowable at the decision instant are skipped entirely,
    #     so even unrepresentable prices in them cannot shift the decision
    unknowable = list(recs) + [
        DailyClose(day=date(2026, 1, 1), close=Decimal("NaN")),  # decision day: still open at 00:05Z
        DailyClose(day=date(2026, 2, 1), close=Decimal("-1")),
        DailyClose(day=date(2027, 1, 1), close=Decimal("Infinity")),
    ]
    assert calculate_decision(unknowable, DECISION, 0) == baseline

    # (b) a knowable but unselected record outside the required window is not price-validated either
    unselected = [DailyClose(day=LAST_DAY - timedelta(days=200), close=Decimal("-5"))] + list(recs)
    with_outlier = calculate_decision(unselected, DECISION, 0)
    assert with_outlier.available_days == WINDOW_DAYS + 1  # the outlier is counted, not used
    assert with_outlier.window_size == WINDOW_DAYS
    for field in ("status", "reason", "statistic", "target_weight", "window_last_day", "benchmark_return_sign"):
        assert getattr(with_outlier, field) == getattr(baseline, field), field


def test_future_records_do_not_change_a_historical_decision():
    base = records(window_days(), [rising(i) for i in range(WINDOW_DAYS)])
    historical = calculate_decision(base, DECISION, 0)

    tail_days = [date(2026, 1, 1) + timedelta(days=offset) for offset in range(10)]
    rising_tail = [
        DailyClose(day=day, close=Decimal(repr(BASE_PRICE * exp(0.02 * index)))) for index, day in enumerate(tail_days)
    ]
    falling_tail = [
        DailyClose(day=day, close=Decimal(repr(BASE_PRICE * exp(-0.02 * index)))) for index, day in enumerate(tail_days)
    ]

    assert calculate_decision(base + rising_tail, DECISION, 0) == historical
    assert calculate_decision(base + falling_tail, DECISION, 0) == historical
    assert calculate_decision(base + falling_tail, DECISION, 1) != historical

    later = datetime(2026, 1, 11, 0, 5, tzinfo=UTC)  # tail is causal exactly here
    decided_rising = calculate_decision(base + rising_tail, later, 0)
    decided_falling = calculate_decision(base + falling_tail, later, 0)
    assert decided_rising.window_last_day == date(2026, 1, 10)
    assert decided_rising.statistic is not None and decided_falling.statistic is not None
    assert decided_rising.statistic != decided_falling.statistic


def test_decisions_are_pure_and_input_order_independent():
    recs = records(window_days(), [rising(i) for i in range(WINDOW_DAYS)])
    first = calculate_decision(recs, DECISION, 0)
    assert calculate_decision(list(recs), DECISION, 0) == first
    assert calculate_decision(list(reversed(recs)), DECISION, 0) == first
    assert calculate_decision(recs, DECISION, 1) != first


# --- statistic and degenerate estimates ------------------------------------------------


def test_flat_window_statistic_is_exactly_zero():
    recs = flat_records()
    fit = fit_log_slope([record.close for record in recs])
    assert fit.status is SignalStatus.ACTIVE
    assert fit.statistic == 0.0
    assert "flat" in fit.reason

    decision = calculate_decision(recs, DECISION, 0)
    assert decision.status is SignalStatus.ACTIVE
    assert decision.statistic == 0.0
    assert decision.target_weight == ZERO
    assert decision.held_sign_after == 0
    assert decision.benchmark_return_sign == 0
    assert decision.benchmark_target_weight == ZERO


def test_flat_window_exits_an_open_long():
    decision = calculate_decision(flat_records(), DECISION, 1)
    assert decision.statistic == 0.0
    assert decision.target_weight == ZERO
    assert decision.held_sign_before == 1 and decision.held_sign_after == 0


def test_noisy_flat_window_has_small_nonzero_statistic():
    recs = records(window_days(), [noisy_flat(i) for i in range(WINDOW_DAYS)])
    decision = calculate_decision(recs, DECISION, 0)
    assert decision.status is SignalStatus.ACTIVE
    assert decision.statistic is not None
    assert decision.statistic != 0.0  # not swallowed by SLOPE_ZERO_EPSILON
    assert abs(decision.statistic) < ENTRY_THRESHOLD
    assert decision.target_weight == ZERO
    assert decision.benchmark_return_sign == 1  # first/last wiggle differ: paired baseline is biased


def test_exact_geometric_series_is_degenerate_and_never_infinite():
    closes = [Decimal(perfect_exponential(i)) for i in range(WINDOW_DAYS)]
    fit = fit_log_slope(closes)
    assert fit.status is SignalStatus.DEGENERATE_ESTIMATE
    assert fit.statistic is None
    assert abs(fit.slope) > SLOPE_ZERO_EPSILON  # non-flat
    assert fit.residual_standard_error <= RESIDUAL_ZERO_EPSILON
    assert "degenerate" in fit.reason

    decision = calculate_decision(records(window_days(), closes), DECISION, 1)
    assert decision.status is SignalStatus.DEGENERATE_ESTIMATE
    assert decision.statistic is None
    assert decision.target_weight == ZERO
    assert decision.held_sign_before == 1 and decision.held_sign_after == 0
    assert "unavailable" in decision.reason


def test_rising_and_falling_windows_open_the_expected_side():
    rising_decision = calculate_decision(
        records(window_days(), [rising(i) for i in range(WINDOW_DAYS)]), DECISION, 0
    )
    assert rising_decision.status is SignalStatus.ACTIVE
    assert rising_decision.statistic is not None and rising_decision.statistic > ENTRY_THRESHOLD
    assert rising_decision.target_weight == LONG
    assert rising_decision.held_sign_before == 0 and rising_decision.held_sign_after == 1
    assert rising_decision.window_size == WINDOW_DAYS
    assert rising_decision.window_last_day == LAST_DAY
    assert rising_decision.benchmark_return_sign == 1
    assert rising_decision.benchmark_target_weight == LONG

    falling_decision = calculate_decision(
        records(window_days(), [falling(i) for i in range(WINDOW_DAYS)]), DECISION, 0
    )
    assert falling_decision.status is SignalStatus.ACTIVE
    assert falling_decision.statistic is not None and falling_decision.statistic < -ENTRY_THRESHOLD
    assert falling_decision.target_weight == SHORT
    assert falling_decision.held_sign_after == -1
    assert falling_decision.benchmark_return_sign == -1
    assert falling_decision.benchmark_target_weight == SHORT


# --- state machine ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("held", "statistic", "target", "after"),
    [
        (0, 2.0, LONG, 1),  # exact entry boundary is inclusive
        (0, 1.999999, ZERO, 0),
        (0, -2.0, SHORT, -1),
        (0, -1.999999, ZERO, 0),
        (0, 0.0, ZERO, 0),
        (1, 0.0, ZERO, 0),  # statistic <= 0 closes a long
        (1, -0.0, ZERO, 0),
        (1, -2.0, ZERO, 0),  # no immediate reversal: never SHORT in the same decision
        (1, 5e-12, LONG, 1),
        (-1, 0.0, ZERO, 0),
        (-1, -0.0, ZERO, 0),
        (-1, 2.0, ZERO, 0),  # symmetric: no immediate reversal
        (-1, -5e-12, SHORT, -1),
    ],
)
def test_transitions_are_exact(held, statistic, target, after):
    assert next_target_weight(held, statistic) == (target, after)


def test_long_to_flat_is_not_an_immediate_reversal():
    recs = records(window_days(), [falling(i) for i in range(WINDOW_DAYS)])
    closing = calculate_decision(recs, DECISION, 1)
    assert closing.statistic is not None and closing.statistic < -ENTRY_THRESHOLD
    assert closing.target_weight == ZERO  # close first
    assert closing.held_sign_after == 0
    re_entry = calculate_decision(recs, DECISION, closing.held_sign_after)
    assert re_entry.target_weight == SHORT  # opposite entry only at the next decision
    assert re_entry.held_sign_after == -1


def test_short_to_flat_is_not_an_immediate_reversal():
    recs = records(window_days(), [rising(i) for i in range(WINDOW_DAYS)])
    closing = calculate_decision(recs, DECISION, -1)
    assert closing.statistic is not None and closing.statistic > ENTRY_THRESHOLD
    assert closing.target_weight == ZERO
    assert closing.held_sign_after == 0
    re_entry = calculate_decision(recs, DECISION, closing.held_sign_after)
    assert re_entry.target_weight == LONG
    assert re_entry.held_sign_after == 1


def test_targets_are_exact_decimals_from_the_fixed_set():
    allowed = {ZERO, LONG, SHORT}
    for held in (-1, 0, 1):
        for statistic in (-3.0, -2.0, -1.0, -0.0, 0.0, 1.0, 2.0, 3.0):
            target, after = next_target_weight(held, statistic)
            assert isinstance(target, Decimal)
            assert target in allowed
            assert after in (-1, 0, 1)
    assert str(next_target_weight(0, 2.5)[0]) == "0.25"
    assert str(next_target_weight(0, -2.5)[0]) == "-0.25"
    assert str(next_target_weight(0, 0.0)[0]) == "0"


def test_invalid_transition_arguments_are_rejected():
    for bad_held in (Decimal("0.25"), 2, -2, True, None, "1"):
        with pytest.raises(MalformedInputError):
            next_target_weight(bad_held, 1.0)
    for bad_statistic in (float("nan"), float("inf"), Decimal("NaN"), True, None, "1"):
        with pytest.raises(MalformedInputError):
            next_target_weight(0, bad_statistic)


# --- held state ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_weight",
    [True, False, Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity"), float("nan"), float("inf"), "0.25", None],
)
def test_nonfinite_or_nonnumeric_holding_rejected(bad_weight):
    with pytest.raises(MalformedInputError):
        held_sign_from_weight(bad_weight)


def test_held_sign_is_derived_from_the_observed_weight():
    assert held_sign_from_weight(Decimal("0.25")) == 1
    assert held_sign_from_weight(Decimal("-0.25")) == -1
    assert held_sign_from_weight(Decimal("0")) == 0
    assert held_sign_from_weight(Decimal("-0.0")) == 0
    assert held_sign_from_weight(0.25) == 1
    assert held_sign_from_weight(-0.01) == -1
    assert held_sign_from_weight(1) == 1


@pytest.mark.parametrize("bad_sign", [Decimal("NaN"), 2, -2, True, "1", None, 0.5])
def test_invalid_held_sign_argument_fails_closed(bad_sign):
    recs = records(window_days(), [rising(i) for i in range(WINDOW_DAYS)])
    decision = calculate_decision(recs, DECISION, bad_sign)
    assert decision.status is SignalStatus.MALFORMED_INPUT
    assert decision.statistic is None
    assert decision.target_weight == ZERO
    assert decision.held_sign_after == 0


def test_unavailable_signal_reason_is_a_flatten_request_not_an_execution_claim():
    """``target_weight`` 0 is a requested target; whether it was executed is unknown here."""
    rising_recs = records(window_days(), [rising(index) for index in range(WINDOW_DAYS)])
    cases = {
        "invalid held_sign": calculate_decision(rising_recs, DECISION, Decimal("0.25")),
        "insufficient history": calculate_decision(rising_recs[:-1], DECISION, 1),
        "stale tail": calculate_decision(
            stale_long_records(WINDOW_DAYS, LAST_DAY - timedelta(days=1)), DECISION, 1
        ),
        "degenerate estimate": calculate_decision(
            records(window_days(), [Decimal(perfect_exponential(index)) for index in range(WINDOW_DAYS)]),
            DECISION,
            1,
        ),
    }
    for label, decision in cases.items():
        assert decision.target_weight == ZERO, label
        assert decision.held_sign_after == 0, label
        assert "flatten requested" in decision.reason, label
        assert "actual execution unknown" in decision.reason, label
        assert "flattened" not in decision.reason, label  # never claimed as done


def test_held_sign_after_is_always_the_sign_of_the_intended_target():
    """``held_sign_after`` tracks ``target_weight`` (an intention), for every status."""
    rising_recs = records(window_days(), [rising(index) for index in range(WINDOW_DAYS)])
    falling_recs = records(window_days(), [falling(index) for index in range(WINDOW_DAYS)])
    scenarios = [
        (rising_recs, 0),
        (rising_recs, 1),
        (rising_recs, -1),
        (falling_recs, 0),
        (falling_recs, 1),
        (flat_records(), 1),
        (records(window_days(), [noisy_flat(index) for index in range(WINDOW_DAYS)]), 1),
        (rising_recs[:-1], 1),
        (stale_long_records(WINDOW_DAYS, LAST_DAY - timedelta(days=1)), 1),
        (rising_recs, 2),
    ]
    for recs, held in scenarios:
        decision = calculate_decision(recs, DECISION, held)
        weight = decision.target_weight
        assert decision.held_sign_after == (0 if weight == ZERO else (1 if weight > 0 else -1))
        assert decision.held_sign_after in (-1, 0, 1)
        # the reported "before" is whatever the caller passed, never inferred from the signal
        assert decision.held_sign_before in (held, 0)


def test_held_sign_contract_is_documented_as_intended_only():
    for func_or_type in (SignalDecision, calculate_decision, next_target_weight):
        doc = (func_or_type.__doc__ or "").lower()
        assert "intended target sign" in doc, func_or_type
    held_doc = (calculate_decision.__doc__ or "").lower()
    assert "ledger" in held_doc and "never" in held_doc
    assert "ledger" in (held_sign_from_weight.__doc__ or "").lower()


# --- benchmark -------------------------------------------------------------------------


def test_benchmark_return_sign_and_target_use_the_same_window():
    assert benchmark_return_sign([Decimal("1"), Decimal("2")]) == 1
    assert benchmark_return_sign([Decimal("2"), Decimal("1")]) == -1
    assert benchmark_return_sign([Decimal("2"), Decimal("2")]) == 0
    assert benchmark_target_weight([Decimal("1"), Decimal("2")]) == LONG
    assert benchmark_target_weight([Decimal("2"), Decimal("1")]) == SHORT
    assert benchmark_target_weight([Decimal("2"), Decimal("2")]) == ZERO
    with pytest.raises(MalformedInputError):
        benchmark_return_sign([Decimal("1"), Decimal("NaN")])
    with pytest.raises(MalformedInputError):
        benchmark_return_sign([Decimal("1")])


def test_benchmark_is_reported_even_when_the_signal_is_unavailable():
    days = window_days(count=WINDOW_DAYS + 1)
    gap = days.pop(10)
    recs = records(days, [rising(i) for i in range(len(days))])
    decision = calculate_decision(recs, DECISION, 0)
    assert decision.status is SignalStatus.MALFORMED_INPUT
    assert gap.isoformat() in decision.reason
    assert decision.window_last_day is None
    assert decision.benchmark_return_sign is None
    assert decision.benchmark_target_weight == ZERO


# --- frozen specification --------------------------------------------------------------


def test_window_thresholds_and_api_surface_are_fixed():
    assert WINDOW_DAYS == 90
    assert ENTRY_THRESHOLD == 2.0
    assert EXIT_THRESHOLD == 0.0
    assert TARGET_WEIGHT == Decimal("0.25")
    assert AVAILABILITY_DELAY == timedelta(minutes=5)
    assert 0 < SLOPE_ZERO_EPSILON < 1e-6
    assert 0 < RESIDUAL_ZERO_EPSILON < 1e-6
    assert set(inspect.signature(calculate_decision).parameters) == {"records", "decision_time", "held_sign"}
    assert set(inspect.signature(select_window).parameters) == {"records", "decision_time"}
    assert set(inspect.signature(fit_log_slope).parameters) == {"closes"}
    with pytest.raises(MalformedInputError):
        fit_log_slope([Decimal("1")] * (WINDOW_DAYS - 1))
