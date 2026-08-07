"""Deterministic single-node L2 MFT orchestration root.

The core owns no clock, thread, network, broker, credential, or order path.  It
composes the existing autonomy kernel and adds only a fixed ordered phase DAG
plus an append-only checkpoint authority.  A caller supplies ``now_ms`` and
phase handlers; the resident loop belongs in ``tools/mft_l2_runtime.py``.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from quantos.contracts.alpha import Actor, ActorKind
from quantos.contracts.hashing import stable_hash
from services.orchestration.autonomy_kernel import (
    KERNEL_RESOURCE,
    AutonomyKernel,
    open_kernel,
)
from services.orchestration.command_envelope import CommandEnvelope


MFT_PHASES = (
    "market_health",
    "materialize",
    "hourly_alpha",
    "paper",
    "intent_projection",
    "execution_dispatch",
)
_SCHEMA_VERSION = 1
_CHECKPOINT_OUTCOMES = frozenset({"COMPLETED", "BLOCKED", "FAILED"})
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TASK_PREFIX = "mft"
_TASK_DEFINITIONS = (
    ("mft.00.market_health", "market_health", 100, 60_000),
    ("mft.10.materialize", "materialize", 1_000, 5 * 60_000),
    ("mft.20.hourly_alpha", "hourly_alpha", 100, 15 * 60_000),
    ("mft.30.paper", "paper", 100, 20 * 60_000),
    ("mft.40.intent_projection", "intent_projection", 100, 25 * 60_000),
    ("mft.50.execution_dispatch", "execution_dispatch", 100, 30 * 60_000),
)
MFT_TASK_IDS = tuple(item[0] for item in _TASK_DEFINITIONS)
_BY_TASK_ID = {item[0]: item for item in _TASK_DEFINITIONS}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS mft_runtime_checkpoints (
  seq              INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id         TEXT NOT NULL UNIQUE,
  cycle_slot_ms    INTEGER NOT NULL,
  phase            TEXT NOT NULL,
  attempt          INTEGER NOT NULL,
  command_id       TEXT NOT NULL UNIQUE,
  outcome          TEXT NOT NULL,
  reason_code      TEXT NOT NULL,
  consumed_units   INTEGER NOT NULL,
  result_hash      TEXT NOT NULL,
  fencing_token    INTEGER NOT NULL,
  recorded_at_ms   INTEGER NOT NULL,
  payload_json     TEXT NOT NULL,
  prev_hash        TEXT NOT NULL,
  record_hash      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mft_runtime_checkpoint_cycle
ON mft_runtime_checkpoints(cycle_slot_ms, phase, seq);
CREATE TRIGGER IF NOT EXISTS mft_runtime_checkpoints_no_update
BEFORE UPDATE ON mft_runtime_checkpoints
BEGIN SELECT RAISE(ABORT, 'mft_runtime_checkpoints is append-only'); END;
CREATE TRIGGER IF NOT EXISTS mft_runtime_checkpoints_no_delete
BEFORE DELETE ON mft_runtime_checkpoints
BEGIN SELECT RAISE(ABORT, 'mft_runtime_checkpoints is append-only'); END;
"""


class MftRuntimeError(RuntimeError):
    """The deterministic runtime or its checkpoint authority is invalid."""


class MftRuntimeBlocked(MftRuntimeError):
    """A phase is deliberately blocked and downstream work must not run."""


