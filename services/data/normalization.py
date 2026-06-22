"""Normalization, timestamp alignment, and ingestion-time guards (Phase 2).

Raw vendor rows are normalized into validated Bar contracts (bad/inconsistent
rows raise at construction). Timestamps are normalized to epoch microseconds UTC
so sources in different timezones align to the same instant. Future-dated bars
are rejected fail-closed at ingestion.
"""
from __future__ import annotations

from typing import Iterable

from quantos.contracts.data import Bar


def normalize_timestamp(epoch_seconds_local: int, tz_offset_seconds: int) -> int:
    """Convert a local epoch-seconds reading + UTC offset to epoch microseconds UTC."""
    return (int(epoch_seconds_local) - int(tz_offset_seconds)) * 1_000_000


class FutureDataError(ValueError):
    """Raised when a record is dated after the ingestion/as-of boundary."""


def normalize_bar(raw: dict, knowledge_time: int) -> Bar:
    """Build a validated Bar from a raw mapping. `knowledge_time` is supplied by
    the caller (ingestion clock); the raw row must NOT carry its own. Raises
    pydantic ValidationError on inconsistent OHLC / non-positive prices / lookahead."""
    if "knowledge_time" in raw:
        raise ValueError("raw row must not contain 'knowledge_time'; pass it explicitly")
    return Bar(knowledge_time=knowledge_time, **raw)


def reject_if_future(bar: Bar, now: int) -> Bar:
    if bar.event_time > now:
        raise FutureDataError(f"bar event_time {bar.event_time} is after now {now}")
    return bar


def align_to_calendar(present_dates: Iterable[str], trading_days: Iterable[str]) -> tuple[list[str], list[str]]:
    """Return (missing_sessions, unexpected_sessions) vs the trading calendar."""
    present, expected = set(present_dates), set(trading_days)
    missing = sorted(expected - present)
    extra = sorted(present - expected)
    return missing, extra
