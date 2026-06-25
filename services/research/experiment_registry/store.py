"""Stdlib-sqlite3 store for the experiment registry (persistence layer).

Owns the schema and ALL SQL. No domain logic, no imports from services/alpha.
Append-only / write-once: trials dedup by PK, evaluations are immutable, alpha
state is upserted. Fail-closed: reading an uninitialized/corrupt store raises
ExperimentStoreError rather than reading as empty (an empty trial population
would un-deflate Deflated Sharpe)."""
from __future__ import annotations

import sqlite3
from typing import Iterable, Optional


class ExperimentStoreError(Exception):
    """The store is missing, corrupt, or uninitialized — fail closed."""


class ImmutableArtifactError(Exception):
    """Attempt to overwrite a recorded evaluation with different content."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
    config_hash TEXT PRIMARY KEY,
    first_seen_ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alpha_state (
    alpha_id TEXT PRIMARY KEY,
    definition_json TEXT NOT NULL,
    state TEXT NOT NULL,
    returns_json TEXT NOT NULL,
    sharpe REAL,
    pvalue REAL
);
CREATE TABLE IF NOT EXISTS evaluations (
    run_id TEXT PRIMARY KEY,
    alpha_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    recorded_ts INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    evaluation_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bandit_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    snapshot_json TEXT NOT NULL
);
"""

_TABLES = ("trials", "alpha_state", "evaluations")


class SqliteExperimentStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise ExperimentStoreError(f"store unreadable: {exc}") from exc
        present = {r["name"] for r in rows}
        missing = [t for t in _TABLES if t not in present]
        if missing:
            raise ExperimentStoreError(f"store uninitialized; missing tables: {missing}")

    def add_trials(self, config_hashes: Iterable[str], *, first_seen_ts: int) -> None:
        self._require_schema()
        self._conn.executemany(
            "INSERT OR IGNORE INTO trials(config_hash, first_seen_ts) VALUES (?, ?)",
            [(h, first_seen_ts) for h in config_hashes],
        )
        self._conn.commit()

    def trial_count(self) -> int:
        self._require_schema()
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM trials").fetchone()["n"])

    def all_trial_hashes(self) -> list[str]:
        self._require_schema()
        return [r["config_hash"] for r in self._conn.execute("SELECT config_hash FROM trials")]

    def upsert_alpha(self, alpha_id: str, definition_json: str, state: str,
                     returns_json: str, sharpe: Optional[float], pvalue: Optional[float]) -> None:
        self._require_schema()
        self._conn.execute(
            "INSERT INTO alpha_state(alpha_id, definition_json, state, returns_json, sharpe, pvalue) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(alpha_id) DO UPDATE SET definition_json=excluded.definition_json, "
            "state=excluded.state, returns_json=excluded.returns_json, sharpe=excluded.sharpe, "
            "pvalue=excluded.pvalue",
            (alpha_id, definition_json, state, returns_json, sharpe, pvalue),
        )
        self._conn.commit()

    def all_alphas(self) -> list[dict]:
        self._require_schema()
        return [dict(r) for r in self._conn.execute(
            "SELECT alpha_id, definition_json, state, returns_json, sharpe, pvalue "
            "FROM alpha_state ORDER BY alpha_id")]

    def save_bandit(self, snapshot_json: str) -> None:
        self._require_schema()
        self._conn.execute("CREATE TABLE IF NOT EXISTS bandit_state "
                           "(id INTEGER PRIMARY KEY CHECK (id = 1), snapshot_json TEXT NOT NULL)")
        self._conn.execute("INSERT OR REPLACE INTO bandit_state(id, snapshot_json) VALUES (1, ?)",
                           (snapshot_json,))
        self._conn.commit()

    def load_bandit(self):
        self._require_schema()
        self._conn.execute("CREATE TABLE IF NOT EXISTS bandit_state "
                           "(id INTEGER PRIMARY KEY CHECK (id = 1), snapshot_json TEXT NOT NULL)")
        row = self._conn.execute("SELECT snapshot_json FROM bandit_state WHERE id = 1").fetchone()
        return row["snapshot_json"] if row is not None else None

    def insert_evaluation(self, *, run_id: str, alpha_id: str, dataset_version: str,
                          recorded_ts: int, verdict: str, content_hash: str,
                          evaluation_json: str) -> bool:
        """Returns True if a new row was inserted, False on an idempotent no-op
        re-record (same run_id + same content). Raises on a conflicting content."""
        self._require_schema()
        existing = self.get_evaluation(run_id)
        if existing is not None:
            if existing["content_hash"] == content_hash:
                return False                             # idempotent re-record (no-op)
            raise ImmutableArtifactError(
                f"run_id {run_id} already recorded with a different content_hash")
        self._conn.execute(
            "INSERT INTO evaluations(run_id, alpha_id, dataset_version, recorded_ts, "
            "verdict, content_hash, evaluation_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, alpha_id, dataset_version, recorded_ts, verdict, content_hash, evaluation_json),
        )
        self._conn.commit()
        return True

    def get_evaluation(self, run_id: str) -> Optional[dict]:
        self._require_schema()
        row = self._conn.execute(
            "SELECT * FROM evaluations WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row is not None else None

    def list_evaluations(self, alpha_id: str) -> list[dict]:
        self._require_schema()
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM evaluations WHERE alpha_id = ? ORDER BY recorded_ts, run_id", (alpha_id,))]
