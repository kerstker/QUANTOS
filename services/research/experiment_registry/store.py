"""Stdlib-sqlite3 store for the experiment registry (persistence layer).

Owns the schema and ALL SQL. No domain logic, no imports from services/alpha.
Append-only / write-once: trials dedup by PK, evaluations are immutable, alpha
state is upserted. Fail-closed: reading an uninitialized/corrupt store raises
ExperimentStoreError rather than reading as empty (an empty trial population
would un-deflate Deflated Sharpe)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from typing import Iterable, Mapping, Optional, Sequence

from quantos.contracts.events import Event, EventOutboxMessage

from services.research.ports.persistence import (
    EvaluationAuditIntent,
    ExperimentStoreError,
    ImmutableArtifactError,
)


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
CREATE TABLE IF NOT EXISTS experiment_event_outbox (
    outbox_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    outbox_id TEXT UNIQUE NOT NULL,
    event_json TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    causation_id TEXT NOT NULL,
    repro_hash TEXT NOT NULL,
    prev_record_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiment_event_dispatch (
    dispatch_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    outbox_id TEXT UNIQUE NOT NULL,
    dispatched_at INTEGER NOT NULL,
    event_repro_hash TEXT NOT NULL,
    prev_record_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS experiment_event_outbox_no_update
BEFORE UPDATE ON experiment_event_outbox
BEGIN SELECT RAISE(ABORT, 'experiment_event_outbox is append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_event_outbox_no_delete
BEFORE DELETE ON experiment_event_outbox
BEGIN SELECT RAISE(ABORT, 'experiment_event_outbox is append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_event_dispatch_no_update
BEFORE UPDATE ON experiment_event_dispatch
BEGIN SELECT RAISE(ABORT, 'experiment_event_dispatch is append-only'); END;
CREATE TRIGGER IF NOT EXISTS experiment_event_dispatch_no_delete
BEFORE DELETE ON experiment_event_dispatch
BEGIN SELECT RAISE(ABORT, 'experiment_event_dispatch is append-only'); END;
"""

_TABLES = ("trials", "alpha_state", "evaluations")
_OUTBOX_TABLES = ("experiment_event_outbox", "experiment_event_dispatch")
_SEARCH_TABLES = ("search_attempt_events", "search_candidates")
_SEARCH_ACTIVITY_TABLES = (
    "search_attempt_heartbeats",
    "search_attempt_recoveries",
    "search_evaluations",
)
_EVALUATION_AUDIT_TABLES = ("evaluation_audit_outbox", "evaluation_audit_dispatch")
_SEARCH_TERMINAL_STATUSES = frozenset({"FAILED", "COMPLETED"})
_SEARCH_RECOVERY_REASON = "STALE_STARTED_TIMEOUT"
_SEARCH_SCHEMA_VERSION = 2
_SEARCH_APPEND_ONLY_TRIGGERS = {
    "search_attempt_events_no_update": ("search_attempt_events", "update"),
    "search_attempt_events_no_delete": ("search_attempt_events", "delete"),
    "search_candidates_no_update": ("search_candidates", "update"),
    "search_candidates_no_delete": ("search_candidates", "delete"),
    "trials_no_update": ("trials", "update"),
    "trials_no_delete": ("trials", "delete"),
    "evaluations_no_update": ("evaluations", "update"),
    "evaluations_no_delete": ("evaluations", "delete"),
    "search_attempt_heartbeats_no_update": ("search_attempt_heartbeats", "update"),
    "search_attempt_heartbeats_no_delete": ("search_attempt_heartbeats", "delete"),
    "search_attempt_recoveries_no_update": ("search_attempt_recoveries", "update"),
    "search_attempt_recoveries_no_delete": ("search_attempt_recoveries", "delete"),
    "search_evaluations_no_update": ("search_evaluations", "update"),
    "search_evaluations_no_delete": ("search_evaluations", "delete"),
    "evaluation_audit_outbox_no_update": ("evaluation_audit_outbox", "update"),
    "evaluation_audit_outbox_no_delete": ("evaluation_audit_outbox", "delete"),
    "evaluation_audit_dispatch_no_update": ("evaluation_audit_dispatch", "update"),
    "evaluation_audit_dispatch_no_delete": ("evaluation_audit_dispatch", "delete"),
}

_SEARCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS search_attempt_events (
    event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id TEXT NOT NULL,
    search_kind TEXT NOT NULL,
    source TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('STARTED', 'FAILED', 'COMPLETED')),
    recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0),
    prev_event_hash TEXT NOT NULL,
    event_hash TEXT UNIQUE NOT NULL
);
CREATE INDEX IF NOT EXISTS search_attempt_events_by_search
ON search_attempt_events(search_id, event_seq);
CREATE TABLE IF NOT EXISTS search_candidates (
    candidate_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status = 'REGISTERED'),
    registered_at INTEGER NOT NULL CHECK(registered_at >= 0),
    prev_candidate_hash TEXT NOT NULL,
    candidate_hash TEXT UNIQUE NOT NULL,
    UNIQUE(search_id, config_hash)
);
CREATE INDEX IF NOT EXISTS search_candidates_by_search
ON search_candidates(search_id, candidate_seq);
CREATE TABLE IF NOT EXISTS search_attempt_heartbeats (
    heartbeat_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id TEXT NOT NULL,
    recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0),
    prev_heartbeat_hash TEXT NOT NULL,
    heartbeat_hash TEXT UNIQUE NOT NULL,
    UNIQUE(search_id, recorded_at)
);
CREATE INDEX IF NOT EXISTS search_attempt_heartbeats_by_search
ON search_attempt_heartbeats(search_id, heartbeat_seq);
CREATE TABLE IF NOT EXISTS search_attempt_recoveries (
    recovery_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id TEXT UNIQUE NOT NULL,
    reason TEXT NOT NULL CHECK(reason = 'STALE_STARTED_TIMEOUT'),
    timeout_ms INTEGER NOT NULL CHECK(timeout_ms > 0),
    last_activity_at INTEGER NOT NULL CHECK(last_activity_at >= 0),
    recovered_at INTEGER NOT NULL CHECK(recovered_at >= 0),
    terminal_event_hash TEXT UNIQUE NOT NULL,
    prev_recovery_hash TEXT NOT NULL,
    recovery_hash TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS search_evaluations (
    link_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    run_id TEXT NOT NULL,
    recorded_at INTEGER NOT NULL CHECK(recorded_at >= 0),
    evaluation_content_hash TEXT NOT NULL,
    prev_link_hash TEXT NOT NULL,
    link_hash TEXT UNIQUE NOT NULL,
    UNIQUE(search_id, run_id)
);
CREATE INDEX IF NOT EXISTS search_evaluations_by_search
ON search_evaluations(search_id, link_seq);
CREATE TABLE IF NOT EXISTS evaluation_audit_outbox (
    audit_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT UNIQUE NOT NULL,
    event_id TEXT UNIQUE NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    timestamp INTEGER NOT NULL CHECK(timestamp >= 0),
    target TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    evaluation_content_hash TEXT NOT NULL,
    prev_record_hash TEXT NOT NULL,
    record_hash TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluation_audit_dispatch (
    dispatch_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    dispatched_at INTEGER NOT NULL CHECK(dispatched_at >= 0),
    audit_entry_hash TEXT NOT NULL,
    prev_record_hash TEXT NOT NULL,
    record_hash TEXT UNIQUE NOT NULL
);
CREATE TRIGGER IF NOT EXISTS search_attempt_events_no_update
BEFORE UPDATE ON search_attempt_events
BEGIN SELECT RAISE(ABORT, 'search_attempt_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS search_attempt_events_no_delete
BEFORE DELETE ON search_attempt_events
BEGIN SELECT RAISE(ABORT, 'search_attempt_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS search_candidates_no_update
BEFORE UPDATE ON search_candidates
BEGIN SELECT RAISE(ABORT, 'search_candidates is append-only'); END;
CREATE TRIGGER IF NOT EXISTS search_candidates_no_delete
BEFORE DELETE ON search_candidates
BEGIN SELECT RAISE(ABORT, 'search_candidates is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trials_no_update
BEFORE UPDATE ON trials
BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trials_no_delete
BEFORE DELETE ON trials
BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_update
BEFORE UPDATE ON evaluations
BEGIN SELECT RAISE(ABORT, 'evaluations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evaluations_no_delete
BEFORE DELETE ON evaluations
BEGIN SELECT RAISE(ABORT, 'evaluations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS search_attempt_heartbeats_no_update
BEFORE UPDATE ON search_attempt_heartbeats
BEGIN SELECT RAISE(ABORT, 'search_attempt_heartbeats is append-only'); END;
CREATE TRIGGER IF NOT EXISTS search_attempt_heartbeats_no_delete
BEFORE DELETE ON search_attempt_heartbeats
BEGIN SELECT RAISE(ABORT, 'search_attempt_heartbeats is append-only'); END;
CREATE TRIGGER IF NOT EXISTS search_attempt_recoveries_no_update
BEFORE UPDATE ON search_attempt_recoveries
BEGIN SELECT RAISE(ABORT, 'search_attempt_recoveries is append-only'); END;
CREATE TRIGGER IF NOT EXISTS search_attempt_recoveries_no_delete
BEFORE DELETE ON search_attempt_recoveries
BEGIN SELECT RAISE(ABORT, 'search_attempt_recoveries is append-only'); END;
CREATE TRIGGER IF NOT EXISTS search_evaluations_no_update
BEFORE UPDATE ON search_evaluations
BEGIN SELECT RAISE(ABORT, 'search_evaluations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS search_evaluations_no_delete
BEFORE DELETE ON search_evaluations
BEGIN SELECT RAISE(ABORT, 'search_evaluations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evaluation_audit_outbox_no_update
BEFORE UPDATE ON evaluation_audit_outbox
BEGIN SELECT RAISE(ABORT, 'evaluation_audit_outbox is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evaluation_audit_outbox_no_delete
BEFORE DELETE ON evaluation_audit_outbox
BEGIN SELECT RAISE(ABORT, 'evaluation_audit_outbox is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evaluation_audit_dispatch_no_update
BEFORE UPDATE ON evaluation_audit_dispatch
BEGIN SELECT RAISE(ABORT, 'evaluation_audit_dispatch is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evaluation_audit_dispatch_no_delete
BEFORE DELETE ON evaluation_audit_dispatch
BEGIN SELECT RAISE(ABORT, 'evaluation_audit_dispatch is append-only'); END;
"""


def _canonical_hash(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _search_event_hash(
    *,
    search_id: str,
    search_kind: str,
    source: str,
    dataset_version: str,
    status: str,
    recorded_at: int,
    prev_event_hash: str,
) -> str:
    return _canonical_hash(
        {
            "dataset_version": dataset_version,
            "prev_event_hash": prev_event_hash,
            "recorded_at": recorded_at,
            "search_id": search_id,
            "search_kind": search_kind,
            "source": source,
            "status": status,
        }
    )


def _search_identity_hash(
    *,
    search_id: str,
    search_kind: str,
    source: str,
    dataset_version: str,
) -> str:
    """Return the stable identity of a search attempt.

    The identity deliberately excludes mutable lifecycle fields (status,
    candidate count, and terminal timestamps).  It is derived from the
    immutable STARTED metadata and can therefore be recomputed in a later
    process without adding a second mutable authority to the ledger.
    """
    return _canonical_hash(
        {
            "dataset_version": dataset_version,
            "search_id": search_id,
            "search_kind": search_kind,
            "source": source,
        }
    )


def _search_candidate_hash(
    *,
    search_id: str,
    config_hash: str,
    status: str,
    registered_at: int,
    prev_candidate_hash: str,
) -> str:
    return _canonical_hash(
        {
            "config_hash": config_hash,
            "prev_candidate_hash": prev_candidate_hash,
            "registered_at": registered_at,
            "search_id": search_id,
            "status": status,
        }
    )


def _search_heartbeat_hash(
    *,
    search_id: str,
    recorded_at: int,
    prev_heartbeat_hash: str,
) -> str:
    return _canonical_hash(
        {
            "prev_heartbeat_hash": prev_heartbeat_hash,
            "recorded_at": recorded_at,
            "search_id": search_id,
        }
    )


def _search_recovery_hash(
    *,
    search_id: str,
    reason: str,
    timeout_ms: int,
    last_activity_at: int,
    recovered_at: int,
    terminal_event_hash: str,
    prev_recovery_hash: str,
) -> str:
    return _canonical_hash(
        {
            "last_activity_at": last_activity_at,
            "prev_recovery_hash": prev_recovery_hash,
            "reason": reason,
            "recovered_at": recovered_at,
            "search_id": search_id,
            "terminal_event_hash": terminal_event_hash,
            "timeout_ms": timeout_ms,
        }
    )


def _search_evaluation_hash(
    *,
    search_id: str,
    config_hash: str,
    run_id: str,
    recorded_at: int,
    evaluation_content_hash: str,
    prev_link_hash: str,
) -> str:
    return _canonical_hash(
        {
            "config_hash": config_hash,
            "evaluation_content_hash": evaluation_content_hash,
            "prev_link_hash": prev_link_hash,
            "recorded_at": recorded_at,
            "run_id": run_id,
            "search_id": search_id,
        }
    )


def _evaluation_audit_hash(
    *,
    run_id: str,
    intent: EvaluationAuditIntent,
    evaluation_content_hash: str,
    prev_record_hash: str,
) -> str:
    return _canonical_hash(
        {
            "action": intent.action,
            "actor": intent.actor,
            "evaluation_content_hash": evaluation_content_hash,
            "event_id": intent.event_id,
            "metadata": dict(intent.metadata),
            "prev_record_hash": prev_record_hash,
            "run_id": run_id,
            "target": intent.target,
            "timestamp": intent.timestamp,
        }
    )


def _evaluation_audit_dispatch_hash(
    *,
    event_id: str,
    dispatched_at: int,
    audit_entry_hash: str,
    prev_record_hash: str,
) -> str:
    return _canonical_hash(
        {
            "audit_entry_hash": audit_entry_hash,
            "dispatched_at": dispatched_at,
            "event_id": event_id,
            "prev_record_hash": prev_record_hash,
        }
    )


def _event_json(event: Event) -> str:
    return json.dumps(
        event.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _outbox_hash(message: EventOutboxMessage, prev_record_hash: str) -> str:
    canonical = json.dumps(
        {
            "causation_id": message.causation_id,
            "correlation_id": message.correlation_id,
            "event": message.event.model_dump(mode="json"),
            "outbox_id": message.outbox_id,
            "prev_record_hash": prev_record_hash,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _dispatch_hash(
    *,
    outbox_id: str,
    dispatched_at: int,
    event_repro_hash: str,
    prev_record_hash: str,
) -> str:
    canonical = json.dumps(
        {
            "dispatched_at": dispatched_at,
            "event_repro_hash": event_repro_hash,
            "outbox_id": outbox_id,
            "prev_record_hash": prev_record_hash,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class SqliteExperimentStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        self._conn = sqlite3.connect(path, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        if create:
            self._conn.executescript(_SCHEMA)
            self._conn.executescript(_SEARCH_SCHEMA)
            self._conn.execute(f"PRAGMA user_version = {_SEARCH_SCHEMA_VERSION}")
            self._conn.commit()

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise ExperimentStoreError(f"store unreadable: {exc}") from exc
        present = {r["name"] for r in rows}
        missing = [t for t in _TABLES if t not in present]
        if missing:
            raise ExperimentStoreError(f"store uninitialized; missing tables: {missing}")

    def _ensure_search_schema(self) -> None:
        """Migrate only an already-valid experiment store on the first search write."""
        if self._conn.in_transaction:
            self._conn.rollback()
        self._require_schema()
        try:
            current_version = int(
                self._conn.execute("PRAGMA user_version").fetchone()[0]
            )
            if current_version > _SEARCH_SCHEMA_VERSION:
                raise ExperimentStoreError(
                    f"unsupported experiment store user_version: {current_version}"
                )
            if current_version == _SEARCH_SCHEMA_VERSION:
                self._require_search_schema()
                return

            self._conn.execute("BEGIN IMMEDIATE")
            locked_version = int(
                self._conn.execute("PRAGMA user_version").fetchone()[0]
            )
            if locked_version == _SEARCH_SCHEMA_VERSION:
                self._conn.commit()
                self._require_search_schema()
                return
            if locked_version > _SEARCH_SCHEMA_VERSION:
                raise ExperimentStoreError(
                    f"unsupported experiment store user_version: {locked_version}"
                )

            statement = ""
            statement_index = 0
            for line in _SEARCH_SCHEMA.splitlines():
                statement = f"{statement}{line}\n"
                if not sqlite3.complete_statement(statement):
                    continue
                sql = statement.strip()
                statement = ""
                if not sql:
                    continue
                self._conn.execute(sql)
                statement_index += 1
                self._migration_checkpoint(f"statement:{statement_index}")
            if statement.strip():
                raise ExperimentStoreError(
                    "search provenance schema contains an incomplete statement"
                )
            self._conn.execute(f"PRAGMA user_version = {_SEARCH_SCHEMA_VERSION}")
            self._migration_checkpoint("before_commit")
            self._require_search_schema()
            self._conn.commit()
        except ExperimentStoreError:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        except Exception as exc:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise ExperimentStoreError(
                f"search provenance schema migration failed: {exc}"
            ) from exc

    def _migration_checkpoint(self, stage: str) -> None:
        """Test seam for proving that every partial migration rolls back."""
        del stage

    def _require_search_schema(self) -> None:
        self._require_schema()
        try:
            rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise ExperimentStoreError(f"store unreadable: {exc}") from exc
        present = {row["name"] for row in rows}
        required_tables = (
            _SEARCH_TABLES + _SEARCH_ACTIVITY_TABLES + _EVALUATION_AUDIT_TABLES
        )
        missing = [table for table in required_tables if table not in present]
        if missing:
            raise ExperimentStoreError(
                f"search provenance unavailable; missing tables: {missing}"
            )
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version < _SEARCH_SCHEMA_VERSION:
            raise ExperimentStoreError(
                f"search provenance user_version is stale: {version}"
            )
        link_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(search_evaluations)")
        }
        required_link_columns = {
            "link_seq",
            "search_id",
            "config_hash",
            "run_id",
            "recorded_at",
            "evaluation_content_hash",
            "prev_link_hash",
            "link_hash",
        }
        missing_link_columns = sorted(required_link_columns - link_columns)
        if missing_link_columns:
            raise ExperimentStoreError(
                "search evaluation binding schema is stale; missing columns: "
                f"{missing_link_columns}"
            )
        triggers = {
            row["name"]: (row["sql"] or "").lower()
            for row in self._conn.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
            )
        }
        for name, (table, action) in _SEARCH_APPEND_ONLY_TRIGGERS.items():
            sql = triggers.get(name, "")
            if (
                f"before {action} on {table}" not in " ".join(sql.split())
                or "raise" not in sql
            ):
                raise ExperimentStoreError(
                    f"append-only trigger missing or invalid: {name}"
                )

    @staticmethod
    def _require_text(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return value

    @staticmethod
    def _require_time(value: object, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} must be a non-negative integer")
        return value

    def _search_event_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT event_seq,search_id,search_kind,source,dataset_version,status,"
            " recorded_at,prev_event_hash,event_hash"
            " FROM search_attempt_events ORDER BY event_seq"
        ).fetchall()

    def _search_candidate_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT candidate_seq,search_id,config_hash,status,registered_at,"
            " prev_candidate_hash,candidate_hash"
            " FROM search_candidates ORDER BY candidate_seq"
        ).fetchall()

    def _search_heartbeat_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT heartbeat_seq,search_id,recorded_at,prev_heartbeat_hash,"
            " heartbeat_hash FROM search_attempt_heartbeats ORDER BY heartbeat_seq"
        ).fetchall()

    def _search_recovery_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT recovery_seq,search_id,reason,timeout_ms,last_activity_at,"
            " recovered_at,terminal_event_hash,prev_recovery_hash,recovery_hash"
            " FROM search_attempt_recoveries ORDER BY recovery_seq"
        ).fetchall()

    def _search_evaluation_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT link_seq,search_id,config_hash,run_id,recorded_at,"
            " evaluation_content_hash,prev_link_hash,link_hash"
            " FROM search_evaluations ORDER BY link_seq"
        ).fetchall()

    def _verified_search_state(
        self,
    ) -> tuple[str, str, dict[str, dict[str, object]]]:
        self._require_search_schema()
        event_head = ""
        search_start_high_water = -1
        attempts: dict[str, dict[str, object]] = {}
        for row in self._search_event_rows():
            if row["prev_event_hash"] != event_head:
                raise ExperimentStoreError(
                    f"search event {row['event_seq']} previous hash mismatch"
                )
            expected = _search_event_hash(
                search_id=row["search_id"],
                search_kind=row["search_kind"],
                source=row["source"],
                dataset_version=row["dataset_version"],
                status=row["status"],
                recorded_at=row["recorded_at"],
                prev_event_hash=event_head,
            )
            if row["event_hash"] != expected:
                raise ExperimentStoreError(
                    f"search event {row['event_seq']} hash mismatch"
                )
            search_id = row["search_id"]
            metadata = (
                row["search_kind"],
                row["source"],
                row["dataset_version"],
            )
            prior = attempts.get(search_id)
            if row["status"] == "STARTED":
                if prior is not None or row["recorded_at"] < search_start_high_water:
                    raise ExperimentStoreError(
                        f"search attempt {search_id} has invalid STARTED provenance"
                    )
                attempts[search_id] = {
                    "metadata": metadata,
                    "status": "STARTED",
                    "started_at": row["recorded_at"],
                    "last_activity_at": row["recorded_at"],
                    "terminal_at": None,
                    "terminal_event_hash": None,
                }
                search_start_high_water = row["recorded_at"]
            elif (
                prior is None
                or prior["status"] != "STARTED"
                or prior["metadata"] != metadata
                or row["recorded_at"] < prior["started_at"]
            ):
                raise ExperimentStoreError(
                    f"search attempt {search_id} has an invalid terminal transition"
                )
            else:
                prior["status"] = row["status"]
                prior["terminal_at"] = row["recorded_at"]
                prior["terminal_event_hash"] = expected
            event_head = expected

        candidate_head = ""
        seen_candidates: set[tuple[str, str]] = set()
        latest_candidate_times: dict[str, int] = {}
        trial_hashes = {
            row["config_hash"]
            for row in self._conn.execute("SELECT config_hash FROM trials")
        }
        for row in self._search_candidate_rows():
            if row["prev_candidate_hash"] != candidate_head:
                raise ExperimentStoreError(
                    f"search candidate {row['candidate_seq']} previous hash mismatch"
                )
            expected = _search_candidate_hash(
                search_id=row["search_id"],
                config_hash=row["config_hash"],
                status=row["status"],
                registered_at=row["registered_at"],
                prev_candidate_hash=candidate_head,
            )
            if row["candidate_hash"] != expected:
                raise ExperimentStoreError(
                    f"search candidate {row['candidate_seq']} hash mismatch"
                )
            attempt = attempts.get(row["search_id"])
            identity = (row["search_id"], row["config_hash"])
            prior_candidate_time = latest_candidate_times.get(row["search_id"], -1)
            if (
                attempt is None
                or identity in seen_candidates
                or row["config_hash"] not in trial_hashes
                or row["registered_at"] < attempt["started_at"]
                or row["registered_at"] < prior_candidate_time
                or (
                    attempt["terminal_at"] is not None
                    and row["registered_at"] > attempt["terminal_at"]
                )
            ):
                raise ExperimentStoreError(
                    f"search candidate {row['candidate_seq']} has invalid provenance"
                )
            seen_candidates.add(identity)
            latest_candidate_times[row["search_id"]] = row["registered_at"]
            attempt["last_activity_at"] = max(
                int(attempt["last_activity_at"]), row["registered_at"]
            )
            candidate_head = expected

        heartbeat_head = ""
        latest_heartbeat_times: dict[str, int] = {}
        for row in self._search_heartbeat_rows():
            if row["prev_heartbeat_hash"] != heartbeat_head:
                raise ExperimentStoreError(
                    f"search heartbeat {row['heartbeat_seq']} previous hash mismatch"
                )
            expected = _search_heartbeat_hash(
                search_id=row["search_id"],
                recorded_at=row["recorded_at"],
                prev_heartbeat_hash=heartbeat_head,
            )
            if row["heartbeat_hash"] != expected:
                raise ExperimentStoreError(
                    f"search heartbeat {row['heartbeat_seq']} hash mismatch"
                )
            attempt = attempts.get(row["search_id"])
            previous_at = latest_heartbeat_times.get(row["search_id"], -1)
            if (
                attempt is None
                or row["recorded_at"] < attempt["started_at"]
                or row["recorded_at"] < previous_at
                or (
                    attempt["terminal_at"] is not None
                    and row["recorded_at"] > attempt["terminal_at"]
                )
            ):
                raise ExperimentStoreError(
                    f"search heartbeat {row['heartbeat_seq']} has invalid provenance"
                )
            latest_heartbeat_times[row["search_id"]] = row["recorded_at"]
            attempt["last_activity_at"] = max(
                int(attempt["last_activity_at"]), row["recorded_at"]
            )
            heartbeat_head = expected

        recovery_head = ""
        recovery_records: list[sqlite3.Row] = []
        for row in self._search_recovery_rows():
            if row["prev_recovery_hash"] != recovery_head:
                raise ExperimentStoreError(
                    f"search recovery {row['recovery_seq']} previous hash mismatch"
                )
            expected = _search_recovery_hash(
                search_id=row["search_id"],
                reason=row["reason"],
                timeout_ms=row["timeout_ms"],
                last_activity_at=row["last_activity_at"],
                recovered_at=row["recovered_at"],
                terminal_event_hash=row["terminal_event_hash"],
                prev_recovery_hash=recovery_head,
            )
            if row["recovery_hash"] != expected:
                raise ExperimentStoreError(
                    f"search recovery {row['recovery_seq']} hash mismatch"
                )
            recovery_records.append(row)
            recovery_head = expected

        link_head = ""
        latest_link_times: dict[str, int] = {}
        registered_candidates = {
            (row["search_id"], row["config_hash"])
            for row in self._conn.execute(
                "SELECT search_id,config_hash FROM search_candidates"
            )
        }
        evaluations = {
            row["run_id"]: row
            for row in self._conn.execute(
                "SELECT run_id,dataset_version,recorded_ts,content_hash FROM evaluations"
            )
        }
        seen_links: set[tuple[str, str]] = set()
        for row in self._search_evaluation_rows():
            if row["prev_link_hash"] != link_head:
                raise ExperimentStoreError(
                    f"search evaluation link {row['link_seq']} previous hash mismatch"
                )
            expected = _search_evaluation_hash(
                search_id=row["search_id"],
                config_hash=row["config_hash"],
                run_id=row["run_id"],
                recorded_at=row["recorded_at"],
                evaluation_content_hash=row["evaluation_content_hash"],
                prev_link_hash=link_head,
            )
            if row["link_hash"] != expected:
                raise ExperimentStoreError(
                    f"search evaluation link {row['link_seq']} hash mismatch"
                )
            attempt = attempts.get(row["search_id"])
            evaluation = evaluations.get(row["run_id"])
            prior_link_time = latest_link_times.get(row["search_id"], -1)
            link_identity = (row["search_id"], row["run_id"])
            if (
                attempt is None
                or link_identity in seen_links
                or (row["search_id"], row["config_hash"])
                not in registered_candidates
                or evaluation is None
                or evaluation["dataset_version"] != attempt["metadata"][2]
                or evaluation["content_hash"] != row["evaluation_content_hash"]
                or row["recorded_at"] < attempt["started_at"]
                or row["recorded_at"] < prior_link_time
                or (
                    attempt["terminal_at"] is not None
                    and row["recorded_at"] > attempt["terminal_at"]
                )
            ):
                raise ExperimentStoreError(
                    f"search evaluation link {row['link_seq']} has invalid provenance"
                )
            attempt["last_activity_at"] = max(
                int(attempt["last_activity_at"]), row["recorded_at"]
            )
            seen_links.add(link_identity)
            link_head = expected
            latest_link_times[row["search_id"]] = row["recorded_at"]

        for row in recovery_records:
            attempt = attempts.get(row["search_id"])
            if (
                attempt is None
                or attempt["status"] != "FAILED"
                or row["reason"] != _SEARCH_RECOVERY_REASON
                or row["last_activity_at"] != attempt["last_activity_at"]
                or row["recovered_at"] != attempt["terminal_at"]
                or row["terminal_event_hash"] != attempt["terminal_event_hash"]
                or row["timeout_ms"] <= 0
                or row["last_activity_at"] + row["timeout_ms"] > row["recovered_at"]
            ):
                raise ExperimentStoreError(
                    f"search recovery {row['recovery_seq']} has invalid provenance"
                )
        return event_head, candidate_head, attempts

    def _require_search_start_clock(self, recorded_at: int) -> None:
        row = self._conn.execute(
            "SELECT MAX(recorded_at) AS latest FROM search_attempt_events"
            " WHERE status='STARTED'"
        ).fetchone()
        latest = row["latest"]
        if latest is not None and recorded_at < latest:
            raise ExperimentStoreError(
                "search STARTED clock rollback: "
                f"recorded_at={recorded_at} latest={latest}"
            )

    def _append_search_terminal_event(
        self,
        *,
        search_id: str,
        attempt: Mapping[str, object],
        status: str,
        recorded_at: int,
        event_head: str,
    ) -> str:
        search_kind, source, dataset_version = attempt["metadata"]
        event_hash = _search_event_hash(
            search_id=search_id,
            search_kind=str(search_kind),
            source=str(source),
            dataset_version=str(dataset_version),
            status=status,
            recorded_at=recorded_at,
            prev_event_hash=event_head,
        )
        self._conn.execute(
            "INSERT INTO search_attempt_events"
            " (search_id,search_kind,source,dataset_version,status,recorded_at,"
            " prev_event_hash,event_hash) VALUES (?,?,?,?,?,?,?,?)",
            (
                search_id,
                search_kind,
                source,
                dataset_version,
                status,
                recorded_at,
                event_head,
                event_hash,
            ),
        )
        return event_hash

    def _require_outbox_schema(self) -> None:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        present = {row["name"] for row in rows}
        missing = [table for table in _OUTBOX_TABLES if table not in present]
        if missing:
            raise ExperimentStoreError(
                f"experiment event outbox unavailable; missing tables: {missing}"
            )

    def _outbox_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT outbox_seq,outbox_id,event_json,correlation_id,causation_id,"
            " repro_hash,prev_record_hash,record_hash"
            " FROM experiment_event_outbox ORDER BY outbox_seq"
        ).fetchall()

    def _dispatch_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT dispatch_seq,outbox_id,dispatched_at,event_repro_hash,"
            " prev_record_hash,record_hash"
            " FROM experiment_event_dispatch ORDER BY dispatch_seq"
        ).fetchall()

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> EventOutboxMessage:
        try:
            event = Event.model_validate_json(row["event_json"])
        except Exception as exc:
            raise ExperimentStoreError(
                f"experiment outbox event {row['outbox_id']} cannot be reconstructed"
            ) from exc
        if event.reproducibility_hash != row["repro_hash"]:
            raise ExperimentStoreError(
                f"experiment outbox event {row['outbox_id']} payload hash mismatch"
            )
        message = EventOutboxMessage(
            event=event,
            correlation_id=row["correlation_id"],
            causation_id=row["causation_id"],
        )
        if message.outbox_id != row["outbox_id"]:
            raise ExperimentStoreError(
                f"experiment outbox event {row['outbox_id']} id mismatch"
            )
        return message

    @classmethod
    def _verify_outbox_rows(
        cls, rows: Sequence[sqlite3.Row]
    ) -> tuple[str, dict[str, EventOutboxMessage]]:
        previous = ""
        messages: dict[str, EventOutboxMessage] = {}
        for row in rows:
            message = cls._message_from_row(row)
            if row["prev_record_hash"] != previous:
                raise ExperimentStoreError(
                    f"experiment outbox row {row['outbox_seq']} previous hash mismatch"
                )
            expected = _outbox_hash(message, previous)
            if row["record_hash"] != expected:
                raise ExperimentStoreError(
                    f"experiment outbox row {row['outbox_seq']} record hash mismatch"
                )
            previous = expected
            messages[message.outbox_id] = message
        return previous, messages

    @staticmethod
    def _verify_dispatch_rows(
        rows: Sequence[sqlite3.Row],
        messages: Mapping[str, EventOutboxMessage],
    ) -> str:
        previous = ""
        for row in rows:
            message = messages.get(row["outbox_id"])
            if (
                message is None
                or message.event.reproducibility_hash != row["event_repro_hash"]
            ):
                raise ExperimentStoreError(
                    f"experiment dispatch row {row['dispatch_seq']} reference mismatch"
                )
            if row["prev_record_hash"] != previous:
                raise ExperimentStoreError(
                    f"experiment dispatch row {row['dispatch_seq']} previous hash mismatch"
                )
            expected = _dispatch_hash(
                outbox_id=row["outbox_id"],
                dispatched_at=row["dispatched_at"],
                event_repro_hash=row["event_repro_hash"],
                prev_record_hash=previous,
            )
            if row["record_hash"] != expected:
                raise ExperimentStoreError(
                    f"experiment dispatch row {row['dispatch_seq']} record hash mismatch"
                )
            previous = expected
        return previous

    def _verified_outbox_state(
        self,
    ) -> tuple[str, dict[str, EventOutboxMessage], str]:
        self._require_outbox_schema()
        outbox_head, messages = self._verify_outbox_rows(self._outbox_rows())
        dispatch_head = self._verify_dispatch_rows(
            self._dispatch_rows(), messages
        )
        return outbox_head, messages, dispatch_head

    def _evaluation_audit_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT audit_seq,run_id,event_id,actor,action,timestamp,target,"
            " metadata_json,evaluation_content_hash,prev_record_hash,record_hash"
            " FROM evaluation_audit_outbox ORDER BY audit_seq"
        ).fetchall()

    def _evaluation_audit_dispatch_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT dispatch_seq,event_id,dispatched_at,audit_entry_hash,"
            " prev_record_hash,record_hash"
            " FROM evaluation_audit_dispatch ORDER BY dispatch_seq"
        ).fetchall()

    @staticmethod
    def _evaluation_audit_intent_from_row(
        row: sqlite3.Row,
    ) -> EvaluationAuditIntent:
        try:
            metadata = json.loads(row["metadata_json"])
        except Exception as exc:
            raise ExperimentStoreError(
                f"evaluation audit {row['event_id']} metadata is corrupt"
            ) from exc
        if not isinstance(metadata, dict):
            raise ExperimentStoreError(
                f"evaluation audit {row['event_id']} metadata is not an object"
            )
        return EvaluationAuditIntent(
            event_id=row["event_id"],
            actor=row["actor"],
            action=row["action"],
            timestamp=row["timestamp"],
            target=row["target"],
            metadata=metadata,
        )

    def _verified_evaluation_audit_state(
        self,
    ) -> tuple[str, dict[str, tuple[str, EvaluationAuditIntent]], str]:
        self._require_search_schema()
        previous = ""
        intents: dict[str, tuple[str, EvaluationAuditIntent]] = {}
        evaluations = {
            row["run_id"]: row["content_hash"]
            for row in self._conn.execute(
                "SELECT run_id,content_hash FROM evaluations"
            )
        }
        for row in self._evaluation_audit_rows():
            intent = self._evaluation_audit_intent_from_row(row)
            if (
                row["prev_record_hash"] != previous
                or evaluations.get(row["run_id"])
                != row["evaluation_content_hash"]
            ):
                raise ExperimentStoreError(
                    f"evaluation audit row {row['audit_seq']} has invalid provenance"
                )
            expected = _evaluation_audit_hash(
                run_id=row["run_id"],
                intent=intent,
                evaluation_content_hash=row["evaluation_content_hash"],
                prev_record_hash=previous,
            )
            if row["record_hash"] != expected:
                raise ExperimentStoreError(
                    f"evaluation audit row {row['audit_seq']} hash mismatch"
                )
            intents[intent.event_id] = (row["run_id"], intent)
            previous = expected

        dispatch_head = ""
        for row in self._evaluation_audit_dispatch_rows():
            if (
                row["event_id"] not in intents
                or row["prev_record_hash"] != dispatch_head
            ):
                raise ExperimentStoreError(
                    f"evaluation audit dispatch {row['dispatch_seq']} has invalid provenance"
                )
            expected = _evaluation_audit_dispatch_hash(
                event_id=row["event_id"],
                dispatched_at=row["dispatched_at"],
                audit_entry_hash=row["audit_entry_hash"],
                prev_record_hash=dispatch_head,
            )
            if row["record_hash"] != expected:
                raise ExperimentStoreError(
                    f"evaluation audit dispatch {row['dispatch_seq']} hash mismatch"
                )
            dispatch_head = expected
        return previous, intents, dispatch_head

    def add_trials(self, config_hashes: Iterable[str], *, first_seen_ts: int) -> None:
        self._require_schema()
        self._conn.executemany(
            "INSERT OR IGNORE INTO trials(config_hash, first_seen_ts) VALUES (?, ?)",
            [(h, first_seen_ts) for h in config_hashes],
        )
        self._conn.commit()

    def begin_search_attempt(
        self,
        *,
        search_id: str,
        search_kind: str,
        source: str,
        dataset_version: str,
        recorded_at: int,
    ) -> bool:
        search_id = self._require_text(search_id, "search_id")
        search_kind = self._require_text(search_kind, "search_kind")
        source = self._require_text(source, "source")
        dataset_version = self._require_text(dataset_version, "dataset_version")
        recorded_at = self._require_time(recorded_at, "recorded_at")
        self._ensure_search_schema()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            event_head, _, attempts = self._verified_search_state()
            self._require_search_start_clock(recorded_at)
            if search_id in attempts:
                raise ExperimentStoreError(
                    f"search attempt {search_id} already exists"
                )
            event_hash = _search_event_hash(
                search_id=search_id,
                search_kind=search_kind,
                source=source,
                dataset_version=dataset_version,
                status="STARTED",
                recorded_at=recorded_at,
                prev_event_hash=event_head,
            )
            self._conn.execute(
                "INSERT INTO search_attempt_events"
                " (search_id,search_kind,source,dataset_version,status,recorded_at,"
                " prev_event_hash,event_hash) VALUES (?,?,?,?,?,?,?,?)",
                (
                    search_id,
                    search_kind,
                    source,
                    dataset_version,
                    "STARTED",
                    recorded_at,
                    event_head,
                    event_hash,
                ),
            )
            self._conn.commit()
            return True
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def register_search_candidates(
        self,
        search_id: str,
        config_hashes: Iterable[str],
        *,
        registered_at: int,
    ) -> int:
        search_id = self._require_text(search_id, "search_id")
        registered_at = self._require_time(registered_at, "registered_at")
        ordered_hashes: list[str] = []
        seen: set[str] = set()
        for value in config_hashes:
            config_hash = self._require_text(value, "config_hash")
            if config_hash not in seen:
                seen.add(config_hash)
                ordered_hashes.append(config_hash)
        if not ordered_hashes:
            raise ValueError("config_hashes must contain at least one candidate")
        self._ensure_search_schema()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            _, candidate_head, attempts = self._verified_search_state()
            attempt = attempts.get(search_id)
            if attempt is None:
                raise ExperimentStoreError(
                    f"search attempt {search_id} has not STARTED"
                )
            if attempt["status"] in _SEARCH_TERMINAL_STATUSES:
                raise ExperimentStoreError(
                    f"search attempt {search_id} is terminal"
                )
            if registered_at < attempt["started_at"]:
                raise ExperimentStoreError(
                    f"candidate time precedes search attempt {search_id}"
                )
            if registered_at < attempt["last_activity_at"]:
                raise ExperimentStoreError(
                    f"candidate clock rollback for search attempt {search_id}"
                )

            existing = {
                row["config_hash"]
                for row in self._conn.execute(
                    "SELECT config_hash FROM search_candidates WHERE search_id=?",
                    (search_id,),
                )
            }
            inserted = 0
            for config_hash in ordered_hashes:
                if config_hash in existing:
                    continue
                digest = _search_candidate_hash(
                    search_id=search_id,
                    config_hash=config_hash,
                    status="REGISTERED",
                    registered_at=registered_at,
                    prev_candidate_hash=candidate_head,
                )
                self._conn.execute(
                    "INSERT INTO search_candidates"
                    " (search_id,config_hash,status,registered_at,prev_candidate_hash,"
                    " candidate_hash) VALUES (?,?,?,?,?,?)",
                    (
                        search_id,
                        config_hash,
                        "REGISTERED",
                        registered_at,
                        candidate_head,
                        digest,
                    ),
                )
                self._conn.execute(
                    "INSERT OR IGNORE INTO trials(config_hash,first_seen_ts) VALUES (?,?)",
                    (config_hash, registered_at),
                )
                candidate_head = digest
                inserted += 1
            self._conn.commit()
            return inserted
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def heartbeat_search_attempt(
        self,
        search_id: str,
        *,
        recorded_at: int,
    ) -> bool:
        """Append cooperative liveness evidence; callers own scheduling and clocks.

        This local ledger does not authenticate a worker or grant a lease. A
        production operator must establish ownership outside this SQLite process.
        """
        search_id = self._require_text(search_id, "search_id")
        recorded_at = self._require_time(recorded_at, "recorded_at")
        self._ensure_search_schema()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            attempts = self._verified_search_state()[2]
            attempt = attempts.get(search_id)
            if attempt is None:
                raise ExperimentStoreError(
                    f"search attempt {search_id} has not STARTED"
                )
            if attempt["status"] in _SEARCH_TERMINAL_STATUSES:
                raise ExperimentStoreError(
                    f"search attempt {search_id} is terminal"
                )
            if recorded_at < attempt["last_activity_at"]:
                raise ExperimentStoreError(
                    f"heartbeat clock rollback for search attempt {search_id}"
                )
            existing = self._conn.execute(
                "SELECT heartbeat_hash FROM search_attempt_heartbeats"
                " WHERE search_id=? AND recorded_at=?",
                (search_id, recorded_at),
            ).fetchone()
            if existing is not None:
                self._conn.rollback()
                return False
            head_row = self._conn.execute(
                "SELECT heartbeat_hash FROM search_attempt_heartbeats"
                " ORDER BY heartbeat_seq DESC LIMIT 1"
            ).fetchone()
            heartbeat_head = "" if head_row is None else head_row["heartbeat_hash"]
            digest = _search_heartbeat_hash(
                search_id=search_id,
                recorded_at=recorded_at,
                prev_heartbeat_hash=heartbeat_head,
            )
            self._conn.execute(
                "INSERT INTO search_attempt_heartbeats"
                " (search_id,recorded_at,prev_heartbeat_hash,heartbeat_hash)"
                " VALUES (?,?,?,?)",
                (search_id, recorded_at, heartbeat_head, digest),
            )
            self._conn.commit()
            return True
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def finish_search_attempt(
        self,
        search_id: str,
        *,
        status: str,
        recorded_at: int,
    ) -> bool:
        search_id = self._require_text(search_id, "search_id")
        if status not in _SEARCH_TERMINAL_STATUSES:
            raise ValueError("status must be FAILED or COMPLETED")
        recorded_at = self._require_time(recorded_at, "recorded_at")
        self._ensure_search_schema()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            event_head, _, attempts = self._verified_search_state()
            attempt = attempts.get(search_id)
            if attempt is None:
                raise ExperimentStoreError(
                    f"search attempt {search_id} has not STARTED"
                )
            if attempt["status"] in _SEARCH_TERMINAL_STATUSES:
                raise ExperimentStoreError(
                    f"search attempt {search_id} is already terminal"
                )
            if recorded_at < attempt["last_activity_at"]:
                raise ExperimentStoreError(
                    f"terminal time precedes search attempt {search_id} provenance"
                )
            self._append_search_terminal_event(
                search_id=search_id,
                attempt=attempt,
                status=status,
                recorded_at=recorded_at,
                event_head=event_head,
            )
            self._conn.commit()
            return True
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def recover_stale_search_attempts(
        self,
        *,
        recorded_at: int,
        timeout_ms: int,
    ) -> tuple[str, ...]:
        """Explicitly terminalize stale STARTED attempts with timeout evidence.

        This is not a background sweeper and has no worker-identity authority.
        Callers must establish that recovery is operationally authorized; the
        transaction only proves the observed timeout and makes replay idempotent.
        """
        recorded_at = self._require_time(recorded_at, "recorded_at")
        timeout_ms = self._require_time(timeout_ms, "timeout_ms")
        if timeout_ms == 0:
            raise ValueError("timeout_ms must be positive")
        self._ensure_search_schema()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            event_head, _, attempts = self._verified_search_state()
            cutoff = recorded_at - timeout_ms
            stale = sorted(
                (
                    search_id
                    for search_id, attempt in attempts.items()
                    if attempt["status"] == "STARTED"
                    and int(attempt["last_activity_at"]) <= cutoff
                ),
                key=lambda search_id: (
                    int(attempts[search_id]["last_activity_at"]),
                    search_id,
                ),
            )
            recovery_head_row = self._conn.execute(
                "SELECT recovery_hash FROM search_attempt_recoveries"
                " ORDER BY recovery_seq DESC LIMIT 1"
            ).fetchone()
            recovery_head = (
                "" if recovery_head_row is None else recovery_head_row["recovery_hash"]
            )
            for search_id in stale:
                attempt = attempts[search_id]
                terminal_event_hash = self._append_search_terminal_event(
                    search_id=search_id,
                    attempt=attempt,
                    status="FAILED",
                    recorded_at=recorded_at,
                    event_head=event_head,
                )
                event_head = terminal_event_hash
                self._recovery_checkpoint("after_terminal")
                recovery_hash = _search_recovery_hash(
                    search_id=search_id,
                    reason=_SEARCH_RECOVERY_REASON,
                    timeout_ms=timeout_ms,
                    last_activity_at=int(attempt["last_activity_at"]),
                    recovered_at=recorded_at,
                    terminal_event_hash=terminal_event_hash,
                    prev_recovery_hash=recovery_head,
                )
                self._conn.execute(
                    "INSERT INTO search_attempt_recoveries"
                    " (search_id,reason,timeout_ms,last_activity_at,recovered_at,"
                    " terminal_event_hash,prev_recovery_hash,recovery_hash)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        search_id,
                        _SEARCH_RECOVERY_REASON,
                        timeout_ms,
                        int(attempt["last_activity_at"]),
                        recorded_at,
                        terminal_event_hash,
                        recovery_head,
                        recovery_hash,
                    ),
                )
                recovery_head = recovery_hash
            self._recovery_checkpoint("before_commit")
            self._conn.commit()
            return tuple(stale)
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def _recovery_checkpoint(self, stage: str) -> None:
        """Test seam proving terminal and timeout evidence cannot split."""
        del stage

    def search_attempt_events(self, search_id: str) -> list[dict]:
        search_id = self._require_text(search_id, "search_id")
        self._verified_search_state()
        return [
            dict(row)
            for row in self._conn.execute(
                "SELECT e.event_seq,e.search_id,e.search_kind,e.source,"
                " e.dataset_version,e.status,e.recorded_at,e.prev_event_hash,"
                " e.event_hash,r.reason AS recovery_reason"
                " FROM search_attempt_events e"
                " LEFT JOIN search_attempt_recoveries r"
                " ON r.terminal_event_hash=e.event_hash"
                " WHERE e.search_id=? ORDER BY e.event_seq",
                (search_id,),
            )
        ]

    def search_identity(self, search_id: str) -> dict[str, str]:
        """Return the canonical, cross-session identity for one search.

        Reads go through the complete hash-chain verifier first.  A caller
        cannot obtain an identity from a tampered STARTED row, and the returned
        digest is derived only from immutable search metadata.
        """
        search_id = self._require_text(search_id, "search_id")
        attempts = self._verified_search_state()[2]
        attempt = attempts.get(search_id)
        if attempt is None:
            raise ExperimentStoreError(
                f"search attempt {search_id} has not STARTED"
            )
        search_kind, source, dataset_version = attempt["metadata"]
        identity = {
            "search_id": search_id,
            "search_kind": str(search_kind),
            "source": str(source),
            "dataset_version": str(dataset_version),
        }
        identity["identity_hash"] = _search_identity_hash(**identity)
        return identity

    def evaluation_binding(self, run_id: str) -> dict | None:
        """Return an evaluation's verified search binding and identity digest.

        The link is only exposed after the complete search provenance chain is
        verified.  A missing link is represented as ``None`` so diagnostic
        evaluations cannot be mistaken for selection-bound artifacts.
        """
        run_id = self._require_text(run_id, "run_id")
        self._verified_search_state()
        row = self._conn.execute(
            "SELECT link_seq,search_id,config_hash,run_id,recorded_at,"
            "evaluation_content_hash,prev_link_hash,link_hash "
            "FROM search_evaluations WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        binding = dict(row)
        binding["search_identity_hash"] = self.search_identity(
            str(row["search_id"])
        )["identity_hash"]
        return binding

    def search_attempt_recoveries(self, search_id: str) -> list[dict]:
        search_id = self._require_text(search_id, "search_id")
        self._verified_search_state()
        return [
            dict(row)
            for row in self._conn.execute(
                "SELECT recovery_seq,search_id,reason,timeout_ms,last_activity_at,"
                " recovered_at,terminal_event_hash,prev_recovery_hash,recovery_hash"
                " FROM search_attempt_recoveries WHERE search_id=?"
                " ORDER BY recovery_seq",
                (search_id,),
            )
        ]

    def search_evaluations(self, search_id: str) -> list[dict]:
        search_id = self._require_text(search_id, "search_id")
        self._verified_search_state()
        return [
            dict(row)
            for row in self._conn.execute(
                "SELECT link_seq,search_id,config_hash,run_id,recorded_at,"
                " evaluation_content_hash,prev_link_hash,link_hash"
                " FROM search_evaluations WHERE search_id=? ORDER BY link_seq",
                (search_id,),
            )
        ]

    def search_candidates(self, search_id: str) -> list[dict]:
        search_id = self._require_text(search_id, "search_id")
        self._verified_search_state()
        return [
            dict(row)
            for row in self._conn.execute(
                "SELECT candidate_seq,search_id,config_hash,status,registered_at,"
                " prev_candidate_hash,candidate_hash"
                " FROM search_candidates WHERE search_id=? ORDER BY candidate_seq",
                (search_id,),
            )
        ]

    def search_history_valid(self) -> bool:
        try:
            self._verified_search_state()
            return True
        except (ExperimentStoreError, sqlite3.DatabaseError):
            return False

    def save_catalog_snapshot(
        self,
        *,
        config_hashes: Iterable[str],
        alphas: Iterable[Mapping[str, object]],
        first_seen_ts: int,
        event_outbox: Sequence[EventOutboxMessage] = (),
    ) -> None:
        """Atomically persist the catalog snapshot and any causally-bound events."""
        self._require_schema()
        self._require_outbox_schema()
        trial_rows = [(value, first_seen_ts) for value in config_hashes]
        alpha_rows = [dict(row) for row in alphas]
        messages = tuple(event_outbox)
        if any(not isinstance(message, EventOutboxMessage) for message in messages):
            raise TypeError("event_outbox must contain EventOutboxMessage values")

        try:
            self._conn.execute("BEGIN IMMEDIATE")
            outbox_head, _, _ = self._verified_outbox_state()
            self._conn.executemany(
                "INSERT OR IGNORE INTO trials(config_hash, first_seen_ts) VALUES (?, ?)",
                trial_rows,
            )
            for row in alpha_rows:
                self._conn.execute(
                    "INSERT INTO alpha_state(alpha_id, definition_json, state, "
                    "returns_json, sharpe, pvalue) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(alpha_id) DO UPDATE SET "
                    "definition_json=excluded.definition_json, state=excluded.state, "
                    "returns_json=excluded.returns_json, sharpe=excluded.sharpe, "
                    "pvalue=excluded.pvalue",
                    (
                        row["alpha_id"],
                        row["definition_json"],
                        row["state"],
                        row["returns_json"],
                        row.get("sharpe"),
                        row.get("pvalue"),
                    ),
                )
            for message in messages:
                existing = self._conn.execute(
                    "SELECT outbox_seq,outbox_id,event_json,correlation_id,"
                    " causation_id,repro_hash,prev_record_hash,record_hash"
                    " FROM experiment_event_outbox WHERE outbox_id=?",
                    (message.outbox_id,),
                ).fetchone()
                if existing is not None:
                    if self._message_from_row(existing) != message:
                        raise ImmutableArtifactError(
                            f"experiment outbox event conflict: {message.outbox_id}"
                        )
                    continue
                digest = _outbox_hash(message, outbox_head)
                self._conn.execute(
                    "INSERT INTO experiment_event_outbox"
                    " (outbox_id,event_json,correlation_id,causation_id,repro_hash,"
                    " prev_record_hash,record_hash) VALUES (?,?,?,?,?,?,?)",
                    (
                        message.outbox_id,
                        _event_json(message.event),
                        message.correlation_id,
                        message.causation_id,
                        message.event.reproducibility_hash,
                        outbox_head,
                        digest,
                    ),
                )
                outbox_head = digest
            self._conn.commit()
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def save_evaluation_catalog_snapshot(
        self,
        *,
        config_hashes: Iterable[str],
        alphas: Iterable[Mapping[str, object]],
        first_seen_ts: int,
    ) -> None:
        """Add new evaluation rows without mutating existing lifecycle state."""
        self._require_schema()
        trial_rows = [(value, first_seen_ts) for value in config_hashes]
        alpha_rows = [dict(row) for row in alphas]
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.executemany(
                "INSERT OR IGNORE INTO trials(config_hash, first_seen_ts) VALUES (?, ?)",
                trial_rows,
            )
            for row in alpha_rows:
                existing = self._conn.execute(
                    "SELECT definition_json FROM alpha_state WHERE alpha_id=?",
                    (row["alpha_id"],),
                ).fetchone()
                if existing is not None:
                    if existing["definition_json"] != row["definition_json"]:
                        raise ImmutableArtifactError(
                            f"alpha_id {row['alpha_id']} already has a different definition"
                        )
                    continue
                self._conn.execute(
                    "INSERT INTO alpha_state(alpha_id,definition_json,state,returns_json,"
                    " sharpe,pvalue) VALUES (?,?,?,?,?,?)",
                    (
                        row["alpha_id"],
                        row["definition_json"],
                        row["state"],
                        row["returns_json"],
                        row.get("sharpe"),
                        row.get("pvalue"),
                    ),
                )
            self._conn.commit()
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def pending_event_outbox(self, *, limit: int = 1_000) -> tuple[dict, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit < 1:
            raise ValueError("limit must be positive")
        self._verified_outbox_state()
        rows = self._conn.execute(
            "SELECT o.outbox_seq,o.outbox_id,o.event_json,o.correlation_id,"
            " o.causation_id,o.repro_hash,o.prev_record_hash,o.record_hash"
            " FROM experiment_event_outbox o"
            " LEFT JOIN experiment_event_dispatch d ON d.outbox_id=o.outbox_id"
            " WHERE d.outbox_id IS NULL ORDER BY o.outbox_seq LIMIT ?",
            (limit,),
        ).fetchall()
        return tuple(
            {
                "outbox_id": row["outbox_id"],
                "event": self._message_from_row(row).event,
                "correlation_id": row["correlation_id"],
                "causation_id": row["causation_id"],
            }
            for row in rows
        )

    def mark_event_dispatched(
        self,
        outbox_id: str,
        *,
        dispatched_at: int,
        event_repro_hash: str,
    ) -> bool:
        if not isinstance(outbox_id, str) or not outbox_id:
            raise ValueError("outbox_id must be non-empty")
        if (
            isinstance(dispatched_at, bool)
            or not isinstance(dispatched_at, int)
            or dispatched_at < 0
        ):
            raise ValueError("dispatched_at must be a non-negative integer")
        if not isinstance(event_repro_hash, str) or not event_repro_hash:
            raise ValueError("event_repro_hash must be non-empty")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            _, messages, dispatch_head = self._verified_outbox_state()
            message = messages.get(outbox_id)
            if (
                message is None
                or message.event.reproducibility_hash != event_repro_hash
            ):
                raise ExperimentStoreError(
                    "experiment outbox dispatch reference mismatch"
                )
            existing = self._conn.execute(
                "SELECT dispatch_seq,outbox_id,dispatched_at,event_repro_hash,"
                " prev_record_hash,record_hash"
                " FROM experiment_event_dispatch WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["dispatched_at"] == dispatched_at
                    and existing["event_repro_hash"] == event_repro_hash
                ):
                    self._conn.rollback()
                    return False
                raise ExperimentStoreError(
                    "experiment outbox dispatch acknowledgement conflict"
                )
            digest = _dispatch_hash(
                outbox_id=outbox_id,
                dispatched_at=dispatched_at,
                event_repro_hash=event_repro_hash,
                prev_record_hash=dispatch_head,
            )
            self._conn.execute(
                "INSERT INTO experiment_event_dispatch"
                " (outbox_id,dispatched_at,event_repro_hash,prev_record_hash,record_hash)"
                " VALUES (?,?,?,?,?)",
                (
                    outbox_id,
                    dispatched_at,
                    event_repro_hash,
                    dispatch_head,
                    digest,
                ),
            )
            self._conn.commit()
            return True
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def event_outbox_chain_valid(self) -> bool:
        try:
            self._verified_outbox_state()
            return True
        except (ExperimentStoreError, sqlite3.DatabaseError):
            return False

    def trial_count(self) -> int:
        self._require_schema()
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM trials").fetchone()["n"])

    def all_trial_hashes(self) -> list[str]:
        self._require_schema()
        return [r["config_hash"] for r in self._conn.execute("SELECT config_hash FROM trials")]

    def upsert_alpha(
        self,
        alpha_id: str,
        definition_json: str,
        state: str,
        returns_json: str,
        sharpe: Optional[float],
        pvalue: Optional[float],
    ) -> None:
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
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT alpha_id, definition_json, state, returns_json, sharpe, pvalue "
                "FROM alpha_state ORDER BY alpha_id"
            )
        ]

    def save_bandit(self, snapshot_json: str) -> None:
        self._require_schema()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS bandit_state "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), snapshot_json TEXT NOT NULL)"
        )
        self._conn.execute("INSERT OR REPLACE INTO bandit_state(id, snapshot_json) VALUES (1, ?)", (snapshot_json,))
        self._conn.commit()

    def load_bandit(self):
        self._require_schema()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS bandit_state "
            "(id INTEGER PRIMARY KEY CHECK (id = 1), snapshot_json TEXT NOT NULL)"
        )
        row = self._conn.execute("SELECT snapshot_json FROM bandit_state WHERE id = 1").fetchone()
        return row["snapshot_json"] if row is not None else None

    def insert_evaluation(
        self,
        *,
        run_id: str,
        alpha_id: str,
        dataset_version: str,
        recorded_ts: int,
        verdict: str,
        content_hash: str,
        evaluation_json: str,
        search_id: str | None = None,
        config_hash: str | None = None,
        audit_intent: EvaluationAuditIntent | None = None,
    ) -> bool:
        """Returns True if a new row was inserted, False on an idempotent no-op
        re-record. Evaluation, exact search link, and audit intent commit together."""
        run_id = self._require_text(run_id, "run_id")
        alpha_id = self._require_text(alpha_id, "alpha_id")
        dataset_version = self._require_text(dataset_version, "dataset_version")
        verdict = self._require_text(verdict, "verdict")
        content_hash = self._require_text(content_hash, "content_hash")
        evaluation_json = self._require_text(evaluation_json, "evaluation_json")
        recorded_ts = self._require_time(recorded_ts, "recorded_ts")
        if search_id is not None:
            search_id = self._require_text(search_id, "search_id")
        if config_hash is not None:
            config_hash = self._require_text(config_hash, "config_hash")
        if (search_id is None) != (config_hash is None):
            raise ValueError(
                "search_id and config_hash must be supplied together for exact binding"
            )
        if audit_intent is not None:
            if not isinstance(audit_intent, EvaluationAuditIntent):
                raise TypeError("audit_intent must be an EvaluationAuditIntent or None")
            for field, value in (
                ("audit_intent.event_id", audit_intent.event_id),
                ("audit_intent.actor", audit_intent.actor),
                ("audit_intent.action", audit_intent.action),
            ):
                self._require_text(value, field)
            try:
                json.dumps(
                    dict(audit_intent.metadata),
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            except Exception as exc:
                raise ValueError("audit intent metadata must be canonical JSON") from exc

        self._ensure_search_schema()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            _, _, attempts = self._verified_search_state()
            audit_head, audit_intents, _ = self._verified_evaluation_audit_state()
            existing = self._conn.execute(
                "SELECT * FROM evaluations WHERE run_id=?", (run_id,)
            ).fetchone()
            inserted = existing is None
            if existing is not None and existing["content_hash"] != content_hash:
                raise ImmutableArtifactError(
                    f"run_id {run_id} already recorded with a different content_hash"
                )
            if inserted:
                self._conn.execute(
                    "INSERT INTO evaluations(run_id,alpha_id,dataset_version,recorded_ts,"
                    " verdict,content_hash,evaluation_json) VALUES (?,?,?,?,?,?,?)",
                    (
                        run_id,
                        alpha_id,
                        dataset_version,
                        recorded_ts,
                        verdict,
                        content_hash,
                        evaluation_json,
                    ),
                )

            # The immutable artifact timestamp is selected while holding the same
            # write lock that decides insertion. Concurrent retries may propose
            # different timestamps, but the audit record always inherits the
            # timestamp of the transaction that first committed the artifact.
            effective_audit_intent = (
                None
                if audit_intent is None
                else replace(
                    audit_intent,
                    timestamp=(
                        recorded_ts if inserted else int(existing["recorded_ts"])
                    ),
                )
            )

            if search_id is not None:
                attempt = attempts.get(search_id)
                candidate = self._conn.execute(
                    "SELECT config_hash FROM search_candidates"
                    " WHERE search_id=? AND config_hash=?",
                    (search_id, config_hash),
                ).fetchone()
                if (
                    attempt is None
                    or attempt["status"] != "STARTED"
                    or candidate is None
                    or attempt["metadata"][2] != dataset_version
                    or recorded_ts < attempt["started_at"]
                    or recorded_ts < attempt["last_activity_at"]
                ):
                    raise ExperimentStoreError(
                        f"evaluation cannot bind to search attempt {search_id}"
                    )
                existing_link = self._conn.execute(
                    "SELECT search_id,config_hash,evaluation_content_hash"
                    " FROM search_evaluations"
                    " WHERE search_id=? AND run_id=?",
                    (search_id, run_id),
                ).fetchone()
                if existing_link is not None:
                    if (
                        existing_link["config_hash"] != config_hash
                        or existing_link["evaluation_content_hash"] != content_hash
                    ):
                        raise ImmutableArtifactError(
                            f"evaluation run {run_id} already has a different search binding"
                        )
                else:
                    link_head_row = self._conn.execute(
                        "SELECT link_hash FROM search_evaluations"
                        " ORDER BY link_seq DESC LIMIT 1"
                    ).fetchone()
                    link_head = (
                        "" if link_head_row is None else link_head_row["link_hash"]
                    )
                    link_hash = _search_evaluation_hash(
                        search_id=search_id,
                        config_hash=config_hash,
                        run_id=run_id,
                        recorded_at=recorded_ts,
                        evaluation_content_hash=content_hash,
                        prev_link_hash=link_head,
                    )
                    self._conn.execute(
                        "INSERT INTO search_evaluations"
                        " (search_id,config_hash,run_id,recorded_at,"
                        " evaluation_content_hash,prev_link_hash,link_hash)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (
                            search_id,
                            config_hash,
                            run_id,
                            recorded_ts,
                            content_hash,
                            link_head,
                            link_hash,
                        ),
                    )

            if effective_audit_intent is not None:
                existing_audit = audit_intents.get(effective_audit_intent.event_id)
                if existing_audit is not None:
                    if existing_audit != (run_id, effective_audit_intent):
                        raise ImmutableArtifactError(
                            "evaluation audit event conflict: "
                            f"{effective_audit_intent.event_id}"
                        )
                else:
                    audit_hash = _evaluation_audit_hash(
                        run_id=run_id,
                        intent=effective_audit_intent,
                        evaluation_content_hash=content_hash,
                        prev_record_hash=audit_head,
                    )
                    self._conn.execute(
                        "INSERT INTO evaluation_audit_outbox"
                        " (run_id,event_id,actor,action,timestamp,target,metadata_json,"
                        " evaluation_content_hash,prev_record_hash,record_hash)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            run_id,
                            effective_audit_intent.event_id,
                            effective_audit_intent.actor,
                            effective_audit_intent.action,
                            effective_audit_intent.timestamp,
                            effective_audit_intent.target,
                            json.dumps(
                                dict(effective_audit_intent.metadata),
                                ensure_ascii=True,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            content_hash,
                            audit_head,
                            audit_hash,
                        ),
                    )
            self._evaluation_write_checkpoint("before_commit")
            self._conn.commit()
            return inserted
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def _evaluation_write_checkpoint(self, stage: str) -> None:
        """Test seam for evaluation, search-link, and audit-intent atomicity."""
        del stage

    def get_evaluation(self, run_id: str) -> Optional[dict]:
        self._require_schema()
        row = self._conn.execute("SELECT * FROM evaluations WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row is not None else None

    def list_evaluations(self, alpha_id: str) -> list[dict]:
        self._require_schema()
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT * FROM evaluations WHERE alpha_id = ? ORDER BY recorded_ts, run_id", (alpha_id,)
            )
        ]

    def pending_evaluation_audits(
        self,
        *,
        run_id: str | None = None,
        limit: int = 1_000,
    ) -> tuple[dict, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit < 1:
            raise ValueError("limit must be positive")
        if run_id is not None:
            run_id = self._require_text(run_id, "run_id")
        self._ensure_search_schema()
        self._verified_evaluation_audit_state()
        where = " WHERE d.event_id IS NULL"
        parameters: list[object] = []
        if run_id is not None:
            where += " AND o.run_id=?"
            parameters.append(run_id)
        parameters.append(limit)
        rows = self._conn.execute(
            "SELECT o.audit_seq,o.run_id,o.event_id,o.actor,o.action,o.timestamp,"
            " o.target,o.metadata_json,o.evaluation_content_hash"
            " FROM evaluation_audit_outbox o"
            " LEFT JOIN evaluation_audit_dispatch d ON d.event_id=o.event_id"
            f"{where} ORDER BY o.audit_seq LIMIT ?",
            parameters,
        ).fetchall()
        return tuple(
            {
                "run_id": row["run_id"],
                "event_id": row["event_id"],
                "actor": row["actor"],
                "action": row["action"],
                "timestamp": row["timestamp"],
                "target": row["target"],
                "metadata": json.loads(row["metadata_json"]),
                "evaluation_content_hash": row["evaluation_content_hash"],
            }
            for row in rows
        )

    def mark_evaluation_audit_dispatched(
        self,
        event_id: str,
        *,
        dispatched_at: int,
        audit_entry_hash: str,
    ) -> bool:
        event_id = self._require_text(event_id, "event_id")
        dispatched_at = self._require_time(dispatched_at, "dispatched_at")
        audit_entry_hash = self._require_text(
            audit_entry_hash, "audit_entry_hash"
        )
        self._ensure_search_schema()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            _, intents, dispatch_head = self._verified_evaluation_audit_state()
            if event_id not in intents:
                raise ExperimentStoreError(
                    f"unknown evaluation audit event: {event_id}"
                )
            existing = self._conn.execute(
                "SELECT dispatched_at,audit_entry_hash FROM evaluation_audit_dispatch"
                " WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["dispatched_at"] == dispatched_at
                    and existing["audit_entry_hash"] == audit_entry_hash
                ):
                    self._conn.rollback()
                    return False
                raise ExperimentStoreError(
                    "evaluation audit dispatch acknowledgement conflict"
                )
            digest = _evaluation_audit_dispatch_hash(
                event_id=event_id,
                dispatched_at=dispatched_at,
                audit_entry_hash=audit_entry_hash,
                prev_record_hash=dispatch_head,
            )
            self._conn.execute(
                "INSERT INTO evaluation_audit_dispatch"
                " (event_id,dispatched_at,audit_entry_hash,prev_record_hash,record_hash)"
                " VALUES (?,?,?,?,?)",
                (
                    event_id,
                    dispatched_at,
                    audit_entry_hash,
                    dispatch_head,
                    digest,
                ),
            )
            self._conn.commit()
            return True
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def evaluation_audit_chain_valid(self) -> bool:
        try:
            self._ensure_search_schema()
            self._verified_evaluation_audit_state()
            return True
        except (ExperimentStoreError, sqlite3.DatabaseError):
            return False

    def close(self) -> None:
        self._conn.close()
