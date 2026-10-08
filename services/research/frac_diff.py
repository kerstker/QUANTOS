"""Fractional differentiation (Lopez de Prado, fixed-width window).

Ordinary first differencing (d=1) makes a price series stationary but destroys its memory;
the raw level (d=0) keeps memory but is non-stationary. A fractional order d in (0,1) keeps
just enough memory to be predictive while gaining stationarity. The differencing weights come
from the binomial expansion w_0=1, w_k = -w_{k-1}*(d-k+1)/k, truncated where |w_k| < thresh.
Each output point is a BACKWARD weighted sum of past levels (PIT). Pure, deterministic.
"""
from __future__ import annotations

from typing import Sequence


def ffd_weights(d: float, *, thresh: float = 1e-4, max_k: int = 10000) -> list:
    """Fixed-width fractional-difference weights (w[0]=1, decaying), truncated at |w|<thresh."""
    if d < 0:
        raise ValueError("d must be >= 0")
    w = [1.0]
    k = 1
    while k <= max_k:
        wk = -w[-1] * (d - k + 1) / k
        if abs(wk) < thresh:
            break
        w.append(wk)
        k += 1
    return w


def frac_diff(series: Sequence[float], d: float, *, thresh: float = 1e-4) -> list:
    """Fractionally difference `series` by order d. d=0 -> identity; d=1 -> first difference.
    Output is shorter than the input by the weight-window warm-up (len(weights)-1)."""
    if d == 0:
        return [float(x) for x in series]
    w = ffd_weights(d, thresh=thresh)
    width = len(w)
    out = []
    for i in range(width - 1, len(series)):
        out.append(round(sum(w[j] * series[i - j] for j in range(width)), 8))
    return out
