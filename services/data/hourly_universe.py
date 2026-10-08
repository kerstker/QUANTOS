"""Point-in-time, immutable hourly universe selection contracts."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable


MAX_FINITE_BINARY64 = float.fromhex("0x1.fffffffffffffp+1023")


class UniverseAdmissionError(ValueError):
    """Raised when too few instruments satisfy a universe policy."""


def _time(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _finite(value: object, name: str) -> float:
    try:
        valid = (not isinstance(value, bool)
                 and isinstance(value, (int, float))
                 and isfinite(value))
    except OverflowError:
        raise ValueError(f"{name} must be finite") from None
    if not valid:
        raise ValueError(f"{name} must be finite")
    return value


def _instrument(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("instrument must be non-empty")
    return value


@dataclass(frozen=True, slots=True)
class UniverseObservation:
    instrument: str
    event_time: int
    knowledge_time: int
    quote_volume: float
    spread_bps: float
    listed_at: int
    data_coverage: float

    def __post_init__(self) -> None:
        _instrument(self.instrument)
        _time(self.event_time, "event_time")
        _time(self.knowledge_time, "knowledge_time")
        _time(self.listed_at, "listed_at")
        if self.knowledge_time < self.event_time:
            raise ValueError("knowledge_time must be >= event_time")
        if self.listed_at > self.event_time:
            raise ValueError("listed_at must be <= event_time")
        volume = _finite(self.quote_volume, "quote_volume")
        spread = _finite(self.spread_bps, "spread_bps")
        coverage = _finite(self.data_coverage, "data_coverage")
        if volume < 0:
            raise ValueError("quote_volume must be non-negative")
        if spread < 0:
            raise ValueError("spread_bps must be non-negative")
        if not 0 <= coverage <= 1:
            raise ValueError("data_coverage must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class UniversePolicy:
    min_size: int = 30
    max_size: int = 80
    min_listing_age_seconds: int = 0
    max_spread_bps: float = MAX_FINITE_BINARY64
    min_data_coverage: float = 0.0

    def __post_init__(self) -> None:
        if (isinstance(self.min_size, bool) or not isinstance(self.min_size, int)
                or isinstance(self.max_size, bool) or not isinstance(self.max_size, int)
                or not 0 < self.min_size <= self.max_size <= 80):
            raise ValueError("policy requires 0 < min_size <= max_size <= 80")
        _time(self.min_listing_age_seconds, "min_listing_age_seconds")
        spread = _finite(self.max_spread_bps, "max_spread_bps")
        coverage = _finite(self.min_data_coverage, "min_data_coverage")
        if spread < 0:
            raise ValueError("max_spread_bps must be non-negative")
        if not 0 <= coverage <= 1:
            raise ValueError("min_data_coverage must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class HourlyUniverse:
    as_of: int
    instruments: tuple[str, ...]
    observations: tuple[UniverseObservation, ...]
    policy: UniversePolicy

    def __post_init__(self) -> None:
        _time(self.as_of, "as_of")
        if not isinstance(self.instruments, tuple) or not isinstance(self.observations, tuple):
            raise ValueError("universe collections must be tuples")
        if len(self.instruments) != len(self.observations):
            raise ValueError("instruments and observations must align")
        if any(not isinstance(row, UniverseObservation) for row in self.observations):
            raise ValueError("observations must contain UniverseObservation records")
        if self.instruments != tuple(row.instrument for row in self.observations):
            raise ValueError("instruments must match observations")
        if not isinstance(self.policy, UniversePolicy):
            raise ValueError("policy must be UniversePolicy")


def select_hourly_universe(
    observations: Iterable[UniverseObservation], *, as_of: int, policy: UniversePolicy
) -> HourlyUniverse:
    """Select the latest causal observation for each instrument and apply policy."""
    _time(as_of, "as_of")
    if not isinstance(policy, UniversePolicy):
        raise ValueError("policy must be UniversePolicy")
    visible: dict[tuple[str, int, int], UniverseObservation] = {}
    for row in observations:
        if not isinstance(row, UniverseObservation):
            raise ValueError("observations must contain UniverseObservation records")
        if row.event_time > as_of or row.knowledge_time > as_of:
            continue
        key = (row.instrument, row.event_time, row.knowledge_time)
        prior = visible.get(key)
        if prior is not None and prior != row:
            raise ValueError("conflicting observations share the same point-in-time key")
        visible[key] = row

    latest: dict[str, UniverseObservation] = {}
    for row in visible.values():
        prior = latest.get(row.instrument)
        if prior is None or (row.event_time, row.knowledge_time) > (prior.event_time, prior.knowledge_time):
            latest[row.instrument] = row

    eligible = [
        row for row in latest.values()
        if as_of - row.listed_at >= policy.min_listing_age_seconds
        and row.spread_bps <= policy.max_spread_bps
        and row.data_coverage >= policy.min_data_coverage
    ]
    eligible.sort(key=lambda row: (-row.quote_volume, row.instrument))
    if len(eligible) < policy.min_size:
        raise UniverseAdmissionError(
            f"only {len(eligible)} instruments admitted; minimum is {policy.min_size}"
        )
    selected = tuple(eligible[:policy.max_size])
    return HourlyUniverse(as_of, tuple(row.instrument for row in selected), selected, policy)
