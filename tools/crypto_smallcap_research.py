"""Out-of-gate, READ-ONLY SMALL/MID-CAP cross-sectional alpha research — does the system actually
make money once the cross-section reaches where inefficiency lives?

The large-cap (top ~25) crypto segment is efficiently priced — simple AND rich cross-sectional alphas
found NO cross-regime edge there. Cross-sectional anomalies live in the small/mid-cap TAIL. This driver
widens the universe to the top-N coins by market cap MINUS the large-cap head (CryptoCompare mktcapfull,
free key), fetches multi-year daily, and runs the SAME curated cross-sectional alphas (alpha_expression
engine) through the UNCHANGED contained loop, with per-CALENDAR-YEAR net Sharpe (incl. the 2022 bear)
and a long-sample no-peek OOS. Honest caveat: small-caps carry large trading costs/slippage, so a gross
edge may not survive the cost model — which is exactly what we measure.

Network lives in the fetcher (out of gate); the research core stays offline/deterministic. auto_approved
must stay 0 (containment). Records/promotes nothing.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.data.market_provider import ingest_provider_rows
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.oos_validation import walk_forward_selection_oos, full_sample_best
from services.alpha.panel_backtest import run_panel_backtest
from services.alpha.catalog import AlphaCatalog
from services.alpha.config import AlphaFactoryConfig
from services.intelligence.agent_loop import ResearchAgentLoop
from quantos.contracts.alpha import AlphaDefinition
from tools.cryptocompare_fetch import fetch_top_symbols, fetch_daily_rows
from tools.crypto_alpha_research import ALPHAS, _periods_timed, _per_year

_DB = os.path.join("data", "cache", "crypto_smallcap.db")
_SNAP = os.path.join("data", "cache", "crypto_smallcap_snapshot.json")
_MIN_BARS = 400
_MIN_VOL = 0.01     # daily-return pstdev floor: below this is a stablecoin / pegged asset (no signal)

# stablecoins + wrapped/staked derivatives + index tokens: no independent signal or a duplicate of an
# underlying -> they corrupt the cross-section. The _MIN_VOL filter catches any stablecoin missed here.
_BLOCK = {
    "USDT", "USDC", "DAI", "USDE", "USD1", "TUSD", "BUSD", "FDUSD", "PYUSD", "USDD", "FRAX", "GUSD",
    "USDP", "STABLE", "USDS", "USDL", "EURT", "EURS", "USDB", "USDX", "CUSD", "USDF", "RLUSD", "USD0",
    "WBTC", "WETH", "WBETH", "AETHWETH", "WEETH", "STETH", "CBETH", "RETH", "WSTETH", "SOLVBTC",
    "LBTC", "CBBTC", "BNSOL", "JITOSOL", "MSOL", "WBT", "WLFI",
}


def _ret_vol(rows) -> float:
    closes = [r["close"] for r in rows if r.get("close", 0) > 0]
    if len(closes) < 30:
        return 0.0
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1] > 0]
    if len(rets) < 30:
        return 0.0
    import statistics
    return statistics.pstdev(rets)


def _fetch_retry(tk, now_ms, tries=3):
    for k in range(tries):
        try:
            return fetch_daily_rows(tk, tk, limit=2000, now_ms=now_ms)
        except Exception:
            time.sleep(1.5 * (k + 1))
    return None


def main() -> None:
    n_uni = int(sys.argv[1]) if len(sys.argv) > 1 else 175      # top-by-mktcap to pull
    drop_top = int(sys.argv[2]) if len(sys.argv) > 2 else 25    # drop the large-cap head
    now_ms = int(time.time() * 1000)
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)

    syms = fetch_top_symbols(n_uni)
    universe = syms[drop_top:]
    print(f"universe: top {n_uni} by mktcap minus top {drop_top} large-caps -> {len(universe)} small/mid-caps",
          flush=True)
    store = SqlitePITStore(_DB, create=True)
    instruments, all_ets, dropped = [], set(), 0
    for tk in universe:
        if tk in _BLOCK:
            dropped += 1
            continue
        rows = _fetch_retry(tk, now_ms)
        if not rows or len(rows) < _MIN_BARS or _ret_vol(rows) < _MIN_VOL:
            dropped += 1            # missing / too short / stablecoin-flat -> no cross-sectional signal
            time.sleep(1.2)
            continue
        ingest_provider_rows(rows, store, dataset_id=f"cc-sc-{tk}", now=now_ms, time_unit="ms")
        instruments.append(tk)
        all_ets.update(r["event_time"] for r in rows)
        if len(instruments) % 25 == 0:
            print(f"  ingested {len(instruments)} coins...", flush=True)
        time.sleep(1.2)

    event_times = sorted(all_ets)
    if len(instruments) < 10:
        raise SystemExit(f"only {len(instruments)} coins with >= {_MIN_BARS} bars — rate-limited? retry later")
    store = CachingPITStore(store)
    t0 = time.perf_counter()
    panel = ax.build_field_panel(store, instruments, event_times, ("close", "volume"))
    print(f"\n{len(instruments)} coins x {len(event_times)} daily periods (dropped {dropped}); "
          f"panel built in {time.perf_counter()-t0:.1f}s", flush=True)

    catalog = AlphaCatalog()
    loop = ResearchAgentLoop(catalog=catalog, config=AlphaFactoryConfig(compute_regime_evidence=True))
    rows_out, pool_ret = [], {}
    for name, expr in ALPHAS.items():
        alpha = ax.evaluate(expr, panel)
        periods, times = _periods_timed(alpha, panel)
        if len(periods) < 24:
            continue
        ret = list(run_panel_backtest(periods).period_returns)
        pool_ret[name] = ret
        full = round(sharpe(ret), 3)
        py = _per_year(times, ret)
        defn = AlphaDefinition(alpha_id=ax.alpha_id(expr), name=name, factor_ids=("close",),
                               params={"family": "cs_alpha", "expr": ax.serialize(expr)})
        ev = next(iter(loop.run([(defn, periods)], timestamp=0)["report"].values())).get("evaluation")
        rows_out.append({"name": name, "full_sharpe": full,
                         "deflated": round(ev.deflated_sharpe, 4) if ev else None,
                         "ic": round(ev.ic_mean, 4) if ev else None,
                         "pos_years": sum(1 for v in py.values() if v > 0), "n_years": len(py),
                         "per_year": py, "verdict": ev.verdict.value if ev else "n/a"})

    # long-sample no-peek selection OOS over the alpha pool
    oos = None
    aligned = {k: v for k, v in pool_ret.items() if len(v) >= 2}
    if len(aligned) >= 2:
        L = min(len(v) for v in aligned.values())
        al = {k: v[-L:] for k, v in aligned.items()}
        sel = walk_forward_selection_oos(al, train_size=max(40, L // 2), test_size=max(20, L // 10),
                                         step=max(20, L // 10), embargo=1, top_k=1)
        bid, bsr = full_sample_best(al)
        oos = {"n_aligned": L, "selection": sel, "naive_best": {"alpha_id": bid, "sharpe": round(bsr, 4)}}

    rows_out.sort(key=lambda r: r["full_sharpe"], reverse=True)
    auto = sum(1 for aid in catalog.all_ids() if str(catalog.state(aid).value) == "APPROVED")
    snap = {"generated_at": now_ms // 1000, "provider": "CryptoCompare histoday (small/mid-cap)",
            "n_instruments": len(instruments), "n_periods": len(event_times), "instruments": instruments,
            "auto_approved": auto, "alphas": rows_out, "oos": oos}
    with open(_SNAP, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=2)

    print(f"\n=== SMALL/MID-CAP cross-sectional alphas: {len(instruments)} coins x {len(event_times)} periods ===")
    print(f"auto_approved={auto} (must be 0)")
    print(f"{'alpha':15s} {'fullSR':>7} {'DSR':>7} {'IC':>8} {'+yrs':>6}   per-year")
    for r in rows_out:
        dsr = f"{r['deflated']:.3f}" if r["deflated"] is not None else "n/a"
        ic = f"{r['ic']:.4f}" if r["ic"] is not None else "n/a"
        pys = " ".join(f"{y}:{v:+.1f}" for y, v in r["per_year"].items())
        print(f"{r['name']:15s} {r['full_sharpe']:>7.3f} {dsr:>7} {ic:>8} {r['pos_years']:>3}/{r['n_years']:<2}  {pys}")
    if oos:
        s = oos["selection"]
        print(f"\n[no-peek OOS over the alpha pool] mean IS {s['mean_is_sharpe']}  mean OOS {s['mean_oos_sharpe']}  "
              f"OOS+ {s['oos_positive_frac']} ({s['n_folds']} folds)")
    survivors = [r["name"] for r in rows_out if r["full_sharpe"] > 0 and r["pos_years"] >= max(1, r["n_years"] - 1)]
    print(f"cross-regime survivors: {survivors or 'NONE'}")
    print(f"-> wrote {_SNAP}")


if __name__ == "__main__":
    main()
