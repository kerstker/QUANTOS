"""Multi-factor risk model math (Phase 7+, audit roadmap) — pure-Python.

Total covariance Sigma = B*Sf*B^T + diag(Delta); factor exposures B^T w; and a
factor-vs-specific variance decomposition. Deterministic, no external solvers.
"""
from __future__ import annotations

from typing import Sequence

from quantos.contracts.factor import FactorRiskModel

Matrix = list[list[float]]


def total_covariance(model: FactorRiskModel, alpha_ids: Sequence[str]) -> Matrix:
    B, spec = model.aligned(alpha_ids)
    F = model.factor_cov
    n, k = len(B), len(model.factor_ids)
    # M = B * F  (n x k)
    M = [[sum(B[i][a] * F[a][c] for a in range(k)) for c in range(k)] for i in range(n)]
    # Sigma = M * B^T + diag(spec)
    cov = [[sum(M[i][c] * B[j][c] for c in range(k)) + (spec[i] if i == j else 0.0)
            for j in range(n)] for i in range(n)]
    return cov


def factor_exposures(weights: Sequence[float], model: FactorRiskModel,
                     alpha_ids: Sequence[str]) -> dict[str, float]:
    B, _ = model.aligned(alpha_ids)
    k = len(model.factor_ids)
    return {model.factor_ids[c]: sum(weights[i] * B[i][c] for i in range(len(B)))
            for c in range(k)}


def risk_decomposition(weights: Sequence[float], model: FactorRiskModel,
                       alpha_ids: Sequence[str]) -> dict:
    B, spec = model.aligned(alpha_ids)
    F = model.factor_cov
    n, k = len(B), len(model.factor_ids)
    e = [sum(weights[i] * B[i][c] for i in range(n)) for c in range(k)]   # B^T w
    Fe = [sum(F[c][a] * e[a] for a in range(k)) for c in range(k)]         # Sf e
    factor_var = sum(e[c] * Fe[c] for c in range(k))                       # e' Sf e
    by_factor = {model.factor_ids[c]: e[c] * Fe[c] for c in range(k)}      # sums to factor_var
    specific_var = sum(weights[i] ** 2 * spec[i] for i in range(n))
    return {
        "total_var": factor_var + specific_var,
        "factor_var": factor_var,
        "specific_var": specific_var,
        "by_factor": by_factor,
        "exposures": {model.factor_ids[c]: e[c] for c in range(k)},
    }
