"""Domain-neutral multiple-testing corrections: Deflated Sharpe Ratio, expected
maximum Sharpe under N trials, minimum track-record length, and Probability of
Backtest Overfitting (CSCV).

`var_sr` (the variance of the Sharpe estimates across the trials) is REQUIRED —
there is no default, because a wrong default silently deflates every strategy
(review finding). Typical values are small (e.g. ~0.01); supply your own estimate.
`observed_sr` is the sample Sharpe (point estimate) computed from `n_obs`
observations; the sqrt(n_obs-1) term is the estimation-error scaling.
"""
from __future__ import annotations

import itertools
import math
from statistics import NormalDist
from typing import Sequence

_N = NormalDist()
_EULER = 0.5772156649015329
_MAX_CSCV_BLOCKS = 16   # C(16,8)=12870 combinations; refuse larger to avoid blow-up


def _check_moments(skew: float, kurt: float) -> None:
    if kurt < 1.0:
        raise ValueError(f"kurt={kurt} implies excess kurtosis < -2 (statistically impossible)")
    if not math.isfinite(skew) or abs(skew) > 1e3:
        raise ValueError(f"skew={skew} is out of a sane range")


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """E[max Sharpe] across `n_trials` independent trials (López de Prado)."""
    if var_sr <= 0:
        raise ValueError("var_sr must be > 0")
    if n_trials < 2:
        return 0.0
    e1 = _N.inv_cdf(1.0 - 1.0 / n_trials)
    e2 = _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(var_sr) * ((1.0 - _EULER) * e1 + _EULER * e2)


def deflated_sharpe_ratio(observed_sr: float, n_obs: int, n_trials: int, var_sr: float,
                          skew: float = 0.0, kurt: float = 3.0) -> float:
    """Probability that the true Sharpe exceeds the multiple-testing-adjusted
    benchmark. Higher is better; decreases with more trials and fewer obs."""
    if n_obs < 2:
        return 0.0
    _check_moments(skew, kurt)
    sr0 = expected_max_sharpe(n_trials, var_sr)
    denom = 1.0 - skew * observed_sr + ((kurt - 1.0) / 4.0) * observed_sr ** 2
    if denom <= 0:
        raise ValueError(f"non-positive variance term (denom={denom:.4g}); skew/kurt too extreme for this SR")
    z = (observed_sr - sr0) * math.sqrt(n_obs - 1) / math.sqrt(denom)
    return _N.cdf(z)


def min_track_record_length(observed_sr: float, target_sr: float,
                            skew: float = 0.0, kurt: float = 3.0, confidence: float = 0.95) -> float:
    """Minimum number of observations needed to assert observed_sr > target_sr
    at `confidence`. Infinite if there is no edge."""
    _check_moments(skew, kurt)
    if not (0.0 < confidence < 1.0):
        raise ValueError(f"confidence={confidence} must be strictly between 0 and 1")
    if observed_sr <= target_sr:
        return math.inf
    z = _N.inv_cdf(confidence)
    var_term = 1.0 - skew * observed_sr + ((kurt - 1.0) / 4.0) * observed_sr ** 2
    if var_term <= 0:
        raise ValueError(f"non-positive variance term (var_term={var_term:.4g}); skew/kurt too extreme for this SR")
    return 1.0 + var_term * (z / (observed_sr - target_sr)) ** 2


def pbo_cscv(perf_matrix: Sequence[Sequence[float]]) -> float:
    """Probability of Backtest Overfitting via Combinatorially Symmetric CV.

    perf_matrix: rows = time blocks, cols = strategy configs. Returns the
    fraction of IS/OOS splits where the in-sample-best config lands at or below
    the OOS median (logit <= 0, Bailey et al. 2015) using mid-ranks for ties.
    High PBO => the selection is overfit.
    """
    n_blocks = len(perf_matrix)
    if n_blocks < 2:
        return 0.0
    if n_blocks > _MAX_CSCV_BLOCKS:
        raise ValueError(f"n_blocks={n_blocks} exceeds CSCV limit {_MAX_CSCV_BLOCKS}; coarsen the blocks")
    n_strats = len(perf_matrix[0])
    if n_strats < 2:
        return 0.0
    for row in perf_matrix:
        if len(row) != n_strats:
            raise ValueError("perf_matrix must be rectangular (all rows same length)")
        if any(not math.isfinite(x) for x in row):
            raise ValueError("perf_matrix contains NaN or inf")

    idx = list(range(n_blocks))
    half = n_blocks // 2
    lambdas = []
    for is_blocks in itertools.combinations(idx, half):
        oos_blocks = [i for i in idx if i not in is_blocks]
        is_perf = [sum(perf_matrix[b][s] for b in is_blocks) for s in range(n_strats)]
        oos_perf = [sum(perf_matrix[b][s] for b in oos_blocks) for s in range(n_strats)]
        best = max(range(n_strats), key=lambda s: is_perf[s])
        best_oos = oos_perf[best]
        less = sum(1 for s in range(n_strats) if oos_perf[s] < best_oos)
        equal = sum(1 for s in range(n_strats) if oos_perf[s] == best_oos)
        mid_rank = less + 0.5 * (equal + 1)
        omega = mid_rank / (n_strats + 1)
        lambdas.append(math.log(omega / (1.0 - omega)))
    return sum(1 for logit in lambdas if logit <= 0.0) / len(lambdas)
