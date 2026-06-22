"""PIT-correct feature calculation (Phase 3).

A rolling feature at bar i is computed ONLY from bars[i-window+1 .. i] (past +
current; never future). Its knowledge_time is the max knowledge_time of the
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


def rolling_feature(
    feature_id: str,
    bars: Sequence[Bar],
    window: int,
    agg: Callable[[list[float]], float] = statistics.fmean,
) -> list[FeatureValue]:
    """Rolling aggregate over `close`. Warmup positions (i+1 < window) get None."""
    if window < 1:
        raise ValueError("window must be >= 1")
    rows = _sorted(bars)
    out: list[FeatureValue] = []
    for i, b in enumerate(rows):
        if i + 1 < window:
            value, kt = None, b.knowledge_time
        else:
            win = rows[i + 1 - window: i + 1]
            value = float(agg([w.close for w in win]))
            kt = max(w.knowledge_time for w in win)
        out.append(FeatureValue(
            feature_id=feature_id, instrument_id=b.instrument_id,
            event_time=b.event_time, knowledge_time=kt, value=value,
        ))
    return out


def momentum(values: list[float]) -> float:
    """Simple window momentum: last / first - 1."""
    return values[-1] / values[0] - 1.0 if values and values[0] else 0.0


def zscore(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = statistics.fmean(values)
    sd = statistics.pstdev(values)
    return (values[-1] - mu) / sd if sd > 0 else 0.0
