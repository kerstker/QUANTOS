# Capital Allocator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pure fail-closed book-level capital allocator that recommends research AUM weights across strategy books.

**Architecture:** Add `services/portfolio/capital_allocator.py` as a deterministic stdlib-only portfolio primitive. It consumes caller-supplied return streams plus optional capacity, quality, and previous-weight inputs; it returns a dataclass allocation plan and diagnostics, without reading stores or touching execution, trading, risk, readiness, broker, or console paths.

**Tech Stack:** Python stdlib dataclasses/math/typing, existing `services.portfolio.optimizer.cost_aware_rebalance`, existing `services.portfolio.risk_model` diagnostics, pytest, AST governance tests.

---

## File Structure

- Create `tests/portfolio/test_capital_allocator.py`: behavior-first tests for allocation, caps, quality multipliers, previous weights, diagnostics, determinism, and fail-closed evidence handling.
- Create `tests/governance/test_capital_allocator_boundary.py`: AST and source scan for portfolio boundary invariants.
- Create `services/portfolio/capital_allocator.py`: pure allocation dataclasses and `allocate_capital`.
- Modify `docs/PROJECT_STATE.md`: add Rank #4 landed primitive note after the Rank #3 note.
- Modify `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`: mark Rank #4 as landed as a primitive.

## Task 1: Behavior Tests

**Files:**
- Create: `tests/portfolio/test_capital_allocator.py`
- Test: `tests/portfolio/test_capital_allocator.py`

- [ ] **Step 1: Write the failing behavior tests**

Create `tests/portfolio/test_capital_allocator.py` with this exact content:

