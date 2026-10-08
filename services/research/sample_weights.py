"""Uniqueness sample weighting for overlapping labels (Lopez de Prado).

When sample label windows overlap, the samples are not IID — a period covered by k labels is
"shared" k ways. Each sample's weight is its average uniqueness = mean over its window of
1/concurrency(t), where concurrency(t) = how many label windows cover period t. Down-weighting
overlap stops a model from over-counting redundant samples. Pure-Python, deterministic,
fail-closed (a degenerate span end<start gets weight 0).
"""
from __future__ import annotations

from typing import Sequence, Tuple


def uniqueness_weights(spans: Sequence[Tuple[int, int]]) -> list:
    """spans: list of (start, end) inclusive integer label windows. Returns each sample's
    average-uniqueness weight in (0,1] (0 for a degenerate end<start)."""
    concurrency: dict = {}
    for s, e in spans:
        if e < s:
            continue
        for t in range(s, e + 1):
            concurrency[t] = concurrency.get(t, 0) + 1
    out = []
    for s, e in spans:
        if e < s:
            out.append(0.0)
            continue
        u = sum(1.0 / concurrency[t] for t in range(s, e + 1)) / (e - s + 1)
        out.append(round(u, 6))
    return out
