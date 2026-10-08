"""RF2 pure research signal: fixed 90-day log-price OLS slope t-statistic.

Registered hypothesis ``LOG_SLOPE_90_HYSTERESIS_V1`` for
docs/engineering/plans/2026-09-20-rf2-bounded-strategy-experiment.md.  Pure,
offline, deterministic: stdlib only, no network, no data read, no ledger, no
runner, no broker/risk-gate import and no module-level mutable state.  The
window length, thresholds and availability delay are module constants and are
deliberately not parameters.

Causality.  A decision at ``T`` sees only UTC days whose close is knowable at
``T``.  Day ``d`` closes at ``d + 1`` 00:00Z and becomes available one
``AVAILABILITY_DELAY`` (5 minutes) later, so the 00:05Z decision on day ``D``
uses ``d <= D - 1`` and never the decision day's own close.  Records that are
not yet knowable at ``T`` are skipped entirely - their prices included - so
appending or changing future bars cannot change a historical decision.  Only
the 90 *selected* closes are validated; a malformed price outside the required
window is ignored.  (Day fields are type-checked for every record, because the
causal order of the window cannot be established without them.)

Anchoring.  The required window is anchored to ``decision_time``, never to the
newest row supplied: it always ends at the newest day whose close is knowable at
``decision_time`` (see :func:`expected_window_last_day`).  A stale tail - the
expected newest days simply absent while 90 or more older rows are present - is
a data gap, so the window is not slid one or more days back onto older closes.

Fail-closed.  Insufficient history, malformed records (bool / non-integral day,
duplicate or absent day inside the required window, non-finite, non-positive or
unrepresentable close) and degenerate estimates all report target weight 0 with
an explicit status and reason.  Missing data is never treated as a valid short.

Intention, not execution.  Every decision is a requested target weight.  The
module neither submits nor observes orders, so it cannot know whether a
requested flatten or entry was actually filled; ``held_sign_after`` is the
*intended target sign* and is never an executed sign.  Callers must recompute
``held_sign`` from their own actual ledger on every decision and must never feed
``held_sign_after`` back as the next ``held_sign``.

Fixed numeric rules (no adjustable epsilon, no fabricated infinity).  A slope
whose magnitude is <= ``SLOPE_ZERO_EPSILON`` is reported as statistic exactly
``0.0`` (flat window, instead of the indeterminate ``0/0``).  A non-flat window
whose residual standard error is <= ``RESIDUAL_ZERO_EPSILON`` is
``DEGENERATE_ESTIMATE`` with statistic ``None`` - a near-perfectly-geometric
series must not manufacture an enormous or infinite t-statistic.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from math import isfinite, log, sqrt
from typing import Iterable, Sequence

WINDOW_DAYS = 90
ENTRY_THRESHOLD = 2.0
EXIT_THRESHOLD = 0.0
TARGET_WEIGHT = Decimal("0.25")
AVAILABILITY_DELAY = timedelta(minutes=5)
SLOPE_ZERO_EPSILON = 1e-12
RESIDUAL_ZERO_EPSILON = 1e-12

_UTC = timezone.utc
_ZERO = Decimal(0)
_MIDNIGHT = time(0, 0)


class MalformedInputError(ValueError):
    """Malformed argument or record field passed to this module's API."""


class SignalStatus(str, Enum):
    """Outcome of a window selection, a fit or a decision."""

    ACTIVE = "ACTIVE"
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    MALFORMED_INPUT = "MALFORMED_INPUT"
    DEGENERATE_ESTIMATE = "DEGENERATE_ESTIMATE"


@dataclass(frozen=True, slots=True)
class DailyClose:
    """One completed UTC day and its close. Validated by the selecting functions."""

    day: date
    close: Decimal