def _integer(value: object, *, name: str, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MftRuntimeError(f"{name} must be an integer")
    if value < (1 if positive else 0):
        qualifier = "positive" if positive else "non-negative"
        raise MftRuntimeError(f"{name} must be {qualifier}")
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise MftRuntimeError(f"{name} must be non-empty canonical text")
    return value


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
        raise MftRuntimeError("runtime payload is not canonical JSON") from exc


@dataclass(frozen=True, slots=True)
class MftTaskResult:
    status: str
    reason_code: str
    consumed_units: int
    checkpoint: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.status not in {"COMPLETED", "BLOCKED"}:
            raise MftRuntimeError("task result status must be COMPLETED or BLOCKED")
        if self.status == "COMPLETED" and self.reason_code:
            raise MftRuntimeError("completed task result must not carry a reason")
        if self.status == "BLOCKED":
            _text(self.reason_code, name="reason_code")
        _integer(self.consumed_units, name="consumed_units")
        if not isinstance(self.checkpoint, Mapping):
            raise MftRuntimeError("checkpoint must be a mapping")
        _canonical(dict(self.checkpoint))

    @classmethod
    def completed(
        cls,
        checkpoint: Mapping[str, Any] | None = None,
        *,
        consumed_units: int = 0,
    ) -> "MftTaskResult":
        return cls("COMPLETED", "", consumed_units, dict(checkpoint or {}))

    @classmethod
    def blocked(
        cls,
        reason_code: str,
        checkpoint: Mapping[str, Any] | None = None,
        *,
        consumed_units: int = 0,
    ) -> "MftTaskResult":
        return cls("BLOCKED", reason_code, consumed_units, dict(checkpoint or {}))


@dataclass(frozen=True, slots=True)
class MftTaskContext:
    phase: str
    task_id: str
    cycle_slot_ms: int
    attempt: int
    now_ms: int
    deadline_ms: int
    budget_units: int
    owner_id: str
    fencing_token: int
    command_id: str
    upstream_checkpoint_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MftCheckpoint:
    seq: int
    event_id: str
    cycle_slot_ms: int
    phase: str
    attempt: int
    command_id: str
    outcome: str
    reason_code: str
    consumed_units: int
    result_hash: str
    fencing_token: int
    recorded_at_ms: int
    prev_hash: str
    record_hash: str


class SqliteMftCheckpointStore:
    """Append-only phase checkpoints sharing the autonomy-kernel database."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if not isinstance(path, str) or not path:
            raise MftRuntimeError("checkpoint path must be non-empty")
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise MftRuntimeError(f"checkpoint store unavailable: {path}")
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        try:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout=5000")
                if create:
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    self._conn.execute("PRAGMA synchronous=FULL")
                    self._conn.executescript(_SCHEMA)
                    self._conn.commit()
                self._verify_locked()
        except Exception:
            self._conn.close()
            raise

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _decode(row: sqlite3.Row) -> MftCheckpoint:
        try:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
            if str(row["payload_json"]) != _canonical(payload):
                raise ValueError("checkpoint payload is not canonical")
            if stable_hash(payload) != str(row["event_id"]):
                raise ValueError("checkpoint event identity mismatch")
            for name in (
                "cycle_slot_ms",
                "phase",
                "attempt",
                "command_id",
                "outcome",
                "reason_code",
                "consumed_units",
                "result_hash",
                "fencing_token",
                "recorded_at_ms",
            ):
                if row[name] != payload[name]:
                    raise ValueError(f"checkpoint column mismatch: {name}")
            return MftCheckpoint(
                seq=int(row["seq"]),
                event_id=str(row["event_id"]),
                cycle_slot_ms=int(row["cycle_slot_ms"]),
                phase=str(row["phase"]),
                attempt=int(row["attempt"]),
                command_id=str(row["command_id"]),
                outcome=str(row["outcome"]),
                reason_code=str(row["reason_code"]),
                consumed_units=int(row["consumed_units"]),
                result_hash=str(row["result_hash"]),
                fencing_token=int(row["fencing_token"]),
                recorded_at_ms=int(row["recorded_at_ms"]),
                prev_hash=str(row["prev_hash"]),
                record_hash=str(row["record_hash"]),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MftRuntimeError("checkpoint row is malformed") from exc

    def _verify_locked(self) -> str:
        try:
            exists = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='mft_runtime_checkpoints'"
            ).fetchone()
            if exists is None:
                raise MftRuntimeError("checkpoint schema is missing")
            previous = ""
            for row in self._conn.execute(
                "SELECT * FROM mft_runtime_checkpoints ORDER BY seq"
            ):
                self._decode(row)
                if str(row["prev_hash"]) != previous:
                    raise MftRuntimeError("checkpoint chain link mismatch")
                expected = stable_hash(
                    {
                        "event_id": str(row["event_id"]),
                        "prev_hash": previous,
                    }
                )
                if str(row["record_hash"]) != expected:
                    raise MftRuntimeError("checkpoint record hash mismatch")
                previous = expected
            return previous
        except sqlite3.DatabaseError as exc:
            raise MftRuntimeError("checkpoint store is unreadable") from exc

    def append(
        self,
        *,
        cycle_slot_ms: int,
        phase: str,
        attempt: int,
        command_id: str,
        outcome: str,
        reason_code: str,
        consumed_units: int,
        result_hash: str,
        fencing_token: int,
        recorded_at_ms: int,
    ) -> MftCheckpoint:
        cycle_slot_ms = _integer(cycle_slot_ms, name="cycle_slot_ms")
        attempt = _integer(attempt, name="attempt")
        consumed_units = _integer(consumed_units, name="consumed_units")
        fencing_token = _integer(
            fencing_token, name="fencing_token", positive=True
        )
        recorded_at_ms = _integer(recorded_at_ms, name="recorded_at_ms")
        phase = _text(phase, name="phase")
        command_id = _text(command_id, name="command_id")
        if phase not in MFT_PHASES:
            raise MftRuntimeError("unknown MFT phase")
        if outcome not in _CHECKPOINT_OUTCOMES:
            raise MftRuntimeError("unknown checkpoint outcome")
        if outcome == "COMPLETED" and reason_code:
            raise MftRuntimeError("completed checkpoint must not carry a reason")
        if outcome != "COMPLETED":
            _text(reason_code, name="reason_code")
        if not isinstance(result_hash, str) or _SHA256.fullmatch(result_hash) is None:
            raise MftRuntimeError("result_hash must be a sha256 digest")
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "cycle_slot_ms": cycle_slot_ms,
            "phase": phase,
            "attempt": attempt,
            "command_id": command_id,
            "outcome": outcome,
            "reason_code": reason_code,
            "consumed_units": consumed_units,
            "result_hash": result_hash,
            "fencing_token": fencing_token,
            "recorded_at_ms": recorded_at_ms,
        }
        payload_json = _canonical(payload)
        event_id = stable_hash(payload)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                head = self._verify_locked()
                existing = self._conn.execute(
                    "SELECT * FROM mft_runtime_checkpoints WHERE command_id=?",
                    (command_id,),
                ).fetchone()
                if existing is not None:
                    decoded = self._decode(existing)
                    if decoded.event_id != event_id:
                        raise MftRuntimeError(
                            "checkpoint command identity reused with different content"
                        )
                    self._conn.rollback()
                    return decoded
                record_hash = stable_hash(
                    {"event_id": event_id, "prev_hash": head}
                )
                cursor = self._conn.execute(
                    "INSERT INTO mft_runtime_checkpoints"
                    "(event_id,cycle_slot_ms,phase,attempt,command_id,outcome,"
                    "reason_code,consumed_units,result_hash,fencing_token,"
                    "recorded_at_ms,payload_json,prev_hash,record_hash) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_id,
                        cycle_slot_ms,
                        phase,
                        attempt,
                        command_id,
                        outcome,
                        reason_code,
                        consumed_units,
                        result_hash,
                        fencing_token,
                        recorded_at_ms,
                        payload_json,
                        head,
                        record_hash,
                    ),
                )
                seq = int(cursor.lastrowid)
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM mft_runtime_checkpoints WHERE seq=?", (seq,)
                ).fetchone()
                if row is None:
                    raise MftRuntimeError("checkpoint disappeared after commit")
                return self._decode(row)
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def latest(self, cycle_slot_ms: int, phase: str) -> MftCheckpoint | None:
        cycle_slot_ms = _integer(cycle_slot_ms, name="cycle_slot_ms")
        phase = _text(phase, name="phase")
        with self._lock:
            self._verify_locked()
            row = self._conn.execute(
                "SELECT * FROM mft_runtime_checkpoints "
                "WHERE cycle_slot_ms=? AND phase=? ORDER BY seq DESC LIMIT 1",
                (cycle_slot_ms, phase),
            ).fetchone()
            return None if row is None else self._decode(row)

    def for_cycle(self, cycle_slot_ms: int) -> tuple[MftCheckpoint, ...]:
        cycle_slot_ms = _integer(cycle_slot_ms, name="cycle_slot_ms")
        with self._lock:
            self._verify_locked()
            rows = self._conn.execute(
                "SELECT * FROM mft_runtime_checkpoints "
                "WHERE cycle_slot_ms=? ORDER BY seq",
                (cycle_slot_ms,),
            ).fetchall()
            return tuple(self._decode(row) for row in rows)

    def event_count(self) -> int:
        with self._lock:
            self._verify_locked()
            return int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM mft_runtime_checkpoints"
                ).fetchone()[0]
            )

    def chain_valid(self) -> bool:
        with self._lock:
            try:
                self._verify_locked()
                return True
            except MftRuntimeError:
                return False


MftHandler = Callable[[MftTaskContext], MftTaskResult]


class MftRuntime:
    """Leader-gated ordered phase runtime over the existing autonomy kernel."""

    def __init__(
        self,
        *,
        kernel: AutonomyKernel,
        checkpoints: SqliteMftCheckpointStore,
        handlers: Mapping[str, MftHandler] | None = None,
    ) -> None:
        if not isinstance(kernel, AutonomyKernel):
            raise TypeError("kernel must be an AutonomyKernel")
        if not isinstance(checkpoints, SqliteMftCheckpointStore):
            raise TypeError("checkpoints must be a SqliteMftCheckpointStore")
        self.kernel = kernel
        self.checkpoints = checkpoints
        self.handlers = dict(handlers or {})
        unknown = set(self.handlers) - set(MFT_PHASES)
        if unknown:
            raise MftRuntimeError(f"unknown handler phases: {sorted(unknown)}")
        self.actor = Actor(actor_id="mft-l2-runtime", kind=ActorKind.SYSTEM)

    def close(self) -> None:
        self.checkpoints.close()
        self.kernel.close()

    def register_tasks(
        self,
        *,
        interval_ms: int,
        first_due_ms: int,
        now_ms: int,
        max_attempts: int = 3,
    ) -> None:
        interval_ms = _integer(interval_ms, name="interval_ms", positive=True)
        first_due_ms = _integer(first_due_ms, name="first_due_ms")
        now_ms = _integer(now_ms, name="now_ms")
        max_attempts = _integer(max_attempts, name="max_attempts", positive=True)
        if first_due_ms < now_ms:
            raise MftRuntimeBlocked("first_due_ms must not precede registration time")
        registered_at_ms = first_due_ms - interval_ms
        if registered_at_ms < 0 or registered_at_ms != now_ms:
            raise MftRuntimeBlocked(
                "first due time must equal registration anchor plus interval"
            )
        for ordinal, (task_id, phase, budget, deadline) in enumerate(
            _TASK_DEFINITIONS
        ):
            dependencies = list(MFT_PHASES[:ordinal])
            self.kernel.tasks.register(
                task_id,
                kind="INTERVAL",
                interval_ms=interval_ms,
                payload={
                    "schema_version": _SCHEMA_VERSION,
                    "phase": phase,
                    "ordinal": ordinal,
                    "budget_units": budget,
                    "deadline_offset_ms": deadline,
                    "dependencies": dependencies,
                    "l2_only": True,
                    "paper_allowed": True,
                    "live_allowed": False,
                    "order_submission_allowed": False,
                },
                max_attempts=max_attempts,
                now_ms=registered_at_ms,
            )

    @staticmethod
    def _slot_and_attempt(envelope: CommandEnvelope) -> tuple[int, int]:
        parts = envelope.idempotency_key.rsplit(":", 2)
        if len(parts) != 3 or parts[0] != envelope.command_type:
            raise MftRuntimeError("scheduler retry identity is malformed")
        try:
            slot = int(parts[1])
            attempt = int(parts[2])
        except ValueError as exc:
            raise MftRuntimeError("scheduler retry identity is malformed") from exc
        return (
            _integer(slot, name="cycle_slot_ms"),
            _integer(attempt, name="attempt"),
        )

    def _cycle_slot(self, task_id: str, scheduler_slot_ms: int) -> int:
        task = self.kernel.tasks.get(task_id)
        if task is None or task.kind != "INTERVAL" or task.interval_ms is None:
            raise MftRuntimeError("registered MFT interval task is unavailable")
        first_due_ms = task.registered_at_ms + task.interval_ms
        if scheduler_slot_ms < first_due_ms:
            raise MftRuntimeError("scheduler slot precedes the MFT cycle anchor")
        return first_due_ms + (
            (scheduler_slot_ms - first_due_ms) // task.interval_ms
        ) * task.interval_ms

    def _checkpoint(
        self,
        context: MftTaskContext,
        *,
        outcome: str,
        reason_code: str,
        consumed_units: int,
        result_hash: str,
    ) -> MftCheckpoint:
        self.kernel.leases.require_owner(
            KERNEL_RESOURCE,
            owner_id=context.owner_id,
            fencing_token=context.fencing_token,
            now_ms=context.now_ms,
        )
        return self.checkpoints.append(
            cycle_slot_ms=context.cycle_slot_ms,
            phase=context.phase,
            attempt=context.attempt,
            command_id=context.command_id,
            outcome=outcome,
            reason_code=reason_code,
            consumed_units=consumed_units,
            result_hash=result_hash,
            fencing_token=context.fencing_token,
            recorded_at_ms=context.now_ms,
        )

    def _execute(
        self,
        envelope: CommandEnvelope,
        *,
        owner_id: str,
        now_ms: int,
    ) -> str:
        definition = _BY_TASK_ID.get(envelope.command_type)
        if definition is None:
            raise MftRuntimeError("runtime received an unregistered task type")
        task_id, phase, expected_budget, expected_deadline = definition
        payload = dict(envelope.payload)
        expected_ordinal = MFT_PHASES.index(phase)
        expected_payload = {
            "schema_version": _SCHEMA_VERSION,
            "phase": phase,
            "ordinal": expected_ordinal,
            "budget_units": expected_budget,
            "deadline_offset_ms": expected_deadline,
            "dependencies": list(MFT_PHASES[:expected_ordinal]),
            "l2_only": True,
            "paper_allowed": True,
            "live_allowed": False,
            "order_submission_allowed": False,
        }
        if task_id != envelope.command_type or payload != expected_payload:
            raise MftRuntimeError("registered task payload is not canonical")
        scheduler_slot_ms, attempt = self._slot_and_attempt(envelope)
        cycle_slot_ms = self._cycle_slot(task_id, scheduler_slot_ms)
        lease = self.kernel.leases.current(KERNEL_RESOURCE, now_ms=now_ms)
        if lease is None or lease.owner_id != owner_id:
            raise MftRuntimeBlocked("runtime leader lease is unavailable")
        self.kernel.leases.require_owner(
            KERNEL_RESOURCE,
            owner_id=owner_id,
            fencing_token=lease.fencing_token,
            now_ms=now_ms,
        )
        completed = self.checkpoints.latest(cycle_slot_ms, phase)
        if completed is not None and completed.outcome == "COMPLETED":
            return completed.record_hash
        if phase == "execution_dispatch":
            context = MftTaskContext(
                phase=phase,
                task_id=task_id,
                cycle_slot_ms=cycle_slot_ms,
                attempt=attempt,
                now_ms=now_ms,
                deadline_ms=cycle_slot_ms + expected_deadline,
                budget_units=expected_budget,
                owner_id=owner_id,
                fencing_token=lease.fencing_token,
                command_id=envelope.command_id,
                upstream_checkpoint_hashes=(),
            )
            self._checkpoint(
                context,
                outcome="BLOCKED",
                reason_code="EXECUTION_DISPATCH_DISABLED_BEFORE_TASK_32",
                consumed_units=0,
                result_hash=stable_hash({}),
            )
            raise MftRuntimeBlocked("execution dispatch is not enabled")
        upstream: list[str] = []
        for dependency in MFT_PHASES[:expected_ordinal]:
            checkpoint = self.checkpoints.latest(cycle_slot_ms, dependency)
            if checkpoint is None or checkpoint.outcome != "COMPLETED":
                context = MftTaskContext(
                    phase=phase,
                    task_id=task_id,
                    cycle_slot_ms=cycle_slot_ms,
                    attempt=attempt,
                    now_ms=now_ms,
                    deadline_ms=cycle_slot_ms + expected_deadline,
                    budget_units=expected_budget,
                    owner_id=owner_id,
                    fencing_token=lease.fencing_token,
                    command_id=envelope.command_id,
                    upstream_checkpoint_hashes=tuple(upstream),
                )
                self._checkpoint(
                    context,
                    outcome="BLOCKED",
                    reason_code="UPSTREAM_NOT_COMPLETED",
                    consumed_units=0,
                    result_hash=stable_hash({}),
                )
                raise MftRuntimeBlocked("upstream phase is not completed")
            upstream.append(checkpoint.record_hash)
        context = MftTaskContext(
            phase=phase,
            task_id=task_id,
            cycle_slot_ms=cycle_slot_ms,
            attempt=attempt,
            now_ms=now_ms,
            deadline_ms=cycle_slot_ms + expected_deadline,
            budget_units=expected_budget,
            owner_id=owner_id,
            fencing_token=lease.fencing_token,
            command_id=envelope.command_id,
            upstream_checkpoint_hashes=tuple(upstream),
        )
        if now_ms > context.deadline_ms:
            self._checkpoint(
                context,
                outcome="BLOCKED",
                reason_code="DEADLINE_EXCEEDED",
                consumed_units=0,
                result_hash=stable_hash({}),
            )
            raise MftRuntimeBlocked("phase deadline exceeded")
        handler = self.handlers.get(phase)
        if handler is None:
            self._checkpoint(
                context,
                outcome="BLOCKED",
                reason_code="HANDLER_UNAVAILABLE",
                consumed_units=0,
                result_hash=stable_hash({}),
            )
            raise MftRuntimeBlocked("phase handler is unavailable")
        try:
            result = handler(context)
            if not isinstance(result, MftTaskResult):
                raise MftRuntimeError("phase handler returned an invalid result")
        except Exception as exc:
            self._checkpoint(
                context,
                outcome="FAILED",
                reason_code=type(exc).__name__,
                consumed_units=0,
                result_hash=stable_hash({}),
            )
            raise
        result_hash = stable_hash(dict(result.checkpoint))
        if result.consumed_units > context.budget_units:
            self._checkpoint(
                context,
                outcome="BLOCKED",
                reason_code="BUDGET_EXCEEDED",
                consumed_units=result.consumed_units,
                result_hash=result_hash,
            )
            raise MftRuntimeBlocked("phase budget exceeded")
        if result.status == "BLOCKED":
            self._checkpoint(
                context,
                outcome="BLOCKED",
                reason_code=result.reason_code,
                consumed_units=result.consumed_units,
                result_hash=result_hash,
            )
            raise MftRuntimeBlocked(result.reason_code)
        checkpoint = self._checkpoint(
            context,
            outcome="COMPLETED",
            reason_code="",
            consumed_units=result.consumed_units,
            result_hash=result_hash,
        )
        return checkpoint.record_hash

    def run_once(
        self,
        *,
        owner_id: str,
        now_ms: int,
        lease_ms: int,
    ) -> dict[str, Any]:
        owner_id = _text(owner_id, name="owner_id")
        now_ms = _integer(now_ms, name="now_ms")
        lease_ms = _integer(lease_ms, name="lease_ms", positive=True)
        due_before = self.kernel.tasks.due_tasks(now_ms)
        foreign = [
            task.task_id
            for task in due_before
            if task.task_id not in _BY_TASK_ID
        ]
        if foreign:
            raise MftRuntimeBlocked(
                f"dedicated MFT runtime contains foreign due tasks: {foreign}"
            )
        summary = self.kernel.run_once(
            owner_id=owner_id,
            actor=self.actor,
            now_ms=now_ms,
            lease_ms=lease_ms,
            executor=lambda envelope: self._execute(
                envelope,
                owner_id=owner_id,
                now_ms=now_ms,
            ),
        )
        result: dict[str, Any] = dict(summary)
        cycle_slots = []
        for task in due_before:
            if task.task_id.startswith(f"{_TASK_PREFIX}."):
                slot = task.next_due_ms
                if task.interval_ms and now_ms - slot > task.interval_ms:
                    slot += ((now_ms - slot) // task.interval_ms) * task.interval_ms
                cycle_slots.append(self._cycle_slot(task.task_id, slot))
        cycle_slot_ms = max(cycle_slots) if cycle_slots else None
        same_cycle_completed = (
            cycle_slot_ms is not None
            and len(set(cycle_slots)) == 1
            and all(
                (
                    checkpoint := self.checkpoints.latest(cycle_slot_ms, phase)
                )
                is not None
                and checkpoint.outcome == "COMPLETED"
                for phase in MFT_PHASES
            )
        )
        if not summary["leader"] and summary["fired"] == 0:
            status = "SKIPPED_NOT_LEADER"
        elif summary["fired"] == 0:
            registered = [
                self.kernel.tasks.get(task_id) for task_id in _BY_TASK_ID
            ]
            status = (
                "BLOCKED"
                if any(
                    task is not None and (task.attempts > 0 or task.dead)
                    for task in registered
                )
                else "IDLE"
            )
        elif same_cycle_completed:
            status = "COMPLETED"
        else:
            status = "BLOCKED"
        result.update(
            {
                "schema_version": _SCHEMA_VERSION,
                "runtime_status": status,
                "cycle_slot_ms": cycle_slot_ms,
                "l2_only": True,
                "paper_allowed": True,
                "live_allowed": False,
                "order_submission_allowed": False,
                "checkpoint_chain_valid": self.checkpoints.chain_valid(),
            }
        )
        return result


def open_mft_runtime(
    path: str,
    *,
    create: bool = False,
    handlers: Mapping[str, MftHandler] | None = None,
    backoff_base_ms: int = 60_000,
    backoff_cap_ms: int = 3_600_000,
) -> MftRuntime:
    kernel = open_kernel(
        path,
        create=create,
        backoff_base_ms=backoff_base_ms,
        backoff_cap_ms=backoff_cap_ms,
    )
    try:
        checkpoints = SqliteMftCheckpointStore(path, create=create)
    except Exception:
        kernel.close()
        raise
    return MftRuntime(
        kernel=kernel,
        checkpoints=checkpoints,
        handlers=handlers,
    )


__all__ = [
    "MFT_PHASES",
    "MFT_TASK_IDS",
    "MftCheckpoint",
    "MftHandler",
    "MftRuntime",
    "MftRuntimeBlocked",
    "MftRuntimeError",
    "MftTaskContext",
    "MftTaskResult",
    "SqliteMftCheckpointStore",
    "open_mft_runtime",
]
