"""Signal -> Portfolio Adapter (Alpha Factory upgrade Phase 5).

Turns ONE period's cross-sectional alpha signal (instrument -> score) into a tradeable target book,
the first-class flow the audit found missing. Pipeline:

  clean (drop missing / stale)  ->  winsorize (k-MAD)  ->  neutralize (cross-sectional OLS on a factor,
  e.g. size)  ->  standardize (z-score)  ->  dollar-neutral weights scaled to a gross target with a
  per-name position cap  ->  cost (commission + slippage via the shared CostModel, PLUS a separate
  half-spread)  ->  capacity (participation vs ADV)  ->  report.

The report exposes every decision: target weights, gross/net, turnover (vs prev), cost_bps (with the
spread broken out), rejected signals + reason, constraint violations, and per-name capacity. The
cross-sectional math mirrors the engine's cs_winsorize / cs_residualize (Phase 1). RESEARCH / DRY-RUN
ONLY — it computes a target book, it never sizes or places an order. Pure-Python stdlib, deterministic.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from services.research.backtest_engine.costs import CostModel

_EPS = 1e-12


@dataclass(frozen=True)
class SignalAdapterConfig:
    winsorize_k: float = 3.0          # k-MAD outlier clip
    max_position: float = 0.1         # cap on |weight| per instrument
    gross_target: float = 1.0         # target sum |weight|
    dollar_neutral: bool = True       # demean so net ~ 0
    half_spread_bps: float = 0.0      # half bid-ask spread, charged SEPARATELY from slippage
    max_participation: float = 0.1    # capacity: max position / ADV
    target_aum: float = 1e7
    cost: CostModel = field(default_factory=CostModel)


def _winsorize(vals: Sequence[float], k: float) -> list:
    if len(vals) < 2:
        return list(vals)
    med = statistics.median(vals)
    mad = statistics.median([abs(v - med) for v in vals])
    if mad <= _EPS:
        return list(vals)
    lo, hi = med - k * 1.4826 * mad, med + k * 1.4826 * mad
    return [min(max(v, lo), hi) for v in vals]


def _zscore(vals: Sequence[float]) -> list:
    if len(vals) < 2:
        return [0.0] * len(vals)
    mu, sd = statistics.fmean(vals), statistics.pstdev(vals)
    return [0.0] * len(vals) if sd <= _EPS else [(v - mu) / sd for v in vals]


def _residualize(xs: Sequence[float], ys: Sequence[float]) -> list:
    """Cross-sectional OLS residual of x on y (mirrors alpha_expression.cs_residualize)."""
    if len(xs) < 2:
        return list(xs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    var_y = sum((y - my) ** 2 for y in ys)
    if var_y <= _EPS:
        return [x - mx for x in xs]
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var_y
    a = mx - beta * my
    return [x - (a + beta * y) for x, y in zip(xs, ys)]


def _empty(rejected: list) -> dict:
    return {"weights": {}, "gross": 0.0, "net": 0.0, "turnover": 0.0, "cost_bps": 0.0,
            "spread_cost_bps": 0.0, "rejected": rejected, "constraint_violations": [],
            "capacity": [], "n_positions": 0, "neutralized": False}


def signal_to_portfolio(signal: Mapping[str, Optional[float]], *,
                        config: Optional[SignalAdapterConfig] = None,
                        neutralize_by: Optional[Mapping[str, float]] = None,
                        adv_notional: Optional[Mapping[str, float]] = None,
                        prev_weights: Optional[Mapping[str, float]] = None,
                        stale_ids: Sequence[str] = ()) -> dict:
    """Map a cross-sectional signal to a target portfolio (see module docstring). Deterministic; never
    places an order. `neutralize_by` (e.g. size = log(ADV)) is regressed out cross-sectionally;
    `adv_notional` enables the capacity check; `prev_weights` enables turnover/cost vs the current book."""
    cfg = config or SignalAdapterConfig()
    stale = set(stale_ids)

    rejected: list = []
    surviving: dict = {}
    for k in sorted(signal):
        if k in stale:
            rejected.append({"id": k, "reason": "STALE"})
            continue
        v = signal[k]
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            rejected.append({"id": k, "reason": "MISSING"})
            continue
        surviving[k] = float(v)

    ids = sorted(surviving)
    if not ids:
        return _empty(rejected)

    vals = _winsorize([surviving[k] for k in ids], cfg.winsorize_k)

    neutralized = False
    if neutralize_by is not None:
        fac = [neutralize_by.get(k) for k in ids]
        if all(f is not None and isinstance(f, (int, float)) and math.isfinite(f) for f in fac):
            vals = _residualize(vals, [float(f) for f in fac])
            neutralized = True

    z = _zscore(vals)
    if cfg.dollar_neutral:
        m = statistics.fmean(z)
        z = [x - m for x in z]

    gross_raw = sum(abs(x) for x in z)
    raw = [x / gross_raw * cfg.gross_target for x in z] if gross_raw > _EPS else [0.0] * len(z)

    violations: list = []
    weights: dict = {}
    for k, wi in zip(ids, raw):
        if abs(wi) > cfg.max_position + 1e-12:
            violations.append({"id": k, "reason": "MAX_POSITION",
                               "weight": round(wi, 6), "cap": cfg.max_position})
            wi = math.copysign(cfg.max_position, wi)
        weights[k] = round(wi, 8)

    gross = sum(abs(v) for v in weights.values())
    net = sum(weights.values())

    prev = dict(prev_weights or {})
    turnover = sum(abs(weights.get(k, 0.0) - prev.get(k, 0.0)) for k in set(weights) | set(prev))

    aum = cfg.target_aum
    traded_notional = turnover * aum
    base_cost = cfg.cost.trade_cost(traded_notional)                       # commission + slippage
    spread_cost = traded_notional * (cfg.half_spread_bps / 10_000.0)       # separate half-spread
    cost_bps = ((base_cost + spread_cost) / aum * 10_000.0) if aum > 0 else 0.0
    spread_cost_bps = (spread_cost / aum * 10_000.0) if aum > 0 else 0.0

    capacity: list = []
    if adv_notional is not None:
        for k in ids:
            pos_notional = abs(weights.get(k, 0.0)) * aum
            adv = adv_notional.get(k, 0.0) or 0.0
            part = (pos_notional / adv) if adv > 0 else math.inf
            capacity.append({"id": k, "participation": round(part, 6) if math.isfinite(part) else part,
                             "capacity_ok": part <= cfg.max_participation})

    return {
        "weights": weights,
        "gross": round(gross, 8),
        "net": round(net, 8),
        "turnover": round(turnover, 8),
        "cost_bps": round(cost_bps, 6),
        "spread_cost_bps": round(spread_cost_bps, 6),
        "rejected": rejected,
        "constraint_violations": violations,
        "capacity": capacity,
        "n_positions": len(weights),
        "neutralized": neutralized,
    }
