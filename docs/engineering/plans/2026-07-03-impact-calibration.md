# Impact Calibration Primitive Implementation Plan

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a L2-safe pure impact calibration primitive that formalizes RE-1 literature/default impact coefficient assumptions.

**Architecture:** Create `services/research/realism/impact_calibration.py` as a standalone pure module layered above the existing `AlmgrenChrissImpact` model and `capacity_band` curve logic. Do not change dry-run costs, execution fills, `CostModel`, broker adapters, or console endpoints.

**Tech Stack:** Python stdlib dataclasses, pytest, AST governance tests, existing `services.research.realism.impact` and `capacity_curve`.

---

## File Structure

- Create `services/research/realism/impact_calibration.py`: dataclasses and pure helpers for default crypto impact calibration.
- Create `tests/research/test_impact_calibration.py`: behavior tests for defaults, ordering, conversion to impact model, and capacity-band integration.
- Create `tests/governance/test_impact_calibration_boundary.py`: AST/source boundary test for no network/broker/execution/order/credential surface.
- Modify `docs/PROJECT_STATE.md`: mark Rank #6 primitive landed.
- Modify `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`: mark Rank #6 complete.

## Task 1: RED Tests For Calibration Semantics

**Files:**
- Create: `tests/research/test_impact_calibration.py`
- Later create: `services/research/realism/impact_calibration.py`

- [ ] **Step 1: Write failing tests**

Create `tests/research/test_impact_calibration.py`:

```python
import pytest

from services.research.realism.capacity_curve import capacity_band
from services.research.realism.impact import AlmgrenChrissImpact
from services.research.realism.impact_calibration import (
    ImpactCalibrationScenario,
    ImpactCalibrationSet,
    default_crypto_impact_calibration,
    impact_band_from_calibration,
    scenario_impact,
    validate_calibration,
)


def test_default_crypto_calibration_is_ordered_and_labeled_uncalibrated_principled():
    calibration = default_crypto_impact_calibration()

    assert calibration.provenance == "UNCALIBRATED_PRINCIPLED"
    assert calibration.conservative.name == "conservative"
    assert calibration.base.name == "base"
    assert calibration.aggressive.name == "aggressive"
    assert calibration.conservative.temp_coef > calibration.base.temp_coef > calibration.aggressive.temp_coef
    assert calibration.conservative.perm_coef > calibration.base.perm_coef > calibration.aggressive.perm_coef
    assert calibration.conservative.temp_exponent == pytest.approx(0.5)
    assert "not venue calibrated" in calibration.source_note.lower()


def test_scenario_impact_preserves_coefficients():
    scenario = ImpactCalibrationScenario(
        name="base",
        temp_coef=10.0,
        perm_coef=5.0,
        temp_exponent=0.55,
        provenance="UNCALIBRATED_PRINCIPLED",
        note="test scenario",
    )

    impact = scenario_impact(scenario)

    assert isinstance(impact, AlmgrenChrissImpact)
    assert impact.temp_coef == pytest.approx(10.0)
    assert impact.perm_coef == pytest.approx(5.0)
    assert impact.temp_exponent == pytest.approx(0.55)


def test_impact_band_matches_capacity_band_contract():
    calibration = default_crypto_impact_calibration()
    band = impact_band_from_calibration(calibration)

    assert band == (calibration.conservative.temp_coef, calibration.aggressive.temp_coef)

    capacity = capacity_band(
        gross_returns=[0.01, 0.02, -0.005, 0.015],
        turnover_by_period=[[(0.5, 1e6)]] * 4,
        aum_grid=[1e5, 1e6, 1e7, 1e8, 1e9],
        impact_coef_lo_hi=band,
        base_impact=scenario_impact(calibration.base),
        max_participation=0.10,
    )

    assert capacity["provenance"] == "UNCALIBRATED_PRINCIPLED"
    assert capacity["aum_low"] is None or capacity["aum_high"] is None or capacity["aum_low"] <= capacity["aum_high"]


def test_invalid_calibration_fails_closed():
    bad = ImpactCalibrationSet(
        provenance="UNCALIBRATED_PRINCIPLED",
        conservative=ImpactCalibrationScenario("conservative", 5.0, 2.5, 0.5, "UNCALIBRATED_PRINCIPLED", "bad"),
        base=ImpactCalibrationScenario("base", 10.0, 5.0, 0.5, "UNCALIBRATED_PRINCIPLED", "bad"),
        aggressive=ImpactCalibrationScenario("aggressive", 20.0, 10.0, 0.5, "UNCALIBRATED_PRINCIPLED", "bad"),
        source_note="bad ordering",
    )

    with pytest.raises(ValueError, match="conservative"):
        validate_calibration(bad)


@pytest.mark.parametrize(
    "scenario",
    [
        ImpactCalibrationScenario("bad", 0.0, 1.0, 0.5, "UNCALIBRATED_PRINCIPLED", "bad"),
        ImpactCalibrationScenario("bad", 1.0, -1.0, 0.5, "UNCALIBRATED_PRINCIPLED", "bad"),
        ImpactCalibrationScenario("bad", 1.0, 1.0, 0.0, "UNCALIBRATED_PRINCIPLED", "bad"),
        ImpactCalibrationScenario("bad", 1.0, 1.0, 1.5, "UNCALIBRATED_PRINCIPLED", "bad"),
    ],
)
def test_invalid_scenario_values_fail_closed(scenario):
    with pytest.raises(ValueError):
        scenario_impact(scenario)
```

