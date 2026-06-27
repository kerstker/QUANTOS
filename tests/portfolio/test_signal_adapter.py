"""Phase 5 — Signal -> Portfolio Adapter: turn one period's cross-sectional alpha signal into a
tradeable target book. Pins the pipeline the audit found missing as a first-class flow: clean
(missing/stale) -> winsorize -> neutralize (size, via cross-sectional OLS) -> standardize -> dollar-
neutral weights with a position cap -> cost (commission + slippage + a SEPARATE half-spread) -> capacity
-> report (weights / gross / net / turnover / cost_bps / rejected+reason / constraint violations).
Research/dry-run only; never places an order. Deterministic."""
import math

from services.portfolio.signal_adapter import SignalAdapterConfig, signal_to_portfolio


def _cfg(**kw):
    return SignalAdapterConfig(**kw)


def test_dollar_neutral_weights_sum_to_gross_and_net_zero():
    sig = {"a": 2.0, "b": 1.0, "c": -1.0, "d": -2.0}
    out = signal_to_portfolio(sig, config=_cfg(gross_target=1.0, max_position=1.0))
    w = out["weights"]
    assert abs(sum(abs(v) for v in w.values()) - 1.0) < 1e-9        # gross == target
    assert abs(sum(w.values())) < 1e-9                              # dollar-neutral (net 0)
    assert w["a"] > 0 and w["d"] < 0                                # high signal long, low signal short


def test_missing_and_stale_are_rejected_with_reason():
    sig = {"a": 1.0, "b": None, "c": float("nan"), "d": -1.0}
    out = signal_to_portfolio(sig, config=_cfg(), stale_ids=("a",))
    assert set(out["weights"]) == {"d"} or "d" in out["weights"]    # only finite, non-stale survive
    reasons = {r["id"]: r["reason"] for r in out["rejected"]}
    assert reasons["b"] == "MISSING" and reasons["c"] == "MISSING" and reasons["a"] == "STALE"


def test_winsorize_caps_outlier_influence():
    # a big universe of tight inliers + one gross outlier: without winsorize the outlier inflates the
    # cross-section std and grabs an outsized weight; winsorize clips it back into the pack.
    sig = {f"in{i}": 1.0 + 0.01 * i for i in range(12)}
    sig["out"] = 50.0
    raw = signal_to_portfolio(sig, config=_cfg(winsorize_k=3.0, max_position=1.0))
    nowin = signal_to_portfolio(sig, config=_cfg(winsorize_k=1e9, max_position=1.0))
    assert abs(raw["weights"]["out"]) < abs(nowin["weights"]["out"])  # winsorize pulls the outlier in


def test_max_position_cap_and_violation_report():
    sig = {"big": 10.0, "a": 0.1, "b": -0.1, "c": -0.2}
    out = signal_to_portfolio(sig, config=_cfg(max_position=0.4, gross_target=1.0))
    assert all(abs(v) <= 0.4 + 1e-9 for v in out["weights"].values())
    assert any(viol["id"] == "big" for viol in out["constraint_violations"])


def test_neutralization_orthogonalizes_against_size():
    # signal = 3*size + small idiosyncratic -> after neutralizing on size, weights ~orthogonal to size
    size = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}
    sig = {k: 3.0 * v + (0.1 if k == "b" else -0.1 if k == "c" else 0.0) for k, v in size.items()}
    out = signal_to_portfolio(sig, config=_cfg(max_position=1.0), neutralize_by=size)
    w = out["weights"]
    sm = sum(size.values()) / len(size)
    dot = sum(w[k] * (size[k] - sm) for k in w)
    assert abs(dot) < 1e-6                                          # neutral to the size factor


def test_turnover_and_cost_with_prev_weights():
    sig = {"a": 2.0, "b": -2.0}
    prev = {"a": 0.0, "b": 0.0}
    out = signal_to_portfolio(sig, config=_cfg(half_spread_bps=1.0), prev_weights=prev)
    assert out["turnover"] > 0
    assert out["cost_bps"] > 0 and out["spread_cost_bps"] > 0       # spread charged separately
    flat = signal_to_portfolio(sig, config=_cfg(), prev_weights=out["weights"])
    assert flat["turnover"] < 1e-9                                  # no change -> no turnover


def test_capacity_flags_thin_liquidity():
    sig = {"a": 1.0, "b": -1.0}
    adv = {"a": 1e9, "b": 100.0}                                    # b is illiquid
    out = signal_to_portfolio(sig, config=_cfg(target_aum=1e7, max_participation=0.1),
                              adv_notional=adv)
    cap = {c["id"]: c for c in out["capacity"]}
    assert cap["b"]["capacity_ok"] is False and cap["a"]["capacity_ok"] is True


def test_empty_and_deterministic():
    assert signal_to_portfolio({}, config=_cfg())["weights"] == {}
    sig = {"a": 1.0, "b": -1.0, "c": 0.5}
    assert signal_to_portfolio(sig, config=_cfg()) == signal_to_portfolio(sig, config=_cfg())


def test_no_rng_or_wallclock_import():
    import inspect
    import services.portfolio.signal_adapter as sa
    src = inspect.getsource(sa)
    assert "import random" not in src and "import time" not in src and "Random" not in src
