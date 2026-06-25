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
                         expected_returns: Optional[Sequence[float]] = None, risk_aversion: float = 1.0,
                         prev_weights: Optional[Sequence[float]] = None,
                         iterations: int = 2000, tol: float = 1e-10) -> list[float]:
    """Constrained MEAN-VARIANCE: minimize 1/2 w'Σw − λ·μ'w. With expected_returns=None
    (default) it is pure minimum-variance. A max_turnover constraint (with prev_weights)
    caps Σ|w − prev| via an L1 ball centered at prev."""
    n = len(alpha_ids)
    if any(not math.isfinite(cov[i][j]) for i in range(n) for j in range(n)):
        raise ValueError("cov has non-finite entries")
    if expected_returns is not None and any(not math.isfinite(m) for m in expected_returns):
        raise ValueError("expected_returns has non-finite entries")
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
    if constraints.max_turnover is not None:
        if prev_weights is None:
            raise ValueError("max_turnover requires prev_weights")
        pw = [float(p) for p in prev_weights]
        # |w - pw|_1 <= tau  <=>  project the displacement onto an L1 ball, re-center
        projections.append(lambda x, pw=pw, tau=constraints.max_turnover:
                           [pw[i] + d for i, d in enumerate(project_l1_ball([x[i] - pw[i] for i in range(n)], tau))])

    # Gershgorin spectral-radius bound -> safe constant step
    radius = max((sum(abs(cov[i][j]) for j in range(n)) for i in range(n)), default=0.0)
    step = 1.0 / radius if radius > 0 else 1.0

    def _full_obj(v):   # 1/2 v'Σv − λμ'v (the actual minimization target)
        base = _objective(v, cov)
        return base - risk_aversion * sum(expected_returns[i] * v[i] for i in range(n)) \
            if expected_returns is not None else base

    w = dykstra_project([constraints.budget / n] * n, projections, iterations=500, tol=tol)
    prev_obj = _full_obj(w)
    for _ in range(iterations):
        grad = [sum(cov[i][j] * w[j] for j in range(n)) for i in range(n)]
        if expected_returns is not None:               # mean-variance: -λμ in the gradient
            grad = [grad[i] - risk_aversion * expected_returns[i] for i in range(n)]
        nxt = dykstra_project([w[i] - step * grad[i] for i in range(n)],
                              projections, iterations=500, tol=tol)
        obj = _full_obj(nxt)
        moved = max((abs(nxt[i] - w[i]) for i in range(n)), default=0.0)
        w = nxt
        if abs(prev_obj - obj) < tol and moved < math.sqrt(tol):
            break
        prev_obj = obj
    else:
        raise OptimizerError("min_variance_weights did not converge")

    # final feasibility verification (fail-closed) — budget + box AND the factor
    # targets + gross-leverage cap (review P0: omitting the latter two let an
    # infeasible vector be returned as the "optimum" when Dykstra had not fully
    # driven those sets to tol).
    tol_feas = 1e-3
    if abs(sum(w) - constraints.budget) > tol_feas or any(
            w[i] < lb[i] - tol_feas or w[i] > ub[i] + tol_feas for i in range(n)):
        raise InfeasibleConstraints("no feasible weight vector found (budget/box)")
    for fid, target in (constraints.factor_targets or {}).items():
        col = list(factor_ids).index(fid)
        exposure = sum(w[i] * loadings[i][col] for i in range(n))
        if abs(exposure - target) > tol_feas:
            raise InfeasibleConstraints(f"factor target '{fid}' unmet: {exposure} != {target}")
    if constraints.max_gross is not None and sum(abs(x) for x in w) > constraints.max_gross + tol_feas:
        raise InfeasibleConstraints("gross-leverage cap unmet")
    if constraints.max_turnover is not None:
        turnover = sum(abs(w[i] - float(prev_weights[i])) for i in range(n))
        if turnover > constraints.max_turnover + tol_feas:
            raise InfeasibleConstraints("turnover cap unmet")
    return w
