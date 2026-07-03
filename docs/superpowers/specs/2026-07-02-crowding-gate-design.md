# Crowding Gate Design

## Context

QUANTOS roadmap Rank #3 is: "拥挤门接进组合构建(fail-closed 超阈值)".
The platform is still `L2_DRY_RUN_ONLY`: research and dry-run simulation only,
with no live trading, broker connectivity, real credentials, or order
submission. Rank #1 delivered execution-realistic multi-book checks; Rank #2
delivered the first read-only Barra-lite diagnostic layer. Rank #3 should turn
crowding evidence into a reusable guardrail without changing execution,
pre-trade, broker, lifecycle, or dry-run behavior.

Approved approach: **Option A, diagnostic gate layer first**.

## Goal

Build a pure, deterministic crowding gate that evaluates a candidate portfolio
before construction or research promotion code chooses to use it.

The gate answers one narrow question:

> Given weights and caller-supplied crowding evidence, should this candidate
> book pass, warn, or be blocked because crowding is too high or unknowable?

## Non-Goals

- Do not wire the gate into `services/risk/pre_trade`.
- Do not change `services/execution/dry_run_pipeline.py`.
- Do not change broker adapter boundaries or readiness flags.
- Do not mutate alpha lifecycle/catalog state.
- Do not fetch data or depend on network sources.
- Do not claim this is a full production crowding engine. It is a first
  fail-closed diagnostic gate over supplied evidence.

## Proposed Interface

Create `services/portfolio/crowding_gate.py`.

Core data contract:

```python
CrowdingGateDecision(
    status: Literal["PASS", "WARN", "BLOCK"],
    score: float | None,
    threshold: float,
    reasons: tuple[str, ...],
    exposures: dict[str, float],
)
```

Core function:

```python
evaluate_crowding_gate(
    weights: Mapping[str, float],
    crowding_by_symbol: Mapping[str, float | None],
    *,
    threshold: float = 0.60,
    warn_threshold: float = 0.45,
    min_coverage: float = 0.80,
) -> CrowdingGateDecision
```

Semantics:

- `score` is gross-weighted average crowding over covered symbols.
- `coverage` is covered gross weight divided by total gross weight.
- Missing, non-finite, or insufficient coverage fails closed to `BLOCK`.
- `score > threshold` returns `BLOCK`.
- `warn_threshold < score <= threshold` returns `WARN`.
- `score <= warn_threshold` returns `PASS`.
- Empty or zero-gross books return `BLOCK`, not pass.
- Negative weights count by absolute weight. Crowding is a concentration risk,
  not a directional alpha signal.

Optional helper:

```python
project_symbol_crowding(
    weights: Mapping[str, float],
    factor_exposures: Mapping[str, Mapping[str, float]],
    factor_name: str = "crowding",
) -> dict[str, float]
```

This helper adapts Rank #2 Barra-lite exposure maps into the gate input. It is
pure and does not import tools or data stores.

## Integration Boundary

For this increment, integration means the gate is usable by portfolio/research
callers and covered by tests. It is not automatically inserted into existing
portfolio construction functions yet.

Reason: inserting it directly into `build_portfolio` or `build_factor_portfolio`
would change existing behavior and expand regression surface. The safer first
step is to make the fail-closed decision primitive explicit, tested, and
governance-scanned. Later increments can wire the primitive into specific
callers with separate specs.

## Safety Rules

- `services/portfolio/crowding_gate.py` must be pure Python stdlib only.
- No network, broker, SDK, credential, order, execution, or pre-trade imports.
- No IO, RNG, or wall-clock.
- No mutation of weights, catalog, lifecycle, dry-run store, or run registry.
- Unknown evidence must never pass silently.

## Tests

Create `tests/portfolio/test_crowding_gate.py`:

- high score returns `BLOCK`.
- medium score returns `WARN`.
- low score returns `PASS`.
- missing evidence below `min_coverage` returns `BLOCK`.
- `None`, NaN, or infinite evidence returns `BLOCK`.
- empty or zero-gross weights return `BLOCK`.
- short and long weights use absolute weight for score and coverage.
- helper extracts a named exposure map and ignores missing symbols
  deterministically.

Create `tests/governance/test_crowding_gate_boundary.py`:

- scan `services/portfolio/crowding_gate.py` for forbidden broker/network/HSM SDK
  imports.
- assert no order/transfer/credential source terms.
- assert no imports from `services.trading`, `services.execution`, or
  `services.risk.pre_trade`.

## Documentation

After implementation and true run evidence:

- Update `docs/PROJECT_STATE.md` with the exact behavior and verification
  evidence.
- Update `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md` Rank #3 to describe the
  delivered primitive as "crowding gate primitive landed", not full automatic
  portfolio wiring.

## Acceptance Criteria

- Targeted tests pass.
- Governance scanner passes.
- `python scripts/validate.py` passes.
- No live/broker/order/credential/readiness surface is changed.
- Gate behavior is fail-closed for unknown or insufficient evidence.

## Self-Review

- No placeholders remain.
- Scope is limited to one pure portfolio gate primitive plus tests and docs.
- The design does not claim automatic wiring into portfolio construction.
- Failure semantics are explicit: unknown evidence blocks.
