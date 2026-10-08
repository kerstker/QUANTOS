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
                     min_positive_frac: float = 0.5, embargo: int = 0) -> bool:
    """Sequential out-of-sample fold consistency. With `embargo` > 0, the first `embargo`
    points of every fold AFTER the first are dropped before scoring (purged/embargoed CV):
    when forward-return labels of horizon h overlap, a fold's opening points share their
    label window with the prior fold, so they are not independent — set embargo = h-1 to
    remove that contamination so the per-fold Sharpe reflects genuinely held-out periods.
    embargo=0 is the original behavior. Deterministic, fail-closed (a fold thinned below 2
    points by the embargo cannot be assessed and does not count as positive)."""
    s = [float(r) for r in returns if r is not None and math.isfinite(r)]
    if n_folds < 2 or len(s) < n_folds * 2 or embargo < 0:
        return False                                  # too thin / degenerate -> fail closed
    fold = len(s) // n_folds
    positives = 0
    for k in range(n_folds):
        lo = k * fold + (embargo if k > 0 else 0)     # purge overlap with the prior fold
        hi = (k + 1) * fold if k < n_folds - 1 else len(s)
        block = s[lo:hi]
        if len(block) >= 2 and _block_sharpe(block) > 0.0:
            positives += 1
    return positives / n_folds >= min_positive_frac
