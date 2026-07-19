# Crowding Gate Implementation Plan

> **Execution:** Follow the repository's task-by-task TDD workflow. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure fail-closed crowding gate primitive for portfolio/research callers.

**Architecture:** Add `services/portfolio/crowding_gate.py` as a small stdlib-only module with a frozen decision contract and deterministic scoring. The first increment exposes the gate and tests it; it does not wire the gate into existing portfolio construction, pre-trade, dry-run, broker, lifecycle, or readiness paths.

**Tech Stack:** Python stdlib only for the service module; pytest for tests; existing governance scanner for safety boundary verification.

---

## File Structure

- Create `services/portfolio/crowding_gate.py`
  - Owns `CrowdingGateDecision`, `evaluate_crowding_gate`, and `project_symbol_crowding`.
  - Pure, deterministic, no IO/network/RNG/wall-clock.
- Create `tests/portfolio/test_crowding_gate.py`
  - TDD behavior tests for PASS/WARN/BLOCK, coverage, invalid evidence, zero-gross books, and helper extraction.
- Create `tests/governance/test_crowding_gate_boundary.py`
  - AST/string safety boundary tests for no broker/network/order/credential/pre-trade/execution/trading surface.
- Modify `docs/PROJECT_STATE.md`
  - Add the true post-run evidence after implementation passes.
- Modify `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`
  - Mark Rank #3 as landed as a primitive, not automatic portfolio wiring.

Do not modify:

- `services/risk/pre_trade/`
- `services/execution/dry_run_pipeline.py`
- `services/trading/`
- `services/alpha/lifecycle.py`
- readiness/safety config

---

## Task 1: Red Tests for Crowding Gate Behavior

**Files:**
- Create: `tests/portfolio/test_crowding_gate.py`

- [ ] **Step 1: Create the failing test file**

Create `tests/portfolio/test_crowding_gate.py` with this exact content:

