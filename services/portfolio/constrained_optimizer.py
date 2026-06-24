"""Constrained minimum-variance optimizer (pure-Python, deterministic).

Minimizes 1/2 * w' Sigma w by PROJECTED GRADIENT: each step takes a gradient
step (grad = Sigma w) then projects back onto the feasible set (budget hyperplane
+ per-name box + factor-exposure hyperplanes + optional L1 gross ball) via
Dykstra. The step size is a Gershgorin bound on the spectral radius, which
guarantees descent for a PSD Sigma without any eigensolve. No RNG; fixed start
(budget/n) and fixed iteration order make it deterministic. Fail-closed on
non-finite cov, infeasible constraints, or non-convergence.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

from .constraints import (
    PortfolioConstraints, InfeasibleConstraints,
    project_box, project_hyperplane, project_l1_ball, dykstra_project,
)


class OptimizerError(Exception):
    """The optimizer did not converge to a feasible optimum."""


def _objective(w, cov):
    n = len(w)
    return 0.5 * sum(w[i] * cov[i][j] * w[j] for i in range(n) for j in range(n))


def min_variance_weights(cov, alpha_ids: Sequence[str], constraints: PortfolioConstraints,
                         *, loadings: Optional[list] = None, factor_ids: Optional[Sequence[str]] = None,
                         iterations: int = 2000, tol: float = 1e-10) -> list[float]:
    n = len(alpha_ids)
    if any(not math.isfinite(cov[i][j]) for i in range(n) for j in range(n)):
        raise ValueError("cov has non-finite entries")
    lb, ub = constraints.resolve_bounds(alpha_ids)        # raises InfeasibleConstraints

    projections = [
        lambda x: project_box(x, lb, ub),
        lambda x, b=constraints.budget: project_hyperplane(x, [1.0] * n, b),
    ]
    if constraints.factor_targets:
        if loadings is None or factor_ids is None:
            raise ValueError("factor_targets require loadings and factor_ids")
        for fid, target in constraints.factor_targets.items():
            col = list(factor_ids).index(fid)
            a = [loadings[i][col] for i in range(n)]
            projections.append(lambda x, a=a, t=target: project_hyperplane(x, a, t))
    if constraints.max_gross is not None:
        projections.append(lambda x, g=constraints.max_gross: project_l1_ball(x, g))

    # Gershgorin spectral-radius bound -> safe constant step
    radius = max((sum(abs(cov[i][j]) for j in range(n)) for i in range(n)), default=0.0)
    step = 1.0 / radius if radius > 0 else 1.0

    w = dykstra_project([constraints.budget / n] * n, projections, iterations=500, tol=tol)
    prev_obj = _objective(w, cov)
    for _ in range(iterations):
        grad = [sum(cov[i][j] * w[j] for j in range(n)) for i in range(n)]
        nxt = dykstra_project([w[i] - step * grad[i] for i in range(n)],
                              projections, iterations=500, tol=tol)
        obj = _objective(nxt, cov)
        moved = max((abs(nxt[i] - w[i]) for i in range(n)), default=0.0)
        w = nxt
        if abs(prev_obj - obj) < tol and moved < math.sqrt(tol):
            break
        prev_obj = obj
    else:
        raise OptimizerError("min_variance_weights did not converge")

    # final feasibility verification (fail-closed)
    if abs(sum(w) - constraints.budget) > 1e-4 or any(
            w[i] < lb[i] - 1e-4 or w[i] > ub[i] + 1e-4 for i in range(n)):
        raise InfeasibleConstraints("no feasible weight vector found")
    return w
