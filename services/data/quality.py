"""Data quality checks (Phase 2).

Detect the data defects that silently corrupt research: missing sessions,
duplicate timestamps, out-of-order timestamps, and future-dated records. Emits
a DataQualityReport (contract) whose `passed` aggregates the checks.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Optional, Sequence

from quantos.contracts.data import DataQualityCheck, DataQualityReport
from quantos.contracts.quality import check_volume_dispersion


class VolumeDispersionError(ValueError):
    """Capacity inputs are too dispersed to support a defensible estimate."""


def assert_volume_dispersion(volume_by_instrument, max_ratio: float = 1e6):
    check = check_volume_dispersion(volume_by_instrument, max_ratio=max_ratio)
    if not check.passed:
        raise VolumeDispersionError(check.detail)
    return check


# Backward-compatible aliases. New production code must use the dispersion names because a
# numeric ratio cannot establish unit provenance.
class VolumeUnitsError(VolumeDispersionError):
    pass


def check_volume_units(volume_by_instrument: Mapping[str, float],
                       max_ratio: float = 1e6) -> DataQualityCheck:
    check = check_volume_dispersion(volume_by_instrument, max_ratio=max_ratio)
    return check.model_copy(update={"name": "volume_units"})


def assert_volume_units(volume_by_instrument, max_ratio: float = 1e6):
    check = check_volume_units(volume_by_instrument, max_ratio=max_ratio)
    if not check.passed:
        raise VolumeUnitsError(check.detail)
    return check


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


def build_ingestion_report(
    dataset_id: str,
    event_times_by_instrument: Mapping[str, Sequence[int]],
    *,
    rejected_count: int = 0,
    as_of: Optional[int] = None,
    expected_interval: Optional[int] = None,
    expected_start: Optional[int] = None,
    expected_end: Optional[int] = None,
    version: str = "",
) -> DataQualityReport:
    """Build an ingestion report without conflating independent instruments.

    Arrival ordering and duplicate checks are evaluated inside each instrument.
    When ``expected_interval`` is supplied, completeness is checked over the
    explicit boundary if provided, otherwise over each instrument's observed
    span. This is the strict ingestion path; no store mutation happens here.
    """
    if isinstance(rejected_count, bool) or not isinstance(rejected_count, int) or rejected_count < 0:
        raise ValueError("rejected_count must be a non-negative integer")
    if as_of is not None and (isinstance(as_of, bool) or not isinstance(as_of, int)):
        raise ValueError("as_of must be an integer")
    if expected_interval is not None:
        if isinstance(expected_interval, bool) or not isinstance(expected_interval, int) \
                or expected_interval <= 0:
            raise ValueError("expected_interval must be a positive integer")
    elif expected_start is not None or expected_end is not None:
        raise ValueError("expected_start/expected_end require expected_interval")
    if expected_start is not None and (isinstance(expected_start, bool) or not isinstance(expected_start, int)):
        raise ValueError("expected_start must be an integer")
    if expected_end is not None and (isinstance(expected_end, bool) or not isinstance(expected_end, int)):
        raise ValueError("expected_end must be an integer")
    if expected_start is not None and expected_end is not None and expected_start > expected_end:
        raise ValueError("expected_start must not exceed expected_end")

    series: dict[str, tuple[int, ...]] = {}
    for key, times in event_times_by_instrument.items():
        validated: list[int] = []
        for t in times:
            if isinstance(t, bool) or not isinstance(t, int):
                raise ValueError(f"event_time must be an integer, got {t!r}")
            validated.append(t)
        series[str(key)] = tuple(validated)
    row_count = sum(len(times) for times in series.values())
    duplicate_count = sum(count_duplicate_event_times(times) for times in series.values())
    out_of_order_count = sum(count_out_of_order(times) for times in series.values())
    future_count = sum(count_future(times, as_of) for times in series.values()) \
        if as_of is not None else 0
    missing_count = 0
    off_grid_count = 0
    if expected_interval is not None:
        for times in series.values():
            # When callers provide explicit boundaries, an empty instrument is
            # a complete absence of the expected grid, not an instrument that
            # should be silently omitted from the aggregate.  This matters for
            # multi-instrument refreshes: a failed ticker must keep the quality
            # report BLOCKed instead of disappearing from ``event_times_by_instrument``.
            if not times:
                if expected_start is not None and expected_end is not None \
                        and expected_start <= expected_end:
                    missing_count += ((expected_end - expected_start) // expected_interval) + 1
                continue
            present = set(times)
            start = expected_start if expected_start is not None else min(present)
            end = expected_end if expected_end is not None else max(present)
            if start <= end:
                expected_count = ((end - start) // expected_interval) + 1
                on_grid_in_window = sum(
                    1
                    for value in present
                    if start <= value <= end and (value - start) % expected_interval == 0
                )
                missing_count += expected_count - on_grid_in_window
                off_grid_count += sum(
                    1 for value in present if (value - start) % expected_interval != 0
                )

    checks = [
        DataQualityCheck(
            name="valid_rows",
            passed=rejected_count == 0,
            detail=f"{rejected_count} rejected row(s)",
        ),
        DataQualityCheck(
            name="nonempty_rows",
            passed=row_count > 0,
            detail=f"{row_count} valid row(s)",
        ),
        DataQualityCheck(
            name="duplicate_timestamps",
            passed=duplicate_count == 0,
            detail=f"{duplicate_count} duplicate(s) within instrument",
        ),
        DataQualityCheck(
            name="ordering",
            passed=out_of_order_count == 0,
            detail=f"{out_of_order_count} out-of-order within instrument",
        ),
        DataQualityCheck(
            name="completeness",
            passed=missing_count == 0,
            detail=f"{missing_count} missing expected session(s)",
        ),
    ]
    if expected_interval is not None:
        checks.append(DataQualityCheck(
            name="cadence_alignment",
            passed=off_grid_count == 0,
            detail=f"{off_grid_count} off-grid timestamp(s)",
        ))
    if as_of is not None:
        checks.append(DataQualityCheck(
            name="no_future_timestamps",
            passed=future_count == 0,
            detail=f"{future_count} future record(s)",
        ))
    return DataQualityReport(
        dataset_id=dataset_id,
        version=version,
        missing_count=missing_count,
        duplicate_count=duplicate_count,
        out_of_order_count=out_of_order_count,
        checks=tuple(checks),
    )


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
