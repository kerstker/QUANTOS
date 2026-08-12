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
