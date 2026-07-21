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

import hashlib
import json
import math
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
        with self._lock:
            self._require_schema()
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO feature_values(feature_id, feature_version, instrument_id,"
                " event_time, knowledge_time, value) VALUES (?,?,?,?,?,?)",
                (fv.feature_id, version, fv.instrument_id, fv.event_time, fv.knowledge_time, fv.value))
            self._conn.commit()
            return cur.rowcount == 1

    def add_many(self, values: Iterable[FeatureValue], *, version: str) -> int:
        rows = list(values)
        if not rows:
            return 0
        with self._lock:
            self._require_schema()
            before = self._conn.total_changes
            self._conn.executemany(
                "INSERT OR IGNORE INTO feature_values(feature_id, feature_version, instrument_id,"
                " event_time, knowledge_time, value) VALUES (?,?,?,?,?,?)",
                [
                    (v.feature_id, version, v.instrument_id, v.event_time, v.knowledge_time, v.value)
                    for v in rows
                ],
            )
            self._conn.commit()
            return self._conn.total_changes - before

    def _row_to_fv(self, r) -> FeatureValue:
        return FeatureValue(feature_id=r["feature_id"], instrument_id=r["instrument_id"],
                            event_time=r["event_time"], knowledge_time=r["knowledge_time"], value=r["value"])

    def as_of(self, feature_id: str, version: str, instrument_id: str, as_of_time: int) -> list:
        """Materialized values for (feature, version, instrument) knowable by as_of_time, ordered by
        (event_time, knowledge_time). PIT: never returns a value whose knowledge_time > as_of_time."""
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

    def summary(self, feature_id: str, version: str) -> dict:
        """Return deterministic coverage metadata for an auditable cache snapshot."""
        with self._lock:
            self._require_schema()
            row = self._conn.execute(
                "SELECT COUNT(*) AS row_count, COUNT(DISTINCT instrument_id) AS instrument_count, "
                "MIN(event_time) AS min_event_time, MAX(event_time) AS max_event_time, "
                "MAX(knowledge_time) AS max_knowledge_time "
                "FROM feature_values WHERE feature_id=? AND feature_version=?",
                (feature_id, version),
            ).fetchone()
        return {
            "row_count": int(row["row_count"]),
            "instrument_count": int(row["instrument_count"]),
            "min_event_time": row["min_event_time"],
            "max_event_time": row["max_event_time"],
            "max_knowledge_time": row["max_knowledge_time"],
        }

    def content_digest(self, feature_id: str, version: str) -> str:
        """Hash canonical stored rows so a cache can be compared after reopen."""
        with self._lock:
            self._require_schema()
            rows = self._conn.execute(
                "SELECT instrument_id, event_time, knowledge_time, value "
                "FROM feature_values WHERE feature_id=? AND feature_version=? "
                "ORDER BY instrument_id, event_time, knowledge_time",
                (feature_id, version),
            ).fetchall()
        digest = hashlib.sha256()
        for row in rows:
            encoded = json.dumps(
                [row["instrument_id"], row["event_time"], row["knowledge_time"], row["value"]],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
            digest.update(encoded)
            digest.update(b"\n")
        return digest.hexdigest()

    def distribution_series(self, feature_id: str, version: str) -> list[float]:
        """Return one latest-known cross-sectional mean per event time.

        The read is deliberately aggregate-only: for a revised bitemporal cell, the
        greatest knowledge time is selected before averaging instruments. This gives
        diagnostic callers a deterministic temporal series without exposing a mutable
        row-level view or allowing a future-known revision into an earlier cell.
        """
        with self._lock:
            self._require_schema()
            rows = self._conn.execute(
                "SELECT event_time, value FROM ("
                "SELECT event_time, value, instrument_id, knowledge_time, "
                "ROW_NUMBER() OVER (PARTITION BY instrument_id, event_time "
                "ORDER BY knowledge_time DESC) AS revision_rank "
                "FROM feature_values WHERE feature_id=? AND feature_version=? "
                "AND value IS NOT NULL"
                ") WHERE revision_rank=1 ORDER BY event_time, instrument_id",
                (feature_id, version),
            ).fetchall()
        by_event: dict[int, list[float]] = {}
        for row in rows:
            value = float(row["value"])
            if math.isfinite(value):
                by_event.setdefault(int(row["event_time"]), []).append(value)
        return [sum(values) / len(values) for _, values in sorted(by_event.items()) if values]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
