# Capital Allocator Design

## Context

QUANTOS roadmap Rank #4 is "multi-book capital allocation": systematize how much
AUM each strategy book should receive after research and dry-run evidence exists.

Rank #1 already showed that gross diversification did not survive execution
reality: `crowd_contra` alone beat the naive, risk-parity, and
portfolio-of-books carry/crowd blends after costs, integer lots, and volatility
targeting. Rank #2 added read-only factor diagnostics. Rank #3 added a pure
fail-closed crowding gate primitive. Rank #4 should now turn that evidence into
a deterministic capital budget recommendation without touching the execution
path.

The approved approach is Option A: build a pure offline allocation primitive
first.

## Goals

Build `services/portfolio/capital_allocator.py` as a deterministic stdlib-only
portfolio primitive.

It answers one question:

> Given return streams and optional capacity or quality constraints, what
> fraction of research AUM should each strategy book receive?

The output is a research allocation plan, not orders, not instrument weights, and
not a live-trading instruction.

## Non-Goals

- Do not read `DryRunStore`, `run_registry`, sqlite, json artifacts, or network
  sources from the portfolio module.
- Do not write snapshots or mutate any state.
- Do not import `services.execution`, `services.trading`, `services.risk`, broker
  SDKs, network clients, HSM/key libraries, or credential helpers.
- Do not connect the allocator to `build_portfolio`, dry-run scheduling,
  pre-trade gates, lifecycle promotion, readiness flags, broker adapters, or the
  console.
- Do not claim full institutional Kelly sizing. This is a conservative
  Kelly-style budget primitive for research allocation.

## API

Create `services/portfolio/capital_allocator.py` with:

```python
@dataclass(frozen=True)
class CapitalAllocatorConfig:
    method: str = "fractional_kelly"
    min_observations: int = 20
    max_weight: float = 0.70
    no_trade_band: float = 0.02
    kelly_fraction: float = 0.25
    min_positive_mean: float = 0.0


@dataclass(frozen=True)
class BookAllocation:
    book_id: str
    target_weight: float
    target_aum: float | None
    unconstrained_weight: float
    cap_weight: float | None


@dataclass(frozen=True)
class CapitalAllocationPlan:
    status: str
    allocations: tuple[BookAllocation, ...]
    turnover: float
    reasons: tuple[str, ...]
    diagnostics: dict
```

Main function:

```python
def allocate_capital(
    returns_by_book: Mapping[str, Sequence[float]],
    *,
    total_aum: float | None = None,
    capacity_by_book: Mapping[str, float] | None = None,
    quality_multiplier_by_book: Mapping[str, float] | None = None,
    prev_weights: Mapping[str, float] | None = None,
    config: CapitalAllocatorConfig | None = None,
) -> CapitalAllocationPlan:
    ...
```

`status` is one of:

- `ALLOCATED`: weights are valid and sum to 1 within numerical tolerance.
- `BLOCKED`: evidence or constraints are insufficient; allocations are empty.

## Allocation Method

The first implementation uses conservative fractional-Kelly-style scores:

1. Validate every book id and return value.
2. Require at least `min_observations` finite returns per book.
3. Compute per-book arithmetic mean and population variance.
4. Compute raw score:

   ```text
   raw_score = max(mean - min_positive_mean, 0) / variance
   ```

5. Apply `kelly_fraction`.
6. Apply optional `quality_multiplier_by_book`, where each multiplier must be
   finite and in `[0, 1]`.
7. Normalize positive scores to sum to 1.
8. If no book has positive score after quality multipliers, return `BLOCKED`
   with `no_positive_kelly_score`.

This intentionally under-allocates weak or negative books. It should therefore
select `crowd_contra` over carry when supplied with the dry-run evidence that
Rank #1 documented, but it must not hard-code any strategy name or conclusion.

## Capacity Caps

If `capacity_by_book` and `total_aum` are supplied, each dollar capacity becomes:

```text
cap_weight = capacity_by_book[book_id] / total_aum
```

Rules:

- A missing capacity means no cap for that book.
- A negative, NaN, or infinite capacity blocks the plan.
- `total_aum <= 0` blocks the plan when capacities are supplied.
- If the sum of all effective caps across allocatable books is less than 1, the
  plan blocks with `infeasible_capacity_caps`.
