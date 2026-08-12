import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_capacity_supplement_keeps_the_exact_frozen_trial_and_gates() -> None:
    parent = json.loads(
        (ROOT / "config" / "binance_book_depth_alpha_confirmation.json").read_text(
            encoding="utf-8"
        )
    )
    supplement = json.loads(
        (
            ROOT
            / "config"
            / "binance_book_depth_alpha_confirmation_supplement_v1.json"
        ).read_text(encoding="utf-8")
    )

    assert supplement["status"] == "PRE_REGISTERED_BEFORE_SUPPLEMENT_DATA_ACCESS"
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
        "required_gates",
    ):
        assert supplement[field] == parent[field]
    window = supplement["supplement_window"]
    assert window["day_count"] == 7
    assert window["end_exclusive_ms"] == window["parent_confirmation_start_ms"]
    assert window["relation_to_parent_confirmation"] == (
        "IMMEDIATELY_PRECEDING_NON_OVERLAPPING"
    )
    unseen = supplement["frozen_unseen_window_evidence"]
    assert unseen["dataset_observation_count"] == 0
    assert unseen["authority_chain_valid"] is True
    assert supplement["parent_capacity_decisions"] == 79
    assert supplement["minimum_combined_decisions"] == 80
    assert supplement["multiple_testing"]["supplement_adds_new_trial"] is False
    terminal = supplement["terminal_policy"]
    assert terminal["all_7_days_must_be_processed"] is True
    assert terminal["no_date_or_period_selection"] is True
    assert terminal["quality_excluded_days_are_never_imputed"] is True
    assert terminal["pass_authorizes_testnet"] is False
