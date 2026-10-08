"""Domain-neutral performance metrics used by backtests and alpha evaluation."""
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
    # 标准 Sortino 分母: RMS of downside returns around MAR=0 (二阶下偏矩),
    # 而非 pstdev(downside) (围绕下行均值中心化, 会系统性低估下行偏差)
    dd = math.sqrt(statistics.fmean([d * d for d in downside]))
    mean = statistics.fmean(returns)
    if dd > 0:
        return (mean / dd) * math.sqrt(periods_per_year)
    return math.inf if mean > 0 else 0.0   # no downside + positive return = infinite Sortino


def generalization_gap(returns: Sequence[float], *, embargo: int = 1,
                       min_len: int = 40, gap_scale: float = 2.0):
    """Normalised in-sample-minus-out-of-sample Sharpe gap of a return stream, in [0,1] (or None).

    Split `returns` into a train half (first) and a test half (second, after an `embargo` gap to avoid
    boundary overlap). The gap = max(0, IS_Sharpe - OOS_Sharpe): only the OVERFIT direction (strong
    in-sample, weak out-of-sample) is penalised; a factor that holds up out-of-sample scores ~0. Returns
    None (-> NO penalty, the conservative default) when the stream is too short to split honestly or when
    either half is degenerate (non-finite Sharpe). Pure, deterministic, stdlib.

    Used as a SEARCH-REWARD-ONLY signal by both search engines (intelligence/reward + research/alpha_factory)
    to bias AWAY from selection-overfit candidates, complementing (not replacing) the post-hoc CPCV/OOS gate.
    Single mid-split: cheap and scales with overfit severity, but a back-loaded factor can escape it."""
    n = len(returns)
    if n < min_len:
        return None
    mid = n // 2
    train = list(returns[:mid])
    test = list(returns[mid + embargo:])
    if len(train) < min_len // 2 or len(test) < min_len // 2:
        return None
    is_sr, oos_sr = sharpe(train), sharpe(test)
    if not (math.isfinite(is_sr) and math.isfinite(oos_sr)):
        return None
    gap = max(0.0, is_sr - oos_sr) / gap_scale
    return min(max(gap, 0.0), 1.0)


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