```python
import math

import pytest

from services.portfolio.crowding_gate import (
    CrowdingGateDecision,
    evaluate_crowding_gate,
    project_symbol_crowding,
)


def test_high_crowding_score_blocks():
    decision = evaluate_crowding_gate(
        {"A": 0.6, "B": -0.4},
        {"A": 0.70, "B": 0.80},
        threshold=0.60,
        warn_threshold=0.45,
    )

    assert isinstance(decision, CrowdingGateDecision)
    assert decision.status == "BLOCK"
    assert decision.score == pytest.approx(0.74)
    assert decision.threshold == 0.60
    assert decision.exposures == {"A": 0.70, "B": 0.80}
    assert any("crowding_score" in reason for reason in decision.reasons)


def test_medium_crowding_score_warns():
    decision = evaluate_crowding_gate(
        {"A": 0.5, "B": -0.5},
        {"A": 0.50, "B": 0.54},
        threshold=0.60,
        warn_threshold=0.45,
    )

    assert decision.status == "WARN"
    assert decision.score == pytest.approx(0.52)
    assert any("warn_threshold" in reason for reason in decision.reasons)


def test_low_crowding_score_passes():
    decision = evaluate_crowding_gate(
        {"A": 0.5, "B": -0.5},
        {"A": 0.10, "B": 0.30},
        threshold=0.60,
        warn_threshold=0.45,
    )

    assert decision.status == "PASS"
    assert decision.score == pytest.approx(0.20)
    assert decision.reasons == ("crowding_score_ok",)


def test_missing_evidence_below_min_coverage_blocks():
    decision = evaluate_crowding_gate(
        {"A": 0.5, "B": -0.5},
        {"A": 0.10},
        min_coverage=0.80,
    )

    assert decision.status == "BLOCK"
    assert decision.score is None
    assert decision.exposures == {"A": 0.10}
    assert any("insufficient_coverage" in reason for reason in decision.reasons)


@pytest.mark.parametrize("bad_value", [None, float("nan"), float("inf"), -float("inf")])
def test_invalid_crowding_evidence_blocks(bad_value):
    decision = evaluate_crowding_gate(
        {"A": 1.0},
        {"A": bad_value},
    )

    assert decision.status == "BLOCK"
    assert decision.score is None
    assert any("invalid_crowding" in reason for reason in decision.reasons)


@pytest.mark.parametrize("weights", [{}, {"A": 0.0, "B": -0.0}])
def test_empty_or_zero_gross_books_block(weights):
    decision = evaluate_crowding_gate(weights, {"A": 0.1, "B": 0.2})

    assert decision.status == "BLOCK"
    assert decision.score is None
    assert decision.reasons == ("empty_or_zero_gross_book",)


def test_non_finite_weight_blocks():
    decision = evaluate_crowding_gate({"A": math.nan}, {"A": 0.1})

    assert decision.status == "BLOCK"
    assert decision.score is None
    assert decision.reasons == ("invalid_weight:A",)


def test_absolute_weight_drives_score_for_long_short_books():
    decision = evaluate_crowding_gate(
        {"HIGH": -0.80, "LOW": 0.20},
        {"HIGH": 0.80, "LOW": 0.00},
        threshold=0.60,
        warn_threshold=0.45,
    )

    assert decision.status == "BLOCK"
    assert decision.score == pytest.approx(0.64)


def test_config_validation_rejects_invalid_thresholds():
    with pytest.raises(ValueError):
        evaluate_crowding_gate({"A": 1.0}, {"A": 0.1}, threshold=0.40, warn_threshold=0.50)

    with pytest.raises(ValueError):
        evaluate_crowding_gate({"A": 1.0}, {"A": 0.1}, min_coverage=1.01)


def test_project_symbol_crowding_extracts_named_factor_deterministically():
    projected = project_symbol_crowding(
        {"MSFT": 0.3, "AAPL": -0.2, "MISSING": 0.5},
        {
            "crowding": {"AAPL": 0.40, "MSFT": 0.20},
            "liquidity": {"AAPL": 1.0, "MSFT": 2.0, "MISSING": 3.0},
        },
    )

    assert projected == {"AAPL": 0.40, "MSFT": 0.20}


def test_project_symbol_crowding_missing_factor_returns_empty_map():
    projected = project_symbol_crowding({"A": 1.0}, {"liquidity": {"A": 0.3}})

    assert projected == {}
```

- [ ] **Step 2: Run the red test**

Run:

```bash
rtk proxy python -m pytest tests/portfolio/test_crowding_gate.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'services.portfolio.crowding_gate'
```

Do not create `services/portfolio/crowding_gate.py` until this red test has been observed.

---

## Task 2: Implement Pure Crowding Gate

**Files:**
- Create: `services/portfolio/crowding_gate.py`
- Test: `tests/portfolio/test_crowding_gate.py`

- [ ] **Step 1: Create the implementation module**

Create `services/portfolio/crowding_gate.py` with this exact content:

