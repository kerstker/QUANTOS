"""FX gauntlet: re-run the FX cross-sectional trend signal on a TRADABLE-ONLY universe under a realistic COST
sweep, so the promising +0.70 full-universe OOS (tools/fx_research.py) is stress-tested honestly before any
"tradable edge" claim. Drops pegged / capital-controlled / sanctioned / pre-Euro-legacy currencies (their
"returns" are not executable and inflate the signal), keeps freely-floating liquid floaters, and reports the
no-peek walk-forward OOS Sharpe at each slippage level — the survival curve that shows WHERE the edge dies.

Reuses the same offline engines (PIT panel + run_panel_backtest + walk_forward_selection_oos). Deterministic;
local store only. HONEST: reports the curve as-is; a signal that dies by ~5-10bps is NOT a tradable edge.
"""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research.panel_builder import make_data_periods_provider
from services.research.backtest_engine.metrics import sharpe
from services.research.backtest_engine.costs import CostModel
from services.alpha.panel_backtest import run_panel_backtest
from services.research.oos_validation import walk_forward_selection_oos, full_sample_best
from quantos.contracts.alpha import AlphaDefinition

_DB = os.path.join("data", "cache", "fx.db")
_SNAP = os.path.join("data", "cache", "fx_gauntlet_snapshot.json")

# Freely-floating, liquid, ACTUALLY-TRADABLE vs USD. Excluded (and why): pre-Euro legacy gone
# (GRD/CYP/SIT/SKK/EEK/LTL/LVL/MTL/ROL/TRL); hard/managed pegs (HKD,DKK,BGN,HRK,CNY,SGD-basket kept as liquid);
# capital-controlled / non-deliverable-onshore (ARS,DZD,MAD,INR,THB,TWD,PHP,IDR,ISK); sanctioned/untradable
# post-2022 (RUB). Kept: G10 floaters + liquid floating EM.
TRADABLE = ["EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "NOK", "SEK",          # G10 floaters
            "BRL", "MXN", "ZAR", "PLN", "HUF", "CZK", "KRW", "ILS", "SGD"]          # liquid floating EM
POOL = [("momentum", 60), ("momentum", 120), ("momentum", 250), ("ts_rank", 60),
        ("zscore", 60), ("dist_from_high", 60), ("dist_from_high", 120), ("accel", 60)]
HEADLINE = ("momentum", 120)
FWD = 5
COST_BPS = [1, 3, 5, 10, 20]          # majors ~1-2bps, liquid EM ~5-15bps -> the survival sweep


def _instruments_and_times(db):
    import sqlite3
    con = sqlite3.connect(db)
    have = {r[0] for r in con.execute("SELECT DISTINCT key FROM bars")}
    ets = sorted(r[0] for r in con.execute("SELECT DISTINCT event_time FROM bars"))
    con.close()
    return [c for c in TRADABLE if c in have], ets


def _net(periods, bps):
    return list(run_panel_backtest(periods, CostModel(fee_bps=0.0, slippage_bps=float(bps))).period_returns)


def _aligned(factor_ret, min_len=1500):
    raw = {k: v for k, v in factor_ret.items() if len(v) >= min_len}
    if not raw:
        return {}
    common = min(len(v) for v in raw.values())
    return {k: list(v)[-common:] for k, v in raw.items()}


def main() -> None:
    if not os.path.exists(_DB):
        print("no data/cache/fx.db — run tools.frankfurter_fetch first")
        return
    insts, ets = _instruments_and_times(_DB)
    print(f"TRADABLE universe: {len(insts)} currencies {insts}", flush=True)
    if len(insts) < 5:
        print("too few tradable currencies present"); return
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    provider = make_data_periods_provider(store, insts, event_times=ets, fwd_horizon=FWD)

    # build each factor's periods ONCE (slow), then re-cost cheaply across the sweep
    periods_by = {}
    for op, w in POOL:
        defn = AlphaDefinition(alpha_id=f"fx_{op}_{w}", name=f"{op}({w})", factor_ids=("close",),
                               params={"operator": op, "window": str(w), "family": "price"})
        per = provider(defn)
        if len(per) >= 1500:
            periods_by[f"fx_{op}_{w}"] = per
        print(f"  built fx_{op}_{w}: {len(per)} periods", flush=True)

    print("\n=== no-peek OOS survival curve (tradable universe) ===", flush=True)
    print(f"{'cost_bps':>8} | {'IS best SR':>10} | {'OOS mean SR':>11} | folds", flush=True)
    curve = []
    for c in COST_BPS:
        fr = _aligned({k: _net(p, c) for k, p in periods_by.items()})
        if not fr:
            continue
        oos = walk_forward_selection_oos(fr, train_size=1000, test_size=250, embargo=FWD, top_k=1)
        is_name, is_sr = full_sample_best(fr)
        curve.append({"cost_bps": c, "is_best": is_name, "is_sharpe": round(is_sr, 3),
                      "oos_mean_sharpe": oos.get("mean_oos_sharpe"), "n_folds": oos.get("n_folds")})
        print(f"{c:>8} | {is_sr:>10.2f} | {str(oos.get('mean_oos_sharpe')):>11} | {oos.get('n_folds')}", flush=True)

    # headline per-year at a realistic 5bps
    hp = provider(AlphaDefinition(alpha_id="h", name="h", factor_ids=("close",),
                                  params={"operator": HEADLINE[0], "window": str(HEADLINE[1]), "family": "price"}))
    hret = _net(hp, 5)
    htimes = ets[FWD: FWD + len(hret)]
    by_year = defaultdict(list)
    for t, r in zip(htimes, hret):
        by_year[time.gmtime(t / 1000).tm_year].append(r)
    per_year = [{"year": y, "n": len(by_year[y]),
                 "net_sharpe": round(sharpe(by_year[y]), 2) if len(by_year[y]) > 1 else None}
                for y in sorted(by_year) if by_year[y]]
    print(f"\nHEADLINE {HEADLINE[0]}_{HEADLINE[1]} @5bps per-year net SR (tradable):", flush=True)
    for r in per_year:
        if r["n"] > 5:
            print(f"   {r['year']}  n={r['n']:4d}  SR {r['net_sharpe']}", flush=True)

    snap = {"asset_class": "FX_tradable", "universe": insts, "n_currencies": len(insts),
            "fwd_horizon": FWD, "cost_sweep": curve, "headline": f"{HEADLINE[0]}_{HEADLINE[1]}",
            "per_year_5bps": per_year}
    os.makedirs(os.path.dirname(_SNAP), exist_ok=True)
    json.dump(snap, open(_SNAP, "w", encoding="utf-8"), indent=2, sort_keys=True)
    print(f"\nsnapshot -> {_SNAP}", flush=True)


if __name__ == "__main__":
    main()
