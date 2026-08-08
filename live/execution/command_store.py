"""Append-only Testnet execution inbox with CAS ownership and fencing."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from quantos.contracts.hashing import stable_hash
from quantos.contracts.mft_execution import TestnetExecutionIntent


_HEX64 = re.compile(r"[0-9a-f]{64}")
_GENESIS = ""


class ExecutionCommandError(RuntimeError):
    """The Testnet command authority is unavailable or unverifiable."""


class ExecutionCommandConflict(ExecutionCommandError):
    """A stable command identity was reused with different content."""


class ExecutionCommandStateError(ExecutionCommandError):
    """A command transition, owner, lease, or fencing token is invalid."""


class ExecutionCommandState(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATED = "VALIDATED"
    IN_FLIGHT = "IN_FLIGHT"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


_TERMINAL = frozenset(
    {
        ExecutionCommandState.COMPLETED,
        ExecutionCommandState.BLOCKED,
        ExecutionCommandState.FAILED,
    }
)
_ALLOWED = {
    ExecutionCommandState.RECEIVED: frozenset(
        {ExecutionCommandState.VALIDATED, ExecutionCommandState.BLOCKED}
    ),
    ExecutionCommandState.VALIDATED: frozenset(
        {ExecutionCommandState.IN_FLIGHT, ExecutionCommandState.BLOCKED}
    ),
    ExecutionCommandState.IN_FLIGHT: frozenset(
        {
            ExecutionCommandState.COMPLETED,
            ExecutionCommandState.BLOCKED,
            ExecutionCommandState.FAILED,
            ExecutionCommandState.UNKNOWN,
        }
    ),
    ExecutionCommandState.UNKNOWN: _TERMINAL,
    ExecutionCommandState.COMPLETED: frozenset(),
    ExecutionCommandState.BLOCKED: frozenset(),
    ExecutionCommandState.FAILED: frozenset(),
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS testnet_execution_command_events (
  seq                INTEGER PRIMARY KEY AUTOINCREMENT,
  command_id         TEXT NOT NULL,
  intent_hash        TEXT NOT NULL,
  idempotency_key    TEXT NOT NULL,
  state              TEXT NOT NULL,
  recorded_at_ms     INTEGER NOT NULL CHECK (recorded_at_ms >= 0),
  worker_id          TEXT NOT NULL,
  lease_until_ms     INTEGER NOT NULL CHECK (lease_until_ms >= 0),
  fencing_token      INTEGER NOT NULL CHECK (fencing_token >= 0),
  reason_codes_json  TEXT NOT NULL,
  result_json        TEXT NOT NULL,
  intent_json        TEXT NOT NULL,
  prev_hash          TEXT NOT NULL,
  row_hash           TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS testnet_execution_command_events_command_idx
  ON testnet_execution_command_events(command_id, seq);
CREATE INDEX IF NOT EXISTS testnet_execution_command_events_state_idx
  ON testnet_execution_command_events(state, seq);
CREATE TRIGGER IF NOT EXISTS testnet_execution_command_events_no_update
BEFORE UPDATE ON testnet_execution_command_events
BEGIN SELECT RAISE(ABORT, 'testnet_execution_command_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS testnet_execution_command_events_no_delete
BEFORE DELETE ON testnet_execution_command_events
BEGIN SELECT RAISE(ABORT, 'testnet_execution_command_events is append-only'); END;
"""


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ExecutionCommandError("command payload is not canonical JSON") from exc


