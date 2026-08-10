"""Persisted point-in-time fundamentals store (durable sqlite3) + BYO-CSV ingest.

A fundamental observation reuses the bitemporal FeatureValue contract:
(feature_id=metric, instrument_id, value) keyed by event_time (fiscal period end)
and knowledge_time (announcement). The knowledge_time>=event_time invariant
(enforced by BitemporalRecord) makes lookahead impossible at the boundary, and
as_of(...) filters knowledge_time<=t so a backtest at t only sees fundamentals
already announced by t. Restatements are retained (same period, later
announcement = a distinct row). Offline; no network/broker. Fail-closed.
"""
from __future__ import annotations

import csv
import math
import sqlite3
from dataclasses import dataclass
from typing import Iterable, Optional

from quantos.contracts.feature import FeatureValue
from .persistent_store import DataStoreError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fundamentals (
    feature_id     TEXT NOT NULL,
    instrument_id  TEXT NOT NULL,
    event_time     INTEGER NOT NULL,
    knowledge_time INTEGER NOT NULL,
    value          REAL,
    PRIMARY KEY (feature_id, instrument_id, event_time, knowledge_time)
);
CREATE INDEX IF NOT EXISTS ix_fund_lookup ON fundamentals (feature_id, instrument_id, knowledge_time);
"""

_FUND_COLS = ("instrument_id", "metric", "event_time", "knowledge_time", "value")


@dataclass(frozen=True)
class FundamentalIngestionReport:
    rows_read: int
    added: int
    duplicates: int
    rejected: tuple


class SqliteFundamentalStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='fundamentals'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise DataStoreError(f"fundamental store unreadable: {exc}") from exc
        if not rows:
            raise DataStoreError("fundamental store uninitialized; missing 'fundamentals' table")

    def add(self, fv: FeatureValue) -> bool:
        self._require_schema()
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO fundamentals(feature_id, instrument_id, event_time, knowledge_time, value)"
            " VALUES (?,?,?,?,?)",
            (fv.feature_id, fv.instrument_id, fv.event_time, fv.knowledge_time, fv.value))
        self._conn.commit()
        return cur.rowcount == 1

    def add_many(self, values: Iterable[FeatureValue]) -> int:
        """Insert a bitemporal batch in one SQLite transaction.

        Primary-key repeats are idempotent no-ops.  A malformed value or
        database failure rolls the entire batch back rather than leaving a
        partial cross-section behind.
        """
        self._require_schema()
        batch = tuple(values)
        try:
            rows = []
            for fv in batch:
                if not isinstance(fv, FeatureValue):
                    raise TypeError(
                        f"expected FeatureValue, got {type(fv).__name__}"
                    )
                rows.append(
                    (
                        fv.feature_id,
                        fv.instrument_id,
                        fv.event_time,
                        fv.knowledge_time,
                        fv.value,
                    )
                )
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO fundamentals"
                "(feature_id, instrument_id, event_time, knowledge_time, value)"
                " VALUES (?,?,?,?,?)",
                rows,
            )
            inserted = self._conn.total_changes - before
            self._conn.commit()
            return inserted
        except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
            self._conn.rollback()
            raise DataStoreError(f"atomic batch insert failed: {exc}") from exc

    def close(self) -> None:
        self._conn.close()

    def _row_to_fv(self, r) -> FeatureValue:
        return FeatureValue(feature_id=r["feature_id"], instrument_id=r["instrument_id"],
                            event_time=r["event_time"], knowledge_time=r["knowledge_time"], value=r["value"])

    def as_of(self, feature_id: str, instrument_id: str, as_of_time: int) -> list:
        """Fundamentals for (metric, instrument) knowable by as_of_time, ordered by
        (event_time, knowledge_time). PIT: never returns a record announced after t."""
        self._require_schema()
        rows = self._conn.execute(
            "SELECT * FROM fundamentals WHERE feature_id=? AND instrument_id=? AND knowledge_time<=? "
            "ORDER BY event_time, knowledge_time", (feature_id, instrument_id, as_of_time))
        return [self._row_to_fv(r) for r in rows]

    def latest_as_of(self, feature_id: str, instrument_id: str, as_of_time: int) -> Optional[FeatureValue]:
        rows = self.as_of(feature_id, instrument_id, as_of_time)
        return rows[-1] if rows else None


def ingest_fundamentals_csv(path: str, store: SqliteFundamentalStore) -> FundamentalIngestionReport:
    """Read a local fundamentals CSV (instrument_id,metric,event_time,knowledge_time,value;
    times are YYYYMMDD ints). The FeatureValue contract rejects lookahead rows
    (knowledge_time<event_time); bad rows are recorded, not crashed."""
    rows_read, added, duplicates, rejected = 0, 0, 0, []
    with open(path, newline="", encoding="utf-8") as fh:
        for i, raw in enumerate(csv.DictReader(fh)):
            rows_read += 1
            try:
                v = float(raw["value"])
                if not math.isfinite(v):                 # 拒 inf/nan(失败关闭,审查 P2)
                    raise ValueError(f"non-finite value: {raw['value']!r}")
                fv = FeatureValue(
                    feature_id=raw["metric"].strip(),
                    instrument_id=raw["instrument_id"].strip(),
                    event_time=int(raw["event_time"]),
                    knowledge_time=int(raw["knowledge_time"]),
                    value=v,
                )
            except Exception as exc:
                rejected.append((i, type(exc).__name__, str(exc)))
                continue
            if store.add(fv):
                added += 1
            else:
                duplicates += 1
    return FundamentalIngestionReport(rows_read=rows_read, added=added, duplicates=duplicates, rejected=tuple(rejected))
