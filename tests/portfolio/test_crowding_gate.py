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


def test_crowding_score_equal_to_threshold_warns_instead_of_blocks():
    decision = evaluate_crowding_gate(
        {"A": 0.5, "B": -0.5},
        {"A": 0.60, "B": 0.60},
        threshold=0.60,
        warn_threshold=0.45,
    )

    assert decision.status == "WARN"
    assert decision.score == pytest.approx(0.60)


def test_crowding_score_equal_to_warn_threshold_passes_instead_of_warns():
    decision = evaluate_crowding_gate(
        {"A": 0.5, "B": -0.5},
        {"A": 0.45, "B": 0.45},
        threshold=0.60,
        warn_threshold=0.45,
    )

    assert decision.status == "PASS"
    assert decision.score == pytest.approx(0.45)


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


@pytest.mark.parametrize("bad_weight", [math.nan, math.inf, -math.inf])
def test_non_finite_weight_blocks(bad_weight):
    decision = evaluate_crowding_gate({"A": bad_weight}, {"A": 0.1})

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
        {"MSFT": 0.3, "MISSING": 0.5, "AAPL": -0.2},
        {
            "crowding": {"AAPL": 0.40, "MSFT": 0.20},
            "liquidity": {"AAPL": 1.0, "MSFT": 2.0, "MISSING": 3.0},
        },
        factor_name="liquidity",
    )

    assert projected == {"AAPL": 1.0, "MISSING": 3.0, "MSFT": 2.0}


def test_project_symbol_crowding_defaults_to_crowding_and_filters_weights():
    projected = project_symbol_crowding(
        {"MSFT": 0.3, "AAPL": -0.2},
        {
            "crowding": {"AAPL": 0.40, "MSFT": 0.20, "UNHELD": 0.90},
            "liquidity": {"AAPL": 1.0, "MSFT": 2.0},
        },
    )

    assert projected == {"AAPL": 0.40, "MSFT": 0.20}


def test_project_symbol_crowding_missing_factor_returns_empty_map():
    projected = project_symbol_crowding({"A": 1.0}, {"liquidity": {"A": 0.3}}, factor_name="crowding")

    assert projected == {}