```python
import math

import pytest

from services.portfolio.capital_allocator import (
    CapitalAllocationPlan,
    CapitalAllocatorConfig,
    allocate_capital,
)


def _series(mean: float, amp: float = 0.001, n: int = 40) -> tuple[float, ...]:
    return tuple(mean + (amp if i % 2 == 0 else -amp) for i in range(n))


def _weights(plan: CapitalAllocationPlan) -> dict[str, float]:
    return {allocation.book_id: allocation.target_weight for allocation in plan.allocations}


def _allocations(plan: CapitalAllocationPlan):
    return {allocation.book_id: allocation for allocation in plan.allocations}


def test_stronger_lower_risk_book_receives_more_capital():
    plan = allocate_capital(
        {
            "crowd_contra": _series(0.004, 0.001),
            "carry": _series(0.001, 0.002),
        }
    )

    weights = _weights(plan)
    assert plan.status == "ALLOCATED"
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights["crowd_contra"] > weights["carry"]
    assert max(weights.values()) <= 0.70 + 1e-12
    assert plan.reasons == ("capital_allocated",)


def test_negative_or_zero_edge_books_receive_no_capital_when_positive_books_exist():
    plan = allocate_capital(
        {
            "positive": _series(0.003, 0.001),
            "zero_edge": _series(0.0, 0.001),
            "negative": _series(-0.002, 0.001),
        },
        config=CapitalAllocatorConfig(max_weight=1.0),
    )

    weights = _weights(plan)
    assert plan.status == "ALLOCATED"
    assert weights["positive"] == pytest.approx(1.0)
    assert weights["zero_edge"] == pytest.approx(0.0)
    assert weights["negative"] == pytest.approx(0.0)


def test_all_negative_evidence_blocks():
    plan = allocate_capital(
        {
            "a": _series(-0.001, 0.001),
            "b": _series(-0.002, 0.001),
        },
        config=CapitalAllocatorConfig(max_weight=1.0),
    )

    assert plan.status == "BLOCKED"
    assert plan.allocations == ()
    assert plan.reasons == ("no_positive_kelly_score",)


def test_capacity_caps_are_respected_and_excess_is_redistributed():
    plan = allocate_capital(
        {
            "strong": _series(0.004, 0.001),
            "backup": _series(0.002, 0.001),
        },
        total_aum=100.0,
        capacity_by_book={"strong": 40.0},
        config=CapitalAllocatorConfig(max_weight=1.0),
    )

    weights = _weights(plan)
    allocs = _allocations(plan)
    assert plan.status == "ALLOCATED"
    assert weights["strong"] == pytest.approx(0.40)
    assert weights["backup"] == pytest.approx(0.60)
    assert allocs["strong"].target_aum == pytest.approx(40.0)
    assert allocs["strong"].cap_weight == pytest.approx(0.40)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_infeasible_capacity_caps_block():
    plan = allocate_capital(
        {
            "a": _series(0.003, 0.001),
            "b": _series(0.002, 0.001),
        },
        total_aum=100.0,
        capacity_by_book={"a": 40.0, "b": 50.0},
        config=CapitalAllocatorConfig(max_weight=1.0),
    )

    assert plan.status == "BLOCKED"
    assert plan.reasons == ("infeasible_capacity_caps",)


def test_quality_multiplier_reduces_allocation_and_invalid_multiplier_blocks():
    plan = allocate_capital(
        {
            "strong": _series(0.004, 0.001),
            "backup": _series(0.002, 0.001),
        },
        quality_multiplier_by_book={"strong": 0.0},
        config=CapitalAllocatorConfig(max_weight=1.0),
    )

    weights = _weights(plan)
    assert plan.status == "ALLOCATED"
    assert weights["strong"] == pytest.approx(0.0)
    assert weights["backup"] == pytest.approx(1.0)

    blocked = allocate_capital(
        {"a": _series(0.001, 0.001), "b": _series(0.002, 0.001)},
        quality_multiplier_by_book={"a": math.nan},
    )
    assert blocked.status == "BLOCKED"
    assert blocked.reasons == ("invalid_quality_multiplier:a",)


def test_previous_weights_inside_no_trade_band_suppress_turnover():
    plan = allocate_capital(
        {
            "a": _series(0.002, 0.001),
            "b": _series(0.002, 0.001),
        },
        prev_weights={"a": 0.51, "b": 0.49},
        config=CapitalAllocatorConfig(max_weight=1.0, no_trade_band=0.02),
    )

    weights = _weights(plan)
    assert plan.status == "ALLOCATED"
    assert weights == pytest.approx({"a": 0.51, "b": 0.49})
    assert plan.turnover == pytest.approx(0.0)


@pytest.mark.parametrize(
    "returns_by_book, kwargs, expected_reason",
    [
        ({}, {}, "empty_returns_by_book"),
        ({"": _series(0.001, 0.001)}, {}, "invalid_book_id:"),
        ({"a": _series(0.001, 0.001, n=5)}, {}, "insufficient_observations:a"),
        ({"a": (0.001,) * 40}, {}, "zero_variance:a"),
        ({"a": tuple([0.001] * 39 + [math.inf])}, {}, "invalid_return:a"),
        ({"a": _series(0.001, 0.001)}, {"total_aum": -1.0}, "invalid_total_aum"),
        (
            {"a": _series(0.001, 0.001), "b": _series(0.002, 0.001)},
            {"total_aum": 100.0, "capacity_by_book": {"a": -1.0}},
            "invalid_capacity:a",
        ),
        (
            {"a": _series(0.001, 0.001), "b": _series(0.002, 0.001)},
            {"prev_weights": {"a": math.nan, "b": 1.0}},
            "invalid_prev_weight:a",
        ),
        (
            {"a": _series(0.001, 0.001), "b": _series(0.002, 0.001)},
            {"prev_weights": {"a": -0.1, "b": 1.1}},
            "invalid_prev_weight:a",
        ),
    ],
)
def test_invalid_inputs_block(returns_by_book, kwargs, expected_reason):
    plan = allocate_capital(returns_by_book, **kwargs)

    assert plan.status == "BLOCKED"
    assert plan.allocations == ()
    assert plan.reasons == (expected_reason,)


def test_output_is_deterministic():
    inputs = {
        "a": _series(0.003, 0.001),
        "b": _series(0.002, 0.001),
        "c": _series(0.001, 0.001),
    }

    first = allocate_capital(inputs, total_aum=1_000_000.0)
    second = allocate_capital(inputs, total_aum=1_000_000.0)

    assert first == second


def test_valid_plan_includes_research_diagnostics():
    plan = allocate_capital(
        {
            "a": _series(0.003, 0.001),
            "b": _series(0.002, 0.001),
        },
        total_aum=1_000_000.0,
    )

    assert plan.status == "ALLOCATED"
    assert plan.diagnostics["method"] == "fractional_kelly"
    assert set(plan.diagnostics["book_stats"]) == {"a", "b"}
    assert set(plan.diagnostics["risk_contribution_pct"]) == {"a", "b"}
    for stats in plan.diagnostics["book_stats"].values():
        assert {"mean", "variance", "volatility", "raw_score", "adjusted_score", "quality_multiplier", "observations"} <= set(stats)
```

