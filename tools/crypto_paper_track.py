# tools/crypto_paper_track.py
"""断点2 driver: populate the forward paper-track store from the offline research snapshot's real per-factor
return series, with an HONEST train/paper split — the [boundary:] tail is the paper (out-of-sample) region,
and the [:boundary] head's Sharpe is the backtest reference. Deterministic, idempotent (re-runnable). Reads
local data only (no network). Writes data/cache/paper_track.db."""
from __future__ import annotations

import json
import os
import sys

from services.research.backtest_engine.metrics import sharpe
from services.research.paper_trading.store import PaperTrackStore
from services.research.paper_trading.advance import advance_paper_track

_SNAP = os.path.join("data", "cache", "crypto_research_snapshot.json")
_DB = os.path.join("data", "cache", "paper_track.db")
_DAY_MS = 86_400_000


def _factor_series() -> dict:
    try:
        snap = json.load(open(_SNAP, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = snap.get("factor_series", {})   # {alpha_id: {"name":, "net_sharpe":, "returns": [...]}}
    series: dict = {}
    for aid, entry in raw.items():
        if isinstance(entry, dict):
            rets = entry.get("returns")
        elif isinstance(entry, list):
            rets = entry
        else:
            rets = None
        if isinstance(rets, list):
            series[aid] = rets   # {alpha_id: [per-period net return]}
    return series


def main(boundary_frac: float = 0.7) -> None:
    series = _factor_series()
    if not series:
        print("no factor_series in snapshot — run tools/crypto_research_offline first")
        return
    if os.path.exists(_DB):
        os.remove(_DB)                      # rebuild deterministically
    store = PaperTrackStore(_DB, create=True)
    n_alphas = 0
    for aid, rets in series.items():
        if not isinstance(rets, list) or len(rets) < 20:
            continue
        boundary = max(10, int(len(rets) * boundary_frac))
        if boundary >= len(rets):
            continue
        bt_sharpe = round(sharpe(rets[:boundary]), 4)     # train-head Sharpe = the honest backtest ref
        n_new = advance_paper_track(store, aid, rets, None, boundary=boundary, source="OOS_TAIL",
                                    ts0=0, dt=_DAY_MS, backtest_sharpe=bt_sharpe)
        n_alphas += 1
    tracks = store.all_tracks()
    print(f"populated {n_alphas} alphas; {len(tracks)} paper tracks -> {_DB}")
    for t in sorted(tracks, key=lambda x: -(x["paper_sharpe"] or -9))[:5]:
        print(f"  {t['alpha_id']:16s} n={t['n_periods']:3d} paperSR {t['paper_sharpe']:+.2f} "
              f"btSR {t['backtest_sharpe']:+.2f} gap {t['perf_gap']}")


if __name__ == "__main__":
    frac = float(sys.argv[sys.argv.index("--boundary-frac") + 1]) if "--boundary-frac" in sys.argv else 0.7
    main(frac)
