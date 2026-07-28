"""Tests for the unified, fail-closed portfolio constraint result layer.

The result layer is intentionally additive: it does not replace the existing
optimizer or the per-domain diagnostic APIs.  Missing evidence is PENDING,
malformed evidence or a breached configured limit is BLOCK.
"""

import math

import pytest

from services.portfolio.portfolio_constraints import (
    ConstraintCheck,
    PortfolioConstraintResult,
    evaluate_capacity_constraint,
    evaluate_crowding_constraint,
    evaluate_firm_constraints,
    evaluate_portfolio_constraints,
    evaluate_tail_budget,
)


def test_tail_budget_passes_with_empirical_cvar():
    result = evaluate_tail_budget(
        {"a": 0.5, "b": -0.5},
        [[-0.02, 0.02], [0.02, -0.02], [0.00, 0.00], [0.01, -0.01]],
        scenario_symbols=("a", "b"),
        max_cvar_loss=0.03,
        alpha=0.75,
    )

    assert isinstance(result, ConstraintCheck)
    assert result.status == "PASS"
    assert result.metrics["cvar_loss"] == pytest.approx(0.02)
    assert result.metrics["tail_count"] == 1.0


def test_tail_budget_breach_blocks():
    result = evaluate_tail_budget(
        {"a": 1.0},
        [[-0.20], [-0.10], [0.01], [0.02]],
        scenario_symbols=("a",),
        max_cvar_loss=0.05,
        alpha=0.75,
    )

    assert result.status == "BLOCK"
    assert "tail_budget_exceeded" in result.reasons[0]
    assert result.metrics["cvar_loss"] == pytest.approx(0.20)


def test_missing_tail_evidence_is_pending():
    assert evaluate_tail_budget(None, None, max_cvar_loss=0.05).status == "PENDING"
    assert evaluate_tail_budget({"a": 1.0}, None, max_cvar_loss=0.05).status == "PENDING"
    assert evaluate_tail_budget(
        {"a": 1.0}, [[0.0]], scenario_symbols=("a",), max_cvar_loss=None
    ).status == "PENDING"


def test_invalid_tail_evidence_blocks():
    result = evaluate_tail_budget(
        {"a": 1.0}, [[math.nan], [0.01]],
        scenario_symbols=("a",), max_cvar_loss=0.05
    )
    assert result.status == "BLOCK"
    assert result.reasons == ("non_finite_scenario:0:0",)


def test_tail_budget_requires_explicit_scenario_symbol_order():
    weights = {"A": 0.9, "B": 0.1}
    scenarios = [[-0.01, -0.20], [-0.01, -0.20]]

    missing = evaluate_tail_budget(weights, scenarios, max_cvar_loss=0.05)
    real_order = evaluate_tail_budget(
        weights,
        scenarios,
        scenario_symbols=("B", "A"),
        max_cvar_loss=0.05,
    )
    mismatch = evaluate_tail_budget(
        weights,
        scenarios,
        scenario_symbols=("A", "C"),
        max_cvar_loss=0.05,
    )

    assert missing.status == "PENDING"
    assert missing.reasons == ("scenario_symbols_missing",)
    assert real_order.status == "BLOCK"
    assert real_order.metrics["cvar_loss"] == pytest.approx(0.181)
    assert mismatch.status == "BLOCK"
    assert mismatch.reasons == ("scenario_symbol_mismatch",)


def test_firm_constraints_aggregate_gross_net_and_symbol_concentration():
    result = evaluate_firm_constraints(
        {"carry": {"BTC": 0.5, "ETH": -0.5}, "crowd": {"BTC": -0.5, "SOL": 0.5}},
        {"carry": 1000.0, "crowd": 1000.0},
        max_gross=2000.0,
        max_net=1000.0,
        max_symbol_weight=0.60,
    )

    assert result.status == "PASS"
    assert result.metrics["firm_gross"] == pytest.approx(1000.0)
    assert result.metrics["firm_net"] == pytest.approx(0.0)
    assert result.metrics["max_symbol_weight"] == pytest.approx(0.5)