```python
"""Fail-closed crowding gate for research and portfolio diagnostics.

This module is a pure decision primitive. It evaluates caller-supplied crowding
evidence for a candidate book and never touches data stores, execution, broker,
pre-trade, lifecycle, credentials, RNG, or wall clock.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

GateStatus = Literal["PASS", "WARN", "BLOCK"]
_EPS = 1e-12


@dataclass(frozen=True)
class CrowdingGateDecision:
    status: GateStatus
    score: float | None
    threshold: float
    reasons: tuple[str, ...]
    exposures: dict[str, float]


def _is_finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _validate_config(*, threshold: float, warn_threshold: float, min_coverage: float) -> None:
    if not _is_finite_number(threshold) or not _is_finite_number(warn_threshold):
        raise ValueError("thresholds must be finite numbers")
    if not 0.0 <= warn_threshold <= threshold:
        raise ValueError("warn_threshold must satisfy 0 <= warn_threshold <= threshold")
    if not _is_finite_number(min_coverage) or not 0.0 <= min_coverage <= 1.0:
        raise ValueError("min_coverage must satisfy 0 <= min_coverage <= 1")


def _block(score: float | None, threshold: float, reasons: tuple[str, ...],
           exposures: dict[str, float] | None = None) -> CrowdingGateDecision:
    return CrowdingGateDecision(
        status="BLOCK",
        score=score,
        threshold=float(threshold),
        reasons=reasons,
        exposures=dict(exposures or {}),
    )


def evaluate_crowding_gate(
    weights: Mapping[str, float],
    crowding_by_symbol: Mapping[str, float | None],
    *,
    threshold: float = 0.60,
    warn_threshold: float = 0.45,
    min_coverage: float = 0.80,
) -> CrowdingGateDecision:
    """Evaluate a long-short book against supplied crowding evidence.

    Score is an absolute-weighted average over covered names. Unknown coverage
    fails closed when it is below `min_coverage`; explicit invalid values always
    block because they indicate corrupted evidence, not simply missing evidence.
    """
    _validate_config(threshold=threshold, warn_threshold=warn_threshold, min_coverage=min_coverage)

    gross = 0.0
    abs_weights: dict[str, float] = {}
    for symbol in sorted(weights):
        weight = weights[symbol]
        if not _is_finite_number(weight):
            return _block(None, threshold, (f"invalid_weight:{symbol}",))
        abs_weight = abs(float(weight))
        if abs_weight > _EPS:
            abs_weights[symbol] = abs_weight
            gross += abs_weight

    if gross <= _EPS:
        return _block(None, threshold, ("empty_or_zero_gross_book",))

    covered_gross = 0.0
    weighted_score = 0.0
    exposures: dict[str, float] = {}
    for symbol, abs_weight in abs_weights.items():
        if symbol not in crowding_by_symbol:
            continue
        value = crowding_by_symbol[symbol]
        if not _is_finite_number(value):
            return _block(None, threshold, (f"invalid_crowding:{symbol}",), exposures)
        crowding = float(value)
        exposures[symbol] = crowding
        covered_gross += abs_weight
        weighted_score += abs_weight * crowding

    coverage = covered_gross / gross if gross > _EPS else 0.0
    if coverage + _EPS < float(min_coverage):
        return _block(
            None,
            threshold,
            (f"insufficient_coverage:{coverage:.6f}<{float(min_coverage):.6f}",),
            exposures,
        )

    score = weighted_score / covered_gross if covered_gross > _EPS else None
    if score is None:
        return _block(None, threshold, ("no_covered_crowding_evidence",), exposures)

    if score > float(threshold):
        return _block(
            score,
            threshold,
            (f"crowding_score:{score:.6f}>threshold:{float(threshold):.6f}",),
            exposures,
        )
    if score > float(warn_threshold):
        return CrowdingGateDecision(
            status="WARN",
            score=score,
            threshold=float(threshold),
            reasons=(f"crowding_score:{score:.6f}>warn_threshold:{float(warn_threshold):.6f}",),
            exposures=exposures,
        )
    return CrowdingGateDecision(
        status="PASS",
        score=score,
        threshold=float(threshold),
        reasons=("crowding_score_ok",),
        exposures=exposures,
    )


def project_symbol_crowding(
    weights: Mapping[str, float],
    factor_exposures: Mapping[str, Mapping[str, float]],
    factor_name: str = "crowding",
) -> dict[str, float]:
    """Extract the named factor exposure map for symbols present in `weights`."""
    factor = factor_exposures.get(factor_name)
    if not factor:
        return {}
    return {symbol: float(factor[symbol]) for symbol in sorted(weights) if symbol in factor}
```

- [ ] **Step 2: Run the behavior tests**

Run:

```bash
rtk proxy python -m pytest tests/portfolio/test_crowding_gate.py -q
```

Expected:

```text
20 passed
```

