import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_confirmation_quality_exclusion_is_bounded_and_fail_closed() -> None:
    payload = json.loads(
        (
            ROOT
            / "config"
            / "binance_book_depth_confirmation_quality_policy.json"
        ).read_text(encoding="utf-8")
    )

    assert payload["status"] == "FROZEN_BEFORE_CONFIRMATION_OUTCOME"
    assert payload["window_day_count"] == 90
    assert payload["maximum_quality_excluded_days"] == 4
    assert payload["minimum_confirmation_decisions"] == 80
    assert payload["exclusion_unit"] == "ENTIRE_UTC_DAY_ALL_14_INSTRUMENTS"
    assert payload["eligible_exclusion"] == {
        "requires_official_checksum_pass": True,
        "requires_parser_or_alignment_quality_rejection": True,
        "network_or_timeout_is_excludable": False,
        "checksum_mismatch_is_excludable": False,
        "unexpected_exception_is_excludable": False,
    }
    assert payload["continuation_policy"]["fifth_quality_rejected_day_blocks"] is True
    assert payload["continuation_policy"]["thresholds_may_not_change_after_outcome"] is True