- Otherwise, weights are capped and excess is redistributed to uncapped room.

The cap logic is deterministic and pure. It never changes the underlying book
weights or dry-run configuration.

## Previous Weights

If `prev_weights` is supplied, use the existing `cost_aware_rebalance` helper to
apply `config.no_trade_band`. The output `turnover` is the sum of absolute moves
from previous book-level AUM weights.

Invalid previous weights block the plan instead of being silently ignored.

## Diagnostics

`diagnostics` should include:

- `method`
- `book_stats`: per book mean, variance, volatility, raw score, adjusted score,
  quality multiplier, and observation count
- `capacity_caps`: per book effective cap weight when available
- `risk_contribution_pct`: computed from existing portfolio covariance/risk
  helpers for the final weights when possible

The diagnostics are for research explainability only.

## Fail-Closed Rules

Return `BLOCKED` with empty allocations when:

- `returns_by_book` is empty.
- Any book id is empty.
- Any return value is non-numeric, NaN, or infinite.
- Any book has fewer than `min_observations` returns.
- Any variance is zero or non-finite.
- Any quality multiplier is non-finite or outside `[0, 1]`.
- Capacity caps are invalid or infeasible.
- Previous weights are invalid.
- The final allocation cannot be normalized to sum to 1.

Use explicit reason strings such as `empty_returns_by_book`,
`insufficient_observations:<book>`, `invalid_return:<book>`,
`zero_variance:<book>`, `invalid_quality_multiplier:<book>`,
`invalid_capacity:<book>`, `infeasible_capacity_caps`, and
`no_positive_kelly_score`.

## File Boundaries

Create:

- `services/portfolio/capital_allocator.py`
- `tests/portfolio/test_capital_allocator.py`
- `tests/governance/test_capital_allocator_boundary.py`

Modify after implementation:

- `docs/PROJECT_STATE.md`
- `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`

The implementation file must remain pure portfolio code. It may import existing
helpers from:

- `services.portfolio.optimizer`
- `services.portfolio.risk_model`

It must not import outside `services.portfolio` except stdlib typing/dataclasses
and math.

## Tests

Behavior tests should cover:

- A strong positive-return, lower-risk book receives more weight than a weaker
  book.
- Negative or zero-edge books receive no capital when other books have positive
  scores.
- All-negative evidence blocks with `no_positive_kelly_score`.
- Capacity caps are respected and excess is redistributed.
- Infeasible capacity caps block.
- Quality multipliers reduce allocation and invalid multipliers block.
- Previous weights inside the no-trade band suppress turnover.
- Empty input, short histories, non-finite returns, zero variance, invalid AUM,
  invalid capacity, and invalid previous weights block.
- Output is deterministic across repeated calls.
- Risk diagnostics are present for valid plans.

Governance tests should scan `services/portfolio/capital_allocator.py` and assert:

- No broker, exchange, network, HSM, credential, order, transfer, or live-trading
  surface.
- No imports from `services.execution`, `services.trading`, or
  `services.risk`.

## Completion Criteria

- Targeted tests pass:

  ```bash
  rtk proxy python -m pytest tests/portfolio/test_capital_allocator.py tests/governance/test_capital_allocator_boundary.py -q
  ```

- Governance scanner passes:

  ```bash
  rtk proxy python apps/research-console/scripts/run_paper_to_live_governance_check.py
  ```

- Full fail-closed gate passes:

  ```bash
  rtk proxy python scripts/validate.py
  ```

- Roadmap Rank #4 is marked as a landed primitive, with an explicit boundary:
  it recommends research AUM weights only and does not automatically rebalance,
  dry-run, promote, submit, or arm anything.

## Self-Review

- Completeness scan: no unresolved blanks.
- Internal consistency: the API, fail-closed rules, and tests all describe the
  same first increment.
- Scope check: this is a single pure portfolio primitive; dry-run-store readers
  and console snapshots are intentionally separate future work.
- Ambiguity check: capacity and quality inputs are optional, but invalid supplied
  values block rather than being ignored.
