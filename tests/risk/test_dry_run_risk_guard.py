"""Rank #13 — honest 4-engine research-risk gate at the dry-run seam.

Runs only the engines the seam can honestly feed (market/drawdown/concentration/stress); the
other five are SKIPPED_NO_HONEST_INPUT, never run on fabricated data. Fail-closed."""
import pytest

from services.risk.research_risk.domain.models import RiskConfig
from services.risk.research_risk.dry_run_guard import evaluate_dry_run_risk


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
