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
    return (statistics.fmean(returns) / sd) * math.sqrt(periods_per_year) if sd > 0 else 0.0


def sortino(returns: Sequence[float], periods_per_year: int = 252) -> float:
    if len(returns) < 2:
        return 0.0
    downside = [min(0.0, r) for r in returns]
    dd = statistics.pstdev(downside)
    return (statistics.fmean(returns) / dd) * math.sqrt(periods_per_year) if dd > 0 else 0.0


def max_drawdown(equity: Sequence[float]) -> float:
    peak = -math.inf
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def metrics(equity: Sequence[float], periods_per_year: int = 252) -> dict:
    rets = returns_from_equity(equity)
    total_return = (equity[-1] / equity[0] - 1.0) if equity and equity[0] else 0.0
    vol = statistics.pstdev(rets) * math.sqrt(periods_per_year) if len(rets) > 1 else 0.0
    mdd = max_drawdown(equity)
    ann = total_return  # single-window proxy; annualization refined with date span later
    return {
        "total_return": total_return,
        "sharpe": sharpe(rets, periods_per_year),
        "sortino": sortino(rets, periods_per_year),
        "volatility": vol,
        "max_drawdown": mdd,
        "calmar": (ann / mdd) if mdd > 0 else 0.0,
        "n_periods": len(rets),
    }
