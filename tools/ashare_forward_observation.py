"""Advance the frozen A-share candidate set with genuinely new sessions.

This worker never backfills a forward decision.  A decision is appended only
when the latest available trading session is a due signal session after the
immutable search freeze.  The target is therefore recorded before the next
open and before its holding-period outcome exists.  Outcomes are appended later
to a separate hash chain; neither table permits update or delete.

The ledger is observation-only.  It contains no account, order, broker,
credential, lifecycle-promotion, or paper-fill path.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import datetime as dt
from pathlib import Path
import sqlite3
import time
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from quantos.contracts.hashing import stable_hash
from quantos.contracts.scheduled_worker import (
    SCHEDULE_EXIT_FAILURE,
    SCHEDULE_EXIT_SAFE_BLOCKED,
    SCHEDULE_EXIT_SUCCESS,
)
from quantos.runtime_paths import runtime_state_root
from tools.ashare_alpha_search_technical import (
    TECHNICAL_STATUS,
    CandidateSpec,
    _load_panel_cache,
    _metric,
    _target_weights,
    build_factor_matrix,
)
from tools.ashare_alpha_search_worker import (
    AShareSearchWorkerError,
    _database_latest_trade_date,
    _ensure_bound_panel_cache,
    _source_not_final_attestation,
    _source_not_final_report,
    _source_session_finality,
    _source_signature,
    _source_prefix_bindings,
    _validate_frozen_search_artifact,
    _validate_freeze_document,
)
from tools.ashare_alpha_runtime_lock import DEFAULT_RUNTIME_LOCK, acquire_runtime_lock
from tools.cache_io import atomic_write_json


ROOT = runtime_state_root()
PIT_ROOT = ROOT / "data" / "stocks" / "pit" / "tdx-windows-20260830"
DEFAULT_DATABASE = PIT_ROOT / "ashare_tdx_pit.sqlite"
DEFAULT_PANEL_CACHE = PIT_ROOT / "ashare_tdx_pit.adv20000000.panel.npz"
DEFAULT_FREEZE = ROOT / "data" / "cache" / "ashare_forward_freeze.json"
DEFAULT_LEDGER = ROOT / "data" / "cache" / "ashare_forward_observation.db"
DEFAULT_OUTPUT = ROOT / "data" / "cache" / "ashare_forward_observation.json"
MIN_ADV_CNY = 20_000_000.0
COST_BPS = 30.0
_ZERO_HASH = "0" * 64
_LAST_RELEASE_RUN_ATTESTATION: dict[str, Any] | None = None
_SAFETY = {
    "paper_only": True,
    "live_allowed": False,
    "order_submission_allowed": False,
    "credentials_accessed": False,
    "broker_connectivity": False,
}


class AShareForwardObservationError(RuntimeError):
    """The frozen candidate or append-only observation authority is invalid."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_authority(
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    schema_version INTEGER NOT NULL,
    freeze_sha256 TEXT NOT NULL,
    candidate_set_hash TEXT NOT NULL,
    freeze_cutoff INTEGER NOT NULL,
    source_prefix_sha256 TEXT NOT NULL,
    source_prefix_rows INTEGER NOT NULL,
    source_database_sha256_at_freeze TEXT NOT NULL,
    search_artifact_sha256 TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    previous_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_checkpoints(
    sequence INTEGER PRIMARY KEY,
    through_date INTEGER NOT NULL UNIQUE,
    prefix_algorithm TEXT NOT NULL,
    prefix_sha256 TEXT NOT NULL,
    prefix_rows INTEGER NOT NULL,
    source_database_sha256 TEXT NOT NULL,
    observation_status TEXT NOT NULL,
    decision_deadline_ms INTEGER NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    previous_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checkpoint_legacy_authority(
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    legacy_table_name TEXT NOT NULL,
    legacy_schema_version INTEGER NOT NULL,
    legacy_row_count INTEGER NOT NULL,
    legacy_head_hash TEXT NOT NULL,
    legacy_last_through_date INTEGER NOT NULL,
    legacy_last_prefix_algorithm TEXT NOT NULL,
    legacy_last_prefix_sha256 TEXT NOT NULL,
    legacy_last_prefix_rows INTEGER NOT NULL,
    migration_status TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    previous_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions(
    sequence INTEGER PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    signal_date INTEGER NOT NULL,
    hold_days INTEGER NOT NULL,
    factor_spec_json TEXT NOT NULL,
    selected_json TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    previous_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    UNIQUE(candidate_id, signal_date)
);
CREATE TABLE IF NOT EXISTS outcomes(
    sequence INTEGER PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    signal_date INTEGER NOT NULL,
    entry_date INTEGER NOT NULL,
    exit_date INTEGER NOT NULL,
    gross_return REAL NOT NULL,
    net_return REAL NOT NULL,
    turnover REAL NOT NULL,
    end_weights_json TEXT NOT NULL,
    unpriced_legs INTEGER NOT NULL,
    source_sha256 TEXT NOT NULL,
    recorded_at_ms INTEGER NOT NULL,
    previous_hash TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    UNIQUE(candidate_id, signal_date),
    FOREIGN KEY(candidate_id, signal_date)
      REFERENCES decisions(candidate_id, signal_date)
);
CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_update BEFORE UPDATE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS outcomes_no_delete BEFORE DELETE ON outcomes
BEGIN SELECT RAISE(ABORT, 'outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS ledger_authority_no_update BEFORE UPDATE ON ledger_authority
BEGIN SELECT RAISE(ABORT, 'ledger authority is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ledger_authority_no_delete BEFORE DELETE ON ledger_authority
BEGIN SELECT RAISE(ABORT, 'ledger authority is immutable'); END;
CREATE TRIGGER IF NOT EXISTS source_checkpoints_no_update BEFORE UPDATE ON source_checkpoints
BEGIN SELECT RAISE(ABORT, 'source checkpoints are append-only'); END;
CREATE TRIGGER IF NOT EXISTS source_checkpoints_no_delete BEFORE DELETE ON source_checkpoints
BEGIN SELECT RAISE(ABORT, 'source checkpoints are append-only'); END;
CREATE TRIGGER IF NOT EXISTS checkpoint_legacy_authority_no_update BEFORE UPDATE ON checkpoint_legacy_authority
BEGIN SELECT RAISE(ABORT, 'legacy checkpoint authority is immutable'); END;
CREATE TRIGGER IF NOT EXISTS checkpoint_legacy_authority_no_delete BEFORE DELETE ON checkpoint_legacy_authority
BEGIN SELECT RAISE(ABORT, 'legacy checkpoint authority is immutable'); END;
"""


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _record_hash(payload: Mapping[str, Any], previous_hash: str) -> str:
    return hashlib.sha256(
        (previous_hash + "\0" + _canonical(dict(payload))).encode("utf-8")
    ).hexdigest()


def _file_hash(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_object_bytes(path: str | Path) -> tuple[dict[str, Any], str]:
    try:
        encoded = Path(path).read_bytes()
        value = json.loads(encoded.decode("utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise AShareForwardObservationError(f"unreadable freeze: {path}") from exc
    if not isinstance(value, dict):
        raise AShareForwardObservationError("forward freeze is not an object")
    return value, hashlib.sha256(encoded).hexdigest()


def _load_freeze(
    path: str | Path,
) -> tuple[dict[str, Any], tuple[CandidateSpec, ...], str]:
    document, freeze_sha256 = _read_object_bytes(path)
    try:
        rows = _validate_freeze_document(document)
        _validate_frozen_search_artifact(document, path)
        candidates = tuple(CandidateSpec(**row) for row in rows)
    except (AShareSearchWorkerError, TypeError, ValueError) as exc:
        raise AShareForwardObservationError("forward freeze contract is invalid") from exc
    return document, candidates, freeze_sha256


def _payload_from_row(row: sqlite3.Row, fields: Sequence[str]) -> dict[str, Any]:
    return {name: row[name] for name in fields}


_DECISION_FIELDS = (
    "sequence",
    "candidate_id",
    "signal_date",
    "hold_days",
    "factor_spec_json",
    "selected_json",
    "source_sha256",
    "recorded_at_ms",
)
_OUTCOME_FIELDS = (
    "sequence",
    "candidate_id",
    "signal_date",
    "entry_date",
    "exit_date",
    "gross_return",
    "net_return",
    "turnover",
    "end_weights_json",
    "unpriced_legs",
    "source_sha256",
    "recorded_at_ms",
)
_AUTHORITY_FIELDS = (
    "schema_version",
    "freeze_sha256",
    "candidate_set_hash",
    "freeze_cutoff",
    "source_prefix_sha256",
    "source_prefix_rows",
    "source_database_sha256_at_freeze",
    "search_artifact_sha256",
    "recorded_at_ms",
)
_CHECKPOINT_FIELDS = (
    "sequence",
    "through_date",
    "prefix_algorithm",
    "prefix_sha256",
    "prefix_rows",
    "source_database_sha256",
    "observation_status",
    "decision_deadline_ms",
    "recorded_at_ms",
)
_LEGACY_CHECKPOINT_FIELDS = (
    "sequence",
    "through_date",
    "prefix_algorithm",
    "prefix_sha256",
    "prefix_rows",
    "source_database_sha256",
    "recorded_at_ms",
)
_LEGACY_AUTHORITY_FIELDS = (
    "legacy_table_name",
    "legacy_schema_version",
    "legacy_row_count",
    "legacy_head_hash",
    "legacy_last_through_date",
    "legacy_last_prefix_algorithm",
    "legacy_last_prefix_sha256",
    "legacy_last_prefix_rows",
    "migration_status",
    "recorded_at_ms",
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_TIMELY_OBSERVATION = "TIMELY_PRE_NEXT_OPEN"
_FREEZE_ANCHOR = "FREEZE_ANCHOR"
_MISSED_DEADLINE = "MISSED_NEXT_OPEN_DEADLINE"
_SOURCE_NOT_FINAL = "SOURCE_NOT_FINAL_AT_OBSERVATION"


def _decision_window_ms(session_date: int) -> tuple[int, int]:
    """Return conservative close-t and next-weekday-open deadlines.

    The weekend-only next-session approximation can be earlier than the real
    exchange open around holidays, never later, so it cannot admit a decision
    after information from the true next open was available.
    """

    try:
        day = dt.datetime.strptime(str(session_date), "%Y%m%d").date()
    except ValueError as exc:
        raise AShareForwardObservationError("source session date is invalid") from exc
    next_day = day + dt.timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += dt.timedelta(days=1)
    close_at = dt.datetime.combine(day, dt.time(15, 0), tzinfo=_SHANGHAI)
    next_open = dt.datetime.combine(next_day, dt.time(9, 30), tzinfo=_SHANGHAI)
    return int(close_at.timestamp() * 1000), int(next_open.timestamp() * 1000)


def _verify_chain(
    connection: sqlite3.Connection,
    table: str,
    fields: Sequence[str],
) -> tuple[int, str]:
    previous = _ZERO_HASH
    count = 0
    for row in connection.execute(f"SELECT * FROM {table} ORDER BY sequence"):
        if int(row["sequence"]) != count + 1:
            raise AShareForwardObservationError(f"{table} sequence is not contiguous")
        payload = _payload_from_row(row, fields)
        if row["previous_hash"] != previous:
            raise AShareForwardObservationError(f"{table} previous hash mismatch")
        expected = _record_hash(payload, previous)
        if row["record_hash"] != expected:
            raise AShareForwardObservationError(f"{table} record hash mismatch")
        previous = expected
        count += 1
    return count, previous


def _table_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})"))


def _legacy_checkpoint_payload(
    connection: sqlite3.Connection,
    *,
    recorded_at_ms: int,
) -> dict[str, Any] | None:
    if not _table_columns(connection, "source_checkpoints_legacy_v1"):
        return None
    expected_columns = (*_LEGACY_CHECKPOINT_FIELDS, "previous_hash", "record_hash")
    if _table_columns(connection, "source_checkpoints_legacy_v1") != expected_columns:
        raise AShareForwardObservationError(
            "legacy source checkpoint schema is unsupported"
        )
    count, head = _verify_chain(
        connection,
        "source_checkpoints_legacy_v1",
        _LEGACY_CHECKPOINT_FIELDS,
    )
    last = connection.execute(
        "SELECT * FROM source_checkpoints_legacy_v1 ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    if count == 0 or last is None:
        # An empty legacy table contains no authority worth carrying forward.
        return None
    return {
        "legacy_table_name": "source_checkpoints_legacy_v1",
        "legacy_schema_version": 1,
        "legacy_row_count": count,
        "legacy_head_hash": head,
        "legacy_last_through_date": int(last["through_date"]),
        "legacy_last_prefix_algorithm": str(last["prefix_algorithm"]),
        "legacy_last_prefix_sha256": str(last["prefix_sha256"]),
        "legacy_last_prefix_rows": int(last["prefix_rows"]),
        "migration_status": "LEGACY_UNATTESTED_DECISION_TIME_RESTARTED",
        "recorded_at_ms": recorded_at_ms,
    }


def _verify_or_record_legacy_checkpoint_authority(
    connection: sqlite3.Connection,
    *,
    recorded_at_ms: int,
) -> dict[str, Any] | None:
    existing = connection.execute(
        "SELECT * FROM checkpoint_legacy_authority WHERE singleton=1"
    ).fetchone()
    payload = _legacy_checkpoint_payload(
        connection,
        recorded_at_ms=(
            int(existing["recorded_at_ms"]) if existing is not None else recorded_at_ms
        ),
    )
    if payload is None:
        if existing is not None:
            raise AShareForwardObservationError(
                "legacy checkpoint authority exists without its preserved ledger"
            )
        return None
    record_hash = _record_hash(payload, _ZERO_HASH)
    if existing is None:
        connection.execute(
            "INSERT INTO checkpoint_legacy_authority VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                *(payload[field] for field in _LEGACY_AUTHORITY_FIELDS),
                _ZERO_HASH,
                record_hash,
            ),
        )
    else:
        if (
            _payload_from_row(existing, _LEGACY_AUTHORITY_FIELDS) != payload
            or existing["previous_hash"] != _ZERO_HASH
            or existing["record_hash"] != record_hash
        ):
            raise AShareForwardObservationError(
                "legacy source checkpoint migration authority is invalid"
            )
    return {**payload, "previous_hash": _ZERO_HASH, "record_hash": record_hash}


def _ensure_ledger_schema(
    connection: sqlite3.Connection,
    *,
    recorded_at_ms: int,
) -> dict[str, Any] | None:
    """Create v2 checkpoints or preserve and attest an old v1 chain.

    The old hashes did not include decision-time status, so they are never
    silently reinterpreted as timely observations.  Their table and chain are
    retained read-only and the v2 observation clock starts explicitly anew.
    """

    current_columns = _table_columns(connection, "source_checkpoints")
    v2_columns = (*_CHECKPOINT_FIELDS, "previous_hash", "record_hash")
    v1_columns = (*_LEGACY_CHECKPOINT_FIELDS, "previous_hash", "record_hash")
    if current_columns and current_columns not in (v1_columns, v2_columns):
        raise AShareForwardObservationError("source checkpoint schema is unsupported")
    if current_columns == v1_columns:
        if _table_columns(connection, "source_checkpoints_legacy_v1"):
            raise AShareForwardObservationError(
                "both active and archived legacy checkpoint tables exist"
            )
        _verify_chain(connection, "source_checkpoints", _LEGACY_CHECKPOINT_FIELDS)
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            "ALTER TABLE source_checkpoints RENAME TO source_checkpoints_legacy_v1;\n"
            "DROP TRIGGER IF EXISTS source_checkpoints_no_update;\n"
            "DROP TRIGGER IF EXISTS source_checkpoints_no_delete;\n"
            + _SCHEMA
        )
        connection.executescript(
            "CREATE TRIGGER IF NOT EXISTS source_checkpoints_legacy_v1_no_update "
            "BEFORE UPDATE ON source_checkpoints_legacy_v1 "
            "BEGIN SELECT RAISE(ABORT, 'legacy source checkpoints are append-only'); END;\n"
            "CREATE TRIGGER IF NOT EXISTS source_checkpoints_legacy_v1_no_delete "
            "BEFORE DELETE ON source_checkpoints_legacy_v1 "
            "BEGIN SELECT RAISE(ABORT, 'legacy source checkpoints are append-only'); END;"
        )
        legacy = _verify_or_record_legacy_checkpoint_authority(
            connection, recorded_at_ms=recorded_at_ms
        )
        connection.commit()
        return legacy

    connection.executescript(_SCHEMA)
    if _table_columns(connection, "source_checkpoints") != v2_columns:
        raise AShareForwardObservationError("source checkpoint v2 schema was not created")
    if _table_columns(connection, "source_checkpoints_legacy_v1"):
        connection.executescript(
            "CREATE TRIGGER IF NOT EXISTS source_checkpoints_legacy_v1_no_update "
            "BEFORE UPDATE ON source_checkpoints_legacy_v1 "
            "BEGIN SELECT RAISE(ABORT, 'legacy source checkpoints are append-only'); END;\n"
            "CREATE TRIGGER IF NOT EXISTS source_checkpoints_legacy_v1_no_delete "
            "BEFORE DELETE ON source_checkpoints_legacy_v1 "
            "BEGIN SELECT RAISE(ABORT, 'legacy source checkpoints are append-only'); END;"
        )
    legacy = _verify_or_record_legacy_checkpoint_authority(
        connection, recorded_at_ms=recorded_at_ms
    )
    connection.commit()
    return legacy


def _authority_payload(
    freeze: Mapping[str, Any],
    *,
    freeze_sha256: str,
    recorded_at_ms: int,
) -> dict[str, Any]:
    source_prefix = freeze["source_prefix"]
    return {
        "schema_version": 1,
        "freeze_sha256": freeze_sha256,
        "candidate_set_hash": str(freeze["candidate_set_hash"]),
        "freeze_cutoff": int(freeze["freeze_cutoff"]),
        "source_prefix_sha256": str(source_prefix["sha256"]),
        "source_prefix_rows": int(source_prefix["rows"]),
        "source_database_sha256_at_freeze": str(
            freeze["source_database_sha256_at_freeze"]
        ),
        "search_artifact_sha256": str(freeze["search_artifact_sha256"]),
        "recorded_at_ms": recorded_at_ms,
    }


def _verify_or_initialize_authority(
    connection: sqlite3.Connection,
    freeze: Mapping[str, Any],
    *,
    freeze_sha256: str,
    recorded_at_ms: int,
) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT * FROM ledger_authority ORDER BY singleton"
    ).fetchall()
    if len(rows) > 1:
        raise AShareForwardObservationError("forward ledger has multiple authorities")
    if not rows:
        legacy_records = sum(
            int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("decisions", "outcomes", "source_checkpoints")
        )
        if legacy_records:
            raise AShareForwardObservationError(
                "non-empty forward ledger lacks immutable freeze authority"
            )
        payload = _authority_payload(
            freeze,
            freeze_sha256=freeze_sha256,
            recorded_at_ms=recorded_at_ms,
        )
        record_hash = _record_hash(payload, _ZERO_HASH)
        connection.execute(
            "INSERT INTO ledger_authority VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                *(payload[field] for field in _AUTHORITY_FIELDS),
                _ZERO_HASH,
                record_hash,
            ),
        )
        return {**payload, "previous_hash": _ZERO_HASH, "record_hash": record_hash}

    row = rows[0]
    if int(row["singleton"]) != 1:
        raise AShareForwardObservationError("forward ledger authority identity is invalid")
    payload = _payload_from_row(row, _AUTHORITY_FIELDS)
    if row["previous_hash"] != _ZERO_HASH or row["record_hash"] != _record_hash(
        payload, _ZERO_HASH
    ):
        raise AShareForwardObservationError("forward ledger authority hash is invalid")
    expected = _authority_payload(
        freeze,
        freeze_sha256=freeze_sha256,
        recorded_at_ms=int(row["recorded_at_ms"]),
    )
    if payload != expected:
        raise AShareForwardObservationError(
            "forward freeze does not match immutable ledger authority"
        )
    return {**payload, "previous_hash": row["previous_hash"], "record_hash": row["record_hash"]}


def _append_source_checkpoint(
    connection: sqlite3.Connection,
    binding: Mapping[str, Any],
    *,
    source_sha256: str,
    observation_status: str,
    decision_deadline_ms: int,
    recorded_at_ms: int,
) -> bool:
    row = connection.execute(
        "SELECT * FROM source_checkpoints ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    if row is not None and int(row["through_date"]) >= int(binding["cutoff"]):
        if (
            int(row["through_date"]) == int(binding["cutoff"])
            and row["prefix_algorithm"] == binding["algorithm"]
            and row["prefix_sha256"] == binding["sha256"]
            and int(row["prefix_rows"]) == int(binding["rows"])
        ):
            return False
        raise AShareForwardObservationError(
            "A-share source is not an append-only extension of its checkpoint"
        )
    sequence, previous_hash = _next_chain(connection, "source_checkpoints")
    payload = {
        "sequence": sequence,
        "through_date": int(binding["cutoff"]),
        "prefix_algorithm": str(binding["algorithm"]),
        "prefix_sha256": str(binding["sha256"]),
        "prefix_rows": int(binding["rows"]),
        "source_database_sha256": source_sha256,
        "observation_status": observation_status,
        "decision_deadline_ms": decision_deadline_ms,
        "recorded_at_ms": recorded_at_ms,
    }
    record_hash = _record_hash(payload, previous_hash)
    connection.execute(
        "INSERT INTO source_checkpoints VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            *(payload[field] for field in _CHECKPOINT_FIELDS),
            previous_hash,
            record_hash,
        ),
    )
    return True


def _next_chain(
    connection: sqlite3.Connection,
    table: str,
) -> tuple[int, str]:
    row = connection.execute(
        f"SELECT sequence,record_hash FROM {table} ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    return (1, _ZERO_HASH) if row is None else (int(row[0]) + 1, str(row[1]))


def _append_decision(
    connection: sqlite3.Connection,
    *,
    spec: CandidateSpec,
    signal_date: int,
    selected: Mapping[str, float],
    source_sha256: str,
    recorded_at_ms: int,
) -> bool:
    existing = connection.execute(
        "SELECT factor_spec_json,selected_json,source_sha256 FROM decisions "
        "WHERE candidate_id=? AND signal_date=?",
        (spec.name, signal_date),
    ).fetchone()
    factor_json = _canonical(
        {
            "name": spec.name,
            "family": spec.family,
            "window": spec.window,
            "skip": spec.skip,
            "hold_days": spec.hold_days,
        }
    )
    selected_json = _canonical(dict(sorted(selected.items())))
    if existing is not None:
        if tuple(existing) != (factor_json, selected_json, source_sha256):
            raise AShareForwardObservationError("forward decision replay conflicts")
        return False
    sequence, previous_hash = _next_chain(connection, "decisions")
    payload = {
        "sequence": sequence,
        "candidate_id": spec.name,
        "signal_date": signal_date,
        "hold_days": spec.hold_days,
        "factor_spec_json": factor_json,
        "selected_json": selected_json,
        "source_sha256": source_sha256,
        "recorded_at_ms": recorded_at_ms,
    }
    connection.execute(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?)",
        (*payload.values(), previous_hash, _record_hash(payload, previous_hash)),
    )
    return True


def _append_outcome(
    connection: sqlite3.Connection,
    *,
    candidate_id: str,
    signal_date: int,
    entry_date: int,
    exit_date: int,
    gross_return: float,
    net_return: float,
    turnover: float,
    end_weights: Mapping[str, float],
    unpriced_legs: int,
    source_sha256: str,
    recorded_at_ms: int,
) -> bool:
    existing = connection.execute(
        "SELECT 1 FROM outcomes WHERE candidate_id=? AND signal_date=?",
        (candidate_id, signal_date),
    ).fetchone()
    if existing is not None:
        return False
    sequence, previous_hash = _next_chain(connection, "outcomes")
    payload = {
        "sequence": sequence,
        "candidate_id": candidate_id,
        "signal_date": signal_date,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "gross_return": float(gross_return),
        "net_return": float(net_return),
        "turnover": float(turnover),
        "end_weights_json": _canonical(dict(sorted(end_weights.items()))),
        "unpriced_legs": int(unpriced_legs),
        "source_sha256": source_sha256,
        "recorded_at_ms": recorded_at_ms,
    }
    connection.execute(
        "INSERT INTO outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (*payload.values(), previous_hash, _record_hash(payload, previous_hash)),
    )
    return True


def _prior_end_weights(
    connection: sqlite3.Connection,
    candidate_id: str,
    signal_date: int,
) -> dict[str, float]:
    row = connection.execute(
        "SELECT end_weights_json FROM outcomes WHERE candidate_id=? "
        "AND signal_date<? ORDER BY signal_date DESC LIMIT 1",
        (candidate_id, signal_date),
    ).fetchone()
    if row is None:
        return {}
    value = json.loads(row[0])
    if not isinstance(value, dict):
        raise AShareForwardObservationError("prior ending weights are malformed")
    return {str(key): float(weight) for key, weight in value.items()}


def _realize(
    connection: sqlite3.Connection,
    panel,
    *,
    source_sha256: str,
    recorded_at_ms: int,
) -> int:
    date_index = {int(value): index for index, value in enumerate(panel.dates)}
    instrument_index = {name: index for index, name in enumerate(panel.instruments)}
    rows = connection.execute(
        "SELECT d.* FROM decisions d LEFT JOIN outcomes o "
        "ON o.candidate_id=d.candidate_id AND o.signal_date=d.signal_date "
        "WHERE o.sequence IS NULL ORDER BY d.signal_date,d.candidate_id"
    ).fetchall()
    inserted = 0
    for row in rows:
        signal_row = date_index.get(int(row["signal_date"]))
        hold_days = int(row["hold_days"])
        if signal_row is None or signal_row + hold_days + 1 >= len(panel.dates):
            continue
        entry_row = signal_row + 1
        exit_row = signal_row + hold_days + 1
        target_raw = json.loads(row["selected_json"])
        if not isinstance(target_raw, dict):
            raise AShareForwardObservationError("stored target is malformed")
        target = {str(name): float(weight) for name, weight in target_raw.items()}
        previous = _prior_end_weights(
            connection, str(row["candidate_id"]), int(row["signal_date"])
        )
        turnover = sum(
            abs(target.get(name, 0.0) - previous.get(name, 0.0))
            for name in set(target) | set(previous)
        )
        returns: dict[str, float] = {}
        unpriced = 0
        for name in target:
            column = instrument_index.get(name)
            if column is None:
                returns[name] = 0.0
                unpriced += 1
                continue
            entry = panel.opens[entry_row, column]
            exit_price = panel.opens[exit_row, column]
            crosses_bad = bool(
                np.any(panel.bad_bars[entry_row : exit_row + 1, column])
            )
            if (
                not crosses_bad
                and np.isfinite(entry)
                and entry > 0
                and np.isfinite(exit_price)
                and exit_price > 0
            ):
                returns[name] = float(exit_price / entry - 1.0)
            else:
                returns[name] = 0.0
                unpriced += 1
        gross_return = sum(target[name] * returns[name] for name in target)
        gross_equity = 1.0 + gross_return
        end_weights = (
            {
                name: target[name] * (1.0 + returns[name]) / gross_equity
                for name in target
            }
            if gross_equity > 0
            else {}
        )
        net_return = gross_return - turnover * COST_BPS / 10_000.0
        inserted += int(
            _append_outcome(
                connection,
                candidate_id=str(row["candidate_id"]),
                signal_date=int(row["signal_date"]),
                entry_date=int(panel.dates[entry_row]),
                exit_date=int(panel.dates[exit_row]),
                gross_return=gross_return,
                net_return=net_return,
                turnover=turnover,
                end_weights=end_weights,
                unpriced_legs=unpriced,
                source_sha256=source_sha256,
                recorded_at_ms=recorded_at_ms,
            )
        )
    return inserted


def _candidate_metrics(connection: sqlite3.Connection, candidates: Sequence[CandidateSpec]) -> dict:
    result: dict[str, Any] = {}
    for candidate in candidates:
        rows = connection.execute(
            "SELECT signal_date,entry_date,exit_date,net_return,turnover,unpriced_legs "
            "FROM outcomes WHERE candidate_id=? ORDER BY signal_date",
            (candidate.name,),
        ).fetchall()
        returns = [float(row["net_return"]) for row in rows]
        metric = _metric(returns, 252.0 / candidate.hold_days)
        result[candidate.name] = {
            "candidate": {
                "name": candidate.name,
                "family": candidate.family,
                "window": candidate.window,
                "skip": candidate.skip,
                "hold_days": candidate.hold_days,
            },
            "realized": metric,
            "average_turnover": (
                sum(float(row["turnover"]) for row in rows) / len(rows)
                if rows
                else None
            ),
            "unpriced_legs": sum(int(row["unpriced_legs"]) for row in rows),
            "periods": [dict(row) for row in rows],
        }
    return result


def _build_attestation(
    report: Mapping[str, Any],
    *,
    database_sha256: str,
    panel_cache: Path,
    freeze_path: Path,
    ledger_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    release_commit = os.environ.get("QUANTOS_RELEASE_COMMIT", "").strip().lower()
    complete = bool(
        len(release_commit) == 40
        and all(character in "0123456789abcdef" for character in release_commit)
        and report.get("evidence_status") == TECHNICAL_STATUS
        and report.get("promotion_allowed") is False
        and report.get("paper_persistence_allowed") is False
        and report.get("execution_allowed") is False
        and report.get("chain_valid") is True
    )
    return {
        "schema_version": 1,
        "module": "ashare_forward_observation",
        "release_commit": release_commit,
        "complete": complete,
        "input_hashes": {
            "ashare_pit_database": database_sha256,
            "ashare_bound_panel": _file_hash(panel_cache),
            "ashare_forward_freeze": _file_hash(freeze_path),
        },
        "output_hashes": {
            "ashare_forward_ledger": _file_hash(ledger_path),
            "ashare_forward_report": _file_hash(output_path),
        },
        "safety": dict(_SAFETY),
    }


def release_run_attestation() -> dict[str, Any] | None:
    return dict(_LAST_RELEASE_RUN_ATTESTATION) if _LAST_RELEASE_RUN_ATTESTATION else None


def _busy_report() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "artifact_kind": "ASHARE_RUNTIME_SINGLE_FLIGHT_V1",
        "evidence_status": TECHNICAL_STATUS,
        "status": "SAFE_BLOCKED_RUNTIME_BUSY",
        "module": "ashare_forward_observation",
        "retry_allowed": True,
        "state_mutated": False,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "execution_allowed": False,
        "safety": {**_SAFETY, "legacy_v1_used": False},
    }


def _busy_attestation(report: Mapping[str, Any]) -> dict[str, Any]:
    release_commit = os.environ.get("QUANTOS_RELEASE_COMMIT", "").strip().lower()
    return {
        "schema_version": 1,
        "module": "ashare_forward_observation",
        "release_commit": release_commit,
        "complete": bool(
            len(release_commit) == 40
            and all(character in "0123456789abcdef" for character in release_commit)
        ),
        "input_hashes": {
            "single_flight_contract": stable_hash(
                {"lock": "data/cache/ashare_alpha_runtime.lock", "blocking": False}
            )
        },
        "output_hashes": {"safe_blocked_decision": stable_hash(dict(report))},
        "safety": dict(_SAFETY),
    }


def _run_cycle_once(
    *,
    database: str | Path = DEFAULT_DATABASE,
    panel_cache: str | Path = DEFAULT_PANEL_CACHE,
    freeze_path: str | Path = DEFAULT_FREEZE,
    ledger_path: str | Path = DEFAULT_LEDGER,
    output_path: str | Path = DEFAULT_OUTPUT,
    recorded_at_ms: int | None = None,
    source_observed_at_ms: int | None = None,
) -> dict[str, Any]:
    global _LAST_RELEASE_RUN_ATTESTATION

    database = Path(database)
    panel_cache = Path(panel_cache)
    freeze_path = Path(freeze_path)
    ledger_path = Path(ledger_path)
    output_path = Path(output_path)
    if not database.is_file() or not panel_cache.is_file() or not freeze_path.is_file():
        raise AShareForwardObservationError("A-share forward prerequisites are missing")
    try:
        finality = _source_session_finality(
            _database_latest_trade_date(database),
            observed_at_ms=source_observed_at_ms,
        )
    except AShareSearchWorkerError as exc:
        raise AShareForwardObservationError(
            "A-share source finality cannot be verified"
        ) from exc
    if not finality["is_final"]:
        blocked = _source_not_final_report("ashare_forward_observation", finality)
        _LAST_RELEASE_RUN_ATTESTATION = _source_not_final_attestation(
            "ashare_forward_observation", blocked
        )
        return blocked
    freeze, candidates, freeze_sha256 = _load_freeze(freeze_path)
    cache_status = _ensure_bound_panel_cache(
        database,
        panel_cache,
        min_adv_cny=MIN_ADV_CNY,
    )
    source_signature = _source_signature(database)
    cached = _load_panel_cache(database, panel_cache, min_adv_cny=MIN_ADV_CNY)
    if cached is None:
        raise AShareForwardObservationError("bound A-share panel cache is missing")
    if source_signature != _source_signature(database):
        raise AShareForwardObservationError(
            "A-share PIT database changed during panel-cache load"
        )
    panel, panel_metadata = cached
    cutoff = int(freeze["freeze_cutoff"])
    date_values = [int(value) for value in panel.dates]
    if cutoff not in date_values:
        raise AShareForwardObservationError("freeze cutoff is absent from current source")
    cutoff_row = date_values.index(cutoff)
    latest_row = len(date_values) - 1
    latest_date = date_values[-1]
    try:
        finality = _source_session_finality(
            latest_date,
            observed_at_ms=source_observed_at_ms,
        )
    except AShareSearchWorkerError as exc:
        raise AShareForwardObservationError(
            "A-share source finality cannot be verified"
        ) from exc
    if not finality["is_final"]:
        blocked = _source_not_final_report("ashare_forward_observation", finality)
        _LAST_RELEASE_RUN_ATTESTATION = _source_not_final_attestation(
            "ashare_forward_observation", blocked
        )
        return blocked
    source_sha256 = str(panel_metadata.get("sha256") or "")
    if len(source_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in source_sha256
    ):
        raise AShareForwardObservationError("bound panel source digest is invalid")
    if source_sha256 != _file_hash(database):
        raise AShareForwardObservationError(
            "bound panel source digest does not match the current database"
        )
    if freeze_sha256 != _file_hash(freeze_path):
        raise AShareForwardObservationError("forward freeze changed during load")
    now_ms = int(recorded_at_ms if recorded_at_ms is not None else time.time() * 1000)

    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(ledger_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        legacy_checkpoint_authority = _ensure_ledger_schema(
            connection,
            recorded_at_ms=now_ms,
        )
        connection.execute("BEGIN IMMEDIATE")
        _verify_chain(connection, "decisions", _DECISION_FIELDS)
        _verify_chain(connection, "outcomes", _OUTCOME_FIELDS)
        checkpoint_count, checkpoint_head = _verify_chain(
            connection, "source_checkpoints", _CHECKPOINT_FIELDS
        )
        last_checkpoint = connection.execute(
            "SELECT * FROM source_checkpoints ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        last_checkpoint_date = (
            int(last_checkpoint["through_date"])
            if last_checkpoint is not None
            else None
        )
        if last_checkpoint_date is not None:
            if last_checkpoint_date < cutoff:
                raise AShareForwardObservationError(
                    "A-share source checkpoint predates the immutable freeze"
                )
            if latest_date < last_checkpoint_date:
                raise AShareForwardObservationError(
                    "A-share source terminal date moved behind its checkpoint"
                )
        authority = _verify_or_initialize_authority(
            connection,
            freeze,
            freeze_sha256=freeze_sha256,
            recorded_at_ms=now_ms,
        )
        requested_cutoffs = {latest_date}
        requested_cutoffs.add(
            cutoff if last_checkpoint_date is None else last_checkpoint_date
        )
        if legacy_checkpoint_authority is not None:
            requested_cutoffs.add(
                int(legacy_checkpoint_authority["legacy_last_through_date"])
            )
        try:
            source_bindings = _source_prefix_bindings(database, requested_cutoffs)
        except AShareSearchWorkerError as exc:
            raise AShareForwardObservationError(
                "A-share source prefix cannot be verified"
            ) from exc
        if (
            last_checkpoint_date is None
            and source_bindings[cutoff] != freeze["source_prefix"]
        ):
            raise AShareForwardObservationError(
                "A-share source revised the immutable freeze prefix"
            )
        if last_checkpoint is not None:
            last_binding = source_bindings[last_checkpoint_date]
            if (
                last_checkpoint["prefix_algorithm"] != last_binding["algorithm"]
                or last_checkpoint["prefix_sha256"] != last_binding["sha256"]
                or int(last_checkpoint["prefix_rows"]) != int(last_binding["rows"])
            ):
                raise AShareForwardObservationError(
                    "A-share source revised an observed forward prefix"
                )
        if legacy_checkpoint_authority is not None:
            legacy_date = int(
                legacy_checkpoint_authority["legacy_last_through_date"]
            )
            legacy_binding = source_bindings[legacy_date]
            if (
                legacy_checkpoint_authority["legacy_last_prefix_algorithm"]
                != legacy_binding["algorithm"]
                or legacy_checkpoint_authority["legacy_last_prefix_sha256"]
                != legacy_binding["sha256"]
                or int(legacy_checkpoint_authority["legacy_last_prefix_rows"])
                != int(legacy_binding["rows"])
            ):
                raise AShareForwardObservationError(
                    "A-share source revised the preserved legacy checkpoint prefix"
                )
        new_source_checkpoints = 0
        if last_checkpoint is None:
            new_source_checkpoints += int(
                _append_source_checkpoint(
                    connection,
                    source_bindings[cutoff],
                    source_sha256=source_sha256,
                    observation_status=_FREEZE_ANCHOR,
                    decision_deadline_ms=0,
                    recorded_at_ms=now_ms,
                )
            )
        close_ms, decision_deadline_ms = _decision_window_ms(latest_date)
        if now_ms < close_ms:
            latest_observation_status = _SOURCE_NOT_FINAL
        elif now_ms >= decision_deadline_ms:
            latest_observation_status = _MISSED_DEADLINE
        else:
            latest_observation_status = _TIMELY_OBSERVATION
        new_source_checkpoints += int(
            _append_source_checkpoint(
                connection,
                source_bindings[latest_date],
                source_sha256=source_sha256,
                observation_status=(
                    _FREEZE_ANCHOR
                    if latest_date == cutoff
                    else latest_observation_status
                ),
                decision_deadline_ms=(
                    0 if latest_date == cutoff else decision_deadline_ms
                ),
                recorded_at_ms=now_ms,
            )
        )
        latest_checkpoint = connection.execute(
            "SELECT observation_status,decision_deadline_ms FROM source_checkpoints "
            "WHERE through_date=?",
            (latest_date,),
        ).fetchone()
        if latest_checkpoint is None:
            raise AShareForwardObservationError("latest source checkpoint is missing")
        latest_observation_status = str(latest_checkpoint["observation_status"])
        checkpoint_count, checkpoint_head = _verify_chain(
            connection, "source_checkpoints", _CHECKPOINT_FIELDS
        )
        new_outcomes = _realize(
            connection,
            panel,
            source_sha256=source_sha256,
            recorded_at_ms=now_ms,
        )

        factors: dict[tuple[str, int, int], np.ndarray] = {}
        new_decisions = 0
        skipped: dict[str, str] = {}
        post_freeze_ordinal = latest_row - cutoff_row - 1
        if post_freeze_ordinal >= 0 and latest_observation_status == _TIMELY_OBSERVATION:
            for candidate in candidates:
                if post_freeze_ordinal % candidate.hold_days != 0:
                    skipped[candidate.name] = "NOT_DUE_ON_LATEST_SESSION"
                    continue
                existing = connection.execute(
                    "SELECT 1 FROM decisions WHERE candidate_id=? AND signal_date=?",
                    (candidate.name, latest_date),
                ).fetchone()
                if existing is not None:
                    skipped[candidate.name] = "IDEMPOTENT_REPLAY"
                    continue
                factor_key = candidate.factor_key
                if factor_key not in factors:
                    factors[factor_key] = build_factor_matrix(
                        candidate,
                        panel.closes,
                        panel.volumes,
                        panel.amounts,
                        panel.opens,
                    )
                scores = factors[factor_key]
                weights, selected_indices = _target_weights(
                    scores[latest_row],
                    panel.base_eligible[latest_row] & np.isfinite(scores[latest_row]),
                    20,
                )
                selected = {
                    panel.instruments[index]: float(weights[index])
                    for index in selected_indices
                }
                if not selected:
                    # Persist the due decision as an explicit all-cash target.
                    # Otherwise a later reader cannot distinguish a genuine
                    # pre-outcome no-position decision from a missed worker run.
                    skipped[candidate.name] = "NO_ELIGIBLE_TARGET_RECORDED_AS_CASH"
                new_decisions += int(
                    _append_decision(
                        connection,
                        spec=candidate,
                        signal_date=latest_date,
                        selected=selected,
                        source_sha256=source_sha256,
                        recorded_at_ms=now_ms,
                    )
                )
        elif post_freeze_ordinal < 0:
            skipped = {candidate.name: "AWAITING_POST_FREEZE_SESSION" for candidate in candidates}
        else:
            skipped = {
                candidate.name: latest_observation_status for candidate in candidates
            }

        decision_count, decision_head = _verify_chain(
            connection, "decisions", _DECISION_FIELDS
        )
        outcome_count, outcome_head = _verify_chain(
            connection, "outcomes", _OUTCOME_FIELDS
        )
        pending_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM decisions d LEFT JOIN outcomes o "
                "ON o.candidate_id=d.candidate_id AND o.signal_date=d.signal_date "
                "WHERE o.sequence IS NULL"
            ).fetchone()[0]
        )
        checkpoint_rows = [
            (int(row[0]), str(row[1]), int(row[2]), int(row[3]))
            for row in connection.execute(
                "SELECT through_date,observation_status,decision_deadline_ms,recorded_at_ms "
                "FROM source_checkpoints WHERE through_date>? ORDER BY through_date",
                (cutoff,),
            )
        ]
        checkpoint_dates = [
            through_date
            for through_date, status, _deadline, _recorded_at in checkpoint_rows
            if status == _TIMELY_OBSERVATION
        ]
        observed_sessions = len(checkpoint_dates)
        calendar_sessions_since_freeze = max(0, latest_row - cutoff_row)
        missed_observation_sessions = max(
            0, calendar_sessions_since_freeze - observed_sessions
        )
        due_decisions_expected = 0
        due_decisions_recorded = 0
        checkpoint_status_by_date = {
            through_date: status
            for through_date, status, _deadline, _recorded_at in checkpoint_rows
        }
        required_decision_gaps: list[dict[str, Any]] = []
        for candidate in candidates:
            for checkpoint_row in range(cutoff_row + 1, latest_row + 1):
                session_date = date_values[checkpoint_row]
                ordinal = checkpoint_row - cutoff_row - 1
                if ordinal % candidate.hold_days != 0:
                    continue
                due_decisions_expected += 1
                timely = (
                    checkpoint_status_by_date.get(session_date)
                    == _TIMELY_OBSERVATION
                )
                recorded = connection.execute(
                    "SELECT 1 FROM decisions WHERE candidate_id=? AND signal_date=?",
                    (candidate.name, session_date),
                ).fetchone() is not None
                if timely and recorded:
                    due_decisions_recorded += 1
                else:
                    required_decision_gaps.append(
                        {
                            "candidate_id": candidate.name,
                            "session_date": session_date,
                            "status": checkpoint_status_by_date.get(
                                session_date, "NO_PRE_OUTCOME_CHECKPOINT"
                            ),
                        }
                    )
        due_decision_coverage_complete = (
            due_decisions_recorded == due_decisions_expected
            and not required_decision_gaps
        )
        metrics = _candidate_metrics(connection, candidates)
        if source_signature != _source_signature(database):
            raise AShareForwardObservationError(
                "A-share PIT database changed during forward observation"
            )
        if freeze_sha256 != _file_hash(freeze_path):
            raise AShareForwardObservationError(
                "forward freeze changed during observation"
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

    minimum_sessions = int(freeze["minimum_sessions"])
    report = {
        "schema_version": 1,
        "artifact_kind": "ASHARE_UNTOUCHED_FORWARD_OBSERVATION_V1",
        "evidence_status": TECHNICAL_STATUS,
        "status": (
            "ARMED_AWAITING_NEW_SESSIONS"
            if observed_sessions == 0
            else "OBSERVING"
        ),
        "generated_at_ms": now_ms,
        "freeze_cutoff": cutoff,
        "latest_source_date": latest_date,
        "sessions_observed": observed_sessions,
        "calendar_sessions_since_freeze": calendar_sessions_since_freeze,
        "missed_observation_sessions": missed_observation_sessions,
        "observation_gaps_detected": missed_observation_sessions > 0,
        "observation_gaps": [
            {
                "session_date": through_date,
                "status": status,
                "decision_deadline_ms": deadline,
                "recorded_at_ms": observed_at,
            }
            for through_date, status, deadline, observed_at in checkpoint_rows
            if status != _TIMELY_OBSERVATION
        ]
        + [
            {
                "session_date": session_date,
                "status": "NO_PRE_OUTCOME_CHECKPOINT",
                "decision_deadline_ms": None,
                "recorded_at_ms": None,
            }
            for session_date in date_values[cutoff_row + 1 :]
            if session_date not in checkpoint_status_by_date
        ],
        "minimum_sessions": minimum_sessions,
        "minimum_sessions_met": (
            observed_sessions >= minimum_sessions
            and due_decision_coverage_complete
            and missed_observation_sessions == 0
        ),
        "selection_locked": True,
        "promotion_allowed": False,
        "paper_persistence_allowed": False,
        "execution_allowed": False,
        "source": {
            **panel_metadata,
            "sha256": source_sha256,
            "panel_cache_sha256": _file_hash(panel_cache),
            "panel_cache_status": cache_status,
        },
        "cycle": {
            "new_decisions": new_decisions,
            "new_outcomes": new_outcomes,
            "decision_count": decision_count,
            "outcome_count": outcome_count,
            "pending_count": pending_count,
            "skipped": skipped,
        },
        "ledger": {
            "path": str(ledger_path),
            "append_only": True,
            "chain_valid": True,
            "freeze_sha256": freeze_sha256,
            "authority_hash": authority["record_hash"],
            "source_checkpoint_count": checkpoint_count,
            "source_checkpoint_head": checkpoint_head,
            "source_checkpoint_added": new_source_checkpoints > 0,
            "new_source_checkpoints": new_source_checkpoints,
            "checkpoint_schema_version": 2,
            "legacy_checkpoint_migration": (
                {
                    "status": legacy_checkpoint_authority["migration_status"],
                    "row_count": legacy_checkpoint_authority["legacy_row_count"],
                    "head_hash": legacy_checkpoint_authority["legacy_head_hash"],
                    "last_through_date": legacy_checkpoint_authority[
                        "legacy_last_through_date"
                    ],
                }
                if legacy_checkpoint_authority is not None
                else None
            ),
            "decision_head": decision_head,
            "outcome_head": outcome_head,
        },
        "chain_valid": True,
        "candidates": metrics,
        "research_contract": {
            "signal_time": "close_t",
            "entry_time": "open_t_plus_1",
            "exit_time": "open_t_plus_hold_plus_1",
            "decisions_recorded_before_outcome": True,
            "historical_backfill_allowed": False,
            "source_history_rule": "APPEND_ONLY_FROM_FROZEN_PREFIX",
            "observation_clock": "DISTINCT_PRE_OUTCOME_SOURCE_CHECKPOINTS_AFTER_FREEZE",
            "due_decisions_expected": due_decisions_expected,
            "due_decisions_recorded": due_decisions_recorded,
            "due_decision_coverage_complete": due_decision_coverage_complete,
            "required_decision_gaps": required_decision_gaps,
            "transaction_cost_bps_per_turnover_unit": COST_BPS,
            "missing_or_bad_execution_leg": "FROZEN_AT_ZERO_RETURN_NOT_REPLACED",
        },
        "safety": {
            **_SAFETY,
            "legacy_v1_used": False,
            "account_state_accessed": False,
        },
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output_path, report, indent=2)
    _LAST_RELEASE_RUN_ATTESTATION = _build_attestation(
        report,
        database_sha256=source_sha256,
        panel_cache=panel_cache,
        freeze_path=freeze_path,
        ledger_path=ledger_path,
        output_path=output_path,
    )
    return report


def run_cycle(
    *,
    database: str | Path = DEFAULT_DATABASE,
    panel_cache: str | Path = DEFAULT_PANEL_CACHE,
    freeze_path: str | Path = DEFAULT_FREEZE,
    ledger_path: str | Path = DEFAULT_LEDGER,
    output_path: str | Path = DEFAULT_OUTPUT,
    recorded_at_ms: int | None = None,
    lock_path: str | Path = DEFAULT_RUNTIME_LOCK,
    source_observed_at_ms: int | None = None,
) -> dict[str, Any]:
    global _LAST_RELEASE_RUN_ATTESTATION

    with acquire_runtime_lock(lock_path) as acquired:
        if not acquired:
            report = _busy_report()
            _LAST_RELEASE_RUN_ATTESTATION = _busy_attestation(report)
            return report
        return _run_cycle_once(
            database=database,
            panel_cache=panel_cache,
            freeze_path=freeze_path,
            ledger_path=ledger_path,
            output_path=output_path,
            recorded_at_ms=recorded_at_ms,
            source_observed_at_ms=source_observed_at_ms,
        )


def main() -> int:
    try:
        report = run_cycle()
    except Exception as exc:
        print(f"A-share forward observation failed: {type(exc).__name__}: {exc}")
        return SCHEDULE_EXIT_FAILURE
    if str(report.get("status", "")).startswith("SAFE_BLOCKED_"):
        reason = str(report["status"]).removeprefix("SAFE_BLOCKED_")
        print(f"ASHARE_FORWARD_OBSERVATION_SAFE_BLOCKED reason={reason}")
        return SCHEDULE_EXIT_SAFE_BLOCKED
    cycle = report["cycle"]
    print(
        "ASHARE_FORWARD_OBSERVATION_PASS "
        f"status={report['status']} sessions={report['sessions_observed']} "
        f"decisions={cycle['new_decisions']} outcomes={cycle['new_outcomes']} "
        f"pending={cycle['pending_count']}"
    )
    return (
        SCHEDULE_EXIT_SUCCESS
        if report["status"] == "OBSERVING"
        else SCHEDULE_EXIT_SAFE_BLOCKED
    )


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_DATABASE",
    "DEFAULT_FREEZE",
    "DEFAULT_LEDGER",
    "DEFAULT_OUTPUT",
    "DEFAULT_PANEL_CACHE",
    "release_run_attestation",
    "run_cycle",
]
