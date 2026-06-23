"""Backtest performance metrics (Phase 4)."""
from __future__ import annotations

import math
import statistics
from typing import Sequence


def returns_from_equity(equity: Sequence[float]) -> list[float]:
    out = []
    for a, b in zip(equity, equity[1:]):
        if a != 0:
            out.append(b / a - 1.0)
    return out


def sharpe(returns: Sequence[float], periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    sd = statistics.pstdev(returns)
    mean = statistics.fmean(returns)
    if sd > 0:
        return (mean / sd) * math.sqrt(periods_per_year)
    return math.inf if mean > 0 else 0.0   # zero-volatility positive return = infinite reward/risk


def sortino(returns: Sequence[float], periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    downside = [min(0.0, r) for r in returns]
    dd = statistics.pstdev(downside)
    mean = statistics.fmean(returns)
    if dd > 0:
        return (mean / dd) * math.sqrt(periods_per_year)
    return math.inf if mean > 0 else 0.0   # no downside + positive return = infinite Sortino


def max_drawdown(equity: Sequence[float]) -> float:
    """Max peak-to-trough loss, clamped to [0, 1] (a 100% loss is total)."""
    peak = -math.inf
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak
            mdd = max(mdd, min(1.0, max(0.0, dd)))
    return mdd


def metrics(equity: Sequence[float], periods_per_year: int = 252) -> dict:
    rets = returns_from_equity(equity)
    total_return = (equity[-1] / equity[0] - 1.0) if equity and equity[0] else 0.0
    vol = statistics.pstdev(rets) * math.sqrt(periods_per_year) if len(rets) > 1 else 0.0
    mdd = max_drawdown(equity)
    gross = 1.0 + total_return
    # annualize by the number of periods observed (proxy when no date span given)
    if rets and gross > 0:
        ann = gross ** (periods_per_year / len(rets)) - 1.0
    elif rets:
        ann = -1.0   # wiped out
    else:
        ann = 0.0
    return {
        "total_return": total_return,
        "sharpe": sharpe(rets, periods_per_year),
        "sortino": sortino(rets, periods_per_year),
        "volatility": vol,
        "max_drawdown": mdd,
        "calmar": (ann / mdd) if mdd > 0 else 0.0,
        "n_periods": len(rets),
    }
