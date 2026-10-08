# Factor Risk Model + Constrained Optimizer Implementation Plan

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a caller-supplied multi-factor risk model and a deterministic pure-Python constrained minimum-variance (long-short) optimizer to the portfolio layer, without changing existing portfolio code.

**Architecture:** New modules: a `FactorRiskModel` contract; `factor_risk.py` (Σ=B·Σf·Bᵀ+diag(Δ), exposures, factor/specific decomposition); `constraints.py` (box/hyperplane/L1 projections + Dykstra); `constrained_optimizer.py` (projected gradient). `construction.py` gains `build_factor_portfolio` only.

**Tech Stack:** Python 3 stdlib (`math`, `statistics`); pydantic v2 contracts. NO numpy/scipy/cvxpy.

## Global Constraints

- L2_DRY_RUN_ONLY; pure-Python stdlib only (no numpy/scipy/cvxpy); deterministic (NO RNG, fixed iteration order, fixed start vector).
- Fail-closed: non-finite inputs raise; infeasible constraints raise `InfeasibleConstraints`; non-convergence raises `OptimizerError`. Never return an infeasible/non-finite weight vector.
- Long-SHORT: per-name box bounds may be negative; `TargetPosition.target_weight` may be negative.
- Do NOT modify existing `risk_model.py` / `optimizer.py`; `construction.py` is ADD-only (keep `build_portfolio`).
- Preserve the import boundary: new portfolio modules import only stdlib + `quantos.contracts` (the AST test `tests/portfolio/test_portfolio_import_boundary.py` must stay green).
- Gate between tasks: `python scripts/validate.py`. Isolated pytest: prefix `PYTHONPATH=H:/code/QUANTOS`.
- Existing types: `Matrix = list[list[float]]` (in `risk_model.py`); `cost_aware_rebalance(prev: dict, target: dict, no_trade_band: float=0.0) -> tuple[dict, float]`; `RebalancePlan(plan_id, portfolio_id, targets: tuple[TargetPosition,...], turnover)`; `TargetPosition(instrument_id, target_weight)`.

---

### Task 1: FactorRiskModel contract

**Files:**
- Create: `quantos/contracts/factor.py`
- Test: `tests/contracts/test_factor.py`

**Interfaces:**
- Produces: `class FactorRiskModel(BaseModel)` (frozen) with fields `factor_ids: tuple[str,...]`, `loadings: dict[str, tuple[float,...]]`, `factor_cov: tuple[tuple[float,...],...]`, `specific_var: dict[str, float]`; method `aligned(self, alpha_ids: Sequence[str]) -> tuple[list[list[float]], list[float]]` returning `(B, spec)` aligned to `alpha_ids` (raises `ValueError` if an id is missing).

- [ ] **Step 1: Write the failing test**

```python
# tests/contracts/test_factor.py
import pytest
from quantos.contracts.factor import FactorRiskModel


def _model():
    return FactorRiskModel(
        factor_ids=("mkt", "size"),
        loadings={"a": (1.0, 0.2), "b": (0.8, -0.5)},
        factor_cov=((0.04, 0.0), (0.0, 0.02)),
        specific_var={"a": 0.01, "b": 0.02},
    )


def test_aligned_returns_rows_and_specific_in_order():
    B, spec = _model().aligned(["b", "a"])
    assert B == [[0.8, -0.5], [1.0, 0.2]]
    assert spec == [0.02, 0.01]


def test_missing_alpha_id_fails_closed():
    with pytest.raises(ValueError):
        _model().aligned(["a", "missing"])


def test_validates_loading_row_length_against_factor_count():
    with pytest.raises(Exception):
        FactorRiskModel(factor_ids=("mkt", "size"), loadings={"a": (1.0,)},
                        factor_cov=((0.04, 0.0), (0.0, 0.02)), specific_var={"a": 0.01})


def test_validates_factor_cov_is_square_symmetric():
    with pytest.raises(Exception):
        FactorRiskModel(factor_ids=("mkt", "size"), loadings={"a": (1.0, 0.2)},
                        factor_cov=((0.04, 0.01), (0.02, 0.02)), specific_var={"a": 0.01})


def test_rejects_negative_specific_var_and_nonfinite():
    with pytest.raises(Exception):
        FactorRiskModel(factor_ids=("mkt",), loadings={"a": (1.0,)},
                        factor_cov=((0.04,),), specific_var={"a": -0.01})
    with pytest.raises(Exception):
        FactorRiskModel(factor_ids=("mkt",), loadings={"a": (float("nan"),)},
                        factor_cov=((0.04,),), specific_var={"a": 0.01})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/contracts/test_factor.py -v`
