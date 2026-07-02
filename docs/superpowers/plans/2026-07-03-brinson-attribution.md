# Brinson-Fachler Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a L2-safe pure Brinson-Fachler attribution primitive for benchmark-relative portfolio explainability.

**Architecture:** Create `services/portfolio/brinson.py` as a standalone pure module with dataclass outputs and fail-closed validation. Keep existing `construction.py` unchanged; Rank #5 is an explainability primitive, not an execution or rebalance connector.

**Tech Stack:** Python stdlib dataclasses, mappings/sequences, pytest, AST governance tests.

---

## File Structure

- Create `services/portfolio/brinson.py`: Brinson-Fachler dataclasses, validation, deterministic attribution function.
- Create `tests/portfolio/test_brinson.py`: behavior tests for identities, edge cases, and determinism.
- Create `tests/governance/test_brinson_boundary.py`: import/source boundary scanner for the new module.
- Modify `docs/PROJECT_STATE.md`: mark Rank #5 primitive landed.
- Modify `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`: mark roadmap row #5 complete.

## Task 1: RED Tests For Brinson-Fachler Math

**Files:**
- Create: `tests/portfolio/test_brinson.py`
- Later create: `services/portfolio/brinson.py`

- [ ] **Step 1: Write failing math tests**

Create `tests/portfolio/test_brinson.py` with:

```python
import math

import pytest

from services.portfolio.brinson import (
    BrinsonAttributionReport,
    brinson_fachler_attribution,
)


def _group(report: BrinsonAttributionReport, name: str):
    return {row.group: row for row in report.groups}[name]


def test_brinson_identity_on_hand_computable_two_sector_example():
    report = brinson_fachler_attribution(
        portfolio_weights={"A": 0.4, "B": 0.2, "C": 0.3, "D": 0.1},
        benchmark_weights={"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25},
        asset_returns={"A": 0.10, "B": 0.02, "C": 0.04, "D": -0.01},
        groups={"A": "growth", "B": "growth", "C": "value", "D": "value"},
    )

    assert report.status == "ATTRIBUTED"
    assert report.reasons == ()

    growth = _group(report, "growth")
    value = _group(report, "value")

    assert growth.portfolio_weight == pytest.approx(0.6)
    assert growth.benchmark_weight == pytest.approx(0.5)
    assert growth.portfolio_return == pytest.approx((0.4 * 0.10 + 0.2 * 0.02) / 0.6)
    assert growth.benchmark_return == pytest.approx((0.25 * 0.10 + 0.25 * 0.02) / 0.5)
    assert value.portfolio_weight == pytest.approx(0.4)
    assert value.benchmark_weight == pytest.approx(0.5)

    assert report.totals["portfolio_return"] == pytest.approx(0.055)
    assert report.totals["benchmark_return"] == pytest.approx(0.0375)
    assert report.totals["active_return"] == pytest.approx(0.0175)
    assert (
        report.totals["allocation_effect"]
        + report.totals["selection_effect"]
        + report.totals["interaction_effect"]
    ) == pytest.approx(report.totals["active_return"])
    assert report.diagnostics["identity_residual"] == pytest.approx(0.0, abs=1e-12)


def test_portfolio_equal_to_benchmark_has_zero_active_effects():
    weights = {"A": 0.5, "B": 0.5}
    report = brinson_fachler_attribution(
        portfolio_weights=weights,
        benchmark_weights=weights,
        asset_returns={"A": 0.08, "B": -0.02},
        groups={"A": "x", "B": "y"},
    )

    assert report.status == "ATTRIBUTED"
    assert report.totals["active_return"] == pytest.approx(0.0)
    assert report.totals["allocation_effect"] == pytest.approx(0.0)
    assert report.totals["selection_effect"] == pytest.approx(0.0)
    assert report.totals["interaction_effect"] == pytest.approx(0.0)


def test_negative_active_weights_remain_additive_for_long_short_research_books():
    report = brinson_fachler_attribution(
        portfolio_weights={"A": 1.2, "B": -0.2},
        benchmark_weights={"A": 0.8, "B": 0.2},
        asset_returns={"A": 0.03, "B": -0.04},
        groups={"A": "major", "B": "minor"},
    )

    assert report.status == "ATTRIBUTED"
    assert report.totals["active_return"] == pytest.approx(
        (1.2 * 0.03 - 0.2 * -0.04) - (0.8 * 0.03 + 0.2 * -0.04)
    )
    assert (
        report.totals["allocation_effect"]
        + report.totals["selection_effect"]
        + report.totals["interaction_effect"]
    ) == pytest.approx(report.totals["active_return"])
```

- [ ] **Step 2: Verify RED**

Run:

```bash
rtk proxy python -m pytest tests/portfolio/test_brinson.py -q
```

Expected: import failure because `services.portfolio.brinson` does not exist.

## Task 2: GREEN Core Implementation

**Files:**
- Create: `services/portfolio/brinson.py`
- Test: `tests/portfolio/test_brinson.py`

- [ ] **Step 1: Implement minimal pure module**

Create `services/portfolio/brinson.py` with dataclasses and `brinson_fachler_attribution`. The implementation must:

- convert numeric inputs to finite floats;
- validate portfolio and benchmark sums against 1.0;
- require returns and groups for all non-zero weighted assets;
- aggregate deterministic group rows by sorted group id;
- compute allocation, selection, interaction, active return, and identity residual;
- return `BLOCKED` report instead of raising for invalid user data.

- [ ] **Step 2: Verify GREEN**

Run:

```bash
rtk proxy python -m pytest tests/portfolio/test_brinson.py -q
```

Expected: all tests pass.

## Task 3: RED Edge Cases And Governance

**Files:**
- Modify: `tests/portfolio/test_brinson.py`
- Create: `tests/governance/test_brinson_boundary.py`
- Modify: `services/portfolio/brinson.py`

