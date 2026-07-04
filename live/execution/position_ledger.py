"""本地仓位账本(Stage 1)。设计:docs/superpowers/specs/2026-07-04-live-execution-stack-design.md §3.5。

append-only SQLite,镜像 `services/research/paper_trading/forward.py::ForwardLedger` /
`store.py::PaperTrackStore` 的模式:INSERT OR IGNORE 幂等(同一 client_order_id/fill_id 重复
写入被忽略,不是覆盖——订单/成交记录一旦落库即不可篡改)。fill 自带 side(不依赖 join 回订单表),
一笔成交本身就是自足的账本条目。
"""
from __future__ import annotations

import sqlite3
from typing import Dict, List, Optional

from quantos.contracts.trading import OrderSide

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_orders (
  client_order_id TEXT PRIMARY KEY,
  instrument_id   TEXT NOT NULL,
  side            TEXT NOT NULL,
  quantity        REAL NOT NULL,
  submitted_at    INTEGER NOT NULL,
  venue_order_id  TEXT,
  status          TEXT NOT NULL DEFAULT 'NEW'
);
CREATE TABLE IF NOT EXISTS live_fills (
  fill_id         TEXT PRIMARY KEY,
  client_order_id TEXT NOT NULL,
  instrument_id   TEXT NOT NULL,
  side            TEXT NOT NULL,
  quantity        REAL NOT NULL,
  price           REAL NOT NULL,
  filled_at       INTEGER NOT NULL
);
"""
_TABLES = ("live_orders", "live_fills")


class LiveLedgerError(RuntimeError):
    pass


class LiveLedger:
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
            raise LiveLedgerError(f"live ledger unreadable: {exc}") from exc
        missing = [t for t in _TABLES if t not in {r["name"] for r in rows}]
        if missing:
            raise LiveLedgerError(f"live ledger missing tables: {missing}")

    def record_submission(self, client_order_id: str, instrument_id: str, side: OrderSide,
                          quantity: float, *, submitted_at: int,
                          venue_order_id: str = "", status: str = "NEW") -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO live_orders"
            "(client_order_id, instrument_id, side, quantity, submitted_at, venue_order_id, status) "
            "VALUES (?,?,?,?,?,?,?)",
            (client_order_id, instrument_id, side.value, float(quantity), int(submitted_at),
             venue_order_id, status))
        self._conn.commit()

    def update_status(self, client_order_id: str, status: str, *,
                      venue_order_id: Optional[str] = None) -> None:
        if venue_order_id is not None:
            self._conn.execute(
                "UPDATE live_orders SET status=?, venue_order_id=? WHERE client_order_id=?",
                (status, venue_order_id, client_order_id))
        else:
            self._conn.execute("UPDATE live_orders SET status=? WHERE client_order_id=?",
                               (status, client_order_id))
        self._conn.commit()

    def record_fill(self, fill_id: str, client_order_id: str, instrument_id: str,
                    side: OrderSide, quantity: float, price: float, *, filled_at: int) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO live_fills"
            "(fill_id, client_order_id, instrument_id, side, quantity, price, filled_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (fill_id, client_order_id, instrument_id, side.value, float(quantity), float(price),
             int(filled_at)))
        self._conn.commit()

    def orders(self) -> List[dict]:
        return [dict(r) for r in
                self._conn.execute("SELECT * FROM live_orders ORDER BY submitted_at")]

    def fills(self, instrument_id: Optional[str] = None) -> List[dict]:
        if instrument_id is None:
            return [dict(r) for r in
                    self._conn.execute("SELECT * FROM live_fills ORDER BY filled_at")]
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM live_fills WHERE instrument_id=? ORDER BY filled_at", (instrument_id,))]

    def position(self, instrument_id: str) -> float:
        return self.all_positions().get(instrument_id, 0.0)

    def all_positions(self) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for f in self.fills():
            sign = 1.0 if f["side"] == OrderSide.BUY.value else -1.0
            out[f["instrument_id"]] = out.get(f["instrument_id"], 0.0) + sign * f["quantity"]
        return out
