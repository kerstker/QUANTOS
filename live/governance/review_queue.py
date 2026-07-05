"""NEEDS_HUMAN_REVIEW 持久化队列(2026-07-05,机构审计 P1)。设计:
docs/superpowers/specs/2026-07-05-institutional-live-stack-design.md 补充。

append-only sqlite,镜像 `live/execution/position_ledger.py` 的既有模式。**只做持久化 +
记录人工决定,不做"批准后自动重新提交"**——见模块顶部的诚实边界说明(那是需要独立设计的
后续工作:批准时的价格/时间早已不是原样,直接重放原订单本身就不安全)。
"""
from __future__ import annotations

import sqlite3
from typing import List, Optional, Sequence

from live.contracts import LiveOrderRequest

_SCHEMA = """
CREATE TABLE IF NOT EXISTS review_queue (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  venue           TEXT NOT NULL,
  decided_ts      INTEGER NOT NULL,
  client_order_id TEXT NOT NULL,
  instrument_id   TEXT NOT NULL,
  side            TEXT NOT NULL,
  quantity        REAL NOT NULL,
  reasons         TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'PENDING',
  reviewer        TEXT,
  reviewed_at     INTEGER,
  note            TEXT,
  recorded_at     INTEGER NOT NULL,
  UNIQUE(venue, decided_ts, client_order_id)
);
"""
_VALID_STATUS = {"APPROVED", "REJECTED"}


class ReviewQueue:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def record_batch(self, venue: str, *, decided_ts: int, orders: Sequence[LiveOrderRequest],
                     reasons: Sequence[str], recorded_at: int) -> None:
        reasons_str = ",".join(reasons)
        for o in orders:
            self._conn.execute(
                "INSERT OR IGNORE INTO review_queue"
                "(venue, decided_ts, client_order_id, instrument_id, side, quantity, reasons,"
                " status, recorded_at) VALUES (?,?,?,?,?,?,?, 'PENDING', ?)",
                (venue, int(decided_ts), o.client_order_id, o.instrument_id, o.side.value,
                 float(o.quantity), reasons_str, int(recorded_at)))
        self._conn.commit()

    def pending(self, venue: Optional[str] = None) -> List[dict]:
        if venue is None:
            rows = self._conn.execute(
                "SELECT * FROM review_queue WHERE status='PENDING' ORDER BY recorded_at").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM review_queue WHERE status='PENDING' AND venue=? "
                "ORDER BY recorded_at", (venue,)).fetchall()
        return [dict(r) for r in rows]

    def all_records(self) -> List[dict]:
        return [dict(r) for r in
                self._conn.execute("SELECT * FROM review_queue ORDER BY recorded_at")]

    def mark_reviewed(self, row_id: int, *, status: str, reviewer: str, at: int,
                      note: str = "") -> None:
        if status not in _VALID_STATUS:
            raise ValueError(f"status must be one of {_VALID_STATUS}, got {status!r}")
        self._conn.execute(
            "UPDATE review_queue SET status=?, reviewer=?, reviewed_at=?, note=? WHERE id=?",
            (status, reviewer, int(at), note, row_id))
        self._conn.commit()
