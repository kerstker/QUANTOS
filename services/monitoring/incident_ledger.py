"""Append-only incident ledger (Rank #27).

A durable, append-only sqlite record of operational incidents (kill-switch trips, data-quality
halts, governance escalations) mirroring the run_registry / audit_store persistence pattern
(threading.Lock + CREATE TABLE IF NOT EXISTS + parent-dir autocreate; time is caller-injected, no
wall-clock). An incident is OPENED once (immutable id) and later RESOLVED (a controlled status +
resolution note) — it is never deleted or its history rewritten. Fail-closed on an uninitialized/
corrupt store. This is the real backing for the console incident page (was synthetic); real RBAC /
signed committee sign-off remain POST-L2 (need an identity layer)."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from typing import Optional

from services.data.persistent_store import DataStoreError

_SEVERITIES = ("INFO", "WARN", "CRITICAL")
_OPEN, _RESOLVED = "OPEN", "RESOLVED"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id    TEXT UNIQUE,
    severity       TEXT NOT NULL,
    kind           TEXT NOT NULL,
    description    TEXT,
    opened_by      TEXT,
    opened_ts      INTEGER NOT NULL,
    status         TEXT NOT NULL,
    resolved_by    TEXT,
    resolved_ts    INTEGER,
    resolution     TEXT,
    metadata_json  TEXT
);
CREATE INDEX IF NOT EXISTS ix_incident_open ON incidents (status, opened_ts);
"""


class IncidentLedger:
    def __init__(self, db_path: str, *, create: bool = True):
        if create and db_path != ":memory:":
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._c = sqlite3.connect(db_path, check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        if create:
            with self._lock:
                self._c.executescript(_SCHEMA)
                self._c.commit()

    def _require_schema(self) -> None:
        try:
            rows = self._c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='incidents'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise DataStoreError(f"incident ledger unreadable: {exc}") from exc
        if not rows:
            raise DataStoreError("incident ledger uninitialized; missing 'incidents' table")

    def open(self, *, incident_id: str, severity: str, kind: str, opened_by: str, opened_ts: int,
             description: str = "", metadata: Optional[dict] = None) -> None:
        if severity not in _SEVERITIES:
            raise ValueError(f"severity must be one of {_SEVERITIES}")
        with self._lock:
            self._require_schema()
            if self._c.execute("SELECT 1 FROM incidents WHERE incident_id=?", (incident_id,)).fetchone():
                raise ValueError(f"incident already opened: {incident_id}")
            self._c.execute(
                "INSERT INTO incidents(incident_id, severity, kind, description, opened_by, opened_ts,"
                " status, metadata_json) VALUES (?,?,?,?,?,?,?,?)",
                (incident_id, severity, kind, description, opened_by, opened_ts, _OPEN,
                 json.dumps(metadata or {}, sort_keys=True)))
            self._c.commit()

    def resolve(self, incident_id: str, *, resolved_by: str, resolved_ts: int, resolution: str = "") -> None:
        with self._lock:
            self._require_schema()
            cur = self._c.execute(
                "UPDATE incidents SET status=?, resolved_by=?, resolved_ts=?, resolution=? "
                "WHERE incident_id=? AND status=?",
                (_RESOLVED, resolved_by, resolved_ts, resolution, incident_id, _OPEN))
            self._c.commit()
            if cur.rowcount == 0:
                raise ValueError(f"no OPEN incident {incident_id!r} to resolve")

    def get(self, incident_id: str) -> Optional[dict]:
        with self._lock:
            self._require_schema()
            r = self._c.execute("SELECT * FROM incidents WHERE incident_id=?", (incident_id,)).fetchone()
        return dict(r) if r else None

    def list(self, *, status: Optional[str] = None, limit: int = 200) -> list:
        q = "SELECT * FROM incidents"
        args: tuple = ()
        if status is not None:
            q += " WHERE status=?"
            args = (status,)
        q += " ORDER BY opened_ts DESC, seq DESC LIMIT ?"
        with self._lock:
            self._require_schema()
            rows = self._c.execute(q, args + (limit,)).fetchall()
        return [dict(r) for r in rows]

    def open_count(self) -> int:
        with self._lock:
            self._require_schema()
            return int(self._c.execute("SELECT COUNT(*) AS n FROM incidents WHERE status=?", (_OPEN,)).fetchone()["n"])
