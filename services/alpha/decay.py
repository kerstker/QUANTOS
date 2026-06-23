"""Alpha decay tracking (Phase 6, step 9) — catch edge erosion before it bleeds.

Three complementary, deterministic signals over a rolling IC series:
  * temporal half-life from an AR(1) fit of {IC_t}: half_life = ln2 / -ln(phi);
  * horizon half-life from IC(h) = IC0 * exp(-lambda h) (OLS on log|IC(h)|);
  * IC trend slope (least squares).
Retirement triggers are DEBOUNCED (critique P1-5): the decay flag fires only when
the breach is sustained across the last `confirm_windows` windows, so a single
noisy window does not retire a good alpha. A performance-gap trigger
(backtest vs paper) is honored when supplied.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence

from quantos.contracts.alpha import DecayReport

from .config import AlphaFactoryConfig
from .metrics import clean, ic_autocorr


def half_life_from_phi(phi: float) -> float:
    """AR(1) decay half-life. phi in (0,1) -> finite; phi<=0 or phi>=1 -> inf."""
    if phi <= 0.0 or phi >= 1.0:
        return math.inf
    return math.log(2) / (-math.log(phi))


def ar1_half_life(ic_series: Sequence[Optional[float]]) -> float:
    phi = ic_autocorr(ic_series, lag=1)
    return half_life_from_phi(phi) if phi is not None else math.inf


def _trend_slope(series: Sequence[float]) -> Optional[float]:
    n = len(series)
    if n < 2:
        return None
    xs = list(range(n))
    mx, my = statistics.fmean(xs), statistics.fmean(series)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, series)) / den


def horizon_half_life(ic_by_horizon: dict[int, Optional[float]]) -> Optional[float]:
    items = sorted((h, ic) for h, ic in ic_by_horizon.items() if ic is not None and abs(ic) > 1e-9)
    if len(items) < 2:
        return None
    sign0 = 1 if items[0][1] > 0 else -1
    pts = [(h, math.log(abs(ic))) for h, ic in items if (1 if ic > 0 else -1) == sign0]
    if len(pts) < 2:
        return None
    xs = [h for h, _ in pts]
    ys = [y for _, y in pts]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    lam = -slope
    return math.log(2) / lam if lam > 0 else math.inf


def detect_decay(
    alpha_id: str,
    rolling_ic: Sequence[Optional[float]],
    *,
    config: Optional[AlphaFactoryConfig] = None,
    baseline: Optional[float] = None,
    perf_gap: Optional[float] = None,
    ic_by_horizon: Optional[dict[int, Optional[float]]] = None,
    as_of: int = 0,
) -> DecayReport:
    config = config or AlphaFactoryConfig()
    cw = config.confirm_windows
    series = clean(rolling_ic)

    if len(series) < cw + 1:
        return DecayReport(alpha_id=alpha_id, decayed=False, reasons=("INSUFFICIENT_HISTORY",), as_of=as_of)

    baseline_ic = baseline if baseline is not None else statistics.fmean(series[:-cw])
    recent = series[-cw:]
    ic_now = series[-1]
    retention = (statistics.fmean(recent) / baseline_ic) if baseline_ic not in (0, 0.0) else None
    half_life = ar1_half_life(series)
    half_life_h = horizon_half_life(ic_by_horizon) if ic_by_horizon else None
    slope = _trend_slope(series)

    reasons: list[str] = []
    # debounced: EVERY one of the last cw windows below the retention floor
    threshold = config.min_ic_retention * baseline_ic
    sustained_low = baseline_ic > 0 and len(recent) >= cw and all(v < threshold for v in recent)
    if sustained_low:
        reasons.append("IC_RETENTION_BELOW_MIN")
    if perf_gap is not None and perf_gap > config.max_perf_gap:
        reasons.append("PERF_GAP_EXCEEDS_MAX")

    return DecayReport(
        alpha_id=alpha_id, ic_now=ic_now, ic_baseline=baseline_ic,
        ic_retention_ratio=retention, half_life_time=half_life,
        half_life_horizon=half_life_h, ic_trend_slope=slope,
        decayed=bool(reasons), reasons=tuple(reasons), as_of=as_of,
    )
