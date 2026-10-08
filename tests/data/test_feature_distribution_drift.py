from __future__ import annotations

import math

import pytest

from services.data.distribution_drift import (
    BLOCK,
    PASS,
    WARN,
    DistributionDriftPolicy,
    evaluate_feature_distribution_drift,
)


def _policy(**overrides: object) -> DistributionDriftPolicy:
    values: dict[str, object] = {
        "baseline_window": 10,
        "recent_window": 5,
        "min_observations": 5,
        "warn_ks": 0.20,
        "block_ks": 0.60,
    }
    values.update(overrides)
    return DistributionDriftPolicy(**values)


def test_declared_feature_panel_passes_and_exposes_each_assessment() -> None:
    panel = {
        "carry": (0.0,) * 15,
        "crowding": (1.0,) * 15,
    }

    report = evaluate_feature_distribution_drift(
        panel,
        expected_features=("carry", "crowding"),
        policy=_policy(),
    )

    assert report.status == PASS
    assert report.feature_ids == ("carry", "crowding")
    assert tuple(feature_id for feature_id, _ in report.assessments) == report.feature_ids
    assert all(assessment.status == PASS for _, assessment in report.assessments)
    assert report.provenance == "PIT_FEATURE_TRAILING_KS_DRIFT_V1"
    assert report.as_dict()["load_bearing"] is False


def test_one_shifted_feature_blocks_without_hiding_the_other_feature() -> None:
    report = evaluate_feature_distribution_drift(
        {
            "stable": (0.0,) * 15,
            "shifted": (0.0,) * 10 + (1.0,) * 5,
        },
        expected_features=("stable", "shifted"),
        policy=_policy(),
    )

    assert report.status == BLOCK
    assert dict(report.assessments)["stable"].status == PASS
    assert dict(report.assessments)["shifted"].status == BLOCK
    assert "shifted:distribution_ks_block" in report.reasons


def test_warn_is_retained_when_no_feature_is_blocked() -> None:
    report = evaluate_feature_distribution_drift(
        {"candidate": (0.0,) * 10 + (0.0,) * 3 + (1.0,) * 2},
        expected_features=("candidate",),
        policy=_policy(),
    )

    assert report.status == WARN
    assert report.reasons == ("candidate:distribution_ks_warn",)


@pytest.mark.parametrize(
    "panel, expected, reason",
    [
        ({"carry": (0.0,) * 15}, ("carry", "crowding"), "missing_feature:crowding"),
        ({"carry": (0.0,) * 15, "extra": (0.0,) * 15}, ("carry",), "unexpected_feature:extra"),
        ({"carry": (0.0,) * 15}, (), "invalid_expected_features"),
        ({"carry": (math.nan,) * 15}, ("carry",), "carry:invalid_return"),
    ],
)
def test_missing_or_invalid_feature_evidence_fails_closed(
    panel, expected, reason: str
) -> None:
    report = evaluate_feature_distribution_drift(
        panel,
        expected_features=expected,
        policy=_policy(),
    )

    assert report.status == BLOCK
    assert reason in report.reasons


def test_exclusive_end_index_ignores_future_suffix_for_every_feature() -> None:
    panel = {"carry": tuple(float(i % 3) for i in range(15))}
    extended = {"carry": panel["carry"] + (100.0, -100.0)}

    before = evaluate_feature_distribution_drift(
        panel, end_index=15, expected_features=("carry",), policy=_policy()
    )
    after = evaluate_feature_distribution_drift(
        extended, end_index=15, expected_features=("carry",), policy=_policy()
    )

    assert after == before


def test_invalid_panel_and_empty_universe_fail_closed() -> None:
    assert evaluate_feature_distribution_drift(None, policy=_policy()).reasons == (
        "invalid_feature_series",
    )
    assert evaluate_feature_distribution_drift({}, policy=_policy()).reasons == (
        "empty_feature_universe",
    )
    assert evaluate_feature_distribution_drift(
        {"carry": (0.0,) * 15}, expected_features=["carry", 1], policy=_policy()
    ).reasons == ("invalid_expected_features",)
