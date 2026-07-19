"""Audit M3 — the 9-engine research-risk gate must be fail-CLOSED: empty/degenerate
inputs and engine exceptions can never inflate gates_passed or reach RISK_APPROVED.
(Previously several engines returned passed=True on empty inputs and the gate had
no exception aggregation.) This gate feeds only the read-only console snapshot, but
the 'deterministic, fail-closed' claim must actually hold."""
import pytest

from quantos.contracts.alpha import AlphaCluster
from services.risk.research_risk.domain.gate import RiskApprovalGate
from services.risk.research_risk.domain.models import ConcentrationRiskReport, RiskConfig, RiskDecision, RiskRun
from services.risk.research_risk.domain.risk_modules import ConcentrationRiskEngine


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


# ---- concentration engine: real clustering (2026-07-09, gap audit rank #3) --------------------

def test_concentration_engine_falls_back_to_max_single_when_no_clusters_supplied():
    # unchanged pre-fix behavior: clusters=None -> max_cluster == max_single
    weights = {"a": 0.1, "b": 0.1, "c": 0.1}
    r = ConcentrationRiskEngine().evaluate(weights, None, RiskConfig())
    assert r.max_cluster == r.max_single_alpha == 0.1


def test_concentration_engine_uses_summed_cluster_weight_when_clusters_supplied():
    # three alphas that individually clear max_single_alpha_weight (0.15) but whose CLUSTER sum
    # (0.33) breaches max_cluster_weight (0.30) -- exactly the "several correlated variants of the
    # same trade" scenario the audit found architecturally unenforceable pre-fix. No OTHER single
    # name is large enough to independently dominate max_cluster (the largest, 'd', is 0.13 < 0.30).
    weights = {"a": 0.11, "b": 0.11, "c": 0.11, "d": 0.13, "e": 0.13, "f": 0.13, "g": 0.13, "h": 0.15}
    clusters = [AlphaCluster(cluster_id="c1", member_ids=("a", "b", "c"),
                             representative_id="a", intra_corr=0.95)]
    config = RiskConfig()
    r = ConcentrationRiskEngine().evaluate(weights, clusters, config)
    assert r.max_single_alpha == pytest.approx(0.15)         # 'h' alone, clears max_single_alpha_weight
    assert r.max_cluster == pytest.approx(0.33)              # a+b+c summed > any single name
    assert r.max_cluster > config.max_cluster_weight
    assert r.passed is False                                 # the cluster alone must fail it


def test_concentration_engine_cluster_member_missing_from_weights_contributes_zero():
    # fail-soft: a cluster member with no weight entry (e.g. retired/never-held) must not KeyError
    weights = {"a": 0.1}
    clusters = [AlphaCluster(cluster_id="c1", member_ids=("a", "ghost"),
                             representative_id="a", intra_corr=0.9)]
    r = ConcentrationRiskEngine().evaluate(weights, clusters, RiskConfig())
    assert r.max_cluster == pytest.approx(0.1)


def test_gate_computes_real_clusters_from_alpha_returns_and_can_reject_on_it():
    # end-to-end: RiskApprovalGate now derives clusters from the SAME alpha_returns it already
    # receives (no new required input) -- two near-perfectly-correlated alphas whose individual
    # weights clear max_single_alpha_weight but whose summed cluster weight does not.
    run = RiskRun(portfolio_id="p")
    inp = _pass_inputs()
    inp["alpha_returns"] = {
        "a0": [0.01, -0.01, 0.01, -0.01, 0.01, -0.01, 0.01, -0.01] * 8,
        "a1": [0.01, -0.01, 0.01, -0.01, 0.01, -0.01, 0.01, -0.01] * 8,   # identical -> rho=1.0
        "a2": [0.01, 0.01, -0.01, -0.01, 0.01, 0.01, -0.01, -0.01] * 8,
    }
    inp["weights"] = {"a0": 0.11, "a1": 0.11, "a2": 0.11,
                      **{f"pad{i}": (1.0 - 0.33) / 7 for i in range(7)}}
    inp["capacity_cny"] = {k: 1.0e8 for k in inp["weights"]}
    d = RiskApprovalGate().evaluate(run=run, **inp)
    report = run.gate_reports["concentration"]
    assert isinstance(report, ConcentrationRiskReport)
    assert report.max_cluster == pytest.approx(0.22, abs=1e-6)   # a0+a1 (a2 uncorrelated, not merged)
    assert report.max_cluster < RiskConfig().max_cluster_weight  # 0.22 < 0.30 -- stays passed (see next test for a breach)


def test_gate_cluster_breach_actually_flips_the_decision():
    run = RiskRun(portfolio_id="p")
    inp = _pass_inputs()
    identical = [0.01, -0.01, 0.01, -0.01, 0.01, -0.01, 0.01, -0.01] * 8
    inp["alpha_returns"] = {"a0": identical, "a1": identical, "a2": list(identical)}  # all 3 identical
    inp["weights"] = {"a0": 0.11, "a1": 0.11, "a2": 0.11,
                      **{f"pad{i}": (1.0 - 0.33) / 7 for i in range(7)}}
    inp["capacity_cny"] = {k: 1.0e8 for k in inp["weights"]}
    d = RiskApprovalGate().evaluate(run=run, **inp)
    report = run.gate_reports["concentration"]
    assert report.max_cluster == pytest.approx(0.33, abs=1e-6)   # all three merge into one cluster
    assert report.passed is False
    assert run.gate_results["concentration"] is False
    assert d.decision == RiskDecision.REJECTED_CONCENTRATION_RISK


def test_gate_cluster_computation_failure_fails_closed_not_crashes():
    """cluster_alphas() is called BEFORE the per-engine try/except loop; if it ever raised, an
    unhandled exception there would crash the WHOLE evaluate() call instead of just failing one
    engine — a regression from this gate's own stated 'never an uncaught crash' discipline.
    Monkeypatch it to raise and confirm evaluate() still returns a normal (non-crashing) decision
    with clusters degraded to None (the pre-fix fallback), not an exception."""
    import services.risk.research_risk.domain.gate as gate_mod
    real_cluster_alphas = gate_mod.cluster_alphas

    def _boom(*a, **k):
        raise RuntimeError("cluster computation blew up")

    gate_mod.cluster_alphas = _boom
    try:
        run = RiskRun(portfolio_id="p")
        d = RiskApprovalGate().evaluate(run=run, **_pass_inputs())   # must not raise
        assert isinstance(d.decision, RiskDecision)
        # concentration engine still ran (with clusters=None fallback) -- not skipped/crashed
        assert run.gate_reports["concentration"] is not None
    finally:
        gate_mod.cluster_alphas = real_cluster_alphas
