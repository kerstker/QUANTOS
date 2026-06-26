"""Merge top-factor diagnostics (cost-stress curve + regime split) into an EXISTING daily snapshot
WITHOUT re-running the whole evolutionary search. Offline: reads data/cache/crypto_daily.db plus the
persisted snapshot, computes the diagnostics for the snapshot's top single (h_) factor, and writes
`cost_stress` + `regime` back. Idempotent; reuses the exact logic the daily driver uses.

Useful when the snapshot already has the factors (from a prior `crypto_daily_research.py` run) and you
only need to (re)attach the dashboard diagnostics — far cheaper than re-running the full search.
"""
from __future__ import annotations

import json
import os

from services.data.persistent_store import SqlitePITStore
from services.data.caching_store import CachingPITStore
from tools.crypto_daily_research import _top_factor_diagnostics

_SNAP = os.path.join("data", "cache", "crypto_research_snapshot.json")
_DB = os.path.join("data", "cache", "crypto_daily.db")


def main() -> None:
    if not os.path.exists(_SNAP):
        raise SystemExit(f"no snapshot at {_SNAP} — run tools/crypto_daily_research.py first")
    snap = json.load(open(_SNAP, encoding="utf-8"))
    instruments = sorted(c["instrument_id"] for c in snap.get("meta", {}).get("coins", []))
    if len(instruments) < 2:
        raise SystemExit("snapshot has < 2 instruments")
    store = CachingPITStore(SqlitePITStore(_DB, create=False))
    cost, regime = _top_factor_diagnostics(store, instruments, snap.get("factors", []))
    snap["cost_stress"] = cost
    snap["regime"] = regime
    with open(_SNAP, "w", encoding="utf-8") as fh:
        json.dump(snap, fh, indent=2)
    print(f"merged cost_stress={cost is not None} regime={regime is not None} -> {_SNAP}")
    if cost:
        print(f"  factor={cost['factor_id']} turnover={cost['turnover']} breakeven_bps={cost['breakeven_bps']}")
        print(f"  cost points: {cost['points']}")
    if regime:
        print(f"  by_trend: {regime['by_trend']}")
        print(f"  by_vol:   {regime['by_vol']}")


if __name__ == "__main__":
    main()
