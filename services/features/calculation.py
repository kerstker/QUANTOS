"""PIT-correct feature calculation (Phase 3).

A rolling feature at bar i is computed ONLY from bars[i-window+1 .. i] (past +
current; never future), WITHIN the bar's own instrument: bars are grouped by
instrument_id and each instrument warms up and aggregates over its own history
only, so an interleaved multi-instrument input cannot mix another instrument's
closes into a window. Its knowledge_time is the max knowledge_time of the
contributing window, so the feature is knowable no earlier than its inputs.
Deterministic and reproducible (CT-3).
"""
from __future__ import annotations

import statistics
from typing import Callable, Sequence

from quantos.contracts.data import Bar
from quantos.contracts.feature import FeatureValue


def _sorted(bars: Sequence[Bar]) -> list[Bar]:
    return sorted(bars, key=lambda b: (b.event_time, b.knowledge_time))


def _by_instrument(bars: Sequence[Bar]) -> dict[str, list[Bar]]:
    """Group bars by instrument_id, first-appearance order (deterministic)."""
    out: dict[str, list[Bar]] = {}
    for b in bars:
        out.setdefault(b.instrument_id, []).append(b)
    return out


def _rolling_one_instrument(
    feature_id: str,
    rows: list[Bar],
    window: int,
    agg: Callable[[list[float]], float],
) -> list[tuple[Bar, FeatureValue]]:
    """Rolling pass over ONE instrument's rows (already sorted by event/knowledge time)."""
    out: list[tuple[Bar, FeatureValue]] = []
    for i, b in enumerate(rows):
        if i + 1 < window:
            value, kt = None, b.knowledge_time
        else:
            win = rows[i + 1 - window: i + 1]
            value = float(agg([w.close for w in win]))
            kt = max(w.knowledge_time for w in win)
        out.append((b, FeatureValue(
            feature_id=feature_id, instrument_id=b.instrument_id,
            event_time=b.event_time, knowledge_time=kt, value=value,
        )))
    return out


def rolling_feature(
    feature_id: str,
    bars: Sequence[Bar],
    window: int,
    agg: Callable[[list[float]], float] = statistics.fmean,
) -> list[FeatureValue]:
    """Rolling aggregate over `close`. Warmup positions (i+1 < window) get None.

    The window is per instrument_id (review P1-5): each instrument is warmed up and
    aggregated independently, so its values equal what a single-instrument call would
    produce. The merged output keeps the (event_time, knowledge_time) ordering of the
    input rows - unchanged for a single-instrument call."""
    if window < 1:
        raise ValueError("window must be >= 1")
    keyed: list[tuple[tuple[int, int], FeatureValue]] = []
    for rows in _by_instrument(bars).values():
        for b, fv in _rolling_one_instrument(feature_id, _sorted(rows), window, agg):
            keyed.append(((b.event_time, b.knowledge_time), fv))
    keyed.sort(key=lambda item: item[0])
    return [fv for _, fv in keyed]


def momentum(values: list[float]) -> float:
    """Simple window momentum: last / first - 1."""
    return values[-1] / values[0] - 1.0 if values and values[0] else 0.0


def zscore(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = statistics.fmean(values)
    sd = statistics.pstdev(values)
    return (values[-1] - mu) / sd if sd > 0 else 0.0
