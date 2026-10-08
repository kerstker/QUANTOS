"""Information-theoretic factor screening (mutual information).

IC measures only LINEAR factor->return dependence. A factor can be strongly informative
yet have ~0 linear correlation (e.g. a U-shaped relationship). Mutual information measures
ANY dependence: MI(X;Y) = sum p(x,y) log( p(x,y) / (p(x) p(y)) ), estimated from a quantile-
binned 2-D histogram (equal-frequency bins -> robust to scale/outliers). MI >= 0; 0 iff X,Y
independent. `normalized_mutual_information` rescales to [0,1] for comparability.

Pure-Python stdlib, deterministic (rank-based binning with a stable tie-break), fail-closed
(too few points for a stable histogram -> None, never a fabricated score).
"""
from __future__ import annotations

import math
from typing import Optional, Sequence


def _quantile_bins(vals: Sequence[float], bins: int) -> list:
    """Assign each value to an equal-frequency bin 0..bins-1 by its rank (stable tie-break)."""
    n = len(vals)
    order = sorted(range(n), key=lambda i: (vals[i], i))
    rank = [0] * n
    for r, i in enumerate(order):
        rank[i] = r
    return [min(bins - 1, int((rank[i] + 0.5) / n * bins)) for i in range(n)]


def entropy(vals: Sequence[float], *, bins: int = 8, base: float = 2.0) -> Optional[float]:
    """Shannon entropy of the quantile-binned values (bits by default). None if too thin."""
    n = len(vals)
    if bins < 2 or n < bins:
        return None
    counts: dict = {}
    for b in _quantile_bins(list(vals), bins):
        counts[b] = counts.get(b, 0) + 1
    return -sum((c / n) * math.log(c / n, base) for c in counts.values())


def mutual_information(x: Sequence[float], y: Sequence[float], *, bins: int = 8,
                       base: float = 2.0) -> Optional[float]:
    """Histogram MI between paired samples x,y (bits by default). Captures non-linear
    dependence a linear IC misses. None when too thin for a stable histogram (fail-closed)."""
    n = len(x)
    if n != len(y):
        raise ValueError("x and y must be the same length")
    if bins < 2 or n < bins * 2:
        return None
    bx = _quantile_bins(list(x), bins)
    by = _quantile_bins(list(y), bins)
    joint: dict = {}
    mx: dict = {}
    my: dict = {}
    for a, b in zip(bx, by):
        joint[(a, b)] = joint.get((a, b), 0) + 1
        mx[a] = mx.get(a, 0) + 1
        my[b] = my.get(b, 0) + 1
    mi = 0.0
    for (a, b), c in joint.items():
        pxy = c / n
        mi += pxy * math.log(pxy / ((mx[a] / n) * (my[b] / n)), base)
    return max(0.0, mi)                       # MI >= 0; clamp tiny negative FP noise


def normalized_mutual_information(x: Sequence[float], y: Sequence[float], *,
                                  bins: int = 8) -> Optional[float]:
    """MI / min(H(x), H(y)) in [0,1]: 0 = independent, 1 = one fully determines the other.
    None when MI/entropy cannot be honestly estimated."""
    mi = mutual_information(x, y, bins=bins)
    if mi is None:
        return None
    hx = entropy(x, bins=bins)
    hy = entropy(y, bins=bins)
    m = min(hx, hy) if (hx is not None and hy is not None) else 0.0
    return round(min(1.0, mi / m), 4) if m > 0 else 0.0
