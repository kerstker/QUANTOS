"""Append-only persistence for public-mark paper-trading snapshots.

This store is deliberately separate from the paper order ledger.  The order
ledger records an idempotent rebalance; this store records the mark-to-market
observations that make a paper account observable between rebalances.  It is
not a broker account, does not authorize an order, and accepts only snapshots
whose caller has already declared a public-data, paper-only provenance.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any


class PaperRealtimeStoreError(RuntimeError):
    """The paper mark store is missing, corrupt, or conflicting."""


_GENESIS = ""
_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_mark_snapshots (
  seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  alpha_id         TEXT NOT NULL,
  observed_at      INTEGER NOT NULL,
  payload_json     TEXT NOT NULL,
  payload_sha256   TEXT NOT NULL,
  prev_hash        TEXT NOT NULL,
  row_hash         TEXT NOT NULL,
  UNIQUE(alpha_id, observed_at)
);
CREATE TRIGGER IF NOT EXISTS paper_mark_snapshots_no_update
BEFORE UPDATE ON paper_mark_snapshots
BEGIN SELECT RAISE(ABORT, 'paper_mark_snapshots is append-only'); END;
CREATE TRIGGER IF NOT EXISTS paper_mark_snapshots_no_delete
BEFORE DELETE ON paper_mark_snapshots
BEGIN SELECT RAISE(ABORT, 'paper_mark_snapshots is append-only'); END;
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
        raise PaperRealtimeStoreError("paper mark payload is not canonical JSON") from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class PaperRealtimeStore:
    """Durable, hash-chained public-mark observations."""

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
            names = {
                row["name"]
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        except sqlite3.DatabaseError as exc:
            raise PaperRealtimeStoreError("paper mark store is unreadable") from exc
        if "paper_mark_snapshots" not in names:
            raise PaperRealtimeStoreError("paper mark store schema is missing")

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _stored_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot, Mapping):
            raise PaperRealtimeStoreError("paper mark snapshot must be a mapping")
        payload = dict(snapshot)
        # Publication time is not the observation identity.  A retry of the
        # same provider timestamp must be idempotent.
        payload.pop("generated_at", None)
        return payload

    def _row_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PaperRealtimeStoreError("paper mark row has invalid JSON") from exc
        if not isinstance(payload, dict) or _sha256(payload) != row["payload_sha256"]:
            raise PaperRealtimeStoreError("paper mark payload hash mismatch")
        return payload

    def append_snapshot(self, snapshot: Mapping[str, Any]) -> bool:
        """Append one observation; an exact retry returns ``False``."""
        payload = self._stored_payload(snapshot)
        alpha_id = str(payload.get("alpha_id", ""))
        try:
            observed_at = int(payload["observed_at"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PaperRealtimeStoreError("paper mark observation timestamp is invalid") from exc
        if not alpha_id or observed_at < 0:
            raise PaperRealtimeStoreError("paper mark observation identity is invalid")
        payload_json = _canonical(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT payload_sha256 FROM paper_mark_snapshots "
                "WHERE alpha_id=? AND observed_at=?",
                (alpha_id, observed_at),
            ).fetchone()
            if existing is not None:
                if existing["payload_sha256"] == payload_sha256:
                    self._conn.rollback()
                    return False
                raise PaperRealtimeStoreError(
                    "paper mark observation identity reused with different content"
                )
            previous = self._conn.execute(
                "SELECT row_hash FROM paper_mark_snapshots ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = _GENESIS if previous is None else str(previous["row_hash"])
            row_values = {
                "alpha_id": alpha_id,
                "observed_at": observed_at,
                "payload_sha256": payload_sha256,
                "prev_hash": prev_hash,
            }
            row_hash = _sha256(row_values)
            self._conn.execute(
                "INSERT INTO paper_mark_snapshots"
                "(alpha_id,observed_at,payload_json,payload_sha256,prev_hash,row_hash)"
                " VALUES (?,?,?,?,?,?)",
                (alpha_id, observed_at, payload_json, payload_sha256, prev_hash, row_hash),
            )
            self._conn.commit()
            return True
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def latest(self, alpha_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM paper_mark_snapshots WHERE alpha_id=? "
            "ORDER BY seq DESC LIMIT 1",
            (str(alpha_id),),
        ).fetchone()
        return None if row is None else self._row_payload(row)

    def rows(self) -> list[dict[str, Any]]:
        return [
            self._row_payload(row)
            for row in self._conn.execute(
                "SELECT * FROM paper_mark_snapshots ORDER BY seq"
            )
        ]

    def recent_rows(self, limit: int = 96) -> list[dict[str, Any]]:
        """Return a bounded chronological window for read-only monitoring."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = list(
            self._conn.execute(
                "SELECT * FROM paper_mark_snapshots ORDER BY seq DESC LIMIT ?",
                (limit,),
            )
        )
        return [self._row_payload(row) for row in reversed(rows)]

    def chain_valid(self) -> bool:
        previous = _GENESIS
        try:
            for row in self._conn.execute(
                "SELECT * FROM paper_mark_snapshots ORDER BY seq"
            ):
                self._row_payload(row)
                if row["prev_hash"] != previous:
                    return False
                values = {
                    "alpha_id": row["alpha_id"],
                    "observed_at": int(row["observed_at"]),
                    "payload_sha256": row["payload_sha256"],
                    "prev_hash": row["prev_hash"],
                }
                if row["row_hash"] != _sha256(values):
                    return False
                previous = row["row_hash"]
            return True
        except (PaperRealtimeStoreError, sqlite3.DatabaseError, TypeError, ValueError):
            return False


__all__ = ["PaperRealtimeStore", "PaperRealtimeStoreError"]