- [ ] **Step 2: Run behavior tests to verify RED**

Run:

```bash
rtk proxy python -m pytest tests/portfolio/test_capital_allocator.py -q
```

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'services.portfolio.capital_allocator'`.

Do not create `services/portfolio/capital_allocator.py` until this red failure has been observed.

## Task 2: Governance Boundary Tests

**Files:**
- Create: `tests/governance/test_capital_allocator_boundary.py`
- Test: `tests/governance/test_capital_allocator_boundary.py`

- [ ] **Step 1: Write the failing governance tests**

Create `tests/governance/test_capital_allocator_boundary.py` with this exact content:

```python
"""Governance checks for the portfolio capital allocator boundary."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_TARGET = Path("services/portfolio/capital_allocator.py")

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
        pytest.fail(f"required capital allocator target is missing: {_TARGET}")
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


def test_capital_allocator_imports_no_broker_exchange_network_or_hsm_sdks():
    roots = {name.split(".", 1)[0].lower() for name in _absolute_import_names(_target_path())}
    forbidden = sorted(roots & _FORBIDDEN_IMPORT_ROOTS)

    assert not forbidden, f"{_TARGET} imports forbidden roots: {forbidden}"


def test_capital_allocator_exposes_no_order_transfer_or_credential_surface():
    source = _read_source(_target_path()).lower()
    hits = []
    for term in sorted(_SENSITIVE_SOURCE_TERMS):
        pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
        if re.search(pattern, source):
            hits.append(term)

    assert not hits, f"{_TARGET} contains sensitive source terms: {hits}"


def test_capital_allocator_does_not_import_execution_trading_or_pre_trade_gate():
    imports = _absolute_import_names(_target_path())
    forbidden = sorted(
        name
        for name in imports
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in _FORBIDDEN_IMPORT_PREFIXES)
    )

    assert not forbidden, f"{_TARGET} imports forbidden runtime boundary modules: {forbidden}"
```

- [ ] **Step 2: Run governance tests to verify RED**

Run:

```bash
rtk proxy python -m pytest tests/governance/test_capital_allocator_boundary.py -q
```

Expected: FAIL with `required capital allocator target is missing: services/portfolio/capital_allocator.py`.

## Task 3: Capital Allocator Core

**Files:**
- Create: `services/portfolio/capital_allocator.py`
- Test: `tests/portfolio/test_capital_allocator.py`
- Test: `tests/governance/test_capital_allocator_boundary.py`

- [ ] **Step 1: Implement the pure allocator**

Create `services/portfolio/capital_allocator.py` with this exact content:

```python
"""Book-level research capital allocation primitive.

This module recommends research AUM weights across strategy books from
caller-supplied evidence. It is pure portfolio code: no stores, no execution
paths, no trading adapters, no credentials, and no network.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

from .optimizer import cost_aware_rebalance
from .risk_model import covariance_matrix, percent_risk_contributions, shrink_covariance


ALLOCATED = "ALLOCATED"
BLOCKED = "BLOCKED"
_EPS = 1e-12


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


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _blocked(reason: str, diagnostics: dict | None = None) -> CapitalAllocationPlan:
    return CapitalAllocationPlan(
        status=BLOCKED,
        allocations=(),
        turnover=0.0,
        reasons=(reason,),
        diagnostics=diagnostics or {},
    )


