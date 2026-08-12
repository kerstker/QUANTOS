import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_first_confirmation_window_is_honestly_blocked_before_outcome() -> None:
    payload = json.loads(
        (
            ROOT
            / "evidence"
            / "binance_book_depth_confirmation_window1_blocked.json"
        ).read_text(encoding="utf-8")
    )

    assert payload["status"] == "BLOCKED_BEFORE_OUTCOME"
    assert payload["reason_code"] == "QUALITY_EXCLUSION_LIMIT_EXCEEDED"
    assert payload["quality_excluded_day_count"] == 5
    assert payload["maximum_quality_excluded_days"] == 4
    assert payload["complete_stored_days"] == 59
    assert payload["stored_observation_count"] == 59 * 14 * 24
    assert payload["all_exclusions_checksum_verified"] is True
    assert payload["confirmation_return_accessed"] is False
    assert payload["confirmation_metrics_computed"] is False
    assert payload["formal_validation_started"] is False
    assert payload["formal_admission"] is False
    assert payload["testnet_authorized"] is False
    assert payload["authority_chain_valid"] is True


def test_second_confirmation_window_is_honestly_blocked_before_outcome() -> None:
    payload = json.loads(
        (
            ROOT
            / "evidence"
            / "binance_book_depth_confirmation_window2_blocked.json"
        ).read_text(encoding="utf-8")
    )

    assert payload["status"] == "BLOCKED_BEFORE_OUTCOME"
    assert payload["reason_code"] == "HISTORICAL_CURVE_SCHEMA_INCOMPATIBLE"
    assert payload["quality_excluded_day_count"] == 13
    assert payload["maximum_quality_excluded_days"] == 12
    assert payload["complete_stored_days"] == 0
    assert payload["all_14_instruments_rejected_each_day"] is True
    assert payload["all_exclusions_checksum_verified"] is True
    assert payload["common_rejection_reason"] == (
        "snapshot does not contain the exact symmetric depth levels"
    )
    assert payload["confirmation_return_accessed"] is False
    assert payload["confirmation_metrics_computed"] is False
    assert payload["formal_validation_started"] is False
    assert payload["formal_admission"] is False
    assert payload["testnet_authorized"] is False
    assert payload["authority_chain_valid"] is True


def test_first_window_capacity_is_blocked_before_outcome_access() -> None:
    payload = json.loads(
        (
            ROOT
            / "evidence"
            / "binance_book_depth_confirmation_window1_capacity_blocked.json"
        ).read_text(encoding="utf-8")
    )

    assert payload["status"] == "BLOCKED_BEFORE_OUTCOME"
    assert payload["reason_code"] == "INSUFFICIENT_CONFIRMATION_DATA_CAPACITY"
    assert payload["processed_days"] == payload["requested_days"] == 90
    assert payload["complete_stored_days"] == 83
    assert payload["complete_day_ratio"] >= payload["minimum_complete_day_ratio"]
    assert payload["complete_stored_days"] >= payload["minimum_complete_days"]
    assert payload["complete_day_capacity_passed"] is True
    assert payload["actual_adjacent_24h_decision_count"] == 77
    assert payload["minimum_confirmation_decisions"] == 80
    assert payload["actual_decision_capacity_passed"] is False
    assert payload["quality_excluded_day_count"] == 7
    assert payload["all_14_instruments_rejected_each_excluded_day"] is True
    assert payload["all_exclusions_checksum_verified"] is True
    assert payload["confirmation_return_accessed"] is False
    assert payload["confirmation_metrics_computed"] is False
    assert payload["formal_validation_started"] is False
    assert payload["formal_admission"] is False
    assert payload["testnet_authorized"] is False
    assert payload["authority_chain_valid"] is True


def test_v4_capacity_retains_mark_alignment_hard_gate_before_outcome() -> None:
    payload = json.loads(
        (
            ROOT
            / "evidence"
            / "binance_book_depth_confirmation_window1_v4_capacity_blocked.json"
        ).read_text(encoding="utf-8")
    )

    assert payload["status"] == "BLOCKED_BEFORE_OUTCOME"
    assert payload["reason_code"] == "INSUFFICIENT_CONFIRMATION_DATA_CAPACITY"
    assert payload["processed_days"] == payload["requested_days"] == 90
    assert payload["complete_stored_days"] == 85
    assert payload["complete_day_capacity_passed"] is True
    assert payload["actual_adjacent_24h_decision_count"] == 79
    assert payload["minimum_confirmation_decisions"] == 80
    assert payload["actual_decision_capacity_passed"] is False
    assert payload["hourly_coverage_rejected_days"] == 4
    assert payload["mark_alignment_rejected_days"] == 1
    assert payload["mark_alignment_failure"]["hard_gate_retained"] is True
    assert payload["mark_alignment_failure"]["maximum_deviation_ratio"] > (
        payload["mark_alignment_failure"]["maximum_allowed_deviation_ratio"]
    )
    assert payload["confirmation_return_accessed"] is False
    assert payload["confirmation_metrics_computed"] is False
    assert payload["formal_validation_started"] is False
    assert payload["formal_admission"] is False
    assert payload["testnet_authorized"] is False
    assert payload["authority_chain_valid"] is True
