"""Multiple-testing corrections (Phase 5): Deflated Sharpe Ratio, expected
maximum Sharpe under N trials, minimum track-record length, and Probability of
Backtest Overfitting (CSCV).

These are what stop a cherry-picked "Sharpe 2.0" — selected from hundreds of
trials — from being mistaken for real skill. `var_sr` (the variance of the
Sharpe estimates across trials) is a caller-supplied estimate; the functions
are exercised for their key properties (monotonicity in trials) in tests.
"""
from __future__ import annotations

import itertools
import math
from statistics import NormalDist
from typing import Sequence

_N = NormalDist()
_EULER = 0.5772156649015329


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """E[max Sharpe] across `n_trials` independent trials (López de Prado)."""
    if n_trials < 2 or var_sr <= 0:
        return 0.0
    e1 = _N.inv_cdf(1.0 - 1.0 / n_trials)
    e2 = _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(var_sr) * ((1.0 - _EULER) * e1 + _EULER * e2)


def deflated_sharpe_ratio(observed_sr: float, n_obs: int, n_trials: int,
                          var_sr: float = 1.0, skew: float = 0.0, kurt: float = 3.0) -> float:
    """Probability that the true (per-observation) Sharpe exceeds the
    multiple-testing-adjusted benchmark. Higher is better; decreases with more
    trials and fewer observations."""
    if n_obs < 2:
        return 0.0
    sr0 = expected_max_sharpe(n_trials, var_sr)
    denom = 1.0 - skew * observed_sr + ((kurt - 1.0) / 4.0) * observed_sr ** 2
    if denom <= 0:
        return 0.0
    z = (observed_sr - sr0) * math.sqrt(n_obs - 1) / math.sqrt(denom)
    return _N.cdf(z)


def min_track_record_length(observed_sr: float, target_sr: float, n_obs: int,
                            skew: float = 0.0, kurt: float = 3.0, confidence: float = 0.95) -> float:
    """Minimum observations needed to assert observed_sr > target_sr at `confidence`."""
    if observed_sr <= target_sr:
        return math.inf
    denom = (observed_sr - target_sr) ** 2
    z = _N.inv_cdf(confidence)
    return 1.0 + (1.0 - skew * observed_sr + ((kurt - 1.0) / 4.0) * observed_sr ** 2) * (z / math.sqrt(denom)) ** 2


def pbo_cscv(perf_matrix: Sequence[Sequence[float]]) -> float:
    """Probability of Backtest Overfitting via Combinatorially Symmetric CV.

    perf_matrix: rows = time blocks, cols = strategy configs; value = that
    config's performance in that block. Returns the fraction of IS/OOS splits in
    which the in-sample-best config lands below the OOS median (logit < 0). High
    PBO => the selection is overfit.
    """
    n_blocks = len(perf_matrix)
    if n_blocks < 2:
        return 0.0
    n_strats = len(perf_matrix[0])
    if n_strats < 2:
        return 0.0
    idx = list(range(n_blocks))
    half = n_blocks // 2
    lambdas = []
    for is_blocks in itertools.combinations(idx, half):
        oos_blocks = [i for i in idx if i not in is_blocks]
        is_perf = [sum(perf_matrix[b][s] for b in is_blocks) for s in range(n_strats)]
        oos_perf = [sum(perf_matrix[b][s] for b in oos_blocks) for s in range(n_strats)]
        best = max(range(n_strats), key=lambda s: is_perf[s])
        beaten = sum(1 for s in range(n_strats) if oos_perf[best] > oos_perf[s])
        rank = beaten / (n_strats - 1)
        rank = min(max(rank, 1e-9), 1.0 - 1e-9)
        lambdas.append(math.log(rank / (1.0 - rank)))
    return sum(1 for l in lambdas if l < 0) / len(lambdas)
