"""Factor timing — scale exposure to a factor by its OWN recent health.

A factor decays and revives; rather than a constant bet, weight it by its trailing Sharpe.
weight[t] uses only the factor's returns STRICTLY before t (PIT), is 0 until there is enough
history, and is 0 whenever the trailing health is <= 0 (do not keep betting a losing factor).
A positive trailing Sharpe maps to (0,1] (capped). Pure-Python, deterministic, fail-closed.
"""
from __future__ import annotations

import statistics
from typing import Sequence


def factor_timing_weights(factor_returns: Sequence[float], *, lookback: int = 20,
                          cap: float = 2.0) -> list:
    """Return a weight in [0,1] per period; weight[t] = f(trailing Sharpe over
    factor_returns[t-lookback:t]). 0 during warm-up or when the factor is unhealthy."""
    if lookback < 2 or cap <= 0:
        raise ValueError("lookback>=2 and cap>0 required")
    n = len(factor_returns)
    out = [0.0] * n
    for t in range(n):
        lo = t - lookback
        if lo < 0:
            continue
        window = list(factor_returns[lo:t])
        if len(window) < 2:
            continue
        sd = statistics.pstdev(window)
        mean = statistics.fmean(window)
        if sd <= 0:
            out[t] = 1.0 if mean > 0 else 0.0      # zero-variance: bet iff positive
            continue
        sharpe = mean / sd
        out[t] = round(min(cap, sharpe) / cap, 6) if sharpe > 0 else 0.0
    return out