- [ ] **Step 2: Verify RED**

Run:

```bash
rtk proxy python -m pytest tests/research/test_impact_calibration.py -q
```

Expected: import failure because `services.research.realism.impact_calibration` does not exist.

## Task 2: GREEN Core Implementation

**Files:**
- Create: `services/research/realism/impact_calibration.py`
- Test: `tests/research/test_impact_calibration.py`

- [ ] **Step 1: Implement module**

Create `services/research/realism/impact_calibration.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
import math

from services.research.realism.impact import AlmgrenChrissImpact


@dataclass(frozen=True)
class ImpactCalibrationScenario:
    name: str
    temp_coef: float
    perm_coef: float
    temp_exponent: float
    provenance: str
    note: str


@dataclass(frozen=True)
class ImpactCalibrationSet:
    provenance: str
    conservative: ImpactCalibrationScenario
    base: ImpactCalibrationScenario
    aggressive: ImpactCalibrationScenario
    source_note: str
```

Add helpers:

- `validate_scenario(scenario) -> None`
- `validate_calibration(calibration) -> None`
- `default_crypto_impact_calibration() -> ImpactCalibrationSet`
- `impact_band_from_calibration(calibration) -> tuple[float, float]`
- `scenario_impact(scenario) -> AlmgrenChrissImpact`

Use coefficients:

- aggressive: temp 5.0, perm 2.5, exponent 0.5
- base: temp 10.0, perm 5.0, exponent 0.5
- conservative: temp 20.0, perm 10.0, exponent 0.5

Use provenance string `UNCALIBRATED_PRINCIPLED` and a source note containing `not venue calibrated`.

- [ ] **Step 2: Verify GREEN**

Run:

```bash
rtk proxy python -m pytest tests/research/test_impact_calibration.py -q
```

Expected: all tests pass.

## Task 3: Governance Boundary

**Files:**
- Create: `tests/governance/test_impact_calibration_boundary.py`

- [ ] **Step 1: Add governance test**

Create `tests/governance/test_impact_calibration_boundary.py` using the same AST/source pattern as `test_brinson_boundary.py`, with target:

```python
_TARGET = Path("services/research/realism/impact_calibration.py")
```

Forbidden import prefixes:

```python
_FORBIDDEN_IMPORT_PREFIXES = (
    "services.trading",
    "services.execution",
    "services.risk.pre_trade",
    "tools",
)
```

Sensitive source terms:

```python
{
    "api_key",
    "cancel_order",
    "create_order",
    "password",
    "place_order",
    "private_key",
    "secret_key",
    "submit_order",
    "token",
    "transfer",
    "withdraw",
}
```

- [ ] **Step 2: Verify governance**

Run:

```bash
rtk proxy python -m pytest tests/research/test_impact_calibration.py tests/governance/test_impact_calibration_boundary.py -q
```

Expected: all tests pass.

## Task 4: Docs And Roadmap

**Files:**
- Modify: `docs/PROJECT_STATE.md`
- Modify: `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`

- [ ] **Step 1: Update project state**

Add a Rank #6 bullet after Rank #5:

- `services/research/realism/impact_calibration.py` landed;
- default crypto scenarios are aggressive/base/conservative;
- provenance is `UNCALIBRATED_PRINCIPLED`;
- it only formalizes assumptions for sensitivity bands;
- it does not alter dry-run, fills, `CostModel`, portfolio construction, pre-trade, broker, lifecycle, readiness, or console.

- [ ] **Step 2: Update roadmap row**

Replace Rank #6 row with:

```markdown
| **6 ✓** | **冲击模型校准 primitive**(已做,文献默认 + 敏感性参数包) | 5 | P1 / M | `services/research/realism/impact_calibration.py`、`impact.py`、`capacity_curve.py` | 已落地纯函数 calibration set: aggressive/base/conservative 三情景 + `UNCALIBRATED_PRINCIPLED` provenance + 可直接传给 `capacity_band` 的系数带。诚实边界:不是 venue/tick 实测校准,不改 dry-run 成本/成交/组合/下单;真实成交校准仍需 tick/LOB/执行数据。 |
```

- [ ] **Step 3: Verify docs**

Run:

```bash
rtk proxy rg -n "Rank #6|impact_calibration|冲击模型校准|UNCALIBRATED_PRINCIPLED|dry-run 成本|capacity_band" docs/PROJECT_STATE.md docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md
```

Expected: Rank #6 appears in both docs with primitive-only boundary.

## Task 5: Final Verification And Commit

**Files:**
- All touched files.

- [ ] **Step 1: Targeted tests**

Run:

```bash
rtk proxy python -m pytest tests/research/test_impact_calibration.py tests/governance/test_impact_calibration_boundary.py tests/research/test_impact.py tests/research/test_capacity_curve.py -q
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

Expected: full gate passes. If sandbox temp-dir permissions fail with `tmp_path` setup errors, rerun the same command with approved escalation and record that the failure was environmental.

- [ ] **Step 4: Commit and push**

Run:

```bash
rtk proxy git add services/research/realism/impact_calibration.py tests/research/test_impact_calibration.py tests/governance/test_impact_calibration_boundary.py docs/PROJECT_STATE.md docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md docs/engineering/plans/2026-07-03-impact-calibration.md
rtk proxy git commit -m "research(roadmap#6): add impact calibration primitive"
rtk proxy git push -u origin codex/impact-calibration
```