def test_firm_symbol_limit_breach_blocks_even_when_net_is_zero():
    result = evaluate_firm_constraints(
        {"a": {"BTC": 1.0}, "b": {"BTC": 1.0}},
        {"a": 1000.0, "b": 1000.0},
        max_gross=10_000.0,
        max_net=10_000.0,
        max_symbol_weight=0.50,
    )
    assert result.status == "BLOCK"
    assert any("symbol_weight_exceeded" in reason for reason in result.reasons)


def test_missing_firm_evidence_or_limits_is_pending():
    assert evaluate_firm_constraints(None, None, max_gross=10.0).status == "PENDING"
    assert evaluate_firm_constraints({}, {}, max_gross=10.0).status == "BLOCK"
    assert evaluate_firm_constraints({"a": {"BTC": 1.0}}, {"a": 1.0}).status == "PENDING"


def test_capacity_constraint_uses_bottleneck_and_blocks_overuse():
    result = evaluate_capacity_constraint(
        {"BTC": 900.0, "ETH": 100.0},
        {"BTC": 1000.0, "ETH": 1000.0},
        max_usage=0.80,
    )
    assert result.status == "BLOCK"
    assert result.metrics["max_usage"] == pytest.approx(0.9)
    assert result.metrics["bottleneck_symbol"] == "BTC"


def test_capacity_missing_inputs_are_pending_and_invalid_values_block():
    assert evaluate_capacity_constraint(None, None, max_usage=0.8).status == "PENDING"
    assert evaluate_capacity_constraint({"BTC": 1.0}, None, max_usage=0.8).status == "PENDING"
    result = evaluate_capacity_constraint({"BTC": 1.0}, {"BTC": 0.0}, max_usage=0.8)
    assert result.status == "BLOCK"
    assert "invalid_capacity:BTC" in result.reasons


def test_crowding_constraint_preserves_warn_and_blocks_bad_coverage():
    warn = evaluate_crowding_constraint(
        {"A": 0.5, "B": -0.5}, {"A": 0.50, "B": 0.54},
        threshold=0.60, warn_threshold=0.45,
    )
    assert warn.status == "WARN"

    pending = evaluate_crowding_constraint({"A": 1.0}, None, threshold=0.60)
    assert pending.status == "PENDING"

    blocked = evaluate_crowding_constraint(
        {"A": 0.5, "B": -0.5}, {"A": 0.1}, threshold=0.60, min_coverage=0.80
    )
    assert blocked.status == "BLOCK"
    assert any("insufficient_coverage" in reason for reason in blocked.reasons)


def test_unified_result_aggregates_checks_and_fail_closed_status():
    result = evaluate_portfolio_constraints(
        weights={"A": 1.0},
        scenarios=[[-0.01], [0.01]],
        scenario_symbols=("A",),
        tail_budget=0.10,
        book_positions={"book": {"A": 1.0}},
        book_notionals={"book": 100.0},
        max_firm_gross=50.0,
        capacity_by_symbol={"A": 200.0},
        position_notional_by_symbol={"A": 100.0},
        max_capacity_usage=0.80,
        crowding_by_symbol={"A": 0.10},
        crowding_threshold=0.60,
    )
    assert isinstance(result, PortfolioConstraintResult)
    assert result.status == "BLOCK"
    assert {check.name for check in result.checks} == {
        "tail_budget", "firm_exposure", "capacity", "crowding"
    }
    assert result.as_dict()["status"] == "BLOCK"


def test_unified_result_with_no_requested_constraints_is_pending():
    result = evaluate_portfolio_constraints()
    assert result.status == "PENDING"
    assert result.reasons == ("no_constraints_configured",)


def test_unified_result_is_deterministic():
    kwargs = {
        "weights": {"A": 1.0},
        "scenarios": [[-0.01], [0.01]],
        "scenario_symbols": ("A",),
        "tail_budget": 0.10,
        "capacity_by_symbol": {"A": 200.0},
        "position_notional_by_symbol": {"A": 100.0},
        "max_capacity_usage": 0.80,
    }
    assert evaluate_portfolio_constraints(**kwargs) == evaluate_portfolio_constraints(**kwargs)