The exact test count may be higher if reviewers add boundary cases. Preserve the spec rule: `score > threshold` blocks, `score == threshold` warns, and `score == warn_threshold` passes.

---

## Task 3: Governance Boundary Tests

**Files:**
- Create: `tests/governance/test_crowding_gate_boundary.py`
- Test: `tests/governance/test_crowding_gate_boundary.py`

- [ ] **Step 1: Create governance test file**

Create `tests/governance/test_crowding_gate_boundary.py` with this exact content:

```python
"""Governance checks for the portfolio crowding gate boundary."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_TARGET = Path("services/portfolio/crowding_gate.py")

_FORBIDDEN_IMPORT_ROOTS = {
    "aiohttp",
    "alpaca",
    "alpaca_trade_api",
    "binance",
    "ccxt",
    "coinbase",
    "ctp",
    "fix",
    "httpx",
    "ib_insync",
    "ibapi",
    "kraken",
    "krakenex",
    "oandapyv20",
    "pkcs11",
    "pypkcs11",
    "quickfix",
    "requests",
    "socket",
    "urllib",
    "vnpy",
    "websocket",
    "websockets",
}

_SENSITIVE_SOURCE_TERMS = {
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

_FORBIDDEN_IMPORT_PREFIXES = (
    "services.trading",
    "services.execution",
    "services.risk.pre_trade",
)


def _target_path() -> Path:
    path = _ROOT / _TARGET
    if not path.exists():
        pytest.fail(f"required crowding gate target is missing: {_TARGET}")
    return path


def _read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _absolute_import_names(path: Path) -> set[str]:
    tree = ast.parse(_read_source(path), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imports.add(node.module)
            imports.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imports


def test_crowding_gate_imports_no_broker_exchange_network_or_hsm_sdks():
    roots = {name.split(".", 1)[0].lower() for name in _absolute_import_names(_target_path())}
    forbidden = sorted(roots & _FORBIDDEN_IMPORT_ROOTS)

    assert not forbidden, f"{_TARGET} imports forbidden roots: {forbidden}"


def test_crowding_gate_exposes_no_order_transfer_or_credential_surface():
    source = _read_source(_target_path()).lower()
    hits = []
    for term in sorted(_SENSITIVE_SOURCE_TERMS):
        pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
        if re.search(pattern, source):
            hits.append(term)

    assert not hits, f"{_TARGET} contains sensitive source terms: {hits}"


def test_crowding_gate_does_not_import_execution_trading_or_pre_trade_gate():
    imports = _absolute_import_names(_target_path())
    forbidden = sorted(
        name
        for name in imports
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in _FORBIDDEN_IMPORT_PREFIXES)
    )

    assert not forbidden, f"{_TARGET} imports forbidden runtime boundary modules: {forbidden}"
```

- [ ] **Step 2: Run governance boundary test**

Run:

```bash
rtk proxy python -m pytest tests/governance/test_crowding_gate_boundary.py -q
```

Expected:

```text
3 passed
```

---

## Task 4: Documentation Updates

**Files:**
- Modify: `docs/PROJECT_STATE.md`
- Modify: `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`

- [ ] **Step 1: Update `docs/PROJECT_STATE.md`**

Add this bullet near the Rank #1/#2 roadmap evidence section after implementation verification:

```markdown
- **对标顶级路线图 Rank #3 已做(2026-07-02,拥挤门 primitive)**:`services/portfolio/crowding_gate.py`
  新增纯函数 fail-closed crowding gate,输入候选权重 + caller-supplied crowding evidence,输出 `PASS/WARN/BLOCK`。
  score=按绝对权重的拥挤暴露均值;缺失覆盖不足、显式 None/NaN/inf、空 book、非有限权重均 `BLOCK`。
  诚实边界:primitive only,未自动接入 `build_portfolio`/干跑/预交易门/券商/生命周期;后续若要改变组合构建行为需单独 spec。
```

- [ ] **Step 2: Update `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`**

Replace Rank #3 row:

