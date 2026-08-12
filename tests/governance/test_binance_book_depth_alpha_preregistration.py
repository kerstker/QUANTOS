from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "config" / "binance_book_depth_alpha_preregistration.json"


def test_book_depth_alpha_preregistration_is_fixed_and_fail_closed() -> None:
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))

    assert payload["status"] == "PRE_REGISTERED_NOT_EVALUATED"
    assert payload["dataset_id"] == "binance_usdm_book_depth_curve_hourly_v2"
    assert payload["history_days"] == 90
    assert len(payload["universe"]) == 14
    assert payload["decision_interval_hours"] == 4
    assert payload["forward_horizon_hours"] == 4
    assert payload["long_count"] == payload["short_count"] == 3
    assert payload["one_way_cost_bps_per_unit_turnover"] == 6.0
    assert payload["cost_stress_multiplier"] == 2.0
    assert payload["minimum_common_days"] == 60
    assert payload["minimum_locked_decisions"] == 100
    assert len(payload["candidates"]) == 6
    assert payload["primary_candidate_id"] == payload["candidates"][0]["candidate_id"]
    assert len({item["candidate_id"] for item in payload["candidates"]}) == 6
    assert payload["primary_required_gates"] == {
        "locked_net_sharpe_minimum": 1.0,
        "locked_rank_ic_mean_minimum": 0.0,
        "locked_rank_ic_t_stat_minimum": 2.0,
        "positive_chronological_thirds_minimum": 2,
        "double_cost_net_sharpe_minimum": 0.0,
        "maximum_single_instrument_gross_pnl_fraction": 0.35,
    }
    assert payload["honesty"] == {
        "retrospective_history_is_forward_credit": False,
        "diagnostic_pass_is_formal_admission": False,
        "diagnostic_pass_authorizes_testnet": False,
        "missing_or_invalid_data_policy": "FAIL_CLOSED",
        "secondary_candidate_may_replace_primary": False,
    }
