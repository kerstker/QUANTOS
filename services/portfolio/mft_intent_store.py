"""Append-only SQLite outbox for L2 Testnet portfolio intent projections."""
from __future__ import annotations

import json
import os
import sqlite3
import threading

from quantos.contracts.hashing import stable_hash
from services.portfolio.mft_intent import (
    MftIntentError,
    MftIntentProjection,
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS mft_intent_outbox (
  seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  cycle_id         TEXT NOT NULL UNIQUE,
  status           TEXT NOT NULL,
  projection_hash  TEXT NOT NULL UNIQUE,
  recorded_at_ms   INTEGER NOT NULL,
  payload_json     TEXT NOT NULL,
  prev_hash        TEXT NOT NULL,
  row_hash         TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS mft_intent_outbox_no_update
BEFORE UPDATE ON mft_intent_outbox
BEGIN SELECT RAISE(ABORT, 'mft_intent_outbox is append-only'); END;
CREATE TRIGGER IF NOT EXISTS mft_intent_outbox_no_delete
BEFORE DELETE ON mft_intent_outbox
BEGIN SELECT RAISE(ABORT, 'mft_intent_outbox is append-only'); END;
"""


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MftIntentError("outbox payload is not canonical JSON") from exc


class SqliteMftIntentOutbox:
    def __init__(self, path: str, *, create: bool = False) -> None:
        if not isinstance(path, str) or not path:
            raise MftIntentError("outbox path must be non-empty")
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise MftIntentError(f"outbox unavailable: {path}")
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        try:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout=5000")
                if create:
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    self._conn.execute("PRAGMA synchronous=FULL")
                    self._conn.executescript(_SCHEMA)
                    self._conn.commit()
                self._verify_locked()
        except Exception:
            self._conn.close()
            raise

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict:
        try:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict) or _canonical(payload) != row["payload_json"]:
                raise ValueError("payload is not canonical")
            if payload.get("projection_hash") != row["projection_hash"]:
                raise ValueError("projection hash column mismatch")
            if stable_hash({key: value for key, value in payload.items() if key != "projection_hash"}) != row["projection_hash"]:
                raise ValueError("projection hash mismatch")
            if (
                payload.get("cycle_id") != row["cycle_id"]
                or payload.get("status") != row["status"]
                or payload.get("recorded_at_ms") != row["recorded_at_ms"]
            ):
                raise ValueError("outbox index columns mismatch")
            return payload
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MftIntentError("outbox row is malformed") from exc

    def _verify_locked(self) -> str:
        try:
            exists = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mft_intent_outbox'"
            ).fetchone()
            if exists is None:
                raise MftIntentError("outbox schema is missing")
            previous = ""
            for row in self._conn.execute("SELECT * FROM mft_intent_outbox ORDER BY seq"):
                self._decode(row)
                if row["prev_hash"] != previous:
                    raise MftIntentError("outbox chain link mismatch")
                expected = stable_hash(
                    {"projection_hash": row["projection_hash"], "prev_hash": previous}
                )
                if row["row_hash"] != expected:
                    raise MftIntentError("outbox row hash mismatch")
                previous = expected
            return previous
        except sqlite3.DatabaseError as exc:
            raise MftIntentError("outbox is unreadable") from exc

    def append(self, projection: MftIntentProjection) -> dict:
        if not isinstance(projection, MftIntentProjection):
            raise TypeError("projection must be MftIntentProjection")
        payload = projection.as_dict()
        payload_json = _canonical(payload)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                head = self._verify_locked()
                existing = self._conn.execute(
                    "SELECT * FROM mft_intent_outbox WHERE cycle_id=?",
                    (projection.cycle_id,),
                ).fetchone()
                if existing is not None:
                    decoded = self._decode(existing)
                    if decoded != payload:
                        raise MftIntentError(
                            "outbox cycle identity reused with different content"
                        )
                    self._conn.rollback()
                    return decoded
                row_hash = stable_hash(
                    {"projection_hash": projection.projection_hash, "prev_hash": head}
                )
                self._conn.execute(
                    "INSERT INTO mft_intent_outbox"
                    "(cycle_id,status,projection_hash,recorded_at_ms,payload_json,prev_hash,row_hash) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        projection.cycle_id,
                        projection.status,
                        projection.projection_hash,
                        projection.recorded_at_ms,
                        payload_json,
                        head,
                        row_hash,
                    ),
                )
                self._conn.commit()
                return payload
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def get(self, cycle_id: str) -> dict | None:
        with self._lock:
            self._verify_locked()
            row = self._conn.execute(
                "SELECT * FROM mft_intent_outbox WHERE cycle_id=?", (str(cycle_id),)
            ).fetchone()
            return None if row is None else self._decode(row)

    def ready(self) -> tuple[dict, ...]:
        with self._lock:
            self._verify_locked()
            rows = self._conn.execute(
                "SELECT * FROM mft_intent_outbox WHERE status='READY' ORDER BY seq"
            ).fetchall()
            return tuple(self._decode(row) for row in rows)

    def row_count(self) -> int:
        with self._lock:
            self._verify_locked()
            return int(self._conn.execute("SELECT COUNT(*) FROM mft_intent_outbox").fetchone()[0])

    def chain_valid(self) -> bool:
        with self._lock:
            try:
                self._verify_locked()
                return True
            except MftIntentError:
                return False


__all__ = ["SqliteMftIntentOutbox"]
