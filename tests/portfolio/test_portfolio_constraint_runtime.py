"""Runtime integration tests for the unified portfolio constraint gate."""

import pytest

from services.portfolio.constrained_optimizer import min_variance_weights
from services.portfolio.constraints import PortfolioConstraints
from services.portfolio.config import PortfolioConfig
from services.portfolio.construction import build_portfolio
from services.portfolio.portfolio_constraints import (
    PortfolioConstraintInputs,
    PortfolioConstraintViolation,
)
from services.portfolio.signal_adapter import SignalAdapterConfig, signal_to_portfolio


def _box() -> PortfolioConstraints:
    return PortfolioConstraints(lower=0.0, upper=1.0, budget=1.0)


def test_optimizer_requires_complete_tail_evidence_when_gate_requested():
    with pytest.raises(PortfolioConstraintViolation) as exc:
        min_variance_weights(
            [[0.1, 0.0], [0.0, 0.1]],
            ["a", "b"],
            _box(),
            portfolio_constraints=PortfolioConstraintInputs(tail_budget=0.05),
        )
    assert exc.value.result.status == "PENDING"
    assert "tail_evidence_missing" in exc.value.result.reasons[0]


def test_optimizer_blocks_tail_budget_breach():
    with pytest.raises(PortfolioConstraintViolation) as exc:
        min_variance_weights(
            [[0.1, 0.0], [0.0, 0.1]],
            ["a", "b"],
            _box(),
            portfolio_constraints=PortfolioConstraintInputs(
                scenarios=[[-1.0, -1.0], [0.0, 0.0]],
                scenario_symbols=("a", "b"),
                tail_budget=0.05,
            ),
        )
    assert exc.value.result.status == "BLOCK"
    assert any("tail_budget_exceeded" in reason for reason in exc.value.result.reasons)


def test_construction_gate_checks_post_rebalance_target():
    returns = {
        "a": tuple(0.01 * i for i in range(10)),
        "b": tuple(0.02 * i for i in range(10)),
        "c": tuple(0.03 * i for i in range(10)),
    }
    inputs = PortfolioConstraintInputs(
        scenarios=[[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]],
        scenario_symbols=("a", "b", "c"),
        tail_budget=0.05,
    )
    plan = build_portfolio(
        "p1", "pf", returns, portfolio_constraints=inputs,
        config=PortfolioConfig(min_overlap=5, max_weight=1.0),
    )
    assert sum(target.target_weight for target in plan.targets) == pytest.approx(1.0)


def test_signal_adapter_exposes_and_enforces_capacity_result():
    inputs = PortfolioConstraintInputs(max_capacity_usage=0.20)
    out = signal_to_portfolio(
        {"a": 1.0, "b": -1.0},
        config=SignalAdapterConfig(gross_target=1.0, max_position=1.0, target_aum=100.0),
        adv_notional={"a": 1_000.0, "b": 1_000.0},
        portfolio_constraints=inputs,
    )
    assert out["portfolio_constraints"]["status"] == "PASS"

    with pytest.raises(PortfolioConstraintViolation) as exc:
        signal_to_portfolio(
            {"a": 1.0, "b": -1.0},
            config=SignalAdapterConfig(gross_target=1.0, max_position=1.0, target_aum=100.0),
            adv_notional={"a": 1_000.0, "b": 10.0},
            portfolio_constraints=inputs,
        )
    assert exc.value.result.status == "BLOCK"
    assert any("capacity_usage_exceeded" in reason for reason in exc.value.result.reasons)


def test_signal_adapter_missing_capacity_evidence_is_pending_and_rejected():
    with pytest.raises(PortfolioConstraintViolation) as exc:
        signal_to_portfolio(
            {"a": 1.0, "b": -1.0},
            config=SignalAdapterConfig(gross_target=1.0, max_position=1.0),
            portfolio_constraints=PortfolioConstraintInputs(max_capacity_usage=0.20),
        )
    assert exc.value.result.status == "PENDING"
    assert "capacity_evidence_missing" in exc.value.result.reasons[0]


def test_crowding_only_gate_does_not_create_unconfigured_tail_check():
    inputs = PortfolioConstraintInputs(
        crowding_by_symbol={"a": 0.10, "b": 0.10},
        crowding_threshold=0.60,
    )
    result = inputs.evaluate({"a": 0.5, "b": -0.5})
    assert result.status == "PASS"
    assert {check.name for check in result.checks} == {"crowding"}
