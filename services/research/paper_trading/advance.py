# services/research/paper_trading/advance.py
"""Advance an alpha's forward paper track by the held-out tail [boundary:] of its realized returns/IC.
The held-out segment is the genuinely out-of-sample paper region (the driver evaluates on [:boundary]).
Idempotent (INSERT OR IGNORE in the store). Pure, deterministic."""
from __future__ import annotations

from typing import Optional, Sequence

from .store import PaperTrackStore


def advance_paper_track(store: PaperTrackStore, alpha_id: str, returns: Sequence[float],
                        ic_series: Optional[Sequence[float]], *, boundary: int,
                        source: str = "OOS_TAIL", ts0: int = 0, dt: int = 86_400_000,
                        backtest_sharpe: Optional[float] = None) -> int:
    paper_r = list(returns)[boundary:]
    paper_ic = list(ic_series)[boundary:] if ic_series else [None] * len(paper_r)
    existing = {p["as_of_ts"] for p in store.periods(alpha_id)}
    rows, n_new = [], 0
    for i, r in enumerate(paper_r):
        ts = ts0 + (boundary + i) * dt
        ic = paper_ic[i] if i < len(paper_ic) else None
        if ts not in existing:
            n_new += 1
        rows.append((ts, r, ic, source))
    store.append_periods(alpha_id, rows)
    store.recompute_summary(alpha_id, backtest_sharpe=backtest_sharpe)
    return n_new
