import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_confirmation_v3_requires_complete_days_and_actual_decisions() -> None:
    contract = json.loads(
        (ROOT / "config" / "binance_book_depth_alpha_confirmation.json").read_text(
            encoding="utf-8"
        )
    )
    policy = json.loads(
        (
            ROOT
            / "config"
            / "binance_book_depth_confirmation_quality_policy_v3.json"
        ).read_text(encoding="utf-8")
    )

    assert policy["status"] == "FROZEN_BEFORE_CONFIRMATION_OUTCOME"
    assert policy["outcome_access_before_freeze"] is False
    assert policy["window_day_count"] == 90
    assert policy["minimum_complete_day_ratio"] == 0.90
    assert policy["minimum_complete_days"] == 81
    assert policy["minimum_confirmation_decisions"] == contract["minimum_decisions"] == 80
    assert policy["maximum_quality_excluded_days"] == 9
    assert policy["exclusion_unit"] == "ENTIRE_UTC_DAY_ALL_14_INSTRUMENTS"
    assert policy["eligible_exclusion"] == {
        "requires_official_checksum_pass": True,
        "requires_parser_or_alignment_quality_rejection": True,
        "network_or_timeout_is_excludable": False,
        "checksum_mismatch_is_excludable": False,
        "unexpected_exception_is_excludable": False,
    }
    assert policy["terminal_acceptance"] == {
        "all_90_days_must_be_processed": True,
        "complete_days_must_meet_ratio": True,
        "actual_adjacent_24h_decisions_must_meet_minimum": True,
        "quality_excluded_days_are_never_imputed": True,
        "thresholds_may_not_change_after_outcome": True,
    }
