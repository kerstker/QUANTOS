"""Rank #13 — honest 4-engine research-risk gate at the dry-run seam.

Runs only the engines the seam can honestly feed (market/drawdown/concentration/stress); the
other five are SKIPPED_NO_HONEST_INPUT, never run on fabricated data. Fail-closed."""
import math

import pytest

from services.risk.research_risk.domain.models import RiskConfig
from services.risk.research_risk.dry_run_guard import (
    DryRunResearchRiskInputs,
    evaluate_dry_run_risk,
    full_research_risk_approved,
)


def _benign():
    # a calm, diffuse, shallow-drawdown book: small returns, equity drifting up, spread weights
    returns = [0.001 if i % 2 == 0 else -0.0005 for i in range(60)]
    equity = []
    e = 1.0
    for r in returns:
        e *= (1 + r)
        equity.append(e)
    weights = {f"S{i}": (0.1 if i % 2 == 0 else -0.1) for i in range(10)}
    ts = [1_600_000_000_000 + i * 7 * 86400_000 for i in range(60)]
    return returns, weights, equity, ts


def test_benign_book_is_risk_approved_over_the_four_honest_engines():
    returns, weights, equity, ts = _benign()
    d = evaluate_dry_run_risk(returns, weights, equity, timestamps=ts)
    assert d["decision"] == "RISK_APPROVED"
    assert d["status"] == "DIAGNOSTIC_ONLY"
    assert full_research_risk_approved(d) is False
    assert d["gates_passed"] == 4 and d["gates_run"] == 4
    assert set(d["engines_run"]) == {"market", "drawdown", "concentration", "stress"}
    assert d["provenance"] == "REAL_RESEARCH_RISK_SUBSET"


def test_the_five_unfeedable_engines_are_honestly_skipped_never_faked():
    returns, weights, equity, ts = _benign()
    d = evaluate_dry_run_risk(returns, weights, equity, timestamps=ts)
    assert set(d["engines_skipped"]) == {"liquidity", "correlation", "regime", "capacity", "model_alpha"}
    assert all(v == "NO_HONEST_INPUT_AT_DRY_RUN_SEAM" for v in d["engines_skipped"].values())
    # a skipped engine must NOT appear as run/passed
    assert "correlation" not in d["engines_run"]


def test_deep_drawdown_book_is_rejected():
    # equity crashes 60% -> drawdown engine (threshold 25%) rejects
    returns = [-0.05] * 20 + [0.001] * 40
    equity, e = [], 1.0
    for r in returns:
        e *= (1 + r)
        equity.append(e)
    weights = {"A": 0.5, "B": -0.5}
    d = evaluate_dry_run_risk(returns, weights, equity)
    assert d["decision"] == "RISK_REJECTED"
    assert d["engines_run"]["drawdown"] is False
    assert any("drawdown" in r for r in d["reasons"])


def test_concentration_breach_is_rejected():
    returns, _w, equity, ts = _benign()
    concentrated = {"BIG": 0.9, "small": 0.1}          # 90% single name >> 15% threshold
    d = evaluate_dry_run_risk(returns, concentrated, equity, timestamps=ts)
    assert d["decision"] == "RISK_REJECTED"
    assert d["engines_run"]["concentration"] is False


def test_stress_uses_timestamps_for_real_replay_provenance():
    returns, weights, equity, ts = _benign()
    d = evaluate_dry_run_risk(returns, weights, equity, timestamps=ts)
    assert d["reports"]["stress"]["provenance"] == "REAL_HISTORICAL_REPLAY"
    # without timestamps it falls back to the labeled synthetic multipliers (Rank #7)
    d2 = evaluate_dry_run_risk(returns, weights, equity, timestamps=None)
    assert d2["reports"]["stress"]["provenance"] == "SYNTHETIC_MULTIPLIER_UNCALIBRATED"


def test_engine_error_fails_closed_not_approved():
    # empty returns -> market/stress engines fail-closed (empty-input guards) -> not approved
    d = evaluate_dry_run_risk([], {"A": 1.0}, [], timestamps=None)
    assert d["decision"] == "RISK_REJECTED"


def test_is_deterministic():
    returns, weights, equity, ts = _benign()
    assert evaluate_dry_run_risk(returns, weights, equity, timestamps=ts) == \
        evaluate_dry_run_risk(returns, weights, equity, timestamps=ts)


def test_market_annualisation_matches_weekly_cadence_and_binds_config():
    returns, weights, equity, ts = _benign()
    daily = evaluate_dry_run_risk(
        returns, weights, equity, timestamps=ts,
        config=RiskConfig(periods_per_year=252),
    )
    weekly = evaluate_dry_run_risk(
        returns, weights, equity, timestamps=ts,
        config=RiskConfig(periods_per_year=52),
    )
    assert weekly["reports"]["market"]["volatility"] == pytest.approx(
        daily["reports"]["market"]["volatility"] * math.sqrt(52 / 252),
        abs=1e-6,
    )
    assert weekly["config_provenance"] != daily["config_provenance"]


