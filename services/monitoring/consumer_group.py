"""Tamper-evident single-node consumer-group coordination.

This module coordinates workers sharing one SQLite event authority. It provides
heartbeats, fixed partition topology, deterministic eager rebalancing, and
generation fencing. It is not a cross-host broker or a distributed consensus
protocol.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from dataclasses import dataclass


class EventConsumerGroupError(RuntimeError):
    """Base error for consumer-group coordination failures."""


class EventConsumerGroupConfigurationError(EventConsumerGroupError):
    """The requested group topology differs from its immutable configuration."""


class EventConsumerGroupIntegrityError(EventConsumerGroupError):
    """The append-only coordination history cannot be verified."""


class EventConsumerGroupFencedError(EventConsumerGroupError):
    """A worker no longer owns the requested partition generation."""


@dataclass(frozen=True)
class EventConsumerGroupAssignment:
    """A generation-fenced assignment returned by a successful heartbeat."""

    group_id: str
    worker_id: str
    generation: int
    partition_count: int
    partitions: tuple[int, ...]
    heartbeat_at_ms: int
    expires_at_ms: int

    @property
    def declared_worker_id(self) -> str:
        return _declared_worker_id(self.worker_id)

    @property
    def identity_assurance(self) -> str:
        return _worker_identity_assurance(self.worker_id)


@dataclass
class _GroupState:
    partition_count: int
    session_timeout_ms: int
    generation: int
    assignments: tuple[str, ...]
    members: dict[str, tuple[str, int]]
    last_at_ms: int


@dataclass
class _GroupStage:
    """Transaction-local coordination state; promoted only after owner commit."""

    states: dict[str, _GroupState]
    head_hash: str
    row_count: int
    last_sequence: int


_ACTIONS = {"GROUP_CREATED", "HEARTBEAT", "LEFT", "REBALANCED"}
_PARTITION_CONSUMER_PREFIX = "__event_group_partition__:"
_WORKER_IDENTITY_NAMESPACE = "__worker_identity__:"
_PROCESS_DECLARED_WORKER_PREFIX = (
    f"{_WORKER_IDENTITY_NAMESPACE}process_declared:"
)
PROCESS_DECLARED_WORKER_ASSURANCE = "PROCESS_DECLARED_UNAUTHENTICATED"
LEGACY_UNLABELED_WORKER_ASSURANCE = "LEGACY_UNLABELED"


def _coordination_worker_id(declared_worker_id: str) -> str:
    if declared_worker_id.startswith(_WORKER_IDENTITY_NAMESPACE):
        raise ValueError("worker_id uses a reserved worker identity prefix")
    return f"{_PROCESS_DECLARED_WORKER_PREFIX}{declared_worker_id}"


def _declared_worker_id(coordination_worker_id: str) -> str:
    if coordination_worker_id.startswith(_PROCESS_DECLARED_WORKER_PREFIX):
        return coordination_worker_id[len(_PROCESS_DECLARED_WORKER_PREFIX):]
    return coordination_worker_id


def _worker_identity_assurance(coordination_worker_id: str) -> str:
    if coordination_worker_id.startswith(_PROCESS_DECLARED_WORKER_PREFIX):
        return PROCESS_DECLARED_WORKER_ASSURANCE
    return LEGACY_UNLABELED_WORKER_ASSURANCE


def _canonical_assignments(assignments: tuple[str, ...]) -> str:
    return json.dumps(
        list(assignments),
        ensure_ascii=True,
        separators=(",", ":"),
    )


def _coordination_hash(
    *,
    group_id: str,
    action: str,
    worker_id: str,
    at_ms: int,
    partition_count: int,
    session_timeout_ms: int,
    generation: int,
    assignments_json: str,
    prev_record_hash: str,
) -> str:
    canonical = json.dumps(
        {
            "action": action,
            "assignments_json": assignments_json,
            "at_ms": at_ms,
            "generation": generation,
            "group_id": group_id,
            "partition_count": partition_count,
            "prev_record_hash": prev_record_hash,
            "session_timeout_ms": session_timeout_ms,
            "worker_id": worker_id,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _active_workers(state: _GroupState, now_ms: int) -> tuple[str, ...]:
    return tuple(
        sorted(
            worker_id
            for worker_id, (action, heartbeat_at_ms) in state.members.items()
            if action == "HEARTBEAT"
            and now_ms <= heartbeat_at_ms + state.session_timeout_ms
        )
    )


def _desired_assignments(state: _GroupState, now_ms: int) -> tuple[str, ...]:
    active = _active_workers(state, now_ms)
    if not active:
        return ("",) * state.partition_count
    return tuple(active[index % len(active)] for index in range(state.partition_count))


class ConsumerGroupCoordinator:
    """Coordination store used while the owning event-bus lock is held."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._c = connection
        self.head_hash = ""
        self.row_count = 0
        self.last_sequence = 0
        self._states: dict[str, _GroupState] = {}
        self._tentative: _GroupStage | None = None

    @staticmethod
    def _identity(value: object, *, name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{name} must be non-empty")
        return normalized

    @classmethod
    def _group_identity(cls, value: object) -> str:
        group_id = cls._identity(value, name="group_id")
        if group_id.startswith(_PARTITION_CONSUMER_PREFIX):
            raise ValueError("group_id uses a reserved internal prefix")
        return group_id

    @staticmethod
    def _non_negative_int(value: object, *, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
        return value

    @classmethod
    def _positive_int(cls, value: object, *, name: str) -> int:
        checked = cls._non_negative_int(value, name=name)
        if checked == 0:
            raise ValueError(f"{name} must be positive")
        return checked

    def create_or_migrate_locked(self) -> None:
        self._c.execute(
            "CREATE TABLE IF NOT EXISTS event_consumer_group_journal ("
            " coordination_seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " group_id TEXT NOT NULL, action TEXT NOT NULL CHECK ("
            " action IN ('GROUP_CREATED','HEARTBEAT','LEFT','REBALANCED')),"
            " worker_id TEXT NOT NULL, at_ms INTEGER NOT NULL CHECK (at_ms >= 0),"
            " partition_count INTEGER NOT NULL CHECK (partition_count >= 1),"
            " session_timeout_ms INTEGER NOT NULL CHECK (session_timeout_ms >= 1),"
            " generation INTEGER NOT NULL CHECK (generation >= 0),"
            " assignments_json TEXT NOT NULL, prev_record_hash TEXT NOT NULL,"
            " record_hash TEXT NOT NULL)"
        )
        self._c.execute(
            "CREATE INDEX IF NOT EXISTS event_consumer_group_lookup "
            "ON event_consumer_group_journal (group_id, coordination_seq)"
        )
        for name, operation in (
            ("event_consumer_group_journal_no_update", "UPDATE"),
            ("event_consumer_group_journal_no_delete", "DELETE"),
        ):
            self._c.execute(
                f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} "
                "ON event_consumer_group_journal BEGIN SELECT RAISE(ABORT, "
                "'event_consumer_group_journal is append-only'); END"
            )
        self.verify_locked()

    def _rows_locked(self) -> list[tuple]:
        try:
            return self._c.execute(
                "SELECT coordination_seq,group_id,action,worker_id,at_ms,"
                " partition_count,session_timeout_ms,generation,assignments_json,"
                " prev_record_hash,record_hash FROM event_consumer_group_journal"
                " ORDER BY coordination_seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise EventConsumerGroupIntegrityError(
                f"consumer-group journal unreadable: {exc}"
            ) from exc

    @staticmethod
    def _parse_assignments(row: tuple) -> tuple[str, ...]:
        try:
            decoded = json.loads(row[8])
        except Exception as exc:
            raise EventConsumerGroupIntegrityError(
                f"consumer-group row {row[0]} assignments are invalid"
            ) from exc
        if (
            not isinstance(decoded, list)
            or len(decoded) != row[5]
            or any(not isinstance(item, str) for item in decoded)
        ):
            raise EventConsumerGroupIntegrityError(
                f"consumer-group row {row[0]} assignments are invalid"
            )
        assignments = tuple(decoded)
        if row[8] != _canonical_assignments(assignments):
            raise EventConsumerGroupIntegrityError(
                f"consumer-group row {row[0]} assignments are not canonical"
            )
        return assignments

    @classmethod
    def _reduce_row(
        cls,
        states: dict[str, _GroupState],
        row: tuple,
        *,
        previous: str,
    ) -> str:
        """Validate and reduce one row for both full replay and staged appends."""
        assignments = cls._parse_assignments(row)
        expected_hash = _coordination_hash(
            group_id=row[1],
            action=row[2],
            worker_id=row[3],
            at_ms=row[4],
            partition_count=row[5],
            session_timeout_ms=row[6],
            generation=row[7],
            assignments_json=row[8],
            prev_record_hash=previous,
        )
        if row[9] != previous:
            raise EventConsumerGroupIntegrityError(
                f"consumer-group row {row[0]} previous hash mismatch"
            )
        if row[10] != expected_hash:
            raise EventConsumerGroupIntegrityError(
                f"consumer-group row {row[0]} record hash mismatch"
            )
        if row[2] not in _ACTIONS:
            raise EventConsumerGroupIntegrityError(
                f"consumer-group row {row[0]} action is invalid"
            )

        state = states.get(row[1])
        if row[2] == "GROUP_CREATED":
            if state is not None or row[3] or row[7] != 0 or any(assignments):
                raise EventConsumerGroupIntegrityError(
                    f"consumer-group row {row[0]} creation is invalid"
                )
            states[row[1]] = _GroupState(
                partition_count=row[5],
                session_timeout_ms=row[6],
                generation=0,
                assignments=assignments,
                members={},
                last_at_ms=row[4],
            )
            return expected_hash

        if state is None:
            raise EventConsumerGroupIntegrityError(
                f"consumer-group row {row[0]} precedes group creation"
            )
        if row[5] != state.partition_count or row[6] != state.session_timeout_ms:
            raise EventConsumerGroupIntegrityError(
                f"consumer-group row {row[0]} changes immutable configuration"
            )
        if row[4] < state.last_at_ms:
            raise EventConsumerGroupIntegrityError(
                f"consumer-group row {row[0]} moves time backwards"
            )

        if row[2] in {"HEARTBEAT", "LEFT"}:
            if (
                not row[3]
                or row[7] != state.generation
                or assignments != state.assignments
            ):
                raise EventConsumerGroupIntegrityError(
                    f"consumer-group row {row[0]} membership is invalid"
                )
            state.members[row[3]] = (row[2], row[4])
        else:
            if not row[3] or row[7] != state.generation + 1:
                raise EventConsumerGroupIntegrityError(
                    f"consumer-group row {row[0]} generation is invalid"
                )
            desired = _desired_assignments(state, row[4])
            if assignments != desired:
                raise EventConsumerGroupIntegrityError(
                    f"consumer-group row {row[0]} rebalance is not deterministic"
                )
            state.generation = row[7]
            state.assignments = assignments
        state.last_at_ms = row[4]
        return expected_hash

    def verify_locked(self) -> str:
        previous = ""
        states: dict[str, _GroupState] = {}
        rows = self._rows_locked()
        for row in rows:
            previous = self._reduce_row(states, row, previous=previous)

        self.head_hash = previous
        self.row_count = len(rows)
        self.last_sequence = int(rows[-1][0]) if rows else 0
        self._states = states
        self._tentative = None
        return previous

    def begin_staged_locked(self) -> _GroupStage:
        if self._tentative is not None:
            raise EventConsumerGroupIntegrityError(
                "consumer-group transaction already has staged state"
            )
        return _GroupStage(
            states=deepcopy(self._states),
            head_hash=self.head_hash,
            row_count=self.row_count,
            last_sequence=self.last_sequence,
        )

    def _append_staged_locked(
        self,
        stage: _GroupStage,
        *,
        group_id: str,
        action: str,
        worker_id: str,
        at_ms: int,
        partition_count: int,
        session_timeout_ms: int,
        generation: int,
        assignments: tuple[str, ...],
    ) -> None:
        assignments_json = _canonical_assignments(assignments)
        digest = _coordination_hash(
            group_id=group_id,
            action=action,
            worker_id=worker_id,
            at_ms=at_ms,
            partition_count=partition_count,
            session_timeout_ms=session_timeout_ms,
            generation=generation,
            assignments_json=assignments_json,
            prev_record_hash=stage.head_hash,
        )
        cursor = self._c.execute(
            "INSERT INTO event_consumer_group_journal"
            " (group_id,action,worker_id,at_ms,partition_count,session_timeout_ms,"
            " generation,assignments_json,prev_record_hash,record_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                group_id,
                action,
                worker_id,
                at_ms,
                partition_count,
                session_timeout_ms,
                generation,
                assignments_json,
                stage.head_hash,
                digest,
            ),
        )
        row = (
            int(cursor.lastrowid),
            group_id,
            action,
            worker_id,
            at_ms,
            partition_count,
            session_timeout_ms,
            generation,
            assignments_json,
            stage.head_hash,
            digest,
        )
        stage.head_hash = self._reduce_row(
            stage.states,
            row,
            previous=stage.head_hash,
        )
        stage.row_count += 1
        stage.last_sequence = int(cursor.lastrowid)

    def tentative_frontier_locked(self) -> tuple[int, int, str] | None:
        if self._tentative is None:
            return None
        return (
            self._tentative.last_sequence,
            self._tentative.row_count,
            self._tentative.head_hash,
        )

    def promote_staged_locked(self) -> None:
        if self._tentative is None:
            return
        self._states = self._tentative.states
        self.head_hash = self._tentative.head_hash
        self.row_count = self._tentative.row_count
        self.last_sequence = self._tentative.last_sequence
        self._tentative = None

    def discard_staged_locked(self) -> None:
        self._tentative = None

    def _append_locked(
        self,
        *,
        group_id: str,
        action: str,
        worker_id: str,
        at_ms: int,
        partition_count: int,
        session_timeout_ms: int,
        generation: int,
        assignments: tuple[str, ...],
    ) -> None:
        assignments_json = _canonical_assignments(assignments)
        digest = _coordination_hash(
            group_id=group_id,
            action=action,
            worker_id=worker_id,
            at_ms=at_ms,
            partition_count=partition_count,
            session_timeout_ms=session_timeout_ms,
            generation=generation,
            assignments_json=assignments_json,
            prev_record_hash=self.head_hash,
        )
        self._c.execute(
            "INSERT INTO event_consumer_group_journal"
            " (group_id,action,worker_id,at_ms,partition_count,session_timeout_ms,"
            " generation,assignments_json,prev_record_hash,record_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                group_id,
                action,
                worker_id,
                at_ms,
                partition_count,
                session_timeout_ms,
                generation,
                assignments_json,
                self.head_hash,
                digest,
            ),
        )
        self.head_hash = digest

    @staticmethod
    def _configuration_matches(
        state: _GroupState,
        *,
        partition_count: int,
        session_timeout_ms: int,
    ) -> bool:
        return (
            state.partition_count == partition_count
            and state.session_timeout_ms == session_timeout_ms
        )

    def heartbeat_locked(
        self,
        group_id: str,
        *,
        worker_id: str,
        now_ms: int,
        partition_count: int,
        session_timeout_ms: int,
    ) -> EventConsumerGroupAssignment:
        group_id = self._group_identity(group_id)
        declared_worker_id = self._identity(worker_id, name="worker_id")
        worker_id = _coordination_worker_id(declared_worker_id)
        now_ms = self._non_negative_int(now_ms, name="now_ms")
        partition_count = self._positive_int(
            partition_count, name="partition_count"
        )
        session_timeout_ms = self._positive_int(
            session_timeout_ms, name="session_timeout_ms"
        )
        stage = self.begin_staged_locked()
        try:
            state = stage.states.get(group_id)
            if state is None:
                self._append_staged_locked(
                    stage,
                    group_id=group_id,
                    action="GROUP_CREATED",
                    worker_id="",
                    at_ms=now_ms,
                    partition_count=partition_count,
                    session_timeout_ms=session_timeout_ms,
                    generation=0,
                    assignments=("",) * partition_count,
                )
                state = stage.states[group_id]
            elif not self._configuration_matches(
                state,
                partition_count=partition_count,
                session_timeout_ms=session_timeout_ms,
            ):
                raise EventConsumerGroupConfigurationError(
                    "consumer-group partition_count and session_timeout_ms are immutable"
                )
            if now_ms < state.last_at_ms:
                raise ValueError("consumer-group time must be monotonic")

            legacy_member = state.members.get(declared_worker_id)
            if (
                legacy_member is not None
                and legacy_member[0] == "HEARTBEAT"
                and now_ms <= legacy_member[1] + state.session_timeout_ms
            ):
                self._append_staged_locked(
                    stage,
                    group_id=group_id,
                    action="LEFT",
                    worker_id=declared_worker_id,
                    at_ms=now_ms,
                    partition_count=state.partition_count,
                    session_timeout_ms=state.session_timeout_ms,
                    generation=state.generation,
                    assignments=state.assignments,
                )
                state = stage.states[group_id]

            self._append_staged_locked(
                stage,
                group_id=group_id,
                action="HEARTBEAT",
                worker_id=worker_id,
                at_ms=now_ms,
                partition_count=state.partition_count,
                session_timeout_ms=state.session_timeout_ms,
                generation=state.generation,
                assignments=state.assignments,
            )
            state = stage.states[group_id]
            desired = _desired_assignments(state, now_ms)
            if desired != state.assignments:
                self._append_staged_locked(
                    stage,
                    group_id=group_id,
                    action="REBALANCED",
                    worker_id=worker_id,
                    at_ms=now_ms,
                    partition_count=state.partition_count,
                    session_timeout_ms=state.session_timeout_ms,
                    generation=state.generation + 1,
                    assignments=desired,
                )
                state = stage.states[group_id]
            assignment = EventConsumerGroupAssignment(
                group_id=group_id,
                worker_id=worker_id,
                generation=state.generation,
                partition_count=state.partition_count,
                partitions=tuple(
                    index
                    for index, owner in enumerate(state.assignments)
                    if owner == worker_id
                ),
                heartbeat_at_ms=now_ms,
                expires_at_ms=now_ms + state.session_timeout_ms,
            )
            self._tentative = stage
            return assignment
        except BaseException:
            self.discard_staged_locked()
            raise

    def leave_locked(
        self,
        group_id: str,
        *,
        worker_id: str,
        now_ms: int,
    ) -> dict:
        group_id = self._group_identity(group_id)
        declared_worker_id = self._identity(worker_id, name="worker_id")
        worker_id = _coordination_worker_id(declared_worker_id)
        now_ms = self._non_negative_int(now_ms, name="now_ms")
        stage = self.begin_staged_locked()
        try:
            state = stage.states.get(group_id)
            if state is None:
                raise EventConsumerGroupConfigurationError(
                    f"consumer group is not registered: {group_id}"
                )
            if now_ms < state.last_at_ms:
                raise ValueError("consumer-group time must be monotonic")
            latest = state.members.get(worker_id)
            if latest is None:
                legacy = state.members.get(declared_worker_id)
                if legacy is not None and legacy[0] == "HEARTBEAT":
                    worker_id = declared_worker_id
                    latest = legacy
            if latest is None or latest[0] != "HEARTBEAT":
                raise EventConsumerGroupFencedError(
                    f"worker is not an active group member: {worker_id}"
                )
            self._append_staged_locked(
                stage,
                group_id=group_id,
                action="LEFT",
                worker_id=worker_id,
                at_ms=now_ms,
                partition_count=state.partition_count,
                session_timeout_ms=state.session_timeout_ms,
                generation=state.generation,
                assignments=state.assignments,
            )
            state = stage.states[group_id]
            desired = _desired_assignments(state, now_ms)
            if desired != state.assignments:
                self._append_staged_locked(
                    stage,
                    group_id=group_id,
                    action="REBALANCED",
                    worker_id=worker_id,
                    at_ms=now_ms,
                    partition_count=state.partition_count,
                    session_timeout_ms=state.session_timeout_ms,
                    generation=state.generation + 1,
                    assignments=desired,
                )
            self._tentative = stage
            return self.status_locked(group_id, now_ms=now_ms)
        except BaseException:
            self.discard_staged_locked()
            raise

    def status_locked(self, group_id: str, *, now_ms: int) -> dict:
        group_id = self._group_identity(group_id)
        now_ms = self._non_negative_int(now_ms, name="now_ms")
        states = self._tentative.states if self._tentative is not None else self._states
        state = states.get(group_id)
        if state is None:
            raise EventConsumerGroupConfigurationError(
                f"consumer group is not registered: {group_id}"
            )
        active = _active_workers(state, now_ms)
        desired = _desired_assignments(state, now_ms)
        return {
            "group_id": group_id,
            "generation": state.generation,
            "partition_count": state.partition_count,
            "session_timeout_ms": state.session_timeout_ms,
            "active_workers": tuple(_declared_worker_id(item) for item in active),
            "active_worker_identities": tuple(
                {
                    "assurance": _worker_identity_assurance(item),
                    "worker_id": _declared_worker_id(item),
                }
                for item in active
            ),
            "worker_identity_policy": PROCESS_DECLARED_WORKER_ASSURANCE,
            "assignments": tuple(
                _declared_worker_id(item) if item else ""
                for item in state.assignments
            ),
            "desired_assignments": tuple(
                _declared_worker_id(item) if item else ""
                for item in desired
            ),
            "rebalance_required": desired != state.assignments,
            "last_at_ms": state.last_at_ms,
        }

    def require_ownership_locked(
        self,
        assignment: EventConsumerGroupAssignment,
        *,
        partition_id: int,
        now_ms: int,
    ) -> None:
        if not isinstance(assignment, EventConsumerGroupAssignment):
            raise TypeError("assignment must be an EventConsumerGroupAssignment")
        partition_id = self._non_negative_int(partition_id, name="partition_id")
        now_ms = self._non_negative_int(now_ms, name="now_ms")
        state = self._states.get(assignment.group_id)
        if state is None:
            raise EventConsumerGroupFencedError("consumer group is unavailable")
        if now_ms < state.last_at_ms:
            raise EventConsumerGroupFencedError(
                "consumer-group time moved backwards"
            )
        desired = _desired_assignments(state, now_ms)
        if desired != state.assignments:
            raise EventConsumerGroupFencedError(
                "consumer group requires rebalance after membership expiry"
            )
        if (
            assignment.partition_count != state.partition_count
            or assignment.generation != state.generation
            or partition_id >= state.partition_count
            or partition_id not in assignment.partitions
            or state.assignments[partition_id] != assignment.worker_id
            or assignment.worker_id not in _active_workers(state, now_ms)
        ):
            raise EventConsumerGroupFencedError(
                "consumer-group assignment is stale or owned by another worker"
            )

    def partition_count_locked(self, group_id: str) -> int:
        group_id = self._group_identity(group_id)
        state = self._states.get(group_id)
        if state is None:
            raise EventConsumerGroupConfigurationError(
                f"consumer group is not registered: {group_id}"
            )
        return state.partition_count

    @staticmethod
    def event_partition(
        *,
        event_id: str,
        correlation_id: str,
        partition_count: int,
    ) -> int:
        key = correlation_id or event_id
        digest = hashlib.sha256(key.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") % partition_count

    @classmethod
    def partition_consumer_id(
        cls,
        group_id: str,
        *,
        partition_id: int,
        partition_count: int,
    ) -> str:
        group_id = cls._group_identity(group_id)
        if partition_count == 1:
            return group_id
        group_digest = hashlib.sha256(group_id.encode("utf-8")).hexdigest()
        return (
            f"{_PARTITION_CONSUMER_PREFIX}{group_digest}:"
            f"{partition_id}/{partition_count}"
        )


__all__ = [
    "ConsumerGroupCoordinator",
    "EventConsumerGroupAssignment",
    "EventConsumerGroupConfigurationError",
    "EventConsumerGroupError",
    "EventConsumerGroupFencedError",
    "EventConsumerGroupIntegrityError",
    "LEGACY_UNLABELED_WORKER_ASSURANCE",
    "PROCESS_DECLARED_WORKER_ASSURANCE",
]
