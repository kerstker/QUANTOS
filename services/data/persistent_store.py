"""Persisted point-in-time bar store (stdlib sqlite3).

Same lookahead-safe semantics as the in-memory PITStore (as_of filters
knowledge_time <= as_of_time), but durable across process restarts and indexed
for as_of queries. Also satisfies the MarketDataSource protocol (fetch_bars).
Fail-closed: reading an uninitialized/corrupt store raises rather than reading as
empty (which would silently drop history). Deterministic ordering."""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

from quantos.contracts.data import Bar


class DataStoreError(Exception):
    """The store is missing, corrupt, or uninitialized — fail closed."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    key TEXT NOT NULL,
    event_time INTEGER NOT NULL,
    knowledge_time INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (key, event_time, knowledge_time)
);
CREATE INDEX IF NOT EXISTS ix_bars_key_kt ON bars (key, knowledge_time);
"""


class SqlitePITStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='bars'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise DataStoreError(f"store unreadable: {exc}") from exc
        if not rows:
            raise DataStoreError("store uninitialized; missing 'bars' table")

    def contains(self, key: str, event_time: int, knowledge_time: int) -> bool:
        self._require_schema()
        row = self._conn.execute(
            "SELECT 1 FROM bars WHERE key=? AND event_time=? AND knowledge_time=?",
            (key, event_time, knowledge_time)).fetchone()
        return row is not None

    def add(self, key: str, bar: Bar) -> bool:
        self._require_schema()
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO bars(key, event_time, knowledge_time, payload_json) VALUES (?,?,?,?)",
            (key, bar.event_time, bar.knowledge_time,
             json.dumps(bar.model_dump(mode="json"), sort_keys=True)))
        self._conn.commit()
        return cur.rowcount == 1

    def add_many(self, key: str, bars) -> int:
        return sum(1 for b in bars if self.add(key, b))

    def add_batch_atomic(self, bars) -> int:
        """Insert a normalized batch in one SQLite transaction.

        This is the load-bearing writer for strict ingestion: serialization or
        database failure rolls the entire batch back, while primary-key repeats
        remain idempotent no-ops.
        """
        self._require_schema()
        batch = tuple(bars)
        try:
            rows = [
                (
                    bar.instrument_id,
                    bar.event_time,
                    bar.knowledge_time,
                    json.dumps(bar.model_dump(mode="json"), sort_keys=True),
                )
                for bar in batch
            ]
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO bars(key, event_time, knowledge_time, payload_json) "
                "VALUES (?,?,?,?)",
                rows,
            )
            inserted = self._conn.total_changes - before
            self._conn.commit()
            return inserted
        except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
            self._conn.rollback()
            raise DataStoreError(f"atomic batch insert failed: {exc}") from exc

    def _query(self, key, as_of_time, event_time_max):
        self._require_schema()
        sql = "SELECT payload_json FROM bars WHERE key=? AND knowledge_time<=?"
        args = [key, as_of_time]
        if event_time_max is not None:
            sql += " AND event_time<=?"
            args.append(event_time_max)
        sql += " ORDER BY event_time, knowledge_time"
        return [Bar.model_validate(json.loads(r["payload_json"]))
                for r in self._conn.execute(sql, args)]

    def as_of(self, key: str, as_of_time: int, event_time_max: Optional[int] = None) -> list:
        return self._query(key, as_of_time, event_time_max)

    def latest_as_of(self, key: str, as_of_time: int, event_time_max: Optional[int] = None):
        rows = self._query(key, as_of_time, event_time_max)
        return rows[-1] if rows else None

    def fetch_bars(self, instrument_id: str, start: int, end: int) -> list:
        # MarketDataSource protocol: a RAW event_time window fetch — it does NOT
        # apply a knowledge_time filter, so it is NOT lookahead-safe on its own. Use
        # as_of(...) for point-in-time / no-lookahead reads (review).
        self._require_schema()
        rows = self._conn.execute(
            "SELECT payload_json FROM bars WHERE key=? AND event_time>=? AND event_time<=? "
            "ORDER BY event_time, knowledge_time", (instrument_id, start, end))
        return [Bar.model_validate(json.loads(r["payload_json"])) for r in rows]

    def fetch_bars_pit(self, instrument_id: str, start: int, end: int) -> list:
        """Bulk PIT-safe read: same return shape and ordering as `fetch_bars`, but each
        returned bar is filtered to `knowledge_time <= event_time` — only bars knowable
        by their OWN event date.

        This is the bulk-PIT read for panel builders: a consumer that uses each bar only
        AT its own event_time (e.g. indexing bars into a date grid) sees exactly the data
        that was knowable on that date, so the bulk read cannot leak the future even
        though it scans the whole window in one pass.

        CAUTION: PIT-safe ONLY for use-each-bar-at-its-own-event-date consumers. It is NOT
        a substitute for `as_of(t)` / `latest_as_of(t)` when consuming at a decision time
        t != event_time (it neither filters knowledge_time <= t nor carries the latest
        value knowable at t forward). Decision-time reads must still go through as_of.
        """
        return [b for b in self.fetch_bars(instrument_id, start, end)
                if b.knowledge_time <= b.event_time]

    def keys(self) -> list:
        self._require_schema()
        return [r["key"] for r in self._conn.execute("SELECT DISTINCT key FROM bars ORDER BY key")]
