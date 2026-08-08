"""Append-only read model for the read-only MFT execution console.

The store is a display authority only.  It carries replacement events for a
small, frozen set of console sections and deliberately exposes no execution or
approval command surface.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

from quantos.contracts.hashing import stable_hash


MFT_CONSOLE_SCHEMA = "quantos.mft-execution-console.v1"
MFT_READ_MODEL_SCHEMA = "quantos.mft-read-model-store.v1"
MFT_READ_MODEL_SECTIONS = (
    "market_gap",
    "scheduler",
    "intent_funnel",
    "oms",
    "fills",
    "positions",
    "risk",
    "reconciliation",
    "tca",
    "mandate_expiry",
)

_GENESIS_HASH = "0" * 64
_CURSOR = re.compile(r"^(0|[1-9][0-9]*):([0-9a-f]{64})$")
_EVENT_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_LOCAL_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/|file://)", re.IGNORECASE)
_SENSITIVE_KEYS = {
    "api_key",
    "api_secret",
    "credential",
    "credentials",
    "database_path",
    "db_path",
    "file_path",
    "password",
    "path",
    "private_key",
    "secret",
}

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS mft_read_model_meta (
  singleton       INTEGER PRIMARY KEY CHECK(singleton = 1),
  schema_version  TEXT NOT NULL
);
INSERT OR IGNORE INTO mft_read_model_meta(singleton, schema_version)
VALUES (1, '{MFT_READ_MODEL_SCHEMA}');
CREATE TABLE IF NOT EXISTS mft_control_events (
  sequence       INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id       TEXT NOT NULL UNIQUE,
  section        TEXT NOT NULL,
  event_at_ms    INTEGER NOT NULL CHECK(event_at_ms >= 0),
  payload_json   TEXT NOT NULL,
  payload_hash   TEXT NOT NULL,
  prev_hash      TEXT NOT NULL,
  event_hash     TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS mft_control_events_no_update
BEFORE UPDATE ON mft_control_events
BEGIN SELECT RAISE(ABORT, 'mft_control_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS mft_control_events_no_delete
BEFORE DELETE ON mft_control_events
BEGIN SELECT RAISE(ABORT, 'mft_control_events is append-only'); END;
"""


