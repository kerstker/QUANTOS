"""Out-of-gate, OFFLINE adversarial verification of the funding-carry LEAD (fund_avg7).

The pool no-peek OOS was NEGATIVE — so before believing the carry family, track the headline carry factor
ALONE (no pool selection): its own no-peek walk-forward IS-vs-OOS per fold, a COST-STRESS sweep (carry is
high-turnover; perps have fees), and per-year. Reuses the prices already in crypto_funding.db and re-fetches
only the funding history. Reports ONLY observed numbers. Promotes nothing.
"""
from __future__ import annotations

import json
import os
import time

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.research.backtest_engine.metrics import sharpe
from services.research.backtest_engine.costs import CostModel
from services.research.validation.walk_forward import walk_forward_splits
from services.alpha.panel_backtest import run_panel_backtest
from tools.binance_funding_fetch import fetch_daily_funding
from tools.crypto_alpha_research import _periods_timed, _per_year

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
FUND = ("field", "funding")
HEADLINE = ("cs_rank", ("neg", ("ts_mean", FUND, 7)))     # fund_avg7
COST_BPS = (3, 10, 20, 30, 50)


def _net(periods, bps):
    return list(run_panel_backtest(periods, CostModel(fee_bps=0.0, slippage_bps=float(bps))).period_returns)


def main() -> None:
    instruments = sorted(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    event_times = sorted({b.event_time for inst in instruments
                          for b in store.fetch_bars(inst, -10**18, 10**18)})
    now_ms = int(time.time() * 1000)
    print(f"re-fetching funding for {len(instruments)} perps...", flush=True)
    funding = {}
    for i, tk in enumerate(instruments):
        try:
            funding[tk] = fetch_daily_funding(tk, days=2200, now_ms=now_ms)
        except Exception:
            funding[tk] = {}
        if (i + 1) % 30 == 0:
            print(f"  {i+1}/{len(instruments)}", flush=True)
        time.sleep(0.1)

    panel = ax.build_field_panel(store, instruments, event_times, ("close", "volume"))
    panel.matrices["funding"] = [[funding.get(inst, {}).get(t) for inst in panel.instruments]
                                 for t in panel.event_times]
    alpha = ax.evaluate(HEADLINE, panel)
    periods, times = _periods_timed(alpha, panel)
    ret3 = _net(periods, 3)
    turn = round(sum(run_panel_backtest(periods).turnovers) / max(1, len(periods)), 3)

    # tracked no-peek OOS for THIS factor alone (no selection)
    L = len(ret3)
    test, train = max(20, L // 10), max(40, L // 2)
    folds = []
    for w, (tr, te) in enumerate(walk_forward_splits(L, train, test, test)):
        tr2 = tr[:-1] if len(tr) > 1 else tr
        folds.append((w, round(sharpe([ret3[j] for j in tr2]), 2), round(sharpe([ret3[j] for j in te]), 2)))
    oos_vals = [o for _, _, o in folds]
    mean_oos = round(sum(oos_vals) / len(oos_vals), 3) if oos_vals else None
    oos_pos = round(sum(1 for o in oos_vals if o > 0) / len(oos_vals), 2) if oos_vals else None

    print(f"\n=== fund_avg7 ALONE: {len(panel.instruments)} perps x {len(event_times)} periods ===")
    print(f"full-sample net Sharpe(3bps) {round(sharpe(ret3),3)}  turnover {turn}")
    print(f"--- tracked no-peek OOS (this factor only) ---")
    for w, isr, osr in folds:
        print(f"  fold{w}: IS {isr:>6}  OOS {osr:>6}")
    print(f"  mean OOS {mean_oos}  OOS+ {oos_pos} ({len(folds)} folds)")
    print(f"--- cost stress (full-sample net Sharpe) ---")
    for bps in COST_BPS:
        print(f"  {bps:>2}bps: {round(sharpe(_net(periods, bps)),3)}")
    print(f"--- per-year (3bps) ---  " + " ".join(f"{y}:{v:+.1f}" for y, v in _per_year(times, ret3).items()))


if __name__ == "__main__":
    main()