Expected: FAIL with `ModuleNotFoundError: quantos.contracts.factor`.

- [ ] **Step 3: Write minimal implementation**

```python
# quantos/contracts/factor.py
"""Caller-supplied multi-factor risk model (frozen contract).

Total covariance is Sigma = B*Sf*B^T + diag(Delta), where B = loadings
(alpha x factor), Sf = factor_cov, Delta = specific_var. The model is supplied
(not estimated) — there is no real data at L2 to estimate it from. Validated
fail-closed: dimensions, symmetry, non-negative specific variance, all finite.
"""
from __future__ import annotations

import math
from typing import Sequence

from pydantic import BaseModel, ConfigDict, model_validator


class FactorRiskModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    factor_ids: tuple[str, ...]
    loadings: dict[str, tuple[float, ...]]
    factor_cov: tuple[tuple[float, ...], ...]
    specific_var: dict[str, float]

    @model_validator(mode="after")
    def _check(self) -> "FactorRiskModel":
        k = len(self.factor_ids)
        if k == 0 or len(set(self.factor_ids)) != k:
            raise ValueError("factor_ids must be non-empty and unique")
        if len(self.factor_cov) != k or any(len(row) != k for row in self.factor_cov):
            raise ValueError("factor_cov must be k x k")
        for i in range(k):
            for j in range(k):
                v = self.factor_cov[i][j]
                if not math.isfinite(v):
                    raise ValueError("factor_cov has non-finite entry")
                if abs(v - self.factor_cov[j][i]) > 1e-12:
                    raise ValueError("factor_cov must be symmetric")
        for aid, row in self.loadings.items():
            if len(row) != k:
                raise ValueError(f"loading row for {aid} must have length {k}")
            if any(not math.isfinite(x) for x in row):
                raise ValueError(f"loading row for {aid} has non-finite entry")
        for aid, sv in self.specific_var.items():
            if not math.isfinite(sv) or sv < 0:
                raise ValueError(f"specific_var[{aid}] must be finite and >= 0")
        return self

    def aligned(self, alpha_ids: Sequence[str]) -> tuple[list[list[float]], list[float]]:
        B, spec = [], []
        for aid in alpha_ids:
            if aid not in self.loadings or aid not in self.specific_var:
                raise ValueError(f"factor model has no entry for alpha {aid}")
            B.append(list(self.loadings[aid]))
            spec.append(float(self.specific_var[aid]))
        return B, spec
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/contracts/test_factor.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add quantos/contracts/factor.py tests/contracts/test_factor.py
git commit -m "feat(contracts): FactorRiskModel — caller-supplied, validated fail-closed"
```

---

### Task 2: factor_risk.py — total covariance + decomposition

**Files:**
- Create: `services/portfolio/factor_risk.py`
- Test: `tests/portfolio/test_factor_risk.py`

**Interfaces:**
- Consumes: `FactorRiskModel.aligned` (Task 1).
- Produces:
  - `total_covariance(model: FactorRiskModel, alpha_ids: Sequence[str]) -> list[list[float]]`
  - `factor_exposures(weights: Sequence[float], model, alpha_ids) -> dict[str, float]`
  - `risk_decomposition(weights, model, alpha_ids) -> dict` with keys `total_var`, `factor_var`, `specific_var`, `by_factor` (dict), `exposures` (dict).

- [ ] **Step 1: Write the failing test**

