"""NEEDS_HUMAN_REVIEW 持久化队列。

append-only sqlite, 只做持久化 + 记录人工决定, 不做"批准后自动重新提交"。
Hardened to reject empty venues/reviewers, non-finite quantities, and
overwriting non-existent or already-reviewed entries.
"""
from __future__ import annotations

import math
import sqlite3
from typing import List, Optional, Sequence

try:
    from live.contracts import LiveOrderRequest
except Exception:  # pragma: no cover
    LiveOrderRequest = object  # type: ignore[misc,assignment]

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

    def close(self) -> None:
        self._conn.close()

    def record_batch(
        self,
        venue: str,
        *,
        decided_ts: int,
        orders: Sequence[LiveOrderRequest],
        reasons: Sequence[str],
        recorded_at: int,
    ) -> None:
        if not isinstance(venue, str) or not venue.strip():
            raise ValueError("venue must be a non-empty string")
        if isinstance(decided_ts, bool) or not isinstance(decided_ts, int):
            raise ValueError("decided_ts must be an integer")
        if isinstance(recorded_at, bool) or not isinstance(recorded_at, int):
            raise ValueError("recorded_at must be an integer")
        reasons_str = ",".join(str(r) for r in reasons)
        for o in orders:
            qty = getattr(o, "quantity", None)
            if isinstance(qty, bool) or not isinstance(qty, (int, float)) or not math.isfinite(float(qty)) or float(qty) <= 0.0:
                raise ValueError(f"order quantity must be finite and > 0, got {qty!r}")
            side = getattr(o, "side", None)
            side_str = side.value if hasattr(side, "value") else str(side)
            self._conn.execute(
                "INSERT OR IGNORE INTO review_queue"
                "(venue, decided_ts, client_order_id, instrument_id, side, quantity, reasons,"
                " status, recorded_at) VALUES (?,?,?,?,?,?,?, 'PENDING', ?)",
                (
                    venue.strip(),
                    decided_ts,
                    o.client_order_id,
                    o.instrument_id,
                    side_str,
                    float(qty),
                    reasons_str,
                    recorded_at,
                ),
            )
        self._conn.commit()

    def pending(self, venue: Optional[str] = None) -> List[dict]:
        if venue is None:
            rows = self._conn.execute(
                "SELECT * FROM review_queue WHERE status='PENDING' ORDER BY recorded_at"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM review_queue WHERE status='PENDING' AND venue=? "
                "ORDER BY recorded_at",
                (venue,),
            ).fetchall()
        return [dict(r) for r in rows]

    def all_records(self) -> List[dict]:
        return [
            dict(r)
            for r in self._conn.execute("SELECT * FROM review_queue ORDER BY recorded_at")
        ]

    def mark_reviewed(
        self,
        row_id: int,
        *,
        status: str,
        reviewer: str,
        at: int,
        note: str = "",
    ) -> None:
        if status not in _VALID_STATUS:
            raise ValueError(f"status must be one of {_VALID_STATUS}, got {status!r}")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("reviewer must be a non-empty string")
        if isinstance(at, bool) or not isinstance(at, int):
            raise ValueError("at must be an integer")
        if isinstance(row_id, bool) or not isinstance(row_id, int):
            raise ValueError("row_id must be an integer")
        cur = self._conn.execute(
            "UPDATE review_queue SET status=?, reviewer=?, reviewed_at=?, note=? "
            "WHERE id=? AND status='PENDING'",
            (status, reviewer.strip(), at, note, row_id),
        )
        self._conn.commit()
        if cur.rowcount != 1:
            raise ValueError(f"review_queue row {row_id} not found or already reviewed")
