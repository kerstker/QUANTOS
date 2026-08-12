from __future__ import annotations

import math

import pytest

from services.research.book_depth_alpha import (
    BookDepthAlphaError,
    candidate_signals,
    evaluate_confirmation,
    evaluate_exploration,
    evaluate_registered_candidates,
)


INSTRUMENTS = tuple(f"S{index:02d}USDT" for index in range(14))
HOUR_MS = 3_600_000


def _score(index: int, decision: int, level_index: int) -> float:
    base = ((index + decision) % 14 - 6.5) / 20.0
    return 0.7 * base + 0.08 * math.sin(
        index * (level_index + 1) * 0.7 + decision * 0.31 + level_index
    )


def _observation(
    event_time: int,
    instrument: str,
    index: int,
    step: int,
    *,
    price: float | None = None,
) -> dict:
    decision = step // 4
    levels = {}
    for level_index, radius in enumerate(("0.2", "1", "2", "3", "4", "5")):
        score = _score(index, decision, level_index)
        scale = 1000.0 * float(radius)
        levels[radius] = {
            "bid_notional_p10": scale * (1.0 + score),
            "ask_notional_p10": scale * (1.0 - score),
        }
    reference_price = (
        100.0 * math.exp(step * (index - 6.5) * 0.0001)
        if price is None
        else price
    )
    return {
        "dataset": "binance_usdm_book_depth_curve_hourly_v2",
        "instrument_id": instrument,
        "event_time_ms": event_time,
        "knowledge_time_ms": event_time + 1,
        "external_price_alignment": "PASS",
        "external_price_reference": {"reference_price": reference_price},
        "levels": levels,
    }


def _contract() -> dict:
    candidate_ids = (
        "depth_imbalance_0p2_continuation_4h_v1",
        "depth_imbalance_1p0_continuation_4h_v1",
        "depth_imbalance_5p0_continuation_4h_v1",
        "depth_near_minus_far_continuation_4h_v1",
        "depth_near_concentration_skew_4h_v1",
        "depth_curve_mean_imbalance_4h_v1",
    )
    return {
        "dataset_id": "binance_usdm_book_depth_curve_hourly_v2",
        "universe": INSTRUMENTS,
        "decision_interval_hours": 4,
        "forward_horizon_hours": 4,
        "long_count": 3,
        "short_count": 3,
        "one_way_cost_bps_per_unit_turnover": 6.0,
        "cost_stress_multiplier": 2.0,
        "annualization_periods": 2190,
        "locked_oos_fraction": 1 / 3,
        "minimum_locked_decisions": 20,
        "primary_candidate_id": candidate_ids[0],
        "candidates": [{"candidate_id": value} for value in candidate_ids],
        "primary_required_gates": {
            "locked_net_sharpe_minimum": 1.0,
            "locked_rank_ic_mean_minimum": 0.0,
            "locked_rank_ic_t_stat_minimum": 2.0,
            "positive_chronological_thirds_minimum": 2,
            "double_cost_net_sharpe_minimum": 0.0,
            "maximum_single_instrument_gross_pnl_fraction": 0.35,
        },
    }


def test_candidate_signals_match_frozen_definitions() -> None:
    observation = _observation(0, INSTRUMENTS[10], 10, 0)
    signals = candidate_signals(observation)

    assert len(signals) == 6
    assert signals["depth_imbalance_0p2_continuation_4h_v1"] == pytest.approx(
        _score(10, 0, 0)
    )
    assert len(set(signals.values())) > 3


def test_registered_diagnostic_uses_locked_oos_costs_and_never_authorizes() -> None:
    observations = []
    prices = {instrument: 100.0 for instrument in INSTRUMENTS}
    for decision in range(101):
        step = decision * 4
        event_time = step * HOUR_MS
        for index, instrument in enumerate(INSTRUMENTS):
            observations.append(
                _observation(
                    event_time,
                    instrument,
                    index,
                    step,
                    price=prices[instrument],
                )
            )
        for index, instrument in enumerate(INSTRUMENTS):
            primary_score = _score(index, decision, 0)
            noise = 0.0015 * math.sin(decision * 0.91 + index * 1.73)
            prices[instrument] *= 1.0 + 0.015 * primary_score + noise

    result = evaluate_registered_candidates(observations, _contract())

    assert result["status"] == "PASS"
    assert result["candidate_count"] == result["trial_count"] == 6
    assert result["common_decision_count"] == 100
    primary = result["candidates"][result["primary_candidate_id"]]
    assert primary["locked_oos"]["period_count"] >= 20
    assert primary["mean_turnover"] > 0
    assert result["formal_admission"] is False
    assert result["forward_credit"] is False
    assert result["testnet_authorized"] is False
    assert result["order_submission_authorized"] is False


def test_registered_diagnostic_rejects_unaligned_or_incomplete_input() -> None:
    observation = _observation(0, INSTRUMENTS[0], 0, 0)
    with pytest.raises(BookDepthAlphaError, match="unaligned"):
        evaluate_registered_candidates(
            [{**observation, "external_price_alignment": "UNASSESSED"}],
            _contract(),
        )

    with pytest.raises(BookDepthAlphaError, match="no exact common"):
        evaluate_registered_candidates([observation], _contract())


def _exploration_contract() -> dict:
    candidates = [item["candidate_id"] for item in _contract()["candidates"]]
    return {
        "dataset_id": "binance_usdm_book_depth_curve_hourly_v2",
        "candidate_ids": candidates,
        "directions": [1, -1],
        "horizon_hours": [4, 8],
        "long_count": 3,
        "short_count": 3,
        "one_way_cost_bps_per_unit_turnover": 6.0,
        "cost_stress_multiplier": 2.0,
        "minimum_decisions": 20,
        "trial_count": 24,
        "selection_policy": {
            "net_sharpe_minimum": 1.0,
            "double_cost_net_sharpe_minimum": 0.0,
            "rank_ic_absolute_t_stat_minimum": 2.0,
            "positive_chronological_thirds_minimum": 2,
            "maximum_single_instrument_gross_pnl_fraction": 0.35,
        },
    }


