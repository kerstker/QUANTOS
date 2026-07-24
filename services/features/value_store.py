"""PIT feature-value materialization store (Rank #15).

Rolling features are recomputed from raw bars on every research run (services/features/
calculation.rolling_feature; the driver panels re-evaluate each time). This clones the
fundamental_store bitemporal schema — adding `feature_version` to the key — so a computed
feature value can be materialized once and read back point-in-time: the FeatureValue contract's
knowledge_time>=event_time invariant blocks boundary lookahead, and as_of() filters
knowledge_time<=t so a backtest at t only sees values already knowable by t. INSERT OR IGNORE
makes writes idempotent (a re-materialization is a no-op, a restatement is a distinct
knowledge_time row). Offline, no wall-clock, fail-closed on an uninitialized store."""
from __future__ import annotations

import os
import sqlite3
import threading
from typing import Iterable, Optional

from quantos.contracts.feature import FeatureValue
from services.data.persistent_store import DataStoreError

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feature_values (
    feature_id      TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    instrument_id   TEXT NOT NULL,
    event_time      INTEGER NOT NULL,
    knowledge_time  INTEGER NOT NULL,
    value           REAL,
    PRIMARY KEY (feature_id, feature_version, instrument_id, event_time, knowledge_time)
);
CREATE INDEX IF NOT EXISTS ix_fv_lookup
    ON feature_values (feature_id, feature_version, instrument_id, knowledge_time);
CREATE TRIGGER IF NOT EXISTS feature_values_no_update
BEFORE UPDATE ON feature_values
BEGIN SELECT RAISE(ABORT, 'feature_values is append-only'); END;
CREATE TRIGGER IF NOT EXISTS feature_values_no_delete
BEFORE DELETE ON feature_values
BEGIN SELECT RAISE(ABORT, 'feature_values is append-only'); END;
"""


class SqliteFeatureValueStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        if create and path != ":memory:":
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        if create:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout=5000")
                if path != ":memory:":
                    self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
                self._conn.executescript(_SCHEMA)
                self._conn.commit()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='feature_values'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise DataStoreError(f"feature value store unreadable: {exc}") from exc
        if not rows:
            raise DataStoreError("feature value store uninitialized; missing 'feature_values' table")

    def add(self, fv: FeatureValue, *, version: str) -> bool:
        return self.add_many((fv,), version=version) == 1

    def add_many(self, values: Iterable[FeatureValue], *, version: str) -> int:
        if not isinstance(version, str) or not version:
            raise ValueError("version must be a non-empty string")
        batch = tuple(values)
        if any(not isinstance(value, FeatureValue) for value in batch):
            raise TypeError("values must contain FeatureValue instances")
        with self._lock:
            self._require_schema()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                before = self._conn.total_changes
                self._conn.executemany(
                    "INSERT OR IGNORE INTO feature_values(feature_id, feature_version, instrument_id,"
                    " event_time, knowledge_time, value) VALUES (?,?,?,?,?,?)",
                    [
                        (value.feature_id, version, value.instrument_id, value.event_time,
                         value.knowledge_time, value.value)
                        for value in batch
                    ],
                )
                inserted = self._conn.total_changes - before
                self._conn.commit()
                return int(inserted)
            except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
                self._conn.rollback()
                raise DataStoreError(f"feature value batch insert failed: {exc}") from exc

    def _row_to_fv(self, r) -> FeatureValue:
        return FeatureValue(feature_id=r["feature_id"], instrument_id=r["instrument_id"],
                            event_time=r["event_time"], knowledge_time=r["knowledge_time"], value=r["value"])

    def as_of(self, feature_id: str, version: str, instrument_id: str, as_of_time: int) -> list:
        """Materialized values for (feature, version, instrument) knowable by as_of_time, ordered by
        (event_time, knowledge_time). PIT: never returns a value whose knowledge_time > as_of_time."""
        if isinstance(as_of_time, bool) or not isinstance(as_of_time, int) or as_of_time < 0:
            raise ValueError("as_of_time must be a non-negative integer")
        if not isinstance(version, str) or not version:
            raise ValueError("version must be a non-empty string")
        with self._lock:
            self._require_schema()
            rows = self._conn.execute(
                "SELECT * FROM feature_values WHERE feature_id=? AND feature_version=? AND instrument_id=?"
                " AND knowledge_time<=? ORDER BY event_time, knowledge_time",
                (feature_id, version, instrument_id, as_of_time)).fetchall()
        return [self._row_to_fv(r) for r in rows]

    def latest_as_of(self, feature_id: str, version: str, instrument_id: str,
                     as_of_time: int) -> Optional[FeatureValue]:
        rows = self.as_of(feature_id, version, instrument_id, as_of_time)
        return rows[-1] if rows else None