def _validate_config(config: CapitalAllocatorConfig) -> str | None:
    if config.method != "fractional_kelly":
        return f"invalid_method:{config.method}"
    if not isinstance(config.min_observations, int) or config.min_observations < 2:
        return "invalid_min_observations"
    if not _is_finite_number(config.max_weight) or not (0.0 < float(config.max_weight) <= 1.0):
        return "invalid_max_weight"
    if not _is_finite_number(config.no_trade_band) or float(config.no_trade_band) < 0.0:
        return "invalid_no_trade_band"
    if not _is_finite_number(config.kelly_fraction) or float(config.kelly_fraction) <= 0.0:
        return "invalid_kelly_fraction"
    if not _is_finite_number(config.min_positive_mean):
        return "invalid_min_positive_mean"
    return None


def _stats(values: Sequence[float]) -> tuple[float, float, float]:
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return mean, variance, math.sqrt(max(variance, 0.0))


def _validate_total_aum(total_aum: float | None) -> str | None:
    if total_aum is None:
        return None
    if not _is_finite_number(total_aum) or float(total_aum) <= 0.0:
        return "invalid_total_aum"
    return None


def _effective_caps(
    book_ids: Sequence[str],
    *,
    total_aum: float | None,
    capacity_by_book: Mapping[str, float] | None,
    config: CapitalAllocatorConfig,
) -> tuple[dict[str, float], dict[str, float], str | None]:
    caps = {book_id: float(config.max_weight) for book_id in book_ids}
    capacity_caps: dict[str, float] = {}
    if capacity_by_book is None:
        return caps, capacity_caps, None

    aum_reason = _validate_total_aum(total_aum)
    if aum_reason is not None:
        return caps, capacity_caps, aum_reason

    known = set(book_ids)
    for book_id, capacity in capacity_by_book.items():
        if book_id not in known:
            return caps, capacity_caps, f"unknown_capacity:{book_id}"
        if not _is_finite_number(capacity) or float(capacity) < 0.0:
            return caps, capacity_caps, f"invalid_capacity:{book_id}"
        cap_weight = float(capacity) / float(total_aum)
        capacity_caps[book_id] = cap_weight
        caps[book_id] = min(caps[book_id], cap_weight)

    return caps, capacity_caps, None


def _apply_caps(
    weights: Mapping[str, float],
    caps: Mapping[str, float],
    eligible: Sequence[str],
) -> tuple[dict[str, float], str | None]:
    eligible = tuple(eligible)
    if sum(caps[book_id] for book_id in eligible) < 1.0 - 1e-10:
        return {}, "infeasible_capacity_caps"

    result = {book_id: 0.0 for book_id in weights}
    fixed: dict[str, float] = {}
    free = set(eligible)
    current = {book_id: float(weights[book_id]) for book_id in eligible}

    for _ in range(len(eligible) + 1):
        capped = sorted(book_id for book_id in free if current[book_id] > caps[book_id] + 1e-12)
        if not capped:
            break
        for book_id in capped:
            fixed[book_id] = float(caps[book_id])
            free.remove(book_id)
        remaining_total = 1.0 - sum(fixed.values())
        if remaining_total < -1e-10:
            return {}, "infeasible_capacity_caps"
        if not free:
            if abs(remaining_total) <= 1e-10:
                break
            return {}, "infeasible_capacity_caps"
        free_score = sum(float(weights[book_id]) for book_id in free)
        if free_score <= _EPS:
            return {}, "infeasible_capacity_caps"
        for book_id in free:
            current[book_id] = remaining_total * float(weights[book_id]) / free_score

    for book_id, weight in fixed.items():
        result[book_id] = weight
    for book_id in free:
        result[book_id] = current[book_id]

    total = sum(result.values())
    if total <= _EPS:
        return {}, "no_positive_kelly_score"
    normalized = {book_id: weight / total for book_id, weight in result.items()}
    for book_id in eligible:
        if normalized[book_id] > caps[book_id] + 1e-10:
            return {}, "infeasible_capacity_caps"
    return normalized, None


