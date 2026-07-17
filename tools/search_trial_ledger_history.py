"""Capture one immutable snapshot of the production alpha trial ledger.

Each invocation appends at most one real session record. The tool never creates
or mutates the SQLite ledger; ``--check`` is fully read-only.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
from typing import Any, Callable, Iterator, Mapping

from quantos.contracts.hashing import stable_hash


DEFAULT_LEDGER_PATH = "data/cache/experiment_registry.db"
DEFAULT_HISTORY_PATH = "evidence/alpha_trial_ledger_history.json"
DEFAULT_SNAPSHOT_PATH = "evidence/alpha_trial_ledger_snapshot.db"
DEFAULT_SEAL_PATH = "evidence/alpha_trial_ledger_snapshot_seal.json"
HISTORY_MODE = "ALPHA_TRIAL_LEDGER_APPEND_ONLY_HISTORY"
HISTORY_SCHEMA_VERSION = 2
LEDGER_CONTRACT_VERSION = 2
PORTABLE_SEAL_MODE = "ALPHA_TRIAL_LEDGER_PORTABLE_SEAL"
PORTABLE_SEAL_SCHEMA_VERSION = 1

_PORTABLE_APPLICATION_TABLES = {
    "trials",
    "evaluations",
    "search_attempt_events",
    "search_candidates",
    "search_evaluations",
}
_PORTABLE_ALLOWED_TABLES = _PORTABLE_APPLICATION_TABLES | {"sqlite_sequence"}
_PORTABLE_APPEND_ONLY_TRIGGER_GUARDS = {
    "trials_no_update": ("trials", "UPDATE"),
    "trials_no_delete": ("trials", "DELETE"),
    "evaluations_no_update": ("evaluations", "UPDATE"),
    "evaluations_no_delete": ("evaluations", "DELETE"),
    "search_attempt_events_no_update": ("search_attempt_events", "UPDATE"),
    "search_attempt_events_no_delete": ("search_attempt_events", "DELETE"),
    "search_candidates_no_update": ("search_candidates", "UPDATE"),
    "search_candidates_no_delete": ("search_candidates", "DELETE"),
    "search_evaluations_no_update": ("search_evaluations", "UPDATE"),
    "search_evaluations_no_delete": ("search_evaluations", "DELETE"),
}
_SEAL_FIELDS = {
    "schema_version",
    "mode",
    "ledger_path",
    "history_path",
    "snapshot_path",
    "history_sha256",
    "snapshot_sha256",
    "history_head_hash",
    "record_count",
    "trial_count",
    "evaluation_count",
    "sealed_at_ms",
}

_PORTABLE_SCHEMA = """
CREATE TABLE trials (
    config_hash TEXT PRIMARY KEY,
    first_seen_ts INTEGER NOT NULL
);
CREATE TABLE evaluations (
    run_id TEXT PRIMARY KEY,
    alpha_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    recorded_ts INTEGER NOT NULL,
    verdict TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    evaluation_json TEXT NOT NULL
);
CREATE TABLE search_attempt_events (
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
CREATE INDEX search_attempt_events_by_search
ON search_attempt_events(search_id, event_seq);
CREATE TABLE search_candidates (
    candidate_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status = 'REGISTERED'),
    registered_at INTEGER NOT NULL CHECK(registered_at >= 0),
    prev_candidate_hash TEXT NOT NULL,
    candidate_hash TEXT UNIQUE NOT NULL,
    UNIQUE(search_id, config_hash)
);
CREATE INDEX search_candidates_by_search
ON search_candidates(search_id, candidate_seq);
CREATE TABLE search_evaluations (
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
CREATE INDEX search_evaluations_by_search
ON search_evaluations(search_id, link_seq);
CREATE TRIGGER trials_no_update BEFORE UPDATE ON trials
BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;
CREATE TRIGGER trials_no_delete BEFORE DELETE ON trials
BEGIN SELECT RAISE(ABORT, 'trials is append-only'); END;
CREATE TRIGGER evaluations_no_update BEFORE UPDATE ON evaluations
BEGIN SELECT RAISE(ABORT, 'evaluations is append-only'); END;
CREATE TRIGGER evaluations_no_delete BEFORE DELETE ON evaluations
BEGIN SELECT RAISE(ABORT, 'evaluations is append-only'); END;
CREATE TRIGGER search_attempt_events_no_update BEFORE UPDATE ON search_attempt_events
BEGIN SELECT RAISE(ABORT, 'search_attempt_events is append-only'); END;
CREATE TRIGGER search_attempt_events_no_delete BEFORE DELETE ON search_attempt_events
BEGIN SELECT RAISE(ABORT, 'search_attempt_events is append-only'); END;
CREATE TRIGGER search_candidates_no_update BEFORE UPDATE ON search_candidates
BEGIN SELECT RAISE(ABORT, 'search_candidates is append-only'); END;
CREATE TRIGGER search_candidates_no_delete BEFORE DELETE ON search_candidates
BEGIN SELECT RAISE(ABORT, 'search_candidates is append-only'); END;
CREATE TRIGGER search_evaluations_no_update BEFORE UPDATE ON search_evaluations
BEGIN SELECT RAISE(ABORT, 'search_evaluations is append-only'); END;
CREATE TRIGGER search_evaluations_no_delete BEFORE DELETE ON search_evaluations
BEGIN SELECT RAISE(ABORT, 'search_evaluations is append-only'); END;
"""

_PORTABLE_COPY_COLUMNS = {
    "trials": ("config_hash", "first_seen_ts"),
    "evaluations": (
        "run_id", "alpha_id", "dataset_version", "recorded_ts", "verdict",
        "content_hash", "evaluation_json",
    ),
    "search_attempt_events": (
        "event_seq", "search_id", "search_kind", "source", "dataset_version",
        "status", "recorded_at", "prev_event_hash", "event_hash",
    ),
    "search_candidates": (
        "candidate_seq", "search_id", "config_hash", "status",
        "registered_at", "prev_candidate_hash", "candidate_hash",
    ),
    "search_evaluations": (
        "link_seq", "search_id", "config_hash", "run_id", "recorded_at",
        "evaluation_content_hash", "prev_link_hash", "link_hash",
    ),
}

_DOCUMENT_FIELDS = {
    "schema_version",
    "mode",
    "ledger_contract_version",
    "ledger_path",
    "records",
}
_RECORD_FIELDS = {
    "sequence",
    "session_id",
    "recorded_at_ms",
    "session_provenance",
    "trials",
    "evaluations",
    "previous_record_hash",
    "record_hash",
}
_SESSION_FIELDS = {
    "search_kind",
    "source",
    "dataset_version",
    "started_at",
    "completed_at",
    "terminal_event_hash",
    "candidates",
    "evaluation_links",
}
_SESSION_EVALUATION_FIELDS = {
    "config_hash",
    "run_id",
    "recorded_at",
    "content_hash",
    "link_hash",
}
_EVALUATION_FIELDS = {
    "run_id",
    "alpha_id",
    "dataset_version",
    "recorded_ts",
    "content_hash",
}
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class TrialLedgerHistoryError(RuntimeError):
    """The ledger or retained history failed a fail-closed check."""


def _canonical_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _record_hash(record: dict[str, Any]) -> str:
    return _canonical_hash({key: value for key, value in record.items() if key != "record_hash"})


def _search_event_hash(row: Mapping[str, Any]) -> str:
    return _canonical_hash(
        {
            "dataset_version": row["dataset_version"],
            "prev_event_hash": row["prev_event_hash"],
            "recorded_at": row["recorded_at"],
            "search_id": row["search_id"],
            "search_kind": row["search_kind"],
            "source": row["source"],
            "status": row["status"],
        }
    )


def _search_candidate_hash(row: Mapping[str, Any]) -> str:
    return _canonical_hash(
        {
            "config_hash": row["config_hash"],
            "prev_candidate_hash": row["prev_candidate_hash"],
            "registered_at": row["registered_at"],
            "search_id": row["search_id"],
            "status": row["status"],
        }
    )


def _search_evaluation_hash(row: Mapping[str, Any]) -> str:
    return _canonical_hash(
        {
            "config_hash": row["config_hash"],
            "evaluation_content_hash": row["evaluation_content_hash"],
            "prev_link_hash": row["prev_link_hash"],
            "recorded_at": row["recorded_at"],
            "run_id": row["run_id"],
            "search_id": row["search_id"],
        }
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(content: bytes | str) -> Any:
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    return json.loads(content, object_pairs_hook=_reject_duplicate_keys)


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _evaluation_identity(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["alpha_id"],
        item["dataset_version"],
        item["recorded_ts"],
        item["content_hash"],
    )


def _validate_history_document(
    document: Any,
    *,
    ledger_path_label: str,
) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != _DOCUMENT_FIELDS:
        raise TrialLedgerHistoryError("history document fields are invalid")
    expected_header = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "mode": HISTORY_MODE,
        "ledger_contract_version": LEDGER_CONTRACT_VERSION,
        "ledger_path": ledger_path_label,
    }
    for key, value in expected_header.items():
        if document.get(key) != value:
            raise TrialLedgerHistoryError(f"history {key} is invalid")

    records = document.get("records")
    if not isinstance(records, list):
        raise TrialLedgerHistoryError("history records must be a list")

    previous_hash = ""
    previous_timestamp = -1
    previous_trials: set[str] = set()
    previous_evaluations: dict[str, tuple[Any, ...]] = {}
    session_ids: set[str] = set()
    for expected_sequence, record in enumerate(records, start=1):
        if not isinstance(record, dict) or set(record) != _RECORD_FIELDS:
            raise TrialLedgerHistoryError(f"history record {expected_sequence} fields are invalid")
        if type(record["sequence"]) is not int or record["sequence"] != expected_sequence:
            raise TrialLedgerHistoryError(f"history record {expected_sequence} sequence is invalid")
        session_id = record["session_id"]
        if not _is_nonempty_string(session_id) or len(session_id) > 256 or session_id in session_ids:
            raise TrialLedgerHistoryError(f"history record {expected_sequence} session_id is invalid")
        session_ids.add(session_id)
        recorded_at_ms = record["recorded_at_ms"]
        if type(recorded_at_ms) is not int or recorded_at_ms < 0 or recorded_at_ms <= previous_timestamp:
            raise TrialLedgerHistoryError(f"history record {expected_sequence} clock is not strictly increasing")
        previous_timestamp = recorded_at_ms

        provenance = record["session_provenance"]
        if not isinstance(provenance, dict) or set(provenance) != _SESSION_FIELDS:
            raise TrialLedgerHistoryError(f"history record {expected_sequence} session provenance is invalid")
        for field in ("search_kind", "source", "dataset_version"):
            if not _is_nonempty_string(provenance[field]):
                raise TrialLedgerHistoryError(f"history record {expected_sequence} session provenance is invalid")
        started_at = provenance["started_at"]
        completed_at = provenance["completed_at"]
        if (
            type(started_at) is not int
            or type(completed_at) is not int
            or started_at < 0
            or completed_at < started_at
            or completed_at > recorded_at_ms
        ):
            raise TrialLedgerHistoryError(f"history record {expected_sequence} session clock is invalid")
        if expected_sequence > 1 and started_at <= records[expected_sequence - 2]["recorded_at_ms"]:
            raise TrialLedgerHistoryError(
                f"history record {expected_sequence} session did not start after the prior snapshot"
            )
        terminal_event_hash = provenance["terminal_event_hash"]
        if not isinstance(terminal_event_hash, str) or not _DIGEST.fullmatch(terminal_event_hash):
            raise TrialLedgerHistoryError(f"history record {expected_sequence} terminal event hash is invalid")
        candidates = provenance["candidates"]
        if (
            not isinstance(candidates, list)
            or not candidates
            or candidates != sorted(set(candidates))
            or any(not isinstance(value, str) or not _DIGEST.fullmatch(value) for value in candidates)
        ):
            raise TrialLedgerHistoryError(f"history record {expected_sequence} session candidates are invalid")
        evaluation_links = provenance["evaluation_links"]
        if not isinstance(evaluation_links, list) or not evaluation_links:
            raise TrialLedgerHistoryError(f"history record {expected_sequence} has no exact evaluation links")
        linked_runs: set[str] = set()
        for link in evaluation_links:
            if not isinstance(link, dict) or set(link) != _SESSION_EVALUATION_FIELDS:
                raise TrialLedgerHistoryError(f"history record {expected_sequence} evaluation link is invalid")
            run_id = link["run_id"]
            if (
                not isinstance(link["config_hash"], str)
                or not _DIGEST.fullmatch(link["config_hash"])
                or link["config_hash"] not in candidates
                or not _is_nonempty_string(run_id)
                or run_id in linked_runs
                or type(link["recorded_at"]) is not int
                or link["recorded_at"] < started_at
                or link["recorded_at"] > completed_at
                or not isinstance(link["content_hash"], str)
                or not _DIGEST.fullmatch(link["content_hash"])
                or not isinstance(link["link_hash"], str)
                or not _DIGEST.fullmatch(link["link_hash"])
            ):
                raise TrialLedgerHistoryError(f"history record {expected_sequence} evaluation link is invalid")
            linked_runs.add(run_id)
        if [link["run_id"] for link in evaluation_links] != sorted(linked_runs):
            raise TrialLedgerHistoryError(f"history record {expected_sequence} evaluation links are not canonical")
        if record["previous_record_hash"] != previous_hash:
            raise TrialLedgerHistoryError(f"history record {expected_sequence} breaks the hash chain")
        expected_hash = _record_hash(record)
        if record["record_hash"] != expected_hash:
            raise TrialLedgerHistoryError(f"history record {expected_sequence} hash is invalid")
        previous_hash = expected_hash

        trials = record["trials"]
        if (
            not isinstance(trials, list)
            or trials != sorted(set(trials))
            or any(not isinstance(value, str) or not _DIGEST.fullmatch(value) for value in trials)
        ):
            raise TrialLedgerHistoryError(f"history record {expected_sequence} trials are invalid")
        current_trials = set(trials)
        if not set(candidates) <= current_trials:
            raise TrialLedgerHistoryError(
                f"history record {expected_sequence} session candidates are absent from trials"
            )
        if not previous_trials <= current_trials:
            raise TrialLedgerHistoryError(f"history record {expected_sequence} removes trials")
        previous_trials = current_trials

        evaluations = record["evaluations"]
        if not isinstance(evaluations, list):
            raise TrialLedgerHistoryError(f"history record {expected_sequence} evaluations are invalid")
        current_evaluations: dict[str, tuple[Any, ...]] = {}
        run_ids: list[str] = []
        for item in evaluations:
            if not isinstance(item, dict) or set(item) != _EVALUATION_FIELDS:
                raise TrialLedgerHistoryError(f"history record {expected_sequence} evaluation fields are invalid")
            run_id = item["run_id"]
            if (
                not _is_nonempty_string(run_id)
                or run_id in current_evaluations
                or not _is_nonempty_string(item["alpha_id"])
                or not _is_nonempty_string(item["dataset_version"])
                or type(item["recorded_ts"]) is not int
                or item["recorded_ts"] < 0
                or not isinstance(item["content_hash"], str)
                or not _DIGEST.fullmatch(item["content_hash"])
            ):
                raise TrialLedgerHistoryError(f"history record {expected_sequence} evaluation is invalid")
            run_ids.append(run_id)
            current_evaluations[run_id] = _evaluation_identity(item)
        if run_ids != sorted(run_ids):
            raise TrialLedgerHistoryError(f"history record {expected_sequence} evaluations are not canonical")
        if any(current_evaluations.get(run_id) != identity for run_id, identity in previous_evaluations.items()):
            raise TrialLedgerHistoryError(f"history record {expected_sequence} removes or changes evaluations")
        for link in evaluation_links:
            identity = current_evaluations.get(link["run_id"])
            if identity is None or identity[1] != provenance["dataset_version"] or identity[3] != link["content_hash"]:
                raise TrialLedgerHistoryError(
                    f"history record {expected_sequence} evaluation link is not bound to its snapshot"
                )
        previous_evaluations = current_evaluations

    return document


def _required_append_only_triggers(connection: sqlite3.Connection) -> None:
    rows = connection.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND sql IS NOT NULL").fetchall()
    trigger_sql = [re.sub(r"\s+", " ", str(row[0]).upper()) for row in rows]
    missing: list[str] = []
    for table in (
        "TRIALS",
        "EVALUATIONS",
        "SEARCH_ATTEMPT_EVENTS",
        "SEARCH_CANDIDATES",
        "SEARCH_EVALUATIONS",
    ):
        for operation in ("UPDATE", "DELETE"):
            pattern = re.compile(rf"BEFORE {operation} ON [\"`\[]?{table}(?:[\"`\]]|\s)")
            if not any(pattern.search(sql) for sql in trigger_sql):
                missing.append(f"{table.lower()}_no_{operation.lower()}_trigger")
    if missing:
        raise TrialLedgerHistoryError("ledger append-only trigger guards are incomplete: " + ", ".join(missing))


def _read_verified_ledger_once(
    ledger_path: Path,
    *,
    require_evaluations: bool = True,
) -> dict[str, Any]:
    if not ledger_path.is_file():
        raise TrialLedgerHistoryError(f"ledger does not exist: {ledger_path}")
    try:
        connection = sqlite3.connect(
            ledger_path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=0.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        data_version_before = int(connection.execute("PRAGMA data_version").fetchone()[0])
        connection.execute("BEGIN")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if integrity != "ok":
            raise TrialLedgerHistoryError("ledger SQLite integrity check failed")
        if schema_version < LEDGER_CONTRACT_VERSION:
            raise TrialLedgerHistoryError("ledger schema version is unsupported")
        required_tables = {
            "trials",
            "evaluations",
            "search_attempt_events",
            "search_candidates",
            "search_evaluations",
        }
        if not required_tables <= tables:
            raise TrialLedgerHistoryError("ledger tables are incomplete")
        _required_append_only_triggers(connection)

        trials = [
            str(row["config_hash"]) for row in connection.execute("SELECT config_hash FROM trials ORDER BY config_hash")
        ]
        if not trials or trials != sorted(set(trials)) or any(not _DIGEST.fullmatch(value) for value in trials):
            raise TrialLedgerHistoryError("ledger trials are empty or invalid")

        evaluations: list[dict[str, Any]] = []
        evaluation_rows_by_run: dict[str, dict[str, Any]] = {}
        for row in connection.execute(
            "SELECT run_id,alpha_id,dataset_version,recorded_ts,content_hash,"
            "evaluation_json FROM evaluations ORDER BY run_id"
        ):
            try:
                evaluation_payload = _load_json(row["evaluation_json"])
                recomputed = stable_hash(
                    {
                        "evaluation": evaluation_payload,
                        "dataset_version": row["dataset_version"],
                    }
                )
            except (TypeError, UnicodeError, ValueError) as exc:
                raise TrialLedgerHistoryError(f"evaluation {row['run_id']} JSON is invalid") from exc
            if recomputed != row["content_hash"]:
                raise TrialLedgerHistoryError(f"evaluation {row['run_id']} content_hash is invalid")
            item = {
                "run_id": row["run_id"],
                "alpha_id": row["alpha_id"],
                "dataset_version": row["dataset_version"],
                "recorded_ts": row["recorded_ts"],
                "content_hash": row["content_hash"],
            }
            if (
                not _is_nonempty_string(item["run_id"])
                or not _is_nonempty_string(item["alpha_id"])
                or not _is_nonempty_string(item["dataset_version"])
                or type(item["recorded_ts"]) is not int
                or item["recorded_ts"] < 0
                or not isinstance(item["content_hash"], str)
                or not _DIGEST.fullmatch(item["content_hash"])
            ):
                raise TrialLedgerHistoryError(f"evaluation {row['run_id']} metadata is invalid")
            evaluations.append(item)
            evaluation_rows_by_run[item["run_id"]] = item
        if require_evaluations and not evaluations:
            raise TrialLedgerHistoryError("ledger evaluations are empty")

        attempts: dict[str, dict[str, Any]] = {}
        event_head = ""
        search_start_high_water = -1
        for expected_sequence, row in enumerate(
            connection.execute(
                "SELECT event_seq,search_id,search_kind,source,dataset_version,"
                "status,recorded_at,prev_event_hash,event_hash "
                "FROM search_attempt_events ORDER BY event_seq"
            ),
            start=1,
        ):
            if (
                row["event_seq"] != expected_sequence
                or not _is_nonempty_string(row["search_id"])
                or not _is_nonempty_string(row["search_kind"])
                or not _is_nonempty_string(row["source"])
                or not _is_nonempty_string(row["dataset_version"])
                or row["status"] not in {"STARTED", "FAILED", "COMPLETED"}
                or type(row["recorded_at"]) is not int
                or row["prev_event_hash"] != event_head
                or not isinstance(row["event_hash"], str)
                or not _DIGEST.fullmatch(row["event_hash"])
                or row["event_hash"] != _search_event_hash(row)
            ):
                raise TrialLedgerHistoryError(f"search event {row['event_seq']} has invalid provenance")
            search_id = row["search_id"]
            metadata = (
                row["search_kind"],
                row["source"],
                row["dataset_version"],
            )
            attempt = attempts.get(search_id)
            if row["status"] == "STARTED":
                if attempt is not None or row["recorded_at"] < search_start_high_water:
                    raise TrialLedgerHistoryError(f"search attempt {search_id} has duplicate STARTED provenance")
                attempts[search_id] = {
                    "search_kind": metadata[0],
                    "source": metadata[1],
                    "dataset_version": metadata[2],
                    "status": "STARTED",
                    "started_at": row["recorded_at"],
                    "completed_at": None,
                    "terminal_event_hash": None,
                    "candidates": [],
                    "evaluation_links": [],
                }
                search_start_high_water = row["recorded_at"]
            elif (
                attempt is None
                or attempt["status"] != "STARTED"
                or metadata
                != (
                    attempt["search_kind"],
                    attempt["source"],
                    attempt["dataset_version"],
                )
                or row["recorded_at"] < attempt["started_at"]
            ):
                raise TrialLedgerHistoryError(f"search attempt {search_id} has an invalid terminal transition")
            else:
                attempt["status"] = row["status"]
                attempt["completed_at"] = row["recorded_at"]
                attempt["terminal_event_hash"] = row["event_hash"]
            event_head = row["event_hash"]

        candidate_head = ""
        latest_candidate_times: dict[str, int] = {}
        candidate_identities: set[tuple[str, str]] = set()
        for expected_sequence, row in enumerate(
            connection.execute(
                "SELECT candidate_seq,search_id,config_hash,status,registered_at,"
                "prev_candidate_hash,candidate_hash "
                "FROM search_candidates ORDER BY candidate_seq"
            ),
            start=1,
        ):
            attempt = attempts.get(row["search_id"])
            identity = (row["search_id"], row["config_hash"])
            terminal_at = None if attempt is None else attempt["completed_at"]
            latest_candidate_at = latest_candidate_times.get(row["search_id"], -1)
            if (
                row["candidate_seq"] != expected_sequence
                or attempt is None
                or identity in candidate_identities
                or row["status"] != "REGISTERED"
                or not isinstance(row["config_hash"], str)
                or not _DIGEST.fullmatch(row["config_hash"])
                or row["config_hash"] not in trials
                or type(row["registered_at"]) is not int
                or row["registered_at"] < latest_candidate_at
                or row["registered_at"] < attempt["started_at"]
                or (terminal_at is not None and row["registered_at"] > terminal_at)
                or row["prev_candidate_hash"] != candidate_head
                or not isinstance(row["candidate_hash"], str)
                or not _DIGEST.fullmatch(row["candidate_hash"])
                or row["candidate_hash"] != _search_candidate_hash(row)
            ):
                raise TrialLedgerHistoryError(f"search candidate {row['candidate_seq']} has invalid provenance")
            attempt["candidates"].append(row["config_hash"])
            candidate_identities.add(identity)
            candidate_head = row["candidate_hash"]
            latest_candidate_times[row["search_id"]] = row["registered_at"]

        link_head = ""
        latest_link_times: dict[str, int] = {}
        linked_identities: set[tuple[str, str]] = set()
        for expected_sequence, row in enumerate(
            connection.execute(
                "SELECT link_seq,search_id,config_hash,run_id,recorded_at,"
                "evaluation_content_hash,prev_link_hash,link_hash "
                "FROM search_evaluations ORDER BY link_seq"
            ),
            start=1,
        ):
            attempt = attempts.get(row["search_id"])
            evaluation = evaluation_rows_by_run.get(row["run_id"])
            identity = (row["search_id"], row["run_id"])
            terminal_at = None if attempt is None else attempt["completed_at"]
            latest_link_at = latest_link_times.get(row["search_id"], -1)
            if (
                row["link_seq"] != expected_sequence
                or attempt is None
                or not attempt["candidates"]
                or row["config_hash"] not in attempt["candidates"]
                or not isinstance(row["config_hash"], str)
                or not _DIGEST.fullmatch(row["config_hash"])
                or not _is_nonempty_string(row["run_id"])
                or identity in linked_identities
                or evaluation is None
                or evaluation["dataset_version"] != attempt["dataset_version"]
                or evaluation["content_hash"] != row["evaluation_content_hash"]
                or type(row["recorded_at"]) is not int
                or row["recorded_at"] < latest_link_at
                or row["recorded_at"] < attempt["started_at"]
                or (terminal_at is not None and row["recorded_at"] > terminal_at)
                or row["prev_link_hash"] != link_head
                or not isinstance(row["evaluation_content_hash"], str)
                or not _DIGEST.fullmatch(row["evaluation_content_hash"])
                or not isinstance(row["link_hash"], str)
                or not _DIGEST.fullmatch(row["link_hash"])
                or row["link_hash"] != _search_evaluation_hash(row)
            ):
                raise TrialLedgerHistoryError(f"search evaluation link {row['link_seq']} has invalid provenance")
            attempt["evaluation_links"].append(
                {
                    "config_hash": row["config_hash"],
                    "run_id": row["run_id"],
                    "recorded_at": row["recorded_at"],
                    "content_hash": row["evaluation_content_hash"],
                    "link_hash": row["link_hash"],
                }
            )
            linked_identities.add(identity)
            link_head = row["link_hash"]
            latest_link_times[row["search_id"]] = row["recorded_at"]

        sessions: dict[str, dict[str, Any]] = {}
        for search_id, attempt in sorted(attempts.items()):
            attempt["candidates"] = sorted(attempt["candidates"])
            attempt["evaluation_links"] = sorted(attempt["evaluation_links"], key=lambda item: item["run_id"])
            sessions[search_id] = attempt
        connection.commit()
        data_version_after = int(connection.execute("PRAGMA data_version").fetchone()[0])
        if data_version_after != data_version_before:
            raise TrialLedgerHistoryError("ledger changed during snapshot capture")
        return {
            "trials": trials,
            "evaluations": evaluations,
            "sessions": sessions,
        }
    except sqlite3.DatabaseError as exc:
        raise TrialLedgerHistoryError(f"ledger cannot be read safely: {exc}") from exc
    finally:
        if "connection" in locals():
            if connection.in_transaction:
                connection.rollback()
            connection.close()


def _read_stable_ledger(
    ledger_path: Path,
    *,
    require_evaluations: bool = True,
) -> dict[str, Any]:
    if require_evaluations:
        first = _read_verified_ledger_once(ledger_path)
        second = _read_verified_ledger_once(ledger_path)
    else:
        first = _read_verified_ledger_once(
            ledger_path,
            require_evaluations=False,
        )
        second = _read_verified_ledger_once(
            ledger_path,
            require_evaluations=False,
        )
    if first != second:
        raise TrialLedgerHistoryError("ledger changed between read-only snapshots")
    return first


def _session_provenance(
    snapshot: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    attempt = snapshot["sessions"].get(session_id)
    if attempt is None:
        raise TrialLedgerHistoryError(f"session_id is not present in the production search ledger: {session_id}")
    if attempt["status"] != "COMPLETED":
        raise TrialLedgerHistoryError(f"search session is not COMPLETED: {session_id}")
    if not attempt["candidates"]:
        raise TrialLedgerHistoryError(f"search session has no registered candidates: {session_id}")
    if not attempt["evaluation_links"]:
        raise TrialLedgerHistoryError(f"search session has no exact search_id-to-evaluation binding: {session_id}")
    return {
        "search_kind": attempt["search_kind"],
        "source": attempt["source"],
        "dataset_version": attempt["dataset_version"],
        "started_at": attempt["started_at"],
        "completed_at": attempt["completed_at"],
        "terminal_event_hash": attempt["terminal_event_hash"],
        "candidates": attempt["candidates"],
        "evaluation_links": attempt["evaluation_links"],
    }


def _assert_history_sessions_match_ledger(
    document: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    for record in document["records"]:
        current = _session_provenance(snapshot, record["session_id"])
        if current != record["session_provenance"]:
            raise TrialLedgerHistoryError(
                f"retained session provenance does not match the current ledger: {record['session_id']}"
            )


def _inspection_metadata_once(ledger_path: Path) -> dict[str, Any]:
    if not ledger_path.is_file():
        raise TrialLedgerHistoryError(f"ledger does not exist: {ledger_path}")
    try:
        connection = sqlite3.connect(
            ledger_path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=0.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if integrity != "ok":
            raise TrialLedgerHistoryError("ledger SQLite integrity check failed")

        def count(table: str) -> int:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if table in tables else 0

        return {
            "schema_version": schema_version,
            "trial_count": count("trials"),
            "evaluation_count": count("evaluations"),
            "search_event_count": count("search_attempt_events"),
            "search_candidate_count": count("search_candidates"),
            "search_evaluation_link_count": count("search_evaluations"),
            "has_exact_binding_schema": "search_evaluations" in tables,
        }
    except sqlite3.DatabaseError as exc:
        raise TrialLedgerHistoryError(f"ledger cannot be read safely: {exc}") from exc
    finally:
        if "connection" in locals():
            connection.close()


def inspect_ledger(
    *,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
) -> dict[str, Any]:
    """Return a read-only operational preflight; never creates or migrates."""
    ledger = Path(ledger_path)
    first = _inspection_metadata_once(ledger)
    second = _inspection_metadata_once(ledger)
    if first != second:
        raise TrialLedgerHistoryError("ledger changed between read-only inspections")
    if first["schema_version"] < LEDGER_CONTRACT_VERSION or not first["has_exact_binding_schema"]:
        return {
            "status": "verified_not_capture_ready",
            "ledger_contract_version": first["schema_version"],
            "required_ledger_contract_version": LEDGER_CONTRACT_VERSION,
            "capture_ready": False,
            "reason": "EXACT_SEARCH_EVALUATION_BINDING_UNAVAILABLE",
            "trial_count": first["trial_count"],
            "evaluation_count": first["evaluation_count"],
            "attempts": [],
        }

    snapshot = _read_stable_ledger(ledger, require_evaluations=False)
    attempts = []
    for search_id, attempt in snapshot["sessions"].items():
        if attempt["status"] != "COMPLETED" or not attempt["candidates"]:
            continue
        exact_count = len(attempt["evaluation_links"])
        attempts.append(
            {
                "search_id": search_id,
                "search_kind": attempt["search_kind"],
                "source": attempt["source"],
                "dataset_version": attempt["dataset_version"],
                "started_at": attempt["started_at"],
                "completed_at": attempt["completed_at"],
                "candidate_count": len(attempt["candidates"]),
                "exact_evaluation_count": exact_count,
                "capture_status": ("CAPTURABLE" if exact_count > 0 else "NOT_CAPTURABLE_NO_EXACT_EVALUATION_BINDING"),
            }
        )
    attempts.sort(key=lambda item: (item["completed_at"], item["search_id"]))
    return {
        "status": "verified",
        "ledger_contract_version": LEDGER_CONTRACT_VERSION,
        "required_ledger_contract_version": LEDGER_CONTRACT_VERSION,
        "capture_ready": any(item["capture_status"] == "CAPTURABLE" for item in attempts),
        "reason": None,
        "trial_count": len(snapshot["trials"]),
        "evaluation_count": len(snapshot["evaluations"]),
        "attempts": attempts,
    }


def _history_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


@contextmanager
def _exclusive_output_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise TrialLedgerHistoryError("another history collector holds the output lock") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as lock:
            lock.write(str(os.getpid()))
            lock.flush()
            os.fsync(lock.fileno())
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _atomic_publish(
    path: Path,
    document: dict[str, Any],
    *,
    expected_previous: bytes | None,
    before_replace: Callable[[], None],
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            json.dump(
                document,
                output,
                ensure_ascii=True,
                allow_nan=False,
                indent=2,
            )
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        before_replace()
        if _history_bytes(path) != expected_previous:
            raise TrialLedgerHistoryError("history changed concurrently before publish")
        os.replace(temporary_path, path)
    except TrialLedgerHistoryError:
        raise
    except OSError as exc:
        raise TrialLedgerHistoryError(f"history cannot be atomically published: {exc}") from exc
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _base_document(ledger_path_label: str) -> dict[str, Any]:
    return {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "mode": HISTORY_MODE,
        "ledger_contract_version": LEDGER_CONTRACT_VERSION,
        "ledger_path": ledger_path_label,
        "records": [],
    }


def _assert_snapshot_extends_history(
    document: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    records = document["records"]
    if not records:
        return
    previous = records[-1]
    if not set(previous["trials"]) <= set(snapshot["trials"]):
        raise TrialLedgerHistoryError("current ledger removed previously retained trials")
    current_evaluations = {item["run_id"]: _evaluation_identity(item) for item in snapshot["evaluations"]}
    if any(current_evaluations.get(item["run_id"]) != _evaluation_identity(item) for item in previous["evaluations"]):
        raise TrialLedgerHistoryError("current ledger removed or changed previously retained evaluations")


def capture_history(
    *,
    session_id: str,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    ledger_path_label: str | None = None,
    recorded_at_ms: int | None = None,
) -> dict[str, Any]:
    """Append exactly one verified ledger snapshot for ``session_id``."""
    if not _is_nonempty_string(session_id) or len(session_id) > 256:
        raise TrialLedgerHistoryError("session_id must be a normalized non-empty string")
    ledger = Path(ledger_path)
    history = Path(history_path)
    label = ledger_path_label or ledger.as_posix()
    with _exclusive_output_lock(history):
        previous_bytes = _history_bytes(history)
        if previous_bytes is None:
            document = _base_document(label)
        else:
            try:
                document = _validate_history_document(
                    _load_json(previous_bytes),
                    ledger_path_label=label,
                )
            except (UnicodeError, ValueError) as exc:
                raise TrialLedgerHistoryError("history JSON is invalid") from exc

        if any(record["session_id"] == session_id for record in document["records"]):
            raise TrialLedgerHistoryError(f"session_id is already retained: {session_id}")
        snapshot = _read_stable_ledger(ledger)
        _assert_snapshot_extends_history(document, snapshot)
        _assert_history_sessions_match_ledger(document, snapshot)
        provenance = _session_provenance(snapshot, session_id)
        timestamp = time.time_ns() // 1_000_000 if recorded_at_ms is None else recorded_at_ms
        previous_timestamp = document["records"][-1]["recorded_at_ms"] if document["records"] else -1
        if type(timestamp) is not int or timestamp < 0 or timestamp <= previous_timestamp:
            raise TrialLedgerHistoryError("recorded_at_ms must increase strictly")

        previous_hash = document["records"][-1]["record_hash"] if document["records"] else ""
        record = {
            "sequence": len(document["records"]) + 1,
            "session_id": session_id,
            "recorded_at_ms": timestamp,
            "session_provenance": provenance,
            "trials": snapshot["trials"],
            "evaluations": snapshot["evaluations"],
            "previous_record_hash": previous_hash,
        }
        record["record_hash"] = _record_hash(record)
        new_document = {**document, "records": [*document["records"], record]}
        _validate_history_document(new_document, ledger_path_label=label)

        def verify_unchanged() -> None:
            if _read_stable_ledger(ledger) != snapshot:
                raise TrialLedgerHistoryError("ledger changed before history publish")

        _atomic_publish(
            history,
            new_document,
            expected_previous=previous_bytes,
            before_replace=verify_unchanged,
        )
        return new_document


def check_history(
    *,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    ledger_path_label: str | None = None,
) -> dict[str, Any]:
    """Verify retained history and its exact current-ledger binding without writes."""
    ledger = Path(ledger_path)
    history = Path(history_path)
    label = ledger_path_label or ledger.as_posix()
    before = _history_bytes(history)
    if before is None:
        raise TrialLedgerHistoryError(f"history does not exist: {history}")
    try:
        document = _validate_history_document(
            _load_json(before),
            ledger_path_label=label,
        )
    except (UnicodeError, ValueError) as exc:
        raise TrialLedgerHistoryError("history JSON is invalid") from exc
    if not document["records"]:
        raise TrialLedgerHistoryError("history contains no session records")
    snapshot = _read_stable_ledger(ledger)
    _assert_history_sessions_match_ledger(document, snapshot)
    final = document["records"][-1]
    if final["trials"] != snapshot["trials"]:
        raise TrialLedgerHistoryError("history does not match current ledger trials")
    if final["evaluations"] != snapshot["evaluations"]:
        raise TrialLedgerHistoryError("history does not match current ledger evaluations")
    if _history_bytes(history) != before:
        raise TrialLedgerHistoryError("history changed during read-only verification")
    if _read_stable_ledger(ledger) != snapshot:
        raise TrialLedgerHistoryError("ledger changed during read-only verification")
    return document


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _portable_tables(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _canonical_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip().rstrip(";").upper()


def _validate_portable_append_only_triggers(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'"
    ).fetchall()
    triggers = {str(name): (str(table), sql) for name, table, sql in rows}
    if set(triggers) != set(_PORTABLE_APPEND_ONLY_TRIGGER_GUARDS):
        raise TrialLedgerHistoryError("portable snapshot trigger allowlist failed")
    for name, (table, operation) in _PORTABLE_APPEND_ONLY_TRIGGER_GUARDS.items():
        actual_table, sql = triggers[name]
        expected_sql = (
            f"CREATE TRIGGER {name} BEFORE {operation} ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
        )
        if actual_table != table or not isinstance(sql, str) or _canonical_sql(sql) != _canonical_sql(expected_sql):
            raise TrialLedgerHistoryError(f"portable snapshot trigger definition failed: {name}")


def _build_portable_snapshot(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.execute("BEGIN")
        destination_connection.executescript(_PORTABLE_SCHEMA)
        for table, columns in _PORTABLE_COPY_COLUMNS.items():
            column_sql = ",".join(columns)
            rows = source_connection.execute(
                f"SELECT {column_sql} FROM {table} ORDER BY 1"
            ).fetchall()
            placeholders = ",".join("?" for _ in columns)
            destination_connection.executemany(
                f"INSERT INTO {table}({column_sql}) VALUES ({placeholders})",
                rows,
            )
        destination_connection.execute(f"PRAGMA user_version={LEDGER_CONTRACT_VERSION}")
        destination_connection.commit()
        integrity = destination_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok" or _portable_tables(destination_connection) != _PORTABLE_ALLOWED_TABLES:
            raise TrialLedgerHistoryError("portable snapshot contract failed")
    except sqlite3.DatabaseError as exc:
        raise TrialLedgerHistoryError(f"portable snapshot cannot be built: {exc}") from exc
    finally:
        source_connection.close()
        destination_connection.close()


def _validate_seal_document(
    document: Any,
    *,
    ledger_path_label: str,
    history_path_label: str,
    snapshot_path_label: str,
) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != _SEAL_FIELDS:
        raise TrialLedgerHistoryError("portable seal fields are invalid")
    expected = {
        "schema_version": PORTABLE_SEAL_SCHEMA_VERSION,
        "mode": PORTABLE_SEAL_MODE,
        "ledger_path": ledger_path_label,
        "history_path": history_path_label,
        "snapshot_path": snapshot_path_label,
    }
    if any(document.get(key) != value for key, value in expected.items()):
        raise TrialLedgerHistoryError("portable seal contract is invalid")
    if any(_DIGEST.fullmatch(document.get(key, "")) is None for key in (
        "history_sha256", "snapshot_sha256", "history_head_hash"
    )):
        raise TrialLedgerHistoryError("portable seal digest is invalid")
    for key in ("record_count", "trial_count", "evaluation_count", "sealed_at_ms"):
        if type(document.get(key)) is not int or document[key] < 0:
            raise TrialLedgerHistoryError(f"portable seal {key} is invalid")
    return document


def check_snapshot(
    *,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    seal_path: str | Path = DEFAULT_SEAL_PATH,
    ledger_path_label: str = DEFAULT_LEDGER_PATH,
    history_path_label: str = DEFAULT_HISTORY_PATH,
    snapshot_path_label: str = DEFAULT_SNAPSHOT_PATH,
) -> dict[str, Any]:
    history_file, snapshot_file, seal_file = map(Path, (history_path, snapshot_path, seal_path))
    try:
        seal = _validate_seal_document(
            _load_json(seal_file.read_bytes()),
            ledger_path_label=ledger_path_label,
            history_path_label=history_path_label,
            snapshot_path_label=snapshot_path_label,
        )
    except (OSError, UnicodeError, ValueError) as exc:
        raise TrialLedgerHistoryError(f"portable seal cannot be read: {exc}") from exc
    try:
        if _file_sha256(history_file) != seal["history_sha256"]:
            raise TrialLedgerHistoryError("portable seal history hash mismatch")
        if _file_sha256(snapshot_file) != seal["snapshot_sha256"]:
            raise TrialLedgerHistoryError("portable seal snapshot hash mismatch")
    except OSError as exc:
        raise TrialLedgerHistoryError(f"portable bundle file is unavailable: {exc}") from exc
    history_document = check_history(
        ledger_path=snapshot_file,
        history_path=history_file,
        ledger_path_label=ledger_path_label,
    )
    final = history_document["records"][-1]
    connection = sqlite3.connect(snapshot_file.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if _portable_tables(connection) != _PORTABLE_ALLOWED_TABLES:
            raise TrialLedgerHistoryError("portable snapshot table allowlist failed")
        _validate_portable_append_only_triggers(connection)
    finally:
        connection.close()
    expected_values = {
        "history_head_hash": final["record_hash"],
        "record_count": len(history_document["records"]),
        "trial_count": len(final["trials"]),
        "evaluation_count": len(final["evaluations"]),
    }
    if any(seal[key] != value for key, value in expected_values.items()):
        raise TrialLedgerHistoryError("portable seal counts or history head mismatch")
    if seal["sealed_at_ms"] < final["recorded_at_ms"]:
        raise TrialLedgerHistoryError("portable seal predates final history record")
    return seal


def seal_snapshot(
    *,
    ledger_path: str | Path = DEFAULT_LEDGER_PATH,
    history_path: str | Path = DEFAULT_HISTORY_PATH,
    snapshot_path: str | Path = DEFAULT_SNAPSHOT_PATH,
    seal_path: str | Path = DEFAULT_SEAL_PATH,
    ledger_path_label: str = DEFAULT_LEDGER_PATH,
    history_path_label: str = DEFAULT_HISTORY_PATH,
    snapshot_path_label: str = DEFAULT_SNAPSHOT_PATH,
    sealed_at_ms: int | None = None,
) -> dict[str, Any]:
    ledger_file = Path(ledger_path)
    history_file = Path(history_path)
    snapshot_file = Path(snapshot_path)
    seal_file = Path(seal_path)
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    seal_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_snapshot: Path | None = None
    with _exclusive_output_lock(history_file):
        history_bytes = _history_bytes(history_file)
        if history_bytes is None:
            raise TrialLedgerHistoryError("history does not exist for portable sealing")
        document = check_history(
            ledger_path=ledger_file,
            history_path=history_file,
            ledger_path_label=ledger_path_label,
        )
        live_snapshot = _read_stable_ledger(ledger_file)
        final = document["records"][-1]
        timestamp = time.time_ns() // 1_000_000 if sealed_at_ms is None else sealed_at_ms
        if type(timestamp) is not int or timestamp < final["recorded_at_ms"]:
            raise TrialLedgerHistoryError("portable seal predates final history record")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{snapshot_file.name}.",
            suffix=".tmp",
            dir=snapshot_file.parent,
        )
        os.close(descriptor)
        temporary_snapshot = Path(temporary_name)
        try:
            _build_portable_snapshot(ledger_file, temporary_snapshot)
            check_history(
                ledger_path=temporary_snapshot,
                history_path=history_file,
                ledger_path_label=ledger_path_label,
            )
            if _history_bytes(history_file) != history_bytes:
                raise TrialLedgerHistoryError("history changed during portable sealing")
            if _read_stable_ledger(ledger_file) != live_snapshot:
                raise TrialLedgerHistoryError("live ledger changed during portable sealing")
            seal = {
                "schema_version": PORTABLE_SEAL_SCHEMA_VERSION,
                "mode": PORTABLE_SEAL_MODE,
                "ledger_path": ledger_path_label,
                "history_path": history_path_label,
                "snapshot_path": snapshot_path_label,
                "history_sha256": hashlib.sha256(history_bytes).hexdigest(),
                "snapshot_sha256": _file_sha256(temporary_snapshot),
                "history_head_hash": final["record_hash"],
                "record_count": len(document["records"]),
                "trial_count": len(final["trials"]),
                "evaluation_count": len(final["evaluations"]),
                "sealed_at_ms": timestamp,
            }
            _validate_seal_document(
                seal,
                ledger_path_label=ledger_path_label,
                history_path_label=history_path_label,
                snapshot_path_label=snapshot_path_label,
            )
            previous_seal = _history_bytes(seal_file)
            os.replace(temporary_snapshot, snapshot_file)

            def verify_inputs_unchanged() -> None:
                if _history_bytes(history_file) != history_bytes:
                    raise TrialLedgerHistoryError("history changed before seal publish")
                if _read_stable_ledger(ledger_file) != live_snapshot:
                    raise TrialLedgerHistoryError("live ledger changed before seal publish")
                if _file_sha256(snapshot_file) != seal["snapshot_sha256"]:
                    raise TrialLedgerHistoryError("snapshot changed before seal publish")

            _atomic_publish(
                seal_file,
                seal,
                expected_previous=previous_seal,
                before_replace=verify_inputs_unchanged,
            )
            return check_snapshot(
                history_path=history_file,
                snapshot_path=snapshot_file,
                seal_path=seal_file,
                ledger_path_label=ledger_path_label,
                history_path_label=history_path_label,
                snapshot_path_label=snapshot_path_label,
            )
        finally:
            if temporary_snapshot is not None:
                try:
                    temporary_snapshot.unlink()
                except FileNotFoundError:
                    pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Append, seal, or verify the production alpha trial-ledger history"
    )
    parser.add_argument("--check", action="store_true", help="verify against the live authority")
    parser.add_argument("--inspect-ledger", action="store_true", help="inspect live capture readiness")
    parser.add_argument("--seal-snapshot", action="store_true", help="publish portable snapshot evidence")
    parser.add_argument("--check-snapshot", action="store_true", help="verify portable evidence only")
    parser.add_argument("--session-id", help="completed search ID to retain")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    selected_mode_count = sum(
        int(value)
        for value in (
            args.check,
            args.inspect_ledger,
            args.seal_snapshot,
            args.check_snapshot,
        )
    )
    if selected_mode_count > 1:
        print("only one mode may be selected", file=sys.stderr)
        return 2
    if selected_mode_count and args.session_id is not None:
        print("--session-id cannot be combined with a mode", file=sys.stderr)
        return 2
    if selected_mode_count == 0 and args.session_id is None:
        print("--session-id is required unless a mode is used", file=sys.stderr)
        return 2
    try:
        if args.inspect_ledger:
            result = inspect_ledger()
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
            return 0
        if args.check:
            result = check_history(ledger_path_label=DEFAULT_LEDGER_PATH)
        elif args.seal_snapshot:
            result = seal_snapshot(
                ledger_path_label=DEFAULT_LEDGER_PATH,
                history_path_label=DEFAULT_HISTORY_PATH,
                snapshot_path_label=DEFAULT_SNAPSHOT_PATH,
            )
        elif args.check_snapshot:
            result = check_snapshot(
                ledger_path_label=DEFAULT_LEDGER_PATH,
                history_path_label=DEFAULT_HISTORY_PATH,
                snapshot_path_label=DEFAULT_SNAPSHOT_PATH,
            )
        else:
            result = capture_history(
                session_id=args.session_id,
                ledger_path_label=DEFAULT_LEDGER_PATH,
            )
    except TrialLedgerHistoryError as exc:
        print(f"alpha trial-ledger history rejected: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "verified",
                "record_count": result.get("record_count", len(result.get("records", []))),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
