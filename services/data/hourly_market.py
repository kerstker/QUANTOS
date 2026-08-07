"""Immutable, causal hourly market data contracts and bar aggregation."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable

_SCHEMA_VERSION = "hourly-market-v1"


def _time(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _instrument(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("instrument must be non-empty")
    return value


@dataclass(frozen=True, slots=True)
class HourlyBar:
    instrument: str
    event_time: int
    knowledge_time: int
    open: float
    high: float
    low: float
    close: float
    quote_volume: float

    def __post_init__(self) -> None:
        _instrument(self.instrument)
        _time(self.event_time, "event_time")
        _time(self.knowledge_time, "knowledge_time")
        if self.knowledge_time < self.event_time:
            raise ValueError("knowledge_time must be >= event_time")
        prices = tuple(_finite(getattr(self, n), n) for n in ("open", "high", "low", "close"))
        if any(price <= 0 for price in prices):
            raise ValueError("OHLC values must be positive")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("OHLC relationship is invalid")
        volume = _finite(self.quote_volume, "quote_volume")
        if volume < 0:
            raise ValueError("quote_volume must be non-negative")


@dataclass(frozen=True, slots=True)
class HourlyFunding:
    instrument: str
    event_time: int
    knowledge_time: int
    rate: float

    def __post_init__(self) -> None:
        _instrument(self.instrument)
        _time(self.event_time, "event_time")
        _time(self.knowledge_time, "knowledge_time")
        if self.knowledge_time < self.event_time:
            raise ValueError("knowledge_time must be >= event_time")
        _finite(self.rate, "rate")


@dataclass(frozen=True, slots=True)
class HourlyOpenInterest:
    instrument: str
    event_time: int
    knowledge_time: int
    value: float

    def __post_init__(self) -> None:
        _instrument(self.instrument)
        _time(self.event_time, "event_time")
        _time(self.knowledge_time, "knowledge_time")
        if self.knowledge_time < self.event_time:
            raise ValueError("knowledge_time must be >= event_time")
        value = _finite(self.value, "value")
        if value < 0:
            raise ValueError("value must be non-negative")


@dataclass(frozen=True, slots=True)
class HourlyMarketPanel:
    schema_version: str
    as_of: int
    bars: tuple[HourlyBar, ...]
    funding: tuple[HourlyFunding, ...]
    open_interest: tuple[HourlyOpenInterest, ...]
    source_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.schema_version, str) or not self.schema_version:
            raise ValueError("schema_version must be non-empty")
        _time(self.as_of, "as_of")
        if not isinstance(self.bars, tuple) or not isinstance(self.funding, tuple) or not isinstance(self.open_interest, tuple):
            raise ValueError("panel series must be tuples")
        if not isinstance(self.source_digest, str) or not self.source_digest.strip():
            raise ValueError("source_digest must be non-empty")
        _validate_panel_core(self.schema_version, self.as_of, self.bars, self.funding, self.open_interest)


def _validate_order(rows: tuple[object, ...]) -> None:
    previous: dict[str, int] = {}
    for row in rows:
        instrument = row.instrument
        prior = previous.get(instrument)
        if prior is not None and row.event_time <= prior:
            raise ValueError("event_time must be strictly increasing per instrument")
        previous[instrument] = row.event_time


def aggregate_bars(rows: Iterable[HourlyBar], *, interval_seconds: int) -> tuple[HourlyBar, ...]:
    if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or interval_seconds <= 0:
        raise ValueError("interval_seconds must be a positive integer")
    materialized = tuple(rows)
    if any(not isinstance(row, HourlyBar) for row in materialized):
        raise ValueError("rows must contain HourlyBar records")
    _validate_order(materialized)
    buckets: dict[tuple[str, int], list[HourlyBar]] = {}
    for row in materialized:
        key = (row.instrument, row.event_time // interval_seconds)
        buckets.setdefault(key, []).append(row)
    result = []
    for (instrument, bucket), bucket_rows in sorted(buckets.items()):
        result.append(HourlyBar(
            instrument, bucket * interval_seconds,
            max(row.knowledge_time for row in bucket_rows),
            bucket_rows[0].open, max(row.high for row in bucket_rows),
            min(row.low for row in bucket_rows), bucket_rows[-1].close,
            sum(row.quote_volume for row in bucket_rows),
        ))
    return tuple(result)


def _validate_panel_core(schema_version: str, as_of: int, bars: tuple[HourlyBar, ...],
                         funding: tuple[HourlyFunding, ...],
                         open_interest: tuple[HourlyOpenInterest, ...]) -> None:
    if schema_version != _SCHEMA_VERSION:
        raise ValueError("unsupported schema_version")
    for name, rows, expected in (
        ("bars", bars, HourlyBar),
        ("funding", funding, HourlyFunding),
        ("open_interest", open_interest, HourlyOpenInterest),
    ):
        if any(not isinstance(row, expected) for row in rows):
            raise ValueError(f"{name} must contain {expected.__name__} records")
    series = (bars, funding, open_interest)
    for rows in series:
        _validate_order(rows)
        for row in rows:
            if row.event_time > as_of or row.knowledge_time > as_of:
                raise ValueError("panel record is after as_of")
    bar_keys = {(row.instrument, row.event_time) for row in bars}
    for rows in series[1:]:
        keys = {(row.instrument, row.event_time) for row in rows}
        if keys and keys != bar_keys:
            raise ValueError("panel series instrument/time keys must match bars")


def validate_panel(panel: HourlyMarketPanel) -> None:
    if not isinstance(panel, HourlyMarketPanel):
        raise ValueError("panel must be HourlyMarketPanel")
    _validate_panel_core(panel.schema_version, panel.as_of, panel.bars, panel.funding, panel.open_interest)