class MftReadModelError(RuntimeError):
    """Typed failure whose code is safe to surface without local details."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MftFreshnessPolicy:
    policy_id: str
    version: str
    max_age_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id must be non-empty")
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be non-empty")
        if (
            isinstance(self.max_age_ms, bool)
            or not isinstance(self.max_age_ms, int)
            or self.max_age_ms < 0
        ):
            raise ValueError("max_age_ms must be a non-negative integer")


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MftReadModelError(
            "payload is not canonical JSON", code="PAYLOAD_INVALID"
        ) from exc


def _validate_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise MftReadModelError("payload must be a mapping", code="PAYLOAD_INVALID")

    def walk(item: object) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise MftReadModelError(
                        "payload keys must be strings", code="PAYLOAD_INVALID"
                    )
                if key.strip().lower() in _SENSITIVE_KEYS:
                    raise MftReadModelError(
                        "sensitive payload fields are forbidden",
                        code="SENSITIVE_PAYLOAD",
                    )
                walk(nested)
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for nested in item:
                walk(nested)
        elif isinstance(item, (bytes, bytearray)):
            raise MftReadModelError(
                "binary payload values are forbidden", code="PAYLOAD_INVALID"
            )
        elif isinstance(item, str) and _LOCAL_PATH.match(item):
            raise MftReadModelError(
                "local paths are forbidden in console payloads",
                code="SENSITIVE_PAYLOAD",
            )

    result = dict(value)
    walk(result)
    _canonical(result)
    return result


def _validate_time(value: int, *, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _event_hash(
    *,
    sequence: int,
    event_id: str,
    section: str,
    event_at_ms: int,
    payload_hash: str,
    prev_hash: str,
) -> str:
    return stable_hash(
        {
            "sequence": sequence,
            "event_id": event_id,
            "section": section,
            "event_at_ms": event_at_ms,
            "payload_hash": payload_hash,
            "prev_hash": prev_hash,
        }
    )


def _cursor_token(sequence: int, event_hash: str) -> str:
    return f"{sequence}:{event_hash}"


def _parse_cursor(cursor: object) -> tuple[int, str] | None:
    if not isinstance(cursor, str):
        return None
    match = _CURSOR.fullmatch(cursor)
    if match is None:
        return None
    return int(match.group(1)), match.group(2)


def _safety() -> dict[str, object]:
    return {
        "read_only": True,
        "readiness": "L2_DRY_RUN_ONLY",
        "testnet_only": True,
        "live_trading_enabled": False,
        "real_broker_enabled": False,
        "allow_order_submission": False,
        "allow_ai_direct_trading": False,
        "require_human_approval": True,
        "submit_capability": False,
        "reset_capability": False,
        "approve_capability": False,
        "microstructure_scope": "TESTNET_MICROSTRUCTURE_NOT_PRODUCTION",
    }


def resnapshot_required_delta(reason: str) -> dict[str, object]:
    return {
        "schema_version": MFT_CONSOLE_SCHEMA,
        "status": "RESNAPSHOT_REQUIRED",
        "reason": str(reason),
        "refetch": "/api/v1/console/mft-execution",
        "read_only": True,
    }


def unavailable_mft_snapshot(*, as_of_ms: int, reason: str) -> dict[str, Any]:
    _validate_time(as_of_ms, name="as_of_ms")
    sections = {name: {"status": "UNAVAILABLE"} for name in MFT_READ_MODEL_SECTIONS}
    provenance = "UNAVAILABLE"
    return {
        "source": "MFT_READ_MODEL_SQLITE_AUTHORITY",
        "read_only": True,
        "api_version": "v1",
        "data_provenance": provenance,
        "snapshot_id": None,
        "snapshot_ids": [],
        "snapshot_status": "UNAVAILABLE",
        "data_quality": {
            "status": "UNAVAILABLE",
            "provenance": provenance,
            "snapshot_ids": [],
            "reason": str(reason),
        },
        "schema_version": MFT_CONSOLE_SCHEMA,
        "as_of_ms": as_of_ms,
        "status": "BLOCKED",
        "reason": str(reason),
        "freshness": {
            "status": "UNAVAILABLE",
            "age_ms": None,
            "policy_id": None,
            "policy_version": None,
            "max_age_ms": None,
        },
        "watermark": None,
        "cursor": None,
        "missing_sections": list(MFT_READ_MODEL_SECTIONS),
        **sections,
        "safety": _safety(),
    }


class MftReadModelStore:
    def __init__(
        self,
        path: str,
        *,
        create: bool = False,
        read_only: bool = False,
    ) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("path must be non-empty")
        if create and read_only:
            raise ValueError("create and read_only are mutually exclusive")
        self._read_only = bool(read_only)
        self._path = path
        if create and path != ":memory:":
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        if not create and path != ":memory:" and not Path(path).is_file():
            raise MftReadModelError("read-model store is missing", code="STORE_MISSING")
        try:
            if read_only:
                if path == ":memory:":
                    raise ValueError("an in-memory store cannot be opened read-only")
                uri = Path(path).resolve().as_uri() + "?mode=ro"
                self._conn = sqlite3.connect(uri, uri=True)
            else:
                self._conn = sqlite3.connect(path)
            self._conn.row_factory = sqlite3.Row
            if create:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
            if read_only:
                self._conn.execute("PRAGMA query_only=ON")
            self._require_schema()
        except MftReadModelError:
            if hasattr(self, "_conn"):
                self._conn.close()
            raise
        except (OSError, sqlite3.DatabaseError) as exc:
            if hasattr(self, "_conn"):
                self._conn.close()
            raise MftReadModelError(
                "read-model store is unreadable", code="STORE_UNREADABLE"
            ) from exc

    def _require_schema(self) -> None:
        try:
            tables = {
                str(row[0])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "mft_read_model_meta" not in tables:
                raise MftReadModelError(
                    "read-model schema is missing", code="STORE_SCHEMA_MISSING"
                )
            versions = [
                str(row[0])
                for row in self._conn.execute(
                    "SELECT schema_version FROM mft_read_model_meta"
                )
            ]
            if versions != [MFT_READ_MODEL_SCHEMA]:
                raise MftReadModelError(
                    "read-model schema is unknown", code="UNKNOWN_SCHEMA"
                )
            if "mft_control_events" not in tables:
                raise MftReadModelError(
                    "read-model schema is incomplete", code="STORE_SCHEMA_INCOMPLETE"
                )
            triggers = {
                str(row[0])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            required_triggers = {
                "mft_control_events_no_update",
                "mft_control_events_no_delete",
            }
            if not required_triggers.issubset(triggers):
                raise MftReadModelError(
                    "read-model append-only guards are missing",
                    code="STORE_SCHEMA_INCOMPLETE",
                )
        except MftReadModelError:
            raise
        except sqlite3.DatabaseError as exc:
            raise MftReadModelError(
                "read-model schema is unreadable", code="STORE_UNREADABLE"
            ) from exc

    def append_section(
        self,
        section: str,
        payload: Mapping[str, Any],
        *,
        event_at_ms: int,
        event_id: str,
    ) -> bool:
        if self._read_only:
            raise MftReadModelError("read-only store cannot append", code="READ_ONLY")
        if section not in MFT_READ_MODEL_SECTIONS:
            raise MftReadModelError("unknown section", code="UNKNOWN_SECTION")
        if not isinstance(event_id, str) or _EVENT_ID.fullmatch(event_id) is None:
            raise ValueError("event_id must be a safe identifier")
        _validate_time(event_at_ms, name="event_at_ms")
        clean_payload = _validate_payload(payload)
        payload_json = _canonical(clean_payload)
        payload_hash = stable_hash(clean_payload)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            existing = self._conn.execute(
                "SELECT section,event_at_ms,payload_hash FROM mft_control_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                exact = (
                    str(existing["section"]) == section
                    and int(existing["event_at_ms"]) == event_at_ms
                    and str(existing["payload_hash"]) == payload_hash
                )
                self._conn.rollback()
                if exact:
                    return False
                raise MftReadModelError(
                    "event_id reuse with different content", code="EVENT_ID_CONFLICT"
                )
            prior = self._conn.execute(
                "SELECT sequence,event_at_ms,event_hash FROM mft_control_events "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = 1 if prior is None else int(prior["sequence"]) + 1
            prev_hash = _GENESIS_HASH if prior is None else str(prior["event_hash"])
            if prior is not None and event_at_ms < int(prior["event_at_ms"]):
                raise MftReadModelError(
                    "event time must be monotonic", code="NON_MONOTONIC_EVENT_TIME"
                )
            event_hash = _event_hash(
                sequence=sequence,
                event_id=event_id,
                section=section,
                event_at_ms=event_at_ms,
                payload_hash=payload_hash,
                prev_hash=prev_hash,
            )
            self._conn.execute(
                "INSERT INTO mft_control_events"
                "(sequence,event_id,section,event_at_ms,payload_json,payload_hash,prev_hash,event_hash) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    sequence,
                    event_id,
                    section,
                    event_at_ms,
                    payload_json,
                    payload_hash,
                    prev_hash,
                    event_hash,
                ),
            )
            self._conn.commit()
            return True
        except MftReadModelError:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise MftReadModelError(
                "read-model append conflict", code="APPEND_CONFLICT"
            ) from exc
        except sqlite3.DatabaseError as exc:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise MftReadModelError(
                "read-model append failed", code="STORE_UNREADABLE"
            ) from exc

    def _verified_events(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        previous = _GENESIS_HASH
        expected_sequence = 1
        try:
            rows = self._conn.execute(
                "SELECT * FROM mft_control_events ORDER BY sequence"
            )
            for row in rows:
                sequence = int(row["sequence"])
                if sequence != expected_sequence:
                    raise MftReadModelError(
                        "read-model sequence gap", code="STORE_INTEGRITY"
                    )
                try:
                    payload = json.loads(str(row["payload_json"]))
                    payload = _validate_payload(payload)
                except (json.JSONDecodeError, MftReadModelError) as exc:
                    raise MftReadModelError(
                        "stored payload is invalid", code="STORE_INTEGRITY"
                    ) from exc
                payload_hash = stable_hash(payload)
                section = str(row["section"])
                event_id = str(row["event_id"])
                event_at_ms = int(row["event_at_ms"])
                stored_prev = str(row["prev_hash"])
                stored_hash = str(row["event_hash"])
                expected_hash = _event_hash(
                    sequence=sequence,
                    event_id=event_id,
                    section=section,
                    event_at_ms=event_at_ms,
                    payload_hash=payload_hash,
                    prev_hash=stored_prev,
                )
                if (
                    section not in MFT_READ_MODEL_SECTIONS
                    or str(row["payload_hash"]) != payload_hash
                    or stored_prev != previous
                    or stored_hash != expected_hash
                ):
                    raise MftReadModelError(
                        "read-model hash chain is invalid", code="STORE_INTEGRITY"
                    )
                result.append(
                    {
                        "sequence": sequence,
                        "event_id": event_id,
                        "section": section,
                        "event_at_ms": event_at_ms,
                        "payload": payload,
                        "payload_hash": payload_hash,
                        "prev_hash": stored_prev,
                        "event_hash": stored_hash,
                    }
                )
                previous = stored_hash
                expected_sequence += 1
            return result
        except MftReadModelError:
            raise
        except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
            raise MftReadModelError(
                "read-model events are unreadable", code="STORE_UNREADABLE"
            ) from exc

    @staticmethod
    def _snapshot_from_events(
        events: Sequence[Mapping[str, Any]],
        *,
        as_of_ms: int,
        freshness_policy: MftFreshnessPolicy,
    ) -> dict[str, Any]:
        states: dict[str, dict[str, Any]] = {}
        for event in events:
            states[str(event["section"])] = copy.deepcopy(dict(event["payload"]))
        missing = [name for name in MFT_READ_MODEL_SECTIONS if name not in states]
        latest = None if not events else events[-1]
        if latest is None:
            freshness_status = "UNAVAILABLE"
            age_ms = None
        else:
            age_ms = max(0, as_of_ms - int(latest["event_at_ms"]))
            freshness_status = (
                "CURRENT" if age_ms <= freshness_policy.max_age_ms else "STALE"
            )
        if missing:
            snapshot_status = "UNAVAILABLE"
        elif freshness_status == "STALE":
            # The real last-known state remains visible for diagnosis, but stale
            # execution state is not an available operational snapshot.
            snapshot_status = "UNAVAILABLE"
        else:
            snapshot_status = "REAL"
        available = snapshot_status == "REAL"
        provenance = "REAL_MFT_READ_MODEL"
        snapshot_id = None if latest is None else str(latest["event_hash"])
        snapshot_ids = [] if snapshot_id is None else [snapshot_id]
        sections = {
            name: states.get(name, {"status": "UNAVAILABLE"})
            for name in MFT_READ_MODEL_SECTIONS
        }
        watermark = None
        cursor = _cursor_token(0, _GENESIS_HASH)
        if latest is not None:
            watermark = {
                "sequence": int(latest["sequence"]),
                "event_at_ms": int(latest["event_at_ms"]),
                "event_hash": str(latest["event_hash"]),
            }
            cursor = _cursor_token(int(latest["sequence"]), str(latest["event_hash"]))
        return {
            "source": "MFT_READ_MODEL_SQLITE_AUTHORITY",
            "read_only": True,
            "api_version": "v1",
            "data_provenance": provenance,
            "snapshot_id": snapshot_id,
            "snapshot_ids": snapshot_ids,
            "snapshot_status": snapshot_status,
            "data_quality": {
                "status": snapshot_status,
                "provenance": provenance,
                "snapshot_ids": snapshot_ids,
            },
            "schema_version": MFT_CONSOLE_SCHEMA,
            "as_of_ms": as_of_ms,
            "status": "AVAILABLE" if available else "BLOCKED",
            "freshness": {
                "status": freshness_status,
                "age_ms": age_ms,
                "policy_id": freshness_policy.policy_id,
                "policy_version": freshness_policy.version,
                "max_age_ms": freshness_policy.max_age_ms,
            },
            "watermark": watermark,
            "cursor": cursor,
            "missing_sections": missing,
            **sections,
            "safety": _safety(),
        }

    def snapshot(
        self,
        *,
        as_of_ms: int,
        freshness_policy: MftFreshnessPolicy,
    ) -> dict[str, Any]:
        _validate_time(as_of_ms, name="as_of_ms")
        if not isinstance(freshness_policy, MftFreshnessPolicy):
            raise TypeError("freshness_policy must be MftFreshnessPolicy")
        events = [
            event
            for event in self._verified_events()
            if int(event["event_at_ms"]) <= as_of_ms
        ]
        return self._snapshot_from_events(
            events, as_of_ms=as_of_ms, freshness_policy=freshness_policy
        )

    def delta(
        self,
        cursor: str,
        *,
        as_of_ms: int,
        freshness_policy: MftFreshnessPolicy,
        limit: int = 500,
    ) -> dict[str, Any]:
        _validate_time(as_of_ms, name="as_of_ms")
        if not isinstance(freshness_policy, MftFreshnessPolicy):
            raise TypeError("freshness_policy must be MftFreshnessPolicy")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        parsed = _parse_cursor(cursor)
        if parsed is None:
            return resnapshot_required_delta("MALFORMED_CURSOR")
        base_sequence, base_hash = parsed
        events = self._verified_events()
        if base_sequence == 0:
            if base_hash != _GENESIS_HASH:
                return resnapshot_required_delta("CURSOR_MISMATCH")
        else:
            if base_sequence > len(events):
                return resnapshot_required_delta("CURSOR_GAP")
            base = events[base_sequence - 1]
            if int(base["sequence"]) != base_sequence:
                return resnapshot_required_delta("CURSOR_GAP")
            if str(base["event_hash"]) != base_hash:
                return resnapshot_required_delta("CURSOR_MISMATCH")
            if int(base["event_at_ms"]) > as_of_ms:
                return resnapshot_required_delta("CURSOR_AHEAD_OF_AS_OF")
        pending = [
            event
            for event in events
            if int(event["sequence"]) > base_sequence
            and int(event["event_at_ms"]) <= as_of_ms
        ]
        selected = pending[:limit]
        end_sequence = base_sequence if not selected else int(selected[-1]["sequence"])
        end_events = events[:end_sequence]
        target = self._snapshot_from_events(
            end_events, as_of_ms=as_of_ms, freshness_policy=freshness_policy
        )
        metadata = {
            key: copy.deepcopy(value)
            for key, value in target.items()
            if key not in MFT_READ_MODEL_SECTIONS
        }
        return {
            "source": "MFT_READ_MODEL_SQLITE_AUTHORITY",
            "read_only": True,
            "api_version": "v1",
            "data_provenance": "REAL_MFT_READ_MODEL",
            "schema_version": MFT_CONSOLE_SCHEMA,
            "status": "OK",
            "from_cursor": cursor,
            "cursor": target["cursor"],
            "as_of_ms": as_of_ms,
            "events": copy.deepcopy(selected),
            "has_more": len(pending) > len(selected),
            "result_metadata": metadata,
        }

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MftReadModelStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def apply_mft_delta(
    snapshot: Mapping[str, Any], delta: Mapping[str, Any]
) -> dict[str, Any]:
    """Purely apply one verified replacement-event page to a v1 snapshot."""
    if snapshot.get("schema_version") != MFT_CONSOLE_SCHEMA:
        raise MftReadModelError("snapshot schema is invalid", code="SNAPSHOT_INVALID")
    if delta.get("schema_version") != MFT_CONSOLE_SCHEMA or delta.get("status") != "OK":
        raise MftReadModelError("delta is not applicable", code="DELTA_INVALID")
    if delta.get("from_cursor") != snapshot.get("cursor"):
        raise MftReadModelError("delta cursor does not match snapshot", code="DELTA_GAP")
    parsed = _parse_cursor(snapshot.get("cursor"))
    if parsed is None:
        raise MftReadModelError("snapshot cursor is invalid", code="SNAPSHOT_INVALID")
    sequence, previous = parsed
    result = copy.deepcopy(dict(snapshot))
    events = delta.get("events")
    if not isinstance(events, list):
        raise MftReadModelError("delta events are invalid", code="DELTA_INVALID")
    for raw_event in events:
        if not isinstance(raw_event, Mapping):
            raise MftReadModelError("delta event is invalid", code="DELTA_INVALID")
        event = dict(raw_event)
        sequence += 1
        section = event.get("section")
        payload = _validate_payload(event.get("payload"))
        payload_hash = stable_hash(payload)
        event_id = event.get("event_id")
        event_at_ms = event.get("event_at_ms")
        if not isinstance(event_id, str) or _EVENT_ID.fullmatch(event_id) is None:
            raise MftReadModelError("delta event id is invalid", code="DELTA_INVALID")
        if (
            isinstance(event_at_ms, bool)
            or not isinstance(event_at_ms, int)
            or event_at_ms < 0
        ):
            raise MftReadModelError("delta event time is invalid", code="DELTA_INVALID")
        if (
            event.get("sequence") != sequence
            or section not in MFT_READ_MODEL_SECTIONS
            or event.get("payload_hash") != payload_hash
            or event.get("prev_hash") != previous
            or event.get("event_hash")
            != _event_hash(
                sequence=sequence,
                event_id=event_id,
                section=str(section),
                event_at_ms=event_at_ms,
                payload_hash=payload_hash,
                prev_hash=previous,
            )
        ):
            raise MftReadModelError("delta event hash is invalid", code="DELTA_INVALID")
        result[str(section)] = copy.deepcopy(payload)
        previous = str(event["event_hash"])
    expected_cursor = _cursor_token(sequence, previous)
    if delta.get("cursor") != expected_cursor:
        raise MftReadModelError("delta terminal cursor is invalid", code="DELTA_INVALID")
    metadata = delta.get("result_metadata")
    if not isinstance(metadata, Mapping):
        raise MftReadModelError("delta metadata is invalid", code="DELTA_INVALID")
    for key, value in metadata.items():
        if key in MFT_READ_MODEL_SECTIONS:
            raise MftReadModelError("delta metadata shadows state", code="DELTA_INVALID")
        result[str(key)] = copy.deepcopy(value)
    quality = result.get("data_quality")
    if (
        result.get("cursor") != expected_cursor
        or result.get("schema_version") != MFT_CONSOLE_SCHEMA
        or result.get("read_only") is not True
        or result.get("safety") != _safety()
        or not isinstance(quality, Mapping)
        or quality.get("status") != result.get("snapshot_status")
        or quality.get("provenance") != result.get("data_provenance")
        or quality.get("snapshot_ids") != result.get("snapshot_ids")
    ):
        raise MftReadModelError("delta result cursor is invalid", code="DELTA_INVALID")
    return result


__all__ = [
    "MFT_CONSOLE_SCHEMA",
    "MFT_READ_MODEL_SCHEMA",
    "MFT_READ_MODEL_SECTIONS",
    "MftFreshnessPolicy",
    "MftReadModelError",
    "MftReadModelStore",
    "apply_mft_delta",
    "resnapshot_required_delta",
    "unavailable_mft_snapshot",
]
