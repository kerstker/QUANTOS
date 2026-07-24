"""Contract tests for the strict crypto robust-optimisation primitives."""

from __future__ import annotations

import math

import pytest

from services.research.robust_optimization import (
    ALPHA_IDS,
    CandidateSpec,
    StressSpec,
    aggregate_stress,
    candidate_config_hash,
    generate_simplex_weights,
    iter_candidate_specs,
    iter_stress_specs,
    screen_reference,
    select_stable_regions,
    strict_backtest_gate,
    vol_target_returns,
)


def test_simplex_is_complete_and_unique() -> None:
    weights = generate_simplex_weights()
    assert len(weights) == 66
    assert len(set(weights)) == len(weights)
    assert all(abs(sum(row) - 1.0) <= 1e-12 for row in weights)
    assert (1.0, 0.0, 0.0) in weights
    assert (0.0, 1.0, 0.0) in weights
    assert (0.0, 0.0, 1.0) in weights


def test_grid_has_no_duplicate_candidates_or_stress_cells() -> None:
    candidates = iter_candidate_specs()
    stress = iter_stress_specs()
    assert len(candidates) == 66 * 7 * 5
    assert len({candidate_config_hash(row) for row in candidates}) == len(candidates)
    assert len(stress) == 6 * 3 * 3 * 3
    assert len({row.as_dict().__repr__() for row in stress}) == len(stress)


def test_pit_vol_target_does_not_use_future_returns() -> None:
    prefix = vol_target_returns([0.01] * 12, lookback=3, leverage_cap=1.0)
    changed_future = vol_target_returns([0.01] * 11 + [0.90], lookback=3, leverage_cap=1.0)
    assert prefix[:11] == changed_future[:11]


def test_cpu_reference_costs_are_explicit_and_metrics_are_reconciled() -> None:
    alpha_returns = {
        ALPHA_IDS[0]: [0.01, -0.005, 0.012, 0.003, -0.002, 0.006, 0.004, 0.001, 0.002, -0.001],
        ALPHA_IDS[1]: [0.004, 0.002, -0.001, 0.006, 0.003, 0.002, 0.001, -0.002, 0.004, 0.003],
        ALPHA_IDS[2]: [-0.001, 0.003, 0.002, -0.004, 0.005, 0.001, 0.002, 0.002, -0.001, 0.004],
    }
    candidate = CandidateSpec((0.4, 0.4, 0.2), 0.75, 0.12)
    stress = StressSpec(4, 10, 0.10, 3)
    metrics = screen_reference(alpha_returns, candidate, stress, turnover=[0.1] * 10)
    assert metrics.n_periods == 10
    assert metrics.fee_drag > 0.0
    assert metrics.impact_drag > 0.0
    assert math.isfinite(metrics.vt_sharpe)
    with pytest.raises(ValueError, match="turnover"):
        screen_reference(alpha_returns, candidate, stress, turnover=None)  # type: ignore[arg-type]


def test_strict_gate_fails_closed_on_missing_or_weak_evidence() -> None:
    candidate = CandidateSpec((1.0, 0.0, 0.0), 1.0, 0.10)
    blocked = strict_backtest_gate(
        candidate=candidate,
        data_ok=True,
        dsr=None,
        pbo=0.1,
        oos_vt_sharpe=1.0,
        walk_forward_profitable_fraction=1.0,
        market_state_profitable_fraction=1.0,
        stress=None,
        full_research_risk=False,
    )
    assert blocked.status == "BLOCK"
    assert "dsr_missing" in blocked.reasons
    assert "stress_evidence_missing" in blocked.reasons
    assert "full_nine_engine_research_risk_missing" in blocked.reasons


