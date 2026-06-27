"""Phase 6 — live/paper-vs-backtest degradation monitor: the comparator the audit found missing. It
diffs realized IC / return / slippage / turnover / drawdown / capacity against the backtest expectation
and RECOMMENDS a lifecycle action (OK / DEGRADED / PAUSED / RETIRED). It only RECOMMENDS — the human +
lifecycle manager decide and enforce (containment unchanged). Deterministic, fail-closed."""
from quantos.contracts.research import AlphaState
from services.alpha.degradation_monitor import DegradationThresholds, monitor_alpha_health


BT = {"ic": 0.05, "return": 0.10, "slippage_bps": 5.0, "turnover": 0.3}


def test_healthy_alpha_is_ok():
    live = {"ic": 0.048, "return": 0.095, "slippage_bps": 5.5, "turnover": 0.32,
            "drawdown": 0.1, "capacity_breach_frac": 0.0}
    r = monitor_alpha_health(BT, live)
    assert r["recommendation"] == "OK" and r["healthy"] is True
    assert r["alerts"] == [] and r["recommended_state"] is None


def test_ic_erosion_recommends_degraded():
    live = {"ic": 0.01, "return": 0.06, "slippage_bps": 5.0, "turnover": 0.3, "drawdown": 0.1}
    r = monitor_alpha_health(BT, live)                 # ic_retention 0.2 < 0.3 floor
    assert r["recommendation"] == "DEGRADED" and r["recommended_state"] == AlphaState.DEGRADED
    assert any(a["metric"] == "ic_retention" for a in r["alerts"])


def test_edge_gone_recommends_retired():
    live = {"ic": -0.01, "return": 0.08, "slippage_bps": 5.0, "turnover": 0.3, "drawdown": 0.1}
    r = monitor_alpha_health(BT, live)                 # live IC flipped negative vs positive backtest
    assert r["recommendation"] == "RETIRED" and r["recommended_state"] == AlphaState.RETIRED


def test_return_collapse_recommends_retired():
    live = {"ic": 0.04, "return": -0.02, "slippage_bps": 5.0, "turnover": 0.3, "drawdown": 0.1}
    r = monitor_alpha_health(BT, live)                 # return_gap = (0.10-(-0.02))/0.10 = 1.2 >= 1.0
    assert r["recommendation"] == "RETIRED"


def test_slippage_blowout_recommends_paused():
    live = {"ic": 0.045, "return": 0.09, "slippage_bps": 20.0, "turnover": 0.3, "drawdown": 0.1}
    r = monitor_alpha_health(BT, live)                 # slippage 4x backtest
    assert r["recommendation"] == "PAUSED" and r["recommended_state"] == AlphaState.PAUSED
    assert any(a["metric"] == "slippage" for a in r["alerts"])


def test_drawdown_breach_recommends_paused():
    live = {"ic": 0.045, "return": 0.09, "slippage_bps": 5.0, "turnover": 0.3, "drawdown": 0.45}
    r = monitor_alpha_health(BT, live)                 # drawdown 0.45 > 0.3 max
    assert r["recommendation"] == "PAUSED"


def test_severity_priority_retired_beats_paused_beats_degraded():
    live = {"ic": -0.02, "return": 0.0, "slippage_bps": 50.0, "turnover": 1.0, "drawdown": 0.5}
    r = monitor_alpha_health(BT, live)                 # everything is broken
    assert r["recommendation"] == "RETIRED"            # the most severe wins
    assert len(r["alerts"]) >= 3                       # but all breaches are reported


def test_missing_data_is_flagged_not_fabricated():
    r = monitor_alpha_health(BT, {"slippage_bps": 5.0})   # no ic/return/turnover in live
    assert any(a["metric"] == "data" for a in r["alerts"])
    assert r["recommended_state"] in (None, AlphaState.DEGRADED, AlphaState.PAUSED, AlphaState.RETIRED)


def test_custom_thresholds_and_determinism():
    th = DegradationThresholds(min_ic_retention=0.9)
    live = {"ic": 0.04, "return": 0.10, "slippage_bps": 5.0, "turnover": 0.3, "drawdown": 0.1}
    r1 = monitor_alpha_health(BT, live, thresholds=th)
    r2 = monitor_alpha_health(BT, live, thresholds=th)
    assert r1 == r2 and r1["recommendation"] == "DEGRADED"   # 0.8 retention < 0.9 floor


def test_no_rng_or_wallclock_import():
    import inspect
    import services.alpha.degradation_monitor as dm
    src = inspect.getsource(dm)
    assert "import random" not in src and "import time" not in src and "Random" not in src