def _risk_diagnostics(
    returns_by_book: Mapping[str, Sequence[float]],
    weights: Mapping[str, float],
    min_observations: int,
) -> dict[str, float]:
    ids, cov = covariance_matrix(dict(returns_by_book), min_overlap=min_observations)
    cov = shrink_covariance(cov, intensity=0.2)
    vector = [weights.get(book_id, 0.0) for book_id in ids]
    prc = percent_risk_contributions(vector, cov)
    return {ids[i]: prc[i] for i in range(len(ids))}


def _validate_prev_weights(prev_weights: Mapping[str, float], known: set[str]) -> str | None:
    total = 0.0
    for book_id, weight in prev_weights.items():
        if book_id not in known:
            return f"unknown_prev_weight:{book_id}"
        if not _is_finite_number(weight) or float(weight) < 0.0:
            return f"invalid_prev_weight:{book_id}"
        total += float(weight)
    if abs(total - 1.0) > 1e-6:
        return "invalid_prev_weight_sum"
    return None


def allocate_capital(
    returns_by_book: Mapping[str, Sequence[float]],
    *,
    total_aum: float | None = None,
    capacity_by_book: Mapping[str, float] | None = None,
    quality_multiplier_by_book: Mapping[str, float] | None = None,
    prev_weights: Mapping[str, float] | None = None,
    config: CapitalAllocatorConfig | None = None,
) -> CapitalAllocationPlan:
    config = config or CapitalAllocatorConfig()
    config_reason = _validate_config(config)
    if config_reason is not None:
        return _blocked(config_reason)

    aum_reason = _validate_total_aum(total_aum)
    if aum_reason is not None:
        return _blocked(aum_reason)

    if not returns_by_book:
        return _blocked("empty_returns_by_book")

    book_ids = sorted(returns_by_book)
    for book_id in book_ids:
        if not isinstance(book_id, str) or not book_id.strip():
            return _blocked(f"invalid_book_id:{book_id}")

    known = set(book_ids)
    quality = {book_id: 1.0 for book_id in book_ids}
    if quality_multiplier_by_book is not None:
        for book_id, multiplier in quality_multiplier_by_book.items():
            if book_id not in known:
                return _blocked(f"unknown_quality_multiplier:{book_id}")
            if (
                not _is_finite_number(multiplier)
                or float(multiplier) < 0.0
                or float(multiplier) > 1.0
            ):
                return _blocked(f"invalid_quality_multiplier:{book_id}")
            quality[book_id] = float(multiplier)

    book_stats: dict[str, dict[str, float | int]] = {}
    scores: dict[str, float] = {}
    clean_returns: dict[str, tuple[float, ...]] = {}
    for book_id in book_ids:
        values: list[float] = []
        for value in returns_by_book[book_id]:
            if not _is_finite_number(value):
                return _blocked(f"invalid_return:{book_id}")
            values.append(float(value))
        if len(values) < config.min_observations:
            return _blocked(f"insufficient_observations:{book_id}")
        mean, variance, volatility = _stats(values)
        if not math.isfinite(variance) or variance <= _EPS:
            return _blocked(f"zero_variance:{book_id}")
        raw_score = max(mean - float(config.min_positive_mean), 0.0) / variance
        adjusted_score = raw_score * float(config.kelly_fraction) * quality[book_id]
        clean_returns[book_id] = tuple(values)
        scores[book_id] = adjusted_score
        book_stats[book_id] = {
            "mean": mean,
            "variance": variance,
            "volatility": volatility,
            "raw_score": raw_score,
            "adjusted_score": adjusted_score,
            "quality_multiplier": quality[book_id],
            "observations": len(values),
        }

    eligible = tuple(book_id for book_id in book_ids if scores[book_id] > _EPS)
    if not eligible:
        return _blocked("no_positive_kelly_score", {"book_stats": book_stats})

    caps, capacity_caps, cap_reason = _effective_caps(
        book_ids,
        total_aum=total_aum,
        capacity_by_book=capacity_by_book,
        config=config,
    )
    if cap_reason is not None:
        return _blocked(cap_reason, {"book_stats": book_stats})

    score_total = sum(scores[book_id] for book_id in eligible)
    unconstrained = {book_id: 0.0 for book_id in book_ids}
    for book_id in eligible:
        unconstrained[book_id] = scores[book_id] / score_total

    capped_weights, blocked_reason = _apply_caps(unconstrained, caps, eligible)
    if blocked_reason is not None:
        return _blocked(
            blocked_reason,
            {
                "method": config.method,
                "book_stats": book_stats,
                "capacity_caps": capacity_caps,
                "effective_caps": caps,
            },
        )

    turnover = sum(abs(capped_weights.get(book_id, 0.0)) for book_id in book_ids)
    final_weights = dict(capped_weights)
    if prev_weights is not None:
        prev_reason = _validate_prev_weights(prev_weights, known)
        if prev_reason is not None:
            return _blocked(prev_reason, {"book_stats": book_stats})
        final_weights, turnover = cost_aware_rebalance(
            dict(prev_weights),
            capped_weights,
            no_trade_band=float(config.no_trade_band),
        )
        final_weights = {book_id: final_weights.get(book_id, 0.0) for book_id in book_ids}
        for book_id in eligible:
            if final_weights[book_id] > caps[book_id] + 1e-10:
                return _blocked("post_rebalance_cap_violation", {"book_stats": book_stats})

    total_weight = sum(final_weights.values())
    if not math.isfinite(total_weight) or abs(total_weight - 1.0) > 1e-8:
        return _blocked("allocation_not_normalized", {"book_stats": book_stats})

    risk_contribution_pct = _risk_diagnostics(
        clean_returns,
        final_weights,
        config.min_observations,
    )
    diagnostics = {
        "method": config.method,
        "book_stats": book_stats,
        "capacity_caps": capacity_caps,
        "effective_caps": caps,
        "risk_contribution_pct": risk_contribution_pct,
    }

    allocations = tuple(
        BookAllocation(
            book_id=book_id,
            target_weight=final_weights[book_id],
            target_aum=(final_weights[book_id] * float(total_aum) if total_aum is not None else None),
            unconstrained_weight=unconstrained[book_id],
            cap_weight=caps[book_id],
        )
        for book_id in book_ids
    )
    return CapitalAllocationPlan(
        status=ALLOCATED,
        allocations=allocations,
        turnover=turnover,
        reasons=("capital_allocated",),
        diagnostics=diagnostics,
    )
