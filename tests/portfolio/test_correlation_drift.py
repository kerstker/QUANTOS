import math

import pytest

from services.portfolio.correlation_drift import (
    BLOCK,
    PASS,
    WARN,
    CorrelationDriftPolicy,
    evaluate_correlation_drift,
)


BASE_A = (1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0)
BASE_B = (1.0, 1.0, -1.0, -1.0, 1.0, 1.0, -1.0, -1.0)
RECENT_A = (1.0, -1.0, 1.0, -1.0)
RECENT_LOW = (1.0, 1.0, -1.0, -1.0)
RECENT_HIGH = RECENT_A


def _policy(**overrides) -> CorrelationDriftPolicy:
    values = {
        "baseline_window": 8,
        "recent_window": 4,
        "warn_correlation": 0.50,
        "block_correlation": 0.80,
        "warn_increase": 0.20,
        "block_increase": 0.50,
        "warn_gross_multiplier": 0.75,
        "block_gross_multiplier": 0.40,
    }
    values.update(overrides)
    return CorrelationDriftPolicy(**values)


def test_stable_low_correlation_passes_without_scaling() -> None:
    assessment = evaluate_correlation_drift(
        {"carry": BASE_A + RECENT_A, "crowd": BASE_B + RECENT_LOW},
        policy=_policy(),
    )

    assert assessment.status == PASS
    assert assessment.recommended_gross_multiplier == 1.0
    assert assessment.reasons == ("correlation_stable",)
    assert assessment.pairs[0].baseline_correlation == pytest.approx(0.0)
    assert assessment.pairs[0].recent_correlation == pytest.approx(0.0)


def test_low_to_high_positive_correlation_break_blocks_and_reduces_gross() -> None:
    assessment = evaluate_correlation_drift(
        {"carry": BASE_A + RECENT_A, "crowd": BASE_B + RECENT_HIGH},
        policy=_policy(),
    )

    assert assessment.status == BLOCK
    assert assessment.recommended_gross_multiplier == 0.40
    pair = assessment.pairs[0]
    assert pair.status == BLOCK
    assert pair.baseline_correlation == pytest.approx(0.0)
    assert pair.recent_correlation == pytest.approx(1.0)
    assert pair.correlation_increase == pytest.approx(1.0)


def test_threshold_crossing_can_warn_without_blocking() -> None:
    assessment = evaluate_correlation_drift(
        {"carry": BASE_A + RECENT_A, "crowd": BASE_B + RECENT_HIGH},
        policy=_policy(block_correlation=1.0, block_increase=2.0),
    )

    assert assessment.status == WARN
    assert assessment.recommended_gross_multiplier == 0.75
    assert assessment.pairs[0].status == WARN


def test_exclusive_end_index_is_invariant_to_future_suffix() -> None:
    original = {"carry": BASE_A + RECENT_A, "crowd": BASE_B + RECENT_HIGH}
    extended = {
        "carry": original["carry"] + (9.0, 8.0, 7.0),
        "crowd": original["crowd"] + (-9.0, -8.0, -7.0),
    }

    before = evaluate_correlation_drift(original, end_index=12, policy=_policy())
    after = evaluate_correlation_drift(extended, end_index=12, policy=_policy())

    assert after == before


@pytest.mark.parametrize(
    "returns, expected_reason",
    [
        ({}, "insufficient_books"),
        ({"only": BASE_A + RECENT_A}, "insufficient_books"),
        (
            {"a": BASE_A + RECENT_A, "b": BASE_B + RECENT_HIGH[:-1]},
            "misaligned_returns",
        ),
        (
            {"a": BASE_A + RECENT_A, "b": BASE_B + (1.0, -1.0, math.nan, -1.0)},
            "invalid_return:b",
        ),
        ({"a": (1.0,) * 12, "b": BASE_B + RECENT_HIGH}, "zero_variance:a:baseline"),
        ({"a": BASE_A, "b": BASE_B}, "insufficient_history"),
        ({"a": None, "b": BASE_B + RECENT_HIGH}, "invalid_return_series:a"),
        ({1: BASE_A + RECENT_A, "b": BASE_B + RECENT_HIGH}, "invalid_book_id"),
    ],
)
def test_missing_or_untrustworthy_evidence_fails_closed(returns, expected_reason) -> None:
    assessment = evaluate_correlation_drift(returns, policy=_policy())

    assert assessment.status == BLOCK
    assert assessment.recommended_gross_multiplier == 0.40
    assert assessment.reasons == (expected_reason,)


def test_invalid_policy_fails_closed_without_computing_pairs() -> None:
    assessment = evaluate_correlation_drift(
        {"carry": BASE_A + RECENT_A, "crowd": BASE_B + RECENT_HIGH},
        policy=_policy(recent_window=1),
    )

    assert assessment.status == BLOCK
    assert assessment.pairs == ()
    assert assessment.reasons == ("invalid_recent_window",)


def test_output_is_deterministic_and_pair_order_is_canonical() -> None:
    inputs = {
        "z": BASE_A + RECENT_A,
        "a": BASE_B + RECENT_HIGH,
        "m": tuple(-value for value in BASE_A + RECENT_A),
    }

    first = evaluate_correlation_drift(inputs, policy=_policy())
    second = evaluate_correlation_drift(inputs, policy=_policy())

    assert first == second
    assert [(pair.book_a, pair.book_b) for pair in first.pairs] == [
        ("a", "m"),
        ("a", "z"),
        ("m", "z"),
    ]