@pytest.mark.parametrize("periods_per_year", [0, -1, float("nan"), float("inf")])
def test_risk_config_rejects_invalid_annualisation_basis(periods_per_year):
    with pytest.raises(ValueError, match="periods_per_year"):
        RiskConfig(periods_per_year=periods_per_year)


def _full_inputs(returns, weights):
    return DryRunResearchRiskInputs(
        capacity_cny={name: 1.0e9 for name in weights},
        daily_volume_cny=1.0e9,
        total_aum=1.0e8,
        alpha_returns={
            "alpha_a": [float(value) for value in returns],
            # A second independently observed stream (not a fabricated copy or
            # sign flip) keeps the correlation engine meaningfully feedable.
            "alpha_b": [0.0003 if i % 3 == 0 else (-0.0002 if i % 3 == 1 else 0.0001)
                         for i in range(len(returns))],
        },
        regime_labels=["normal"] * len(returns),
        alpha_metadata=[
            {
                "alpha_id": "alpha_a",
                "age_days": 10.0,
                "validation_confidence": 0.8,
                "paper_confidence": 0.8,
                "decay_flag": False,
                "overfit_flag": False,
                "leakage_flag": False,
            },
            {
                "alpha_id": "alpha_b",
                "age_days": 12.0,
                "validation_confidence": 0.8,
                "paper_confidence": 0.8,
                "decay_flag": False,
                "overfit_flag": False,
                "leakage_flag": False,
            },
        ],
    )


def test_strict_full_contract_without_inputs_is_pending_and_fail_closed():
    returns, weights, equity, ts = _benign()
    d = evaluate_dry_run_risk(
        returns, weights, equity, timestamps=ts, require_full_inputs=True
    )
    assert d["status"] == "PENDING"
    assert d["decision"] == "RISK_REJECTED"
    assert d["gates_run"] == 0 and d["gates_passed"] == 0
    assert set(d["engines_pending"]) == {
        "liquidity", "correlation", "regime", "capacity", "model_alpha"
    }
    assert "full_research_risk_inputs_missing" in d["reasons"]


def test_strict_full_contract_invalid_alignment_stays_pending():
    returns, weights, equity, ts = _benign()
    inputs = _full_inputs(returns, weights)
    invalid = DryRunResearchRiskInputs(
        capacity_cny=inputs.capacity_cny,
        daily_volume_cny=inputs.daily_volume_cny,
        total_aum=inputs.total_aum,
        alpha_returns={"alpha_a": list(inputs.alpha_returns["alpha_a"][:-1]),
                       "alpha_b": list(inputs.alpha_returns["alpha_b"])},
        regime_labels=inputs.regime_labels,
        alpha_metadata=inputs.alpha_metadata,
    )
    d = evaluate_dry_run_risk(
        returns, weights, equity, timestamps=ts,
        full_inputs=invalid, require_full_inputs=True,
    )
    assert d["status"] == "PENDING"
    assert any("alpha_returns_invalid" in reason for reason in d["reasons"])


def test_strict_full_contract_runs_all_nine_engines_from_real_inputs():
    returns, weights, equity, ts = _benign()
    d = evaluate_dry_run_risk(
        returns, weights, equity, timestamps=ts,
        full_inputs=_full_inputs(returns, weights), require_full_inputs=True,
    )
    assert d["status"] == "APPROVED"
    assert d["decision"] == "RISK_APPROVED"
    assert d["gates_run"] == d["gates_passed"] == 9
    assert set(d["engines_run"]) == {
        "market", "liquidity", "concentration", "correlation", "drawdown",
        "regime", "stress", "capacity", "model_alpha",
    }
    assert d["engines_skipped"] == {} and d["engines_pending"] == {}
    assert d["provenance"] == "REAL_RESEARCH_RISK_FULL"


def test_strict_full_contract_preserves_engine_rejection_reason():
    returns, weights, equity, ts = _benign()
    # Every individual capacity is too small for the supplied AUM, so the real
    # capacity engine rejects; no fallback to the four-engine subset is allowed.
    inputs = _full_inputs(returns, weights)
    constrained = DryRunResearchRiskInputs(
        capacity_cny={name: 1.0 for name in weights},
        daily_volume_cny=inputs.daily_volume_cny,
        total_aum=inputs.total_aum,
        alpha_returns=inputs.alpha_returns,
        regime_labels=inputs.regime_labels,
        alpha_metadata=inputs.alpha_metadata,
    )
    d = evaluate_dry_run_risk(
        returns, weights, equity, timestamps=ts,
        full_inputs=constrained, require_full_inputs=True,
    )
    assert d["status"] == "REJECTED"
    assert d["engines_run"]["capacity"] is False
    assert any("Capacity:" in reason for reason in d["reasons"])