def _time(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ExecutionCommandStateError(f"{name} must be a non-negative integer")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ExecutionCommandStateError(f"{name} must be non-empty canonical text")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise ExecutionCommandStateError(f"{name} must be a lowercase sha256 digest")
    return value


def _reason_codes(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ExecutionCommandStateError("reason_codes must be a collection")
    materialized = tuple(_text(value, "reason_code") for value in values)
    canonical = tuple(sorted(set(materialized)))
    if len(canonical) != len(materialized):
        raise ExecutionCommandStateError("reason_codes must be unique")
    return canonical


def _intent_json(intent: TestnetExecutionIntent) -> str:
    if not isinstance(intent, TestnetExecutionIntent):
        raise TypeError("intent must be a TestnetExecutionIntent")
    try:
        payload = _canonical_json(intent.model_dump(mode="json"))
        restored = TestnetExecutionIntent.model_validate_json(payload)
    except Exception as exc:
        raise ExecutionCommandError("execution intent is malformed") from exc
    if restored != intent:
        raise ExecutionCommandError("execution intent failed canonical round-trip")
    return payload


def _row_hash(row: Mapping[str, object], previous: str) -> str:
    content = {
        key: row[key]
        for key in (
            "seq",
            "command_id",
            "intent_hash",
            "idempotency_key",
            "state",
            "recorded_at_ms",
            "worker_id",
            "lease_until_ms",
            "fencing_token",
            "reason_codes_json",
            "result_json",
            "intent_json",
        )
    }
    return stable_hash({"content": content, "prev_hash": previous})


@dataclass(frozen=True, slots=True)
class ExecutionCommandRecord:
    seq: int
    command_id: str
    intent_hash: str
    idempotency_key: str
    state: ExecutionCommandState
    recorded_at_ms: int
    worker_id: str
    lease_until_ms: int
    fencing_token: int
    reason_codes: tuple[str, ...]
    result: dict[str, Any]
    intent: TestnetExecutionIntent
    prev_hash: str
    row_hash: str


class SqliteExecutionCommandStore:
    """Single-node durable inbox; every state transition is a chained event."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if not isinstance(path, str) or not path:
            raise ExecutionCommandError("command store path must be non-empty")
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise ExecutionCommandError(f"command store unavailable: {path}")
        if create and path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        try:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout=5000")
                if create:
                    try:
                        self._conn.execute("PRAGMA journal_mode=WAL")
                    except sqlite3.OperationalError as exc:
                        if "locked" not in str(exc).lower():
                            raise
                    self._conn.execute("PRAGMA synchronous=FULL")
                    self._conn.executescript(_SCHEMA)
                    self._conn.commit()
                self._verify_locked()
        except Exception:
            self._conn.close()
            raise

    @staticmethod
    def _decode(row: sqlite3.Row) -> ExecutionCommandRecord:
        try:
            reasons = json.loads(str(row["reason_codes_json"]))
            result = json.loads(str(row["result_json"]))
            if (
                not isinstance(reasons, list)
                or not isinstance(result, dict)
                or _canonical_json(reasons) != row["reason_codes_json"]
                or _canonical_json(result) != row["result_json"]
            ):
                raise ValueError("non-canonical event details")
            intent = TestnetExecutionIntent.model_validate_json(row["intent_json"])
            if _intent_json(intent) != row["intent_json"]:
                raise ValueError("non-canonical intent")
            state = ExecutionCommandState(row["state"])
            return ExecutionCommandRecord(
                seq=int(row["seq"]),
                command_id=str(row["command_id"]),
                intent_hash=str(row["intent_hash"]),
                idempotency_key=str(row["idempotency_key"]),
                state=state,
                recorded_at_ms=int(row["recorded_at_ms"]),
                worker_id=str(row["worker_id"]),
                lease_until_ms=int(row["lease_until_ms"]),
                fencing_token=int(row["fencing_token"]),
                reason_codes=tuple(str(value) for value in reasons),
                result=result,
                intent=intent,
                prev_hash=str(row["prev_hash"]),
                row_hash=str(row["row_hash"]),
            )
        except Exception as exc:
            if isinstance(exc, ExecutionCommandError):
                raise
            raise ExecutionCommandError("command event is malformed") from exc

    def _rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(
                "SELECT * FROM testnet_execution_command_events ORDER BY seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise ExecutionCommandError("command store is unreadable") from exc

    def _verify_locked(self) -> str:
        try:
            exists = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='testnet_execution_command_events'"
            ).fetchone()
            if exists is None:
                raise ExecutionCommandError("command store schema is missing")
            previous_hash = _GENESIS
            previous_by_command: dict[str, ExecutionCommandRecord] = {}
            for expected_seq, row in enumerate(self._rows_locked(), start=1):
                if int(row["seq"]) != expected_seq:
                    raise ExecutionCommandError("command event sequence is not contiguous")
                if row["prev_hash"] != previous_hash:
                    raise ExecutionCommandError("command event chain link mismatch")
                expected_hash = _row_hash(row, previous_hash)
                if row["row_hash"] != expected_hash:
                    raise ExecutionCommandError("command event row hash mismatch")
                record = self._decode(row)
                self._verify_record(record, previous_by_command.get(record.command_id))
                previous_by_command[record.command_id] = record
                previous_hash = expected_hash
            return previous_hash
        except sqlite3.DatabaseError as exc:
            raise ExecutionCommandError("command store is unreadable") from exc

    @staticmethod
    def _verify_record(
        record: ExecutionCommandRecord,
        previous: ExecutionCommandRecord | None,
    ) -> None:
        if (
            record.command_id != record.intent.command_id
            or record.intent_hash != record.intent.intent_hash
            or record.idempotency_key != record.intent.idempotency_key
        ):
            raise ExecutionCommandError("command event identity mismatch")
        canonical_reasons = tuple(sorted(set(record.reason_codes)))
        if record.reason_codes != canonical_reasons:
            raise ExecutionCommandError("command event reason codes are not canonical")
        needs_reason = record.state in {
            ExecutionCommandState.BLOCKED,
            ExecutionCommandState.FAILED,
            ExecutionCommandState.UNKNOWN,
        }
        if needs_reason != bool(record.reason_codes):
            raise ExecutionCommandError("command event reason-code invariant failed")
        if previous is None:
            if record.state is not ExecutionCommandState.RECEIVED:
                raise ExecutionCommandError("first command event must be RECEIVED")
            if record.recorded_at_ms < record.intent.decided_at_ms:
                raise ExecutionCommandError("command was received before its decision")
            if record.worker_id or record.lease_until_ms or record.fencing_token:
                raise ExecutionCommandError("RECEIVED command cannot have an owner")
            return
        if record.state not in _ALLOWED[previous.state]:
            raise ExecutionCommandError(
                f"illegal stored transition {previous.state.value}->{record.state.value}"
            )
        if record.intent != previous.intent:
            raise ExecutionCommandError("command intent changed across transitions")
        if record.recorded_at_ms < previous.recorded_at_ms:
            raise ExecutionCommandError("command event time moved backward")
        if record.state is ExecutionCommandState.IN_FLIGHT:
            if (
                not record.worker_id
                or record.lease_until_ms <= record.recorded_at_ms
                or record.fencing_token != previous.fencing_token + 1
            ):
                raise ExecutionCommandError("IN_FLIGHT ownership is malformed")
        elif previous.state in {
            ExecutionCommandState.IN_FLIGHT,
            ExecutionCommandState.UNKNOWN,
        }:
            if (
                record.worker_id != previous.worker_id
                or record.lease_until_ms != previous.lease_until_ms
                or record.fencing_token != previous.fencing_token
            ):
                raise ExecutionCommandError("command ownership changed after claim")
        elif record.worker_id or record.lease_until_ms or record.fencing_token:
            raise ExecutionCommandError("unclaimed command event has an owner")

    def _latest_row_locked(self, command_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM testnet_execution_command_events WHERE command_id=? "
            "ORDER BY seq DESC LIMIT 1",
            (command_id,),
        ).fetchone()

    def _append_locked(
        self,
        previous: ExecutionCommandRecord | None,
        *,
        intent: TestnetExecutionIntent,
        state: ExecutionCommandState,
        recorded_at_ms: int,
        worker_id: str = "",
        lease_until_ms: int = 0,
        fencing_token: int = 0,
        reason_codes: Iterable[str] = (),
        result: Mapping[str, object] | None = None,
    ) -> ExecutionCommandRecord:
        head = self._verify_locked()
        recorded_at_ms = _time(recorded_at_ms, "recorded_at_ms")
        reasons = _reason_codes(reason_codes)
        result_json = _canonical_json(dict(result or {}))
        intent_json = _intent_json(intent)
        seq = int(
            self._conn.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM testnet_execution_command_events"
            ).fetchone()[0]
        )
        row: dict[str, object] = {
            "seq": seq,
            "command_id": intent.command_id,
            "intent_hash": intent.intent_hash,
            "idempotency_key": intent.idempotency_key,
            "state": state.value,
            "recorded_at_ms": recorded_at_ms,
            "worker_id": worker_id,
            "lease_until_ms": lease_until_ms,
            "fencing_token": fencing_token,
            "reason_codes_json": _canonical_json(list(reasons)),
            "result_json": result_json,
            "intent_json": intent_json,
        }
        row_hash = _row_hash(row, head)
        self._conn.execute(
            "INSERT INTO testnet_execution_command_events"
            "(seq,command_id,intent_hash,idempotency_key,state,recorded_at_ms,"
            "worker_id,lease_until_ms,fencing_token,reason_codes_json,result_json,"
            "intent_json,prev_hash,row_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                seq,
                intent.command_id,
                intent.intent_hash,
                intent.idempotency_key,
                state.value,
                recorded_at_ms,
                worker_id,
                lease_until_ms,
                fencing_token,
                row["reason_codes_json"],
                result_json,
                intent_json,
                head,
                row_hash,
            ),
        )
        stored = self._latest_row_locked(intent.command_id)
        assert stored is not None
        record = self._decode(stored)
        self._verify_record(record, previous)
        return record

    def enqueue(
        self,
        intent: TestnetExecutionIntent,
        *,
        recorded_at_ms: int,
    ) -> ExecutionCommandRecord:
        intent_json = _intent_json(intent)
        recorded_at_ms = _time(recorded_at_ms, "recorded_at_ms")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                existing_row = self._latest_row_locked(intent.command_id)
                if existing_row is not None:
                    existing = self._decode(existing_row)
                    if _intent_json(existing.intent) != intent_json:
                        raise ExecutionCommandConflict(
                            "command identity reused with different intent content"
                        )
                    self._conn.rollback()
                    return existing
                by_key = self._conn.execute(
                    "SELECT * FROM testnet_execution_command_events "
                    "WHERE idempotency_key=? ORDER BY seq LIMIT 1",
                    (intent.idempotency_key,),
                ).fetchone()
                if by_key is not None:
                    raise ExecutionCommandConflict(
                        "idempotency key reused by a different command"
                    )
                record = self._append_locked(
                    None,
                    intent=intent,
                    state=ExecutionCommandState.RECEIVED,
                    recorded_at_ms=recorded_at_ms,
                )
                self._conn.commit()
                return record
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def _transition(
        self,
        command_id: str,
        *,
        expected: frozenset[ExecutionCommandState],
        state: ExecutionCommandState,
        recorded_at_ms: int,
        reason_codes: Iterable[str] = (),
        result: Mapping[str, object] | None = None,
        worker_id: str | None = None,
        lease_until_ms: int | None = None,
        fencing_token: int | None = None,
    ) -> ExecutionCommandRecord:
        command_id = _text(command_id, "command_id")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                row = self._latest_row_locked(command_id)
                if row is None:
                    raise ExecutionCommandStateError(f"unknown command: {command_id}")
                previous = self._decode(row)
                if previous.state not in expected:
                    raise ExecutionCommandStateError(
                        f"command is {previous.state.value}, cannot transition to {state.value}"
                    )
                record = self._append_locked(
                    previous,
                    intent=previous.intent,
                    state=state,
                    recorded_at_ms=recorded_at_ms,
                    worker_id=previous.worker_id if worker_id is None else worker_id,
                    lease_until_ms=(
                        previous.lease_until_ms
                        if lease_until_ms is None
                        else lease_until_ms
                    ),
                    fencing_token=(
                        previous.fencing_token
                        if fencing_token is None
                        else fencing_token
                    ),
                    reason_codes=reason_codes,
                    result=result,
                )
                self._conn.commit()
                return record
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def mark_validated(
        self, command_id: str, *, recorded_at_ms: int
    ) -> ExecutionCommandRecord:
        return self._transition(
            command_id,
            expected=frozenset({ExecutionCommandState.RECEIVED}),
            state=ExecutionCommandState.VALIDATED,
            recorded_at_ms=recorded_at_ms,
        )

    def block(
        self,
        command_id: str,
        *,
        reason_codes: Iterable[str],
        recorded_at_ms: int,
        result: Mapping[str, object] | None = None,
    ) -> ExecutionCommandRecord:
        reasons = _reason_codes(reason_codes)
        if not reasons:
            raise ExecutionCommandStateError("BLOCKED command requires reason codes")
        return self._transition(
            command_id,
            expected=frozenset(
                {ExecutionCommandState.RECEIVED, ExecutionCommandState.VALIDATED}
            ),
            state=ExecutionCommandState.BLOCKED,
            recorded_at_ms=recorded_at_ms,
            reason_codes=reasons,
            result=result,
        )

    def claim(
        self,
        command_id: str,
        *,
        worker_id: str,
        now_ms: int,
        lease_until_ms: int,
    ) -> ExecutionCommandRecord:
        worker_id = _text(worker_id, "worker_id")
        now_ms = _time(now_ms, "now_ms")
        lease_until_ms = _time(lease_until_ms, "lease_until_ms")
        if lease_until_ms <= now_ms:
            raise ExecutionCommandStateError("claim lease must extend beyond now_ms")
        current = self.get(command_id)
        token = 1 if current is None else current.fencing_token + 1
        return self._transition(
            command_id,
            expected=frozenset({ExecutionCommandState.VALIDATED}),
            state=ExecutionCommandState.IN_FLIGHT,
            recorded_at_ms=now_ms,
            worker_id=worker_id,
            lease_until_ms=lease_until_ms,
            fencing_token=token,
        )

    def settle(
        self,
        command_id: str,
        *,
        worker_id: str,
        fencing_token: int,
        state: ExecutionCommandState,
        recorded_at_ms: int,
        reason_codes: Iterable[str] = (),
        result: Mapping[str, object] | None = None,
    ) -> ExecutionCommandRecord:
        if state not in _ALLOWED[ExecutionCommandState.IN_FLIGHT]:
            raise ExecutionCommandStateError("settlement state is invalid")
        worker_id = _text(worker_id, "worker_id")
        fencing_token = _time(fencing_token, "fencing_token")
        recorded_at_ms = _time(recorded_at_ms, "recorded_at_ms")
        current = self.get(command_id)
        if current is None:
            raise ExecutionCommandStateError(f"unknown command: {command_id}")
        if current.state is not ExecutionCommandState.IN_FLIGHT:
            raise ExecutionCommandStateError(
                f"command is {current.state.value}, cannot settle"
            )
        if current.worker_id != worker_id or current.fencing_token != fencing_token:
            raise ExecutionCommandStateError("command owner or fencing token mismatch")
        if recorded_at_ms >= current.lease_until_ms:
            raise ExecutionCommandStateError("command lease expired before settlement")
        reasons = _reason_codes(reason_codes)
        if state in {
            ExecutionCommandState.BLOCKED,
            ExecutionCommandState.FAILED,
            ExecutionCommandState.UNKNOWN,
        } and not reasons:
            raise ExecutionCommandStateError(f"{state.value} command requires reason codes")
        if state is ExecutionCommandState.COMPLETED and reasons:
            raise ExecutionCommandStateError("COMPLETED command cannot have reason codes")
        return self._transition(
            command_id,
            expected=frozenset({ExecutionCommandState.IN_FLIGHT}),
            state=state,
            recorded_at_ms=recorded_at_ms,
            reason_codes=reasons,
            result=result,
        )

    def mark_expired_in_flight_unknown(
        self, *, now_ms: int
    ) -> tuple[ExecutionCommandRecord, ...]:
        now_ms = _time(now_ms, "now_ms")
        changed: list[ExecutionCommandRecord] = []
        for record in self.by_state(ExecutionCommandState.IN_FLIGHT):
            if record.lease_until_ms <= now_ms:
                changed.append(
                    self._transition(
                        record.command_id,
                        expected=frozenset({ExecutionCommandState.IN_FLIGHT}),
                        state=ExecutionCommandState.UNKNOWN,
                        recorded_at_ms=now_ms,
                        reason_codes=("WORKER_LEASE_EXPIRED",),
                        result={"recovery_required": True},
                    )
                )
        return tuple(changed)

    def resolve_unknown(
        self,
        command_id: str,
        *,
        state: ExecutionCommandState,
        recorded_at_ms: int,
        recovery_evidence_hash: str,
        result: Mapping[str, object] | None = None,
        reason_codes: Iterable[str] = (),
    ) -> ExecutionCommandRecord:
        if state not in _TERMINAL:
            raise ExecutionCommandStateError("unknown resolution must be terminal")
        try:
            evidence_hash = _digest(
                recovery_evidence_hash, "recovery_evidence_hash"
            )
        except ExecutionCommandStateError as exc:
            raise ExecutionCommandStateError(
                "unknown resolution requires concrete recovery evidence"
            ) from exc
        reasons = _reason_codes(reason_codes)
        if state in {ExecutionCommandState.BLOCKED, ExecutionCommandState.FAILED}:
            if not reasons:
                raise ExecutionCommandStateError(
                    f"{state.value} resolution requires reason codes"
                )
        elif reasons:
            raise ExecutionCommandStateError(
                "COMPLETED resolution cannot have reason codes"
            )
        details = dict(result or {})
        details["recovery_evidence_hash"] = evidence_hash
        return self._transition(
            command_id,
            expected=frozenset({ExecutionCommandState.UNKNOWN}),
            state=state,
            recorded_at_ms=recorded_at_ms,
            reason_codes=reasons,
            result=details,
        )

    def get(self, command_id: str) -> ExecutionCommandRecord | None:
        command_id = _text(command_id, "command_id")
        with self._lock:
            self._verify_locked()
            row = self._latest_row_locked(command_id)
            return None if row is None else self._decode(row)

    def latest_records(self) -> tuple[ExecutionCommandRecord, ...]:
        with self._lock:
            self._verify_locked()
            rows = self._conn.execute(
                "SELECT e.* FROM testnet_execution_command_events e "
                "JOIN (SELECT command_id,MAX(seq) AS seq "
                "FROM testnet_execution_command_events GROUP BY command_id) latest "
                "ON latest.command_id=e.command_id AND latest.seq=e.seq ORDER BY e.seq"
            ).fetchall()
            return tuple(self._decode(row) for row in rows)

    def by_state(
        self, state: ExecutionCommandState
    ) -> tuple[ExecutionCommandRecord, ...]:
        if not isinstance(state, ExecutionCommandState):
            raise TypeError("state must be an ExecutionCommandState")
        return tuple(record for record in self.latest_records() if record.state is state)

    def row_count(self) -> int:
        with self._lock:
            self._verify_locked()
            return int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM testnet_execution_command_events"
                ).fetchone()[0]
            )

    def chain_valid(self) -> bool:
        with self._lock:
            try:
                self._verify_locked()
                return True
            except ExecutionCommandError:
                return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "ExecutionCommandConflict",
    "ExecutionCommandError",
    "ExecutionCommandRecord",
    "ExecutionCommandState",
    "ExecutionCommandStateError",
    "SqliteExecutionCommandStore",
]
