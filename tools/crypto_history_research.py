"""Out-of-gate, READ-ONLY MULTI-YEAR daily research — the decisive OUT-OF-REGIME test.

The daily lead (dist_from_high_14) passed OOS / cost / within-sample regime, but every test so far
sat inside the 2024-25 BULL. This driver closes the last gap: it fetches MULTI-YEAR daily OHLCV
(CryptoCompare histoday — a neutral DATA aggregator, free key) for a cross-section of coins that
traded THROUGH the 2022 bear, ingests to data/cache/crypto_history.db, evaluates the factor through
the UNCHANGED contained loop, and — the whole point — breaks the headline factor's net Sharpe down
BY CALENDAR YEAR so 2022 is explicit. If dist_from_high_14 survives 2022, the lead is regime-robust
for real; if it craters, we've found its weakness. Plus a long-sample no-peek OOS (many more folds).

Network lives ONLY in the fetcher (out of the gate); the research core stays offline / deterministic.
Nothing here promotes an alpha, flips a flag, or raises a readiness level.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.data.market_provider import ingest_provider_rows
from services.research.signals import SIGNAL_OPS
from services.research.leakage_evidence import signal_leakage_ok
from services.research.backtest_engine.metrics import sharpe
from services.research.backtest_engine.costs import CostModel
from services.research.regime import label_regimes_by_vol, regime_conditional_stats
from services.research.oos_validation import walk_forward_selection_oos, full_sample_best
from services.alpha.panel_backtest import PanelPeriod, run_panel_backtest
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.intelligence.agent_loop import ResearchAgentLoop
from quantos.contracts.alpha import AlphaDefinition
from tools.cryptocompare_fetch import fetch_daily_rows

# established coins that traded THROUGH the 2022 bear (CryptoCompare uses bare tickers).
# newer launches (SUI/APT/ARB/OP/INJ) are excluded — they have no 2022 data for the bear test.
COINS = ["BTC", "ETH", "BNB", "XRP", "ADA", "DOGE", "SOL", "DOT", "LTC", "TRX", "LINK", "AVAX",
         "ATOM", "XLM", "NEAR", "UNI", "MATIC", "FIL", "ALGO", "AAVE", "XTZ", "EOS", "MKR", "GRT",
         "SAND", "MANA", "CRV", "COMP", "SNX", "CHZ"]
FWD_HORIZON = 1
POOL = [("dist_from_high", 3), ("dist_from_high", 7), ("dist_from_high", 14), ("dist_from_high", 30),
        ("momentum", 7), ("momentum", 14), ("accel", 14), ("ts_rank", 14)]
HEADLINE = ("dist_from_high", 14)
COST_LEVELS = (3, 10, 20, 30, 40, 50)
_DB = os.path.join("data", "cache", "crypto_history.db")
_SNAP = os.path.join("data", "cache", "crypto_history_snapshot.json")


def _periods_with_times(store, instruments, ets, op, window):
    """Build the factor's PanelPeriods AND the decision event_time of each (for the per-year split)."""
    insts = sorted(instruments)
    periods, times, fn = [], [], SIGNAL_OPS[op]
    for i, t in enumerate(ets):
        j = i + FWD_HORIZON
        if j >= len(ets):
            break
        t_fwd = ets[j]
        rows = []
        for inst in insts:
            by_close = {}
            for b in store.as_of(inst, t):
                by_close[b.event_time] = b.close
            closes = [by_close[e] for e in sorted(by_close)]
            nb, fb = store.latest_as_of(inst, t), store.latest_as_of(inst, t_fwd)
            if (not closes or nb is None or fb is None or nb.close == 0
                    or fb.event_time <= nb.event_time):
                continue
            s = fn(closes, window)
            if s is None or not math.isfinite(s):
                continue
            rows.append((inst, float(s), fb.close / nb.close - 1.0))
        if len(rows) >= 2:
            periods.append(PanelPeriod(tuple(r[0] for r in rows), tuple(r[1] for r in rows),
                                       tuple(r[2] for r in rows)))
            times.append(t)
    return periods, times


def _net(periods, bps):
    return list(run_panel_backtest(periods, CostModel(fee_bps=0.0, slippage_bps=float(bps))).period_returns)


