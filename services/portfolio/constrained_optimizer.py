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


def _cvar_and_subgrad(w, scenarios, alpha):
    """Empirical Rockafellar-Uryasev CVaR_alpha of the portfolio LOSS over `scenarios` (T×n
    return matrix), and a subgradient w.r.t. w. Loss_s = -scenarios[s]·w; CVaR = mean of the
    worst k = ceil((1-alpha)·T) losses; a subgradient is the mean of ∂Loss/∂w over that tail
    set = -mean_tail(scenarios[s]). Deterministic tail selection (stable sort by return)."""
    t = len(scenarios)
    n = len(w)
    rets = [sum(scenarios[s][i] * w[i] for i in range(n)) for s in range(t)]
    order = sorted(range(t), key=lambda s: rets[s])       # ascending -> worst (most negative) first
    k = max(1, math.ceil((1.0 - alpha) * t))
    tail = order[:k]
    cvar = sum(-rets[s] for s in tail) / k
    subgrad = [(-sum(scenarios[s][i] for s in tail) / k) for i in range(n)]
    return cvar, subgrad


def min_variance_weights(cov, alpha_ids: Sequence[str], constraints: PortfolioConstraints,
                         *, loadings: Optional[list] = None, factor_ids: Optional[Sequence[str]] = None,
                         expected_returns: Optional[Sequence[float]] = None, risk_aversion: float = 1.0,
                         prev_weights: Optional[Sequence[float]] = None,
                         cvar_scenarios: Optional[Sequence[Sequence[float]]] = None,
                         cvar_penalty: float = 0.0, cvar_alpha: float = 0.95,
                         iterations: int = 2000, tol: float = 1e-10) -> list[float]:
    """Constrained MEAN-VARIANCE: minimize 1/2 w'Σw − λ·μ'w. With expected_returns=None
    (default) it is pure minimum-variance. A max_turnover constraint (with prev_weights)
    caps Σ|w − prev| via an L1 ball centered at prev.

    Rank #10c: pass `cvar_scenarios` (a T×n scenario return matrix) + `cvar_penalty` ρ to add
    a tail-aware term +ρ·CVaR_α(loss) to the objective (its tail subgradient folds into the
    gradient). Because that term is non-smooth, the CVaR path is a projected-subgradient method
    (not a descent method): it tracks and returns the best feasible iterate. The pure smooth
    path (penalty 0 / no scenarios) is byte-identical to before."""
    n = len(alpha_ids)
    if any(not math.isfinite(cov[i][j]) for i in range(n) for j in range(n)):
        raise ValueError("cov has non-finite entries")
    if expected_returns is not None and any(not math.isfinite(m) for m in expected_returns):
        raise ValueError("expected_returns has non-finite entries")
    cvar_on = cvar_scenarios is not None and cvar_penalty != 0.0
    if cvar_on:
        if not math.isfinite(cvar_penalty) or not math.isfinite(cvar_alpha) or not 0.0 < cvar_alpha < 1.0:
            raise ValueError("cvar_penalty must be finite and 0 < cvar_alpha < 1")
        for row in cvar_scenarios:
            if len(row) != n or any(not math.isfinite(x) for x in row):
                raise ValueError("cvar_scenarios must be a finite T×n matrix")
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

    def _full_obj(v):   # 1/2 v'Σv − λμ'v (+ ρ·CVaR_α if enabled) — the actual minimization target
        base = _objective(v, cov)
        if expected_returns is not None:
            base -= risk_aversion * sum(expected_returns[i] * v[i] for i in range(n))
        if cvar_on:
            base += cvar_penalty * _cvar_and_subgrad(v, cvar_scenarios, cvar_alpha)[0]
        return base

    w = dykstra_project([constraints.budget / n] * n, projections, iterations=500, tol=tol)
    prev_obj = _full_obj(w)
    best_w, best_obj = w, prev_obj                        # subgradient methods aren't descent: keep the best
    for _ in range(iterations):
        grad = [sum(cov[i][j] * w[j] for j in range(n)) for i in range(n)]
        if expected_returns is not None:               # mean-variance: -λμ in the gradient
            grad = [grad[i] - risk_aversion * expected_returns[i] for i in range(n)]
        if cvar_on:                                    # + ρ·(tail subgradient)
            sub = _cvar_and_subgrad(w, cvar_scenarios, cvar_alpha)[1]
            grad = [grad[i] + cvar_penalty * sub[i] for i in range(n)]
        nxt = dykstra_project([w[i] - step * grad[i] for i in range(n)],
                              projections, iterations=500, tol=tol)
        obj = _full_obj(nxt)
        if obj < best_obj:
            best_w, best_obj = nxt, obj
        moved = max((abs(nxt[i] - w[i]) for i in range(n)), default=0.0)
        w = nxt
        if not cvar_on and abs(prev_obj - obj) < tol and moved < math.sqrt(tol):
            break                                      # smooth path: unchanged descent convergence
        prev_obj = obj
    else:
        if not cvar_on:
            raise OptimizerError("min_variance_weights did not converge")
    if cvar_on:
        w = best_w                                     # return the best feasible iterate seen

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
