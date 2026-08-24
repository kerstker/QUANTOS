"""PIT primitives for the independent Binance RPI-depth Alpha family.

The public RPI channel is a genuinely separate observation surface from the
five-level book used by the main realtime factory: Binance returns a complete,
RPI-inclusive 1000-level book once per hour.  This module binds that surface to
three fixed, falsifiable one-hour candidates and keeps the maturity boundary
machine-readable:

* less than three common days: collect only;
* three to thirty common days: diagnostic exploration only;
* at least thirty common days: statistically eligible, never automatically
  promoted.

It is a deterministic research core: no filesystem, network, credentials,
broker, lifecycle transition, or order-submission seam.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass, replace
import math
from typing import Mapping, Sequence

from .realtime_alpha_discovery import DAY_MS, RealtimeFrame, RealtimeHypothesis


HOUR_MS = 3_600_000
RPI_FEATURE_ID = "rt_rpi_notional_imbalance_1000"
RPI_DATASET_ID = "rpi_depth_1000"
RPI_EXPLORATION_MIN_SPAN_MS = 3 * DAY_MS
RPI_VALIDATION_MIN_SPAN_MS = 30 * DAY_MS
RPI_MAX_AGE_MS = 3 * HOUR_MS
# The authority is a fixed 14-name forward universe.  Losing one name changes
# the cross-sectional experiment and therefore blocks this v1 family instead
# of silently shrinking the mandate to the generic evaluator's breadth floor.
RPI_MIN_INSTRUMENTS = 14


@dataclass(frozen=True)
class RpiDepthObservation:
    instrument_id: str
    event_time_ms: int
    knowledge_time_ms: int
    notional_imbalance: float

    def __post_init__(self) -> None:
        if not isinstance(self.instrument_id, str) or not self.instrument_id:
            raise ValueError("RPI instrument is required")
        if (
            type(self.event_time_ms) is not int
            or type(self.knowledge_time_ms) is not int
            or self.event_time_ms < 0
            or self.knowledge_time_ms < self.event_time_ms
        ):
            raise ValueError("RPI observation violates PIT time ordering")
        if (
            isinstance(self.notional_imbalance, bool)
            or not isinstance(self.notional_imbalance, (int, float))
            or not math.isfinite(float(self.notional_imbalance))
            or not -1.0 <= float(self.notional_imbalance) <= 1.0
        ):
            raise ValueError("RPI notional imbalance is outside [-1, 1]")


@dataclass(frozen=True)
class RpiDataset:
    observations: tuple[RpiDepthObservation, ...]
    authority_hash: str
    chain_valid: bool

    def __post_init__(self) -> None:
        if not isinstance(self.observations, tuple) or any(
            not isinstance(item, RpiDepthObservation) for item in self.observations
        ):
            raise TypeError("RPI observations must be a tuple")
        if (
            not isinstance(self.authority_hash, str)
            or len(self.authority_hash) != 64
            or any(character not in "0123456789abcdef" for character in self.authority_hash)
        ):
            raise ValueError("RPI authority_hash must be a SHA-256 digest")
        if not isinstance(self.chain_valid, bool):
            raise TypeError("RPI chain_valid must be a bool")


@dataclass(frozen=True)
class RpiDataAdmission:
    status: str
    common_start_ms: int | None
    common_end_ms: int | None
    common_span_ms: int
    feature_age_ms: int | None
    common_instruments: tuple[str, ...]
    row_count: int
    chain_valid: bool
    reasons: tuple[str, ...]
    exploration_allowed: bool
    formal_validation_allowed: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def default_rpi_hypotheses() -> tuple[RealtimeHypothesis, ...]:
    """The immutable three-trial, one-hour RPI research family.

    Continuation is the economic claim, reversal is its explicit direction
    falsifier, and deep/top divergence tests whether RPI-inclusive depth adds
    information beyond the already-researched five-level book.  All share one
    horizon so their PBO matrix has a coherent calendar and annualization.
    """
    definitions = (
        (
            "rpi_depth_continuation",
            "RPI-inclusive deep book imbalance provides persistent liquidity support over the next hour.",
            "The edge must survive doubled costs and disappear when deep RPI imbalance is absent.",
        ),
        (
            "rpi_depth_reversal",
            "RPI-inclusive deep liquidity walls attract price and cause a one-hour cross-sectional reversal.",
            "The sign-reversed control must independently clear the same locked holdout and cost gates.",
        ),
        (
            "rpi_top_divergence",
            "Deep RPI-inclusive liquidity dominates unsupported five-level imbalance when the two books diverge.",
            "The deep-minus-shallow signal must add net information beyond either displayed book alone.",
        ),
    )
    return tuple(
        RealtimeHypothesis(
            candidate_id=f"rt_{mechanism}_1h_v1",
            family_id="rpi_liquidity",
            mechanism=mechanism,
            horizon_ms=HOUR_MS,
            required_features=(
                "rt_mid_price",
                "rt_spread_bps",
                "rt_depth_imbalance_5",
                RPI_FEATURE_ID,
            ),
            economic_hypothesis=hypothesis,
            falsification_standard=falsification,
            signal_version="realtime-rpi-depth.v1",
        )
        for mechanism, hypothesis, falsification in definitions
    )


def assess_rpi_data_admission(
    observations: Sequence[RpiDepthObservation],
    *,
    now_ms: int,
    chain_valid: bool,
    expected_instruments: Sequence[str] | None = None,
    min_instruments: int = RPI_MIN_INSTRUMENTS,
    exploration_min_span_ms: int = RPI_EXPLORATION_MIN_SPAN_MS,
    validation_min_span_ms: int = RPI_VALIDATION_MIN_SPAN_MS,
    max_age_ms: int = RPI_MAX_AGE_MS,
) -> RpiDataAdmission:
    if type(now_ms) is not int or now_ms < 0:
        raise ValueError("now_ms must be a non-negative integer")
    if not isinstance(chain_valid, bool):
        raise TypeError("chain_valid must be a bool")
    expected = None
    if expected_instruments is not None:
        expected = tuple(sorted(str(item) for item in expected_instruments))
        if not expected or len(expected) != len(set(expected)) or any(
            not item for item in expected
        ):
            raise ValueError("expected_instruments must be non-empty and unique")
    rows = tuple(observations)
    if any(not isinstance(item, RpiDepthObservation) for item in rows):
        raise TypeError("observations must contain RpiDepthObservation values")
    grouped: dict[str, list[RpiDepthObservation]] = {}
    for item in rows:
        grouped.setdefault(item.instrument_id, []).append(item)
    instruments = tuple(sorted(grouped))
    common_start = (
        max(min(item.knowledge_time_ms for item in grouped[name]) for name in instruments)
        if instruments
        else None
    )
    common_end = (
        min(max(item.knowledge_time_ms for item in grouped[name]) for name in instruments)
        if instruments
        else None
    )
    span = (
        max(0, int(common_end) - int(common_start))
        if common_start is not None and common_end is not None
        else 0
    )
    age = None if common_end is None else max(0, now_ms - common_end)
    reasons: list[str] = []
    if not chain_valid:
        reasons.append("RPI_AUTHORITY_CHAIN_INVALID")
    if expected is not None and instruments != expected:
        reasons.append("RPI_FIXED_UNIVERSE_MISMATCH")
    elif len(instruments) < min_instruments:
        reasons.append("RPI_COMMON_INSTRUMENTS_BELOW_MINIMUM")
    if common_end is not None and common_end > now_ms:
        reasons.append("RPI_KNOWLEDGE_TIME_IN_FUTURE")
    if age is not None and age > max_age_ms:
        reasons.append("RPI_FEATURE_STALE")

    hard_integrity = any(
        reason in {"RPI_AUTHORITY_CHAIN_INVALID", "RPI_KNOWLEDGE_TIME_IN_FUTURE"}
        for reason in reasons
    )
    coverage_block = any(
        reason in {
            "RPI_FIXED_UNIVERSE_MISMATCH",
            "RPI_COMMON_INSTRUMENTS_BELOW_MINIMUM",
        }
        for reason in reasons
    )
    stale = "RPI_FEATURE_STALE" in reasons
    exploration = (
        not hard_integrity
        and not coverage_block
        and not stale
        and span >= exploration_min_span_ms
    )
    formal = exploration and span >= validation_min_span_ms
    if hard_integrity:
        status = "BLOCKED_INTEGRITY"
    elif coverage_block:
        status = "BLOCKED_COVERAGE"
    elif stale:
        status = "BLOCKED_STALE"
    elif formal:
        status = "VALIDATION_READY"
    elif exploration:
        status = "EXPLORATION_READY"
    else:
        status = "COLLECTING_DATA"
        reasons.append("RPI_HISTORY_BELOW_3D")
    if not formal:
        reasons.append("RPI_HISTORY_BELOW_30D")
    return RpiDataAdmission(
        status=status,
        common_start_ms=common_start,
        common_end_ms=common_end,
        common_span_ms=span,
        feature_age_ms=age,
        common_instruments=instruments,
        row_count=len(rows),
        chain_valid=chain_valid,
        reasons=tuple(dict.fromkeys(reasons)),
        exploration_allowed=exploration,
        formal_validation_allowed=formal,
    )


def enrich_frames_with_rpi(
    frames_by_instrument: Mapping[str, Sequence[RealtimeFrame]],
    observations: Sequence[RpiDepthObservation],
    *,
    max_age_ms: int = RPI_MAX_AGE_MS,
) -> dict[str, tuple[RealtimeFrame, ...]]:
    """Attach only the latest RPI observation known by each decision time."""
    if type(max_age_ms) is not int or max_age_ms < 0:
        raise ValueError("max_age_ms must be a non-negative integer")
    grouped: dict[str, list[RpiDepthObservation]] = {}
    for item in observations:
        if not isinstance(item, RpiDepthObservation):
            raise TypeError("observations must contain RpiDepthObservation values")
        grouped.setdefault(item.instrument_id, []).append(item)
    output: dict[str, tuple[RealtimeFrame, ...]] = {}
    for instrument, raw_frames in sorted(frames_by_instrument.items()):
        frames = tuple(sorted(raw_frames, key=lambda item: item.decision_time_ms))
        if any(item.instrument_id != instrument for item in frames):
            raise ValueError("frame instrument does not match its series key")
        observed = sorted(
            grouped.get(instrument, ()), key=lambda item: item.knowledge_time_ms
        )
        knowledge_times = [item.knowledge_time_ms for item in observed]
        enriched: list[RealtimeFrame] = []
        for frame in frames:
            index = bisect_right(knowledge_times, frame.decision_time_ms) - 1
            value: float | None = None
            if index >= 0:
                item = observed[index]
                if frame.decision_time_ms - item.knowledge_time_ms <= max_age_ms:
                    value = float(item.notional_imbalance)
            enriched.append(
                replace(frame, rpi_notional_imbalance_1000=value)
            )
        output[instrument] = tuple(enriched)
    return output


__all__ = [
    "HOUR_MS",
    "RPI_DATASET_ID",
    "RPI_FEATURE_ID",
    "RpiDataAdmission",
    "RpiDataset",
    "RpiDepthObservation",
    "assess_rpi_data_admission",
    "default_rpi_hypotheses",
    "enrich_frames_with_rpi",
]
