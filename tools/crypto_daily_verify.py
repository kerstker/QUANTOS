"""Out-of-gate, OFFLINE adversarial verification of the DAILY trend finding.

Reads only the already-persisted daily PIT store (data/cache/crypto_daily.db) — NO network.
Two skeptic tests on the dist_from_high trend signal that topped the daily run:

  1) COST STRESS — the default CostModel is just 3 bps one-way (fee 1 + slip 2): fine for majors,
     LIGHT for alts. Rebuild the whole atomic price-factor pool at escalating one-way costs and
     re-run the no-peek walk-forward SELECTION OOS at each. Find the breakeven cost where the
     out-of-sample edge dies. (Panel periods are built ONCE per factor; only the cheap backtest
     re-runs per cost.)

  2) CONCENTRATION (leave-one-coin-out) — is the headline edge broad or carried by 1-2 mega-winners?
     Drop each coin in turn, recompute the headline factor's full-sample net Sharpe (default cost).
     A tight spread = broad; a big swing when one coin leaves = a concentrated bet.

Prints a table. Records nothing, promotes nothing, fetches nothing.
"""
from __future__ import annotations

import json
import os

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research.backtest_engine.costs import CostModel
from services.research.backtest_engine.metrics import sharpe
from services.research.panel_builder import build_panel_periods
from services.research.signals import SIGNAL_OPS
from services.alpha.panel_backtest import run_panel_backtest
from services.research.oos_validation import walk_forward_selection_oos, full_sample_best

OPERATORS = ("momentum", "ts_mean", "zscore", "volatility", "ts_rank",
             "ema_ratio", "dist_from_high", "accel")
WINDOWS = (3, 7, 14, 30)
FWD_HORIZON = 1
HEADLINE = ("dist_from_high", 14)
COST_LEVELS_BPS = (3, 10, 20, 30, 50)        # total ONE-WAY bps (fee+slippage)
_DAILY_DB = os.path.join("data", "cache", "crypto_daily.db")


def _periods_for(store, instruments, event_times, op, window):
    fn = SIGNAL_OPS[op]
    return build_panel_periods(store, instruments, event_times=event_times, fwd_horizon=FWD_HORIZON,
                              signal_fn=lambda closes: fn(closes, window))


def _net_returns(periods, one_way_bps):
    cm = CostModel(fee_bps=0.0, slippage_bps=float(one_way_bps))
    return list(run_panel_backtest(periods, cm).period_returns)


def main() -> None:
    store = CachingPITStore(SqlitePITStore(_DAILY_DB, create=False))
    # instrument list comes from the daily snapshot's coin metadata (the store has no list-API)
    snap_path = os.path.join("data", "cache", "crypto_research_snapshot.json")
    instruments = []
    if os.path.exists(snap_path):
        snap = json.load(open(snap_path, encoding="utf-8"))
        instruments = sorted(c["instrument_id"] for c in snap.get("meta", {}).get("coins", []))
    if len(instruments) < 2:
        raise SystemExit("need >=2 instruments from the daily snapshot; run tools/crypto_daily_research.py")
    event_times = sorted({b.event_time for inst in instruments
                          for b in store.fetch_bars(inst, 0, 10 ** 14)})
    print(f"instruments={len(instruments)} daily periods={len(event_times)}")

    # build each factor's panel periods ONCE (the expensive step)
    periods_by_factor = {}
    for op in OPERATORS:
        for w in WINDOWS:
            per = _periods_for(store, instruments, event_times, op, w)
            if per:
                periods_by_factor[f"h_{op}_{w}"] = per
    headline_id = f"h_{HEADLINE[0]}_{HEADLINE[1]}"

    print("\n=== 1) COST STRESS: no-peek selection OOS vs one-way cost ===")
    print(f"{'one-way bps':>11} {'headline full-net Sharpe':>24} {'mean IS':>9} {'mean OOS':>9} "
          f"{'OOS+ frac':>9} {'selected':>22}")
    for bps in COST_LEVELS_BPS:
        pool = {fid: _net_returns(per, bps) for fid, per in periods_by_factor.items()}
        pool = {k: v for k, v in pool.items() if len(v) >= 2}
        L = min(len(v) for v in pool.values())
        aligned = {k: v[-L:] for k, v in pool.items()}
        test_size = max(8, L // 8)
        train_size = max(20, L // 2)
        sel = walk_forward_selection_oos(aligned, train_size=train_size, test_size=test_size,
                                         step=test_size, embargo=FWD_HORIZON, top_k=1)
        head_full = sharpe(aligned.get(headline_id, [0.0, 0.0]))
        last_sel = sel["folds"][-1]["selected"] if sel.get("folds") else []
        print(f"{bps:>11} {head_full:>24.3f} {sel['mean_is_sharpe']:>9.3f} {sel['mean_oos_sharpe']:>9.3f} "
              f"{sel['oos_positive_frac']:>9.2f} {','.join(last_sel):>22}")

    print("\n=== 2) CONCENTRATION: leave-one-coin-out, headline full-sample net Sharpe (3bps) ===")
    base_per = periods_by_factor[headline_id]
    base_sr = sharpe(_net_returns(base_per, 3))
    print(f"baseline (all {len(instruments)} coins): {base_sr:.3f}")
    loo = []
    for drop in instruments:
        keep = [i for i in instruments if i != drop]
        per = _periods_for(store, keep, event_times, *HEADLINE)
        sr = sharpe(_net_returns(per, 3)) if per else float("nan")
        loo.append((drop, sr))
    loo.sort(key=lambda x: x[1])
    print("  most-damaging to drop (lowest resulting Sharpe = that coin was carrying it):")
    for drop, sr in loo[:5]:
        print(f"    drop {drop:5s} -> {sr:.3f}  (Δ {sr - base_sr:+.3f})")
    print("  least-damaging / improves when dropped:")
    for drop, sr in loo[-3:]:
        print(f"    drop {drop:5s} -> {sr:.3f}  (Δ {sr - base_sr:+.3f})")
    srs = [sr for _, sr in loo]
    print(f"  leave-one-out spread: min={min(srs):.3f} max={max(srs):.3f} "
          f"(baseline {base_sr:.3f}) -> {'BROAD' if (max(srs)-min(srs)) < 1.0 else 'CONCENTRATED-ish'}")


if __name__ == "__main__":
    main()
