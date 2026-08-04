"""PIT feature-value materialization store (Rank #15).

Rolling features are recomputed from raw bars on every research run (services/features/
calculation.rolling_feature; the driver panels re-evaluate each time). This clones the
fundamental_store bitemporal schema — adding `feature_version` to the key — so a computed
feature value can be materialized once and read back point-in-time: the FeatureValue contract's
knowledge_time>=event_time invariant blocks boundary lookahead, and as_of() filters
knowledge_time<=t so a backtest at t only sees values already knowable by t. INSERT OR IGNORE
makes writes idempotent (a re-materialization is a no-op, a restatement is a distinct
knowledge_time row). Offline, no wall-clock, fail-closed on an uninitialized store.

Schema v2 (727 §6.2 FactorObservation, Factor Garden F3, 2026-07-27): four nullable
columns — computed_at_ms / dataset_version / materializer_version / quality_verdict —
added by lazy migration on the first v2 write. Historical rows keep them NULL ("materialized
before v2" — an honest marker, never backfilled). The v1 write/read paths are bit-identical;
the v2 metadata surfaces through add_many's optional kwargs and as_of_observations()."""
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

# Schema v2 (727 §6.2 FactorObservation, Factor Garden F3): the four observation-metadata
# columns, all nullable with no DEFAULT. _SCHEMA above deliberately stays the v1 shape so
# the lazy migration (_ensure_v2_columns, same precedent as
# experiment_registry._ensure_triple_columns) is the SINGLE column-adding path — exercised
# by fresh and legacy stores alike on the first v2 write. Historical v1 rows keep NULL v2
# columns ("materialized before v2" — an honest marker, never backfilled).
_SCHEMA_VERSION = 2
_V2_COLUMNS = {
    "computed_at_ms": "INTEGER",       # caller-supplied int ms; when the batch was computed
    "dataset_version": "TEXT",         # which data version the values were computed from
    "materializer_version": "TEXT",    # which materializer wrote the row
    "quality_verdict": "TEXT",         # caller/global quality marker (per-cell checks: later)
}


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

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def row_count(self) -> int:
        with self._lock:
            self._require_schema()
            return int(
                self._conn.execute("SELECT COUNT(*) FROM feature_values").fetchone()[0]
            )

    def add(self, fv: FeatureValue, *, version: str, computed_at_ms=None,
            dataset_version=None, materializer_version=None, quality_verdict=None) -> bool:
        return self.add_many((fv,), version=version, computed_at_ms=computed_at_ms,
                             dataset_version=dataset_version,
                             materializer_version=materializer_version,
                             quality_verdict=quality_verdict) == 1

    @staticmethod
    def _validate_v2_metadata(computed_at_ms, dataset_version, materializer_version,
                              quality_verdict) -> bool:
        """Validate the optional v2 (727 §6.2) batch metadata; returns True if any is set."""
        if computed_at_ms is not None and (isinstance(computed_at_ms, bool)
                                           or not isinstance(computed_at_ms, int)
                                           or computed_at_ms < 0):
            raise ValueError("computed_at_ms must be a non-negative integer or None")
        for name, text in (("dataset_version", dataset_version),
                           ("materializer_version", materializer_version),
                           ("quality_verdict", quality_verdict)):
            if text is not None and (not isinstance(text, str) or not text):
                raise ValueError(f"{name} must be a non-empty string or None")
        return any(v is not None for v in (computed_at_ms, dataset_version,
                                           materializer_version, quality_verdict))

    def _fv_columns(self) -> set:
        return {row["name"] for row in self._conn.execute("PRAGMA table_info(feature_values)")}

    def _ensure_v2_columns(self) -> None:
        """Lazy-migrate the store to the 727 §6.2 schema on the first v2 write. Idempotent;
        historical rows keep NULL v2 columns (the honest pre-v2 marker), only new writes
        carry the FactorObservation metadata. Caller holds the lock + write transaction."""
        present = self._fv_columns()
        for column, ddl in _V2_COLUMNS.items():
            if column not in present:
                self._conn.execute(f"ALTER TABLE feature_values ADD COLUMN {column} {ddl}")

    def add_many(self, values: Iterable[FeatureValue], *, version: str,
                 computed_at_ms=None, dataset_version=None,
                 materializer_version=None, quality_verdict=None) -> int:
        """Append values for `version`; returns rows inserted (INSERT OR IGNORE idempotent).

        The four optional kwargs are the schema-v2 (727 §6.2 FactorObservation) batch
        metadata — all default None, so v1 callers are bit-identical (6-column insert, no
        migration). The first call carrying any v2 metadata lazily migrates the table."""
        if not isinstance(version, str) or not version:
            raise ValueError("version must be a non-empty string")
        has_v2 = self._validate_v2_metadata(computed_at_ms, dataset_version,
                                            materializer_version, quality_verdict)
        batch = tuple(values)
        if any(not isinstance(value, FeatureValue) for value in batch):
            raise TypeError("values must contain FeatureValue instances")
        with self._lock:
            self._require_schema()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                if has_v2:
                    self._ensure_v2_columns()          # re-checked under the write lock
                before = self._conn.total_changes
                if has_v2:
                    self._conn.executemany(
                        "INSERT OR IGNORE INTO feature_values(feature_id, feature_version,"
                        " instrument_id, event_time, knowledge_time, value, computed_at_ms,"
                        " dataset_version, materializer_version, quality_verdict)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [
                            (value.feature_id, version, value.instrument_id, value.event_time,
                             value.knowledge_time, value.value, computed_at_ms,
                             dataset_version, materializer_version, quality_verdict)
                            for value in batch
                        ],
                    )
                else:
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

    def tail_as_of(self, feature_id: str, version: str, instrument_id: str,
                   as_of_time: int, *, limit: int) -> list:
        """Latest bounded PIT values in canonical ascending order.

        Unlike ``as_of`` this read is constant-space with respect to archive
        growth.  SQLite can satisfy the descending event/knowledge order from
        the table primary key and stops after ``limit`` qualifying rows; the
        small result is reversed before returning so callers retain the exact
        ordering contract of ``as_of``.
        """
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if isinstance(as_of_time, bool) or not isinstance(as_of_time, int) or as_of_time < 0:
            raise ValueError("as_of_time must be a non-negative integer")
        if not isinstance(version, str) or not version:
            raise ValueError("version must be a non-empty string")
        with self._lock:
            self._require_schema()
            rows = self._conn.execute(
                # The schema-defined composite PRIMARY KEY is the only index
                # ordered by event_time.  Force it here: the otherwise useful
                # knowledge-time lookup index makes SQLite scan and sort the
                # complete series before applying LIMIT, defeating the bounded
                # realtime read on multi-million-row stores.
                "SELECT * FROM feature_values"
                " INDEXED BY sqlite_autoindex_feature_values_1"
                " WHERE feature_id=? AND feature_version=?"
                " AND instrument_id=? AND knowledge_time<=?"
                " ORDER BY event_time DESC, knowledge_time DESC LIMIT ?",
                (feature_id, version, instrument_id, as_of_time, limit),
            ).fetchall()
        return [self._row_to_fv(row) for row in reversed(rows)]

    def latest_cursor(self, feature_id: str, version: str,
                      instrument_id: str) -> Optional[tuple[int, int]]:
        """Latest ``(knowledge_time, event_time)`` cursor for one series."""
        if not isinstance(version, str) or not version:
            raise ValueError("version must be a non-empty string")
        with self._lock:
            self._require_schema()
            row = self._conn.execute(
                "SELECT knowledge_time, event_time FROM feature_values WHERE feature_id=?"
                " AND feature_version=? AND instrument_id=?"
                " ORDER BY knowledge_time DESC, event_time DESC LIMIT 1",
                (feature_id, version, instrument_id),
            ).fetchone()
        return None if row is None else (int(row[0]), int(row[1]))

    def latest_knowledge_time(self, feature_id: str, version: str,
                              instrument_id: str) -> Optional[int]:
        """Latest retained knowledge-time watermark for one feature series."""
        cursor = self.latest_cursor(feature_id, version, instrument_id)
        return None if cursor is None else cursor[0]

    def latest_as_of(self, feature_id: str, version: str, instrument_id: str,
                     as_of_time: int) -> Optional[FeatureValue]:
        rows = self.tail_as_of(
            feature_id,
            version,
            instrument_id,
            as_of_time,
            limit=1,
        )
        return rows[-1] if rows else None

    def as_of_observations(self, feature_id: str, version: str, instrument_id: str,
                           as_of_time: int) -> list:
        """Schema-v2 read path (727 §6.2 FactorObservation): same PIT filter and ordering
        as as_of(), but returns full row dicts including the v2 metadata columns
        (computed_at_ms / dataset_version / materializer_version / quality_verdict).

        Minimal-intrusion choice: as_of()/latest_as_of() keep returning the FeatureValue
        contract bit-identically (their consumers never asked for the metadata); this is
        the additive observation-level read. On an unmigrated v1 store the v2 columns read
        back as None (NULL AS fallback) — the honest "materialized before v2" marker."""
        if isinstance(as_of_time, bool) or not isinstance(as_of_time, int) or as_of_time < 0:
            raise ValueError("as_of_time must be a non-negative integer")
        if not isinstance(version, str) or not version:
            raise ValueError("version must be a non-empty string")
        with self._lock:
            self._require_schema()
            present = self._fv_columns()
            select_v2 = "".join(
                f", {column}" if column in present else f", NULL AS {column}"
                for column in _V2_COLUMNS
            )
            rows = self._conn.execute(
                "SELECT feature_id, feature_version, instrument_id, event_time,"
                " knowledge_time, value" + select_v2 +
                " FROM feature_values WHERE feature_id=? AND feature_version=?"
                " AND instrument_id=? AND knowledge_time<=?"
                " ORDER BY event_time, knowledge_time",
                (feature_id, version, instrument_id, as_of_time)).fetchall()
        return [dict(r) for r in rows]
