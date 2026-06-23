"""Audit M3 — the 9-engine research-risk gate must be fail-CLOSED: empty/degenerate
inputs and engine exceptions can never inflate gates_passed or reach RISK_APPROVED.
(Previously several engines returned passed=True on empty inputs and the gate had
no exception aggregation.) This gate feeds only the read-only console snapshot, but
the 'deterministic, fail-closed' claim must actually hold."""
from services.risk.research_risk.domain.gate import RiskApprovalGate
from services.risk.research_risk.domain.models import RiskDecision, RiskRun


def _pass_inputs():
    # a low-risk portfolio that clears all 9 gates
    returns = [0.001, -0.001, 0.0015, -0.0005, 0.001, -0.001, 0.0008, -0.0007] * 3
    weights = {f"a{i}": 0.1 for i in range(10)}              # gross 1.0, max 0.1, eff_n 10
    equity = [100.0 + i * 0.1 for i in range(30)]            # monotone up -> ~0 drawdown
    capacity = {f"a{i}": 1.0e8 for i in range(10)}
    alpha_returns = {
        "a0": [0.01, -0.01, 0.01, -0.01, 0.01, -0.01],
        "a1": [0.01, 0.01, -0.01, -0.01, 0.01, 0.01],       # ~uncorrelated with a0
        "a2": [0.01, -0.01, -0.01, 0.01, 0.0, 0.0],
    }
    meta = [{"validation_confidence": 0.8, "paper_confidence": 0.8, "age_days": 10,
             "decay_flag": False, "overfit_flag": False, "leakage_flag": False}]
    return dict(returns=returns, weights=weights, equity_curve=equity, capacity_cny=capacity,
                alpha_returns=alpha_returns, regime_labels=["normal"] * len(returns),
                alpha_metadata=meta, daily_volume_cny=1e9, total_aum=1e8)


def test_all_gates_pass_approves():
    run = RiskRun(portfolio_id="p")
    d = RiskApprovalGate().evaluate(run=run, **_pass_inputs())
    assert d.decision == RiskDecision.RISK_APPROVED
    assert d.gates_passed == 9 and all(run.gate_results.values())


def test_empty_inputs_fail_closed_not_approved():
    # the bug: stress/capacity/correlation/liquidity used to PASS on empty inputs
    run = RiskRun(portfolio_id="p")
    d = RiskApprovalGate().evaluate(run=run, returns=[], weights={}, equity_curve=[],
                                    capacity_cny={}, alpha_returns={})
    assert d.decision not in (RiskDecision.RISK_APPROVED, RiskDecision.RISK_APPROVED_WITH_LIMITS)
    for gate in ("liquidity", "correlation", "stress", "capacity", "concentration"):
        assert run.gate_results[gate] is False, gate


def test_engine_exception_is_failed_not_a_silent_pass():
    gate = RiskApprovalGate()

    class _Boom:
        def evaluate(self, *a, **k):
            raise RuntimeError("engine blew up")

    gate._engines["stress"] = _Boom()
    run = RiskRun(portfolio_id="p")
    d = gate.evaluate(run=run, **_pass_inputs())     # everything else would pass
    assert run.gate_results["stress"] is False        # exception -> failed, not crash, not pass
    assert d.decision != RiskDecision.RISK_APPROVED
    assert any("stress: engine error" in r for r in d.reasons)


def test_single_engine_failure_maps_to_its_rejection_reason():
    run = RiskRun(portfolio_id="p")
    inp = _pass_inputs()
    inp["returns"] = [0.2, -0.25, 0.3, -0.35, 0.2, -0.3] * 3   # huge vol -> market fails first
    d = RiskApprovalGate().evaluate(run=run, **inp)
    assert d.decision == RiskDecision.REJECTED_MARKET_RISK
    assert run.gate_results["market"] is False
