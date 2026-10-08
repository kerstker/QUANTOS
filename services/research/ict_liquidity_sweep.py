"""Pure primitives for a forward-only ICT liquidity-sweep translation.

The practitioner vocabulary is reduced to observable market events: a prior
price level is breached by aggressive flow, price reclaims the level, and the
displayed book opposes the breach with no loss of executable depth.  This
module deliberately performs no IO, wall-clock access, registry mutation,
lifecycle transition, credential access, broker connection, or order action.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Literal, Sequence
from zoneinfo import ZoneInfo


SweepSide = Literal["HIGH", "LOW"]
SweepSignalMode = Literal[
    "session_absorption_reversal",
    "all_day_absorption_reversal",
    "session_sweep_only_reversal",
    "session_absorption_continuation",
    "placebo_session_absorption_reversal",
]
SessionBucket = Literal["PRIMARY", "PLACEBO", "OTHER"]


@dataclass(frozen=True)
class IctLiquiditySweepConfig:
    cadence_ms: int = 30_000
    level_lookback_frames: int = 120
    max_reclaim_frames: int = 4
    holding_ms: int = 15 * 60_000
    execution_latency_ms: int = 250
    breach_spread_multiple: float = 1.0
    aggressive_flow_imbalance_min: float = 0.50
    opposing_depth_imbalance_min: float = 0.25
    minimum_depth_replenishment_ratio: float = 1.0
    session_timezone: str = "America/New_York"
    primary_session_hours: tuple[tuple[int, int], ...] = ((2, 5), (7, 10))
    placebo_session_hours: tuple[tuple[int, int], ...] = ((14, 17), (19, 22))

    def __post_init__(self) -> None:
        integer_fields = (
            "cadence_ms",
            "level_lookback_frames",
            "max_reclaim_frames",
            "holding_ms",
            "execution_latency_ms",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.execution_latency_ms >= self.cadence_ms:
            raise ValueError("execution_latency_ms must be below one frame")
        for name in (
            "breach_spread_multiple",
            "aggressive_flow_imbalance_min",
            "opposing_depth_imbalance_min",
            "minimum_depth_replenishment_ratio",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
        if self.aggressive_flow_imbalance_min > 1.0:
            raise ValueError("aggressive_flow_imbalance_min cannot exceed 1")
        if self.opposing_depth_imbalance_min > 1.0:
            raise ValueError("opposing_depth_imbalance_min cannot exceed 1")
        try:
            ZoneInfo(self.session_timezone)
        except Exception as exc:  # pragma: no cover - platform tzdata failure
            raise ValueError("session_timezone is unavailable") from exc
        _validate_sessions(self.primary_session_hours, name="primary_session_hours")
        _validate_sessions(self.placebo_session_hours, name="placebo_session_hours")
        primary = _expanded_hours(self.primary_session_hours)
        placebo = _expanded_hours(self.placebo_session_hours)
        if primary.intersection(placebo):
            raise ValueError("primary and placebo session hours must not overlap")


@dataclass(frozen=True)
class SweepFrame:
    instrument_id: str
    event_time_ms: int
    decision_time_ms: int
    mid_price: float
    spread_bps: float
    taker_imbalance_5s: float
    depth_imbalance_5: float
    executable_depth_5_usd: float

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not self.instrument_id:
            raise ValueError("instrument_id is required")
        for name in ("event_time_ms", "decision_time_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.decision_time_ms < self.event_time_ms:
            raise ValueError("decision_time_ms cannot precede event_time_ms")
        numeric = (
            self.mid_price,
            self.spread_bps,
            self.taker_imbalance_5s,
            self.depth_imbalance_5,
            self.executable_depth_5_usd,
        )
        if any(isinstance(value, bool) or not math.isfinite(float(value)) for value in numeric):
            raise ValueError("frame numeric fields must be finite numbers")
        if self.mid_price <= 0.0 or self.spread_bps < 0.0:
            raise ValueError("mid price must be positive and spread non-negative")
        if self.executable_depth_5_usd <= 0.0:
            raise ValueError("executable depth must be positive")
        for value in (self.taker_imbalance_5s, self.depth_imbalance_5):
            if not -1.0 <= float(value) <= 1.0:
                raise ValueError("imbalance values must be inside [-1, 1]")


@dataclass(frozen=True)
class LiquiditySweepEvent:
    instrument_id: str
    side: SweepSide
    breach_event_time_ms: int
    confirmation_event_time_ms: int
    decision_time_ms: int
    prior_boundary: float
    breach_price: float
    confirmation_price: float
    penetration_bps: float
    aggressive_flow_confirmed: bool
    displayed_absorption_confirmed: bool
    depth_replenishment_ratio: float
    reversal_direction: int
    session_bucket: SessionBucket

    @property
    def absorption_confirmed(self) -> bool:
        return self.aggressive_flow_confirmed and self.displayed_absorption_confirmed


def _validate_sessions(
    sessions: tuple[tuple[int, int], ...], *, name: str
) -> None:
    if not sessions:
        raise ValueError(f"{name} must not be empty")
    for item in sessions:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in item)
        ):
            raise TypeError(f"{name} entries must be integer (start, stop) tuples")
        start, stop = item
        if not 0 <= start < stop <= 24:
            raise ValueError(f"{name} entries must satisfy 0 <= start < stop <= 24")


def _expanded_hours(sessions: tuple[tuple[int, int], ...]) -> frozenset[int]:
    return frozenset(hour for start, stop in sessions for hour in range(start, stop))


def session_bucket(
    event_time_ms: int,
    config: IctLiquiditySweepConfig = IctLiquiditySweepConfig(),
) -> SessionBucket:
    """Classify a timestamp in a DST-safe practitioner/session control clock."""

    if isinstance(event_time_ms, bool) or not isinstance(event_time_ms, int):
        raise TypeError("event_time_ms must be an integer")
    if event_time_ms < 0:
        raise ValueError("event_time_ms must be non-negative")
    local = datetime.fromtimestamp(
        event_time_ms / 1_000.0, tz=timezone.utc
    ).astimezone(ZoneInfo(config.session_timezone))
    hour = local.hour
    if hour in _expanded_hours(config.primary_session_hours):
        return "PRIMARY"
    if hour in _expanded_hours(config.placebo_session_hours):
        return "PLACEBO"
    return "OTHER"


def _validate_series(frames: Sequence[SweepFrame]) -> tuple[SweepFrame, ...]:
    rows = tuple(frames)
    if not rows:
        return ()
    instrument = rows[0].instrument_id
    if any(row.instrument_id != instrument for row in rows):
        raise ValueError("all frames must belong to one instrument")
    times = tuple(row.event_time_ms for row in rows)
    if any(current <= previous for previous, current in zip(times, times[1:])):
        raise ValueError("frame event times must be strictly increasing")
    decisions = tuple(row.decision_time_ms for row in rows)
    if any(
        current <= previous
        for previous, current in zip(decisions, decisions[1:])
    ):
        raise ValueError("frame decision times must be strictly increasing")
    return rows


def _is_contiguous(
    frames: Sequence[SweepFrame], start: int, stop: int, cadence_ms: int
) -> bool:
    return all(
        frames[index].event_time_ms - frames[index - 1].event_time_ms == cadence_ms
        for index in range(start + 1, stop)
    )


def detect_liquidity_sweeps(
    frames: Sequence[SweepFrame],
    config: IctLiquiditySweepConfig = IctLiquiditySweepConfig(),
) -> tuple[LiquiditySweepEvent, ...]:
    """Detect causal breach-and-reclaim events without consulting labels.

    Missing 30-second frames invalidate only the affected formation/reclaim
    window.  After an event, the same instrument is locked out for the frozen
    15-minute holding horizon so future daily aggregation cannot double count
    overlapping labels.
    """

    rows = _validate_series(frames)
    if not rows:
        return ()
    out: list[LiquiditySweepEvent] = []
    next_allowed_decision_ms = 0
    lookback = config.level_lookback_frames
    for breach_index in range(lookback, len(rows) - 1):
        breach = rows[breach_index]
        if breach.decision_time_ms < next_allowed_decision_ms:
            continue
        formation_start = breach_index - lookback
        if not _is_contiguous(
            rows, formation_start, breach_index + 1, config.cadence_ms
        ):
            continue
        history = rows[formation_start:breach_index]
        prior_high = max(row.mid_price for row in history)
        prior_low = min(row.mid_price for row in history)
        breach_distance = (
            breach.mid_price
            * breach.spread_bps
            / 10_000.0
            * config.breach_spread_multiple
        )
        side: SweepSide | None = None
        boundary = 0.0
        reversal_direction = 0
        if breach.mid_price > prior_high + breach_distance:
            side = "HIGH"
            boundary = prior_high
            reversal_direction = -1
        elif breach.mid_price < prior_low - breach_distance:
            side = "LOW"
            boundary = prior_low
            reversal_direction = 1
        if side is None:
            continue
        confirmation_limit = min(
            len(rows) - 1,
            breach_index + config.max_reclaim_frames,
        )
        for confirmation_index in range(breach_index + 1, confirmation_limit + 1):
            if not _is_contiguous(
                rows, breach_index, confirmation_index + 1, config.cadence_ms
            ):
                break
            confirmation = rows[confirmation_index]
            reclaimed = (
                confirmation.mid_price <= boundary
                if side == "HIGH"
                else confirmation.mid_price >= boundary
            )
            if not reclaimed:
                continue
            flow_confirmed = (
                breach.taker_imbalance_5s
                >= config.aggressive_flow_imbalance_min
                if side == "HIGH"
                else breach.taker_imbalance_5s
                <= -config.aggressive_flow_imbalance_min
            )
            depth_opposes = (
                confirmation.depth_imbalance_5
                <= -config.opposing_depth_imbalance_min
                if side == "HIGH"
                else confirmation.depth_imbalance_5
                >= config.opposing_depth_imbalance_min
            )
            depth_ratio = (
                confirmation.executable_depth_5_usd
                / breach.executable_depth_5_usd
            )
            displayed_absorption = bool(
                depth_opposes
                and depth_ratio >= config.minimum_depth_replenishment_ratio
            )
            penetration_bps = (
                abs(breach.mid_price - boundary) / boundary * 10_000.0
            )
            event = LiquiditySweepEvent(
                instrument_id=breach.instrument_id,
                side=side,
                breach_event_time_ms=breach.event_time_ms,
                confirmation_event_time_ms=confirmation.event_time_ms,
                decision_time_ms=(
                    confirmation.decision_time_ms + config.execution_latency_ms
                ),
                prior_boundary=boundary,
                breach_price=breach.mid_price,
                confirmation_price=confirmation.mid_price,
                penetration_bps=penetration_bps,
                aggressive_flow_confirmed=flow_confirmed,
                displayed_absorption_confirmed=displayed_absorption,
                depth_replenishment_ratio=depth_ratio,
                reversal_direction=reversal_direction,
                session_bucket=session_bucket(
                    confirmation.event_time_ms, config
                ),
            )
            out.append(event)
            next_allowed_decision_ms = event.decision_time_ms + config.holding_ms
            break
    return tuple(out)


def signal_for_event(
    event: LiquiditySweepEvent,
    *,
    mode: SweepSignalMode,
) -> int:
    """Return the frozen {-1, 0, +1} signal for one candidate control."""

    if mode == "session_absorption_reversal":
        active = event.session_bucket == "PRIMARY" and event.absorption_confirmed
        return event.reversal_direction if active else 0
    if mode == "all_day_absorption_reversal":
        return event.reversal_direction if event.absorption_confirmed else 0
    if mode == "session_sweep_only_reversal":
        return event.reversal_direction if event.session_bucket == "PRIMARY" else 0
    if mode == "session_absorption_continuation":
        active = event.session_bucket == "PRIMARY" and event.absorption_confirmed
        return -event.reversal_direction if active else 0
    if mode == "placebo_session_absorption_reversal":
        active = event.session_bucket == "PLACEBO" and event.absorption_confirmed
        return event.reversal_direction if active else 0
    raise ValueError("unsupported ICT liquidity-sweep signal mode")


__all__ = [
    "IctLiquiditySweepConfig",
    "LiquiditySweepEvent",
    "SessionBucket",
    "SweepFrame",
    "SweepSide",
    "SweepSignalMode",
    "detect_liquidity_sweeps",
    "session_bucket",
    "signal_for_event",
]
