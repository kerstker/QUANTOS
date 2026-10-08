"""Durable, auditable persistence for the feature registry.

The in-memory registry is useful inside one research run, but it cannot prove
what definition or leakage verdict was used after a restart.  This SQLite
adapter keeps the existing facade while adding a tamper-evident status history:
registration and status changes append hash-chained events, and every read
checks the current row against that chain.  No wall clock is taken here;
callers provide ``recorded_at_ms`` when they have an operational timestamp.

The store is deliberately fail-closed.  An uninitialized, partial, or
tampered database raises ``DataStoreError`` instead of looking empty.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from typing import Any, Optional

from quantos.contracts.feature import (
    FeatureDefinition,
    FeatureRegistryItem,
    FeatureStatus,
)
from services.data.persistent_store import DataStoreError


_SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS feature_registry (
    feature_id      TEXT NOT NULL,
    version         TEXT NOT NULL,
    owner           TEXT,
    status          TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    leakage_passed  INTEGER,
    code_version    TEXT,
    PRIMARY KEY (feature_id, version)
);
CREATE TABLE IF NOT EXISTS feature_registry_history (
    seq                INTEGER PRIMARY KEY AUTOINCREMENT,
    feature_id         TEXT NOT NULL,
    version            TEXT NOT NULL,
    operation          TEXT NOT NULL CHECK (operation IN ('REGISTER','STATUS')),
    owner              TEXT NOT NULL,
    status             TEXT NOT NULL,
    definition_json    TEXT NOT NULL,
    definition_sha256  TEXT NOT NULL,
    leakage_passed     INTEGER,
    code_version       TEXT,
    metadata_json      TEXT NOT NULL,
    recorded_at_ms     INTEGER NOT NULL,
    prev_record_hash   TEXT NOT NULL,
    record_hash        TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS ix_feature_registry_history_key
    ON feature_registry_history(feature_id, version, seq);
CREATE TRIGGER IF NOT EXISTS feature_registry_history_no_update
BEFORE UPDATE ON feature_registry_history
BEGIN SELECT RAISE(ABORT, 'feature_registry_history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS feature_registry_history_no_delete
BEFORE DELETE ON feature_registry_history
BEGIN SELECT RAISE(ABORT, 'feature_registry_history is append-only'); END;
"""
_REQUIRED_TABLES = frozenset({"feature_registry", "feature_registry_history"})


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _definition_json(definition: FeatureDefinition) -> str:
    return definition.model_dump_json(
        by_alias=True,
        exclude_none=False,
        exclude_unset=False,
    )


def _definition_hash(definition_json: str) -> str:
    # Pydantic's JSON preserves field order, so hash its parsed canonical form
    # to make the digest independent of serializer whitespace/order.
    return _sha256_text(_canonical(json.loads(definition_json)))


def _validate_recorded_at(recorded_at_ms: int) -> int:
    if isinstance(recorded_at_ms, bool) or not isinstance(recorded_at_ms, int) \
            or recorded_at_ms < 0:
        raise ValueError("recorded_at_ms must be a non-negative integer")
    return recorded_at_ms