def test_stress_gate_requires_worst_cell_and_seventy_five_percent_positive() -> None:
    candidate = CandidateSpec((1.0, 0.0, 0.0), 1.0, 0.10)
    good = strict_backtest_gate(
        candidate=candidate,
        data_ok=True,
        dsr=0.96,
        pbo=0.2,
        oos_vt_sharpe=0.9,
        walk_forward_profitable_fraction=0.75,
        market_state_profitable_fraction=0.75,
        stress={"cells": 4, "worst_vt_sharpe": 0.81, "worst_vt_max_drawdown": 0.09, "positive_fraction": 0.75},
        full_research_risk=True,
    )
    assert good.passed
    bad = strict_backtest_gate(
        candidate=candidate,
        data_ok=True,
        dsr=0.96,
        pbo=0.2,
        oos_vt_sharpe=0.9,
        walk_forward_profitable_fraction=0.75,
        market_state_profitable_fraction=0.75,
        stress={"cells": 4, "worst_vt_sharpe": 0.81, "worst_vt_max_drawdown": 0.09, "positive_fraction": 0.50},
        full_research_risk=True,
    )
    assert not bad.passed
    assert "stress_positive_fraction_below_hard_floor" in bad.reasons


def test_stable_region_rejects_isolated_peak_and_keeps_neighboring_cells() -> None:
    center = CandidateSpec((1.0, 0.0, 0.0), 1.0, 0.10)
    neighbor = CandidateSpec((0.9, 0.1, 0.0), 1.0, 0.10)
    def record(candidate: CandidateSpec, sharpe: float) -> dict:
        return {
            "candidate": candidate,
            "candidate_hash": candidate_config_hash(candidate),
            "gate": {"status": "PASS"},
            "stress": {
                "cells": 4,
                "worst_vt_sharpe": sharpe,
                "worst_vt_max_drawdown": 0.08,
                "positive_fraction": 0.8,
                "median_vt_return": 0.2,
                "median_turnover": 1.0,
                "fee_drag": 0.01,
                "impact_drag": 0.01,
            },
        }
    assert select_stable_regions([record(center, 2.0)]) == ()
    regions = select_stable_regions([record(center, 2.0), record(neighbor, 1.0)])
    assert len(regions) == 1
    assert regions[0].size == 2
    assert regions[0].selected_hash == candidate_config_hash(center)


def test_aggregate_stress_is_fail_closed_for_empty_input() -> None:
    assert aggregate_stress(()) ["status"] == "BLOCK"


def test_forward_gate_requires_eight_fresh_positive_consistent_periods() -> None:
    candidate = CandidateSpec((0.4, 0.4, 0.2), 0.75, 0.12)
    common = dict(
        candidate=candidate,
        data_ok=True,
        dsr=0.99,
        pbo=0.10,
        oos_vt_sharpe=1.0,
        walk_forward_profitable_fraction=0.80,
        market_state_profitable_fraction=0.80,
        stress={"cells": 4, "worst_vt_sharpe": 0.9, "worst_vt_max_drawdown": 0.08, "positive_fraction": 0.80},
        full_research_risk=True,
        require_forward=True,
    )
    pending = strict_backtest_gate(**common, forward={"valid_periods": 7, "data_fresh": True, "positive_net": True, "ledger_consistent": True, "max_drawdown": 0.05})
    assert not pending.passed
    assert "forward_less_than_8_periods" in pending.reasons
    stale = strict_backtest_gate(**common, forward={"valid_periods": 8, "data_fresh": False, "positive_net": True, "ledger_consistent": True, "max_drawdown": 0.05})
    assert "forward_data_not_fresh" in stale.reasons
    negative = strict_backtest_gate(**common, forward={"valid_periods": 8, "data_fresh": True, "positive_net": False, "ledger_consistent": True, "max_drawdown": 0.05})
    assert "forward_net_not_positive" in negative.reasons
    inconsistent = strict_backtest_gate(**common, forward={"valid_periods": 8, "data_fresh": True, "positive_net": True, "ledger_consistent": False, "max_drawdown": 0.05})
    assert "forward_ledger_inconsistent" in inconsistent.reasons
