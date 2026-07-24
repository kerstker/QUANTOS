"""Signal -> Portfolio Adapter (Alpha Factory upgrade Phase 5).

Turns ONE period's cross-sectional alpha signal (instrument -> score) into a tradeable target book,
the first-class flow the audit found missing. Pipeline:

  clean (drop missing / stale / wrong-type)  ->  winsorize (k-MAD)  ->  neutralize (cross-sectional OLS
  on a factor, e.g. size; FAIL-CLOSED: a name lacking the factor is rejected, never silently
  un-neutralized)  ->  standardize (z-score)  ->  weights projected onto {dollar-neutral} ∩ {gross
  target} ∩ {|w| <= position cap} by iterative projection  ->  cost (commission + slippage via the
  shared CostModel, PLUS a separate half-spread)  ->  capacity (participation vs ADV)  ->  report.

The report exposes every decision: target weights, gross/net, turnover (vs prev), cost_bps (with the
spread broken out), rejected signals + reason, constraint violations (names that BIND the cap), and
per-name capacity (UNKNOWN when ADV is absent). Degenerate inputs (a single survivor, or a constant
cross-section with no differentiation) yield an explicit EMPTY book rather than silent zero weights.

The cross-sectional math mirrors the engine's cs_winsorize / cs_residualize (Phase 1). RESEARCH /
DRY-RUN ONLY — it computes a target book, it never sizes or places an order. Pure-Python stdlib,
deterministic (no RNG, no wall-clock).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, replace
from typing import Mapping, Optional, Sequence

from quantos.contracts.cost import CostModel

from .portfolio_constraints import (
    PortfolioConstraintInputs,
    enforce_portfolio_constraints,
)

_EPS = 1e-12


@dataclass(frozen=True)
class SignalAdapterConfig:
    winsorize_k: float = 3.0          # k-MAD outlier clip
    max_position: float = 0.1         # cap on |weight| per instrument
    gross_target: float = 1.0         # target sum |weight|
    dollar_neutral: bool = True       # project net -> 0
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


def _project_constraints(z: Sequence[float], *, max_position: float, gross_target: float,
                         dollar_neutral: bool, iters: int = 100) -> list:
    """Project a raw score vector onto {net=0 if dollar_neutral} ∩ {sum|w|=gross_target} ∩
    {|w|<=max_position} by iterating demean -> scale-to-gross -> clip to a fixed point. At a feasible
    fixed point all three hold exactly; when the cap is too tight for all three at once, it settles at
    the closest feasible book (the caller reports the actual net/gross). Deterministic."""
    w = list(z)
    for _ in range(iters):
        prev = list(w)
        if dollar_neutral:
            m = statistics.fmean(w)
            w = [x - m for x in w]
        g = sum(abs(x) for x in w)
        if g > _EPS:
            w = [x / g * gross_target for x in w]
        w = [math.copysign(max_position, x) if abs(x) > max_position + 1e-12 else x for x in w]
        if all(abs(a - b) < 1e-13 for a, b in zip(w, prev)):
            break
    return w


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _empty(rejected: list) -> dict:
    return {"weights": {}, "gross": 0.0, "net": 0.0, "turnover": 0.0, "cost_bps": 0.0,
            "spread_cost_bps": 0.0, "rejected": rejected, "constraint_violations": [],
            "capacity": [], "n_positions": 0, "neutralized": False}


def signal_to_portfolio(signal: Mapping[str, Optional[float]], *,
                        config: Optional[SignalAdapterConfig] = None,
                        neutralize_by: Optional[Mapping[str, float]] = None,
                        adv_notional: Optional[Mapping[str, float]] = None,
                        prev_weights: Optional[Mapping[str, float]] = None,
                        stale_ids: Sequence[str] = (),
                        portfolio_constraints: Optional[PortfolioConstraintInputs] = None) -> dict:
    """Map a cross-sectional signal to a target portfolio (see module docstring). Deterministic; never
    places an order."""
    cfg = config or SignalAdapterConfig()
    stale = set(stale_ids)

    rejected: list = []
    surviving: dict = {}
    for k in sorted(signal):
        if k in stale:
            rejected.append({"id": k, "reason": "STALE"})
            continue
        v = signal[k]
        if isinstance(v, bool):
            rejected.append({"id": k, "reason": "INVALID_TYPE"})
            continue
        if not _is_number(v):
            rejected.append({"id": k, "reason": "MISSING"})
            continue
        surviving[k] = float(v)

    ids = sorted(surviving)

    # fail-closed neutralization: a name lacking a finite factor is REJECTED (MISSING_FACTOR), not left
    # silently un-neutralized; the remainder is neutralized against the factor.
    neutralize_vals = None
    if neutralize_by is not None:
        kept = []
        for k in ids:
            if _is_number(neutralize_by.get(k)):
                kept.append(k)
            else:
                rejected.append({"id": k, "reason": "MISSING_FACTOR"})
        ids = kept
        neutralize_vals = [float(neutralize_by[k]) for k in ids]

    if not ids:
        # An explicitly requested gate must see the empty target and reject it
        # (rather than allowing an empty book to masquerade as a valid result).
        enforce_portfolio_constraints({}, portfolio_constraints)
        return _empty(rejected)

    vals = _winsorize([surviving[k] for k in ids], cfg.winsorize_k)

    neutralized = False
    if neutralize_vals is not None and len(ids) >= 2:
        vals = _residualize(vals, neutralize_vals)
        neutralized = True

    z = _zscore(vals)
    if cfg.dollar_neutral:
        z = [x - statistics.fmean(z) for x in z]

    # raw (uncapped) target -> identifies the names that BIND the position cap
    gross_raw = sum(abs(x) for x in z)
    raw = [x / gross_raw * cfg.gross_target for x in z] if gross_raw > _EPS else [0.0] * len(ids)
    violations = [{"id": k, "reason": "MAX_POSITION", "weight": round(wi, 6), "cap": cfg.max_position}
                  for k, wi in zip(ids, raw) if abs(wi) > cfg.max_position + 1e-12]

    projected = _project_constraints(z, max_position=cfg.max_position, gross_target=cfg.gross_target,
                                     dollar_neutral=cfg.dollar_neutral)
    weights = {k: round(wi, 8) for k, wi in zip(ids, projected) if abs(wi) > _EPS}

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
            if k not in adv_notional:
                capacity.append({"id": k, "participation": None, "capacity_ok": None})  # UNKNOWN
                continue
            pos_notional = abs(weights.get(k, 0.0)) * aum
            adv = adv_notional.get(k) or 0.0
            part = (pos_notional / adv) if adv > 0 else math.inf
            capacity.append({"id": k, "participation": round(part, 6) if math.isfinite(part) else part,
                             "capacity_ok": part <= cfg.max_participation})

    # Capacity evidence is naturally available at this adapter boundary.  If
    # the caller requested the unified capacity check but did not precompute
    # position notionals, derive them from the final weights and configured AUM
    # without inventing missing ADV data.
    constraint_result = None
    if portfolio_constraints is not None:
        effective_constraints = portfolio_constraints
        if (
            portfolio_constraints.max_capacity_usage is not None
            and portfolio_constraints.position_notional_by_symbol is None
            and portfolio_constraints.capacity_by_symbol is None
            and adv_notional is not None
        ):
            effective_constraints = replace(
                portfolio_constraints,
                position_notional_by_symbol={
                    symbol: abs(weight) * float(aum)
                    for symbol, weight in weights.items()
                },
                capacity_by_symbol=dict(adv_notional),
            )
        constraint_result = enforce_portfolio_constraints(weights, effective_constraints)

    result = {
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
    if constraint_result is not None:
        result["portfolio_constraints"] = constraint_result.as_dict()
    return result
