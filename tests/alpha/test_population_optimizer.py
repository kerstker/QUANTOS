"""Phase 4 — Alpha Population Optimizer: select a SET and assign WEIGHTS to maximize a portfolio-level
objective, not just rank single alphas by IC. Builds on factor_set's incremental-IR de-redundancy and
Phase-3 similarity for diversity. Pins the core portfolio math (combine / drawdown / IR / weighting /
marginal contribution incl. drawdown) and the optimizer output (selected+weights / excluded+reasons /
expected metrics / objective). Deterministic."""
import math

from services.alpha import population_optimizer as po


def _series(n, mean, amp, phase=0, freq=0.7):
    # deterministic pseudo-return series: mean drift + bounded oscillation. `freq` lets a test build a
    # genuinely decorrelated alpha (a different frequency -> low pairwise correlation).
    return [mean + amp * math.sin(freq * (i + phase)) for i in range(n)]


# ---- core math ----
def test_combine_and_information_ratio():
    a = [0.01, 0.02, 0.01, 0.02]
    b = [0.02, 0.01, 0.02, 0.01]
    port = po.combine_returns({"a": a, "b": b}, {"a": 0.5, "b": 0.5})
    assert port == [0.015, 0.015, 0.015, 0.015]            # constant -> zero variance
    assert po.information_ratio(port) > 0                  # positive constant -> +inf-ish (mean/0 guard)
    assert po.information_ratio([0.0, 0.0]) == 0.0


def test_max_drawdown():
    assert po.max_drawdown([0.1, 0.1, 0.1]) == 0.0         # monotonic up -> no drawdown
    dd = po.max_drawdown([0.1, -0.2, 0.05])
    assert dd > 0.15                                       # a -20% leg creates a real drawdown


def test_weighting_schemes_normalize():
    rbi = {"hi": _series(40, 0.01, 0.05), "lo": _series(40, 0.01, 0.005)}
    eq = po.weights_for(rbi, scheme="equal")
    assert abs(sum(eq.values()) - 1.0) < 1e-9 and eq["hi"] == eq["lo"]
    iv = po.weights_for(rbi, scheme="inverse_vol")
    assert abs(sum(iv.values()) - 1.0) < 1e-9
    assert iv["lo"] > iv["hi"]                             # the lower-vol alpha gets more weight


def test_marginal_contribution_includes_drawdown():
    rbi = {"steady": _series(40, 0.01, 0.005), "risky": _series(40, 0.0, 0.06, phase=3)}
    w = po.weights_for(rbi, scheme="equal")
    mc = po.marginal_contributions(rbi, w)
    assert set(mc["steady"]) == {"marginal_ir", "marginal_dd"}
    # dropping the risky leg should not improve IR more than dropping the steady leg
    assert mc["steady"]["marginal_ir"] >= mc["risky"]["marginal_ir"]


# ---- optimizer ----
def test_optimize_selects_diverse_set_and_drops_redundant():
    base = _series(60, 0.01, 0.04)
    rbi = {
        "a": base,
        "a_clone": [x * 1.001 for x in base],              # near-duplicate of a -> redundant
        "b": _series(60, 0.008, 0.04, freq=2.3),           # different frequency -> decorrelated, informative
    }
    out = po.optimize_population(rbi, max_k=3)
    sel = set(out["selected_ids"])
    assert "a" in sel and "b" in sel                       # the two informative alphas are kept
    assert "a_clone" not in sel                            # the clone is dropped as redundant
    assert any(e["id"] == "a_clone" for e in out["excluded"])
    assert abs(sum(s["weight"] for s in out["selected"]) - 1.0) < 1e-9
    for s in out["selected"]:
        assert "marginal_ir" in s and "marginal_dd" in s


def test_optimize_reports_portfolio_metrics_and_objective():
    rbi = {"a": _series(60, 0.01, 0.03), "b": _series(60, 0.009, 0.03, phase=7)}
    out = po.optimize_population(rbi, max_k=2, cost_bps=5.0)
    for k in ("portfolio_ir", "post_cost_ir", "max_drawdown", "diversity", "objective",
              "expected_turnover", "weights"):
        assert k in out
    assert out["post_cost_ir"] <= out["portfolio_ir"]      # costs never improve the IR
    assert 0.0 <= out["diversity"] <= 1.0


def test_evolutionary_method_is_deterministic_and_no_worse_than_greedy():
    rbi = {f"f{i}": _series(60, 0.006 + 0.001 * i, 0.03, phase=i * 2) for i in range(6)}
    g = po.optimize_population(rbi, max_k=3, method="greedy")
    e1 = po.optimize_population(rbi, max_k=3, method="evolutionary")
    e2 = po.optimize_population(rbi, max_k=3, method="evolutionary")
    assert e1["selected_ids"] == e2["selected_ids"]        # deterministic
    assert e1["objective"] >= g["objective"] - 1e-9        # local search never regresses below greedy


def test_empty_and_single():
    assert po.optimize_population({}, max_k=3)["selected_ids"] == []
    one = po.optimize_population({"a": _series(40, 0.01, 0.02)}, max_k=3)
    assert one["selected_ids"] == ["a"] and abs(one["weights"]["a"] - 1.0) < 1e-9


def test_no_rng_or_wallclock_import():
    import inspect
    src = inspect.getsource(po)
    assert "import random" not in src and "import time" not in src and "Random" not in src
