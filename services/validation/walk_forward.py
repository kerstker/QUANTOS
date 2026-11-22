"""Walk-forward and purged/embargoed time-series validation splits."""

from __future__ import annotations

from typing import Optional


def walk_forward_splits(
    n: int,
    train_size: int,
    test_size: int,
    step: Optional[int] = None,
    embargo: int = 0,
) -> list[tuple[list[int], list[int]]]:
    """Return sequential windows whose test range strictly follows training (with optional embargo)."""
    if train_size < 1 or test_size < 1:
        raise ValueError("train_size and test_size must be >= 1")
    if embargo < 0:
        raise ValueError("embargo must be >= 0")
    step = test_size if step is None else step
    if step < 1:
        raise ValueError("step must be >= 1")
    splits = []
    start = 0
    while start + train_size + embargo + test_size <= n:
        train = list(range(start, start + train_size))
        test_start = start + train_size + embargo
        test = list(range(test_start, test_start + test_size))
        splits.append((train, test))
        start += step
    return splits


def purged_kfold(
    n: int,
    k: int,
    embargo: int = 0,
) -> list[tuple[list[int], list[int]]]:
    """Return contiguous test folds with a purged embargo band around each fold."""
    if k < 2 or k > n:
        raise ValueError("require 2 <= k <= n")
    if embargo < 0:
        raise ValueError("embargo must be >= 0")
    fold = n // k
    out = []
    for i in range(k):
        test_start = i * fold
        test_end = n if i == k - 1 else (i + 1) * fold
        test = list(range(test_start, test_end))
        lo = max(0, test_start - embargo)
        hi = min(n, test_end + embargo)
        train = [j for j in range(n) if j < lo or j >= hi]
        out.append((train, test))
    return out


__all__ = ["purged_kfold", "walk_forward_splits"]
