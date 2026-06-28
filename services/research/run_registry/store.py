"""ResearchRunStore — durable sqlite3 persistence for research runs (one row per run).

Flat columns for the queryable fields (kind/status/created_ts for listing); the nested fields
(params/metrics/series/caveats) live in a single JSON payload column. Pure stdlib; mirrors the
experiment_registry persistence pattern. Deterministic (no wall-clock here)."""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional

from .contract import ResearchRun

_COLS = ("run_id", "kind", "title", "status", "created_ts", "finished_ts",
         "verdict", "provenance", "error", "payload_json")


class ResearchRunStore:
    """Thread-safe: the async research_runner writes from worker threads while reads come from the API
    thread, so the connection allows cross-thread use and a lock serialises access (sqlite single-writer)."""

    def __init__(self, db_path: str, create: bool = True):
        self._c = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        if create:
            with self._lock:
                self._c.execute(
                    "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, kind TEXT, title TEXT,"
                    " status TEXT, created_ts INTEGER, finished_ts INTEGER, verdict TEXT,"
                    " provenance TEXT, error TEXT, payload_json TEXT)")
                self._c.execute("CREATE INDEX IF NOT EXISTS ix_runs_created ON runs(created_ts)")
                self._c.commit()

    def upsert(self, r: ResearchRun) -> None:
        payload = json.dumps({"params": r.params, "metrics": r.metrics, "series": r.series,
                              "caveats": r.caveats, "log": r.log, "code_version": r.code_version})
        with self._lock:
            self._c.execute(
                "INSERT OR REPLACE INTO runs (run_id,kind,title,status,created_ts,finished_ts,verdict,"
                "provenance,error,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (r.run_id, r.kind, r.title, r.status, r.created_ts, r.finished_ts, r.verdict,
                 r.provenance, r.error, payload))
            self._c.commit()

    def _row(self, row) -> ResearchRun:
        p = json.loads(row[9])
        return ResearchRun(run_id=row[0], kind=row[1], title=row[2], status=row[3], created_ts=row[4],
                           finished_ts=row[5], verdict=row[6], provenance=row[7], error=row[8],
                           params=p["params"], metrics=p["metrics"], series=p["series"], caveats=p["caveats"],
                           log=p.get("log", []), code_version=p.get("code_version"))

    def append_log(self, run_id: str, line: str) -> None:
        """Append one progress/log line to a run, live (read-modify-write under the lock)."""
        with self._lock:
            row = self._c.execute("SELECT payload_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                return
            p = json.loads(row[0])
            p.setdefault("log", []).append(line)
            self._c.execute("UPDATE runs SET payload_json=? WHERE run_id=?", (json.dumps(p), run_id))
            self._c.commit()

    def get(self, run_id: str) -> Optional[ResearchRun]:
        with self._lock:
            r = self._c.execute(f"SELECT {','.join(_COLS)} FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return self._row(r) if r else None

    def list(self, kind: Optional[str] = None, limit: int = 200) -> list:
        q = f"SELECT {','.join(_COLS)} FROM runs"
        args: tuple = ()
        if kind:
            q += " WHERE kind=?"
            args = (kind,)
        q += " ORDER BY created_ts DESC LIMIT ?"
        with self._lock:
            rows = self._c.execute(q, args + (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def set_status(self, run_id: str, status: str, error: Optional[str] = None) -> None:
        with self._lock:
            self._c.execute("UPDATE runs SET status=?, error=? WHERE run_id=?", (status, error, run_id))
            self._c.commit()
