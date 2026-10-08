"""Pure primitives for the frozen forward attention-momentum experiment.

This module deliberately has no IO, wall clock, RNG, registry, lifecycle,
broker, credential, or order dependency.  Callers must provide only values
that were available by the decision knowledge time.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import statistics
from typing import Literal, Sequence


AttentionSignalMode = Literal[
    "attention_conditioned_momentum",
    "price_only_momentum",
    "timestamp_placebo",
    "attention_only",
]
_TIMESTAMP_SALT = "quantos.attention-momentum.timestamp-placebo.v1"


@dataclass(frozen=True)
class AttentionMomentumConfig:
    attention_lookback_days: int = 28
    momentum_lookback_days: int = 7
    holding_days: int = 1
    timestamp_placebo_window_days: int = 28
    universe_size: int = 13
    quantile: float = 0.25

    def __post_init__(self) -> None:
        for name in (
            "attention_lookback_days",
            "momentum_lookback_days",
            "holding_days",
            "timestamp_placebo_window_days",
            "universe_size",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if not math.isfinite(self.quantile) or not 0.0 < self.quantile < 0.5:
            raise ValueError("quantile must be finite and strictly between 0 and 0.5")


def _finite_vector(values: Sequence[float], *, name: str) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if not result:
        raise ValueError(f"{name} must not be empty")
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{name} must contain only finite values")
    return result


def rank_zscore(values: Sequence[float]) -> tuple[float, ...]:
    """Return deterministic average-rank z-scores with exact tie handling."""

    vector = _finite_vector(values, name="values")
    ordered = sorted(enumerate(vector), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(vector)
    start = 0
    while start < len(ordered):
        stop = start + 1
        while stop < len(ordered) and ordered[stop][1] == ordered[start][1]:
            stop += 1
        average_rank = (start + stop - 1) / 2.0
        for position in range(start, stop):
            ranks[ordered[position][0]] = average_rank
        start = stop
    sigma = statistics.pstdev(ranks)
    if sigma == 0.0:
        return tuple(0.0 for _value in ranks)
    mean = statistics.fmean(ranks)
    return tuple((rank - mean) / sigma for rank in ranks)


def attention_innovation(
    current_pageviews: Sequence[float],
    trailing_pageviews: Sequence[Sequence[float]],
) -> tuple[float, ...]:
    """Cross-sectionally rank a log-pageview innovation versus trailing medians."""

    current = _finite_vector(current_pageviews, name="current_pageviews")
    history = tuple(
        _finite_vector(row, name="trailing_pageviews row")
        for row in trailing_pageviews
    )
    if not history:
        raise ValueError("trailing_pageviews must not be empty")
    if any(len(row) != len(current) for row in history):
        raise ValueError("attention history shape mismatch")
    if any(value < 0.0 for value in current) or any(
        value < 0.0 for row in history for value in row
    ):
        raise ValueError("pageviews must be non-negative")
    innovations = []
    for column, value in enumerate(current):
        baseline = statistics.median(
            math.log1p(row[column]) for row in history
        )
        innovations.append(math.log1p(value) - baseline)
    return rank_zscore(innovations)


def price_momentum(
    latest_completed_close: Sequence[float],
    lagged_completed_close: Sequence[float],
) -> tuple[float, ...]:
    """Cross-sectionally rank fixed close-to-close log momentum."""

    latest = _finite_vector(latest_completed_close, name="latest_completed_close")
    lagged = _finite_vector(lagged_completed_close, name="lagged_completed_close")
    if len(latest) != len(lagged):
        raise ValueError("price vectors must have equal length")
    if any(value <= 0.0 for value in latest + lagged):
        raise ValueError("prices must be positive")
    return rank_zscore(
        tuple(math.log(current / previous) for current, previous in zip(latest, lagged, strict=True))
    )


def deterministic_timestamp_lag(
    event_time_ms: int,
    window_days: int,
) -> int:
    """Select a fixed prior-only placebo lag without consulting data or returns."""

    if isinstance(event_time_ms, bool) or not isinstance(event_time_ms, int):
        raise TypeError("event_time_ms must be an integer")
    if event_time_ms < 0:
        raise ValueError("event_time_ms must be non-negative")
    if isinstance(window_days, bool) or not isinstance(window_days, int):
        raise TypeError("window_days must be an integer")
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    digest = hashlib.sha256(
        f"{_TIMESTAMP_SALT}:{event_time_ms}".encode("utf-8")
    ).digest()
    return 1 + int.from_bytes(digest[:8], "big") % window_days


def combine_signal(
    momentum_rank_z: Sequence[float],
    attention_rank_z: Sequence[float],
    *,
    mode: AttentionSignalMode,
) -> tuple[float, ...]:
    """Create one of the four frozen candidate signals."""

    momentum = _finite_vector(momentum_rank_z, name="momentum_rank_z")
    attention = _finite_vector(attention_rank_z, name="attention_rank_z")
    if len(momentum) != len(attention):
        raise ValueError("momentum and attention vectors must have equal length")
    if mode == "price_only_momentum":
        return rank_zscore(momentum)
    if mode == "attention_only":
        return rank_zscore(attention)
    if mode not in (
        "attention_conditioned_momentum",
        "timestamp_placebo",
    ):
        raise ValueError("unsupported attention signal mode")
    conditioned = tuple(
        momentum_value * max(attention_value, 0.0)
        for momentum_value, attention_value in zip(momentum, attention, strict=True)
    )
    return rank_zscore(conditioned)


__all__ = [
    "AttentionMomentumConfig",
    "AttentionSignalMode",
    "attention_innovation",
    "combine_signal",
    "deterministic_timestamp_lag",
    "price_momentum",
    "rank_zscore",
]
