"""Series-level alpha metrics (Phase 6, build step 3).

`factor_engine.compute_ic` gives ONE cross-section's IC; this module derives the
SERIES statistics it lacks and gets right — the **correct ICIR** = mean/std of a
SERIES of per-period ICs (not factor_engine's single-cross-section pair-product
figure), plus the IC t-stat, two-sided p-value, hit rate, lag-1 autocorrelation,
and breadth.

Thin coverage is handled fail-closed (critique P1-2): if too few periods have a
valid IC, or the mean cross-section is too narrow, the series is flagged
`coverage_ok=False` so the evaluator routes the alpha to REQUIRES_HUMAN_REVIEW
instead of silently overstating T and inflating the t-stat / p-value.
"""
from __future__ import annotations

import math
import statistics
from statistics import NormalDist
from typing import Optional, Sequence

_N = NormalDist()


def clean(ic_series: Sequence[Optional[float]]) -> list[float]:
    return [float(v) for v in ic_series if v is not None and math.isfinite(v)]


def icir(ic_series: Sequence[Optional[float]]) -> Optional[float]:
    """Information ratio of the IC SERIES = mean(IC_t) / std(IC_t)."""
    s = clean(ic_series)
    if len(s) < 2:
        return None
    sd = statistics.pstdev(s)
    return statistics.fmean(s) / sd if sd > 0 else None


def ic_tstat(ic_series: Sequence[Optional[float]]) -> Optional[float]:
    """t = mean / (std / sqrt(T)) = ICIR * sqrt(T)."""
    s = clean(ic_series)
    if len(s) < 2:
        return None
    sd = statistics.pstdev(s)
    if sd <= 0:
        return None
    return statistics.fmean(s) / (sd / math.sqrt(len(s)))


def ic_pvalue(ic_series: Sequence[Optional[float]]) -> Optional[float]:
    t = ic_tstat(ic_series)
    return None if t is None else 2.0 * (1.0 - _N.cdf(abs(t)))


def hit_rate(ic_series: Sequence[Optional[float]]) -> Optional[float]:
    s = clean(ic_series)
    return statistics.fmean([1.0 if v > 0 else 0.0 for v in s]) if s else None


def ic_autocorr(ic_series: Sequence[Optional[float]], lag: int = 1) -> Optional[float]:
    s = clean(ic_series)
    if len(s) < lag + 2:
        return None
    x, y = s[:-lag], s[lag:]
    mx, my = statistics.fmean(x), statistics.fmean(y)
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    vx = sum((a - mx) ** 2 for a in x)
    vy = sum((b - my) ** 2 for b in y)
    return cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else None


def series_metrics(
    ic_series: Sequence[Optional[float]],
    breadth_per_period: Sequence[int] = (),
    *,
    min_periods: int = 12,
    min_breadth: int = 10,
) -> dict:
    """Full series battery + a fail-closed coverage flag."""
    valid = clean(ic_series)
    breadths = [int(b) for b in breadth_per_period]
    mean_breadth = statistics.fmean(breadths) if breadths else 0.0
    coverage_ok = len(valid) >= min_periods and (not breadths or mean_breadth >= min_breadth)
    return {
        "mean_ic": statistics.fmean(valid) if valid else None,
        "ic_std": statistics.pstdev(valid) if len(valid) > 1 else None,
        "icir": icir(ic_series),
        "ic_tstat": ic_tstat(ic_series),
        "ic_pvalue": ic_pvalue(ic_series),
        "hit_rate": hit_rate(ic_series),
        "ic_autocorr": ic_autocorr(ic_series),
        "n_periods": len(valid),
        "mean_breadth": mean_breadth,
        "coverage_ok": coverage_ok,
    }
