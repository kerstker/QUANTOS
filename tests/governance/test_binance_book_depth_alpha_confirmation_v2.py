import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_second_confirmation_keeps_trial_and_gates_on_an_untouched_window() -> None:
    first = json.loads(
        (ROOT / "config" / "binance_book_depth_alpha_confirmation.json").read_text(
            encoding="utf-8"
        )
    )
    second = json.loads(
        (
            ROOT / "config" / "binance_book_depth_alpha_confirmation_v2.json"
        ).read_text(encoding="utf-8")
    )

    assert second["status"] == "PRE_REGISTERED_BEFORE_CONFIRMATION_DATA_ACCESS"
    assert second["source_blocked_confirmation_commit"] == (
        "26c88315c60f14fca2b568b5e6a6baf75ac85385"
    )
    for field in (
        "dataset_id",
        "candidate_id",
        "direction",
        "horizon_hours",
        "universe",
        "long_count",
        "short_count",
        "one_way_cost_bps_per_unit_turnover",
        "cost_stress_multiplier",
        "annualization_periods",
        "minimum_decisions",
        "required_gates",
    ):
        assert second[field] == first[field]
    window = second["confirmation_window"]
    assert window["day_count"] == 120
    assert window["end_exclusive_ms"] == window["prior_confirmation_start_exclusive_ms"]
    assert window["relation_to_prior_confirmation"] == (
        "STRICTLY_EARLIER_NON_OVERLAPPING"
    )
    unseen = second["frozen_unseen_window_evidence"]
    assert unseen["dataset_observation_count"] == 0
    assert unseen["authority_chain_valid"] is True
    assert second["multiple_testing"]["confirmation_adds_new_trial"] is False
    assert second["post_confirmation_policy"]["confirmation_pass_authorizes_testnet"] is False


def test_second_confirmation_quality_exclusions_preserve_decision_floor() -> None:
    payload = json.loads(
        (
            ROOT
            / "config"
            / "binance_book_depth_confirmation_quality_policy_v2.json"
        ).read_text(encoding="utf-8")
    )

    assert payload["status"] == "FROZEN_BEFORE_CONFIRMATION_OUTCOME"
    assert payload["window_day_count"] == 120
    assert payload["maximum_quality_excluded_days"] == 12
    assert payload["worst_case_available_decisions"] == 119 - 2 * 12 == 95
    assert payload["minimum_confirmation_decisions"] == 80
    assert payload["exclusion_unit"] == "ENTIRE_UTC_DAY_ALL_14_INSTRUMENTS"
    assert payload["eligible_exclusion"]["network_or_timeout_is_excludable"] is False
    assert payload["eligible_exclusion"]["checksum_mismatch_is_excludable"] is False
    assert payload["continuation_policy"]["thirteenth_quality_rejected_day_blocks"] is True
