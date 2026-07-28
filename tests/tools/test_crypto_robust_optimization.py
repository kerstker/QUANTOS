"""Operator-level contracts for the CUDA/CPU robust optimisation seam."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.research.robust_optimization import ALPHA_IDS, CandidateSpec, StressSpec, screen_reference
from tools.crypto_robust_optimization import (
    GpuUnavailable,
    _cpu_replay_candidate,
    _gpu_screen,
    _prepare_top_from_existing,
    _require_cuda,
)


def test_top_schedule_retains_real_adv_for_names_outside_current_legs(monkeypatch) -> None:
    from tools import top_contra_dry_run_diagnostic

    monkeypatch.setattr(
        top_contra_dry_run_diagnostic,
        "_prepare",
        lambda _panel: (
            [100],
            {100: {"BTC": 1.0}},
            {100: {}},
            {100: {}},
            [],
        ),
    )
    panel = SimpleNamespace(
        event_times=[100],
        instruments=("BTC", "ETH"),
        matrices={
            "close": [[100.0, 50.0]],
            "volume": [[10.0, 20.0]],
            "funding": [[0.0, 0.0]],
        },
    )

    schedule = _prepare_top_from_existing(panel)

    assert schedule.adv_by_period[100] == {"BTC": 1_000.0, "ETH": 1_000.0}


def test_cpu_replay_never_invents_full_research_inputs_or_exchange_filters() -> None:
    candidate = CandidateSpec((0.4, 0.4, 0.2), 0.75, 0.12)
    stress = StressSpec(4, 10, 0.10, 3)
    missing = _cpu_replay_candidate(candidate, stress, {"full_research_risk": {}})
    assert missing["status"] == "BLOCK"
    assert "full_research_risk_inputs_missing" in missing["reasons"][0]


def test_cpu_reference_is_deterministic_for_gpu_parity_fixture() -> None:
    alpha_returns = {
        ALPHA_IDS[0]: [0.01, 0.0, -0.005, 0.006, 0.002, 0.004, -0.001, 0.003, 0.002, 0.001],
        ALPHA_IDS[1]: [0.002, 0.004, 0.001, -0.002, 0.003, 0.001, 0.002, -0.001, 0.003, 0.002],
        ALPHA_IDS[2]: [-0.001, 0.002, 0.003, 0.001, -0.003, 0.004, 0.001, 0.002, 0.0, 0.003],
    }
    candidate = CandidateSpec((0.3, 0.5, 0.2), 1.0, 0.10)
    stress = StressSpec(4, 10, 0.10, 3)
    turnover = [0.2] * 10
    left = screen_reference(alpha_returns, candidate, stress, turnover=turnover)
    right = screen_reference(alpha_returns, candidate, stress, turnover=turnover)
    assert left.as_dict() == right.as_dict()


def test_gpu_small_screen_matches_cpu_reference_when_cuda_is_available() -> None:
    try:
        _require_cuda()
    except GpuUnavailable:
        pytest.skip("CUDA is unavailable; production path must report GPU_UNAVAILABLE")
    alpha_returns = {
        ALPHA_IDS[0]: [0.01, 0.0, -0.005, 0.006, 0.002, 0.004, -0.001, 0.003, 0.002, 0.001],
        ALPHA_IDS[1]: [0.002, 0.004, 0.001, -0.002, 0.003, 0.001, 0.002, -0.001, 0.003, 0.002],
        ALPHA_IDS[2]: [-0.001, 0.002, 0.003, 0.001, -0.003, 0.004, 0.001, 0.002, 0.0, 0.003],
    }
    candidate = CandidateSpec((0.3, 0.5, 0.2), 1.0, 0.10)
    stress = StressSpec(4, 10, 0.10, 3)
    turnover = [0.2] * 10
    _, cells = _gpu_screen(
        {"alpha_returns": alpha_returns, "turnover": turnover, "state_labels": ["mid"] * 10},
        candidates=[candidate], stresses=[stress],
    )
    cpu = screen_reference(alpha_returns, candidate, stress, turnover=turnover)
    gpu = cells[0]["metrics"]
    assert gpu["net_return"] == pytest.approx(cpu.net_return, rel=1e-9, abs=1e-9)
    assert gpu["vt_return"] == pytest.approx(cpu.vt_return, rel=1e-9, abs=1e-9)
    assert gpu["vt_sharpe"] == pytest.approx(cpu.vt_sharpe, rel=1e-8, abs=1e-8)
    assert gpu["vt_max_drawdown"] == pytest.approx(cpu.vt_max_drawdown, rel=1e-8, abs=1e-8)


def test_cpu_replay_with_complete_fixture_reaches_real_pipeline() -> None:
    candidate = CandidateSpec((1 / 3, 1 / 3, 1 / 3), 0.25, 0.15)
    stress = StressSpec(4, 10, 0.10, 3)
    periods = list(range(10))
    sleeve = {alpha: {"BTC": 0.0} for alpha in ALPHA_IDS}
    sleeve_by_period = {period: sleeve for period in periods}
    bundle = {
        "periods": periods,
        "sleeve_weights_by_period": sleeve_by_period,
        "prices_by_period": {period: {"BTC": 100.0 + period} for period in periods},
        "rates_by_period": {period: {"BTC": 0.0} for period in periods},
        "adv_by_period": {period: {"BTC": 1_000_000_000.0} for period in periods},
        "full_research_risk": {
            "capacity_cny": {"BTC": 1_000_000_000.0},
            "daily_volume_cny": 1_000_000_000.0,
            "total_aum": 1_000_000.0,
            "alpha_returns": {alpha: [0.0] * 10 for alpha in ALPHA_IDS},
            "regime_labels": ["mid"] * 10,
            "alpha_metadata": [
                {"age_days": 100.0, "validation_confidence": 1.0, "paper_confidence": 1.0,
                 "decay_flag": False, "overfit_flag": False, "leakage_flag": False}
            ] * 3,
        },
    }
    result = _cpu_replay_candidate(candidate, stress, bundle, filters={"BTC": (1.0, 1.0, 1.0)})
    assert result["status"] in {"PASS", "BLOCK"}
    assert "candidate_hash" in result
