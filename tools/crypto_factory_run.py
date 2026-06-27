"""MASS-PRODUCTION on REAL data: run the cs_genome alpha factory (run_alpha_factory) on real cached
Binance perps (close + volume + funding) — let the deterministic genome EVOLVE cs-alphas scored by
economic L/S Sharpe, then run the full pipeline + a no-peek CPCV OOS over the elite. Out-of-gate,
OFFLINE (cached only, no network). The test: does the machine, breeding from scratch, DISCOVER the
funding carry on its own? OBSERVED NUMBERS ONLY.
"""
from __future__ import annotations

import json
import os

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from services.research import alpha_expression as ax
from services.research.alpha_factory import run_alpha_factory
from services.research.validation.cpcv import cpcv_summary

_DB = os.path.join("data", "cache", "crypto_funding.db")
_SNAP = os.path.join("data", "cache", "crypto_funding_snapshot.json")
_FUND_CACHE = os.path.join("data", "cache", "crypto_funding_rates.json")

FIELDS = ["close", "volume", "funding"]
WINDOWS = [3, 5, 7, 14]


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
    print(f"=== MASS PRODUCTION on REAL data: {len(panel.instruments)} perps x "
          f"{len(panel.event_times)} daily bars ===")

    out = run_alpha_factory(panel, FIELDS, WINDOWS, generations=3, population_size=24, elite_k=8,
                            dataset_version="binance_funding", timestamp=0)
    print(f"genome bred & scored {out['n_evaluated']} distinct cs-alphas over {out['generations']} "
          f"generations (scored by {out['scored_by']} L/S Sharpe)\n")

    print(f"--- TOP elite the machine discovered (economic Sharpe, leakage COMPUTED) ---")
    for b in out["best"]:
        e = b["expr"]
        print(f"  eco_SR={b['score']:7.3f}  leak_ok={b['leakage_ok']}  {e[:58]}")

    rbi = out["pipeline"]["economic_returns_by_id"] or out["pipeline"]["returns_by_id"]
    if len(rbi) >= 2:
        length = min(len(r) for r in rbi.values())
        aligned = {k: tuple(v[-length:]) for k, v in rbi.items()}
        cp = cpcv_summary(aligned, n_groups=6, k_test=2, embargo=1)
        if cp:
            print(f"\n--- no-peek CPCV OOS over the elite (ECONOMIC returns) ---")
            print(f"  splits={cp['n_splits']} mean_oos_SR={cp['mean_oos_sharpe']} "
                  f"frac_positive={cp['frac_positive']} p05={cp['p05_oos_sharpe']} "
                  f"p95={cp['p95_oos_sharpe']}")

    print(f"\n--- final pipeline portfolio (P4) + brief (P7) ---")
    print(f"  selected: {len(out['pipeline']['portfolio']['selected_ids'])} alphas; "
          f"target book net={round(out['pipeline']['target_book']['net'], 6)}")
    print(f"  brief: {out['pipeline']['brief']['summary']}")


if __name__ == "__main__":
    main()
