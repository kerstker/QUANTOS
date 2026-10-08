import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_depth_exploration_grid_is_fixed_and_counts_every_trial() -> None:
    payload = json.loads(
        (ROOT / "config" / "binance_book_depth_alpha_exploration.json").read_text(
            encoding="utf-8"
        )
    )

    assert payload["status"] == "FROZEN_BEFORE_EXPLORATION"
    assert len(payload["candidate_ids"]) == 6
    assert payload["directions"] == [1, -1]
    assert payload["horizon_hours"] == [4, 8, 12, 24, 48]
    assert payload["trial_count"] == 6 * 2 * 5 == 60
    assert payload["one_way_cost_bps_per_unit_turnover"] == 6.0
    assert payload["cost_stress_multiplier"] == 2.0
    assert payload["confirmation_policy"] == {
        "required": True,
        "history_relation": "STRICTLY_EARLIER_NON_OVERLAPPING_90D",
        "selection_may_use_confirmation_data": False,
        "formal_admission_from_exploration": False,
        "testnet_authorized": False,
    }
