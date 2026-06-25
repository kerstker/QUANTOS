"""Real out-of-sample walk-forward consistency (audit P2 fix).

The evaluator previously set walk_forward_passed = (full-sample net_sharpe > 0),
which a single lucky sub-period could pass. This partitions the net-return SERIES
into sequential held-out folds and passes only if a sufficient fraction of the
folds clear a positive Sharpe — so an edge concentrated in one period no longer
masquerades as a robust out-of-sample result. Deterministic, fail-closed (a
too-thin series cannot be assessed and returns False).
"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence


def _block_sharpe(block: Sequence[float]) -> float:
    if len(block) < 2:
        return 0.0
    sd = statistics.pstdev(block)
    if sd > 0:
        return statistics.fmean(block) / sd
    # zero-variance block: a constant POSITIVE block is a perfect (infinite-Sharpe)
    # edge, not "no edge" — score by the sign of the mean (review: false-negative fix)
    mean = statistics.fmean(block)
    return math.inf if mean > 0 else (-math.inf if mean < 0 else 0.0)


def walk_forward_oos(returns: Sequence[Optional[float]], *, n_folds: int = 4,
                     min_positive_frac: float = 0.5) -> bool:
    s = [float(r) for r in returns if r is not None and math.isfinite(r)]
    if n_folds < 2 or len(s) < n_folds * 2:
        return False                                  # too thin / degenerate -> fail closed
    fold = len(s) // n_folds
    positives = 0
    for k in range(n_folds):
        lo = k * fold
        hi = (k + 1) * fold if k < n_folds - 1 else len(s)
        if _block_sharpe(s[lo:hi]) > 0.0:
            positives += 1
    return positives / n_folds >= min_positive_frac
