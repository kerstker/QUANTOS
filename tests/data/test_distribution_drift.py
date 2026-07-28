import math

import pytest

from services.data.distribution_drift import (
    BLOCK,
    PASS,
    WARN,
    DistributionDriftPolicy,
    evaluate_distribution_drift,
)


def _policy(**overrides) -> DistributionDriftPolicy:
    values = {
        "baseline_window": 10,
        "recent_window": 5,
        "min_observations": 5,
        "warn_ks": 0.20,
        "block_ks": 0.60,
    }
    values.update(overrides)
    return DistributionDriftPolicy(**values)


def test_stable_distribution_passes() -> None:
    values = tuple(float(i % 5) for i in range(15))

    assessment = evaluate_distribution_drift(values, policy=_policy())

    assert assessment.status == PASS
    assert assessment.ks_distance == pytest.approx(0.0)
    assert assessment.reasons == ("distribution_stable",)


def test_shifted_distribution_blocks_and_exposes_summary_only() -> None:
    values = (0.0,) * 10 + (1.0,) * 5

    assessment = evaluate_distribution_drift(values, policy=_policy())

    assert assessment.status == BLOCK
    assert assessment.ks_distance == pytest.approx(1.0)
    assert assessment.baseline_mean == pytest.approx(0.0)
    assert assessment.recent_mean == pytest.approx(1.0)
    assert assessment.sample_counts == (10, 5)


def test_moderate_distribution_shift_warns() -> None:
    values = (0.0,) * 10 + (0.0,) * 3 + (1.0,) * 2

    assessment = evaluate_distribution_drift(values, policy=_policy())

    assert assessment.status == WARN
    assert assessment.ks_distance == pytest.approx(0.4)


def test_exclusive_end_index_is_invariant_to_future_suffix() -> None:
    original = tuple(float(i % 5) for i in range(15))
    extended = original + (100.0, -100.0)

    before = evaluate_distribution_drift(original, end_index=15, policy=_policy())
    after = evaluate_distribution_drift(extended, end_index=15, policy=_policy())

    assert after == before


@pytest.mark.parametrize(
    "values, expected_reason",
    [
        ((1.0,) * 14, "insufficient_history"),
        ((0.0,) * 10 + (math.nan,) * 5, "invalid_return"),
        (None, "invalid_return_series"),
    ],
)
def test_invalid_or_insufficient_input_fails_closed(values, expected_reason) -> None:
    assessment = evaluate_distribution_drift(values, policy=_policy())

    assert assessment.status == BLOCK
    assert assessment.reasons == (expected_reason,)
    assert assessment.ks_distance is None


def test_invalid_policy_fails_closed() -> None:
    assessment = evaluate_distribution_drift(
        (0.0,) * 10 + (1.0,) * 5,
        policy=_policy(recent_window=2),
    )

    assert assessment.status == BLOCK
    assert assessment.reasons == ("invalid_recent_window",)


def test_output_is_deterministic_and_calibration_is_explicit() -> None:
    values = tuple(float(i % 3) for i in range(15))

    first = evaluate_distribution_drift(values, policy=_policy())
    second = evaluate_distribution_drift(values, policy=_policy())

    assert first == second
    assert first.provenance == "PIT_TRAILING_KS_DRIFT_V1"
    assert first.calibration == "UNCALIBRATED_PRINCIPLED"
