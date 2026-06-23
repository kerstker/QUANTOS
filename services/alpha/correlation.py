"""Pairwise alpha correlation (Phase 6, step 6a).

MIN_OVERLAP-gated: if two return series share fewer than min_overlap common
points the correlation is None (fail-closed, never fabricated). Deterministic:
series are aligned by sorted index position; ties broken by alpha_id order.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional


def _pearson(a: list[float], b: list[float]) -> Optional[float]:
    n = len(a)
    if n < 2:
        return None
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va > 0 and vb > 0 else None


def pairwise_correlation(
    return_series: dict[str, tuple[float, ...]],
    min_overlap: int = 60,
) -> dict[tuple[str, str], Optional[float]]:
    """Upper-triangle correlation matrix, keyed by (sorted alpha_id pair)."""
    ids = sorted(return_series)
    result: dict[tuple[str, str], Optional[float]] = {}
    for i, aid in enumerate(ids):
        for bid in ids[i + 1:]:
            a, b = return_series[aid], return_series[bid]
            overlap = min(len(a), len(b))
            if overlap < min_overlap:
                result[(aid, bid)] = None
            else:
                # align from the end (most recent in common)
                a2, b2 = list(a[-overlap:]), list(b[-overlap:])
                result[(aid, bid)] = _pearson(a2, b2)
    return result


def max_abs_corr_to_set(
    candidate_id: str,
    candidate_returns: tuple[float, ...],
    approved_set: dict[str, tuple[float, ...]],
    min_overlap: int = 60,
) -> Optional[float]:
    """Max |corr| of a candidate against the approved set. None if no overlap."""
    corrs = []
    for aid, rets in approved_set.items():
        overlap = min(len(candidate_returns), len(rets))
        if overlap < min_overlap:
            continue
        c = _pearson(list(candidate_returns[-overlap:]), list(rets[-overlap:]))
        if c is not None:
            corrs.append(abs(c))
    return max(corrs) if corrs else None
