"""Volume / liquidity signal operators (the 量能/流动性 factor dimension).

Price-only factors are blind to participation. These operators read the per-instrument
volume series (and price, where a dollar figure is needed) and map (closes, volumes, window)
to a per-instrument scalar, or None when degenerate. Pure, deterministic; PIT/no-lookahead is
the caller's responsibility (it passes only knowable-by-t history, exactly like SIGNAL_OPS).

Note: with CoinGecko market_chart the volume series is 24h ROLLING USD volume (a liquidity
LEVEL), so these read as liquidity-level dynamics; with true per-bar volume they read as
classic volume factors. Same operators, same code.
"""
from __future__ import annotations

import statistics
from typing import Callable, Optional, Sequence


def _vol_momentum(closes: Sequence[float], volumes: Sequence[float], window: int) -> Optional[float]:
    if window < 1 or len(volumes) < window or volumes[-window] <= 0:
        return None
    return volumes[-1] / volumes[-window] - 1.0


def _vol_zscore(closes: Sequence[float], volumes: Sequence[float], window: int) -> Optional[float]:
    if window < 2 or len(volumes) < window:
        return None
    w = volumes[-window:]
    sd = statistics.pstdev(w)
    return None if sd <= 0 else (w[-1] - statistics.fmean(w)) / sd


def _dollar_vol_trend(closes: Sequence[float], volumes: Sequence[float], window: int) -> Optional[float]:
    """Latest dollar volume (close*volume) vs its window mean — a participation surge/fade."""
    if window < 1 or len(volumes) < window or len(closes) < window:
        return None
    dv = [closes[i] * volumes[i] for i in range(len(volumes))]
    m = statistics.fmean(dv[-window:])
    return None if m <= 0 else dv[-1] / m - 1.0


def _amihud(closes: Sequence[float], volumes: Sequence[float], window: int) -> Optional[float]:
    """Amihud illiquidity: mean over the window of |return| / dollar_volume (higher = thinner)."""
    if window < 1 or len(closes) < window + 1:
        return None
    vals = []
    for i in range(len(closes) - window, len(closes)):
        dv = closes[i] * volumes[i]
        if closes[i - 1] <= 0 or dv <= 0:
            return None
        vals.append(abs(closes[i] / closes[i - 1] - 1.0) / dv)
    return statistics.fmean(vals) if vals else None


VOLUME_OPS: dict[str, Callable[[Sequence[float], Sequence[float], int], Optional[float]]] = {
    "vol_momentum": _vol_momentum,
    "vol_zscore": _vol_zscore,
    "dollar_vol_trend": _dollar_vol_trend,
    "amihud": _amihud,
}
