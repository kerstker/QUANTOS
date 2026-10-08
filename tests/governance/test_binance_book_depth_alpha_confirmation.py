import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_depth_confirmation_is_frozen_on_an_unseen_non_overlapping_window() -> None:
    payload = json.loads(
        (ROOT / "config" / "binance_book_depth_alpha_confirmation.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["status"] == "PRE_REGISTERED_BEFORE_CONFIRMATION_DATA_ACCESS"
    assert payload["source_exploration_commit"] == (
        "aa7937252a04077dd9dd91286c8acc238b272cdc"
    )
    assert payload["candidate_id"] == "depth_imbalance_1p0_continuation_4h_v1"
    assert payload["direction"] == -1
    assert payload["horizon_hours"] == 24
    window = payload["confirmation_window"]
    assert window["day_count"] == 90
    assert window["end_exclusive_ms"] == window["exploration_start_exclusive_ms"]
    assert window["relation_to_exploration"] == "STRICTLY_EARLIER_NON_OVERLAPPING"
    unseen = payload["frozen_unseen_window_evidence"]
    assert unseen["dataset_observation_count"] == 0
    assert unseen["authority_chain_valid"] is True
    assert len(unseen["authority_head_row_hash"]) == 64
    assert payload["multiple_testing"]["global_trial_count_after_exploration"] == 2823
    assert payload["multiple_testing"]["confirmation_adds_new_trial"] is False
    assert payload["post_confirmation_policy"] == {
        "pass_requires_formal_validation_dag": True,
        "confirmation_pass_is_formal_admission": False,
        "confirmation_pass_is_forward_credit": False,
        "confirmation_pass_authorizes_testnet": False,
        "missing_or_invalid_data_policy": "FAIL_CLOSED",
    }