```python
# tests/portfolio/test_factor_risk.py
import math
import pytest
from quantos.contracts.factor import FactorRiskModel
from services.portfolio.factor_risk import (
    total_covariance, factor_exposures, risk_decomposition,
)


def _model():
    return FactorRiskModel(
        factor_ids=("mkt", "size"),
        loadings={"a": (1.0, 0.2), "b": (0.8, -0.5), "c": (0.3, 0.9)},
        factor_cov=((0.04, 0.005), (0.005, 0.02)),
        specific_var={"a": 0.01, "b": 0.02, "c": 0.015},
    )


def _quad(w, cov):
    n = len(w)
    return sum(w[i] * cov[i][j] * w[j] for i in range(n) for j in range(n))


def test_total_covariance_is_symmetric_and_correct_dimension():
    cov = total_covariance(_model(), ["a", "b", "c"])
    assert len(cov) == 3 and all(len(r) == 3 for r in cov)
    for i in range(3):
        for j in range(3):
            assert abs(cov[i][j] - cov[j][i]) < 1e-12
    # diagonal includes specific variance: cov[0][0] = b0 Sf b0 + spec_a
    # = 1*0.04*1 + 1*0.005*0.2 + 0.2*0.005*1 + 0.2*0.02*0.2 + 0.01
    assert cov[0][0] == pytest.approx(0.04 + 0.002 + 0.0008 + 0.01, abs=1e-12)


def test_factor_exposures_equal_B_transpose_w():
    w = [0.5, 0.3, 0.2]
    e = factor_exposures(w, _model(), ["a", "b", "c"])
    assert e["mkt"] == pytest.approx(0.5 * 1.0 + 0.3 * 0.8 + 0.2 * 0.3)
    assert e["size"] == pytest.approx(0.5 * 0.2 + 0.3 * -0.5 + 0.2 * 0.9)


def test_decomposition_identity_factor_plus_specific_equals_total():
    w = [0.5, -0.3, 0.2]                       # long-short
    d = risk_decomposition(w, _model(), ["a", "b", "c"])
    assert d["factor_var"] + d["specific_var"] == pytest.approx(d["total_var"], abs=1e-12)
    # total_var equals the quadratic form w' Sigma w
    cov = total_covariance(_model(), ["a", "b", "c"])
    assert d["total_var"] == pytest.approx(_quad(w, cov), abs=1e-12)
    # by_factor contributions sum to factor_var
    assert sum(d["by_factor"].values()) == pytest.approx(d["factor_var"], abs=1e-12)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/portfolio/test_factor_risk.py -v`
Expected: FAIL with `ModuleNotFoundError: services.portfolio.factor_risk`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/portfolio/factor_risk.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/portfolio/test_factor_risk.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add services/portfolio/factor_risk.py tests/portfolio/test_factor_risk.py
git commit -m "feat(portfolio): factor risk model math — total cov + factor/specific decomposition"
```

---

### Task 3: constraints.py — projections + Dykstra

**Files:**
- Create: `services/portfolio/constraints.py`
- Test: `tests/portfolio/test_constraints.py`

**Interfaces:**
- Produces:
  - `class InfeasibleConstraints(Exception)`
  - `project_box(w, lb, ub) -> list[float]` (lb/ub are lists)
  - `project_hyperplane(w, a, b) -> list[float]`
  - `project_l1_ball(w, radius) -> list[float]`
  - `dykstra_project(w, projections, *, iterations=500, tol=1e-12) -> list[float]`
  - `@dataclass(frozen=True) class PortfolioConstraints` with `lower`, `upper` (float | dict), `budget: float = 1.0`, `max_gross: Optional[float] = None`, `factor_targets: dict[str, float] = {}`; method `resolve_bounds(alpha_ids) -> tuple[list[float], list[float]]` (raises `InfeasibleConstraints` if `lb[i] > ub[i]` or `budget` not in `[sum lb, sum ub]`).

- [ ] **Step 1: Write the failing test**

```python
# tests/portfolio/test_constraints.py
import math
import pytest
from services.portfolio.constraints import (
    PortfolioConstraints, InfeasibleConstraints,
    project_box, project_hyperplane, project_l1_ball, dykstra_project,
)


def test_project_box_clamps_including_negative_bounds():
    assert project_box([1.5, -2.0, 0.1], [-1.0, -1.0, -1.0], [1.0, 1.0, 1.0]) == [1.0, -1.0, 0.1]


def test_project_hyperplane_lands_on_plane():
    a, b = [1.0, 1.0, 1.0], 1.0
    p = project_hyperplane([0.0, 0.0, 0.0], a, b)
    assert sum(p) == pytest.approx(1.0)


def test_project_l1_ball_noop_inside_and_shrinks_outside():
    assert project_l1_ball([0.2, -0.3], 1.0) == [0.2, -0.3]      # inside -> unchanged
    out = project_l1_ball([1.0, -1.0, 1.0], 1.5)
    assert sum(abs(x) for x in out) == pytest.approx(1.5, abs=1e-9)


