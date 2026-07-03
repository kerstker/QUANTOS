"""Roadmap Rank #7 — StressTestEngine must stress against REAL historical crisis
windows (services/research/realism/scenario.py: LUNA/FTX/banking replay + the
endogenous worst-drawdown window), not fabricated fixed multipliers. The legacy
multiplier path survives only as an HONESTLY LABELED fallback for callers without
timestamps; bad timestamp input fails closed."""
import math

from services.research.realism.scenario import iso_to_ms
from services.risk.research_risk.domain.gate import RiskApprovalGate
from services.risk.research_risk.domain.models import RiskConfig, RiskDecision, RiskRun
from services.risk.research_risk.domain.risk_modules import StressTestEngine

DAY_MS = 86_400_000


def _crash_series():
    """Weekly-ish book history covering the FTX and banking windows (NOT 2021-05 / LUNA),
    with a ~38.8% drawdown INSIDE the FTX window and a NaN pair that must be filtered
    without breaking (return, timestamp) alignment."""
    rows = [
        ("2022-08-01", 0.01), ("2022-08-15", 0.01), ("2022-09-01", 0.01),
        ("2022-09-15", 0.01), ("2022-10-01", 0.01), ("2022-10-15", 0.01),
        ("2022-11-01", 0.01),
        ("2022-11-07", -0.20), ("2022-11-10", -0.15), ("2022-11-15", -0.10),  # FTX 11-05..11-21
        ("2022-12-01", 0.01), ("2023-01-15", 0.01), ("2023-02-15", 0.01),
        ("2023-03-10", 0.01),                                                  # banking 03-08..03-20
        ("2023-04-01", float("nan")),                                          # filtered as a PAIR
        ("2023-04-15", 0.01),
    ]
    return [r for _d, r in rows], [iso_to_ms(d) for d, _r in rows]


def test_real_replay_evaluates_covered_crisis_windows_and_breaches():
    returns, ts = _crash_series()
    report = StressTestEngine().evaluate(returns, RiskConfig(), timestamps=ts)
    assert report.provenance == "REAL_HISTORICAL_REPLAY"
    # only the two covered crises + the always-computable endogenous worst window
    assert set(report.scenario_losses) == {"2022-11 FTX", "2023-03 banking/USDC",
                                           "worst_realized_window"}
    assert report.scenarios_skipped == 2            # 2021-05 crash + 2022-05 LUNA: no coverage
    assert report.total_scenarios == 3
    # FTX-window drawdown: 1 - 0.8*0.85*0.9 = 0.388 > max_stress_loss 0.35 -> breach
    assert abs(report.scenario_losses["2022-11 FTX"] - 0.388) < 0.01
    assert report.scenario_losses["2023-03 banking/USDC"] == 0.0
    assert report.scenarios_survived == 1
    assert report.passed is False
    assert abs(report.worst_stress_loss - 0.388) < 0.01
    assert report.worst_scenario in ("2022-11 FTX", "worst_realized_window")


def test_real_replay_passes_when_crisis_windows_are_mild():
    _returns, ts = _crash_series()
    mild = [0.005 if i % 2 else -0.004 for i in range(len(ts))]
    report = StressTestEngine().evaluate(mild, RiskConfig(), timestamps=ts)
    assert report.provenance == "REAL_HISTORICAL_REPLAY"
    assert report.passed is True
    assert report.scenarios_survived == report.total_scenarios == 3
    assert report.worst_stress_loss < 0.05


def test_no_timestamps_falls_back_to_labeled_synthetic_multipliers():
    # legacy callers keep the OLD numbers, but the report now carries an honest label
    report = StressTestEngine().evaluate([0.01, -0.02, 0.03, -0.01], RiskConfig())
    assert report.provenance == "SYNTHETIC_MULTIPLIER_UNCALIBRATED"
    assert report.total_scenarios == 4
    assert report.worst_scenario == "liquidity_shock"            # 1.5x is always the max
    assert abs(report.worst_stress_loss - 0.045) < 1e-12         # base_loss 0.03 * 1.5
    assert set(report.scenario_losses) == {"market_gap", "liquidity_shock",
                                           "vol_spike", "correlation_break"}
    assert report.passed is True


def test_mismatched_timestamps_fail_closed():
    report = StressTestEngine().evaluate([0.01, 0.02], RiskConfig(), timestamps=[123])
    assert report.passed is False
    assert report.provenance == "INVALID_INPUT"
    assert report.total_scenarios == 0


def test_no_finite_pairs_fail_closed():
    report = StressTestEngine().evaluate([float("nan")], RiskConfig(), timestamps=[123])
    assert report.passed is False
    assert report.provenance == "INVALID_INPUT"


def _pass_inputs():
    returns = [0.001, -0.001, 0.0015, -0.0005, 0.001, -0.001, 0.0008, -0.0007] * 3
    weights = {f"a{i}": 0.1 for i in range(10)}
    equity = [100.0 + i * 0.1 for i in range(30)]
    capacity = {f"a{i}": 1.0e8 for i in range(10)}
    alpha_returns = {
        "a0": [0.01, -0.01, 0.01, -0.01, 0.01, -0.01],
        "a1": [0.01, 0.01, -0.01, -0.01, 0.01, 0.01],
        "a2": [0.01, -0.01, -0.01, 0.01, 0.0, 0.0],
    }
    meta = [{"validation_confidence": 0.8, "paper_confidence": 0.8, "age_days": 10,
             "decay_flag": False, "overfit_flag": False, "leakage_flag": False}]
    return dict(returns=returns, weights=weights, equity_curve=equity, capacity_cny=capacity,
                alpha_returns=alpha_returns, regime_labels=["normal"] * len(returns),
                alpha_metadata=meta, daily_volume_cny=1e9, total_aum=1e8)


def test_gate_threads_timestamps_and_exposes_reports():
    inputs = _pass_inputs()
    ts = [iso_to_ms("2022-10-03") + i * 7 * DAY_MS for i in range(len(inputs["returns"]))]
    run = RiskRun(portfolio_id="p")
    d = RiskApprovalGate().evaluate(run=run, timestamps=ts, **inputs)
    assert d.decision == RiskDecision.RISK_APPROVED                  # mild book still approves
    stress = run.gate_reports["stress"]
    assert stress.provenance == "REAL_HISTORICAL_REPLAY"             # timestamps reached the engine
    assert "2022-11 FTX" in stress.scenario_losses                   # weekly grid covers FTX window
    assert set(run.gate_reports) == {"market", "liquidity", "concentration", "correlation",
                                     "drawdown", "regime", "stress", "capacity", "model_alpha"}


def test_gate_without_timestamps_keeps_legacy_labeled_fallback():
    run = RiskRun(portfolio_id="p")
    d = RiskApprovalGate().evaluate(run=run, **_pass_inputs())
    assert d.decision == RiskDecision.RISK_APPROVED
    assert run.gate_reports["stress"].provenance == "SYNTHETIC_MULTIPLIER_UNCALIBRATED"


def test_gate_engine_exception_stores_none_report():
    gate = RiskApprovalGate()

    class _Boom:
        def evaluate(self, *a, **k):
            raise RuntimeError("boom")

    gate._engines["stress"] = _Boom()
    run = RiskRun(portfolio_id="p")
    gate.evaluate(run=run, **_pass_inputs())
    assert run.gate_reports["stress"] is None                        # exception -> None, fail-closed
    assert run.gate_results["stress"] is False
