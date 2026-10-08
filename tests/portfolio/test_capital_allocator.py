import math

import pytest

from services.portfolio.capital_allocator import (
    CapitalAllocationPlan,
    CapitalAllocatorConfig,
    allocate_capital,
)


def _series(mean: float, amp: float = 0.001, n: int = 40) -> tuple[float, ...]:
    return tuple(mean + (amp if i % 2 == 0 else -amp) for i in range(n))


def _pattern(mean: float, pattern: tuple[float, ...], n: int = 60) -> tuple[float, ...]:
    return tuple(mean + pattern[i % len(pattern)] for i in range(n))


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


def test_covariance_participates_in_sizing_correlated_books():
    common = tuple(0.002 if i % 2 == 0 else -0.002 for i in range(60))
    plan = allocate_capital(
        {
            "clone_a": _pattern(0.003, common),
            "clone_b": _pattern(0.003, common),
            "diversifier": _pattern(0.002, (0.001, -0.001, -0.001, 0.001)),
        },
        config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0, risk_aversion=1.0),
    )

    weights = _weights(plan)
    assert plan.status == "ALLOCATED"
    assert weights["diversifier"] > 0.05
    assert weights["clone_a"] + weights["clone_b"] < 0.98
    assert plan.diagnostics["covariance_aware"] is True


def test_negative_or_zero_edge_books_receive_no_capital_when_positive_books_exist():
    plan = allocate_capital(
        {
            "positive": _series(0.003, 0.001),
            "zero_edge": _series(0.0, 0.001),
            "negative": _series(-0.002, 0.001),
        },
        config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0),
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
        config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0),
    )

    assert plan.status == "BLOCKED"
    assert plan.allocations == ()
    assert plan.reasons == ("no_positive_expected_return",)


def test_mean_shrinkage_can_block_statistically_thin_edge():
    plan = allocate_capital(
        {
            "thin": _series(0.0001, 0.01),
            "flat": _series(0.0, 0.01),
        },
        config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=2.0),
    )

    assert plan.status == "BLOCKED"
    assert plan.reasons == ("no_positive_expected_return",)


def test_capacity_caps_are_respected_and_cascading_caps_converge():
    plan = allocate_capital(
        {
            "strong": _series(0.006, 0.001),
            "middle": _series(0.004, 0.001),
            "backup": _series(0.002, 0.001),
        },
        total_aum=100.0,
        capacity_by_book={"strong": 30.0, "middle": 35.0},
        config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0),
    )

    weights = _weights(plan)
    assert plan.status == "ALLOCATED"
    assert weights["strong"] <= 0.30 + 1e-10
    assert weights["middle"] <= 0.35 + 1e-10
    assert weights["backup"] >= 0.35 - 1e-10
    assert sum(weights.values()) == pytest.approx(1.0)


def test_infeasible_capacity_caps_block():
    plan = allocate_capital(
        {
            "a": _series(0.003, 0.001),
            "b": _series(0.002, 0.001),
        },
        total_aum=100.0,
        capacity_by_book={"a": 40.0, "b": 50.0},
        config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0),
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
        config=CapitalAllocatorConfig(max_weight=1.0, mean_shrink_z=0.0),
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
        config=CapitalAllocatorConfig(max_weight=1.0, no_trade_band=0.02, mean_shrink_z=0.0),
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
    assert plan.diagnostics["method"] == "covariance_mean_variance"
    assert set(plan.diagnostics["book_stats"]) == {"a", "b"}
    assert set(plan.diagnostics["risk_contribution_pct"]) == {"a", "b"}
    assert plan.diagnostics["numerical_tolerance"]["weight_sum"] == 1e-8
    for stats in plan.diagnostics["book_stats"].values():
        assert {
            "mean",
            "variance",
            "volatility",
            "standard_error",
            "shrunk_mean",
            "expected_return",
            "quality_multiplier",
            "observations",
        } <= set(stats)
