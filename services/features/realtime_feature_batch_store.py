"""Tamper-evident batch journal for realtime feature materialization."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any


class RealtimeFeatureBatchStoreError(RuntimeError):
    """The realtime feature batch journal is missing, corrupt, or conflicting."""


_GENESIS = ""
_SCHEMA = """
CREATE TABLE IF NOT EXISTS realtime_feature_batches (
  seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id         TEXT NOT NULL UNIQUE,
  observed_at_ms   INTEGER NOT NULL,
  payload_json     TEXT NOT NULL,
  payload_sha256   TEXT NOT NULL,
  prev_hash        TEXT NOT NULL,
  row_hash         TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS realtime_feature_batches_no_update
BEFORE UPDATE ON realtime_feature_batches
BEGIN SELECT RAISE(ABORT, 'realtime_feature_batches is append-only'); END;
CREATE TRIGGER IF NOT EXISTS realtime_feature_batches_no_delete
BEFORE DELETE ON realtime_feature_batches
BEGIN SELECT RAISE(ABORT, 'realtime_feature_batches is append-only'); END;
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
        raise RealtimeFeatureBatchStoreError(
            "batch payload is not canonical JSON"
        ) from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class RealtimeFeatureBatchStore:
    """Append-only chain binding source snapshots to a materialized batch."""

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
            row = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='realtime_feature_batches'"
            ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise RealtimeFeatureBatchStoreError("batch store is unreadable") from exc
        if row is None:
            raise RealtimeFeatureBatchStoreError("batch store schema is missing")

    def close(self) -> None:
        self._conn.close()

    def append(self, payload: Mapping[str, Any]) -> bool:
        if not isinstance(payload, Mapping):
            raise RealtimeFeatureBatchStoreError("batch must be a mapping")
        batch = dict(payload)
        batch_id = str(batch.get("batch_id", "")).strip()
        try:
            observed_at_ms = int(batch["observed_at_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RealtimeFeatureBatchStoreError(
                "batch timestamp is invalid"
            ) from exc
        if not batch_id or observed_at_ms < 0:
            raise RealtimeFeatureBatchStoreError("batch identity is invalid")
        payload_json = _canonical(batch)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT payload_sha256 FROM realtime_feature_batches "
                "WHERE batch_id=?",
                (batch_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_sha256"] == payload_sha256:
                    self._conn.rollback()
                    return False
                raise RealtimeFeatureBatchStoreError(
                    "batch_id reused with different content"
                )
            previous = self._conn.execute(
                "SELECT row_hash FROM realtime_feature_batches "
                "ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = _GENESIS if previous is None else str(previous["row_hash"])
            row_hash = _sha256(
                {
                    "batch_id": batch_id,
                    "observed_at_ms": observed_at_ms,
                    "payload_sha256": payload_sha256,
                    "prev_hash": prev_hash,
                }
            )
            self._conn.execute(
                "INSERT INTO realtime_feature_batches"
                "(batch_id,observed_at_ms,payload_json,payload_sha256,"
                "prev_hash,row_hash) VALUES (?,?,?,?,?,?)",
                (
                    batch_id,
                    observed_at_ms,
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

    def row_count(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM realtime_feature_batches"
            ).fetchone()[0]
        )

    def latest(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM realtime_feature_batches ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return None if row is None else self._decode(row)

    def latest_with_authority(self) -> tuple[str, dict[str, Any] | None]:
        """Freeze the verified authority and latest checkpoint in one view."""
        try:
            self._conn.execute("BEGIN")
            authority = self.authority_hash()
            latest = self.latest()
            self._conn.commit()
            return authority, latest
        except RealtimeFeatureBatchStoreError:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        except sqlite3.DatabaseError as exc:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise RealtimeFeatureBatchStoreError(
                "batch journal snapshot is unavailable"
            ) from exc

    def _decode(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RealtimeFeatureBatchStoreError("batch JSON is invalid") from exc
        if not isinstance(payload, dict) or _sha256(payload) != row["payload_sha256"]:
            raise RealtimeFeatureBatchStoreError("batch payload hash mismatch")
        return payload

    def chain_valid(self) -> bool:
        try:
            self.authority_hash()
            return True
        except RealtimeFeatureBatchStoreError:
            return False

    def authority_hash(self) -> str:
        """Return a hash only after verifying the append-only frontier."""
        previous = _GENESIS
        row_count = 0
        try:
            for row in self._conn.execute(
                "SELECT * FROM realtime_feature_batches ORDER BY seq"
            ):
                self._decode(row)
                if row["prev_hash"] != previous:
                    raise RealtimeFeatureBatchStoreError(
                        "batch journal authority is invalid"
                    )
                expected = _sha256(
                    {
                        "batch_id": row["batch_id"],
                        "observed_at_ms": int(row["observed_at_ms"]),
                        "payload_sha256": row["payload_sha256"],
                        "prev_hash": row["prev_hash"],
                    }
                )
                if row["row_hash"] != expected:
                    raise RealtimeFeatureBatchStoreError(
                        "batch journal authority is invalid"
                    )
                previous = str(row["row_hash"])
                row_count += 1
            return _sha256(
                {
                    "schema_version": 1,
                    "row_count": row_count,
                    "chain_head": previous,
                }
            )
        except RealtimeFeatureBatchStoreError as exc:
            if str(exc) == "batch journal authority is invalid":
                raise
            raise RealtimeFeatureBatchStoreError(
                "batch journal authority is invalid"
            ) from exc
        except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
            raise RealtimeFeatureBatchStoreError(
                "batch journal authority is invalid"
            ) from exc


__all__ = ["RealtimeFeatureBatchStore", "RealtimeFeatureBatchStoreError"]
