"""Outcome-blind primitives for forced-liquidation episode accounting.

The module deliberately carries no price, size, return, label, or execution
field.  It turns public liquidation prints into closed bursts and then into
independent prediction units using only identities and the three recorded
clocks.  Outcome construction belongs to a later terminal unblinding step.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


MAX_FUTURE_SKEW_MS = 30_000
_INSTRUMENT = re.compile(r"[A-Z0-9-]{2,48}")
_PRINT_ID = re.compile(r"[A-Za-z0-9:_-]{1,160}")
_SIDES = frozenset({"Buy", "Sell"})


def _non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


@dataclass(frozen=True, slots=True)
class LiquidationPrint:
    """One public forced-liquidation observation without outcome fields."""

    instrument: str
    liquidated_position_side: str
    event_time_ms: int
    received_at_ms: int
    processed_at_ms: int
    print_id: str

    def __post_init__(self) -> None:
        if _INSTRUMENT.fullmatch(self.instrument) is None:
            raise ValueError("instrument is invalid")
        if self.liquidated_position_side not in _SIDES:
            raise ValueError("liquidated_position_side is invalid")
        if _PRINT_ID.fullmatch(self.print_id) is None:
            raise ValueError("print_id is invalid")
        event_time_ms = _non_negative_int(self.event_time_ms, "event_time_ms")
        received_at_ms = _non_negative_int(self.received_at_ms, "received_at_ms")
        processed_at_ms = _non_negative_int(self.processed_at_ms, "processed_at_ms")
        if (
            event_time_ms > received_at_ms + MAX_FUTURE_SKEW_MS
            or received_at_ms > processed_at_ms
        ):
            raise ValueError("print clocks are causally invalid")

    @property
    def forced_pressure_side(self) -> str:
        """The aggressive flow side implied by the liquidated position."""

        return "Sell" if self.liquidated_position_side == "Buy" else "Buy"


@dataclass(frozen=True, slots=True)
class LiquidationBurst:
    """A quiet-period-closed group of same-instrument, same-side prints."""

    instrument: str
    liquidated_position_side: str
    start_received_at_ms: int
    end_received_at_ms: int
    first_event_time_ms: int
    last_event_time_ms: int
    last_processed_at_ms: int
    print_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if _INSTRUMENT.fullmatch(self.instrument) is None:
            raise ValueError("instrument is invalid")
        if self.liquidated_position_side not in _SIDES:
            raise ValueError("liquidated_position_side is invalid")
        for name in (
            "start_received_at_ms",
            "end_received_at_ms",
            "first_event_time_ms",
            "last_event_time_ms",
            "last_processed_at_ms",
        ):
            _non_negative_int(getattr(self, name), name)
        if self.start_received_at_ms > self.end_received_at_ms:
            raise ValueError("burst receive interval is invalid")
        if not self.print_ids or len(set(self.print_ids)) != len(self.print_ids):
            raise ValueError("burst print_ids must be non-empty and unique")
        if any(_PRINT_ID.fullmatch(value) is None for value in self.print_ids):
            raise ValueError("burst print_id is invalid")

    @property
    def forced_pressure_side(self) -> str:
        return "Sell" if self.liquidated_position_side == "Buy" else "Buy"

    @property
    def print_count(self) -> int:
        return len(self.print_ids)


def _burst(rows: list[LiquidationPrint]) -> LiquidationBurst:
    return LiquidationBurst(
        instrument=rows[0].instrument,
        liquidated_position_side=rows[0].liquidated_position_side,
        start_received_at_ms=rows[0].received_at_ms,
        end_received_at_ms=rows[-1].received_at_ms,
        first_event_time_ms=min(row.event_time_ms for row in rows),
        last_event_time_ms=max(row.event_time_ms for row in rows),
        last_processed_at_ms=max(row.processed_at_ms for row in rows),
        print_ids=tuple(row.print_id for row in rows),
    )


def group_closed_liquidation_bursts(
    prints: Iterable[LiquidationPrint],
    *,
    observed_through_ms: int,
    max_gap_ms: int = 5_000,
    quiet_period_ms: int = 2_000,
) -> tuple[LiquidationBurst, ...]:
    """Group only prints known by ``observed_through_ms`` into closed bursts.

    A burst is emitted only after its fixed quiet period has elapsed.  Appending
    observations whose processing clocks are after the observation boundary
    therefore cannot change this result.
    """

    boundary = _non_negative_int(observed_through_ms, "observed_through_ms")
    gap = _non_negative_int(max_gap_ms, "max_gap_ms")
    quiet = _non_negative_int(quiet_period_ms, "quiet_period_ms")
    if gap == 0 or quiet == 0:
        raise ValueError("max_gap_ms and quiet_period_ms must be positive")
    rows = tuple(prints)
    if any(not isinstance(row, LiquidationPrint) for row in rows):
        raise TypeError("prints must contain LiquidationPrint values")
    identifiers = [row.print_id for row in rows]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("prints contain duplicate print_id values")
    known = sorted(
        (row for row in rows if row.processed_at_ms <= boundary),
        key=lambda row: (
            row.instrument,
            row.liquidated_position_side,
            row.received_at_ms,
            row.event_time_ms,
            row.processed_at_ms,
            row.print_id,
        ),
    )
    groups: list[list[LiquidationPrint]] = []
    current: list[LiquidationPrint] = []
    current_key: tuple[str, str] | None = None
    for row in known:
        key = (row.instrument, row.liquidated_position_side)
        if (
            current
            and key == current_key
            and row.received_at_ms - current[-1].received_at_ms <= gap
        ):
            current.append(row)
            continue
        if current:
            groups.append(current)
        current = [row]
        current_key = key
    if current:
        groups.append(current)
    closed = (
        _burst(group)
        for group in groups
        if group[-1].received_at_ms + quiet <= boundary
    )
    return tuple(
        sorted(
            closed,
            key=lambda burst: (
                burst.start_received_at_ms,
                burst.instrument,
                burst.liquidated_position_side,
                burst.print_ids,
            ),
        )
    )


def select_independent_bursts(
    bursts: Iterable[LiquidationBurst],
    *,
    lockout_ms: int = 30 * 60 * 1_000,
) -> tuple[LiquidationBurst, ...]:
    """Select the earliest burst, then lock the whole instrument regardless of side."""

    lockout = _non_negative_int(lockout_ms, "lockout_ms")
    if lockout == 0:
        raise ValueError("lockout_ms must be positive")
    rows = tuple(bursts)
    if any(not isinstance(row, LiquidationBurst) for row in rows):
        raise TypeError("bursts must contain LiquidationBurst values")
    ordered = sorted(
        rows,
        key=lambda row: (
            row.start_received_at_ms,
            row.instrument,
            row.liquidated_position_side,
            row.print_ids,
        ),
    )
    next_eligible: dict[str, int] = {}
    selected: list[LiquidationBurst] = []
    for row in ordered:
        if row.start_received_at_ms < next_eligible.get(row.instrument, 0):
            continue
        selected.append(row)
        next_eligible[row.instrument] = row.end_received_at_ms + lockout
    return tuple(selected)


__all__ = [
    "LiquidationBurst",
    "LiquidationPrint",
    "group_closed_liquidation_bursts",
    "select_independent_bursts",
]
