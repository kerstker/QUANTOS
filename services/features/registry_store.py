"""Durable sqlite3 persistence for the feature registry (Rank #15).

The in-memory `FeatureRegistry` loses every registration on restart and the console admits it
("no persisted FeatureRegistry yet"). This store mirrors the run_registry sqlite pattern
(threading.Lock + CREATE TABLE IF NOT EXISTS + parent-dir autocreate; time is never taken here)
and preserves the same facade (register/get/has/versions/set_status). Each version additionally
carries a `leakage_passed` flag + `code_version`, so a cached/materialized feature is auditable
back to the leakage verdict and the code that produced it. Pure stdlib; fail-closed on an
uninitialized/corrupt store (never reads as empty)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Optional

from quantos.contracts.feature import (
    FeatureDefinition, FeatureRegistryItem, FeatureStatus)
from services.data.persistent_store import DataStoreError

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
"""


class SqliteFeatureRegistryStore:
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
                "SELECT name FROM sqlite_master WHERE type='table' AND name='feature_registry'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise DataStoreError(f"feature registry unreadable: {exc}") from exc
        if not rows:
            raise DataStoreError("feature registry uninitialized; missing 'feature_registry' table")

    def register(self, item: FeatureRegistryItem, *, leakage_passed: Optional[bool] = None,
                 code_version: Optional[str] = None) -> None:
        """Append-only registration; a duplicate (feature_id, version) raises (mirrors the in-memory facade)."""
        with self._lock:
            self._require_schema()
            key = (item.definition.feature_id, item.version)
            if self._conn.execute("SELECT 1 FROM feature_registry WHERE feature_id=? AND version=?",
                                  key).fetchone():
                raise ValueError(f"already registered: {key}")
            self._conn.execute(
                "INSERT INTO feature_registry(feature_id, version, owner, status, definition_json,"
                " leakage_passed, code_version) VALUES (?,?,?,?,?,?,?)",
                (item.definition.feature_id, item.version, item.owner, item.status.value,
                 item.definition.model_dump_json(),
                 None if leakage_passed is None else int(bool(leakage_passed)), code_version))
            self._conn.commit()

    def _row_to_item(self, r) -> FeatureRegistryItem:
        return FeatureRegistryItem(
            definition=FeatureDefinition.model_validate_json(r["definition_json"]),
            version=r["version"], owner=r["owner"] or "", status=FeatureStatus(r["status"]))

    def get(self, feature_id: str, version: str) -> FeatureRegistryItem:
        with self._lock:
            self._require_schema()
            r = self._conn.execute("SELECT * FROM feature_registry WHERE feature_id=? AND version=?",
                                   (feature_id, version)).fetchone()
        if r is None:
            raise KeyError((feature_id, version))
        return self._row_to_item(r)

    def has(self, feature_id: str) -> bool:
        with self._lock:
            self._require_schema()
            return self._conn.execute("SELECT 1 FROM feature_registry WHERE feature_id=? LIMIT 1",
                                      (feature_id,)).fetchone() is not None

    def versions(self, feature_id: str) -> list[str]:
        with self._lock:
            self._require_schema()
            rows = self._conn.execute(
                "SELECT version FROM feature_registry WHERE feature_id=? ORDER BY version",
                (feature_id,)).fetchall()
        return [r["version"] for r in rows]

    def set_status(self, feature_id: str, version: str, status: FeatureStatus) -> FeatureRegistryItem:
        with self._lock:
            self._require_schema()
            cur = self._conn.execute("UPDATE feature_registry SET status=? WHERE feature_id=? AND version=?",
                                     (status.value, feature_id, version))
            self._conn.commit()
            if cur.rowcount == 0:
                raise KeyError((feature_id, version))
        return self.get(feature_id, version)

    def audit(self, feature_id: str, version: str) -> dict:
        """The leakage verdict + code version stamped at registration (Rank #15 auditability)."""
        with self._lock:
            self._require_schema()
            r = self._conn.execute(
                "SELECT leakage_passed, code_version FROM feature_registry WHERE feature_id=? AND version=?",
                (feature_id, version)).fetchone()
        if r is None:
            raise KeyError((feature_id, version))
        lp = r["leakage_passed"]
        return {"leakage_passed": None if lp is None else bool(lp), "code_version": r["code_version"]}

    def items(self) -> list[FeatureRegistryItem]:
        """List registered feature versions in stable order for read-only consumers."""
        with self._lock:
            self._require_schema()
            rows = self._conn.execute(
                "SELECT * FROM feature_registry ORDER BY feature_id, version"
            ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
