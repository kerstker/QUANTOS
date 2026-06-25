"""Factor/portfolio risk model (Phase 7, step 1) — pure-Python, deterministic.

Builds a covariance matrix over aligned alpha return streams, shrinks it toward
its diagonal for small-sample stability (the optimizer must not chase noise
correlations), and decomposes portfolio risk into per-alpha contributions (the
basis for risk budgeting). Fail-closed below a minimum overlap.
"""
from __future__ import annotations

import math
import statistics
from typing import Optional, Sequence

Matrix = list[list[float]]


def covariance_matrix(
    returns_by_alpha: dict[str, Sequence[float]],
    min_overlap: int = 60,
) -> tuple[list[str], Matrix]:
    """(sorted ids, sample covariance matrix) over the common trailing window.

    Fail-closed: raises if there are no series or the shared overlap is below
    min_overlap (a covariance on mismatched/short samples is not trustworthy)."""
    ids = sorted(returns_by_alpha)
    if not ids:
        raise ValueError("no return series provided")
    overlap = min(len(returns_by_alpha[a]) for a in ids)
    if overlap < min_overlap:
        raise ValueError(f"overlap {overlap} < min_overlap {min_overlap}")
    aligned = {a: list(returns_by_alpha[a][-overlap:]) for a in ids}
    n = len(ids)
    cov: Matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i, n):
            if i == j:
                c = statistics.variance(aligned[ids[i]])
            else:
                c = statistics.covariance(aligned[ids[i]], aligned[ids[j]])
            cov[i][j] = cov[j][i] = c
    return ids, cov


def shrink_covariance(cov: Matrix, intensity: float = 0.2) -> Matrix:
    """Linear shrinkage toward the diagonal: off-diagonals * (1-intensity).
    intensity in [0,1]; 0 = raw sample cov, 1 = fully diagonal (no correlations)."""
    if not (0.0 <= intensity <= 1.0):
        raise ValueError("intensity must be in [0,1]")
    n = len(cov)
    out: Matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            out[i][j] = cov[i][j] if i == j else cov[i][j] * (1.0 - intensity)
    return out


def portfolio_variance(weights: Sequence[float], cov: Matrix) -> float:
    n = len(weights)
    return sum(weights[i] * weights[j] * cov[i][j] for i in range(n) for j in range(n))


def portfolio_vol(weights: Sequence[float], cov: Matrix) -> float:
    return math.sqrt(max(0.0, portfolio_variance(weights, cov)))


def marginal_risk_contributions(weights: Sequence[float], cov: Matrix) -> list[float]:
    """Marginal risk contribution per asset in variance space: MRC_i = (cov·w)_i.
    Component RC_i = w_i · MRC_i, so sum_i w_i·MRC_i == portfolio_variance."""
    n = len(weights)
    return [sum(cov[i][j] * weights[j] for j in range(n)) for i in range(n)]


def risk_contributions(weights: Sequence[float], cov: Matrix) -> list[float]:
    """Component risk contribution per asset (sum == portfolio variance)."""
    mrc = marginal_risk_contributions(weights, cov)
    return [weights[i] * mrc[i] for i in range(len(weights))]


def percent_risk_contributions(weights: Sequence[float], cov: Matrix) -> list[float]:
    contrib = risk_contributions(weights, cov)
    total = sum(contrib)
    if total == 0:
        return [0.0] * len(weights)
    return [c / total for c in contrib]