def test_dykstra_lands_in_box_and_budget_intersection():
    lb, ub = [0.0, 0.0, 0.0], [0.5, 0.5, 0.5]
    ones = [1.0, 1.0, 1.0]
    p = dykstra_project([0.9, 0.9, 0.9],
                        [lambda x: project_box(x, lb, ub),
                         lambda x: project_hyperplane(x, ones, 1.0)])
    assert sum(p) == pytest.approx(1.0, abs=1e-6)
    assert all(lb[i] - 1e-6 <= p[i] <= ub[i] + 1e-6 for i in range(3))


def test_resolve_bounds_infeasible_budget_raises():
    c = PortfolioConstraints(lower=0.0, upper=0.1, budget=1.0)  # 3*0.1 < 1.0
    with pytest.raises(InfeasibleConstraints):
        c.resolve_bounds(["a", "b", "c"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/portfolio/test_constraints.py -v`
Expected: FAIL with `ModuleNotFoundError: services.portfolio.constraints`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/portfolio/constraints.py
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
    cum, rho, theta = 0.0, 0, 0.0
    css = 0.0
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/portfolio/test_constraints.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add services/portfolio/constraints.py tests/portfolio/test_constraints.py
git commit -m "feat(portfolio): constraint set + box/hyperplane/L1 projections + Dykstra"
```

---

### Task 4: constrained_optimizer.py — min_variance_weights

**Files:**
- Create: `services/portfolio/constrained_optimizer.py`
- Test: `tests/portfolio/test_constrained_optimizer.py`

**Interfaces:**
- Consumes: `constraints` projections + `PortfolioConstraints` + `InfeasibleConstraints` (Task 3).
- Produces:
  - `class OptimizerError(Exception)`
  - `min_variance_weights(cov, alpha_ids, constraints, *, loadings=None, factor_ids=None, iterations=2000, tol=1e-10) -> list[float]`

- [ ] **Step 1: Write the failing test**

```python
# tests/portfolio/test_constrained_optimizer.py
import math
import pytest
from services.portfolio.constraints import PortfolioConstraints, InfeasibleConstraints
from services.portfolio.constrained_optimizer import min_variance_weights, OptimizerError


def _quad(w, cov):
    n = len(w)
    return sum(w[i] * cov[i][j] * w[j] for i in range(n) for j in range(n))


COV = [[0.04, 0.006, 0.0], [0.006, 0.09, 0.0], [0.0, 0.0, 0.16]]
IDS = ["a", "b", "c"]


def test_respects_budget_and_box():
    c = PortfolioConstraints(lower=0.0, upper=1.0, budget=1.0)
    w = min_variance_weights(COV, IDS, c)
    assert sum(w) == pytest.approx(1.0, abs=1e-6)
    assert all(-1e-6 <= x <= 1.0 + 1e-6 for x in w)


def test_min_variance_beats_equal_weight():
    c = PortfolioConstraints(lower=0.0, upper=1.0, budget=1.0)
    w = min_variance_weights(COV, IDS, c)
    eq = [1 / 3, 1 / 3, 1 / 3]
    assert _quad(w, COV) <= _quad(eq, COV) + 1e-9


def test_long_short_factor_neutrality():
    # one factor; loadings make a factor-neutral (B^T w = 0) long-short book
    loadings = [[1.0], [1.0], [-1.0]]
    c = PortfolioConstraints(lower=-1.0, upper=1.0, budget=0.0,
                             factor_targets={"f": 0.0})
    w = min_variance_weights(COV, IDS, c, loadings=loadings, factor_ids=["f"])
    exposure = sum(w[i] * loadings[i][0] for i in range(3))
    assert exposure == pytest.approx(0.0, abs=1e-5)
    assert sum(w) == pytest.approx(0.0, abs=1e-5)


def test_gross_leverage_cap_enforced():
    c = PortfolioConstraints(lower=-2.0, upper=2.0, budget=0.0, max_gross=1.0)
    w = min_variance_weights(COV, IDS, c)
    assert sum(abs(x) for x in w) <= 1.0 + 1e-5


def test_infeasible_constraints_raise():
    c = PortfolioConstraints(lower=0.0, upper=0.1, budget=1.0)   # 3*0.1 < 1
    with pytest.raises(InfeasibleConstraints):
        min_variance_weights(COV, IDS, c)


def test_non_finite_cov_raises():
    bad = [[float("nan"), 0.0, 0.0], [0.0, 0.09, 0.0], [0.0, 0.0, 0.16]]
    with pytest.raises(ValueError):
        min_variance_weights(bad, IDS, PortfolioConstraints(lower=0.0, upper=1.0))


def test_is_deterministic():
    c = PortfolioConstraints(lower=0.0, upper=1.0, budget=1.0)
    assert min_variance_weights(COV, IDS, c) == min_variance_weights(COV, IDS, c)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/portfolio/test_constrained_optimizer.py -v`
Expected: FAIL with `ModuleNotFoundError: services.portfolio.constrained_optimizer`.

- [ ] **Step 3: Write minimal implementation**

```python
# services/portfolio/constrained_optimizer.py
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
    if abs(sum(w) - constraints.budget) > 1e-4 or any(w[i] < lb[i] - 1e-4 or w[i] > ub[i] + 1e-4 for i in range(n)):
        raise InfeasibleConstraints("no feasible weight vector found")
    return w
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/portfolio/test_constrained_optimizer.py -v`
Expected: PASS (7 passed). If `test_long_short_factor_neutrality` or `test_gross_leverage_cap_enforced` are marginally outside tol, raise `iterations` is not needed — the Dykstra inner tol already drives feasibility; confirm the asserts use `abs=1e-5`.

- [ ] **Step 5: Commit**

```bash
git add services/portfolio/constrained_optimizer.py tests/portfolio/test_constrained_optimizer.py
git commit -m "feat(portfolio): constrained min-variance optimizer (projected gradient + Dykstra)"
```

---

### Task 5: construction wiring + factor risk report + gate

**Files:**
- Modify: `services/portfolio/construction.py` (ADD `build_factor_portfolio` + `factor_risk_report`; do not touch `build_portfolio`)
- Test: `tests/portfolio/test_construction.py` (extend)

**Interfaces:**
- Consumes: `total_covariance`/`risk_decomposition` (Task 2), `min_variance_weights` (Task 4), `cost_aware_rebalance` (existing), `RebalancePlan`/`TargetPosition` (contracts).
- Produces:
  - `build_factor_portfolio(plan_id, portfolio_id, model, alpha_ids, constraints, *, prev_weights=None, config=None) -> RebalancePlan`
  - `factor_risk_report(model, alpha_ids, weights) -> dict`

- [ ] **Step 1: Write the failing test (append to tests/portfolio/test_construction.py)**

```python
# --- factor portfolio (audit roadmap) ---
from quantos.contracts.factor import FactorRiskModel
from services.portfolio.constraints import PortfolioConstraints
from services.portfolio.construction import build_factor_portfolio, factor_risk_report


def _fmodel():
    return FactorRiskModel(
        factor_ids=("f",),
        loadings={"a": (1.0,), "b": (1.0,), "c": (-1.0,)},
        factor_cov=((0.04,),),
        specific_var={"a": 0.02, "b": 0.03, "c": 0.05},
    )


def test_build_factor_portfolio_returns_factor_neutral_plan():
    c = PortfolioConstraints(lower=-1.0, upper=1.0, budget=0.0, factor_targets={"f": 0.0})
    plan = build_factor_portfolio("p1", "pf", _fmodel(), ["a", "b", "c"], c)
    weights = {t.instrument_id: t.target_weight for t in plan.targets}
    exposure = weights["a"] * 1.0 + weights["b"] * 1.0 + weights["c"] * -1.0
    assert abs(exposure) < 1e-4                       # factor-neutral
    assert abs(sum(weights.values())) < 1e-4          # dollar-neutral (budget 0)


def test_factor_risk_report_sums_to_total():
    rep = factor_risk_report(_fmodel(), ["a", "b", "c"], [0.5, -0.3, 0.2])
    assert rep["factor_var"] + rep["specific_var"] == pytest.approx(rep["total_var"], abs=1e-12)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/portfolio/test_construction.py -k factor -v`
Expected: FAIL with `ImportError: cannot import name 'build_factor_portfolio'`.

- [ ] **Step 3: Write minimal implementation (append to services/portfolio/construction.py)**

```python
from quantos.contracts.factor import FactorRiskModel
from quantos.contracts.portfolio import RebalancePlan, TargetPosition
from .constraints import PortfolioConstraints
from .constrained_optimizer import min_variance_weights
from .factor_risk import total_covariance, risk_decomposition
from .optimizer import cost_aware_rebalance
from .config import PortfolioConfig


def build_factor_portfolio(plan_id: str, portfolio_id: str, model: FactorRiskModel,
                           alpha_ids, constraints: PortfolioConstraints, *,
                           prev_weights: dict | None = None,
                           config: PortfolioConfig | None = None) -> RebalancePlan:
    cfg = config or PortfolioConfig()
    ids = list(alpha_ids)
    cov = total_covariance(model, ids)
    B, _ = model.aligned(ids)
    w = min_variance_weights(cov, ids, constraints, loadings=B, factor_ids=list(model.factor_ids))
    target = {ids[i]: w[i] for i in range(len(ids))}
    if prev_weights is not None:
        target, turnover = cost_aware_rebalance(prev_weights, target, cfg.no_trade_band)
    else:
        turnover = sum(abs(v) for v in target.values())
    return RebalancePlan(
        plan_id=plan_id, portfolio_id=portfolio_id,
        targets=tuple(TargetPosition(instrument_id=a, target_weight=round(target[a], 10))
                      for a in ids if a in target),
        turnover=round(turnover, 10),
    )


def factor_risk_report(model: FactorRiskModel, alpha_ids, weights) -> dict:
    return risk_decomposition(list(weights), model, list(alpha_ids))
```

Note: `cost_aware_rebalance` renormalizes to sum 1 in its current form; for a budget-0 long-short book pass `prev_weights=None` (the default) so the optimizer's dollar-neutral weights are preserved. (Rebalancing a long-short book is out of scope — roadmap.)

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/portfolio/test_construction.py -v`
Expected: PASS (all existing construction tests + 2 new).

- [ ] **Step 5: Run the import-boundary test + full gate**

Run: `PYTHONPATH=. python -m pytest tests/portfolio/test_portfolio_import_boundary.py -v`
Expected: PASS (new modules import only stdlib + quantos.contracts).

Run: `python scripts/validate.py`
Expected: `VALIDATE PASSED (fail-closed gate green).` (test count up by ~22).

- [ ] **Step 6: Commit**

```bash
git add services/portfolio/construction.py tests/portfolio/test_construction.py
git commit -m "feat(portfolio): build_factor_portfolio + factor_risk_report (constrained min-variance)"
```

---

## Self-Review

**1. Spec coverage:**
- §3.1 FactorRiskModel contract → Task 1. ✓
- §3.2 factor_risk (total_covariance/exposures/decomposition) → Task 2. ✓
- §3.3 constraints (projections + Dykstra + PortfolioConstraints + InfeasibleConstraints) → Task 3. ✓
- §3.4 constrained_optimizer (projected gradient, Gershgorin step, fail-closed) → Task 4. ✓
- §3.5 construction build_factor_portfolio + factor_risk_report → Task 5. ✓
- §5 error handling (non-finite/infeasible/non-convergence) → Tasks 1,3,4 tests. ✓
- §6 testing incl. import boundary → Task 5 Step 5. ✓

**2. Placeholder scan:** Every code step is complete; no TBD/TODO. ✓

**3. Type consistency:** `min_variance_weights(cov, alpha_ids, constraints, *, loadings=None, factor_ids=None, ...)` — defined in Task 4, called in Task 5 with `loadings=B, factor_ids=list(model.factor_ids)`. `total_covariance(model, alpha_ids)` consistent (Task 2 ↔ Task 5). `PortfolioConstraints(lower, upper, budget, max_gross, factor_targets)` consistent (Task 3 ↔ Tasks 4,5). `resolve_bounds` returns `(lb, ub)` lists; optimizer uses them. `project_*`/`dykstra_project` signatures consistent. `RebalancePlan`/`TargetPosition` field names match contracts. ✓

Implementer note: the factor-neutrality test uses `budget=0.0` (dollar-neutral) — `resolve_bounds` allows `budget` in `[Σlb, Σub]` = `[-3, 3]`, so 0 is feasible; the equal-budget start `budget/n = 0` is the zero vector, a valid feasible seed.