class SqliteFeatureRegistryStore:
    """SQLite implementation of the versioned feature registry facade."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if create and path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        try:
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout=5000")
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=FULL")
        except sqlite3.DatabaseError as exc:
            raise DataStoreError(f"feature registry unreadable: {exc}") from exc
        self._lock = threading.RLock()
        try:
            if create:
                with self._lock:
                    self._conn.executescript(_SCHEMA)
                    self._conn.commit()
                    self._backfill_history_unlocked()
        except BaseException:
            self._conn.close()
            raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "SqliteFeatureRegistryStore":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise DataStoreError(f"feature registry unreadable: {exc}") from exc
        tables = {str(row[0]) for row in rows}
        missing = sorted(_REQUIRED_TABLES - tables)
        if missing:
            raise DataStoreError(
                "feature registry uninitialized; missing tables: " + ", ".join(missing)
            )

    @staticmethod
    def _metadata(
        *,
        source_dataset_id: Optional[str],
        source_version: Optional[str],
        lineage: Optional[Mapping[str, Any]],
    ) -> str:
        if source_dataset_id is not None and (
            not isinstance(source_dataset_id, str) or not source_dataset_id.strip()
        ):
            raise ValueError("source_dataset_id must be a non-empty string when supplied")
        if source_version is not None and (
            not isinstance(source_version, str) or not source_version.strip()
        ):
            raise ValueError("source_version must be a non-empty string when supplied")
        if lineage is not None and not isinstance(lineage, Mapping):
            raise TypeError("lineage must be a mapping when supplied")
        value: dict[str, Any] = dict(lineage or {})
        if source_dataset_id is not None:
            value["source_dataset_id"] = source_dataset_id
        if source_version is not None:
            value["source_version"] = source_version
        try:
            return _canonical(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("lineage metadata must be canonical JSON") from exc

    @staticmethod
    def _history_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": _SCHEMA_VERSION,
            "feature_id": row["feature_id"],
            "version": row["version"],
            "operation": row["operation"],
            "owner": row["owner"],
            "status": row["status"],
            "definition_json": row["definition_json"],
            "definition_sha256": row["definition_sha256"],
            "leakage_passed": row["leakage_passed"],
            "code_version": row["code_version"],
            "metadata_json": row["metadata_json"],
            "recorded_at_ms": row["recorded_at_ms"],
        }

    def _record_hash(self, row: sqlite3.Row, prev_hash: str) -> str:
        return _sha256_text(_canonical({
            "payload": self._history_payload(row),
            "prev_record_hash": prev_hash,
        }))

    def _history_rows_unlocked(self, feature_id: str, version: str):
        return self._conn.execute(
            "SELECT * FROM feature_registry_history "
            "WHERE feature_id=? AND version=? ORDER BY seq",
            (feature_id, version),
        ).fetchall()

    def _verify_history_unlocked(self) -> dict[tuple[str, str], sqlite3.Row]:
        """Verify the global chain and current rows; return latest history rows."""
        self._require_schema()
        previous = ""
        latest: dict[tuple[str, str], sqlite3.Row] = {}
        try:
            rows = self._conn.execute(
                "SELECT * FROM feature_registry_history ORDER BY seq"
            ).fetchall()
            for row in rows:
                if row["prev_record_hash"] != previous:
                    raise DataStoreError(
                        f"feature registry history previous hash mismatch at seq {row['seq']}"
                    )
                expected = self._record_hash(row, previous)
                if row["record_hash"] != expected:
                    raise DataStoreError(
                        f"feature registry history hash mismatch at seq {row['seq']}"
                    )
                key = (row["feature_id"], row["version"])
                if row["operation"] == "REGISTER" and key in latest:
                    raise DataStoreError(f"feature registry duplicate registration for {key}")
                if row["operation"] == "STATUS" and key not in latest:
                    raise DataStoreError(f"feature registry status has no registration: {key}")
                latest[key] = row
                previous = expected
            current = self._conn.execute("SELECT * FROM feature_registry").fetchall()
        except sqlite3.DatabaseError as exc:
            raise DataStoreError(f"feature registry unreadable: {exc}") from exc
        current_keys = set()
        for row in current:
            key = (row["feature_id"], row["version"])
            current_keys.add(key)
            event = latest.get(key)
            if event is None:
                raise DataStoreError(f"feature registry row has no history: {key}")
            try:
                definition_hash = _definition_hash(row["definition_json"])
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise DataStoreError(f"feature registry definition is invalid: {key}") from exc
            if (
                (row["owner"] or "") != event["owner"]
                or row["status"] != event["status"]
                or row["definition_json"] != event["definition_json"]
                or definition_hash != event["definition_sha256"]
                or row["leakage_passed"] != event["leakage_passed"]
                or row["code_version"] != event["code_version"]
            ):
                raise DataStoreError(f"feature registry current row diverges from history: {key}")
        orphaned = sorted(set(latest) - current_keys)
        if orphaned:
            raise DataStoreError(f"feature registry history has no current row: {orphaned[0]}")
        return latest

    def _append_history_unlocked(
        self,
        *,
        feature_id: str,
        version: str,
        operation: str,
        owner: str,
        status: FeatureStatus,
        definition_json: str,
        leakage_passed: Optional[bool],
        code_version: Optional[str],
        metadata_json: str,
        recorded_at_ms: int,
    ) -> None:
        previous_row = self._conn.execute(
            "SELECT record_hash FROM feature_registry_history ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        previous = previous_row[0] if previous_row else ""
        candidate = {
            "feature_id": feature_id,
            "version": version,
            "operation": operation,
            "owner": owner,
            "status": status.value,
            "definition_json": definition_json,
            "definition_sha256": _definition_hash(definition_json),
            "leakage_passed": None if leakage_passed is None else int(bool(leakage_passed)),
            "code_version": code_version,
            "metadata_json": metadata_json,
            "recorded_at_ms": recorded_at_ms,
        }
        payload = _canonical({"schema_version": _SCHEMA_VERSION, **candidate})
        record_hash = _sha256_text(_canonical({"payload": json.loads(payload), "prev_record_hash": previous}))
        self._conn.execute(
            "INSERT INTO feature_registry_history"
            "(feature_id,version,operation,owner,status,definition_json,definition_sha256,"
            "leakage_passed,code_version,metadata_json,recorded_at_ms,prev_record_hash,record_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                feature_id, version, operation, owner, status.value, definition_json,
                candidate["definition_sha256"], candidate["leakage_passed"], code_version,
                metadata_json, recorded_at_ms, previous, record_hash,
            ),
        )

    def _backfill_history_unlocked(self) -> None:
        """Migrate a pre-history store without inventing operational timestamps."""
        existing = self._conn.execute(
            "SELECT COUNT(*) FROM feature_registry_history"
        ).fetchone()[0]
        if existing:
            self._verify_history_unlocked()
            return
        rows = self._conn.execute("SELECT * FROM feature_registry ORDER BY feature_id, version").fetchall()
        if not rows:
            return
        for row in rows:
            self._append_history_unlocked(
                feature_id=row["feature_id"], version=row["version"], operation="REGISTER",
                owner=row["owner"] or "", status=FeatureStatus(row["status"]),
                definition_json=row["definition_json"], leakage_passed=(
                    None if row["leakage_passed"] is None else bool(row["leakage_passed"])
                ), code_version=row["code_version"], metadata_json=_canonical({"migrated": True}),
                recorded_at_ms=0,
            )
        self._conn.commit()
        self._verify_history_unlocked()

    def register(
        self,
        item: FeatureRegistryItem,
        *,
        leakage_passed: Optional[bool] = None,
        code_version: Optional[str] = None,
        source_dataset_id: Optional[str] = None,
        source_version: Optional[str] = None,
        lineage: Optional[Mapping[str, Any]] = None,
        recorded_at_ms: int = 0,
    ) -> None:
        """Append a definition; duplicate ``(feature_id, version)`` is rejected."""
        if not isinstance(item, FeatureRegistryItem):
            raise TypeError("item must be a FeatureRegistryItem")
        if not item.version:
            raise ValueError("feature version must be non-empty")
        recorded_at_ms = _validate_recorded_at(recorded_at_ms)
        metadata_json = self._metadata(
            source_dataset_id=source_dataset_id,
            source_version=source_version,
            lineage=lineage,
        )
        definition_json = _definition_json(item.definition)
        key = (item.definition.feature_id, item.version)
        with self._lock:
            self._require_schema()
            self._verify_history_unlocked()
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "INSERT INTO feature_registry(feature_id,version,owner,status,definition_json,"
                    "leakage_passed,code_version) VALUES (?,?,?,?,?,?,?)",
                    (
                        key[0], key[1], item.owner, item.status.value, definition_json,
                        None if leakage_passed is None else int(bool(leakage_passed)), code_version,
                    ),
                )
                self._append_history_unlocked(
                    feature_id=key[0], version=key[1], operation="REGISTER", owner=item.owner,
                    status=item.status, definition_json=definition_json, leakage_passed=leakage_passed,
                    code_version=code_version, metadata_json=metadata_json, recorded_at_ms=recorded_at_ms,
                )
                self._conn.commit()
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                raise ValueError(f"already registered: {key}") from exc
            except Exception:
                self._conn.rollback()
                raise

    def _row_to_item(self, row: sqlite3.Row) -> FeatureRegistryItem:
        try:
            return FeatureRegistryItem(
                definition=FeatureDefinition.model_validate_json(row["definition_json"]),
                version=row["version"], owner=row["owner"] or "", status=FeatureStatus(row["status"]),
            )
        except Exception as exc:
            raise DataStoreError(
                f"feature registry row is invalid: {(row['feature_id'], row['version'])}"
            ) from exc

    def get(self, feature_id: str, version: str) -> FeatureRegistryItem:
        with self._lock:
            self._verify_history_unlocked()
            row = self._conn.execute(
                "SELECT * FROM feature_registry WHERE feature_id=? AND version=?",
                (feature_id, version),
            ).fetchone()
        if row is None:
            raise KeyError((feature_id, version))
        return self._row_to_item(row)

    def has(self, feature_id: str) -> bool:
        with self._lock:
            self._verify_history_unlocked()
            return self._conn.execute(
                "SELECT 1 FROM feature_registry WHERE feature_id=? LIMIT 1", (feature_id,)
            ).fetchone() is not None

    def versions(self, feature_id: str) -> list[str]:
        with self._lock:
            self._verify_history_unlocked()
            rows = self._conn.execute(
                "SELECT version FROM feature_registry WHERE feature_id=? ORDER BY version", (feature_id,)
            ).fetchall()
        return [str(row["version"]) for row in rows]

    def all_items(self) -> list[FeatureRegistryItem]:
        """All current rows, history-verified, ordered by (feature_id, version).

        Additive read for the Factor Garden's by_family scan (F1, 2026-07-27);
        same fail-closed verification posture as every other read.
        """
        with self._lock:
            self._verify_history_unlocked()
            rows = self._conn.execute(
                "SELECT * FROM feature_registry ORDER BY feature_id, version"
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def set_status(
        self,
        feature_id: str,
        version: str,
        status: FeatureStatus,
        *,
        recorded_at_ms: int = 0,
    ) -> FeatureRegistryItem:
        """Record a status transition while retaining the immutable definition."""
        if not isinstance(status, FeatureStatus):
            raise TypeError("status must be a FeatureStatus")
        recorded_at_ms = _validate_recorded_at(recorded_at_ms)
        with self._lock:
            self._verify_history_unlocked()
            row = self._conn.execute(
                "SELECT * FROM feature_registry WHERE feature_id=? AND version=?",
                (feature_id, version),
            ).fetchone()
            if row is None:
                raise KeyError((feature_id, version))
            if row["status"] == status.value:
                return self._row_to_item(row)
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "UPDATE feature_registry SET status=? WHERE feature_id=? AND version=?",
                    (status.value, feature_id, version),
                )
                latest = self._conn.execute(
                    "SELECT * FROM feature_registry WHERE feature_id=? AND version=?",
                    (feature_id, version),
                ).fetchone()
                self._append_history_unlocked(
                    feature_id=feature_id, version=version, operation="STATUS",
                    owner=latest["owner"] or "", status=status,
                    definition_json=latest["definition_json"],
                    leakage_passed=(None if latest["leakage_passed"] is None else bool(latest["leakage_passed"])),
                    code_version=latest["code_version"], metadata_json=_canonical({}),
                    recorded_at_ms=recorded_at_ms,
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
        return self.get(feature_id, version)

    def audit(self, feature_id: str, version: str) -> dict:
        """Return leakage/code metadata plus the verified append-only history."""
        with self._lock:
            latest = self._verify_history_unlocked()
            row = self._conn.execute(
                "SELECT leakage_passed, code_version, definition_json FROM feature_registry "
                "WHERE feature_id=? AND version=?", (feature_id, version)
            ).fetchone()
            if row is None:
                raise KeyError((feature_id, version))
            events = []
            for event in self._history_rows_unlocked(feature_id, version):
                events.append({
                    "seq": int(event["seq"]),
                    "operation": event["operation"],
                    "status": event["status"],
                    "recorded_at_ms": int(event["recorded_at_ms"]),
                    "metadata": json.loads(event["metadata_json"]),
                    "record_hash": event["record_hash"],
                })
            event = latest.get((feature_id, version))
            if event is None:
                raise DataStoreError(f"feature registry row has no history: {(feature_id, version)}")
            return {
                "leakage_passed": None if row["leakage_passed"] is None else bool(row["leakage_passed"]),
                "code_version": row["code_version"],
                "definition_sha256": _definition_hash(row["definition_json"]),
                "history": events,
            }