def _regime(periods, ret):
    mkt = [sum(p.forward_returns) / len(p.forward_returns) if p.forward_returns else 0.0 for p in periods]
    eq, v = [1.0], 1.0
    for x in mkt:
        v *= (1.0 + x)
        eq.append(v)
    vol = label_regimes_by_vol(eq[1:], vol_window=20, n_regimes=3)
    trend = [None] * len(mkt)
    for i in range(len(mkt)):
        if i >= 14:
            comp = 1.0
            for jj in range(i - 14, i):
                comp *= (1.0 + mkt[jj])
            trend[i] = 1 if comp > 1.0 else 0
    vn, tn = {0: "calm", 1: "normal", 2: "storm"}, {0: "down/flat", 1: "up"}

    def _b(stats, names):
        return [{"label": names.get(k, str(k)), "n": s["n"], "sharpe": s["sharpe"],
                 "positive_frac": s["positive_frac"]} for k, s in sorted(stats.items())]
    return {"by_vol": _b(regime_conditional_stats(ret, vol), vn),
            "by_trend": _b(regime_conditional_stats(ret, trend), tn)}


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    n = int(sys.argv[2]) if len(sys.argv) > 2 else len(COINS)
    universe = COINS[:max(2, n)]
    now_ms = int(time.time() * 1000)
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    store = SqlitePITStore(_DB, create=True)

    instruments, all_ets, dropped = [], set(), []
    for tk in universe:
        try:
            rows = fetch_daily_rows(tk, tk, limit=limit, now_ms=now_ms)
            if not rows:
                raise ValueError("no bars")
            ingest_provider_rows(rows, store, dataset_id=f"cc-{tk}", now=now_ms, time_unit="ms")
        except Exception as exc:
            dropped.append(tk)
            print(f"{tk:5s} SKIPPED ({type(exc).__name__})", flush=True)
            time.sleep(1.0)
            continue
        instruments.append(tk)
        all_ets.update(r["event_time"] for r in rows)
        lo = time.strftime("%Y-%m", time.gmtime(rows[0]["event_time"] / 1000))
        hi = time.strftime("%Y-%m", time.gmtime(rows[-1]["event_time"] / 1000))
        print(f"{tk:5s} {len(rows):4d} bars  {lo}..{hi}", flush=True)
        time.sleep(1.0)

    event_times = sorted(all_ets)
    store = CachingPITStore(store)        # ingestion done -> serve the O(P^2) panel builds from memory
    if len(instruments) < 2 or len(event_times) < 60:
        raise SystemExit("too little multi-year data — check the CryptoCompare key / coverage")
    print(f"\nMULTI-YEAR: {len(instruments)} coins x {len(event_times)} daily periods", flush=True)

    # contained evaluation of the focused price-factor pool (no re-discovery; we TEST the known lead)
    catalog = AlphaCatalog()
    loop = ResearchAgentLoop(catalog=catalog, config=AlphaFactoryConfig(compute_regime_evidence=True))
    pool_periods, candidates = {}, []
    for op, w in POOL:
        per, _ = _periods_with_times(store, instruments, event_times, op, w)
        pool_periods[(op, w)] = per
        defn = AlphaDefinition(alpha_id=f"h_{op}_{w}", name=f"{op}({w})", factor_ids=("close",),
                               params={"operator": op, "window": str(w), "family": "price"})
        candidates.append((defn, per))
        print(f"  built h_{op}_{w}: {len(per)} periods", flush=True)

    # 断点1 (方案A): compute the REAL leakage_ok evidence HERE — the driver has the raw PIT bars + the
    # factor's compute, which the agent loop (PanelPeriods only) does not. Run detect_leakage on a bounded
    # recent slice (leakage is a structural property; a representative slice suffices and stays cheap), then
    # feed it as evidence so the evaluator's leakage gate participates with a real verdict instead of None.
    # regime is self-computed by the evaluator (compute_regime_evidence=True above); PBO by the loop.
    lk_ets = event_times[-80:] if len(event_times) > 80 else event_times
    lk_bars = [b for inst in sorted(instruments)
               for b in store.as_of(inst, lk_ets[-1]) if b.event_time >= lk_ets[0]]
    lk_times = lk_ets[len(lk_ets) // 2: len(lk_ets) // 2 + 3]
    evidence = {f"h_{op}_{w}": {"leakage_ok": signal_leakage_ok(op, w, lk_bars, lk_times)} for op, w in POOL}
    lk_ok = sum(1 for e in evidence.values() if e["leakage_ok"] is True)
    print(f"  leakage evidence: {lk_ok}/{len(evidence)} factors PIT-verified leak-free", flush=True)
    report = loop.run(candidates, timestamp=now_ms, evidence=evidence)["report"]

    # headline: per-CALENDAR-YEAR net Sharpe (the 2022-bear test)
    hp = pool_periods[HEADLINE]
    ht = _periods_with_times(store, instruments, event_times, *HEADLINE)[1]
    hret = _net(hp, 3)
    by_year = defaultdict(list)
    for t, r in zip(ht, hret):
        by_year[time.gmtime(t / 1000).tm_year].append(r)
    per_year = [{"year": y, "n": len(by_year[y]),
                 "net_sharpe": round(sharpe(by_year[y]), 2) if len(by_year[y]) > 1 else None,
                 "mean": round(statistics.fmean(by_year[y]), 5)} for y in sorted(by_year)]

    # long-sample no-peek selection OOS over the focused pool (many folds now)
    pool_ret = {f"h_{op}_{w}": _net(pool_periods[(op, w)], 3) for op, w in POOL
                if len(_net(pool_periods[(op, w)], 3)) >= 2}
    L = min(len(v) for v in pool_ret.values())
    aligned = {k: v[-L:] for k, v in pool_ret.items()}
    sel = walk_forward_selection_oos(aligned, train_size=max(40, L // 2), test_size=max(20, L // 10),
                                     step=max(20, L // 10), embargo=FWD_HORIZON, top_k=1)
    best_id, best_sr = full_sample_best(aligned)

    # cost stress + regime for the headline
    cost_pts, prev, breakeven = [], None, None
    for bps in COST_LEVELS:
        ns = round(sharpe(_net(hp, bps)), 3)
        cost_pts.append({"one_way_bps": bps, "net_sharpe": ns})
        if breakeven is None and prev and prev[1] >= 0 > ns:
            breakeven = round(prev[0] + (bps - prev[0]) * prev[1] / (prev[1] - ns), 1)
        prev = (bps, ns)
    regime = _regime(hp, hret)

    verdicts = []
    for aid, entry in sorted(report.items()):
        ev = entry.get("evaluation")
        if ev is not None:
            verdicts.append({"alpha_id": aid, "net_sharpe": round(ev.net_sharpe, 3),
                             "deflated_sharpe": round(ev.deflated_sharpe, 4), "ic": round(ev.ic_mean, 4),
                             "verdict": ev.verdict.value, "state": entry.get("state")})
    verdicts.sort(key=lambda r: r["net_sharpe"], reverse=True)
    auto = sum(1 for v in verdicts if v["state"] == "APPROVED")

    snapshot = {
        "generated_at": now_ms // 1000,
        "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ms / 1000)),
        "provider": "CryptoCompare histoday (read-only, neutral aggregator)",
        "meta": {"n_instruments": len(instruments), "n_periods": len(event_times),
                 "instruments": instruments, "dropped": dropped, "limit_days": limit,
                 "headline": f"h_{HEADLINE[0]}_{HEADLINE[1]}"},
        "verdicts": verdicts, "auto_approved": auto,
        "per_year": per_year,
        "oos": {"method": "WALK_FORWARD_NO_PEEK_SELECTION_OOS", "n_aligned_periods": L,
                "selection": sel, "naive_full_sample_best": {"alpha_id": best_id, "sharpe": round(best_sr, 4)}},
        "cost_stress": {"factor_id": f"h_{HEADLINE[0]}_{HEADLINE[1]}", "breakeven_bps": breakeven, "points": cost_pts},
        "regime": regime,
    }
    with open(_SNAP, "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2)

    print(f"\n=== MULTI-YEAR OUT-OF-REGIME TEST: {len(instruments)} coins x {len(event_times)} periods ===")
    print(f"auto_approved={auto} (must be 0)  dropped={dropped}")
    print("--- headline net Sharpe BY YEAR (the 2022-bear test) ---")
    for y in per_year:
        print(f"  {y['year']}: net Sharpe {y['net_sharpe']}  (n={y['n']})")
    print(f"--- long-sample no-peek OOS: mean IS {sel['mean_is_sharpe']}  mean OOS {sel['mean_oos_sharpe']}  "
          f"OOS+ {sel['oos_positive_frac']} ({sel['n_folds']} folds) ---")
    print(f"--- cost breakeven ~{breakeven}bps;  regime by_trend "
          f"{[(b['label'], b['sharpe']) for b in regime['by_trend']]} ---")
    print(f"-> wrote {_SNAP}")


if __name__ == "__main__":
    main()
