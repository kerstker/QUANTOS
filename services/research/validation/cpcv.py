"""Combinatorial Purged Cross-Validation (CPCV, Lopez de Prado) — OOS distribution, not a scalar.

`pbo_cscv` gives ONE probability-of-overfit number. CPCV instead produces a DISTRIBUTION of out-of-
sample outcomes: partition the timeline into N contiguous groups; for every combination of k groups
held out as TEST, select the best candidate on the remaining TRAIN groups (purged + embargoed around
each test boundary so overlapping-label points cannot leak), then score that selected candidate on the
TEST groups. Over all C(N,k) splits this yields a distribution of OOS Sharpes that exposes selection-
overfit far more richly than a single PBO scalar — a fragile edge shows up as a wide, left-shifted,
often-negative distribution even when its full-sample Sharpe looks fine.

Pure-Python stdlib, deterministic (no RNG, no wall-clock), fail-closed (a series too short to form
N groups of >=2, or with no scorable candidate, returns None rather than a fabricated distribution).
"""
from __future__ import annotations

import itertools
import math
import statistics
from typing import Optional, Sequence

_MAX_SPLITS = 5000          # C(N,k) guard against combinatorial blow-up


def _groups(t: int, n_groups: int):
    """Contiguous, near-equal index ranges [(lo, hi), ...] covering [0, t)."""
    base, rem = divmod(t, n_groups)
    out, lo = [], 0
    for g in range(n_groups):
        hi = lo + base + (1 if g < rem else 0)
        out.append((lo, hi))
        lo = hi
    return out


def _sharpe(xs: Sequence[float]) -> Optional[float]:
    vals = [x for x in xs if x is not None and math.isfinite(x)]
    if len(vals) < 2:
        return None
    sd = statistics.pstdev(vals)
    if sd <= 0:
        m = statistics.fmean(vals)
        return math.inf if m > 0 else (-math.inf if m < 0 else 0.0)
    return statistics.fmean(vals) / sd


def _quantile(sorted_vals, q: float) -> float:
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def cpcv_oos_distribution(returns_by_candidate, *, n_groups: int = 6, k_test: int = 2,
                          embargo: int = 0):
    """Return (oos_sharpes, selected_names): for each C(n_groups, k_test) split, the OOS Sharpe of the
    candidate selected by best TRAIN Sharpe (train purged+embargoed around the test groups), and which
    candidate that was. None if the data is too short / nothing is scorable (fail-closed)."""
    if isinstance(returns_by_candidate, dict):
        names = list(returns_by_candidate.keys())
        series = [list(returns_by_candidate[k]) for k in names]
    else:
        series = [list(s) for s in returns_by_candidate]
        names = list(range(len(series)))
    if not series:
        raise ValueError("need at least one candidate")
    t = len(series[0])
    if any(len(s) != t for s in series):
        raise ValueError("all candidate series must be the same length (aligned)")
    if not (2 <= n_groups) or not (1 <= k_test < n_groups):
        raise ValueError("require n_groups >= 2 and 1 <= k_test < n_groups")
    if embargo < 0:
        raise ValueError("embargo must be >= 0")
    if math.comb(n_groups, k_test) > _MAX_SPLITS:
        raise ValueError(f"C({n_groups},{k_test}) exceeds split limit {_MAX_SPLITS}; coarsen groups")
    if t < n_groups * 2:
        return None                                      # too short to form N groups of >= 2

    groups = _groups(t, n_groups)
    oos_sharpes: list = []
    selected: list = []
    for test_g in itertools.combinations(range(n_groups), k_test):
        test_idx = set()
        purge = set()
        for g in test_g:
            lo, hi = groups[g]
            test_idx.update(range(lo, hi))
            purge.update(range(max(0, lo - embargo), lo))      # embargo before the test block
            purge.update(range(hi, min(t, hi + embargo)))      # and after it
        train_idx = [i for i in range(t) if i not in test_idx and i not in purge]
        if len(train_idx) < 2:
            continue
        best_c, best_s = None, None
        for ci, s in enumerate(series):
            shp = _sharpe([s[i] for i in train_idx])
            if shp is None:
                continue
            if best_s is None or shp > best_s:
                best_s, best_c = shp, ci
        if best_c is None:
            continue
        oos = _sharpe([series[best_c][i] for i in sorted(test_idx)])
        if oos is None:
            continue
        oos_sharpes.append(oos)
        selected.append(names[best_c])
    if not oos_sharpes:
        return None
    return oos_sharpes, selected


def cpcv_summary(returns_by_candidate, *, n_groups: int = 6, k_test: int = 2,
                 embargo: int = 0) -> Optional[dict]:
    """Summary statistics of the CPCV OOS-Sharpe distribution. None (fail-closed) when not assessable.
    `frac_positive` and `selected_counts` count every split; mean/median/std/quantiles use the finite
    Sharpes (a zero-variance positive test block scores +inf and is excluded from the moments only)."""
    res = cpcv_oos_distribution(returns_by_candidate, n_groups=n_groups, k_test=k_test, embargo=embargo)
    if res is None:
        return None
    sharpes, selected = res
    finite = sorted(x for x in sharpes if math.isfinite(x))
    if not finite:
        return None
    counts: dict = {}
    for nm in selected:
        counts[nm] = counts.get(nm, 0) + 1
    return {
        "n_splits": len(sharpes),
        "mean_oos_sharpe": round(statistics.fmean(finite), 6),
        "median_oos_sharpe": round(statistics.median(finite), 6),
        "std_oos_sharpe": round(statistics.pstdev(finite), 6) if len(finite) > 1 else 0.0,
        "frac_positive": round(sum(1 for x in sharpes if x > 0) / len(sharpes), 6),
        "p05_oos_sharpe": round(_quantile(finite, 0.05), 6),
        "p95_oos_sharpe": round(_quantile(finite, 0.95), 6),
        "min_oos_sharpe": round(finite[0], 6),
        "selected_counts": counts,
    }
