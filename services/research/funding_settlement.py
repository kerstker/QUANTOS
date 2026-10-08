"""Causal event-aligned funding-settlement research primitives.

The signal at settlement ``t`` is the rate that has just settled and is known
at ``t``.  A decision is priced from the completed mark-price hour after that
settlement, then held across the next scheduled eight-hour settlement.  The
label includes exactly the next settled funding cash flow; it never prorates a
future payment or uses that future rate as a signal.

This module is deterministic and IO-free.  It has no broker, credential,
network, promotion, or order authority.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from quantos.contracts.panel import PanelPeriod


HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
SETTLEMENT_INTERVAL_MS = 8 * HOUR_MS
WEEKLY_SETTLEMENT_COUNT = 21
WEEK_MS = 7 * DAY_MS
MONDAY_UTC_OFFSET_MS = 4 * DAY_MS


class FundingSettlementError(ValueError):
    """Funding-settlement evidence is malformed, noncausal, or too thin."""


@dataclass(frozen=True)
class SettlementObservation:
    instrument_id: str
    settlement_time_ms: int
    decision_time_ms: int
    settled_rate: float
    decision_mark_price: float

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not self.instrument_id.strip():
            raise FundingSettlementError("instrument_id must be non-empty")
        for name in ("settlement_time_ms", "decision_time_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise FundingSettlementError(f"{name} must be a non-negative integer")
        if self.decision_time_ms != self.settlement_time_ms + HOUR_MS:
            raise FundingSettlementError(
                "decision mark must be the completed hour after settlement"
            )
        if (
            isinstance(self.settled_rate, bool)
            or not isinstance(self.settled_rate, (int, float))
            or not math.isfinite(float(self.settled_rate))
            or abs(float(self.settled_rate)) > 0.10
        ):
            raise FundingSettlementError("settled_rate is outside its domain")
        if (
            isinstance(self.decision_mark_price, bool)
            or not isinstance(self.decision_mark_price, (int, float))
            or not math.isfinite(float(self.decision_mark_price))
            or float(self.decision_mark_price) <= 0.0
        ):
            raise FundingSettlementError("decision_mark_price must be positive and finite")


def _validated_history(
    history: Mapping[str, Sequence[SettlementObservation]],
) -> dict[str, dict[int, SettlementObservation]]:
    if not isinstance(history, Mapping):
        raise FundingSettlementError("history must be a mapping")
    normalized: dict[str, dict[int, SettlementObservation]] = {}
    for raw_instrument, raw_rows in sorted(history.items()):
        instrument = str(raw_instrument)
        rows = tuple(raw_rows)
        if not rows:
            continue
        by_time: dict[int, SettlementObservation] = {}
        previous = -1
        for row in rows:
            if not isinstance(row, SettlementObservation):
                raise FundingSettlementError("history rows must be SettlementObservation")
            if row.instrument_id != instrument:
                raise FundingSettlementError("history key and row instrument differ")
            if row.settlement_time_ms <= previous:
                raise FundingSettlementError(
                    "settlement observations must be strictly increasing"
                )
            if row.settlement_time_ms % HOUR_MS != 0:
                raise FundingSettlementError("settlement time is not on an hourly boundary")
            previous = row.settlement_time_ms
            by_time[row.settlement_time_ms] = row
        normalized[instrument] = by_time
    return normalized


def _rank_legs(
    rows: Sequence[tuple[str, float]],
    *,
    quantile: float,
) -> tuple[set[str], set[str], list[str]]:
    ranked = [
        instrument
        for instrument, _signal in sorted(rows, key=lambda item: (item[1], item[0]))
    ]
    count = max(1, int(round(len(ranked) * quantile)))
    if count * 2 > len(ranked):
        raise FundingSettlementError("long and short entry tails overlap")
    return set(ranked[-count:]), set(ranked[:count]), ranked


def _buffered_legs(
    rows: Sequence[tuple[str, float]],
    *,
    previous_long: set[str],
    previous_short: set[str],
    entry_quantile: float,
    exit_quantile: float,
) -> tuple[set[str], set[str]]:
    entry_long, entry_short, ranked = _rank_legs(rows, quantile=entry_quantile)
    exit_count = max(1, int(round(len(ranked) * exit_quantile)))
    if exit_count * 2 > len(ranked):
        raise FundingSettlementError("long and short exit bands overlap")
    long_exit_band = set(ranked[-exit_count:])
    short_exit_band = set(ranked[:exit_count])
    target_count = len(entry_long)

    long_legs = previous_long & long_exit_band
    for instrument in reversed(ranked):
        if len(long_legs) >= target_count:
            break
        if instrument in entry_long and instrument not in long_legs:
            long_legs.add(instrument)

    short_legs = previous_short & short_exit_band
    for instrument in ranked:
        if len(short_legs) >= target_count:
            break
        if instrument in entry_short and instrument not in short_legs:
            short_legs.add(instrument)

    if (
        len(long_legs) != target_count
        or len(short_legs) != target_count
        or long_legs & short_legs
    ):
        raise FundingSettlementError("buffered rank could not form a neutral book")
    return long_legs, short_legs


def build_settlement_periods(
    history: Mapping[str, Sequence[SettlementObservation]],
    *,
    mode: Literal["rank_control", "buffered_rank"],
    min_cross_section: int = 10,
    entry_quantile: float = 0.20,
    exit_quantile: float = 0.40,
) -> tuple[PanelPeriod, ...]:
    """Build causal, non-overlapping eight-hour funding-settlement periods.

    ``rank_control`` selects the current entry tails each settlement.
    ``buffered_rank`` retains an existing leg while it remains inside the fixed
    40% exit band and fills vacancies only from the fixed 20% entry tail.

    Returned periods retain the full admitted cross-section.  Chosen legs are
    encoded as +1/-1 and inactive names as zero.  A panel evaluator using the
    same 20% entry quantile therefore reconstructs the exact equal-weight
    long/short target, preserves the ten-name breadth gate, and measures the
    realized turnover without inventing a custom backtester.
    """
    if mode not in {"rank_control", "buffered_rank"}:
        raise FundingSettlementError("unknown settlement mode")
    if (
        isinstance(min_cross_section, bool)
        or not isinstance(min_cross_section, int)
        or min_cross_section < 2
    ):
        raise FundingSettlementError("min_cross_section must be at least two")
    if not 0.0 < entry_quantile < exit_quantile < 0.5:
        raise FundingSettlementError(
            "quantiles must satisfy 0 < entry < exit < 0.5"
        )

    normalized = _validated_history(history)
    event_times = sorted(
        {event_time for rows in normalized.values() for event_time in rows}
    )
    periods: list[PanelPeriod] = []
    previous_long: set[str] = set()
    previous_short: set[str] = set()
    previous_event_time: int | None = None

    for event_time in event_times:
        next_time = event_time + SETTLEMENT_INTERVAL_MS
        available = [
            instrument
            for instrument, rows in normalized.items()
            if event_time in rows and next_time in rows
        ]
        if len(available) < min_cross_section:
            continue
        if (
            previous_event_time is not None
            and event_time - previous_event_time != SETTLEMENT_INTERVAL_MS
        ):
            # Missing labels break the stateful holding chain.  Reset rather
            # than silently carrying a position across an unobserved interval.
            previous_long.clear()
            previous_short.clear()

        ranked_rows = [
            (
                instrument,
                -float(normalized[instrument][event_time].settled_rate),
            )
            for instrument in sorted(available)
        ]
        if mode == "rank_control":
            long_legs, short_legs, _ranked = _rank_legs(
                ranked_rows,
                quantile=entry_quantile,
            )
        else:
            long_legs, short_legs = _buffered_legs(
                ranked_rows,
                previous_long=previous_long,
                previous_short=previous_short,
                entry_quantile=entry_quantile,
                exit_quantile=exit_quantile,
            )

        selected = sorted(available)
        signals: list[float] = []
        forward_returns: list[float] = []
        for instrument in selected:
            current = normalized[instrument][event_time]
            future = normalized[instrument][next_time]
            signals.append(
                1.0
                if instrument in long_legs
                else (-1.0 if instrument in short_legs else 0.0)
            )
            forward_returns.append(
                float(future.decision_mark_price)
                / float(current.decision_mark_price)
                - 1.0
                - float(future.settled_rate)
            )
        periods.append(
            PanelPeriod(
                instruments=tuple(selected),
                signals=tuple(signals),
                forward_returns=tuple(forward_returns),
                event_time=event_time + HOUR_MS,
            )
        )
        previous_long = set(long_legs)
        previous_short = set(short_legs)
        previous_event_time = event_time

    if not periods:
        raise FundingSettlementError("no causal settlement periods clear breadth")
    return tuple(periods)


def build_weekly_carry_periods(
    history: Mapping[str, Sequence[SettlementObservation]],
    *,
    min_cross_section: int = 10,
) -> tuple[PanelPeriod, ...]:
    """Build the single fixed weekly funding-carry research schedule.

    Decisions occur only after the Monday 00:00 UTC settlement has a completed
    01:00 mark bar.  The signal is the negative mean of the 21 already-settled
    rates ending at that Monday.  The non-overlapping label holds to the next
    Monday decision mark and includes exactly the next 21 settled funding cash
    flows.  There is no weekday, horizon, window, quantile, or direction grid.
    """
    if (
        isinstance(min_cross_section, bool)
        or not isinstance(min_cross_section, int)
        or min_cross_section < 2
    ):
        raise FundingSettlementError("min_cross_section must be at least two")

    normalized = _validated_history(history)
    event_times = sorted(
        event_time
        for event_time in {
            item for rows in normalized.values() for item in rows
        }
        if (event_time - MONDAY_UTC_OFFSET_MS) % WEEK_MS == 0
    )
    periods: list[PanelPeriod] = []
    for event_time in event_times:
        formation_times = tuple(
            event_time - offset * SETTLEMENT_INTERVAL_MS
            for offset in range(WEEKLY_SETTLEMENT_COUNT - 1, -1, -1)
        )
        cashflow_times = tuple(
            event_time + offset * SETTLEMENT_INTERVAL_MS
            for offset in range(1, WEEKLY_SETTLEMENT_COUNT + 1)
        )
        future_time = cashflow_times[-1]
        required_times = formation_times + cashflow_times
        available = [
            instrument
            for instrument, rows in normalized.items()
            if all(required_time in rows for required_time in required_times)
        ]
        if len(available) < min_cross_section:
            continue

        selected = tuple(sorted(available))
        signals: list[float] = []
        forward_returns: list[float] = []
        for instrument in selected:
            rows = normalized[instrument]
            current = rows[event_time]
            future = rows[future_time]
            trailing_rate = statistics.fmean(
                float(rows[item].settled_rate) for item in formation_times
            )
            realized_funding = sum(
                float(rows[item].settled_rate) for item in cashflow_times
            )
            signals.append(-trailing_rate)
            forward_returns.append(
                float(future.decision_mark_price)
                / float(current.decision_mark_price)
                - 1.0
                - realized_funding
            )
        periods.append(
            PanelPeriod(
                instruments=selected,
                signals=tuple(signals),
                forward_returns=tuple(forward_returns),
                event_time=event_time + HOUR_MS,
            )
        )

    if not periods:
        raise FundingSettlementError("no causal weekly carry periods clear breadth")
    return tuple(periods)


__all__ = [
    "DAY_MS",
    "FundingSettlementError",
    "HOUR_MS",
    "MONDAY_UTC_OFFSET_MS",
    "SETTLEMENT_INTERVAL_MS",
    "SettlementObservation",
    "WEEKLY_SETTLEMENT_COUNT",
    "WEEK_MS",
    "build_settlement_periods",
    "build_weekly_carry_periods",
]
