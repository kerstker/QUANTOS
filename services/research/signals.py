"""Built-in cross-sectional signal operators (pipeline integration).

Each operator maps an instrument's PIT close history (oldest -> newest) plus a
window to a per-instrument scalar signal, or None when the history is too short or
degenerate (the panel builder then drops that instrument from the cross-section).
Pure, deterministic. PIT/no-lookahead is enforced by the caller passing only
knowable-by-t closes (store.as_of)."""
from __future__ import annotations

import statistics
from typing import Callable, Optional, Sequence


def _momentum(closes: Sequence[float], window: int) -> Optional[float]:
    if window < 1 or len(closes) < window or closes[-window] == 0:
        return None
    return closes[-1] / closes[-window] - 1.0


def _ts_mean(closes: Sequence[float], window: int) -> Optional[float]:
    if window < 1 or len(closes) < window:
        return None
    m = statistics.fmean(closes[-window:])
    return None if m == 0 else closes[-1] / m - 1.0


def _zscore(closes: Sequence[float], window: int) -> Optional[float]:
    if window < 2 or len(closes) < window:
        return None
    w = closes[-window:]
    sd = statistics.pstdev(w)
    return None if sd == 0 else (w[-1] - statistics.fmean(w)) / sd


SIGNAL_OPS: dict[str, Callable[[Sequence[float], int], Optional[float]]] = {
    "momentum": _momentum,
    "ts_mean": _ts_mean,
    "zscore": _zscore,
}
