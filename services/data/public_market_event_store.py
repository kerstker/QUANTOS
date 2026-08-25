"""Append-only event authority for credential-free public market streams.

The store keeps exchange event time, local receive time and local persistence
time as separate columns.  It stores source messages, not research labels, and
grants no execution or lifecycle permission.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
import re
import sqlite3
from typing import Any


class PublicMarketEventStoreError(RuntimeError):
    """A public event could not be admitted without overstating provenance."""


MAX_FUTURE_SKEW_MS = 30_000
SQLITE_BUSY_TIMEOUT_SECONDS = 30.0
_GENESIS = ""
_IDENTIFIER = re.compile(r"[a-z0-9_]{2,64}")
_INSTRUMENT = re.compile(r"[A-Z0-9-]{2,48}")
_EVENT_ID = re.compile(r"[0-9a-f]{64}")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS public_market_events (
  seq               INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id          TEXT NOT NULL UNIQUE,
  venue             TEXT NOT NULL,
  channel           TEXT NOT NULL,
  instrument_id     TEXT NOT NULL,
  event_time_ms     INTEGER NOT NULL,
  received_at_ms    INTEGER NOT NULL,
  processed_at_ms   INTEGER NOT NULL,
  source_sequence   TEXT NOT NULL,
  payload_json      TEXT NOT NULL,
  payload_sha256    TEXT NOT NULL,
  prev_hash         TEXT NOT NULL,
  row_hash          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_public_market_event_time
ON public_market_events(venue, instrument_id, channel, event_time_ms);
CREATE TRIGGER IF NOT EXISTS public_market_events_no_update
BEFORE UPDATE ON public_market_events
BEGIN SELECT RAISE(ABORT, 'public_market_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS public_market_events_no_delete
BEFORE DELETE ON public_market_events
BEGIN SELECT RAISE(ABORT, 'public_market_events is append-only'); END;
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
        raise PublicMarketEventStoreError("event is not canonical JSON") from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class PublicMarketEventStore:
    """Durable raw-message authority with batch-atomic hash chaining."""

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
            raise PublicMarketEventStoreError("event store is unreadable") from exc
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
            raise PublicMarketEventStoreError("event store is unreadable") from exc
        if "public_market_events" not in tables:
            raise PublicMarketEventStoreError("event store schema is missing")

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _validated(event: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(event, Mapping):
            raise PublicMarketEventStoreError("event must be a mapping")
        item = dict(event)
        venue = item.get("venue")
        channel = item.get("channel")
        instrument_id = item.get("instrument_id")
        event_id = item.get("event_id")
        source_sequence = item.get("source_sequence")
        payload = item.get("payload")
        if not isinstance(venue, str) or _IDENTIFIER.fullmatch(venue) is None:
            raise PublicMarketEventStoreError("venue is invalid")
        if not isinstance(channel, str) or _IDENTIFIER.fullmatch(channel) is None:
            raise PublicMarketEventStoreError("channel is invalid")
        if (
            not isinstance(instrument_id, str)
            or _INSTRUMENT.fullmatch(instrument_id) is None
        ):
            raise PublicMarketEventStoreError("instrument_id is invalid")
        if not isinstance(event_id, str) or _EVENT_ID.fullmatch(event_id) is None:
            raise PublicMarketEventStoreError("event_id is invalid")
        if not isinstance(source_sequence, str) or not source_sequence.strip():
            raise PublicMarketEventStoreError("source_sequence is invalid")
        if not isinstance(payload, Mapping):
            raise PublicMarketEventStoreError("payload must be a mapping")
        try:
            raw_clocks = (
                item["event_time_ms"],
                item["received_at_ms"],
                item["processed_at_ms"],
            )
            if any(isinstance(value, bool) for value in raw_clocks):
                raise TypeError
            event_time_ms, received_at_ms, processed_at_ms = map(int, raw_clocks)
        except (KeyError, TypeError, ValueError) as exc:
            raise PublicMarketEventStoreError("event clocks are invalid") from exc
        if (
            event_time_ms < 0
            or received_at_ms < 0
            or processed_at_ms < 0
            or event_time_ms > received_at_ms + MAX_FUTURE_SKEW_MS
            or received_at_ms > processed_at_ms
        ):
            raise PublicMarketEventStoreError("event clocks are invalid")
        return {
            "event_id": event_id,
            "venue": venue,
            "channel": channel,
            "instrument_id": instrument_id,
            "event_time_ms": event_time_ms,
            "received_at_ms": received_at_ms,
            "processed_at_ms": processed_at_ms,
            "source_sequence": source_sequence,
            "payload": dict(payload),
        }

    def append_events(self, events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
            raise PublicMarketEventStoreError("events must be a sequence")
        validated = [self._validated(event) for event in events]
        if not validated:
            return self.head()
        event_ids = [str(item["event_id"]) for item in validated]
        if len(set(event_ids)) != len(event_ids):
            raise PublicMarketEventStoreError("batch reuses an event_id")
        inserted = 0
        duplicate = 0
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            head = self._conn.execute(
                "SELECT seq,row_hash FROM public_market_events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            previous = _GENESIS if head is None else str(head["row_hash"])
            last_seq = 0 if head is None else int(head["seq"])
            for item in validated:
                payload_json = _canonical(item["payload"])
                payload_sha256 = hashlib.sha256(
                    payload_json.encode("utf-8")
                ).hexdigest()
                existing = self._conn.execute(
                    "SELECT payload_sha256,venue,channel,instrument_id,event_time_ms,"
                    "received_at_ms,processed_at_ms,source_sequence "
                    "FROM public_market_events WHERE event_id=?",
                    (item["event_id"],),
                ).fetchone()
                identity = (
                    item["venue"],
                    item["channel"],
                    item["instrument_id"],
                    item["event_time_ms"],
                    item["received_at_ms"],
                    item["processed_at_ms"],
                    item["source_sequence"],
                )
                if existing is not None:
                    existing_identity = tuple(
                        existing[name]
                        for name in (
                            "venue",
                            "channel",
                            "instrument_id",
                            "event_time_ms",
                            "received_at_ms",
                            "processed_at_ms",
                            "source_sequence",
                        )
                    )
                    if (
                        str(existing["payload_sha256"]) != payload_sha256
                        or existing_identity != identity
                    ):
                        raise PublicMarketEventStoreError(
                            "event_id reused with different content"
                        )
                    duplicate += 1
                    continue
                row_hash = _sha256(
                    {
                        "event_id": item["event_id"],
                        "venue": item["venue"],
                        "channel": item["channel"],
                        "instrument_id": item["instrument_id"],
                        "event_time_ms": item["event_time_ms"],
                        "received_at_ms": item["received_at_ms"],
                        "processed_at_ms": item["processed_at_ms"],
                        "source_sequence": item["source_sequence"],
                        "payload_sha256": payload_sha256,
                        "prev_hash": previous,
                    }
                )
                cursor = self._conn.execute(
                    "INSERT INTO public_market_events"
                    "(event_id,venue,channel,instrument_id,event_time_ms,"
                    "received_at_ms,processed_at_ms,source_sequence,payload_json,"
                    "payload_sha256,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item["event_id"],
                        item["venue"],
                        item["channel"],
                        item["instrument_id"],
                        item["event_time_ms"],
                        item["received_at_ms"],
                        item["processed_at_ms"],
                        item["source_sequence"],
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
                "duplicates": duplicate,
                "head_seq": last_seq,
                "head_hash": previous,
            }
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def head(self) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT seq,row_hash FROM public_market_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return {
            "inserted": 0,
            "duplicates": 0,
            "head_seq": 0 if row is None else int(row["seq"]),
            "head_hash": _GENESIS if row is None else str(row["row_hash"]),
        }

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = list(
            self._conn.execute(
                "SELECT * FROM public_market_events ORDER BY seq DESC LIMIT ?",
                (limit,),
            )
        )
        output = []
        for row in reversed(rows):
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict) or _sha256(payload) != row["payload_sha256"]:
                raise PublicMarketEventStoreError("event payload hash mismatch")
            output.append(
                {
                    "seq": int(row["seq"]),
                    "event_id": str(row["event_id"]),
                    "venue": str(row["venue"]),
                    "channel": str(row["channel"]),
                    "instrument_id": str(row["instrument_id"]),
                    "event_time_ms": int(row["event_time_ms"]),
                    "received_at_ms": int(row["received_at_ms"]),
                    "processed_at_ms": int(row["processed_at_ms"]),
                    "source_sequence": str(row["source_sequence"]),
                    "payload": payload,
                }
            )
        return output

    def verify_chain_from(
        self,
        *,
        checkpoint_seq: int = 0,
        checkpoint_row_hash: str = _GENESIS,
    ) -> dict[str, Any]:
        if (
            isinstance(checkpoint_seq, bool)
            or not isinstance(checkpoint_seq, int)
            or checkpoint_seq < 0
        ):
            raise ValueError("checkpoint_seq must be a non-negative integer")
        if checkpoint_seq == 0:
            if checkpoint_row_hash != _GENESIS:
                raise ValueError("genesis checkpoint hash is invalid")
        elif _EVENT_ID.fullmatch(checkpoint_row_hash) is None:
            raise ValueError("checkpoint_row_hash is invalid")
        try:
            if checkpoint_seq:
                anchor = self._conn.execute(
                    "SELECT row_hash FROM public_market_events WHERE seq=?",
                    (checkpoint_seq,),
                ).fetchone()
                if anchor is None or str(anchor["row_hash"]) != checkpoint_row_hash:
                    raise PublicMarketEventStoreError("chain checkpoint anchor mismatch")
            previous = checkpoint_row_hash
            last_seq = checkpoint_seq
            verified = 0
            for row in self._conn.execute(
                "SELECT * FROM public_market_events WHERE seq>? ORDER BY seq",
                (checkpoint_seq,),
            ):
                payload = json.loads(str(row["payload_json"]))
                if not isinstance(payload, dict) or _sha256(payload) != row["payload_sha256"]:
                    raise PublicMarketEventStoreError("event payload hash mismatch")
                if str(row["prev_hash"]) != previous:
                    raise PublicMarketEventStoreError("event chain link mismatch")
                expected = _sha256(
                    {
                        "event_id": row["event_id"],
                        "venue": row["venue"],
                        "channel": row["channel"],
                        "instrument_id": row["instrument_id"],
                        "event_time_ms": int(row["event_time_ms"]),
                        "received_at_ms": int(row["received_at_ms"]),
                        "processed_at_ms": int(row["processed_at_ms"]),
                        "source_sequence": row["source_sequence"],
                        "payload_sha256": row["payload_sha256"],
                        "prev_hash": row["prev_hash"],
                    }
                )
                if str(row["row_hash"]) != expected:
                    raise PublicMarketEventStoreError("event row hash mismatch")
                previous = str(row["row_hash"])
                last_seq = int(row["seq"])
                verified += 1
            return {
                "valid": True,
                "anchor_seq": checkpoint_seq,
                "verified_events": verified,
                "verified_seq": last_seq,
                "verified_row_hash": previous,
            }
        except (json.JSONDecodeError, sqlite3.DatabaseError, PublicMarketEventStoreError) as exc:
            return {
                "valid": False,
                "anchor_seq": checkpoint_seq,
                "verified_events": 0,
                "verified_seq": checkpoint_seq,
                "verified_row_hash": checkpoint_row_hash,
                "error": str(exc),
            }


__all__ = [
    "PublicMarketEventStore",
    "PublicMarketEventStoreError",
]
