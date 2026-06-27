"""End-to-end wiring: the Phase 1-7 modules, built and unit-tested in isolation, run together as ONE
contained research-to-portfolio pipeline on a cross-sectional panel:

  cs expression -> COMPUTE leakage_ok (P0) -> evaluate w/ catalog DSR (P2) -> rich AlphaAsset + registry
  + Research Memory (P3/P7) -> population optimizer (P4) -> Signal->Portfolio target book (P5) ->
  auto research brief (P7).

It NEVER drives a lifecycle transition (no APPROVED risk) — it produces evaluations, a portfolio and a
brief. Research / dry-run only, deterministic."""
import math

import services.research.alpha_expression as ax
from services.research.alpha_pipeline import run_alpha_asset_pipeline


def _panel():
    T, N = 60, 6
    insts = [f"C{i}" for i in range(N)]
    close, volume, funding = [], [], []
    for t in range(T):
        close.append([100.0 + i + 5.0 * math.sin(0.3 * t + i) for i in range(N)])
        volume.append([1000.0 + 50.0 * math.sin(0.5 * t + 2 * i) + 10 * i for i in range(N)])
        funding.append([0.0001 * math.sin(0.2 * t + i) for i in range(N)])
    return ax.FieldPanel(insts, list(range(T)), {"close": close, "volume": volume, "funding": funding})


EXPRS = [
    ("cs_rank", ("ts_delta", ("field", "close"), 3)),
    ("cs_zscore", ("ts_mean", ("field", "volume"), 5)),
    ("cs_rank", ("neg", ("ts_mean", ("field", "funding"), 7))),
]


def test_pipeline_runs_end_to_end_and_computes_leakage():
    out = run_alpha_asset_pipeline(_panel(), EXPRS, fwd_horizon=1, dataset_version="dv1", timestamp=1)
    assert out["n_evaluated"] == 3
    # P0 wired: leakage_ok was COMPUTED (these PIT-safe alphas prove invariant), not left None
    assert all(a["leakage_ok"] is True for a in out["assets"])
    assert out["leakage_summary"]["proven"] == 3 and out["leakage_summary"]["leaked"] == 0
    # P3 wired: the registry + memory are populated
    assert len(out["registry"].all_ids()) == 3
    assert out["memory"].get(out["assets"][0]["alpha_id"]) is not None
    # P4 wired: a portfolio was optimized
    assert "selected_ids" in out["portfolio"] and len(out["portfolio"]["selected_ids"]) >= 1
    # P5 wired: a Signal->Portfolio target book exists and is dollar-neutral
    tb = out["target_book"]
    assert "weights" in tb and abs(tb["net"]) < 1e-6
    # P7 wired: an auto research brief was composed over the run's notes
    assert out["brief"]["n_tested"] == 3
    # per-alpha net returns are exposed for a downstream honest no-peek OOS / CPCV
    assert set(out["returns_by_id"]) <= {a["alpha_id"] for a in out["assets"]}
    # economic (carry-aware: price + funding collected) returns are computed when funding is present
    assert set(out["economic_returns_by_id"]) <= {a["alpha_id"] for a in out["assets"]}
    assert any(a["economic_sharpe"] is not None for a in out["assets"])


def test_pipeline_is_deterministic():
    a = run_alpha_asset_pipeline(_panel(), EXPRS, fwd_horizon=1, dataset_version="dv1", timestamp=1)
    b = run_alpha_asset_pipeline(_panel(), EXPRS, fwd_horizon=1, dataset_version="dv1", timestamp=1)
    assert a["portfolio"]["selected_ids"] == b["portfolio"]["selected_ids"]
    assert a["target_book"]["weights"] == b["target_book"]["weights"]
    assert a["brief"] == b["brief"]


def test_pipeline_flags_leaking_alpha(monkeypatch):
    # inject a future-reading operator: the pipeline must COMPUTE leakage_ok = False and not pretend safe
    def _ts_lead(m, d):
        t, n = ax._dims(m)
        out = ax._empty(t, n)
        for r in range(t):
            for i in range(n):
                if r + d < t:
                    out[r][i] = m[r + d][i]
        return out
    monkeypatch.setitem(ax._TS_WINDOW, "ts_lead", _ts_lead)
    leaky = ("cs_rank", ("ts_lead", ("field", "close"), 1))
    out = run_alpha_asset_pipeline(_panel(), [leaky], fwd_horizon=1, dataset_version="dv1", timestamp=1)
    assert out["assets"][0]["leakage_ok"] is False
    assert out["leakage_summary"]["leaked"] == 1


def test_no_rng_or_wallclock_import():
    import inspect
    import services.research.alpha_pipeline as p
    src = inspect.getsource(p)
    assert "import random" not in src and "import time" not in src and "Random" not in src
