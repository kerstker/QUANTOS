"""Run the END-TO-END Alpha pipeline (alpha_pipeline.run_alpha_asset_pipeline) on REAL cached crypto
data (Binance perps: close + volume + funding), then run an HONEST no-peek CPCV OOS over the discovered
alphas. Out-of-gate, OFFLINE (reads only the cached DBs/JSON — no network). The point: feed the machine
a MIX of price / volume / funding alphas and see — honestly — which kind it keeps and which generalize.

OBSERVED NUMBERS ONLY. Prints what the run produced; writes nothing into docs/memory.
"""
from __future__ import annotations

import json
import os
import statistics

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.research.alpha_pipeline import run_alpha_asset_pipeline
from services.research.backtest_engine.metrics import sharpe
from services.research.validation.cpcv import cpcv_summary

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND_CACHE = os.path.join("data", "cache", "crypto_funding_rates.json")

F = ("field", "funding")
C = ("field", "close")
V = ("field", "volume")

# a deliberate MIX — price/volume (historically null OOS) + funding carry (the one real edge so far)
EXPRS = [
    ("cs_rank", ("ts_delta", C, 5)),                                  # price momentum
    ("cs_rank", ("neg", ("ts_delta", C, 5))),                         # price reversal
    ("cs_zscore", ("ts_mean", C, 10)),                                # price level
    ("cs_zscore", ("ts_mean", V, 5)),                                 # volume level
    ("cs_rank", ("ts_delta", V, 5)),                                  # volume change
    ("cs_rank", ("neg", ("ts_mean", F, 7))),                          # funding carry (mean7)
    ("cs_zscore", ("neg", ("decay_linear", F, 7))),                   # funding carry (decay7)
    ("cs_rank", ("neg", ("decay_linear", F, 14))),                    # funding carry (decay14)
    ("cs_zscore", ("neg", ("ts_mean", F, 3))),                        # funding carry (mean3)
    ("cs_rank", ("mul", ("neg", ("ts_mean", F, 7)), ("neg", ("ts_delta", C, 5)))),  # carry x reversal
]


def _label(expr) -> str:
    s = ax.serialize(expr)
    return (s[:46] + "…") if len(s) > 47 else s


def main() -> None:
    instruments = sorted(json.load(open(_SNAP, encoding="utf-8"))["instruments"])
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    event_times = sorted({b.event_time for inst in instruments
                          for b in store.fetch_bars(inst, -10**18, 10**18)})
    funding = {tk: {int(k): v for k, v in d.items()}
               for tk, d in json.load(open(_FUND_CACHE, encoding="utf-8")).items()}
    panel = ax.build_field_panel(store, instruments, event_times, ("close", "volume"))
    panel.matrices["funding"] = [[funding.get(inst, {}).get(t) for inst in panel.instruments]
                                 for t in panel.event_times]
    T, N = len(panel.event_times), len(panel.instruments)
    print(f"=== REAL data: {N} perps x {T} daily bars (Binance, offline cache) ===")

    out = run_alpha_asset_pipeline(panel, EXPRS, fwd_horizon=1, dataset_version="binance_funding",
                                   timestamp=0, max_k=6)
    label_by_id = {ax.alpha_id(e): _label(e) for e in EXPRS}

    print(f"\n--- per-alpha (leakage COMPUTED; price-only net SR vs ECONOMIC SR = price + funding) ---")
    print(f"{'expression':48s} {'leak_ok':>8} {'price_SR':>9} {'eco_SR':>8}  verdict")
    for a in out["assets"]:
        sr, eco = a["net_sharpe"], a.get("economic_sharpe")
        print(f"{label_by_id.get(a['alpha_id'], a['alpha_id']):48s} {str(a['leakage_ok']):>8} "
              f"{('' if sr is None else f'{sr:8.3f}'):>9} {('' if eco is None else f'{eco:7.3f}'):>8}  {a['verdict']}")
    print(f"leakage summary: {out['leakage_summary']}")

    print(f"\n--- population optimizer (P4): de-redundant set + weights ---")
    for k in out["portfolio"]["selected_ids"]:
        print(f"  {label_by_id.get(k, k):48s} w={out['portfolio']['weights'].get(k, 0):.3f}")

    # HONEST no-peek CPCV OOS over the discovered alphas — on the ECONOMIC (carry-aware) returns
    rbi = out["economic_returns_by_id"] or out["returns_by_id"]
    if len(rbi) >= 2:
        length = min(len(r) for r in rbi.values())
        aligned = {k: tuple(v[-length:]) for k, v in rbi.items()}
        cp = cpcv_summary(aligned, n_groups=6, k_test=2, embargo=1)
        print(f"\n--- CPCV no-peek OOS over the {len(rbi)} alphas (ECONOMIC returns, combinatorial purged CV) ---")
        if cp:
            print(f"  splits={cp['n_splits']} mean_oos_SR={cp['mean_oos_sharpe']} "
                  f"frac_positive={cp['frac_positive']} p05={cp['p05_oos_sharpe']} "
                  f"p95={cp['p95_oos_sharpe']}")
            print(f"  selected-on-train counts: "
                  f"{ {label_by_id.get(k, k)[:30]: v for k, v in cp['selected_counts'].items()} }")
        else:
            print("  (not assessable)")

    print(f"\n--- Signal->Portfolio target book (P5, latest cross-section) ---")
    tb = out["target_book"]
    print(f"  gross={tb['gross']} net={round(tb['net'], 6)} n_positions={tb['n_positions']}")

    print(f"\n--- auto research brief (P7) ---")
    print(f"  {out['brief']['summary']}")
    for act in out["brief"]["next_actions"][:3]:
        print(f"  next: {act}")


if __name__ == "__main__":
    main()
