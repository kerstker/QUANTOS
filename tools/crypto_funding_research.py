"""Out-of-gate, OFFLINE-core cross-sectional research on PRICE + FUNDING-RATE (an alt-data dimension).

Price+volume daily showed no cross-regime edge even across 336 coins. Funding rate is ORTHOGONAL data —
the perp long/short carry, a direct read of leverage/positioning. This driver pulls Binance public klines
(price) AND public funding history for the USDT-perp universe, builds the field panel with an extra
`funding` matrix, and runs FUNDING-based + funding×price cross-sectional alphas through the UNCHANGED
contained loop, with per-CALENDAR-YEAR net Sharpe (incl. 2022 bear) and a long-sample no-peek OOS.

Read-only public DATA only (no key/account/order/SDK). auto_approved must stay 0. Honest caveats: funding
carry is a documented-but-crowded crypto effect; survivorship bias (today's surviving perps); perp trading
costs (fees + funding paid) are real — measured by the cost model. Reports ONLY observed numbers.
"""
from __future__ import annotations

import json
import os
import sys
import time

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
from tools.binance_klines_fetch import fetch_daily_rows
from tools.binance_funding_fetch import fetch_perp_symbols, fetch_daily_funding
from tools.crypto_alpha_research import _periods_timed, _per_year
from tools.crypto_smallcap_research import _BLOCK, _ret_vol

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_MIN_BARS = 400
_MIN_VOL = 0.01

C, FUND = ("field", "close"), ("field", "funding")
# funding-based + funding×price cross-sectional alphas (none expressible without the funding dimension).
# carry convention: NEGATIVE funding => you are PAID to hold long, so signal = -funding (long high signal).
ALPHAS = {
    "fund_carry":    ("cs_rank", ("neg", FUND)),
    "fund_carry_z":  ("cs_zscore", ("neg", FUND)),
    "fund_avg7":     ("cs_rank", ("neg", ("ts_mean", FUND, 7))),
    "fund_mom3":     ("cs_rank", ("ts_delta", FUND, 3)),
    "fund_decay7":   ("cs_zscore", ("decay_linear", ("neg", FUND), 7)),
    "fund_x_pxmom":  ("cs_rank", ("mul", ("neg", FUND), ("ts_delta", C, 5))),
    "fund_x_rev":    ("cs_rank", ("mul", ("neg", FUND), ("neg", ("ts_delta", C, 1)))),
    "fund_lvl_rank": ("cs_rank", FUND),
    # price controls (no funding) for comparison
    "px_mom20":      ("cs_rank", ("ts_delta", C, 20)),
    "px_rev1":       ("cs_rank", ("neg", ("ts_delta", C, 1))),
}


def main() -> None:
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    now_ms = int(time.time() * 1000)
    os.makedirs(os.path.join("data", "cache"), exist_ok=True)
    syms = [s for s in fetch_perp_symbols() if s not in _BLOCK][:cap]
    print(f"USDT-perp universe (capped): {len(syms)}", flush=True)

    store = SqlitePITStore(_DB, create=True)
    funding_by_tk, instruments, all_ets, dropped, failed = {}, [], set(), 0, 0
    for tk in syms:
        try:
            rows = fetch_daily_rows(tk, days=2200, now_ms=now_ms)
            fund = fetch_daily_funding(tk, days=2200, now_ms=now_ms)
        except Exception:
            failed += 1
            continue
        if not rows or len(rows) < _MIN_BARS or _ret_vol(rows) < _MIN_VOL or len(fund) < _MIN_BARS:
            dropped += 1
            continue
        ingest_provider_rows(rows, store, dataset_id=f"bnf-{tk}", now=now_ms, time_unit="ms")
        funding_by_tk[tk] = fund
        instruments.append(tk)
        all_ets.update(r["event_time"] for r in rows)
        if len(instruments) % 20 == 0:
            print(f"  ingested {len(instruments)} (dropped {dropped}, failed {failed})...", flush=True)
        time.sleep(0.1)

    event_times = sorted(all_ets)
    if len(instruments) < 10:
        raise SystemExit(f"only {len(instruments)} usable perps")
    store = CachingPITStore(store)
    t0 = time.perf_counter()
    panel = ax.build_field_panel(store, instruments, event_times, ("close", "volume"))
    # inject the funding field matrix aligned to (event_times x sorted instruments)
    panel.matrices["funding"] = [[funding_by_tk.get(inst, {}).get(t) for inst in panel.instruments]
                                 for t in panel.event_times]
    cover = sum(1 for row in panel.matrices["funding"] for v in row if v is not None)
    print(f"\n{len(instruments)} perps x {len(event_times)} daily periods (dropped {dropped}, failed {failed}); "
          f"panel {time.perf_counter()-t0:.1f}s; funding cells {cover}", flush=True)

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
        py = _per_year(times, ret)
        defn = AlphaDefinition(alpha_id=ax.alpha_id(expr), name=name, factor_ids=("close",),
                               params={"family": "funding_alpha", "expr": ax.serialize(expr)})
        ev = next(iter(loop.run([(defn, periods)], timestamp=0)["report"].values())).get("evaluation")
        rows_out.append({"name": name, "full_sharpe": round(sharpe(ret), 3),
                         "deflated": round(ev.deflated_sharpe, 4) if ev else None,
                         "ic": round(ev.ic_mean, 4) if ev else None,
                         "pos_years": sum(1 for v in py.values() if v > 0), "n_years": len(py),
                         "per_year": py, "verdict": ev.verdict.value if ev else "n/a"})

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
    snap = {"generated_at": now_ms // 1000, "provider": "Binance public klines + funding (perp)",
            "n_instruments": len(instruments), "n_periods": len(event_times), "instruments": instruments,
            "auto_approved": auto, "alphas": rows_out, "oos": oos}
    with open(_SNAP, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=2)

    print(f"\n=== PRICE+FUNDING cross-sectional alphas: {len(instruments)} perps x {len(event_times)} periods ===")
    print(f"auto_approved={auto} (must be 0)")
    print(f"{'alpha':15s} {'fullSR':>7} {'DSR':>7} {'IC':>8} {'+yrs':>6}   per-year")
    for r in rows_out:
        dsr = f"{r['deflated']:.3f}" if r["deflated"] is not None else "n/a"
        ic = f"{r['ic']:.4f}" if r["ic"] is not None else "n/a"
        pys = " ".join(f"{y}:{v:+.1f}" for y, v in r["per_year"].items())
        print(f"{r['name']:15s} {r['full_sharpe']:>7.3f} {dsr:>7} {ic:>8} {r['pos_years']:>3}/{r['n_years']:<2}  {pys}")
    if oos:
        s = oos["selection"]
        print(f"\n[no-peek OOS] mean IS {s['mean_is_sharpe']}  mean OOS {s['mean_oos_sharpe']}  "
              f"OOS+ {s['oos_positive_frac']} ({s['n_folds']} folds)  naive-best {oos['naive_best']}")
    survivors = [r["name"] for r in rows_out if r["full_sharpe"] > 0 and r["pos_years"] >= max(1, r["n_years"] - 1)]
    print(f"cross-regime survivors: {survivors or 'NONE'}")
    print(f"-> wrote {_SNAP}")


if __name__ == "__main__":
    main()
