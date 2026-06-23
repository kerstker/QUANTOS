"""Cost-aware risk-budgeted portfolio optimizer (Phase 7, step 2).

Allocates a non-negative budget across the (ranked, de-duplicated) alpha streams.
Risk parity is the default — robust, inversion-free, and exactly the right tool
for combining diversified alpha sleeves (each alpha is internally dollar-neutral,
so the portfolio layer sizes RISK, not direction). Plus inverse-vol, a
concentration cap, and a no-trade band so we don't churn (and pay costs) on noise.
All pure-Python and deterministic.
"""
from __future__ import annotations

import math
from typing import Sequence

Matrix = list[list[float]]


def inverse_vol_weights(cov: Matrix) -> list[float]:
    """Weights proportional to 1/volatility (non-negative, sum to 1)."""
    n = len(cov)
    if n == 0:
        return []
    inv = [(1.0 / math.sqrt(cov[i][i]) if cov[i][i] > 0 else 0.0) for i in range(n)]
    s = sum(inv)
    return [x / s for x in inv] if s > 0 else [1.0 / n] * n


def risk_parity_weights(cov: Matrix, iterations: int = 1000) -> list[float]:
    """Equal-risk-contribution weights via a damped multiplicative fixed point
    (w_i proportional to 1/MRC_i at the solution). Non-negative, sum to 1."""
    n = len(cov)
    if n == 0:
        return []
    if n == 1:
        return [1.0]
    w = [1.0 / n] * n
    for _ in range(iterations):
        mrc = [sum(cov[i][j] * w[j] for j in range(n)) for i in range(n)]
        rc = [w[i] * mrc[i] for i in range(n)]
        total = sum(rc)
        if total <= 0:
            break
        target = total / n
        nxt = [(w[i] * math.sqrt(target / rc[i]) if rc[i] > 0 else w[i]) for i in range(n)]
        s = sum(nxt)
        if s <= 0:
            break
        w = [x / s for x in nxt]
    return w


def cap_weights(weights: Sequence[float], max_weight: float) -> list[float]:
    """Cap each weight at max_weight, redistributing the excess to uncapped names,
    keeping the sum at 1 (concentration limit)."""
    n = len(weights)
    if n == 0:
        return []
    if max_weight * n < 1.0 - 1e-9:
        raise ValueError("max_weight too small to sum to 1 across all names")
    w = list(weights)
    for _ in range(100):
        excess = 0.0
        capped = [False] * n
        for i in range(n):
            if w[i] > max_weight + 1e-15:
                excess += w[i] - max_weight
                w[i] = max_weight
                capped[i] = True
            elif abs(w[i] - max_weight) <= 1e-15:
                capped[i] = True
        if excess <= 1e-12:
            break
        room = sum(w[i] for i in range(n) if not capped[i])
        if room <= 0:
            break
        for i in range(n):
            if not capped[i]:
                w[i] += excess * (w[i] / room)
    return w


def cost_aware_rebalance(
    prev_weights: dict[str, float],
    target_weights: dict[str, float],
    no_trade_band: float = 0.0,
) -> tuple[dict[str, float], float]:
    """Only move a name when |target - prev| exceeds the no-trade band, then
    renormalize. Returns (new_weights, turnover). Suppresses cost-churn on noise."""
    names = sorted(set(prev_weights) | set(target_weights))
    moved = {}
    for nm in names:
        p, t = prev_weights.get(nm, 0.0), target_weights.get(nm, 0.0)
        moved[nm] = t if abs(t - p) > no_trade_band else p
    s = sum(moved.values())
    new = {k: v / s for k, v in moved.items()} if s > 0 else moved
    turnover = sum(abs(new.get(nm, 0.0) - prev_weights.get(nm, 0.0)) for nm in names)
    return new, turnover
