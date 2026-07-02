# Brinson-Fachler Attribution Primitive Design

## Context

QUANTOS roadmap Rank #5 calls for Brinson-Fachler multi-layer attribution so portfolio decisions can be explained by group allocation, selection, and residual interaction rather than only by per-alpha return and covariance risk reports.

Existing portfolio attribution in `services/portfolio/construction.py` already explains weighted return contribution and covariance risk contribution. Barra-lite diagnostics in `services/portfolio/barra_lite.py` already expose factor risk, but there is no benchmark-relative group attribution primitive.

## Decision

Build a new pure module:

- `services/portfolio/brinson.py`
- tests in `tests/portfolio/test_brinson.py`
- governance boundary test in `tests/governance/test_brinson_boundary.py`

This module is a research explainability primitive only. It does not read stores, write stores, trigger dry-run, touch pre-trade risk, import broker/execution/trading modules, or change portfolio construction.

## API

```python
brinson_fachler_attribution(
    portfolio_weights: Mapping[str, float],
    benchmark_weights: Mapping[str, float],
    asset_returns: Mapping[str, float],
    groups: Mapping[str, str],
) -> BrinsonAttributionReport
```

Return dataclasses:

- `BrinsonGroupAttribution`
  - `group`
  - `portfolio_weight`
  - `benchmark_weight`
  - `portfolio_return`
  - `benchmark_return`
  - `allocation_effect`
  - `selection_effect`
  - `interaction_effect`
  - `active_return`
- `BrinsonAttributionReport`
  - `status`: `ATTRIBUTED` or `BLOCKED`
  - `groups`: tuple of group rows, sorted by group id
  - `totals`: allocation, selection, interaction, active return, portfolio return, benchmark return
  - `reasons`: fail-closed reasons
  - `diagnostics`: coverage and identity residuals

## Formula

Use the canonical Brinson-Fachler single-period decomposition:

- Group portfolio return: weighted average of asset returns within the portfolio group.
- Group benchmark return: weighted average of asset returns within the benchmark group.
- Total benchmark return: weighted average over all benchmark assets.
- Allocation effect: `(portfolio_group_weight - benchmark_group_weight) * (benchmark_group_return - total_benchmark_return)`
- Selection effect: `benchmark_group_weight * (portfolio_group_return - benchmark_group_return)`
- Interaction effect: `(portfolio_group_weight - benchmark_group_weight) * (portfolio_group_return - benchmark_group_return)`
- Group active return: `portfolio_group_weight * portfolio_group_return - benchmark_group_weight * benchmark_group_return`

The sum of group allocation, selection, and interaction must equal total active return within numerical tolerance.

## Validation

Fail closed to `BLOCKED` when:

- any weight or return is non-finite;
- either portfolio or benchmark has no non-zero gross weight;
- a weighted asset lacks a return or group;
- portfolio and benchmark weight sums are not close to 1.0 within `1e-8`;
- the Brinson identity residual is larger than `1e-10`.

Zero-weight assets may be omitted from returns or groups. Negative weights are allowed so long as total weights sum to 1.0; this supports long-short research books.

## Multi-Layer Boundary

Rank #5 mentions multi-layer attribution. The first primitive supports a single caller-supplied group map. Multi-layer output is intentionally deferred to a later adapter that calls the pure function repeatedly for sector/style/macro group maps. This keeps the identity and failure modes clear.

## Determinism

The function has no IO, no randomness, no wall clock, no process state, and stable sorted output. Same input must produce byte-identical dataclass/dict values.

## Non-Goals

- No console endpoint or UI.
- No DB persistence.
- No connection to `DryRunStore`, `run_registry`, pre-trade, broker adapter, lifecycle, or readiness state.
- No automatic portfolio rebalance or gating decision.
- No hidden benchmark generation. The caller must provide benchmark weights explicitly.

## Tests

Tests must cover:

- exact Brinson identity on a hand-computable two-sector example;
- portfolio equal to benchmark produces zero active effects;
- negative active weights remain additive;
- missing return/group and non-finite values block;
- invalid weight sums block;
- deterministic output;
- governance scan: no network/broker/order/credential/runtime boundary imports.