- [ ] **Step 1: Add fail-closed and determinism tests**

Append tests for:

```python
@pytest.mark.parametrize(
    ("portfolio_weights", "benchmark_weights", "asset_returns", "groups", "reason"),
    [
        ({"A": 0.5}, {"A": 0.5}, {"A": 0.01}, {"A": "x"}, "portfolio_weights_sum_not_one"),
        ({"A": 1.0}, {"A": 1.0}, {}, {"A": "x"}, "missing_asset_return"),
        ({"A": 1.0}, {"A": 1.0}, {"A": 0.01}, {}, "missing_asset_group"),
        ({"A": math.inf}, {"A": 1.0}, {"A": 0.01}, {"A": "x"}, "non_finite_weight"),
        ({"A": 1.0}, {"A": 1.0}, {"A": math.nan}, {"A": "x"}, "non_finite_return"),
    ],
)
def test_invalid_inputs_block(portfolio_weights, benchmark_weights, asset_returns, groups, reason):
    report = brinson_fachler_attribution(
        portfolio_weights=portfolio_weights,
        benchmark_weights=benchmark_weights,
        asset_returns=asset_returns,
        groups=groups,
    )

    assert report.status == "BLOCKED"
    assert reason in report.reasons
    assert report.groups == ()


def test_zero_weight_assets_may_omit_return_and_group():
    report = brinson_fachler_attribution(
        portfolio_weights={"A": 1.0, "Z": 0.0},
        benchmark_weights={"A": 1.0},
        asset_returns={"A": 0.02},
        groups={"A": "core"},
    )

    assert report.status == "ATTRIBUTED"
    assert report.totals["active_return"] == pytest.approx(0.0)


def test_output_is_deterministic():
    kwargs = dict(
        portfolio_weights={"B": 0.4, "A": 0.6},
        benchmark_weights={"A": 0.5, "B": 0.5},
        asset_returns={"B": -0.01, "A": 0.04},
        groups={"B": "z", "A": "a"},
    )

    assert brinson_fachler_attribution(**kwargs) == brinson_fachler_attribution(**kwargs)
```

- [ ] **Step 2: Add governance test**

Create `tests/governance/test_brinson_boundary.py` modeled on `test_capital_allocator_boundary.py`, with target `services/portfolio/brinson.py`, forbidden network/broker SDK roots, sensitive order/credential terms, and forbidden import prefixes:

```python
_FORBIDDEN_IMPORT_PREFIXES = (
    "services.trading",
    "services.execution",
    "services.risk.pre_trade",
)
```

- [ ] **Step 3: Verify RED if any behavior is missing**

Run:

```bash
rtk proxy python -m pytest tests/portfolio/test_brinson.py tests/governance/test_brinson_boundary.py -q
```

Expected before edge implementation cleanup: any missing validation fails.

- [ ] **Step 4: Update implementation until GREEN**

Run the same command until all Brinson tests and governance boundary tests pass.

## Task 4: Docs And Roadmap

**Files:**
- Modify: `docs/PROJECT_STATE.md`
- Modify: `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`

- [ ] **Step 1: Update project state**

Add a Rank #5 bullet after Rank #4 stating:

- `services/portfolio/brinson.py` is landed;
- it computes Brinson-Fachler allocation/selection/interaction attribution against caller-supplied benchmark and group map;
- it is pure research explainability only;
- it does not read/write stores, rebalance, dry-run, pre-trade, broker, lifecycle, readiness, or console.

- [ ] **Step 2: Update roadmap row**

Replace Rank #5 row with a checked row:

```markdown
| **5 ✓** | **Brinson-Fachler 归因 primitive**(已做,allocation/selection/interaction) | 4 | P2 / M | `services/portfolio/brinson.py`、`construction.py` 既有归因 | 已落地纯函数 benchmark-relative attribution: caller-supplied portfolio/benchmark weights + asset returns + group map → `ATTRIBUTED/BLOCKED`;输出 allocation/selection/interaction/active return 恒等式诊断。诚实边界:primitive only,不读写 DB、不接 dry-run/console/预交易/券商/生命周期;多层 sector/style/macro 适配器留后续。 |
```

- [ ] **Step 3: Verify docs**

Run:

```bash
rtk proxy rg -n "Rank #5|Brinson|brinson|归因 primitive|不读写 DB|dry-run" docs/PROJECT_STATE.md docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md
```

Expected: Rank #5 appears in both docs with primitive-only boundary.

## Task 5: Final Verification And Commit

**Files:**
- All touched files.

- [ ] **Step 1: Targeted tests**

Run:

```bash
rtk proxy python -m pytest tests/portfolio/test_brinson.py tests/governance/test_brinson_boundary.py tests/portfolio/test_attribution.py tests/portfolio/test_barra_lite.py -q
```

Expected: all pass.

- [ ] **Step 2: Governance scanner**

Run:

```bash
rtk proxy python apps/research-console/scripts/run_paper_to_live_governance_check.py
```

Expected: `Violations: 0`.

- [ ] **Step 3: Full gate**

Run:

```bash
rtk proxy python scripts/validate.py
```

Expected: full gate passes. If sandbox temp-dir permissions fail, rerun the same command with approved escalation and record that the failure was environmental.

- [ ] **Step 4: Commit and push**

Run:

```bash
rtk proxy git add services/portfolio/brinson.py tests/portfolio/test_brinson.py tests/governance/test_brinson_boundary.py docs/PROJECT_STATE.md docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md docs/superpowers/plans/2026-07-03-brinson-attribution.md
rtk proxy git commit -m "research(roadmap#5): add brinson attribution primitive"
rtk proxy git push -u origin codex/brinson-attribution
```
