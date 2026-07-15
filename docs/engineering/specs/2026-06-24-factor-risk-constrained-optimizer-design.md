# Factor Risk Model + Constrained Optimizer — Design

Date: 2026-06-24 · Status: approved · Scope: single implementation plan
Audit driver: `docs/QUANTOS_AUDIT_2026-06.md` §6 P1 — "No factor risk model and no
constrained optimizer — only diagonal-shrinkage covariance + risk parity."

## 1. Goal

Add to the portfolio layer, as new modules (existing `risk_model.py` /
`optimizer.py` / `construction.py` untouched):
1. A **multi-factor risk model**: portfolio covariance `Σ = B·Σf·Bᵀ + diag(Δ)`
   from caller-supplied loadings `B`, factor covariance `Σf`, and specific
   variance `Δ`; with a **factor-vs-specific variance decomposition** and factor
   exposures `Bᵀw`.
2. A deterministic, pure-Python **constrained minimum-variance optimizer**:
   `min ½·wᵀΣw` subject to budget (`Σw = 1`), per-name **box bounds** (long-SHORT,
   bounds may be negative), **factor-exposure** equality (neutrality / targets
   `Bᵀw = t`), and an optional **gross-leverage** cap (`Σ|w| ≤ G`).

Constraints: L2_DRY_RUN_ONLY; pure-Python stdlib only (NO numpy/scipy/cvxpy);
deterministic (no RNG); fail-closed; the factor model is **caller-supplied** (no
real data to estimate from); the portfolio import boundary (no imports from
trading/execution/risk/broker) is preserved.

## 2. Architecture

```
quantos/contracts/factor.py       FactorRiskModel (frozen contract, validated)
services/portfolio/factor_risk.py total_covariance / factor_exposures / risk_decomposition
services/portfolio/constraints.py PortfolioConstraints + pure-Python projections + Dykstra
services/portfolio/constrained_optimizer.py  min_variance_weights (projected gradient)
services/portfolio/construction.py (MODIFY) + build_factor_portfolio(...)
```

Dependency direction: `constrained_optimizer` → `constraints` (projections) and
takes a plain `cov` matrix; `factor_risk` → `contracts/factor`; `construction`
wires `factor_risk.total_covariance` → `constrained_optimizer`. The optimizer is
decoupled from the factor model (it takes any covariance), so it is testable on
its own. No new third-party imports; import-boundary test still passes.

## 3. Components

### 3.1 `FactorRiskModel` (contract)
Frozen pydantic v2, `allow_inf_nan=False` on floats:
- `factor_ids: tuple[str, ...]` (k factors, unique, non-empty)
- `loadings: dict[str, tuple[float, ...]]` — `alpha_id -> B row` (each length k)
- `factor_cov: tuple[tuple[float, ...], ...]` — `Σf`, k×k, symmetric
- `specific_var: dict[str, float]` — `alpha_id -> Δ_ii ≥ 0`
- validator: every loading row has length k; `factor_cov` is k×k and symmetric;
  `specific_var` values ≥ 0; all finite. Helper `aligned(alpha_ids)` returns
  `(B: list[list[float]], spec: list[float])` for an ordered id list, raising if
  an id is missing (fail-closed).

### 3.2 `factor_risk.py`
- `total_covariance(model, alpha_ids) -> Matrix` = `B·Σf·Bᵀ + diag(Δ)` (pure-Python
  matmul). Symmetric, deterministic.