```

- [ ] **Step 2: Run behavior and governance tests to verify GREEN**

Run:

```bash
rtk proxy python -m pytest tests/portfolio/test_capital_allocator.py tests/governance/test_capital_allocator_boundary.py -q
```

Expected: PASS for all capital allocator behavior and governance tests.

- [ ] **Step 3: Run adjacent portfolio tests**

Run:

```bash
rtk proxy python -m pytest tests/portfolio/test_optimizer.py tests/portfolio/test_construction.py tests/portfolio/test_portfolio_import_boundary.py -q
```

Expected: PASS. This checks the new allocator did not disturb existing portfolio construction and boundary rules.

- [ ] **Step 4: Commit core implementation**

Run:

```bash
rtk proxy git add services/portfolio/capital_allocator.py tests/portfolio/test_capital_allocator.py tests/governance/test_capital_allocator_boundary.py
rtk proxy git commit -m "research(roadmap#4): add fail-closed capital allocator primitive"
```

Expected: one commit containing only the allocator module and its behavior/governance tests.

## Task 4: Roadmap Documentation

**Files:**
- Modify: `docs/PROJECT_STATE.md`
- Modify: `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`
- Test: `docs/PROJECT_STATE.md`
- Test: `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`

- [ ] **Step 1: Update project state**

In `docs/PROJECT_STATE.md`, add this bullet immediately after the existing Rank #3 crowding-gate bullet:

```markdown
- **对标顶级路线图 Rank #4 已做(2026-07-02,多 book 资本配置 primitive)**:`services/portfolio/capital_allocator.py`
  新增纯函数 fail-closed capital allocator,输入多策略收益流 + 可选容量/质量/前次权重,输出 `ALLOCATED/BLOCKED`
  研究 AUM 权重建议、turnover 与诊断(`book_stats`/capacity/effective caps/risk contribution)。默认使用保守
  fractional-Kelly-style score(`max(mean-min_positive_mean,0)/variance`)并叠加 per-book cap、capacity cap 与
  no-trade band。诚实边界:研究 allocation primitive only,不读 `DryRunStore`/`run_registry`,不写 DB,不自动调仓,
  不接 dry-run/pre-trade/broker/lifecycle/readiness/console;治理测试钉死无网络/券商/订单/凭证 surface。