```markdown
| **3 ✓** | **拥挤门 primitive**(已做,fail-closed 超阈值) | 4 | P1 / S | `services/portfolio/crowding_gate.py`、`services/alpha/crowding.py` | 已落地纯函数 gate:caller-supplied crowding evidence + candidate weights → `PASS/WARN/BLOCK`;unknown/invalid/coverage不足 fail-closed 到 `BLOCK`。诚实边界:primitive only,尚未自动改写组合构建行为;后续可单独把它接入指定 research/portfolio caller。 |
```

- [ ] **Step 3: Confirm docs mention no automatic wiring**

Run:

```bash
rtk proxy powershell -NoProfile -Command "rg -n 'Rank #3|拥挤门|primitive only|未自动|fail-closed' docs/PROJECT_STATE.md docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md"
```

Expected: output includes the new `PROJECT_STATE.md` bullet and the Rank #3 roadmap row.

---

## Task 5: Verification and Commit

**Files:**
- All files from Tasks 1-4

- [ ] **Step 1: Run targeted tests**

Run:

```bash
rtk proxy python -m pytest tests/portfolio/test_crowding_gate.py tests/governance/test_crowding_gate_boundary.py -q
```

Expected:

```text
23 passed
```

- [ ] **Step 2: Run governance scanner**

Run:

```bash
rtk proxy python apps/research-console/scripts/run_paper_to_live_governance_check.py
```

Expected:

```text
Violations:    0
SCANNER PASSED: No forbidden implementation items found.
```

- [ ] **Step 3: Run full validation gate**

Run:

```bash
rtk proxy powershell -NoProfile -Command "`$env:PYTEST_ADDOPTS='-p no:cacheprovider --basetemp=H:/code/QUANTOS/data/cache/pytest-basetemp'; python scripts/validate.py"
```

Expected:

```text
VALIDATE PASSED (fail-closed gate green).
```

The exact test count may be higher than the previous `1197 passed`; verify there are zero failures.

- [ ] **Step 4: Check staged scope**

Run:

```bash
rtk proxy git status --short --branch
rtk proxy git diff --check
```

Expected:

- Only the new crowding gate files plus the two docs are modified/untracked for this task.
- Existing unrelated untracked files such as `AGENTS.md`, Claude scratchpad, and `tools/yahoo_equity_fetch.py` remain unstaged.
- `git diff --check` has no whitespace errors. Windows LF/CRLF warnings are acceptable.

- [ ] **Step 5: Commit implementation**

Run:

```bash
rtk proxy git add services/portfolio/crowding_gate.py tests/portfolio/test_crowding_gate.py tests/governance/test_crowding_gate_boundary.py docs/PROJECT_STATE.md docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md
rtk proxy git commit -m "research(roadmap#3): add fail-closed crowding gate primitive"
```

Expected:

```text
[codex/crowding-gate <sha>] research(roadmap#3): add fail-closed crowding gate primitive
```

- [ ] **Step 6: Push branch**

Run:

```bash
rtk proxy git push -u origin codex/crowding-gate
```

Expected:

```text
branch 'codex/crowding-gate' set up to track 'origin/codex/crowding-gate'
```

---

## Self-Review Checklist

- Spec coverage:
  - Pure `services/portfolio/crowding_gate.py`: Task 2.
  - PASS/WARN/BLOCK semantics: Task 1 tests and Task 2 implementation.
  - Fail-closed unknown/invalid/coverage behavior: Task 1 tests.
  - No automatic construction/pre-trade/dry-run/broker wiring: Task 4 docs and Task 3 governance.
  - Full verification: Task 5.
- Placeholder scan:
  - No TBD/TODO/fill-later placeholders.
  - Every code-producing step includes exact code.
- Type consistency:
  - `CrowdingGateDecision`, `evaluate_crowding_gate`, and `project_symbol_crowding` names match across tests and implementation.
  - Status values are exactly `PASS`, `WARN`, and `BLOCK`.
