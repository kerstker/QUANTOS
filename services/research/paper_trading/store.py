"""Forward paper-track persistence (断点2). Append-only sqlite ledger of an alpha's REAL out-of-sample
paper P&L / IC, one row per period (immutable), plus a recomputed per-alpha summary. Mirrors the
experiment_registry store pattern. Pure stdlib, deterministic (no RNG / no wall-clock here)."""
from __future__ import annotations

import sqlite3
import statistics
from typing import Optional, Sequence

from services.research.backtest_engine.metrics import sharpe
from services.research.paper_trading.domain.consistency import performance_gap


class PaperTrackStoreError(RuntimeError):
    pass


_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_periods (
  alpha_id        TEXT    NOT NULL,
  as_of_ts        INTEGER NOT NULL,
  realized_return REAL    NOT NULL,
  realized_ic     REAL,
  source          TEXT    NOT NULL,
  PRIMARY KEY (alpha_id, as_of_ts)
);
CREATE TABLE IF NOT EXISTS paper_track (
  alpha_id        TEXT PRIMARY KEY,
  start_ts        INTEGER, last_ts INTEGER, n_periods INTEGER,
  cum_return      REAL, paper_sharpe REAL, paper_ic REAL,
  backtest_sharpe REAL, perf_gap REAL
);
"""
_TABLES = ("paper_periods", "paper_track")


class PaperTrackStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._require_schema()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise PaperTrackStoreError(f"paper-track store unreadable: {exc}") from exc
        missing = [t for t in _TABLES if t not in {r["name"] for r in rows}]
        if missing:
            raise PaperTrackStoreError(f"paper-track store missing tables: {missing}")

    def append_periods(self, alpha_id: str, rows: Sequence[tuple]) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO paper_periods(alpha_id, as_of_ts, realized_return, realized_ic, source) "
            "VALUES (?,?,?,?,?)",
            [(alpha_id, int(ts), float(r), (None if ic is None else float(ic)), str(src))
             for ts, r, ic, src in rows])
        self._conn.commit()

    def periods(self, alpha_id: str) -> list:
        return [dict(r) for r in self._conn.execute(
            "SELECT as_of_ts, realized_return, realized_ic, source FROM paper_periods "
            "WHERE alpha_id=? ORDER BY as_of_ts", (alpha_id,))]

    def recompute_summary(self, alpha_id: str, *, backtest_sharpe: Optional[float] = None) -> None:
        ps = self.periods(alpha_id)
        if not ps:
            return
        rets = [p["realized_return"] for p in ps]
        ics = [p["realized_ic"] for p in ps if p["realized_ic"] is not None]
        cum = 1.0
        for r in rets:
            cum *= (1.0 + r)
        psharpe = sharpe(rets)
        pic = statistics.fmean(ics) if ics else None
        gap = performance_gap(backtest_sharpe, psharpe)
        self._conn.execute(
            "INSERT OR REPLACE INTO paper_track(alpha_id, start_ts, last_ts, n_periods, cum_return, "
            "paper_sharpe, paper_ic, backtest_sharpe, perf_gap) VALUES (?,?,?,?,?,?,?,?,?)",
            (alpha_id, ps[0]["as_of_ts"], ps[-1]["as_of_ts"], len(ps), round(cum - 1.0, 6),
             round(psharpe, 4), (None if pic is None else round(pic, 4)), backtest_sharpe,
             (None if gap is None else round(gap, 4))))
        self._conn.commit()

    def track(self, alpha_id: str) -> Optional[dict]:
        r = self._conn.execute("SELECT * FROM paper_track WHERE alpha_id=?", (alpha_id,)).fetchone()
        return dict(r) if r else None

    def all_tracks(self) -> list:
        return [dict(r) for r in self._conn.execute("SELECT * FROM paper_track ORDER BY alpha_id")]
