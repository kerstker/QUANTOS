"""Series-level alpha metrics (Phase 6, build step 3).

`services.validation.factor_statistics.compute_ic` gives ONE cross-section's IC; this module derives the
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
from typing import Optional, Sequence


def _betacf(a: float, b: float, x: float) -> float:
    """Continued-fraction expansion for the regularized incomplete beta (NR §6.4)."""
    MAXIT, EPS, FPMIN = 300, 3.0e-14, 1.0e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < EPS:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b) in [0, 1] (deterministic)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


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
    sd = statistics.stdev(s)
    if sd <= 0:
        return None
    return statistics.fmean(s) / (sd / math.sqrt(len(s)))


def ic_pvalue(ic_series: Sequence[Optional[float]]) -> Optional[float]:
    """Two-sided Student-t p-value with df = T-1. The normal approximation
    overstates significance at the small T this system permits (review finding);
    the t-distribution has the correct heavier tails. Identity used:
    two-sided p = I_x(df/2, 1/2) with x = df / (df + t^2)."""
    t = ic_tstat(ic_series)
    if t is None:
        return None
    df = len(clean(ic_series)) - 1
    if df < 1:
        return None
    x = df / (df + t * t)
    return _betai(df / 2.0, 0.5, x)


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
