"""No-peek walk-forward selection out-of-sample test (Phase 5 extension).

The danger that a single full-sample Sharpe (or even a Deflated Sharpe) cannot
catch is SELECTION overfit: when you search many factors and report the best, the
winner is partly luck. The honest test is to re-select on each TRAIN window and
measure the *selected* factor on the immediately-following, never-seen TEST window.
If the in-sample-selected edge persists OOS it is real; if it collapses, the
in-sample number was a selection illusion.

Pure / deterministic: operates on pre-computed per-period net return series (one
per candidate factor, all aligned to the same period index). Reuses the existing
walk-forward splitter and Sharpe. Fail-closed: bad input raises; no usable window
yields zero folds and None aggregates (never a fabricated pass).
"""
from __future__ import annotations

import statistics
from typing import Mapping, Optional, Sequence

from services.research.backtest_engine.metrics import sharpe
from services.research.validation.walk_forward import walk_forward_splits


def _slice(series: Sequence[float], idx: Sequence[int]) -> list[float]:
    return [series[j] for j in idx]


def _combined_slice(factor_returns: Mapping[str, Sequence[float]],
                    selected: Sequence[str], idx: Sequence[int]) -> list[float]:
    """Equal-weight blend of the selected factors' returns over the given periods."""
    return [statistics.fmean([factor_returns[i][j] for i in selected]) for j in idx]


def full_sample_best(factor_returns: Mapping[str, Sequence[float]]) -> tuple[str, float]:
    """The naive in-sample illusion: the single factor with the best Sharpe over the
    WHOLE sample (this is the number selection overfit inflates)."""
    if not factor_returns:
        raise ValueError("factor_returns must be non-empty")
    scored = sorted(((i, sharpe(list(s))) for i, s in factor_returns.items()),
                    key=lambda kv: (-kv[1], kv[0]))
    return scored[0]


def walk_forward_selection_oos(
    factor_returns: Mapping[str, Sequence[float]], *,
    train_size: int, test_size: int, step: Optional[int] = None,
    embargo: int = 0, top_k: int = 1,
) -> dict:
    """Re-rank candidates by Sharpe on each TRAIN window (minus an `embargo` band
    adjacent to the test window — the train periods whose forward return overlaps
    the test), select the top_k, then measure their equal-weight Sharpe on the
    held-out TEST window. Returns per-fold IS vs OOS Sharpe and the aggregate
    overfit haircut (mean OOS − mean IS).

    Raises on < 2 candidates, ragged series, or degenerate sizes (fail-closed).
    A window whose train shrinks below 2 usable periods (e.g. a big embargo) is
    skipped; if none survive, n_folds == 0 and aggregates are None."""
    ids = sorted(factor_returns)
    if len(ids) < 2:
        raise ValueError("need >= 2 candidate factors to test selection")
    lengths = {len(factor_returns[i]) for i in ids}
    if len(lengths) != 1:
        raise ValueError("all factor return series must have equal length")
    if train_size < 2 or test_size < 1 or embargo < 0 or top_k < 1:
        raise ValueError("require train_size>=2, test_size>=1, embargo>=0, top_k>=1")
    n = lengths.pop()

    folds = []
    for window, (train, test) in enumerate(walk_forward_splits(n, train_size, test_size, step)):
        tr = train[:-embargo] if (embargo and embargo < len(train)) else (train if embargo == 0 else [])
        if len(tr) < 2:
            continue                                   # un-assessable window -> skip (fail-closed)
        scored = sorted(((i, sharpe(_slice(factor_returns[i], tr))) for i in ids),
                        key=lambda kv: (-kv[1], kv[0]))
        selected = [i for i, _ in scored[:top_k]]
        is_sharpe = sharpe(_combined_slice(factor_returns, selected, tr))
        oos_sharpe = sharpe(_combined_slice(factor_returns, selected, test))
        folds.append({
            "window": window,
            "train_range": [tr[0], tr[-1] + 1],
            "test_range": [test[0], test[-1] + 1],
            "selected": selected,
            "is_sharpe": round(is_sharpe, 4),
            "oos_sharpe": round(oos_sharpe, 4),
        })

    oos = [f["oos_sharpe"] for f in folds]
    is_ = [f["is_sharpe"] for f in folds]
    mean_oos = round(statistics.fmean(oos), 4) if oos else None
    mean_is = round(statistics.fmean(is_), 4) if is_ else None
    return {
        "method": "WALK_FORWARD_NO_PEEK_SELECTION_OOS",
        "n_periods": n, "train_size": train_size, "test_size": test_size,
        "step": step or test_size, "embargo": embargo, "top_k": top_k,
        "n_candidates": len(ids),
        "n_folds": len(folds),
        "folds": folds,
        "mean_is_sharpe": mean_is,
        "mean_oos_sharpe": mean_oos,
        "oos_minus_is": round(mean_oos - mean_is, 4) if (mean_oos is not None and mean_is is not None) else None,
        "oos_positive_frac": round(sum(1 for v in oos if v > 0) / len(oos), 4) if oos else None,
    }
