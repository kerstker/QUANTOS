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


def _volatility(closes: Sequence[float], window: int) -> Optional[float]:
    """Realized volatility = pstdev of the last `window` simple returns (low-vol /
    risk factor). Needs window+1 closes; None on any zero denominator."""
    if window < 2 or len(closes) < window + 1:
        return None
    rets = []
    for i in range(len(closes) - window, len(closes)):
        if closes[i - 1] == 0:
            return None
        rets.append(closes[i] / closes[i - 1] - 1.0)
    return statistics.pstdev(rets)


def _ts_rank(closes: Sequence[float], window: int) -> Optional[float]:
    """Fraction of the last `window` closes that are <= the latest close, in (0,1]
    (relative position within its own recent range)."""
    if window < 2 or len(closes) < window:
        return None
    w = closes[-window:]
    last = w[-1]
    return sum(1 for x in w if x <= last) / window


def _ema_ratio(closes: Sequence[float], window: int) -> Optional[float]:
    """closes[-1]/EMA - 1, EMA with alpha=2/(window+1) iterated from closes[0]
    (deterministic trend signal). None if empty or EMA degenerates to 0."""
    if window < 1 or not closes:
        return None
    alpha = 2.0 / (window + 1.0)
    ema = closes[0]
    for c in closes[1:]:
        ema = alpha * c + (1.0 - alpha) * ema
    return None if ema == 0 else closes[-1] / ema - 1.0


def _dist_from_high(closes: Sequence[float], window: int) -> Optional[float]:
    """closes[-1]/max(last window) - 1 (<= 0): distance below the recent high."""
    if window < 1 or len(closes) < window:
        return None
    hi = max(closes[-window:])
    return None if hi == 0 else closes[-1] / hi - 1.0


def _accel(closes: Sequence[float], window: int) -> Optional[float]:
    """Momentum acceleration: momentum(window) over the latest window minus momentum
    over the preceding window. None unless both legs are computable (len >= 2*window)."""
    now = _momentum(closes, window)
    prev = _momentum(closes[:-window] if window else closes, window)
    if now is None or prev is None:
        return None
    return now - prev


SIGNAL_OPS: dict[str, Callable[[Sequence[float], int], Optional[float]]] = {
    "momentum": _momentum,
    "ts_mean": _ts_mean,
    "zscore": _zscore,
    "volatility": _volatility,
    "ts_rank": _ts_rank,
    "ema_ratio": _ema_ratio,
    "dist_from_high": _dist_from_high,
    "accel": _accel,
}