```

- [ ] **Step 2: Update top-tier roadmap Rank #4**

In `docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md`, replace the Rank #4 row with:

```markdown
| **4 ✓** | **多 book 资本配置 primitive**(已做,fail-closed fractional-Kelly-style AUM budget) | 4/8 | P1 / M | `services/portfolio/capital_allocator.py`、优化器、风险模型 | 已落地纯函数 allocator:多策略收益流 + 可选容量/质量/前次权重 → `ALLOCATED/BLOCKED` 研究 AUM 权重建议;容量/质量/历史证据异常 fail-closed。诚实边界:primitive only,不读 dry-run/run_registry,不自动调仓/干跑/晋级/下单;后续可单独做 `tools/` 读取 dry-run 历史生成 snapshot。 |
```

- [ ] **Step 3: Verify docs mention the new boundary**

Run:

```bash
rtk proxy rg -n "Rank #4|capital allocator|资本配置|primitive only|不自动调仓|DryRunStore|run_registry" docs/PROJECT_STATE.md docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md
```

Expected: output includes the new `PROJECT_STATE.md` bullet and the updated Rank #4 roadmap row.

- [ ] **Step 4: Commit docs**

Run:

```bash
rtk proxy git add docs/PROJECT_STATE.md docs/QUANTOS_TOPTIER_REMEDIATION_ROADMAP.md
rtk proxy git commit -m "docs(roadmap): mark capital allocator primitive landed"
```

Expected: one docs-only commit.

## Task 5: Final Verification

**Files:**
- Verify: full repository gate

- [ ] **Step 1: Run targeted tests**

Run:

```bash
rtk proxy python -m pytest tests/portfolio/test_capital_allocator.py tests/governance/test_capital_allocator_boundary.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run governance scanner**

Run:

```bash
rtk proxy python apps/research-console/scripts/run_paper_to_live_governance_check.py
```

Expected: scanner reports zero violations.

- [ ] **Step 3: Run full fail-closed gate**

Run:

```bash
rtk proxy python scripts/validate.py
```

Expected: `VALIDATE PASSED`.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
rtk proxy git status --short --branch
rtk proxy git log --oneline -5
```

Expected: only unrelated pre-existing untracked files remain untracked; recent commits include the plan, core implementation, and docs commits.

- [ ] **Step 5: Push the branch**

Run:

```bash
rtk proxy git push -u origin codex/capital-allocator
```

Expected: branch `codex/capital-allocator` is pushed and tracks `origin/codex/capital-allocator`.

## Self-Review

Spec coverage:
- Pure `services/portfolio/capital_allocator.py`: Task 3.
- Behavior tests for allocation, caps, quality, previous weights, invalid inputs, determinism, diagnostics: Task 1.
- Governance boundary tests: Task 2.
- Roadmap and project state updates: Task 4.
- Targeted, governance, and full validation commands: Task 5.

Completeness scan:
- No unresolved blanks.
- Every new code file has exact content.
- Every command has expected output.

Type consistency:
- `CapitalAllocatorConfig`, `BookAllocation`, `CapitalAllocationPlan`, and `allocate_capital` signatures match the spec.
- Test helper names and imported production symbols match the implementation.
- Reason strings used in tests match the implementation.