- `factor_exposures(weights, model, alpha_ids) -> dict[str, float]` = `Bᵀw`.
- `risk_decomposition(weights, model, alpha_ids) -> dict` with `total_var`,
  `factor_var = (Bᵀw)ᵀ Σf (Bᵀw)`, `specific_var = Σ w_i²·Δ_i`, and `by_factor`
  (each factor's contribution to `factor_var`). Identity `factor_var + specific_var
  == total_var` holds by construction (the test asserts it to 1e-9).

### 3.3 `constraints.py`
- `PortfolioConstraints` (frozen dataclass): `lower`/`upper` (scalar or
  `dict[alpha_id,float]`), `budget: float = 1.0`, `max_gross: Optional[float] = None`,
  `factor_targets: dict[str, float] = {}` (a factor id → target exposure; absent
  factors are unconstrained; `0.0` = neutral).
- Pure-Python projection primitives (each returns a new list):
  - `project_box(w, lb, ub)` — clamp.
  - `project_hyperplane(w, a, b)` — `w - a·((a·w − b)/(a·a))` (onto `a·w = b`).
  - `project_l1_ball(w, radius)` — Euclidean projection onto `Σ|w| ≤ radius`
    (Duchi 2008 sort-based algorithm; no-op if already inside).
- `dykstra_project(w, projections, *, iterations, tol)` — Dykstra's algorithm
  cycling the projection list with correction terms; converges to the projection
  onto the (convex) intersection. Deterministic; raises `InfeasibleConstraints`
  if the residual stalls above `tol` (e.g. `lb > ub`, or `Σ lb > budget > Σ ub`).

### 3.4 `constrained_optimizer.py`
- `min_variance_weights(cov, alpha_ids, constraints, *, loadings=None,
  iterations=1000, tol=1e-9) -> list[float]`:
  (`loadings`: optional `list[list[float]]` B aligned to `alpha_ids`, supplied ONLY
  when `factor_targets` is set — needed to build the factor-exposure hyperplanes;
  the objective itself uses only `cov`.)
  - Objective `½·wᵀΣw`, gradient `Σw`. Step size from a Gershgorin spectral-radius
    bound `1 / max_i Σ_j |Σ_ij|` (guarantees descent for PSD Σ; deterministic, no
    eigensolve).
  - Iteration: `w ← dykstra_project(w − step·Σw, sets)` where `sets` = budget
    hyperplane + box + one hyperplane per constrained factor (`Bᵀw row = target`)
    + (if `max_gross`) the L1 ball. Start from the feasible projection of the
    equal-budget vector.
  - **Fail-closed**: raise `ValueError` on non-finite `cov`; raise
    `InfeasibleConstraints` when the constraint set is empty; raise
    `OptimizerError` if not converged (objective still decreasing beyond `tol`
    after `iterations`). Never returns an infeasible or non-finite weight vector.
  - On success the result satisfies (to `tol`): `Σw = budget`, `lb ≤ w ≤ ub`,
    `Bᵀw = target` for constrained factors, `Σ|w| ≤ G`.

### 3.5 `construction.py` (add, do not change existing)
- `build_factor_portfolio(plan_id, portfolio_id, factor_model, alpha_ids,
  constraints, *, prev_weights=None, config=None) -> RebalancePlan`:
  `cov = total_covariance(model, ids)` → `w = min_variance_weights(cov, ids,
  constraints, loadings=model.aligned(ids)[0])` → optional `cost_aware_rebalance` vs
  `prev_weights` → `RebalancePlan` of `TargetPosition(instrument_id=alpha_id,
  target_weight=w_i)` (long-short: negative weights allowed). Plus a companion
  `factor_risk_report(model, alpha_ids, weights)` returning the §3.2 decomposition
  for explainability.

## 4. Data flow

```
FactorRiskModel (caller-supplied) ──► total_covariance ──► min_variance_weights ──► RebalancePlan
                               └────► factor_risk_report (factor/specific/by-factor + exposures)
```

## 5. Error handling — fail-closed

- Non-finite inputs (loadings/cov/specific_var) → contract validation or optimizer
  raises; never silently sanitized.
- Infeasible constraints (`lb>ub`, budget outside `[Σlb, Σub]`, contradictory
  factor targets) → `InfeasibleConstraints`, never a best-effort infeasible vector.
- Non-convergence → `OptimizerError` (loud), never a non-optimal silent return.
- Determinism: fixed step, fixed iteration order, no RNG; identical inputs →
  identical weights (a determinism test asserts byte-identical output).

## 6. Testing (tests-first)

- `tests/portfolio/test_factor_risk.py`: `total_covariance` symmetry/dimensions;
  `risk_decomposition` identity `factor_var+specific_var==total_var`;
  `factor_exposures == Bᵀw`; dimension-mismatch / missing-id fail-closed.
- `tests/portfolio/test_constraints.py`: `project_box`/`project_hyperplane`/
  `project_l1_ball` correctness; `dykstra_project` lands in the intersection
  (box ∩ budget ∩ factor) within tol; infeasible set raises.
- `tests/portfolio/test_constrained_optimizer.py`: min-variance respects budget,
  box (incl. negative bounds), factor-neutrality (`Bᵀw≈0`), and gross cap;
  achieves variance ≤ a feasible reference (equal-weight); infeasible/non-finite
  raise; determinism (two runs identical).
- `tests/portfolio/test_construction.py` (extend): `build_factor_portfolio`
  returns a normalized `RebalancePlan` whose factor exposures match the targets;
  `factor_risk_report` decomposition sums to total.
- `tests/portfolio/test_portfolio_import_boundary.py` (existing) must still pass
  (new modules import only stdlib + `quantos.contracts`).
- Wire: the new test files live under `tests/portfolio` (already in
  `scripts/validate.py`); governance scanner auto-covers the new modules.

## 7. Out of scope (YAGNI / roadmap)

Mean-variance with expected returns + risk aversion; turnover as a hard
constraint (handled by the existing `cost_aware_rebalance`); tracking-error
objective; estimating the factor model from data (none at L2 — it is supplied);
integer/round-lot constraints. This plan delivers the factor risk model + the
constrained minimum-variance long-short optimizer only.
