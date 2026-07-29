"""Append-only, hash-chained storage for public cross-venue order books."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any


class CrossVenueOrderBookStoreError(RuntimeError):
    """A cross-venue book bundle is missing, corrupt, or conflicting."""


_GENESIS = ""
_SCHEMA = """
CREATE TABLE IF NOT EXISTS cross_venue_order_book_bundles (
  seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id      TEXT NOT NULL UNIQUE,
  observed_at_ms   INTEGER NOT NULL,
  payload_json     TEXT NOT NULL,
  payload_sha256   TEXT NOT NULL,
  prev_hash        TEXT NOT NULL,
  row_hash         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cross_venue_book_observed
ON cross_venue_order_book_bundles(observed_at_ms);
CREATE TRIGGER IF NOT EXISTS cross_venue_order_book_bundles_no_update
BEFORE UPDATE ON cross_venue_order_book_bundles
BEGIN SELECT RAISE(ABORT, 'cross_venue_order_book_bundles is append-only'); END;
CREATE TRIGGER IF NOT EXISTS cross_venue_order_book_bundles_no_delete
BEFORE DELETE ON cross_venue_order_book_bundles
BEGIN SELECT RAISE(ABORT, 'cross_venue_order_book_bundles is append-only'); END;
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
        raise CrossVenueOrderBookStoreError(
            "book bundle is not canonical JSON"
        ) from exc


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class CrossVenueOrderBookStore:
    """Durable public-book bundles with idempotency and tamper evidence."""

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
            raise CrossVenueOrderBookStoreError("book store is unreadable") from exc
        if "cross_venue_order_book_bundles" not in tables:
            raise CrossVenueOrderBookStoreError("book store schema is missing")

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _identity(bundle: Mapping[str, Any]) -> tuple[str, int]:
        if not isinstance(bundle, Mapping):
            raise CrossVenueOrderBookStoreError("book bundle must be a mapping")
        snapshot_id = str(bundle.get("snapshot_id", "")).strip()
        try:
            observed_at_ms = int(bundle["observed_at_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CrossVenueOrderBookStoreError(
                "book bundle timestamp is invalid"
            ) from exc
        books = bundle.get("books")
        if (
            not snapshot_id
            or observed_at_ms < 0
            or not isinstance(books, list)
            or not books
        ):
            raise CrossVenueOrderBookStoreError("book bundle identity is invalid")
        return snapshot_id, observed_at_ms

    def _decode(self, row: sqlite3.Row) -> dict[str, Any]:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise CrossVenueOrderBookStoreError(
                "book bundle row has invalid JSON"
            ) from exc
        if not isinstance(payload, dict) or _sha256(payload) != row["payload_sha256"]:
            raise CrossVenueOrderBookStoreError("book bundle payload hash mismatch")
        return payload

    def append_bundle(self, bundle: Mapping[str, Any]) -> bool:
        payload = dict(bundle)
        snapshot_id, observed_at_ms = self._identity(payload)
        payload_json = _canonical(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT payload_sha256 FROM cross_venue_order_book_bundles "
                "WHERE snapshot_id=?",
                (snapshot_id,),
            ).fetchone()
            if existing is not None:
                if existing["payload_sha256"] == payload_sha256:
                    self._conn.rollback()
                    return False
                raise CrossVenueOrderBookStoreError(
                    "snapshot_id reused with different content"
                )
            previous = self._conn.execute(
                "SELECT row_hash FROM cross_venue_order_book_bundles "
                "ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            prev_hash = _GENESIS if previous is None else str(previous["row_hash"])
            row_hash = _sha256(
                {
                    "snapshot_id": snapshot_id,
                    "observed_at_ms": observed_at_ms,
                    "payload_sha256": payload_sha256,
                    "prev_hash": prev_hash,
                }
            )
            self._conn.execute(
                "INSERT INTO cross_venue_order_book_bundles"
                "(snapshot_id,observed_at_ms,payload_json,payload_sha256,"
                "prev_hash,row_hash) VALUES (?,?,?,?,?,?)",
                (
                    snapshot_id,
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

    def latest(self) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM cross_venue_order_book_bundles "
            "ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return None if row is None else self._decode(row)

    def row_count(self) -> int:
        return int(
            self._conn.execute(
                "SELECT COUNT(*) FROM cross_venue_order_book_bundles"
            ).fetchone()[0]
        )

    def chain_valid(self) -> bool:
        previous = _GENESIS
        try:
            for row in self._conn.execute(
                "SELECT * FROM cross_venue_order_book_bundles ORDER BY seq"
            ):
                self._decode(row)
                if row["prev_hash"] != previous:
                    return False
                expected = _sha256(
                    {
                        "snapshot_id": row["snapshot_id"],
                        "observed_at_ms": int(row["observed_at_ms"]),
                        "payload_sha256": row["payload_sha256"],
                        "prev_hash": row["prev_hash"],
                    }
                )
                if row["row_hash"] != expected:
                    return False
                previous = str(row["row_hash"])
            return True
        except (
            CrossVenueOrderBookStoreError,
            sqlite3.DatabaseError,
            TypeError,
            ValueError,
        ):
            return False


__all__ = ["CrossVenueOrderBookStore", "CrossVenueOrderBookStoreError"]
