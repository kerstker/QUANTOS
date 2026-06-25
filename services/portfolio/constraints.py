"""Constraint set + Euclidean projections for the constrained optimizer.

Pure-Python, deterministic. Dykstra's algorithm projects onto the intersection
of convex sets (box, budget hyperplane, factor-exposure hyperplanes, L1 gross
ball). Fail-closed: obviously infeasible bounds raise InfeasibleConstraints.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence, Union


class InfeasibleConstraints(Exception):
    """The constraint set is empty (no weight vector can satisfy it)."""


def project_box(w: Sequence[float], lb: Sequence[float], ub: Sequence[float]) -> list[float]:
    return [min(max(w[i], lb[i]), ub[i]) for i in range(len(w))]


def project_hyperplane(w: Sequence[float], a: Sequence[float], b: float) -> list[float]:
    aa = sum(x * x for x in a)
    if aa == 0.0:
        return list(w)                       # degenerate normal -> no constraint
    t = (sum(a[i] * w[i] for i in range(len(w))) - b) / aa
    return [w[i] - t * a[i] for i in range(len(w))]


def project_l1_ball(w: Sequence[float], radius: float) -> list[float]:
    if radius < 0:
        raise InfeasibleConstraints("gross-leverage radius must be >= 0")
    if sum(abs(x) for x in w) <= radius + 1e-15:
        return list(w)
    u = sorted((abs(x) for x in w), reverse=True)
    cum, rho, css = 0.0, 0, 0.0
    for j, uj in enumerate(u, start=1):
        cum += uj
        if uj - (cum - radius) / j > 0:
            rho, css = j, cum
    theta = (css - radius) / rho
    return [math.copysign(max(abs(x) - theta, 0.0), x) for x in w]


def dykstra_project(w: Sequence[float], projections: Sequence[Callable],
                    *, iterations: int = 500, tol: float = 1e-12) -> list[float]:
    n = len(w)
    x = list(w)
    corr = [[0.0] * n for _ in projections]
    for _ in range(iterations):
        max_change = 0.0
        for i, proj in enumerate(projections):
            y = [x[j] - corr[i][j] for j in range(n)]
            z = proj(y)
            corr[i] = [z[j] - y[j] for j in range(n)]
            max_change = max(max_change, max((abs(z[j] - x[j]) for j in range(n)), default=0.0))
            x = z
        if max_change < tol:
            break
    return x


@dataclass(frozen=True)
class PortfolioConstraints:
    lower: Union[float, dict] = -1.0
    upper: Union[float, dict] = 1.0
    budget: float = 1.0
    max_gross: Optional[float] = None
    factor_targets: dict = field(default_factory=dict)
    max_turnover: Optional[float] = None     # cap on sum|w - prev_weights| (needs prev_weights)

    def _bound(self, spec: Union[float, dict], aid: str) -> float:
        return float(spec[aid]) if isinstance(spec, dict) else float(spec)

    def resolve_bounds(self, alpha_ids: Sequence[str]) -> tuple[list[float], list[float]]:
        lb = [self._bound(self.lower, a) for a in alpha_ids]
        ub = [self._bound(self.upper, a) for a in alpha_ids]
        for i in range(len(alpha_ids)):
            if lb[i] > ub[i]:
                raise InfeasibleConstraints(f"lower>upper for {alpha_ids[i]}")
        if not (sum(lb) - 1e-12 <= self.budget <= sum(ub) + 1e-12):
            raise InfeasibleConstraints(
                f"budget {self.budget} not in [{sum(lb)}, {sum(ub)}]")
        if self.max_gross is not None and self.max_gross < abs(self.budget) - 1e-12:
            raise InfeasibleConstraints("max_gross < |budget| is infeasible")
        return lb, ub