@dataclass(frozen=True, slots=True)
class WindowSelection:
    """The causal 90-day window knowable at ``decision_time`` (ACTIVE) or why not."""

    decision_time: datetime
    status: SignalStatus
    reason: str
    available_days: int
    days: tuple[date, ...] = ()
    closes: tuple[Decimal, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is SignalStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class LogSlopeFit:
    """Centred-index OLS of ln(close) with intercept; ``intercept`` is centred too."""

    slope: float
    intercept: float
    residual_standard_error: float
    statistic: float | None
    status: SignalStatus
    reason: str


@dataclass(frozen=True, slots=True)
class SignalDecision:
    """A single pre-registered decision. ``target_weight`` is 0, +0.25 or -0.25.

    ``held_sign_before`` is the *actual* sign of the position the caller reported
    from its own ledger.  ``held_sign_after`` is the *intended target sign* implied
    by ``target_weight`` - never an actual executed sign: this module has no order,
    fill or ledger information, so a requested flatten or entry may or may not have
    been executed.  Recompute ``held_sign`` from the actual ledger on every
    decision and never feed ``held_sign_after`` back as the next ``held_sign``.
    """

    decision_time: datetime
    status: SignalStatus
    reason: str
    statistic: float | None
    target_weight: Decimal
    held_sign_before: int
    held_sign_after: int
    window_size: int
    available_days: int
    window_last_day: date | None
    benchmark_return_sign: int | None
    benchmark_target_weight: Decimal


def _number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise MalformedInputError(f"{label} must be a number, not bool")
    if not isinstance(value, (int, float, Decimal)):
        raise MalformedInputError(f"{label} must be a number, got {type(value).__name__}")
    try:
        number = float(value)
    except (OverflowError, InvalidOperation, ValueError) as exc:
        raise MalformedInputError(f"{label} is not a finite number: {exc}") from exc
    if not isfinite(number):
        raise MalformedInputError(f"{label} must be finite, got {value!r}")
    return number


def _price_number(value: object, label: str) -> float:
    number = _number(value, label)
    if number <= 0.0:  # also catches positive values that underflow to zero as a float
        raise MalformedInputError(f"{label} must be a positive price, got {value!r}")
    return number


def _price_decimal(value: object, label: str) -> Decimal:
    number = _price_number(value, label)
    return value if isinstance(value, Decimal) else Decimal(repr(number))


def _as_utc_decision_time(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, datetime):
        raise MalformedInputError(f"decision_time must be a UTC datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise MalformedInputError("decision_time must be timezone-aware UTC")
    return value


def _as_utc_day(value: object) -> date:
    if isinstance(value, bool):
        raise MalformedInputError("day must be a date, not bool")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise MalformedInputError("day datetime must be timezone-aware UTC")
        if value.time() != _MIDNIGHT:
            raise MalformedInputError("day must be a whole UTC day, without a time of day")
        return value.date()
    if isinstance(value, date):
        return value
    raise MalformedInputError(f"day must be a date, got {type(value).__name__}")


def available_at(day: date) -> datetime:
    """Instant at which ``day``'s close becomes knowable: day end + AVAILABILITY_DELAY."""
    return datetime.combine(day + timedelta(days=1), _MIDNIGHT, _UTC) + AVAILABILITY_DELAY


def expected_window_last_day(moment: datetime) -> date | None:
    """Newest UTC day whose close is knowable at ``moment``: ``(moment - 5min).date() - 1day``.

    The required window is anchored here, so the newest expected day does not move
    with the newest row supplied.  ``None`` only when ``moment`` is so early that no
    completed day can be knowable.
    """
    moment = _as_utc_decision_time(moment)
    try:
        return (moment - AVAILABILITY_DELAY).date() - timedelta(days=1)
    except (OverflowError, ValueError):  # before the earliest representable day
        return None


def _sign_target(sign: int | None) -> Decimal:
    if sign is None or sign == 0:
        return _ZERO
    return TARGET_WEIGHT if sign > 0 else -TARGET_WEIGHT


def _rejected(moment: datetime, reason: str, available_days: int) -> WindowSelection:
    return WindowSelection(
        decision_time=moment,
        status=SignalStatus.MALFORMED_INPUT,
        reason=reason,
        available_days=available_days,
    )


def select_window(records: Iterable[DailyClose], decision_time: datetime) -> WindowSelection:
    """Select and validate the causal 90 completed-day window knowable at ``decision_time``.

    Records not yet available at the decision instant are ignored, so future
    bars never enter the window and their prices are never validated.  The window
    is anchored to ``decision_time`` (:func:`expected_window_last_day`), not to the
    newest row supplied: a stale tail is reported as a missing required day rather
    than silently sliding the window onto older closes.
    """
    moment = _as_utc_decision_time(decision_time)
    per_day: dict[date, DailyClose] = {}
    for index, record in enumerate(records):
        if not isinstance(record, DailyClose):
            return _rejected(moment, f"record {index} is not a DailyClose: {type(record).__name__}", len(per_day))
        try:
            day = _as_utc_day(record.day)
        except MalformedInputError as exc:
            return _rejected(moment, f"record {index}: {exc}", len(per_day))
        if available_at(day) > moment:
            continue
        if day in per_day:
            return _rejected(moment, f"duplicate day {day.isoformat()} available at {moment.isoformat()}", len(per_day))
        per_day[day] = record

    days = sorted(per_day)
    if len(days) < WINDOW_DAYS:
        return WindowSelection(
            decision_time=moment,
            status=SignalStatus.INSUFFICIENT_HISTORY,
            reason=(
                f"insufficient history: need {WINDOW_DAYS} completed UTC days available at "
                f"{moment.isoformat()}, found {len(days)}"
            ),
            available_days=len(days),
        )

    last_required = expected_window_last_day(moment)
    if last_required is None:
        return WindowSelection(
            decision_time=moment,
            status=SignalStatus.INSUFFICIENT_HISTORY,
            reason=f"insufficient history: no completed UTC day is knowable at {moment.isoformat()}",
            available_days=len(days),
        )
    window = tuple(last_required - timedelta(days=offset) for offset in range(WINDOW_DAYS - 1, -1, -1))
    for day in window:
        if day not in per_day:
            return _rejected(
                moment,
                f"missing day {day.isoformat()} inside the required {WINDOW_DAYS}-day window "
                f"{window[0].isoformat()}..{window[-1].isoformat()} anchored to {moment.isoformat()} "
                f"({len(days)} earlier completed UTC days available)",
                len(days),
            )

    closes: list[Decimal] = []
    for day in window:
        try:
            closes.append(_price_decimal(per_day[day].close, f"close for {day.isoformat()}"))
        except MalformedInputError as exc:
            return _rejected(moment, str(exc), len(days))

    return WindowSelection(
        decision_time=moment,
        status=SignalStatus.ACTIVE,
        reason=(
            f"required window {window[0].isoformat()}..{window[-1].isoformat()} validated: {WINDOW_DAYS} "
            f"consecutive completed UTC days, day-end + {AVAILABILITY_DELAY} availability, decision-day close excluded"
        ),
        available_days=len(days),
        days=tuple(window),
        closes=tuple(closes),
    )


def fit_log_slope(closes: Sequence[Decimal]) -> LogSlopeFit:
    """OLS of ln(close) on the centred index 0..89 with intercept; slope / SE(slope).

    The statistic is only a signal: it is not evidence of independent return
    significance and confers no admission or execution authority.
    """
    if len(closes) != WINDOW_DAYS:
        raise MalformedInputError(f"log-slope fit requires exactly {WINDOW_DAYS} closes, got {len(closes)}")
    logs = [log(_price_number(close, f"close[{index}]")) for index, close in enumerate(closes)]
    offsets = [index - (WINDOW_DAYS - 1) / 2.0 for index in range(WINDOW_DAYS)]
    mean_y = sum(logs) / WINDOW_DAYS
    sxx = sum(offset * offset for offset in offsets)
    slope = sum(offset * (value - mean_y) for offset, value in zip(offsets, logs)) / sxx
    intercept = mean_y
    rss = sum((value - (intercept + slope * offset)) ** 2 for offset, value in zip(offsets, logs))
    rse = sqrt(rss / (WINDOW_DAYS - 2))

    if abs(slope) <= SLOPE_ZERO_EPSILON:
        return LogSlopeFit(
            slope=slope,
            intercept=intercept,
            residual_standard_error=rse,
            statistic=0.0,
            status=SignalStatus.ACTIVE,
            reason=(
                f"flat window: |slope| {abs(slope):.3g} <= SLOPE_ZERO_EPSILON {SLOPE_ZERO_EPSILON:g}; "
                "statistic fixed at 0 rather than the indeterminate 0/0"
            ),
        )
    if not isfinite(rse) or rse <= RESIDUAL_ZERO_EPSILON:
        return LogSlopeFit(
            slope=slope,
            intercept=intercept,
            residual_standard_error=rse,
            statistic=None,
            status=SignalStatus.DEGENERATE_ESTIMATE,
            reason=(
                f"degenerate estimate: non-flat window (slope {slope:.6g}) with residual standard error "
                f"{rse:.3g} <= RESIDUAL_ZERO_EPSILON {RESIDUAL_ZERO_EPSILON:g}; statistic is undefined, never infinity"
            ),
        )

    statistic = slope / (rse / sqrt(sxx))
    if not isfinite(statistic):
        return LogSlopeFit(
            slope=slope,
            intercept=intercept,
            residual_standard_error=rse,
            statistic=None,
            status=SignalStatus.DEGENERATE_ESTIMATE,
            reason=f"degenerate estimate: statistic {statistic!r} is not finite; never infinity",
        )
    return LogSlopeFit(
        slope=slope,
        intercept=intercept,
        residual_standard_error=rse,
        statistic=statistic,
        status=SignalStatus.ACTIVE,
        reason=f"OLS slope t-statistic over {WINDOW_DAYS} completed UTC days (centred index, with intercept)",
    )


def next_target_weight(held_sign: int, statistic: float) -> tuple[Decimal, int]:
    """Exact LOG_SLOPE_90_HYSTERESIS_V1 transition: ``(target_weight, held_sign_after)``.

    flat  (0): statistic >= +2 -> +0.25 ; <= -2 -> -0.25 ; otherwise stay flat.
    long (+1): statistic <= 0  -> 0 (close) ; otherwise stay +0.25.
    short(-1): statistic >= 0  -> 0 (close) ; otherwise stay -0.25.

    A single decision never reverses: closing always targets 0, so re-entering
    in the opposite direction can only happen at a later decision.  ``held_sign``
    must be the sign actually held per the caller's ledger, and the returned
    ``held_sign_after`` is the *intended target sign* - never an executed sign.
    """
    if type(held_sign) is not int or held_sign not in (-1, 0, 1):
        raise MalformedInputError(f"held_sign must be -1, 0 or 1, got {held_sign!r}")
    if isinstance(statistic, bool) or not isinstance(statistic, (int, float)) or not isfinite(float(statistic)):
        raise MalformedInputError(f"statistic must be a finite number, got {statistic!r}")
    value = float(statistic)
    if held_sign == 0:
        if value >= ENTRY_THRESHOLD:
            return TARGET_WEIGHT, 1
        if value <= -ENTRY_THRESHOLD:
            return -TARGET_WEIGHT, -1
        return _ZERO, 0
    if held_sign > 0:
        return (_ZERO, 0) if value <= EXIT_THRESHOLD else (TARGET_WEIGHT, 1)
    return (_ZERO, 0) if value >= -EXIT_THRESHOLD else (-TARGET_WEIGHT, -1)


def held_sign_from_weight(weight: object) -> int:
    """Derive the actual held sign (-1 / 0 / +1) from an observed position weight.

    Intended for the caller's own ledger: pass the position that actually exists,
    recomputed at every decision.  Never pass a previous ``held_sign_after``, which
    is only the sign that was requested.
    """
    number = _number(weight, "weight")
    return 1 if number > 0.0 else -1 if number < 0.0 else 0


def benchmark_return_sign(closes: Sequence[Decimal]) -> int:
    """Sign of the first-to-last return inside the same validated 90-day window."""
    if len(closes) < 2:
        raise MalformedInputError("benchmark requires at least two closes")
    values = [_price_decimal(close, f"close[{index}]") for index, close in enumerate(closes)]
    if values[-1] > values[0]:
        return 1
    return -1 if values[-1] < values[0] else 0


def benchmark_target_weight(closes: Sequence[Decimal]) -> Decimal:
    """Fixed no-lag same-window benchmark target: +0.25 / 0 / -0.25."""
    return _sign_target(benchmark_return_sign(closes))


def _decision(
    moment: datetime,
    status: SignalStatus,
    reason: str,
    statistic: float | None,
    target: Decimal,
    held_before: int,
    held_after: int,
    selection: WindowSelection,
    window_last_day: date | None,
    benchmark_sign: int | None,
) -> SignalDecision:
    return SignalDecision(
        decision_time=moment,
        status=status,
        reason=reason,
        statistic=statistic,
        target_weight=target,
        held_sign_before=held_before,
        held_sign_after=held_after,
        window_size=len(selection.days),
        available_days=selection.available_days,
        window_last_day=window_last_day,
        benchmark_return_sign=benchmark_sign,
        benchmark_target_weight=_sign_target(benchmark_sign),
    )


def calculate_decision(records: Iterable[DailyClose], decision_time: datetime, held_sign: int = 0) -> SignalDecision:
    """Decide the target weight at ``decision_time`` from the actual held sign.

    ``held_sign`` is the state: the caller passes the sign of the position it
    actually holds (see :func:`held_sign_from_weight`) and receives
    ``held_sign_after`` back, so this module keeps no hidden state.  ``held_sign``
    must come from the caller's own ledger on *every* decision; ``held_sign_after``
    is the intended target sign for ``target_weight``, never an executed sign, and
    must never be fed back as the next ``held_sign``.  Every returned weight is a
    request: whether an execution actually happened is unknown to this module.
    """
    moment = _as_utc_decision_time(decision_time)
    selection = select_window(records, moment)
    window_last_day = selection.days[-1] if selection.days else None
    benchmark_sign = benchmark_return_sign(selection.closes) if len(selection.closes) == WINDOW_DAYS else None

    if type(held_sign) is not int or held_sign not in (-1, 0, 1):
        return _decision(
            moment,
            SignalStatus.MALFORMED_INPUT,
            f"held_sign must be -1, 0 or 1, got {held_sign!r}; signal unavailable -> target 0 "
            "(flatten requested; actual execution unknown)",
            None,
            _ZERO,
            0,
            0,
            selection,
            window_last_day,
            benchmark_sign,
        )
    if selection.status is not SignalStatus.ACTIVE:
        return _decision(
            moment,
            selection.status,
            f"{selection.reason}; signal unavailable -> target 0 (flatten requested; actual execution unknown)",
            None,
            _ZERO,
            held_sign,
            0,
            selection,
            window_last_day,
            benchmark_sign,
        )

    fit = fit_log_slope(selection.closes)
    if fit.statistic is None:
        return _decision(
            moment,
            fit.status,
            f"{fit.reason}; signal unavailable -> target 0 (flatten requested; actual execution unknown)",
            None,
            _ZERO,
            held_sign,
            0,
            selection,
            window_last_day,
            benchmark_sign,
        )

    target, held_after = next_target_weight(held_sign, fit.statistic)
    return _decision(
        moment,
        SignalStatus.ACTIVE,
        fit.reason,
        fit.statistic,
        target,
        held_sign,
        held_after,
        selection,
        window_last_day,
        benchmark_sign,
    )
