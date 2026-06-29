"""Translate the forward paper-track summaries into the {backtest, live} shape that
services/alpha/maintenance.run_maintenance feeds to degradation_monitor.monitor_alpha_health. The live side
is the REAL accumulated paper metrics; the backtest side is the supplied evaluation metrics. Only alphas
with a paper track AND a backtest reference are included (fail-closed: no paper -> not in the dict)."""
from __future__ import annotations

from typing import Mapping

from .store import PaperTrackStore


def paper_health_by_alpha(store: PaperTrackStore, *, backtest_by_alpha: Mapping[str, dict]) -> dict:
    out: dict = {}
    for t in store.all_tracks():
        aid = t["alpha_id"]
        bt = backtest_by_alpha.get(aid)
        if bt is None:
            continue
        out[aid] = {"backtest": dict(bt),
                    "live": {"ic": t.get("paper_ic"), "return": t.get("cum_return")}}
    return out