def test_exploration_counts_complete_grid_and_cannot_authorize() -> None:
    observations = []
    prices = {instrument: 100.0 for instrument in INSTRUMENTS}
    for decision in range(101):
        step = decision * 4
        event_time = step * HOUR_MS
        for index, instrument in enumerate(INSTRUMENTS):
            observations.append(
                _observation(
                    event_time,
                    instrument,
                    index,
                    step,
                    price=prices[instrument],
                )
            )
        for index, instrument in enumerate(INSTRUMENTS):
            primary_score = _score(index, decision, 0)
            noise = 0.001 * math.sin(decision * 0.91 + index * 1.73)
            prices[instrument] *= 1.0 + 0.02 * primary_score + noise

    result = evaluate_exploration(
        observations, _contract(), _exploration_contract()
    )

    assert result["trial_count"] == result["multiple_testing_trial_increment"] == 24
    assert len(result["trials"]) == 24
    assert result["status"] in {"LEAD_SELECTED", "NO_LEAD"}
    assert result["confirmation_required"] is True
    assert result["formal_admission"] is False
    assert result["forward_credit"] is False
    assert result["testnet_authorized"] is False
    assert result["order_submission_authorized"] is False
    plus = result["trials"][
        "depth_imbalance_0p2_continuation_4h_v1|direction=+1|horizon=4h"
    ]
    minus = result["trials"][
        "depth_imbalance_0p2_continuation_4h_v1|direction=-1|horizon=4h"
    ]
    assert plus["net"]["net_sharpe"] > minus["net"]["net_sharpe"]


def test_exploration_rejects_grid_or_candidate_drift() -> None:
    contract = _exploration_contract()
    contract["trial_count"] = 23
    with pytest.raises(BookDepthAlphaError, match="trial count"):
        evaluate_exploration([], _contract(), contract)

    contract = _exploration_contract()
    contract["candidate_ids"] = list(reversed(contract["candidate_ids"]))
    with pytest.raises(BookDepthAlphaError, match="candidate population"):
        evaluate_exploration([], _contract(), contract)


def _confirmation_contract() -> dict:
    return {
        "dataset_id": "binance_usdm_book_depth_curve_hourly_v2",
        "candidate_id": "depth_imbalance_0p2_continuation_4h_v1",
        "direction": 1,
        "horizon_hours": 4,
        "universe": INSTRUMENTS,
        "long_count": 3,
        "short_count": 3,
        "one_way_cost_bps_per_unit_turnover": 6.0,
        "cost_stress_multiplier": 2.0,
        "annualization_periods": 2190.0,
        "minimum_decisions": 20,
        "required_gates": {
            "net_sharpe_minimum": 1.0,
            "net_mean_return_minimum": 0.0,
            "double_cost_net_sharpe_minimum": 0.0,
            "rank_ic_mean_minimum": 0.0,
            "rank_ic_t_stat_minimum": 2.0,
            "positive_chronological_thirds_minimum": 2,
            "maximum_single_instrument_gross_pnl_fraction": 0.35,
        },
    }


def test_confirmation_applies_fixed_gates_and_cannot_authorize() -> None:
    observations = []
    prices = {instrument: 100.0 for instrument in INSTRUMENTS}
    for decision in range(101):
        step = decision * 4
        event_time = step * HOUR_MS
        for index, instrument in enumerate(INSTRUMENTS):
            observations.append(
                _observation(
                    event_time,
                    instrument,
                    index,
                    step,
                    price=prices[instrument],
                )
            )
        for index, instrument in enumerate(INSTRUMENTS):
            score = _score(index, decision, 0)
            noise = 0.001 * math.sin(decision * 0.91 + index * 1.73)
            prices[instrument] *= 1.0 + 0.02 * score + noise

    result = evaluate_confirmation(observations, _confirmation_contract())

    assert result["status"] == "PASS"
    assert result["decision_count"] == 100
    assert result["net"]["net_sharpe"] > 1.0
    assert result["double_cost_net"]["net_sharpe"] > 0.0
    assert result["rank_ic"]["t_stat"] > 2.0
    assert all(result["gate_checks"].values())
    assert result["formal_validation_dag_required"] is True
    assert result["formal_admission"] is False
    assert result["forward_credit"] is False
    assert result["testnet_authorized"] is False
    assert "period_evidence" not in result

    auditable = evaluate_confirmation(
        observations, _confirmation_contract(), include_period_evidence=True
    )
    assert len(auditable["period_evidence"]) == auditable["decision_count"]
    assert auditable["period_evidence"][0]["event_time_ms"] == 0
    assert set(auditable["period_evidence"][0]) == {
        "event_time_ms",
        "gross_return",
        "turnover",
        "net_return",
        "double_cost_net_return",
        "rank_ic",
        "instrument_contributions",
    }


def test_confirmation_rejects_insufficient_or_unaligned_input() -> None:
    contract = _confirmation_contract()
    with pytest.raises(BookDepthAlphaError, match="decision count"):
        evaluate_confirmation([], contract)
    observation = _observation(0, INSTRUMENTS[0], 0, 0)
    with pytest.raises(BookDepthAlphaError, match="unaligned"):
        evaluate_confirmation(
            [{**observation, "external_price_alignment": "UNASSESSED"}], contract
        )
