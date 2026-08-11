"""Append-only, hash-chained storage for public Binance market snapshots."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class BinanceRealtimeStoreError(RuntimeError):
    """The Binance public-market store is missing, corrupt, or conflicting."""


MAX_FUTURE_SKEW_MS = 30_000
SQLITE_BUSY_TIMEOUT_SECONDS = 30.0
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
        try:
            if create:
                self._conn = sqlite3.connect(
                    path,
                    timeout=SQLITE_BUSY_TIMEOUT_SECONDS,
                )
            else:
                read_only_uri = f"{Path(path).resolve().as_uri()}?mode=ro"
                self._conn = sqlite3.connect(
                    read_only_uri,
                    uri=True,
                    timeout=SQLITE_BUSY_TIMEOUT_SECONDS,
                )
        except sqlite3.Error as exc:
            raise BinanceRealtimeStoreError("snapshot store is unreadable") from exc
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
            knowledge_time_ms = int(
                snapshot.get("knowledge_time_ms", observed_at_ms)
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BinanceRealtimeStoreError("snapshot timestamps are invalid") from exc
        if (
            not instrument_id.endswith("USDT")
            or observed_at_ms < 0
            or event_time_ms < 0
            or knowledge_time_ms < 0
            or event_time_ms > knowledge_time_ms
            or event_time_ms > observed_at_ms + MAX_FUTURE_SKEW_MS
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

    def latest_for_provider(
        self,
        instrument_id: str,
        provider: str,
        *,
        as_of_ms: int | None = None,
    ) -> dict[str, Any] | None:
        """Return the latest verified row for one exact public provider."""
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError("provider must be a non-empty string")
        if as_of_ms is not None and (
            isinstance(as_of_ms, bool)
            or not isinstance(as_of_ms, int)
            or as_of_ms < 0
        ):
            raise ValueError("as_of_ms must be a non-negative integer")
        if as_of_ms is None:
            query = (
                "SELECT * FROM binance_market_snapshots WHERE instrument_id=? "
                "ORDER BY observed_at_ms DESC"
            )
            args = (str(instrument_id).upper(),)
        else:
            query = (
                "SELECT * FROM binance_market_snapshots WHERE instrument_id=? "
                "AND observed_at_ms<=? ORDER BY observed_at_ms DESC"
            )
            args = (str(instrument_id).upper(), as_of_ms)
        rows = self._conn.execute(query, args)
        for row in rows:
            payload = self._decode(row)
            if payload.get("provider") == provider:
                return payload
        return None

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

    @staticmethod
    def _validate_checkpoint(
        checkpoint_seq: int,
        checkpoint_row_hash: str,
    ) -> None:
        if (
            isinstance(checkpoint_seq, bool)
            or not isinstance(checkpoint_seq, int)
            or checkpoint_seq < 0
        ):
            raise BinanceRealtimeStoreError("chain checkpoint sequence is invalid")
        if checkpoint_seq == 0:
            if checkpoint_row_hash != _GENESIS:
                raise BinanceRealtimeStoreError("genesis checkpoint hash is invalid")
        elif (
            not isinstance(checkpoint_row_hash, str)
            or len(checkpoint_row_hash) != 64
            or any(character not in "0123456789abcdef" for character in checkpoint_row_hash)
        ):
            raise BinanceRealtimeStoreError("chain checkpoint hash is invalid")

    def _verify_chain_suffix(
        self,
        *,
        checkpoint_seq: int,
        checkpoint_row_hash: str,
    ) -> dict[str, Any]:
        self._validate_checkpoint(checkpoint_seq, checkpoint_row_hash)
        if checkpoint_seq > 0:
            anchor = self._conn.execute(
                "SELECT row_hash FROM binance_market_snapshots WHERE seq=?",
                (checkpoint_seq,),
            ).fetchone()
            if anchor is None or str(anchor["row_hash"]) != checkpoint_row_hash:
                raise BinanceRealtimeStoreError("chain checkpoint anchor mismatch")
        previous = checkpoint_row_hash
        last_seq = checkpoint_seq
        verified_rows = 0
        for row in self._conn.execute(
            "SELECT * FROM binance_market_snapshots WHERE seq>? ORDER BY seq",
            (checkpoint_seq,),
        ):
            self._decode(row)
            if row["prev_hash"] != previous:
                raise BinanceRealtimeStoreError("snapshot chain link mismatch")
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
                raise BinanceRealtimeStoreError("snapshot row hash mismatch")
            previous = str(row["row_hash"])
            last_seq = int(row["seq"])
            verified_rows += 1
        return {
            "valid": True,
            "anchor_seq": checkpoint_seq,
            "verified_suffix_rows": verified_rows,
            "verified_seq": last_seq,
            "verified_row_hash": previous,
        }

    def verify_chain_from(
        self,
        *,
        checkpoint_seq: int = 0,
        checkpoint_row_hash: str = _GENESIS,
    ) -> dict[str, Any]:
        """Verify the append-only suffix after a previously trusted head."""
        try:
            return self._verify_chain_suffix(
                checkpoint_seq=checkpoint_seq,
                checkpoint_row_hash=checkpoint_row_hash,
            )
        except (BinanceRealtimeStoreError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
            return {
                "valid": False,
                "anchor_seq": checkpoint_seq,
                "verified_suffix_rows": 0,
                "verified_seq": checkpoint_seq,
                "verified_row_hash": checkpoint_row_hash,
                "error": str(exc),
            }

    def verified_recent_rows(
        self,
        limit: int,
        *,
        checkpoint_seq: int = 0,
        checkpoint_row_hash: str = _GENESIS,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Verify a chain suffix and read its recent rows from one DB snapshot."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        try:
            self._conn.execute("BEGIN")
            verification = self._verify_chain_suffix(
                checkpoint_seq=checkpoint_seq,
                checkpoint_row_hash=checkpoint_row_hash,
            )
            rows = list(
                self._conn.execute(
                    "SELECT * FROM binance_market_snapshots ORDER BY seq DESC LIMIT ?",
                    (limit,),
                )
            )
            decoded = [self._decode(row) for row in reversed(rows)]
            self._conn.rollback()
            return decoded, verification
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def chain_valid(self) -> bool:
        return bool(self.verify_chain_from()["valid"])


__all__ = ["BinanceRealtimeStore", "BinanceRealtimeStoreError"]
