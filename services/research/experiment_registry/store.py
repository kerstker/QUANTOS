"""Stdlib-sqlite3 store for the experiment registry (persistence layer).

Owns the schema and ALL SQL. No domain logic, no imports from services/alpha.
Append-only / write-once: trials dedup by PK, evaluations are immutable, alpha
state is upserted. Fail-closed: reading an uninitialized/corrupt store raises
ExperimentStoreError rather than reading as empty (an empty trial population
would un-deflate Deflated Sharpe)."""

from __future__ import annotations

from quantos.contracts.research_registry_identity import (
    search_identity_hash as _search_identity_hash, trial_oos_access_hash as _trial_oos_access_hash,
)

import json
import os
import sqlite3
import threading
from functools import lru_cache
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Iterator, Mapping, Optional, Sequence

from quantos.contracts.events import EventOutboxMessage

from services.research.experiment_registry.evaluation_artifact import (
    validate_evaluation_artifact_write,
)
from services.research.experiment_registry.family_schema_v2_activation import (
    verify_exact_v2_search_writer_source,
)
from services.research.experiment_registry.family_schema_v2_catalog import (
    EXACT_V2_INHERITED_SCHEMA_CATALOG,
)
from services.research.experiment_registry.family_schema_v2_semantics import (
    canonical_hash,
    dispatch_hash as _dispatch_hash,
    evaluation_audit_dispatch_hash as _evaluation_audit_dispatch_hash,
    evaluation_audit_hash as _evaluation_audit_hash,
    evaluation_audit_intent_from_row as _v2_evaluation_audit_intent_from_row,
    event_json as _event_json,
    message_from_row as _v2_message_from_row,
    outbox_hash as _outbox_hash,
    search_candidate_hash as _search_candidate_hash,
    search_evaluation_hash as _search_evaluation_hash,
    search_event_hash as _search_event_hash,
    search_heartbeat_hash as _search_heartbeat_hash,
    search_recovery_hash as _search_recovery_hash,
    verify_dispatch_rows as _v2_verify_dispatch_rows,
    verify_evaluation_audit_state,
    verify_event_outbox_state,
    verify_outbox_rows as _v2_verify_outbox_rows,
    verify_search_state,
)
from services.research.experiment_registry.family_schema_v3_verifier import (
    FamilyAuthorityExternalProviders,
    FamilyAuthorityVerificationError,
    FamilyAuthorityVerificationEvidence,
    verify_family_authority,
)
from services.research.ports.persistence import (
    EvaluationArtifactWrite,
    EvaluationAuditIntent,
    ResearchEvidenceArtifactWrite,
    ProjectResearchEvidenceArtifactWrite,
    ExperimentStoreError,
    ImmutableArtifactError,
    TrialPersistenceError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, keeps the store import-light
    from services.research.trial_provenance import TrialProvenance


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
# 727 C2 lazy migration: the three orthogonal lifecycle machines (design doc
# 2026-07-27-three-orthogonal-state-machines-design.md §6) land alongside the
# legacy mixed `state` column. All nullable with NO default — a pre-C2 row reads
# back NULL and the loader falls back to project(legacy state); fabricating
# values for rows whose dimensions were never recorded would be exactly the
# backfill this migration refuses to do.
_TRIPLE_COLUMNS = {
    "research_state": "TEXT",
    "deployment_state": "TEXT",
    "health_state": "TEXT",
    "research_reason": "TEXT",
    "deployment_reason": "TEXT",
    "health_reason": "TEXT",
    # C3: rows whose triple diverges from project(legacy state) need their loss
    # markers persisted (C2 rows are exact projections, so NULL means "derive
    # from the legacy state" — never a fabricated marker list).
    "lossy_json": "TEXT",
}

_ALPHA_BASE_COLUMNS = (
    "alpha_id",
    "definition_json",
    "state",
    "returns_json",
    "sharpe",
    "pvalue",
)


def _alpha_insert_sql(*, on_conflict_update: bool) -> str:
    """Single source for the alpha_state INSERT column list — the base columns
    plus every 727 C2/C3 triple column, so the three write paths cannot drift."""
    columns = list(_ALPHA_BASE_COLUMNS) + list(_TRIPLE_COLUMNS)
    sql = (
        f"INSERT INTO alpha_state({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})"
    )
    if on_conflict_update:
        updates = ", ".join(
            f"{column}=excluded.{column}"
            for column in columns
            if column != "alpha_id"
        )
        sql += f" ON CONFLICT(alpha_id) DO UPDATE SET {updates}"
    return sql


def _alpha_row_params(row: Mapping[str, object]) -> tuple:
    params: list[object] = [row[column] for column in _ALPHA_BASE_COLUMNS[:4]]
    params.extend(row.get(column) for column in _ALPHA_BASE_COLUMNS[4:])
    params.extend(row.get(column) for column in _TRIPLE_COLUMNS)
    return tuple(params)
_OUTBOX_TABLES = ("experiment_event_outbox", "experiment_event_dispatch")
_SEARCH_TABLES = ("search_attempt_events", "search_candidates")
_SEARCH_ACTIVITY_TABLES = (
    "search_attempt_heartbeats",
    "search_attempt_recoveries",
    "search_evaluations",
    "trial_provenance",
    "trial_oos_access",
)
_EVALUATION_AUDIT_TABLES = ("evaluation_audit_outbox", "evaluation_audit_dispatch")
_SEARCH_TERMINAL_STATUSES = frozenset({"FAILED", "COMPLETED"})
_SEARCH_RECOVERY_REASON = "STALE_STARTED_TIMEOUT"
# v3 adds trial_provenance (727 §7.1 TrialRecord). The migration replays the
# whole _SEARCH_SCHEMA script and every statement is CREATE ... IF NOT EXISTS,
# so a v2 store gains only the new table + triggers and keeps every existing row.
_SEARCH_SCHEMA_VERSION = 4
_RESEARCH_EVIDENCE_SCHEMA_VERSION = 5
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
    "trial_provenance_no_update": ("trial_provenance", "update"),
    "trial_provenance_no_delete": ("trial_provenance", "delete"),
    "trial_oos_access_no_update": ("trial_oos_access", "update"),
    "trial_oos_access_no_delete": ("trial_oos_access", "delete"),
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
CREATE TABLE IF NOT EXISTS trial_provenance (
    provenance_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_id TEXT UNIQUE NOT NULL,
    research_session_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at INTEGER NOT NULL CHECK(started_at >= 0),
    completed_at INTEGER NOT NULL CHECK(completed_at >= started_at),
    parent_trial_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    expression_hash TEXT NOT NULL,
    parameter_hash TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    oos_peek_count INTEGER NOT NULL CHECK(oos_peek_count >= 0),
    record_json TEXT NOT NULL,
    prev_entry_hash TEXT NOT NULL,
    entry_hash TEXT UNIQUE NOT NULL
);
CREATE INDEX IF NOT EXISTS trial_provenance_by_config
ON trial_provenance(config_hash, provenance_seq);
CREATE INDEX IF NOT EXISTS trial_provenance_by_session
ON trial_provenance(research_session_id, provenance_seq);
CREATE TRIGGER IF NOT EXISTS trial_provenance_no_update
BEFORE UPDATE ON trial_provenance
BEGIN SELECT RAISE(ABORT, 'trial_provenance is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trial_provenance_no_delete
BEFORE DELETE ON trial_provenance
BEGIN SELECT RAISE(ABORT, 'trial_provenance is append-only'); END;
CREATE TABLE IF NOT EXISTS trial_oos_access (
    access_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    trial_id TEXT NOT NULL,
    method TEXT NOT NULL,
    boundary TEXT NOT NULL,
    viewed_at INTEGER NOT NULL CHECK(viewed_at >= 0),
    prev_entry_hash TEXT NOT NULL,
    entry_hash TEXT UNIQUE NOT NULL,
    UNIQUE(trial_id, method, boundary)
);
CREATE INDEX IF NOT EXISTS trial_oos_access_by_trial
ON trial_oos_access(trial_id, access_seq);
CREATE TRIGGER IF NOT EXISTS trial_oos_access_no_update
BEFORE UPDATE ON trial_oos_access
BEGIN SELECT RAISE(ABORT, 'trial_oos_access is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trial_oos_access_no_delete
BEFORE DELETE ON trial_oos_access
BEGIN SELECT RAISE(ABORT, 'trial_oos_access is append-only'); END;
"""


def _canonical_hash(payload: Mapping[str, object]) -> str:
    """Compatibility alias for historical private tests and repair tooling."""
    return canonical_hash(payload)






def _create_exact_v2_schema(connection: sqlite3.Connection) -> None:
    """Materialize the machine-pinned v2 schema without any v4 additions."""
    entries = EXACT_V2_INHERITED_SCHEMA_CATALOG.sqlite_schema_entries
    for object_type in ("table", "index", "trigger"):
        for entry in entries:
            if (
                entry.object_type == object_type
                and entry.name != "sqlite_sequence"
                and entry.sql is not None
            ):
                connection.execute(entry.sql)
    connection.execute(
        f"PRAGMA user_version = {EXACT_V2_INHERITED_SCHEMA_CATALOG.schema_version}"
    )


def _evidence_catalog(connection):
    return tuple(tuple(row) for row in connection.execute(
        'SELECT type,name,tbl_name,sql FROM main.sqlite_schema ORDER BY type,name'))


@lru_cache(maxsize=1)
def _evidence_catalogs():
    """Only the three existing local physical profiles; no authority catalog."""
    connection = sqlite3.connect(':memory:')
    try:
        _create_exact_v2_schema(connection)
        profiles = {'EXACT_V2': _evidence_catalog(connection)}
        connection.executescript(_SCHEMA + _SEARCH_SCHEMA)
        profiles['EXACT_V4'] = _evidence_catalog(connection)
        for name, ddl in _TRIPLE_COLUMNS.items():
            connection.execute(f'ALTER TABLE alpha_state ADD COLUMN {name} {ddl}')
        profiles['EXACT_V4_C2'] = _evidence_catalog(connection)
        return profiles
    finally:
        connection.close()


def _complete_exact_v2_schema(connection: sqlite3.Connection) -> None:
    """Complete a legacy v0/v1 store to exact v2 inside the caller transaction."""

    existing = {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT type,name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
        )
    }
    for object_type in ("table", "index", "trigger"):
        for entry in EXACT_V2_INHERITED_SCHEMA_CATALOG.sqlite_schema_entries:
            if (
                entry.object_type == object_type
                and entry.name != "sqlite_sequence"
                and entry.sql is not None
                and (entry.object_type, entry.name) not in existing
            ):
                connection.execute(entry.sql)
    connection.execute(
        f"PRAGMA user_version = {EXACT_V2_INHERITED_SCHEMA_CATALOG.schema_version}"
    )


@dataclass(frozen=True, slots=True)
class _WriteLease:
    """Opaque capability for one exact store-owned SQLite transaction."""

    connection: sqlite3.Connection
    generation: int
    owner_thread: int


class SqliteExperimentStore:
    def __init__(
        self,
        path: str,
        *,
        create: bool = False,
        read_only: bool = False,
        expected_authority_instance_id: str | None = None,
        external_providers: FamilyAuthorityExternalProviders | None = None,
        project_result_reader=None,
        lock_timeout_seconds: float = 30.0,
    ) -> None:
        # Research batches share one append-only ledger. Wait for SQLite's
        # existing writer before BEGIN; never retry a transaction body or
        # wait without a bound. Callers with a tighter latency budget may lower it.
        if type(lock_timeout_seconds) not in (int, float) or not 0 <= lock_timeout_seconds <= 60:
            raise ValueError("lock_timeout_seconds must be finite and between 0 and 60")
        if create and read_only:
            raise ExperimentStoreError("create and read_only are mutually exclusive")
        if read_only and path == ":memory:":
            raise ExperimentStoreError("read-only experiment store requires a file authority")
        connection = None
        try:
            target = Path(path).resolve().as_uri() + "?mode=ro" if read_only else path
            connection = sqlite3.connect(target, timeout=lock_timeout_seconds, uri=read_only)
            if read_only:
                connection.execute("PRAGMA query_only=ON")
        except (OSError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
            if connection is not None:
                connection.close()
            if read_only:
                raise ExperimentStoreError("experiment store unavailable") from exc
            raise
        self._conn = connection
        self._conn.row_factory = sqlite3.Row
        self._database_path = next(row['file'] for row in self._conn.execute('PRAGMA database_list')
                                   if row['name'] == 'main')
        self._database_identity = self._project_database_identity() if self._database_path else None
        self._conn.execute("PRAGMA foreign_keys=ON")
        if self._conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            self._conn.close()
            raise ExperimentStoreError("experiment store requires foreign_keys=ON")
        self._expected_authority_instance_id = expected_authority_instance_id
        self._external_providers = external_providers
        if project_result_reader is not None:
            from services.research.project_result_material_reader import ProjectResultMaterialReader
            if type(project_result_reader) is not ProjectResultMaterialReader:
                self._conn.close()
                raise ValueError('PROJECT_REPORT_READER_CONFIGURATION')
        self._project_result_reader = project_result_reader
        self._write_generation = 0
        self._active_write_lease: _WriteLease | None = None
        self._connection_invalidated = False
        if create:
            existing = self._conn.execute(
                "SELECT 1 FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
            if existing is None:
                _create_exact_v2_schema(self._conn)
                self._conn.commit()

    def ensure_event_outbox_schema(self) -> None:
        """Add the legacy owner-store outbox without changing schema authority."""

        self._reject_write_ownership_conflict()
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def _reject_write_ownership_conflict(self) -> None:
        """Reject external/reentrant ownership before schema or transaction work."""
        if self._connection_invalidated:
            raise ExperimentStoreError("experiment store connection is invalidated")
        if self._active_write_lease is not None:
            raise ExperimentStoreError("active store write lease forbids reentrant mutation")
        if self._conn.in_transaction:
            raise ExperimentStoreError("caller-owned transaction forbids store mutation")

    def _commit_owned_transaction(self, conn: sqlite3.Connection) -> None:
        conn.commit()

    def _rollback_owned_transaction(self, conn: sqlite3.Connection) -> None:
        conn.rollback()

    def _write_lease_checkpoint(self, stage: str) -> None:
        """Test seam covering the post-BEGIN, pre-lease rollback boundary."""
        del stage

    def _invalidate_connection(self, detail: str, cause: BaseException) -> None:
        self._connection_invalidated = True
        self._active_write_lease = None
        try:
            self._conn.close()
        except BaseException:
            pass
        raise ExperimentStoreError(f"store-owned transaction {detail}; connection invalidated") from cause

    @contextmanager
    def _immediate_transaction(
        self,
    ) -> Iterator[tuple[sqlite3.Connection, _WriteLease]]:
        """Own one exact transaction and expose only its opaque lease capability."""
        self._reject_write_ownership_conflict()
        conn = self._conn
        conn.execute("BEGIN IMMEDIATE")
        lease: _WriteLease | None = None
        try:
            self._write_lease_checkpoint("after_begin")
            self._require_search_write_permission(self._schema_version())
            self._write_generation += 1
            lease = _WriteLease(
                connection=conn,
                generation=self._write_generation,
                owner_thread=threading.get_ident(),
            )
            self._active_write_lease = lease
        except BaseException as setup_exc:
            if not conn.in_transaction:
                self._invalidate_connection("lost its active transaction during lease setup", setup_exc)
            try:
                self._rollback_owned_transaction(conn)
            except BaseException as rollback_exc:
                self._invalidate_connection("rollback failed", rollback_exc)
            raise
        try:
            yield conn, lease
        except BaseException as exc:
            if not conn.in_transaction:
                self._invalidate_connection("lost its active transaction before rollback", exc)
            try:
                self._rollback_owned_transaction(conn)
            except BaseException as rollback_exc:
                self._invalidate_connection("rollback failed", rollback_exc)
            raise
        else:
            if not conn.in_transaction:
                self._invalidate_connection(
                    "lost its active transaction before commit",
                    ExperimentStoreError("owned transaction is inactive"),
                )
            try:
                self._commit_owned_transaction(conn)
            except BaseException as commit_exc:
                self._invalidate_connection("commit failed", commit_exc)
        finally:
            if lease is not None and self._active_write_lease is lease:
                self._active_write_lease = None

    def _require_write_lock(
        self,
        conn: sqlite3.Connection,
        lease: object,
    ) -> None:
        if conn is not self._conn:
            raise ExperimentStoreError("locked primitive received a foreign connection")
        if not isinstance(lease, _WriteLease):
            raise ExperimentStoreError("locked primitive received a forged write lease")
        if lease.owner_thread != threading.get_ident():
            raise ExperimentStoreError("write lease used from the wrong thread")
        if (
            self._active_write_lease is not lease
            or lease.connection is not conn
            or lease.generation != self._write_generation
        ):
            raise ExperimentStoreError("locked primitive received a stale write lease")
        if not conn.in_transaction:
            raise ExperimentStoreError("locked primitive requires a store-owned write transaction")

    def _require_schema(self) -> None:
        try:
            rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise ExperimentStoreError(f"store unreadable: {exc}") from exc
        present = {r["name"] for r in rows}
        missing = [t for t in _TABLES if t not in present]
        if missing:
            raise ExperimentStoreError(f"store uninitialized; missing tables: {missing}")

    def _alpha_state_columns(self) -> set[str]:
        return {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(alpha_state)").fetchall()
        }

    def _ensure_triple_columns(self) -> None:
        """Lazily add the 727 C2 dual-write columns to an existing valid store."""

        self._reject_write_ownership_conflict()
        self._require_schema()
        if not (_TRIPLE_COLUMNS.keys() - self._alpha_state_columns()):
            return
        if self._schema_version() == 3:
            # Exact family-authority v3 has a machine-pinned namespace.  It
            # cannot be altered in place without invalidating its authority.
            return
        if self._schema_version() == _RESEARCH_EVIDENCE_SCHEMA_VERSION:
            with self._immediate_transaction() as (conn, lease):
                self._prepare_triple_columns_locked(conn, lease)
            return
        self._ensure_search_schema(require_provenance=True)
        if not (_TRIPLE_COLUMNS.keys() - self._alpha_state_columns()):
            return
        with self._immediate_transaction() as (conn, _lease):
            present = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(alpha_state)").fetchall()
            }
            for column, ddl in _TRIPLE_COLUMNS.items():
                if column not in present:
                    conn.execute(
                        f"ALTER TABLE alpha_state ADD COLUMN {column} {ddl}"
                    )

    def _has_sealed_v2_history_locked(self, conn: sqlite3.Connection) -> bool:
        """Return whether v2 already contains authority-bearing history."""

        for table in (
            "search_attempt_events",
            "evaluations",
            "experiment_event_outbox",
            "evaluation_audit_outbox",
        ):
            if conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None:
                return True
        return False

    def _prepare_triple_columns_locked(
        self,
        conn: sqlite3.Connection,
        lease: object,
    ) -> bool:
        """Prepare C2 columns inside the caller's one owned transaction.

        A populated exact-v2 authority is immutable and remains on the legacy
        projection path. Empty/pre-C2 stores move to v4 atomically with the
        caller's first dual write.
        """

        self._require_write_lock(conn, lease)
        present = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(alpha_state)").fetchall()
        }
        if not (_TRIPLE_COLUMNS.keys() - present):
            return True
        version = self._schema_version()
        if version == 3:
            return False
        if version == _RESEARCH_EVIDENCE_SCHEMA_VERSION:
            # The approved exact-v2 profile keeps its original projection,
            # just like populated v2. Original v4 lifecycle writes may add
            # only their existing C2 columns, in this same owned transaction.
            profile = self._evidence_profile()
            if profile == 'EXACT_V2':
                return False
            for column, ddl in _TRIPLE_COLUMNS.items():
                if column not in present:
                    conn.execute(f'ALTER TABLE alpha_state ADD COLUMN {column} {ddl}')
            self._evidence_profile()
            return True
        if version == 2 and self._has_sealed_v2_history_locked(conn):
            return False
        if version not in (2, _SEARCH_SCHEMA_VERSION):
            raise ExperimentStoreError(
                f"lifecycle migration requires user_version 2 or 4, found {version}"
            )
        if version == 2:
            statement = ""
            for line in f"{_SCHEMA}\n{_SEARCH_SCHEMA}".splitlines():
                statement = f"{statement}{line}\n"
                if not sqlite3.complete_statement(statement):
                    continue
                sql = statement.strip()
                statement = ""
                if sql:
                    conn.execute(sql)
            if statement.strip():
                raise ExperimentStoreError(
                    "lifecycle migration schema contains an incomplete statement"
                )
        for column, ddl in _TRIPLE_COLUMNS.items():
            if column not in present:
                conn.execute(f"ALTER TABLE alpha_state ADD COLUMN {column} {ddl}")
        if version == 2:
            conn.execute(f"PRAGMA user_version = {_SEARCH_SCHEMA_VERSION}")
        return True

    def _schema_version(self) -> int:
        try:
            row = self._conn.execute("PRAGMA user_version").fetchone()
        except sqlite3.DatabaseError as exc:
            raise ExperimentStoreError(f"store unreadable: {exc}") from exc
        if row is None or len(row) != 1 or type(row[0]) is not int:
            raise ExperimentStoreError("experiment store user_version is unreadable")
        return int(row[0])

    def _require_family_schema_v3(self) -> FamilyAuthorityVerificationEvidence:
        """Verify exact v3 without migration, writes, or transaction control."""

        version = self._schema_version()
        if version != 3:
            raise ExperimentStoreError(
                f"family authority requires exact user_version 3, found {version}"
            )
        expected = self._expected_authority_instance_id
        if type(expected) is not str or not expected:
            raise ExperimentStoreError("family authority expected authority_instance_id is not configured")
        try:
            return verify_family_authority(
                self._conn,
                expected_authority_instance_id=expected,
                external_providers=self._external_providers,
            )
        except (FamilyAuthorityVerificationError, TypeError, sqlite3.DatabaseError) as exc:
            raise ExperimentStoreError(f"family authority verification failed: {exc}") from exc

    def _require_search_write_permission(self, version: int) -> None:
        if version == _RESEARCH_EVIDENCE_SCHEMA_VERSION:
            self._verify_research_evidence_store()
            return
        if version not in (2, 3, _SEARCH_SCHEMA_VERSION):
            raise ExperimentStoreError(f"unsupported experiment store user_version: {version}")
        if version == 2:
            verify_exact_v2_search_writer_source(self._conn)
            return
        if version == 3:
            self._require_search_schema(allow_v3=True)
            verify_search_state(self._conn)
            self._require_family_schema_v3()
            return
        self._require_search_schema()
        verify_search_state(self._conn)
        if self._expected_authority_instance_id is not None:
            self._require_family_schema_v3()

    def _require_runtime_mutation_schema(self) -> None:
        """Gate legacy mutators on known Phase-A versions before ``BEGIN``."""

        self._ensure_search_schema()

    @contextmanager
    def _search_read_snapshot(self) -> Iterator[None]:
        """Pin the implicit read snapshot until every preflight query finishes.

        An unfinished SELECT keeps SQLite's read snapshot on this connection.
        Close it before the existing BEGIN IMMEDIATE; preflight must not add
        transaction-control statements or acquire a store write lease.
        """
        self._reject_write_ownership_conflict()
        cursor = self._conn.execute("SELECT name FROM main.sqlite_schema")
        try:
            if cursor.fetchone() is None:
                raise ExperimentStoreError("store uninitialized; missing tables")
            yield
        finally:
            cursor.close()

    def _ensure_search_schema(self, *, require_provenance: bool = False) -> None:
        """Migrate only an already-valid experiment store on the first search write."""
        self._reject_write_ownership_conflict()
        migration_started = False
        try:
            with self._search_read_snapshot():
                self._require_schema()
                current_version = self._schema_version()
                if current_version == _RESEARCH_EVIDENCE_SCHEMA_VERSION:
                    self._verify_research_evidence_store()
                    if require_provenance and not self._evidence_has_provenance():
                        raise ExperimentStoreError('version 5 exact-v2 profile has no trial provenance capability')
                    return
                if current_version > _SEARCH_SCHEMA_VERSION:
                    raise ExperimentStoreError(f"unsupported experiment store user_version: {current_version}")
                if current_version == _SEARCH_SCHEMA_VERSION:
                    self._require_search_write_permission(current_version)
                    return
                if current_version == 3:
                    self._require_search_write_permission(current_version)
                    if require_provenance:
                        raise ExperimentStoreError(
                            "trial provenance is unavailable in exact family schema v3"
                        )
                    return
                if current_version == 2:
                    self._require_search_write_permission(current_version)
                    if not require_provenance:
                        return

            self._conn.execute("BEGIN IMMEDIATE")
            migration_started = True
            locked_version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if locked_version == _SEARCH_SCHEMA_VERSION:
                self._require_search_write_permission(locked_version)
                try:
                    self._commit_owned_transaction(self._conn)
                except BaseException as commit_exc:
                    migration_started = False
                    self._invalidate_connection("migration commit failed", commit_exc)
                migration_started = False
                with self._search_read_snapshot():
                    self._require_search_write_permission(locked_version)
                return
            if locked_version > _SEARCH_SCHEMA_VERSION:
                raise ExperimentStoreError(f"unsupported experiment store user_version: {locked_version}")
            if locked_version < 2:
                _complete_exact_v2_schema(self._conn)
                self._migration_checkpoint("exact_v2_complete")
                verify_exact_v2_search_writer_source(self._conn)
                if not require_provenance:
                    self._commit_owned_transaction(self._conn)
                    migration_started = False
                    return
                locked_version = 2
            self._require_search_write_permission(locked_version)

            statement = ""
            statement_index = 0
            for line in f"{_SCHEMA}\n{_SEARCH_SCHEMA}".splitlines():
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
                raise ExperimentStoreError("search provenance schema contains an incomplete statement")
            self._conn.execute(f"PRAGMA user_version = {_SEARCH_SCHEMA_VERSION}")
            self._migration_checkpoint("before_commit")
            self._require_search_write_permission(_SEARCH_SCHEMA_VERSION)
            try:
                self._commit_owned_transaction(self._conn)
            except BaseException as commit_exc:
                migration_started = False
                self._invalidate_connection("migration commit failed", commit_exc)
            migration_started = False
        except BaseException as exc:
            if migration_started and self._conn.in_transaction:
                try:
                    self._rollback_owned_transaction(self._conn)
                except BaseException as rollback_exc:
                    self._invalidate_connection("migration rollback failed", rollback_exc)
            if isinstance(exc, ExperimentStoreError):
                raise
            if not isinstance(exc, Exception):
                raise
            raise ExperimentStoreError(f"search provenance schema migration failed: {exc}") from exc

    def _migration_checkpoint(self, stage: str) -> None:
        """Test seam for proving that every partial migration rolls back."""
        del stage

    def _require_search_schema(self, *, allow_v3: bool = False) -> int:
        self._require_schema()
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version == _RESEARCH_EVIDENCE_SCHEMA_VERSION:
            self._evidence_profile()
            return version
        allowed_versions = (
            (2, 3, _SEARCH_SCHEMA_VERSION)
            if allow_v3
            else (2, _SEARCH_SCHEMA_VERSION)
        )
        if version not in allowed_versions:
            expected = "2, 3, or 4" if allow_v3 else "2 or 4"
            raise ExperimentStoreError(
                f"search provenance requires user_version {expected}, found {version}"
            )
        try:
            rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        except sqlite3.DatabaseError as exc:
            raise ExperimentStoreError(f"store unreadable: {exc}") from exc
        present = {row["name"] for row in rows}
        activity_tables = (
            _SEARCH_ACTIVITY_TABLES
            if version == _SEARCH_SCHEMA_VERSION
            else tuple(
                table
                for table in _SEARCH_ACTIVITY_TABLES
                if table not in {"trial_provenance", "trial_oos_access"}
            )
        )
        required_tables = _SEARCH_TABLES + activity_tables + _EVALUATION_AUDIT_TABLES
        missing = [table for table in required_tables if table not in present]
        if missing:
            raise ExperimentStoreError(f"search provenance unavailable; missing tables: {missing}")
        link_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(search_evaluations)")}
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
                f"search evaluation binding schema is stale; missing columns: {missing_link_columns}"
            )
        triggers = {
            row["name"]: (row["sql"] or "").lower()
            for row in self._conn.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")
        }
        for name, (table, action) in _SEARCH_APPEND_ONLY_TRIGGERS.items():
            if version != _SEARCH_SCHEMA_VERSION and table in {
                "trial_provenance",
                "trial_oos_access",
            }:
                continue
            sql = triggers.get(name, "")
            if f"before {action} on {table}" not in " ".join(sql.split()) or "raise" not in sql:
                raise ExperimentStoreError(f"append-only trigger missing or invalid: {name}")
        return version

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
        if self._schema_version() == 2:
            verified = verify_search_state(self._conn)
            # Preserve the semantic verifier's precise corruption diagnosis;
            # the exact-schema verifier then adds the namespace/source pin.
            verify_exact_v2_search_writer_source(self._conn)
            return verified.event_head, verified.candidate_head, verified.attempts
        version = self._require_search_schema(allow_v3=True)
        verified = verify_search_state(self._conn)
        if version == 3:
            self._require_family_schema_v3()
        return verified.event_head, verified.candidate_head, verified.attempts

    def _require_search_start_clock(self, recorded_at: int) -> None:
        row = self._conn.execute(
            "SELECT MAX(recorded_at) AS latest FROM search_attempt_events WHERE status='STARTED'"
        ).fetchone()
        latest = row["latest"]
        if latest is not None and recorded_at < latest:
            raise ExperimentStoreError(f"search STARTED clock rollback: recorded_at={recorded_at} latest={latest}")

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
        rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        present = {row["name"] for row in rows}
        missing = [table for table in _OUTBOX_TABLES if table not in present]
        if missing:
            raise ExperimentStoreError(f"experiment event outbox unavailable; missing tables: {missing}")

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
        return _v2_message_from_row(row)

    @classmethod
    def _verify_outbox_rows(cls, rows: Sequence[sqlite3.Row]) -> tuple[str, dict[str, EventOutboxMessage]]:
        del cls
        return _v2_verify_outbox_rows(rows)

    @staticmethod
    def _verify_dispatch_rows(
        rows: Sequence[sqlite3.Row],
        messages: Mapping[str, EventOutboxMessage],
    ) -> str:
        return _v2_verify_dispatch_rows(rows, messages)

    def _verified_outbox_state(
        self,
    ) -> tuple[str, dict[str, EventOutboxMessage], str]:
        self._require_outbox_schema()
        verified = verify_event_outbox_state(self._conn)
        return (
            verified.outbox_head,
            verified.messages,
            verified.dispatch_head,
        )

    def _append_event_outbox_locked(
        self,
        conn: sqlite3.Connection,
        lease: object,
        messages: Sequence[EventOutboxMessage],
    ) -> None:
        self._require_write_lock(conn, lease)
        outbox_head, _, _ = self._verified_outbox_state()
        for message in messages:
            existing = conn.execute(
                "SELECT outbox_seq,outbox_id,event_json,correlation_id,"
                " causation_id,repro_hash,prev_record_hash,record_hash"
                " FROM experiment_event_outbox WHERE outbox_id=?",
                (message.outbox_id,),
            ).fetchone()
            if existing is not None:
                if self._message_from_row(existing) != message:
                    raise ImmutableArtifactError(f"experiment outbox event conflict: {message.outbox_id}")
                continue
            digest = _outbox_hash(message, outbox_head)
            conn.execute(
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
            self._event_outbox_write_checkpoint("after_outbox")

    def _event_outbox_write_checkpoint(self, stage: str) -> None:
        """Test seam proving catalog state and event intent cannot split."""
        del stage

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
        return _v2_evaluation_audit_intent_from_row(row)

    def _verified_evaluation_audit_state(
        self,
    ) -> tuple[str, dict[str, tuple[str, EvaluationAuditIntent]], str]:
        version = self._require_search_schema(allow_v3=True)
        if version == 3:
            verify_search_state(self._conn)
            self._require_family_schema_v3()
        verified = verify_evaluation_audit_state(self._conn)
        return verified.audit_head, verified.intents, verified.dispatch_head

    def add_trials(self, config_hashes: Iterable[str], *, first_seen_ts: int) -> None:
        self._reject_write_ownership_conflict()
        self._require_runtime_mutation_schema()
        with self._immediate_transaction() as (conn, _lease):
            conn.executemany(
                "INSERT OR IGNORE INTO trials(config_hash, first_seen_ts) VALUES (?, ?)",
                [(h, first_seen_ts) for h in config_hashes],
            )

    # --- trial provenance (727 §7.1) ----------------------------------------

    def _trial_provenance_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT provenance_seq,trial_id,config_hash,record_json,"
            " prev_entry_hash,entry_hash"
            " FROM trial_provenance ORDER BY provenance_seq"
        ).fetchall()

    def record_trial_provenance(self, record: "TrialProvenance") -> bool:
        """Append one immutable TrialRecord; return False if already retained.

        The config_hash is ALSO admitted into `trials` in the SAME transaction.
        A provenance row whose config never entered the trial population would
        let a real attempt escape the DSR/PBO/FDR denominator (§7.2) — the two
        writes must not be separable.
        """
        self._ensure_search_schema(require_provenance=True)
        payload = record.canonical_payload()
        record_json = json.dumps(
            payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT entry_hash FROM trial_provenance WHERE trial_id = ?",
                (record.trial_id,),
            ).fetchone()
            if existing is not None:
                self._conn.commit()
                return False
            head_row = self._conn.execute(
                "SELECT entry_hash FROM trial_provenance"
                " ORDER BY provenance_seq DESC LIMIT 1"
            ).fetchone()
            prev = "" if head_row is None else str(head_row["entry_hash"])
            entry = record.entry_hash(prev)
            self._conn.execute(
                "INSERT OR IGNORE INTO trials(config_hash, first_seen_ts) VALUES (?, ?)",
                (record.config_hash, record.started_at),
            )
            self._conn.execute(
                "INSERT INTO trial_provenance("
                " trial_id,research_session_id,config_hash,actor_id,actor_type,status,"
                " started_at,completed_at,parent_trial_id,hypothesis_id,expression_hash,"
                " parameter_hash,code_hash,oos_peek_count,record_json,prev_entry_hash,"
                " entry_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.trial_id,
                    record.research_session_id,
                    record.config_hash,
                    record.actor_id,
                    record.actor_type.value,
                    record.status.value,
                    record.started_at,
                    record.completed_at,
                    record.parent_trial_id,
                    record.hypothesis_id,
                    record.expression_hash,
                    record.parameter_hash,
                    record.code_hash,
                    record.oos_peek_count,
                    record_json,
                    prev,
                    entry,
                ),
            )
            self._conn.commit()
            return True
        except sqlite3.DatabaseError as exc:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise TrialPersistenceError(
                f"trial provenance could not be retained: {exc}"
            ) from exc

    def trial_provenance(self, trial_id: str) -> Optional[dict]:
        self._ensure_search_schema(require_provenance=True)
        row = self._conn.execute(
            "SELECT provenance_seq,trial_id,config_hash,record_json,"
            " prev_entry_hash,entry_hash FROM trial_provenance WHERE trial_id = ?",
            (trial_id,),
        ).fetchone()
        if row is None:
            return None
        return self._decoded_provenance_row(row)

    def trial_provenance_for_config(self, config_hash: str) -> list[dict]:
        """Every ATTEMPT at this config. §7.2 counts attempts, `trials` counts
        distinct configs — so this legitimately returns more than one row."""
        self._ensure_search_schema(require_provenance=True)
        return [
            self._decoded_provenance_row(row)
            for row in self._conn.execute(
                "SELECT provenance_seq,trial_id,config_hash,record_json,"
                " prev_entry_hash,entry_hash FROM trial_provenance"
                " WHERE config_hash = ? ORDER BY provenance_seq",
                (config_hash,),
            )
        ]

    @staticmethod
    def _decoded_provenance_row(row: sqlite3.Row) -> dict:
        try:
            record = json.loads(row["record_json"])
        except Exception as exc:
            raise ExperimentStoreError(
                f"trial provenance {row['trial_id']} record is corrupt"
            ) from exc
        if not isinstance(record, dict):
            raise ExperimentStoreError(
                f"trial provenance {row['trial_id']} record is not an object"
            )
        return {
            "provenance_seq": row["provenance_seq"],
            "trial_id": row["trial_id"],
            "config_hash": row["config_hash"],
            "record": record,
            "prev_entry_hash": row["prev_entry_hash"],
            "entry_hash": row["entry_hash"],
        }

    def trial_provenance_chain_valid(self) -> bool:
        """Recompute the whole provenance chain. Fail-closed: any corruption,
        gap, reordering, or a config missing from `trials` returns False."""
        try:
            self._ensure_search_schema(require_provenance=True)
            trials = {
                str(row["config_hash"])
                for row in self._conn.execute("SELECT config_hash FROM trials")
            }
            previous = ""
            for row in self._trial_provenance_rows():
                decoded = self._decoded_provenance_row(row)
                if row["prev_entry_hash"] != previous:
                    return False
                if decoded["config_hash"] not in trials:
                    return False
                expected = _canonical_hash(
                    {"entry": decoded["record"], "prev_hash": previous}
                )
                if row["entry_hash"] != expected:
                    return False
                previous = expected
            return True
        except Exception:
            return False

    def trial_provenance_head(self) -> str:
        """Current chain head ("" when empty); the value an evidence artifact
        pins so a later audit can prove no record was inserted retroactively."""
        self._ensure_search_schema(require_provenance=True)
        row = self._conn.execute(
            "SELECT entry_hash FROM trial_provenance"
            " ORDER BY provenance_seq DESC LIMIT 1"
        ).fetchone()
        return "" if row is None else str(row["entry_hash"])

    def record_search_oos_access(
        self,
        search_id: str,
        accesses: Mapping[str, Sequence[str]],
        *,
        viewed_at: int,
    ) -> int:
        """Append actual OOS boundaries viewed by every trial in a search.

        TrialRecord is written before data access and remains immutable. This
        companion chain records only boundaries returned by completed OOS
        validators. A persistence failure is fatal so OOS cannot be consumed
        without accounting for the peek.
        """
        search_id = self._require_text(search_id, "search_id")
        viewed_at = self._require_time(viewed_at, "viewed_at")
        normalized: list[tuple[str, str]] = []
        for method, boundaries in sorted(accesses.items()):
            method = self._require_text(method, "method")
            for boundary in sorted(set(boundaries)):
                normalized.append(
                    (method, self._require_text(boundary, "boundary"))
                )
        if not normalized:
            return 0
        self._ensure_search_schema(require_provenance=True)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._verified_search_state()
            trials = [
                str(row["trial_id"])
                for row in self._conn.execute(
                    "SELECT trial_id FROM trial_provenance"
                    " WHERE research_session_id=? ORDER BY provenance_seq",
                    (search_id,),
                )
            ]
            if not trials:
                raise TrialPersistenceError(
                    f"search {search_id} viewed OOS data without retained trials"
                )
            head_row = self._conn.execute(
                "SELECT entry_hash FROM trial_oos_access"
                " ORDER BY access_seq DESC LIMIT 1"
            ).fetchone()
            head = "" if head_row is None else str(head_row["entry_hash"])
            inserted = 0
            for trial_id in trials:
                for method, boundary in normalized:
                    existing = self._conn.execute(
                        "SELECT entry_hash FROM trial_oos_access"
                        " WHERE trial_id=? AND method=? AND boundary=?",
                        (trial_id, method, boundary),
                    ).fetchone()
                    if existing is not None:
                        continue
                    entry = _trial_oos_access_hash(
                        trial_id=trial_id,
                        method=method,
                        boundary=boundary,
                        viewed_at=viewed_at,
                        prev_entry_hash=head,
                    )
                    self._conn.execute(
                        "INSERT INTO trial_oos_access("
                        " trial_id,method,boundary,viewed_at,prev_entry_hash,entry_hash)"
                        " VALUES (?,?,?,?,?,?)",
                        (trial_id, method, boundary, viewed_at, head, entry),
                    )
                    head = entry
                    inserted += 1
            self._conn.commit()
            return inserted
        except TrialPersistenceError:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        except Exception as exc:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise TrialPersistenceError(
                f"OOS access provenance could not be retained: {exc}"
            ) from exc

    def trial_oos_access(self, trial_id: str) -> list[dict]:
        trial_id = self._require_text(trial_id, "trial_id")
        self._ensure_search_schema(require_provenance=True)
        return [
            dict(row)
            for row in self._conn.execute(
                "SELECT access_seq,trial_id,method,boundary,viewed_at,"
                " prev_entry_hash,entry_hash FROM trial_oos_access"
                " WHERE trial_id=? ORDER BY access_seq",
                (trial_id,),
            )
        ]

    def trial_oos_access_chain_valid(self) -> bool:
        try:
            self._ensure_search_schema(require_provenance=True)
            trial_ids = {
                str(row["trial_id"])
                for row in self._conn.execute("SELECT trial_id FROM trial_provenance")
            }
            previous = ""
            for row in self._conn.execute(
                "SELECT access_seq,trial_id,method,boundary,viewed_at,"
                " prev_entry_hash,entry_hash FROM trial_oos_access"
                " ORDER BY access_seq"
            ):
                if row["trial_id"] not in trial_ids:
                    return False
                if row["prev_entry_hash"] != previous:
                    return False
                expected = _trial_oos_access_hash(
                    trial_id=row["trial_id"],
                    method=row["method"],
                    boundary=row["boundary"],
                    viewed_at=row["viewed_at"],
                    prev_entry_hash=previous,
                )
                if row["entry_hash"] != expected:
                    return False
                previous = expected
            return True
        except Exception:
            return False

    def trial_oos_access_head(self) -> str:
        self._ensure_search_schema(require_provenance=True)
        row = self._conn.execute(
            "SELECT entry_hash FROM trial_oos_access"
            " ORDER BY access_seq DESC LIMIT 1"
        ).fetchone()
        return "" if row is None else str(row["entry_hash"])

    def oos_peek_totals(self) -> dict[str, int]:
        """Max distinct OOS boundaries any single attempt at each config had
        already viewed. §0.4.7 forbids calling re-inspected OOS data "unseen";
        this surfaces the number instead of leaving it unrecorded."""
        self._ensure_search_schema(require_provenance=True)
        per_config: dict[str, int] = {}
        for row in self._conn.execute(
            "SELECT trial_id,config_hash,record_json FROM trial_provenance"
            " ORDER BY provenance_seq"
        ):
            try:
                initial = set(
                    json.loads(row["record_json"]).get(
                        "viewed_oos_boundaries", ()
                    )
                )
            except Exception as exc:
                raise ExperimentStoreError(
                    f"trial provenance {row['trial_id']} record is corrupt"
                ) from exc
            appended = {
                f"{access['method']}:{access['boundary']}"
                for access in self._conn.execute(
                    "SELECT method,boundary FROM trial_oos_access WHERE trial_id=?",
                    (row["trial_id"],),
                )
            }
            count = len(initial | appended)
            config_hash = str(row["config_hash"])
            per_config[config_hash] = max(per_config.get(config_hash, 0), count)
        return per_config

    def begin_search_attempt(
        self,
        *,
        search_id: str,
        search_kind: str,
        source: str,
        dataset_version: str,
        recorded_at: int,
    ) -> bool:
        self._reject_write_ownership_conflict()
        search_id = self._require_text(search_id, "search_id")
        search_kind = self._require_text(search_kind, "search_kind")
        source = self._require_text(source, "source")
        dataset_version = self._require_text(dataset_version, "dataset_version")
        recorded_at = self._require_time(recorded_at, "recorded_at")
        self._ensure_search_schema()
        with self._immediate_transaction() as (conn, _lease):
            event_head, _, attempts = self._verified_search_state()
            self._require_search_start_clock(recorded_at)
            if search_id in attempts:
                raise ExperimentStoreError(f"search attempt {search_id} already exists")
            event_hash = _search_event_hash(
                search_id=search_id,
                search_kind=search_kind,
                source=source,
                dataset_version=dataset_version,
                status="STARTED",
                recorded_at=recorded_at,
                prev_event_hash=event_head,
            )
            conn.execute(
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
            return True

    def register_search_candidates(
        self,
        search_id: str,
        config_hashes: Iterable[str],
        *,
        registered_at: int,
    ) -> int:
        self._reject_write_ownership_conflict()
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
        with self._immediate_transaction() as (conn, _lease):
            _, candidate_head, attempts = self._verified_search_state()
            attempt = attempts.get(search_id)
            if attempt is None:
                raise ExperimentStoreError(f"search attempt {search_id} has not STARTED")
            if attempt["status"] in _SEARCH_TERMINAL_STATUSES:
                raise ExperimentStoreError(f"search attempt {search_id} is terminal")
            if registered_at < attempt["started_at"]:
                raise ExperimentStoreError(f"candidate time precedes search attempt {search_id}")
            if registered_at < attempt["last_activity_at"]:
                raise ExperimentStoreError(f"candidate clock rollback for search attempt {search_id}")

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
                conn.execute(
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
                conn.execute(
                    "INSERT OR IGNORE INTO trials(config_hash,first_seen_ts) VALUES (?,?)",
                    (config_hash, registered_at),
                )
                candidate_head = digest
                inserted += 1
            return inserted

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
        self._reject_write_ownership_conflict()
        search_id = self._require_text(search_id, "search_id")
        recorded_at = self._require_time(recorded_at, "recorded_at")
        self._ensure_search_schema()
        with self._immediate_transaction() as (conn, _lease):
            attempts = self._verified_search_state()[2]
            attempt = attempts.get(search_id)
            if attempt is None:
                raise ExperimentStoreError(f"search attempt {search_id} has not STARTED")
            if attempt["status"] in _SEARCH_TERMINAL_STATUSES:
                raise ExperimentStoreError(f"search attempt {search_id} is terminal")
            if recorded_at < attempt["last_activity_at"]:
                raise ExperimentStoreError(f"heartbeat clock rollback for search attempt {search_id}")
            existing = self._conn.execute(
                "SELECT heartbeat_hash FROM search_attempt_heartbeats WHERE search_id=? AND recorded_at=?",
                (search_id, recorded_at),
            ).fetchone()
            if existing is not None:
                return False
            head_row = self._conn.execute(
                "SELECT heartbeat_hash FROM search_attempt_heartbeats ORDER BY heartbeat_seq DESC LIMIT 1"
            ).fetchone()
            heartbeat_head = "" if head_row is None else head_row["heartbeat_hash"]
            digest = _search_heartbeat_hash(
                search_id=search_id,
                recorded_at=recorded_at,
                prev_heartbeat_hash=heartbeat_head,
            )
            conn.execute(
                "INSERT INTO search_attempt_heartbeats"
                " (search_id,recorded_at,prev_heartbeat_hash,heartbeat_hash)"
                " VALUES (?,?,?,?)",
                (search_id, recorded_at, heartbeat_head, digest),
            )
            return True

    def finish_search_attempt(
        self,
        search_id: str,
        *,
        status: str,
        recorded_at: int,
    ) -> bool:
        self._reject_write_ownership_conflict()
        search_id = self._require_text(search_id, "search_id")
        if status not in _SEARCH_TERMINAL_STATUSES:
            raise ValueError("status must be FAILED or COMPLETED")
        recorded_at = self._require_time(recorded_at, "recorded_at")
        self._ensure_search_schema()
        with self._immediate_transaction():
            event_head, _, attempts = self._verified_search_state()
            attempt = attempts.get(search_id)
            if attempt is None:
                raise ExperimentStoreError(f"search attempt {search_id} has not STARTED")
            if attempt["status"] in _SEARCH_TERMINAL_STATUSES:
                raise ExperimentStoreError(f"search attempt {search_id} is already terminal")
            if recorded_at < attempt["last_activity_at"]:
                raise ExperimentStoreError(f"terminal time precedes search attempt {search_id} provenance")
            self._append_search_terminal_event(
                search_id=search_id,
                attempt=attempt,
                status=status,
                recorded_at=recorded_at,
                event_head=event_head,
            )
            return True

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
        self._reject_write_ownership_conflict()
        recorded_at = self._require_time(recorded_at, "recorded_at")
        timeout_ms = self._require_time(timeout_ms, "timeout_ms")
        if timeout_ms == 0:
            raise ValueError("timeout_ms must be positive")
        self._ensure_search_schema()
        with self._immediate_transaction() as (conn, _lease):
            event_head, _, attempts = self._verified_search_state()
            cutoff = recorded_at - timeout_ms
            stale = sorted(
                (
                    search_id
                    for search_id, attempt in attempts.items()
                    if attempt["status"] == "STARTED" and int(attempt["last_activity_at"]) <= cutoff
                ),
                key=lambda search_id: (
                    int(attempts[search_id]["last_activity_at"]),
                    search_id,
                ),
            )
            recovery_head_row = self._conn.execute(
                "SELECT recovery_hash FROM search_attempt_recoveries ORDER BY recovery_seq DESC LIMIT 1"
            ).fetchone()
            recovery_head = "" if recovery_head_row is None else recovery_head_row["recovery_hash"]
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
                conn.execute(
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
            return tuple(stale)

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
        self._reject_write_ownership_conflict()
        self._require_runtime_mutation_schema()
        self._require_outbox_schema()
        trial_rows = [(value, first_seen_ts) for value in config_hashes]
        alpha_rows = [dict(row) for row in alphas]
        requires_triple = any(
            any(column in row for column in _TRIPLE_COLUMNS) for row in alpha_rows
        )
        messages = tuple(event_outbox)
        if any(not isinstance(message, EventOutboxMessage) for message in messages):
            raise TypeError("event_outbox must contain EventOutboxMessage values")

        with self._immediate_transaction() as (conn, lease):
            write_triple = (
                self._prepare_triple_columns_locked(conn, lease)
                if requires_triple
                else not (_TRIPLE_COLUMNS.keys() - self._alpha_state_columns())
            )
            conn.executemany(
                "INSERT OR IGNORE INTO trials(config_hash, first_seen_ts) VALUES (?, ?)",
                trial_rows,
            )
            for row in alpha_rows:
                if not write_triple:
                    conn.execute(
                        "INSERT INTO alpha_state(alpha_id, definition_json, state, "
                        "returns_json, sharpe, pvalue) VALUES (?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(alpha_id) DO UPDATE SET "
                        "definition_json=excluded.definition_json, "
                        "state=excluded.state, returns_json=excluded.returns_json, "
                        "sharpe=excluded.sharpe, pvalue=excluded.pvalue",
                        (
                            row["alpha_id"],
                            row["definition_json"],
                            row["state"],
                            row["returns_json"],
                            row.get("sharpe"),
                            row.get("pvalue"),
                        ),
                    )
                    continue
                conn.execute(
                    "INSERT INTO alpha_state(alpha_id, definition_json, state, "
                    "returns_json, sharpe, pvalue, research_state, deployment_state, "
                    "health_state, research_reason, deployment_reason, health_reason, "
                    "lossy_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(alpha_id) DO UPDATE SET "
                    "definition_json=excluded.definition_json, state=excluded.state, "
                    "returns_json=excluded.returns_json, sharpe=excluded.sharpe, "
                    "pvalue=excluded.pvalue, research_state=excluded.research_state, "
                    "deployment_state=excluded.deployment_state, "
                    "health_state=excluded.health_state, "
                    "research_reason=excluded.research_reason, "
                    "deployment_reason=excluded.deployment_reason, "
                    "health_reason=excluded.health_reason, "
                    "lossy_json=excluded.lossy_json",
                    (
                        row["alpha_id"],
                        row["definition_json"],
                        row["state"],
                        row["returns_json"],
                        row.get("sharpe"),
                        row.get("pvalue"),
                        row.get("research_state"),
                        row.get("deployment_state"),
                        row.get("health_state"),
                        row.get("research_reason"),
                        row.get("deployment_reason"),
                        row.get("health_reason"),
                        row.get("lossy_json"),
                    ),
                )
            self._append_event_outbox_locked(conn, lease, messages)

    def save_evaluation_catalog_snapshot(
        self,
        *,
        config_hashes: Iterable[str],
        alphas: Iterable[Mapping[str, object]],
        first_seen_ts: int,
    ) -> None:
        """Add new evaluation rows without mutating existing lifecycle state."""
        self._reject_write_ownership_conflict()
        self._require_runtime_mutation_schema()
        trial_rows = [(value, first_seen_ts) for value in config_hashes]
        alpha_rows = [dict(row) for row in alphas]
        requires_triple = any(
            any(column in row for column in _TRIPLE_COLUMNS) for row in alpha_rows
        )
        with self._immediate_transaction() as (conn, _lease):
            write_triple = (
                self._prepare_triple_columns_locked(conn, _lease)
                if requires_triple
                else not (_TRIPLE_COLUMNS.keys() - self._alpha_state_columns())
            )
            conn.executemany(
                "INSERT OR IGNORE INTO trials(config_hash, first_seen_ts) VALUES (?, ?)",
                trial_rows,
            )
            for row in alpha_rows:
                existing = conn.execute(
                    "SELECT definition_json FROM alpha_state WHERE alpha_id=?",
                    (row["alpha_id"],),
                ).fetchone()
                if existing is not None:
                    if existing["definition_json"] != row["definition_json"]:
                        raise ImmutableArtifactError(f"alpha_id {row['alpha_id']} already has a different definition")
                    continue
                if not write_triple:
                    conn.execute(
                        "INSERT INTO alpha_state(alpha_id,definition_json,state,"
                        "returns_json,sharpe,pvalue) VALUES (?,?,?,?,?,?)",
                        (
                            row["alpha_id"],
                            row["definition_json"],
                            row["state"],
                            row["returns_json"],
                            row.get("sharpe"),
                            row.get("pvalue"),
                        ),
                    )
                    continue
                conn.execute(
                    "INSERT INTO alpha_state(alpha_id,definition_json,state,returns_json,"
                    " sharpe,pvalue,research_state,deployment_state,health_state,"
                    " research_reason,deployment_reason,health_reason,lossy_json)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row["alpha_id"],
                        row["definition_json"],
                        row["state"],
                        row["returns_json"],
                        row.get("sharpe"),
                        row.get("pvalue"),
                        row.get("research_state"),
                        row.get("deployment_state"),
                        row.get("health_state"),
                        row.get("research_reason"),
                        row.get("deployment_reason"),
                        row.get("health_reason"),
                        row.get("lossy_json"),
                    ),
                )

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
        self._reject_write_ownership_conflict()
        if not isinstance(outbox_id, str) or not outbox_id:
            raise ValueError("outbox_id must be non-empty")
        if isinstance(dispatched_at, bool) or not isinstance(dispatched_at, int) or dispatched_at < 0:
            raise ValueError("dispatched_at must be a non-negative integer")
        if not isinstance(event_repro_hash, str) or not event_repro_hash:
            raise ValueError("event_repro_hash must be non-empty")
        self._require_runtime_mutation_schema()
        with self._immediate_transaction() as (conn, _lease):
            _, messages, dispatch_head = self._verified_outbox_state()
            message = messages.get(outbox_id)
            if message is None or message.event.reproducibility_hash != event_repro_hash:
                raise ExperimentStoreError("experiment outbox dispatch reference mismatch")
            existing = self._conn.execute(
                "SELECT dispatch_seq,outbox_id,dispatched_at,event_repro_hash,"
                " prev_record_hash,record_hash"
                " FROM experiment_event_dispatch WHERE outbox_id=?",
                (outbox_id,),
            ).fetchone()
            if existing is not None:
                if existing["dispatched_at"] == dispatched_at and existing["event_repro_hash"] == event_repro_hash:
                    return False
                raise ExperimentStoreError("experiment outbox dispatch acknowledgement conflict")
            digest = _dispatch_hash(
                outbox_id=outbox_id,
                dispatched_at=dispatched_at,
                event_repro_hash=event_repro_hash,
                prev_record_hash=dispatch_head,
            )
            conn.execute(
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
            return True

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

    def alpha_hypothesis_trial_hashes(self) -> tuple[str, ...]:
        """Return trial hashes from alpha-hypothesis searches only, excluding
        tuning grids and gate perturbations from the DSR denominator.

        Falls back to all_trial_hashes() if no search registration metadata exists
        (e.g., tests or legacy direct saves).
        """
        self._require_schema()

        # Non-hypothesis search kinds that should NOT count toward DSR trial inflation
        _NON_HYPOTHESIS_SEARCH_KINDS = frozenset({
            "ROBUST_CRYPTO_GRID",
            "PROMOTION_GATE_PERTURBATION",
        })

        # Check if search_candidates has any data
        candidate_count = self._conn.execute(
            "SELECT COUNT(*) AS n FROM search_candidates"
        ).fetchone()["n"]

        if candidate_count == 0:
            # No search metadata exists -> fall back to all trials
            # (legacy path for tests and direct registry.save())
            return tuple(sorted(r["config_hash"] for r in self._conn.execute(
                "SELECT config_hash FROM trials"
            )))

        # Filter trials by search_kind via search_candidates
        rows = self._conn.execute("""
            SELECT DISTINCT c.config_hash
            FROM search_candidates c
            INNER JOIN search_attempt_events e ON c.search_id = e.search_id
            WHERE e.search_kind NOT IN ({})
        """.format(",".join("?" * len(_NON_HYPOTHESIS_SEARCH_KINDS))),
            tuple(_NON_HYPOTHESIS_SEARCH_KINDS)
        ).fetchall()

        return tuple(sorted(r["config_hash"] for r in rows))

    def upsert_alpha(
        self,
        alpha_id: str,
        definition_json: str,
        state: str,
        returns_json: str,
        sharpe: Optional[float],
        pvalue: Optional[float],
        *,
        research_state: Optional[str] = None,
        deployment_state: Optional[str] = None,
        health_state: Optional[str] = None,
        research_reason: Optional[str] = None,
        deployment_reason: Optional[str] = None,
        health_reason: Optional[str] = None,
        lossy_json: Optional[str] = None,
    ) -> None:
        self._reject_write_ownership_conflict()
        self._require_runtime_mutation_schema()
        with self._immediate_transaction() as (conn, _lease):
            write_triple = self._prepare_triple_columns_locked(conn, _lease)
            if write_triple:
                conn.execute(
                    "INSERT INTO alpha_state(alpha_id,definition_json,state,"
                    "returns_json,sharpe,pvalue,research_state,deployment_state,"
                    "health_state,research_reason,deployment_reason,health_reason,"
                    "lossy_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(alpha_id) DO UPDATE SET "
                    "definition_json=excluded.definition_json,state=excluded.state,"
                    "returns_json=excluded.returns_json,sharpe=excluded.sharpe,"
                    "pvalue=excluded.pvalue,research_state=excluded.research_state,"
                    "deployment_state=excluded.deployment_state,"
                    "health_state=excluded.health_state,"
                    "research_reason=excluded.research_reason,"
                    "deployment_reason=excluded.deployment_reason,"
                    "health_reason=excluded.health_reason,"
                    "lossy_json=excluded.lossy_json",
                    (
                        alpha_id,
                        definition_json,
                        state,
                        returns_json,
                        sharpe,
                        pvalue,
                        research_state,
                        deployment_state,
                        health_state,
                        research_reason,
                        deployment_reason,
                        health_reason,
                        lossy_json,
                    ),
                )
                return
            conn.execute(
                "INSERT INTO alpha_state(alpha_id, definition_json, state, returns_json, sharpe, pvalue) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(alpha_id) DO UPDATE SET definition_json=excluded.definition_json, "
                "state=excluded.state, returns_json=excluded.returns_json, sharpe=excluded.sharpe, "
                "pvalue=excluded.pvalue",
                (alpha_id, definition_json, state, returns_json, sharpe, pvalue),
            )

    def all_alphas(self) -> list[dict]:
        self._require_schema()
        present = self._alpha_state_columns()
        select_triple = "".join(
            f", {column}" if column in present else f", NULL AS {column}"
            for column in _TRIPLE_COLUMNS
        )
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT alpha_id, definition_json, state, returns_json, sharpe, pvalue "
                f"{select_triple} FROM alpha_state ORDER BY alpha_id"
            )
        ]

    def evaluation_context_snapshot(self) -> tuple[list[str], list[dict]]:
        """Freeze trial identities and historical alpha rows in one view."""
        self._require_schema()
        try:
            self._conn.execute("BEGIN")
            trials = self.all_trial_hashes()
            alphas = self.all_alphas()
            self._conn.commit()
            return trials, alphas
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def save_bandit(self, snapshot_json: str) -> None:
        self._reject_write_ownership_conflict()
        self._require_runtime_mutation_schema()
        with self._immediate_transaction() as (conn, _lease):
            conn.execute(
                "CREATE TABLE IF NOT EXISTS bandit_state "
                "(id INTEGER PRIMARY KEY CHECK (id = 1), snapshot_json TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO bandit_state(id, snapshot_json) VALUES (1, ?)",
                (snapshot_json,),
            )

    def load_bandit(self):
        self._require_schema()
        present = self._conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='bandit_state'"
        ).fetchone()
        if present is None:
            return None
        row = self._conn.execute("SELECT snapshot_json FROM bandit_state WHERE id = 1").fetchone()
        return row["snapshot_json"] if row is not None else None

    def _evidence_profile(self):
        from services.validation.research_evidence_artifact import require
        require(self._expected_authority_instance_id is None, 'FAMILY_AUTHORITY')
        require(tuple(row[1] for row in self._conn.execute('PRAGMA database_list'))
                in (('main',), ('main', 'temp')), 'SCHEMA_NAMESPACE')
        require(not self._conn.execute('SELECT 1 FROM temp.sqlite_schema').fetchall(), 'SCHEMA_TEMP')
        actual = _evidence_catalog(self._conn)
        matches = [name for name, expected in _evidence_catalogs().items() if actual == expected]
        require(len(matches) == 1, 'SCHEMA_CATALOG')
        return matches[0]

    def _evidence_has_provenance(self):
        return self._evidence_profile() != 'EXACT_V2'

    def _evidence_population_locked(self):
        from services.validation import research_evidence_artifact as artifact
        population = dict(schema=artifact.POPULATION_SCHEMA,
            trials=[dict(row) for row in self._conn.execute(
                'SELECT config_hash,first_seen_ts FROM trials ORDER BY config_hash')],
            provenance=[], oos=[], search_candidates=[])
        attempts = verify_search_state(self._conn).attempts
        for row in self._conn.execute('SELECT * FROM search_candidates ORDER BY candidate_seq'):
            attempt = attempts[row['search_id']]
            kind, source, dataset = attempt['metadata']
            identity = dict(search_id=row['search_id'], search_kind=kind, source=source, dataset_version=dataset)
            identity['identity_hash'] = _search_identity_hash(**identity)
            population['search_candidates'].append(dict(row, search_identity=identity,
                                                        search_started_at=attempt['started_at']))
        if self._evidence_has_provenance():
            population['provenance'] = [dict(row) for row in self._conn.execute(
                'SELECT * FROM trial_provenance ORDER BY provenance_seq')]
            population['oos'] = [dict(row) for row in self._conn.execute(
                'SELECT * FROM trial_oos_access ORDER BY access_seq')]
        population['sha256'] = artifact.digest(population)
        return artifact.validate_population(population)

    def _verify_research_evidence_store(self, *, allow_prior=False):
        """Full local catalog/old domain/chains/new receipt verification, read only."""
        from services.research.experiment_registry import family_schema_v2_activation as original
        from services.research.experiment_registry.family_schema_v2_semantics import verify_v2_semantics
        from services.validation import research_evidence_artifact as artifact
        from services.validation import project_research_evidence_artifact as project_artifact
        version = self._schema_version()
        artifact.require(version in ((2, 4, 5) if allow_prior else (5,)), 'VERSION_5_REQUIRED')
        profile = self._evidence_profile()
        artifact.require(version != 2 or profile == 'EXACT_V2', 'SCHEMA_VERSION')
        artifact.require(version != 4 or profile != 'EXACT_V2', 'SCHEMA_VERSION')
        artifact.require(tuple(tuple(r) for r in self._conn.execute('PRAGMA integrity_check')) == (('ok',),)
                         and not self._conn.execute('PRAGMA foreign_key_check').fetchall(), 'SCHEMA_INTEGRITY')
        catalog = EXACT_V2_INHERITED_SCHEMA_CATALOG
        tables = {table.table_name: original._read_typed_table_rows(self._conn, table, catalog.origin_projection)
                  for table in catalog.tables}
        original._validate_trials(tables['trials'])
        original._validate_alpha_state(tables['alpha_state'], frozenset(r[0] for r in tables['trials']))
        original._validate_bandit(tables['bandit_state'])
        original._validate_search_domain(tables)
        verify_v2_semantics(self._conn)
        population = self._evidence_population_locked()
        if profile != 'EXACT_V2':
            self._verify_evidence_provenance(population)
        if profile == 'EXACT_V4_C2':
            from services.lifecycle import DeploymentState, HealthReason, HealthState, HealthVerdict, ResearchState
            for row in self._conn.execute('SELECT * FROM alpha_state'):
                if row['research_state'] is not None:
                    ResearchState(row['research_state']); DeploymentState(row['deployment_state'])
                    HealthVerdict(HealthState(row['health_state']), HealthReason(row['health_reason'] or 'NONE'))
                for key in ('research_reason', 'deployment_reason'):
                    artifact.require(row[key] is None or type(row[key]) is str, 'SCHEMA_C2_REASON')
                if row['lossy_json'] is not None:
                    lossy = json.loads(row['lossy_json'])
                    artifact.require(type(lossy) is list and all(type(x) is str for x in lossy), 'SCHEMA_C2_LOSSY')
        for row in tables['evaluations']:
            payload = json.loads(row[6])
            if type(payload) is dict and payload.get('schema') == artifact.SCHEMA:
                artifact.require(version == 5, 'VERSION_5_REQUIRED')
                value = artifact.validate_row(dict(zip(('run_id', 'alpha_id', 'dataset_version',
                    'recorded_ts', 'verdict', 'content_hash', 'evaluation_json'), row, strict=True)))
                artifact.verify_population_prefix(value['population'], population)
                binding = self._conn.execute('SELECT search_id,config_hash FROM search_evaluations WHERE run_id=?', (row[0],)).fetchall()
                artifact.require([tuple(r) for r in binding] == [(value['search_identity']['search_id'], value['config_hash'])], 'SEARCH_BINDING')
                artifact.require(self.search_identity(value['search_identity']['search_id']) == value['search_identity'], 'SEARCH_IDENTITY')
                audit = self._conn.execute('SELECT COUNT(*) FROM evaluation_audit_outbox WHERE run_id=?', (row[0],)).fetchone()[0]
                artifact.require(audit == 1, 'AUDIT_MISSING')
            elif type(payload) is dict and payload.get('schema') in project_artifact.SCHEMAS:
                artifact.require(version == 5, 'VERSION_5_REQUIRED')
                self._validate_project_review_row_locked(dict(zip(
                    ('run_id', 'alpha_id', 'dataset_version', 'recorded_ts',
                     'verdict', 'content_hash', 'evaluation_json'), row, strict=True)),
                    population=population, history=self._project_registry_history_locked())
            else:
                original._validate_evaluations((row,))
        # Check every original AUTOINCREMENT high water, including v4 additions.
        owned = set(catalog.owned_sqlite_sequence_tables)
        if profile != 'EXACT_V2':
            owned |= {'trial_provenance', 'trial_oos_access'}
        actual_sequences = dict(self._conn.execute('SELECT name,seq FROM sqlite_sequence'))
        artifact.require(set(actual_sequences) <= owned, 'SCHEMA_SEQUENCE')
        for table in owned:
            key = next(r['name'] for r in self._conn.execute(f'PRAGMA table_info({table})') if r['pk'] == 1)
            maximum = self._conn.execute(f'SELECT MAX({key}) FROM {table}').fetchone()[0]
            artifact.require(actual_sequences.get(table) == maximum, 'SCHEMA_SEQUENCE')
        return population

    def _verify_evidence_provenance(self, population):
        from services.research.trial_provenance import ActorType, TrialProvenance, TrialStatus
        from services.validation.research_evidence_artifact import canonical, require
        trials = {r['config_hash'] for r in population['trials']}
        previous = ''
        for index, row in enumerate(population['provenance'], 1):
            payload = json.loads(row['record_json'])
            record = TrialProvenance(**dict(payload, actor_type=ActorType(payload['actor_type']),
                                           status=TrialStatus(payload['status'])))
            require(canonical(record.canonical_payload()) == canonical(payload), 'POPULATION_PROVENANCE')
            require(row['provenance_seq'] == index and row['config_hash'] in trials
                and row['prev_entry_hash'] == previous and row['entry_hash'] == record.entry_hash(previous), 'POPULATION_PROVENANCE_CHAIN')
            for key in ('trial_id', 'research_session_id', 'config_hash', 'actor_id', 'actor_type', 'status',
                        'started_at', 'completed_at', 'parent_trial_id', 'hypothesis_id', 'expression_hash',
                        'parameter_hash', 'code_hash'):
                require(canonical(row[key]) == canonical(payload[key]), 'POPULATION_PROVENANCE_COLUMNS')
            require(row['oos_peek_count'] == record.oos_peek_count, 'POPULATION_PROVENANCE_COLUMNS')
            previous = row['entry_hash']
        ids = {r['trial_id'] for r in population['provenance']}
        previous = ''
        for index, row in enumerate(population['oos'], 1):
            require(row['access_seq'] == index and row['trial_id'] in ids
                and type(row['viewed_at']) is int and row['viewed_at'] >= 0
                and row['prev_entry_hash'] == previous, 'POPULATION_OOS')
            expected = _trial_oos_access_hash(**{k:row[k] for k in ('trial_id', 'method', 'boundary', 'viewed_at', 'prev_entry_hash')})
            require(row['entry_hash'] == expected, 'POPULATION_OOS_HASH')
            previous = expected

    def research_evidence_population(self):
        with self._search_read_snapshot():
            return self._verify_research_evidence_store(allow_prior=True)

    def upgrade_research_evidence_schema(self, *, expected_version, expected_population_sha256):
        from services.validation.research_evidence_artifact import require, digest
        require(type(expected_version) is int and expected_version in (2, 4), 'VERSION_UPGRADE')
        require(self._schema_version() == expected_version, 'VERSION_CAS')
        self._evidence_profile()
        with self._immediate_transaction() as (conn, _lease):
            require(self._schema_version() == expected_version, 'VERSION_CAS')
            population = self._verify_research_evidence_store(allow_prior=True)
            require(population['sha256'] == expected_population_sha256, 'POPULATION_CAS')
            profile = self._evidence_profile()
            catalog_sha256 = digest([list(row) for row in _evidence_catalog(conn)])
            self._migration_checkpoint('research_evidence_before_version')
            conn.execute('PRAGMA user_version=5')
            self._migration_checkpoint('research_evidence_after_version')
            require(self._verify_research_evidence_store() == population, 'POPULATION_UPGRADE_CHANGED')
        return dict(schema='QUANTOS_RESEARCH_EVIDENCE_UPGRADE_V1', previous_version=expected_version,
            version=5, profile=profile, catalog_sha256=catalog_sha256, population_sha256=population['sha256'],
            promotion_approved=False, submission_enabled=False)

    def _validate_research_write(self, write):
        from services.validation import research_evidence_artifact as artifact
        artifact.require(self._schema_version() == 5 and type(write) is ResearchEvidenceArtifactWrite, 'VERSION_5_REQUIRED')
        artifact.validate_row(vars(write))
        expected = EvaluationAuditIntent(event_id=f'exp-{write.run_id}', actor='experiment-registry',
            action='EXPERIMENT_RECORDED', timestamp=write.recorded_ts, target=write.alpha_id,
            metadata=dict(run_id=write.run_id, content_hash=write.content_hash, verdict=write.verdict))
        artifact.require(write.audit_intent == expected, 'AUDIT_INTENT')

    def retain_research_evidence(self, *, prepare, recheck, search_id, config_hash):
        from services.validation import research_evidence_artifact as artifact
        artifact.require(self._schema_version() == 5, 'VERSION_5_REQUIRED')
        with self._immediate_transaction() as (conn, lease):
            population = self._evidence_population_locked()
            identity = self.search_identity(search_id)
            write = prepare(population, identity)
            self._validate_research_write(write)
            value = artifact.decode(write.evaluation_json.encode())
            artifact.require(value['config_hash'] == config_hash and value['search_identity'] == identity, 'SEARCH_BINDING')
            artifact.verify_population_prefix(value['population'], population, exact=True)
            existing = conn.execute('SELECT * FROM evaluations WHERE run_id=?', (write.run_id,)).fetchone()
            if existing is not None:
                # Full cold preflight verified all three originals. Revalidate
                # current evidence below without rewriting old time or links.
                old = artifact.validate_row(dict(existing))
                # A retry may advance the check clock, not the receipt's facts.
                current = dict(value, assessment=dict(value['assessment'], checked_at_ms=old['assessment']['checked_at_ms']))
                if artifact.canonical(current) != artifact.canonical(old):
                    raise ImmutableArtifactError('research receipt ID conflicts with original facts')
                fields = {k:existing[k] for k in ('run_id','alpha_id','dataset_version','recorded_ts','verdict','content_hash','evaluation_json')}
                write = ResearchEvidenceArtifactWrite(**fields, audit_intent=replace(write.audit_intent,
                    timestamp=fields['recorded_ts'], metadata=dict(run_id=fields['run_id'],content_hash=fields['content_hash'],verdict=fields['verdict'])))
            else:
                self._insert_evaluation_locked(conn, lease, write, search_id=search_id, config_hash=config_hash)
            # After every SQL/checkpoint, before COMMIT: actual reader and clock,
            # never the previously supplied dictionary as a stand-in for IO.
            artifact.verify_population_prefix(population, self._evidence_population_locked(), exact=True)
            recheck(artifact.decode(write.evaluation_json.encode()))
            return dict(conn.execute('SELECT * FROM evaluations WHERE run_id=?', (write.run_id,)).fetchone())

    def get_research_evidence(self, run_id):
        from services.validation import research_evidence_artifact as artifact
        with self._search_read_snapshot():
            self._verify_research_evidence_store()
            row = self._conn.execute('SELECT * FROM evaluations WHERE run_id=?', (run_id,)).fetchone()
            if row is None:
                return None
            artifact.validate_row(dict(row))
            return dict(row)

    def _project_registry_history_locked(self):
        """Original complete activity and evaluation metadata, never row bodies."""
        return dict(events=[dict(row) for row in self._search_event_rows()],
            heartbeats=[dict(row) for row in self._search_heartbeat_rows()],
            recoveries=[dict(row) for row in self._search_recovery_rows()],
            evaluations=[dict(row) for row in self._conn.execute(
                'SELECT run_id,dataset_version,recorded_ts,content_hash FROM evaluations ORDER BY run_id')],
            evaluation_links=[dict(row) for row in self._conn.execute(
                'SELECT * FROM search_evaluations ORDER BY link_seq')])

    def _project_database_identity(self):
        info = os.stat(self._database_path, follow_symlinks=False)
        return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode, info.st_nlink)

    def _project_registry_view_locked(self):
        return dict(population=self._evidence_population_locked(),
                    history=self._project_registry_history_locked())

    @staticmethod
    def _project_review_audit(row):
        return EvaluationAuditIntent(event_id=f"exp-{row['run_id']}", actor='experiment-registry',
            action='EXPERIMENT_RECORDED', timestamp=row['recorded_ts'], target=row['alpha_id'],
            metadata={key:row[key] for key in ('run_id', 'content_hash', 'verdict')})

    def _validate_project_review_row_locked(self, row, *, population, history):
        from services.validation import project_research_evidence_artifact as artifact
        decoded = artifact.decode(row['evaluation_json'].encode())
        with self.project_research_result_materials(decoded['snapshot']['project_cut']) as reports:
            value = artifact.validate_row(row, reports=reports)
        artifact.verify_registry_prefix(value, population=population, history=history)
        self._validate_project_review_custody_locked(row, value)
        return value

    def _validate_project_review_custody_locked(self, row, value):
        from services.validation import project_research_evidence_artifact as artifact
        artifact.require(artifact.canonical(self.search_identity(value['search_identity']['search_id']))
            == artifact.canonical(value['search_identity']), 'SEARCH_IDENTITY')
        artifact.require(not self._conn.execute(
            'SELECT 1 FROM search_evaluations WHERE run_id=?', (row['run_id'],)).fetchall(),
            'PROJECT_REVIEW_ACTIVITY_LINK')
        audits = self._conn.execute(
            'SELECT * FROM evaluation_audit_outbox WHERE run_id=?', (row['run_id'],)).fetchall()
        artifact.require(len(audits) == 1, 'PROJECT_REVIEW_AUDIT')
        artifact.require(self._evaluation_audit_intent_from_row(audits[0]) == self._project_review_audit(row)
            and audits[0]['evaluation_content_hash'] == row['content_hash'], 'PROJECT_REVIEW_AUDIT')
        return value

    def _frozen_validation_rows_locked(self, runs):
        from services.validation import project_research_evidence_artifact as artifact
        refs={p['forward_design']['risk_policy']['frozen_validation_policy']['reference']['review_id']
            for run in runs.values() for p in (run['plan'],)
            if 'frozen_validation_policy' in p.get('forward_design',{}).get('risk_policy',{})}
        rows={}
        for key in sorted(refs):
            row=self._conn.execute('SELECT * FROM evaluations WHERE run_id=?',(key,)).fetchone()
            artifact.require(row is not None, 'FROZEN_VALIDATION_ROW_REQUIRED')
            rows[key]=dict(row)
            value=artifact.decode(row['evaluation_json'].encode())
            self._validate_project_review_custody_locked(rows[key],value)
        artifact.canonical(rows)
        return rows

    @contextmanager
    def project_research_result_materials(self, cut):
        """All original reports for a cut, using only bootstrap configuration."""
        from quantos.contracts import registered_research_run as native
        from services.validation import project_research_evidence_artifact as artifact
        complete = any(run['plan']['schema'] in native.CHUNKED_GOLD_SCHEMAS
            and run['result_material']['status'] == 'COMPLETE' for run in cut['runs'].values())
        if not complete:
            yield None
            return
        artifact.require(self._project_result_reader is not None, 'PROJECT_CHUNKED_MATERIAL_REQUIRED')
        with self._project_result_reader.reports(cut, review_rows=self._frozen_validation_rows_locked(cut["runs"])) as reports:
            yield reports

    def project_research_registry_view(self):
        """Read current native metadata; this does not grant current admission."""
        with self._search_read_snapshot():
            self._verify_research_evidence_store(allow_prior=True)
            return self._project_registry_view_locked()

    def _read_project_review_rows_locked(self, ids):
        from services.validation import project_research_evidence_artifact as artifact
        artifact.require(type(ids) in (list, tuple) and len(ids) <= 100_000
            and all(type(key) is str and len(key) == 64 and all(c in '0123456789abcdef' for c in key)
                    for key in ids) and len(set(ids)) == len(ids), 'PROJECT_REVIEW_IDS')
        view = self._project_registry_view_locked()
        rows = {}
        for key in ids:
            row = self._conn.execute('SELECT * FROM evaluations WHERE run_id=?', (key,)).fetchone()
            artifact.require(row is not None, 'PROJECT_REVIEW_MISSING')
            rows[key] = dict(row)
            self._validate_project_review_row_locked(rows[key], **view)
        artifact.canonical(rows)
        return rows

    def read_project_research_review_rows(self, ids):
        """Exact V2 rows with native audit/no-link checks, in one read snapshot."""
        with self._search_read_snapshot():
            self._verify_research_evidence_store(allow_prior=True)
            return self._read_project_review_rows_locked(ids)

    def _validate_project_research_write(self, write):
        from services.validation import project_research_evidence_artifact as artifact
        artifact.require(self._schema_version() == 5
            and type(write) is ProjectResearchEvidenceArtifactWrite, 'VERSION_5_REQUIRED')
        value = artifact.decode(write.evaluation_json.encode())
        with self.project_research_result_materials(value['snapshot']['project_cut']) as reports:
            artifact.validate_row(vars(write), reports=reports)
        artifact.require(write.audit_intent == self._project_review_audit(vars(write)), 'AUDIT_INTENT')

    def retain_project_research_evidence(self, *, prepare, recheck):
        """Persist a separately verified review after computation, without activity.

        Callbacks perform fixed-original reads and end-clock validation. The
        original SQL lease owns the evaluation and its audit together; callbacks
        receive its exact metadata and a bounded reader on that same connection.
        """
        from services.validation import project_research_evidence_artifact as artifact
        artifact.require(self._schema_version() == 5, 'VERSION_5_REQUIRED')
        with self._immediate_transaction() as (conn, lease):
            before = self._project_registry_view_locked()
            permitted = [artifact.canonical(before)]
            audit_permitted = [artifact.canonical([dict(row) for row in self._evaluation_audit_rows()])]

            def read_rows(ids):
                self._require_write_lock(conn, lease)
                return self._read_project_review_rows_locked(ids)

            def registry_view_for(database_path):
                self._require_write_lock(conn, lease)
                artifact.require(self._database_path and isinstance(database_path, (str, Path))
                    and Path(database_path).is_absolute()
                    and str(Path(database_path)) == self._database_path, 'PROJECT_REGISTRY_PATH')
                artifact.require(self._project_database_identity() == self._database_identity,
                                 'PROJECT_REGISTRY_REPLACED')
                self._verify_research_evidence_store()
                artifact.require(artifact.canonical(self._project_registry_view_locked()) in permitted
                    and artifact.canonical([dict(row) for row in self._evaluation_audit_rows()]) in audit_permitted,
                    'PROJECT_REGISTRY_LEASE_CHANGED')
                return artifact.decode(artifact.canonical(before))

            # This short-lived callable is created only inside the real writer
            # lease. Public read_project_research_review_rows has no capability.
            def frozen_rows_for(runs):
                self._require_write_lock(conn, lease)
                return self._frozen_validation_rows_locked(runs)
            registry_view_for.frozen_rows_for=frozen_rows_for
            read_rows.registry_view_for = registry_view_for
            write = prepare(artifact.decode(artifact.canonical(before)), read_rows)
            self._validate_project_research_write(write)
            value = artifact.decode(write.evaluation_json.encode())
            artifact.verify_registry_prefix(value, **before)
            existing = conn.execute('SELECT * FROM evaluations WHERE run_id=?', (write.run_id,)).fetchone()
            if existing is not None:
                old = self._validate_project_review_row_locked(dict(existing), **before)
                with self.project_research_result_materials(old['snapshot']['project_cut']) as reports:
                    old_identity = artifact.review_identity(old, reports=reports)
                with self.project_research_result_materials(value['snapshot']['project_cut']) as reports:
                    if old_identity != artifact.review_identity(value, reports=reports):
                        raise ImmutableArtifactError('project review ID conflicts with original facts')
                # The exact original time, bytes and audit remain immutable.
                row = dict(existing)
                value = old
            else:
                self._insert_evaluation_locked(conn, lease, write)
                row = dict(conn.execute('SELECT * FROM evaluations WHERE run_id=?', (write.run_id,)).fetchone())
            self._verify_research_evidence_store()
            after = self._project_registry_view_locked()
            expected = artifact.decode(artifact.canonical(before))
            if existing is None:
                expected['history']['evaluations'].append({key:row[key] for key in artifact.METADATA_FIELDS})
                expected['history']['evaluations'].sort(key=lambda item:item['run_id'])
            artifact.require(artifact.canonical(after) == artifact.canonical(expected), 'PROJECT_REVIEW_TRANSACTION_DELTA')
            permitted.append(artifact.canonical(after))
            audit_permitted.append(artifact.canonical([dict(item) for item in self._evaluation_audit_rows()]))
            # This is deliberately last: a full cold audit must not run after
            # the caller's final original/source/key/statement clock checks.
            recheck(value, before, after, read_rows)
            return row

    def get_project_research_evidence(self, run_id):
        """Historical integrity only; freshness is a separate current preflight."""
        with self._search_read_snapshot():
            self._verify_research_evidence_store()
            row = self._conn.execute('SELECT * FROM evaluations WHERE run_id=?', (run_id,)).fetchone()
            if row is None:
                return None
            self._validate_project_review_row_locked(dict(row), **self._project_registry_view_locked())
            return dict(row)

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
        self._reject_write_ownership_conflict()
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
            raise ValueError("search_id and config_hash must be supplied together for exact binding")
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

        write = EvaluationArtifactWrite(
            run_id=run_id,
            alpha_id=alpha_id,
            dataset_version=dataset_version,
            recorded_ts=recorded_ts,
            verdict=verdict,
            content_hash=content_hash,
            evaluation_json=evaluation_json,
            audit_intent=audit_intent,
        )
        validate_evaluation_artifact_write(write)
        self._ensure_search_schema()
        with self._immediate_transaction() as (conn, lease):
            return self._insert_evaluation_locked(
                conn,
                lease,
                write,
                search_id=search_id,
                config_hash=config_hash,
            )

    def _insert_evaluation_locked(
        self,
        conn: sqlite3.Connection,
        lease: object,
        write: EvaluationArtifactWrite,
        search_id: str | None = None,
        config_hash: str | None = None,
    ) -> bool:
        self._require_write_lock(conn, lease)
        if type(write) is ProjectResearchEvidenceArtifactWrite:
            if search_id is not None or config_hash is not None:
                raise ValueError('project review cannot be a search activity')
            self._validate_project_research_write(write)
        elif type(write) is ResearchEvidenceArtifactWrite:
            self._validate_research_write(write)
        else:
            validate_evaluation_artifact_write(write)
        _, _, attempts = self._verified_search_state()
        audit_head, audit_intents, _ = self._verified_evaluation_audit_state()
        existing = conn.execute("SELECT * FROM evaluations WHERE run_id=?", (write.run_id,)).fetchone()
        inserted = existing is None
        if existing is not None and existing["content_hash"] != write.content_hash:
            raise ImmutableArtifactError(f"run_id {write.run_id} already recorded with a different content_hash")
        if inserted:
            conn.execute(
                "INSERT INTO evaluations(run_id,alpha_id,dataset_version,recorded_ts,"
                " verdict,content_hash,evaluation_json) VALUES (?,?,?,?,?,?,?)",
                (
                    write.run_id,
                    write.alpha_id,
                    write.dataset_version,
                    write.recorded_ts,
                    write.verdict,
                    write.content_hash,
                    write.evaluation_json,
                ),
            )
            self._evaluation_write_checkpoint("after_evaluation")

        # The immutable artifact timestamp is selected while holding the same
        # write lock that decides insertion. Concurrent retries may propose
        # different timestamps, but the audit record always inherits the
        # timestamp of the transaction that first committed the artifact.
        effective_audit_intent = (
            None
            if write.audit_intent is None
            else replace(
                write.audit_intent,
                timestamp=(write.recorded_ts if inserted else int(existing["recorded_ts"])),
            )
        )

        if search_id is not None:
            attempt = attempts.get(search_id)
            candidate = conn.execute(
                "SELECT config_hash FROM search_candidates WHERE search_id=? AND config_hash=?",
                (search_id, config_hash),
            ).fetchone()
            if (
                attempt is None
                or attempt["status"] != "STARTED"
                or candidate is None
                or attempt["metadata"][2] != write.dataset_version
                or write.recorded_ts < attempt["started_at"]
                or write.recorded_ts < attempt["last_activity_at"]
            ):
                raise ExperimentStoreError(f"evaluation cannot bind to search attempt {search_id}")
            existing_link = conn.execute(
                "SELECT search_id,config_hash,evaluation_content_hash"
                " FROM search_evaluations"
                " WHERE search_id=? AND run_id=?",
                (search_id, write.run_id),
            ).fetchone()
            if existing_link is not None:
                if (
                    existing_link["config_hash"] != config_hash
                    or existing_link["evaluation_content_hash"] != write.content_hash
                ):
                    raise ImmutableArtifactError(
                        f"evaluation run {write.run_id} already has a different search binding"
                    )
            else:
                link_head_row = conn.execute(
                    "SELECT link_hash FROM search_evaluations ORDER BY link_seq DESC LIMIT 1"
                ).fetchone()
                link_head = "" if link_head_row is None else link_head_row["link_hash"]
                link_hash = _search_evaluation_hash(
                    search_id=search_id,
                    config_hash=config_hash,
                    run_id=write.run_id,
                    recorded_at=write.recorded_ts,
                    evaluation_content_hash=write.content_hash,
                    prev_link_hash=link_head,
                )
                conn.execute(
                    "INSERT INTO search_evaluations"
                    " (search_id,config_hash,run_id,recorded_at,"
                    " evaluation_content_hash,prev_link_hash,link_hash)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (
                        search_id,
                        config_hash,
                        write.run_id,
                        write.recorded_ts,
                        write.content_hash,
                        link_head,
                        link_hash,
                    ),
                )
                self._evaluation_write_checkpoint("after_link")

        if effective_audit_intent is not None:
            existing_audit = audit_intents.get(effective_audit_intent.event_id)
            if existing_audit is not None:
                if existing_audit != (write.run_id, effective_audit_intent):
                    raise ImmutableArtifactError(f"evaluation audit event conflict: {effective_audit_intent.event_id}")
            else:
                audit_hash = _evaluation_audit_hash(
                    run_id=write.run_id,
                    intent=effective_audit_intent,
                    evaluation_content_hash=write.content_hash,
                    prev_record_hash=audit_head,
                )
                conn.execute(
                    "INSERT INTO evaluation_audit_outbox"
                    " (run_id,event_id,actor,action,timestamp,target,metadata_json,"
                    " evaluation_content_hash,prev_record_hash,record_hash)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        write.run_id,
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
                        write.content_hash,
                        audit_head,
                        audit_hash,
                    ),
                )
                self._evaluation_write_checkpoint("after_audit")
        self._evaluation_write_checkpoint("before_commit")
        return inserted

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

    def latest_evaluations_by_alpha(self) -> dict[str, dict]:
        """Return each alpha's deterministic evaluation head in one read.

        Console projections need one latest row per alpha.  Performing one
        query per alpha blocks the single API process for large registries, so
        keep the same ``recorded_ts, run_id`` ordering contract while taking a
        single SQLite snapshot.
        """

        self._require_schema()
        rows = self._conn.execute(
            "WITH ranked AS ("
            " SELECT run_id, ROW_NUMBER() OVER ("
            "  PARTITION BY alpha_id ORDER BY recorded_ts DESC, run_id DESC"
            " ) AS row_rank FROM evaluations"
            ") "
            "SELECT e.* FROM evaluations e "
            "JOIN ranked r ON r.run_id=e.run_id "
            "WHERE r.row_rank=1 ORDER BY e.alpha_id"
        )
        return {str(row["alpha_id"]): dict(row) for row in rows}

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
        self._reject_write_ownership_conflict()
        event_id = self._require_text(event_id, "event_id")
        dispatched_at = self._require_time(dispatched_at, "dispatched_at")
        audit_entry_hash = self._require_text(audit_entry_hash, "audit_entry_hash")
        self._ensure_search_schema()
        with self._immediate_transaction() as (conn, _lease):
            _, intents, dispatch_head = self._verified_evaluation_audit_state()
            if event_id not in intents:
                raise ExperimentStoreError(f"unknown evaluation audit event: {event_id}")
            existing = self._conn.execute(
                "SELECT dispatched_at,audit_entry_hash FROM evaluation_audit_dispatch WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                if existing["dispatched_at"] == dispatched_at and existing["audit_entry_hash"] == audit_entry_hash:
                    return False
                raise ExperimentStoreError("evaluation audit dispatch acknowledgement conflict")
            digest = _evaluation_audit_dispatch_hash(
                event_id=event_id,
                dispatched_at=dispatched_at,
                audit_entry_hash=audit_entry_hash,
                prev_record_hash=dispatch_head,
            )
            conn.execute(
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
            return True

    def evaluation_audit_chain_valid(self) -> bool:
        try:
            self._verified_evaluation_audit_state()
            return True
        except (ExperimentStoreError, sqlite3.DatabaseError):
            return False

    def close(self) -> None:
        self._conn.close()
