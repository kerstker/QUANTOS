import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_v4_quality_policy_matches_hourly_estimator_without_weakening_hard_gates() -> None:
    contract = json.loads(
        (ROOT / "config" / "binance_book_depth_alpha_confirmation.json").read_text(
            encoding="utf-8"
        )
    )
    policy = json.loads(
        (
            ROOT
            / "config"
            / "binance_book_depth_confirmation_quality_policy_v4.json"
        ).read_text(encoding="utf-8")
    )

    assert policy["status"] == "FROZEN_BEFORE_CONFIRMATION_OUTCOME"
    assert policy["outcome_access_before_freeze"] is False
    assert policy["estimator"] == "HOURLY_P10_DEPTH_CURVE"
    coverage = policy["coverage_decision"]
    assert coverage["daily_total_snapshot_floor_is_admission_gate"] is False
    assert coverage["daily_total_snapshot_count_is_retained_diagnostic"] is True
    assert coverage["all_24_utc_hours_required"] is True
    assert coverage["minimum_hourly_snapshot_count"] == 96
    assert coverage["minimum_hourly_coverage_ratio"] == 0.8
    hard = policy["unchanged_hard_gates"]
    assert hard == {
        "official_archive_checksum_required": True,
        "exact_symmetric_depth_levels_required": True,
        "maximum_inverted_snapshot_ratio": 0.002,
        "all_hours_must_align_to_checksum_verified_mark_prices": True,
        "maximum_mark_alignment_deviation_ratio": 0.01,
        "network_or_timeout_is_excludable": False,
        "checksum_mismatch_is_excludable": False,
        "unexpected_exception_is_excludable": False,
    }
    assert policy["minimum_complete_days"] == 81
    assert policy["minimum_confirmation_decisions"] == contract["minimum_decisions"] == 80
    assert policy["authorization"] == {
        "formal_admission": False,
        "forward_credit": False,
        "testnet": False,
        "order_submission": False,
    }


def test_v4_source_diagnostic_proves_no_outcome_access_and_two_quality_classes() -> None:
    diagnostic = json.loads(
        (
            ROOT
            / "evidence"
            / "binance_book_depth_confirmation_quality_diagnostic.json"
        ).read_text(encoding="utf-8")
    )

    assert diagnostic["mark_prices_accessed"] is False
    assert diagnostic["candidate_signals_accessed"] is False
    assert diagnostic["candidate_returns_accessed"] is False
    assert diagnostic["confirmation_outcome_accessed"] is False
    hourly_complete = [
        row for row in diagnostic["days"]
        if row["instruments_with_hour_below_floor"] == 0
    ]
    hourly_rejected = [
        row for row in diagnostic["days"]
        if row["instruments_with_hour_below_floor"] > 0
    ]
    assert [row["minimum_hour_snapshot_count"] for row in hourly_complete] == [
        101, 101, 102
    ]
    assert [row["minimum_hour_snapshot_count"] for row in hourly_rejected] == [
        90, 89, 92, 94
    ]
