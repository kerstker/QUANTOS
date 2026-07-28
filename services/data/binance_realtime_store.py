"""Append-only, hash-chained storage for public Binance market snapshots."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any


class BinanceRealtimeStoreError(RuntimeError):
    """The Binance public-market store is missing, corrupt, or conflicting."""


_GENESIS = ""
_SCHEMA = """
CREATE TABLE IF NOT EXISTS binance_market_snapshots (
  seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  instrument_id    TEXT NOT NULL,
  observed_at_ms   INTEGER NOT NULL,
  event_time_ms    INTEGER NOT NULL,
  payload_json     TEXT NOT NULL,
  payload_sha256   TEXT NOT NULL,
  prev_hash        TEXT NOT NULL,
  row_hash         TEXT NOT NULL,
  UNIQUE(instrument_id, observed_at_ms)
);
CREATE INDEX IF NOT EXISTS idx_binance_market_event_time
ON binance_market_snapshots(instrument_id, event_time_ms);
CREATE TRIGGER IF NOT EXISTS binance_market_snapshots_no_update
BEFORE UPDATE ON binance_market_snapshots
BEGIN SELECT RAISE(ABORT, 'binance_market_snapshots is append-only'); END;
CREATE TRIGGER IF NOT EXISTS binance_market_snapshots_no_delete
BEFORE DELETE ON binance_market_snapshots
BEGIN SELECT RAISE(ABORT, 'binance_market_snapshots is append-only'); END;
"""


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise BinanceRealtimeStoreError("snapshot is not canonical JSON") from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class BinanceRealtimeStore:
    """Durable public-market observations with idempotency and tamper evidence."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._require_schema()

    def _require_schema(self) -> None:
        try:
            tables = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        except sqlite3.DatabaseError as exc:
            raise BinanceRealtimeStoreError("snapshot store is unreadable") from exc
        if "binance_market_snapshots" not in tables:
            raise BinanceRealtimeStoreError("snapshot store schema is missing")

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _identity(snapshot: Mapping[str, Any]) -> tuple[str, int, int]:
        if not isinstance(snapshot, Mapping):
            raise BinanceRealtimeStoreError("snapshot must be a mapping")
        instrument_id = str(snapshot.get("instrument_id", "")).upper()
        try:
            observed_at_ms = int(snapshot["observed_at_ms"])
            event_time_ms = int(snapshot["event_time_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BinanceRealtimeStoreError("snapshot timestamps are invalid") from exc
        if (
            not instrument_id.endswith("USDT")
            or observed_at_ms < 0
            or event_time_ms < 0
            or event_time_ms > observed_at_ms
        ):
            raise BinanceRealtimeStoreError("snapshot identity is invalid")
        return instrument_id, observed_at_ms, event_time_ms

    def _decode(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BinanceRealtimeStoreError("snapshot row has invalid JSON") from exc
        if not isinstance(payload, dict) or _sha256(payload) != row["payload_sha256"]:
            raise BinanceRealtimeStoreError("snapshot payload hash mismatch")
        return payload

    def append_snapshot(self, snapshot: Mapping[str, Any]) -> bool:
        payload = dict(snapshot)
        instrument_id, observed_at_ms, event_time_ms = self._identity(payload)
        payload_json = _canonical(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT payload_sha256 FROM binance_market_snapshots "
                "WHERE instrument_id=? AND observed_at_ms=?",
                (instrument_id, observed_at_ms),
            ).fetchone()
            if existing is not None:
                if existing["payload_sha256"] == payload_sha256:
                    self._conn.rollback()
                    return False
                raise BinanceRealtimeStoreError(
                    "snapshot identity reused with different content"
                )
            previous = self._conn.execute(
                "SELECT row_hash FROM binance_market_snapshots ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = _GENESIS if previous is None else str(previous["row_hash"])
            row_hash = _sha256(
                {
                    "instrument_id": instrument_id,
                    "observed_at_ms": observed_at_ms,
                    "event_time_ms": event_time_ms,
                    "payload_sha256": payload_sha256,
                    "prev_hash": prev_hash,
                }
            )
            self._conn.execute(
                "INSERT INTO binance_market_snapshots"
                "(instrument_id,observed_at_ms,event_time_ms,payload_json,"
                "payload_sha256,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?)",
                (
                    instrument_id,
                    observed_at_ms,
                    event_time_ms,
                    payload_json,
                    payload_sha256,
                    prev_hash,
                    row_hash,
                ),
            )
            self._conn.commit()
            return True
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def latest(self, instrument_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM binance_market_snapshots WHERE instrument_id=? "
            "ORDER BY seq DESC LIMIT 1",
            (str(instrument_id).upper(),),
        ).fetchone()
        return None if row is None else self._decode(row)

    def recent_rows(self, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = list(
            self._conn.execute(
                "SELECT * FROM binance_market_snapshots ORDER BY seq DESC LIMIT ?",
                (limit,),
            )
        )
        return [self._decode(row) for row in reversed(rows)]

    def row_count(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM binance_market_snapshots"
            ).fetchone()[0]
        )

    def chain_valid(self) -> bool:
        previous = _GENESIS
        try:
            for row in self._conn.execute(
                "SELECT * FROM binance_market_snapshots ORDER BY seq"
            ):
                self._decode(row)
                if row["prev_hash"] != previous:
                    return False
                expected = _sha256(
                    {
                        "instrument_id": row["instrument_id"],
                        "observed_at_ms": int(row["observed_at_ms"]),
                        "event_time_ms": int(row["event_time_ms"]),
                        "payload_sha256": row["payload_sha256"],
                        "prev_hash": row["prev_hash"],
                    }
                )
                if row["row_hash"] != expected:
                    return False
                previous = str(row["row_hash"])
            return True
        except (BinanceRealtimeStoreError, sqlite3.DatabaseError, TypeError, ValueError):
            return False


__all__ = ["BinanceRealtimeStore", "BinanceRealtimeStoreError"]
