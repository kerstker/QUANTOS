"""Data quality checks (Phase 2).

Detect the data defects that silently corrupt research: missing sessions,
duplicate timestamps, out-of-order timestamps, and future-dated records. Emits
a DataQualityReport (contract) whose `passed` aggregates the checks.
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from quantos.contracts.data import DataQualityCheck, DataQualityReport


def count_duplicate_event_times(times: Sequence[int]) -> int:
    seen, dups = set(), 0
    for t in times:
        if t in seen:
            dups += 1
        seen.add(t)
    return dups


def count_out_of_order(times: Sequence[int]) -> int:
    """Number of adjacent pairs where time goes backwards."""
    return sum(1 for a, b in zip(times, times[1:]) if b < a)


def missing_sessions(expected_days: Iterable[str], present_days: Iterable[str]) -> list[str]:
    present = set(present_days)
    return sorted(d for d in expected_days if d not in present)


def count_future(times: Sequence[int], as_of: int) -> int:
    """Records claiming an event_time after the as-of/now boundary."""
    return sum(1 for t in times if t > as_of)


def build_report(
    dataset_id: str,
    event_times: Sequence[int],
    as_of: Optional[int] = None,
    expected_days: Optional[Iterable[str]] = None,
    present_days: Optional[Iterable[str]] = None,
    version: str = "",
) -> DataQualityReport:
    dups = count_duplicate_event_times(event_times)
    ooo = count_out_of_order(event_times)
    missing = missing_sessions(expected_days or [], present_days or [])
    future = count_future(event_times, as_of) if as_of is not None else 0

    checks = [
        DataQualityCheck(name="duplicate_timestamps", passed=dups == 0, detail=f"{dups} duplicate(s)"),
        DataQualityCheck(name="ordering", passed=ooo == 0, detail=f"{ooo} out-of-order"),
        DataQualityCheck(name="completeness", passed=len(missing) == 0, detail=f"{len(missing)} missing session(s)"),
    ]
    if as_of is not None:
        checks.append(DataQualityCheck(name="no_future_timestamps", passed=future == 0, detail=f"{future} future record(s)"))

    return DataQualityReport(
        dataset_id=dataset_id, version=version,
        missing_count=len(missing), duplicate_count=dups, out_of_order_count=ooo,
        checks=tuple(checks),
    )
