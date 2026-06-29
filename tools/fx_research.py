"""FX cross-sectional factor research on the ECB/Frankfurter daily reference rates (data/cache/fx.db) — a
genuinely DIFFERENT asset class + regime from crypto, ~30 currencies back to 1999 (GFC / 2015 CHF / COVID /
2022 hikes). Reuses the SAME offline engines as crypto: PIT panel builder (+ wired leakage_ok), the contained
ResearchAgentLoop gate (regime self-computed, PBO self-computed, leakage evidence supplied), and the no-peek
walk-forward selection OOS. Deterministic; reads the local store only (no network). Writes a snapshot.

HONESTY: this REPORTS what the gate finds; it does not assume an edge. Cross-sectional FX momentum/reversal is
a studied space — the no-peek OOS is the arbiter, exactly as for crypto.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from collections import defaultdict

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research.panel_builder import make_data_periods_provider
from services.research.leakage_evidence import signal_leakage_ok
from services.research.backtest_engine.metrics import sharpe
from services.research.backtest_engine.costs import CostModel
from services.alpha.panel_backtest import run_panel_backtest
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.research.oos_validation import walk_forward_selection_oos, full_sample_best
from services.intelligence.agent_loop import ResearchAgentLoop
from quantos.contracts.alpha import AlphaDefinition

_DB = os.path.join("data", "cache", "fx.db")
_SNAP = os.path.join("data", "cache", "fx_research_snapshot.json")
# currency momentum/reversal trend at FX-appropriate (longer) windows; price-only (volume is absent in FX).
POOL = [("momentum", 60), ("momentum", 120), ("momentum", 250), ("ts_rank", 60),
        ("zscore", 60), ("dist_from_high", 60), ("dist_from_high", 120), ("accel", 60)]
HEADLINE = ("momentum", 120)
FWD = 5            # weekly-ish horizon (daily FX is noisy / costly to rebalance)


def _instruments_and_times(db):
    import sqlite3
    con = sqlite3.connect(db)
    insts = sorted(r[0] for r in con.execute("SELECT DISTINCT key FROM bars"))
    ets = sorted(r[0] for r in con.execute("SELECT DISTINCT event_time FROM bars"))
    con.close()
    return insts, ets


def _net(periods, bps=2.0):
    return list(run_panel_backtest(periods, CostModel(fee_bps=0.0, slippage_bps=float(bps))).period_returns)


def main() -> None:
    if not os.path.exists(_DB):
        print("no data/cache/fx.db — run tools.frankfurter_fetch first")
        return
    insts, ets = _instruments_and_times(_DB)
    if len(insts) < 5 or len(ets) < 400:
        print(f"too little FX data ({len(insts)} ccy x {len(ets)} days)")
        return
    print(f"FX: {len(insts)} currencies x {len(ets)} daily periods "
          f"{time.strftime('%Y-%m', time.gmtime(ets[0] / 1000))}..{time.strftime('%Y-%m', time.gmtime(ets[-1] / 1000))}",
          flush=True)
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    provider = make_data_periods_provider(store, insts, event_times=ets, fwd_horizon=FWD)

    catalog = AlphaCatalog()
    loop = ResearchAgentLoop(catalog=catalog, config=AlphaFactoryConfig(compute_regime_evidence=True))
    candidates, factor_ret, evidence = [], {}, {}
    for op, w in POOL:
        aid = f"fx_{op}_{w}"
        defn = AlphaDefinition(alpha_id=aid, name=f"{op}({w})", factor_ids=("close",),
                               params={"operator": op, "window": str(w), "family": "price"})
        periods = provider(defn)
        if len(periods) < 200:
            print(f"  skip {aid}: only {len(periods)} periods", flush=True)
            continue
        candidates.append((defn, periods))
        factor_ret[aid] = _net(periods)
        evidence[aid] = {"leakage_ok": provider.leakage_ok(defn)}
        print(f"  built {aid}: {len(periods)} periods | leakage_ok={evidence[aid]['leakage_ok']}", flush=True)

    report = loop.run(candidates, timestamp=int(ets[-1]), evidence=evidence)["report"]
    verdicts = {aid: r.get("verdict") for aid, r in report.items()}
    leak_true = sum(1 for e in evidence.values() if e["leakage_ok"] is True)
    print(f"\nleakage: {leak_true}/{len(evidence)} factors PIT-verified leak-free", flush=True)
    print("verdicts:", verdicts, flush=True)

    # no-peek walk-forward selection OOS over the pool (the arbiter). Longer windows drop more LEADING
    # periods, so series differ in length but all END on the same date -> align on the common recent tail.
    usable_raw = {k: v for k, v in factor_ret.items() if len(v) >= 1500}
    if usable_raw:
        common = min(len(v) for v in usable_raw.values())
        usable = {k: list(v)[-common:] for k, v in usable_raw.items()}
    else:
        usable = {}
    oos = walk_forward_selection_oos(usable, train_size=1000, test_size=250, embargo=FWD, top_k=1) if usable else {}
    is_name, is_sr = full_sample_best(usable) if usable else ("", 0.0)

    # per-CALENDAR-YEAR net Sharpe of the headline (the multi-regime test)
    hp = provider(AlphaDefinition(alpha_id="h", name="h", factor_ids=("close",),
                                  params={"operator": HEADLINE[0], "window": str(HEADLINE[1]), "family": "price"}))
    hret = _net(hp)
    htimes = ets[FWD: FWD + len(hret)]
    by_year = defaultdict(list)
    for t, r in zip(htimes, hret):
        by_year[time.gmtime(t / 1000).tm_year].append(r)
    per_year = [{"year": y, "n": len(by_year[y]),
                 "net_sharpe": round(sharpe(by_year[y]), 2) if len(by_year[y]) > 1 else None}
                for y in sorted(by_year)]

    print(f"\nIN-SAMPLE best: {is_name} SR {is_sr:+.2f}", flush=True)
    print(f"NO-PEEK OOS: mean test SR {oos.get('mean_oos_sharpe')} "
          f"(IS {oos.get('mean_is_sharpe')}, folds {oos.get('n_folds')}, "
          f"haircut {oos.get('haircut')})", flush=True)
    print(f"HEADLINE {HEADLINE[0]}_{HEADLINE[1]} per-year net SR:", flush=True)
    for r in per_year:
        print(f"   {r['year']}  n={r['n']:4d}  SR {r['net_sharpe']}", flush=True)

    snap = {"asset_class": "FX", "source": "frankfurter_ecb", "n_currencies": len(insts),
            "n_periods": len(ets), "fwd_horizon": FWD, "pool": [f"{o}_{w}" for o, w in POOL],
            "leakage_verified": f"{leak_true}/{len(evidence)}", "verdicts": verdicts,
            "in_sample_best": {"name": is_name, "sharpe": round(is_sr, 4)}, "oos": oos,
            "headline": f"{HEADLINE[0]}_{HEADLINE[1]}", "per_year": per_year}
    os.makedirs(os.path.dirname(_SNAP), exist_ok=True)
    json.dump(snap, open(_SNAP, "w", encoding="utf-8"), indent=2, sort_keys=True)
    print(f"\nsnapshot -> {_SNAP}", flush=True)


if __name__ == "__main__":
    main()
