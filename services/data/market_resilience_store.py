"""Append-only authority for outcome-free market-resilience snapshots."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


class MarketResilienceStoreError(RuntimeError):
    """A derived snapshot cannot be admitted without overstating evidence."""


SQLITE_BUSY_TIMEOUT_SECONDS = 30.0
MAX_FUTURE_SKEW_MS = 30_000
_GENESIS = ""
_IDENTIFIER = re.compile(r"[a-z0-9_]{2,64}")
_INSTRUMENT = re.compile(r"[A-Z0-9-]{2,48}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_OUTCOME_FIELDS = frozenset(
    {
        "alpha",
        "forward_return",
        "future_return",
        "label",
        "outcome",
        "pnl",
        "return",
        "sharpe",
        "target",
        "trial",
        "trial_id",
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_resilience_snapshots (
  seq                    INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id            TEXT NOT NULL UNIQUE,
  venue                  TEXT NOT NULL,
  instrument_id          TEXT NOT NULL,
  segment_id             TEXT NOT NULL,
  source_event_time_ms    INTEGER NOT NULL,
  received_at_ms          INTEGER NOT NULL,
  processed_at_ms         INTEGER NOT NULL,
  raw_head_seq            INTEGER NOT NULL,
  raw_head_hash           TEXT NOT NULL,
  payload_json            TEXT NOT NULL,
  payload_sha256          TEXT NOT NULL,
  prev_hash               TEXT NOT NULL,
  row_hash                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_market_resilience_instrument_time
ON market_resilience_snapshots(venue, instrument_id, processed_at_ms, seq);
CREATE TRIGGER IF NOT EXISTS market_resilience_snapshots_no_update
BEFORE UPDATE ON market_resilience_snapshots
BEGIN SELECT RAISE(ABORT, 'market_resilience_snapshots is append-only'); END;
CREATE TRIGGER IF NOT EXISTS market_resilience_snapshots_no_delete
BEFORE DELETE ON market_resilience_snapshots
BEGIN SELECT RAISE(ABORT, 'market_resilience_snapshots is append-only'); END;
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
        raise MarketResilienceStoreError("snapshot is not canonical JSON") from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _has_outcome_field(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if (
                normalized in _OUTCOME_FIELDS
                or normalized.endswith("_label")
                or normalized.endswith("_outcome")
                or normalized.endswith("_return")
            ):
                return True
            if _has_outcome_field(item):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_has_outcome_field(item) for item in value)
    return False


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise MarketResilienceStoreError(f"{name} is invalid")
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise MarketResilienceStoreError(f"{name} is invalid") from exc
    if parsed < 0:
        raise MarketResilienceStoreError(f"{name} is invalid")
    return parsed


class MarketResilienceStore:
    """Durable, batch-atomic hash chain with no mutation or outcome columns."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        try:
            if create:
                self._conn = sqlite3.connect(path, timeout=SQLITE_BUSY_TIMEOUT_SECONDS)
            else:
                uri = f"{Path(path).resolve().as_uri()}?mode=ro"
                self._conn = sqlite3.connect(
                    uri,
                    uri=True,
                    timeout=SQLITE_BUSY_TIMEOUT_SECONDS,
                )
        except sqlite3.Error as exc:
            raise MarketResilienceStoreError("snapshot store is unreadable") from exc
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
            raise MarketResilienceStoreError("snapshot store is unreadable") from exc
        if "market_resilience_snapshots" not in tables:
            raise MarketResilienceStoreError("snapshot store schema is missing")

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _validated(snapshot: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(snapshot, Mapping):
            raise MarketResilienceStoreError("snapshot must be a mapping")
        item = dict(snapshot)
        if _has_outcome_field(item):
            raise MarketResilienceStoreError("snapshot contains an outcome field")
        snapshot_id = item.get("snapshot_id")
        venue = item.get("venue")
        instrument_id = item.get("instrument_id")
        segment_id = item.get("segment_id")
        if not isinstance(snapshot_id, str) or _SHA256.fullmatch(snapshot_id) is None:
            raise MarketResilienceStoreError("snapshot_id is invalid")
        content = dict(item)
        content.pop("snapshot_id")
        if _sha256(content) != snapshot_id:
            raise MarketResilienceStoreError(
                "snapshot_id does not match snapshot content"
            )
        if not isinstance(venue, str) or _IDENTIFIER.fullmatch(venue) is None:
            raise MarketResilienceStoreError("venue is invalid")
        if not isinstance(instrument_id, str) or _INSTRUMENT.fullmatch(instrument_id) is None:
            raise MarketResilienceStoreError("instrument_id is invalid")
        if not isinstance(segment_id, str) or _SHA256.fullmatch(segment_id) is None:
            raise MarketResilienceStoreError("segment_id is invalid")
        event_time = _integer(item.get("source_event_time_ms"), "source_event_time_ms")
        received_at = _integer(item.get("received_at_ms"), "received_at_ms")
        processed_at = _integer(item.get("processed_at_ms"), "processed_at_ms")
        if event_time > received_at + MAX_FUTURE_SKEW_MS or received_at > processed_at:
            raise MarketResilienceStoreError("snapshot clocks are invalid")
        raw_head = item.get("raw_authority_head")
        if not isinstance(raw_head, Mapping):
            raise MarketResilienceStoreError("raw authority head is invalid")
        raw_head_seq = _integer(raw_head.get("head_seq"), "raw authority head seq")
        raw_head_hash = raw_head.get("head_hash")
        if raw_head_seq == 0 or not isinstance(raw_head_hash, str) or _SHA256.fullmatch(raw_head_hash) is None:
            raise MarketResilienceStoreError("raw authority head is invalid")
        if item.get("cancel_rate_inferred") is not False:
            raise MarketResilienceStoreError("cancel_rate_inferred must be false")
        return {
            "snapshot_id": snapshot_id,
            "venue": venue,
            "instrument_id": instrument_id,
            "segment_id": segment_id,
            "source_event_time_ms": event_time,
            "received_at_ms": received_at,
            "processed_at_ms": processed_at,
            "raw_head_seq": raw_head_seq,
            "raw_head_hash": raw_head_hash,
            "payload": item,
        }

    def append_snapshots(
        self,
        snapshots: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(snapshots, Sequence) or isinstance(snapshots, (str, bytes)):
            raise MarketResilienceStoreError("snapshots must be a sequence")
        validated = [self._validated(snapshot) for snapshot in snapshots]
        if not validated:
            return self.head()
        snapshot_ids = [str(item["snapshot_id"]) for item in validated]
        if len(set(snapshot_ids)) != len(snapshot_ids):
            raise MarketResilienceStoreError("batch reuses a snapshot_id")
        inserted = 0
        duplicates = 0
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            head = self._conn.execute(
                "SELECT seq,row_hash FROM market_resilience_snapshots "
                "ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            previous = _GENESIS if head is None else str(head["row_hash"])
            last_seq = 0 if head is None else int(head["seq"])
            for item in validated:
                payload_json = _canonical(item["payload"])
                payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
                existing = self._conn.execute(
                    "SELECT payload_sha256 FROM market_resilience_snapshots "
                    "WHERE snapshot_id=?",
                    (item["snapshot_id"],),
                ).fetchone()
                if existing is not None:
                    if str(existing["payload_sha256"]) != payload_sha256:
                        raise MarketResilienceStoreError(
                            "snapshot_id reused with different content"
                        )
                    duplicates += 1
                    continue
                row_hash = _sha256(
                    {
                        "snapshot_id": item["snapshot_id"],
                        "venue": item["venue"],
                        "instrument_id": item["instrument_id"],
                        "segment_id": item["segment_id"],
                        "source_event_time_ms": item["source_event_time_ms"],
                        "received_at_ms": item["received_at_ms"],
                        "processed_at_ms": item["processed_at_ms"],
                        "raw_head_seq": item["raw_head_seq"],
                        "raw_head_hash": item["raw_head_hash"],
                        "payload_sha256": payload_sha256,
                        "prev_hash": previous,
                    }
                )
                cursor = self._conn.execute(
                    "INSERT INTO market_resilience_snapshots"
                    "(snapshot_id,venue,instrument_id,segment_id,source_event_time_ms,"
                    "received_at_ms,processed_at_ms,raw_head_seq,raw_head_hash,"
                    "payload_json,payload_sha256,prev_hash,row_hash) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item["snapshot_id"],
                        item["venue"],
                        item["instrument_id"],
                        item["segment_id"],
                        item["source_event_time_ms"],
                        item["received_at_ms"],
                        item["processed_at_ms"],
                        item["raw_head_seq"],
                        item["raw_head_hash"],
                        payload_json,
                        payload_sha256,
                        previous,
                        row_hash,
                    ),
                )
                last_seq = int(cursor.lastrowid)
                previous = row_hash
                inserted += 1
            self._conn.commit()
            return {
                "inserted": inserted,
                "duplicates": duplicates,
                "head_seq": last_seq,
                "head_hash": previous,
            }
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def head(self) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT seq,row_hash FROM market_resilience_snapshots "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return {
            "inserted": 0,
            "duplicates": 0,
            "head_seq": 0 if row is None else int(row["seq"]),
            "head_hash": _GENESIS if row is None else str(row["row_hash"]),
        }

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError as exc:
            raise MarketResilienceStoreError("snapshot payload is invalid JSON") from exc
        if not isinstance(payload, dict) or _sha256(payload) != row["payload_sha256"]:
            raise MarketResilienceStoreError("snapshot payload hash mismatch")
        return payload

    def recent_snapshots(self, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        try:
            rows = list(
                self._conn.execute(
                    "SELECT * FROM market_resilience_snapshots "
                    "ORDER BY seq DESC LIMIT ?",
                    (limit,),
                )
            )
            return [self._decode_row(row) for row in reversed(rows)]
        except sqlite3.DatabaseError as exc:
            raise MarketResilienceStoreError("snapshot store is unreadable") from exc

    def verify_chain_from(
        self,
        *,
        checkpoint_seq: int = 0,
        checkpoint_row_hash: str = _GENESIS,
    ) -> dict[str, Any]:
        checkpoint = _integer(checkpoint_seq, "checkpoint_seq")
        if checkpoint == 0:
            if checkpoint_row_hash != _GENESIS:
                raise ValueError("genesis checkpoint hash is invalid")
        elif not isinstance(checkpoint_row_hash, str) or _SHA256.fullmatch(checkpoint_row_hash) is None:
            raise ValueError("checkpoint_row_hash is invalid")
        try:
            if checkpoint:
                anchor = self._conn.execute(
                    "SELECT row_hash FROM market_resilience_snapshots WHERE seq=?",
                    (checkpoint,),
                ).fetchone()
                if anchor is None or str(anchor["row_hash"]) != checkpoint_row_hash:
                    raise MarketResilienceStoreError("snapshot chain checkpoint mismatch")
            previous = checkpoint_row_hash
            last_seq = checkpoint
            verified = 0
            for row in self._conn.execute(
                "SELECT * FROM market_resilience_snapshots WHERE seq>? ORDER BY seq",
                (checkpoint,),
            ):
                payload = json.loads(str(row["payload_json"]))
                if not isinstance(payload, dict) or _sha256(payload) != row["payload_sha256"]:
                    raise MarketResilienceStoreError("snapshot payload hash mismatch")
                if str(row["prev_hash"]) != previous:
                    raise MarketResilienceStoreError("snapshot chain link mismatch")
                expected = _sha256(
                    {
                        "snapshot_id": row["snapshot_id"],
                        "venue": row["venue"],
                        "instrument_id": row["instrument_id"],
                        "segment_id": row["segment_id"],
                        "source_event_time_ms": int(row["source_event_time_ms"]),
                        "received_at_ms": int(row["received_at_ms"]),
                        "processed_at_ms": int(row["processed_at_ms"]),
                        "raw_head_seq": int(row["raw_head_seq"]),
                        "raw_head_hash": row["raw_head_hash"],
                        "payload_sha256": row["payload_sha256"],
                        "prev_hash": row["prev_hash"],
                    }
                )
                if str(row["row_hash"]) != expected:
                    raise MarketResilienceStoreError("snapshot row hash mismatch")
                previous = str(row["row_hash"])
                last_seq = int(row["seq"])
                verified += 1
            return {
                "valid": True,
                "anchor_seq": checkpoint,
                "verified_events": verified,
                "verified_seq": last_seq,
                "verified_row_hash": previous,
            }
        except (json.JSONDecodeError, sqlite3.DatabaseError, MarketResilienceStoreError) as exc:
            return {
                "valid": False,
                "anchor_seq": checkpoint,
                "verified_events": 0,
                "verified_seq": checkpoint,
                "verified_row_hash": checkpoint_row_hash,
                "error": str(exc),
            }

    def verify_chain(self) -> dict[str, Any]:
        return self.verify_chain_from()


__all__ = [
    "MarketResilienceStore",
    "MarketResilienceStoreError",
]
