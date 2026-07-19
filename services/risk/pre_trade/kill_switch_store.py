"""Append-only authoritative kill-switch state with a transactional event outbox."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading

from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from quantos.contracts.events import (
    EVENT_KILL_SWITCH_ACTIVATED,
    EVENT_KILL_SWITCH_RESET,
    Event,
    EventOutboxMessage,
)
from quantos.contracts.killswitch import KillSwitchState
from services.risk.pre_trade.events import (
    kill_switch_activated_event,
    kill_switch_activation_event_id,
    kill_switch_reset_event,
)


class KillSwitchStateStoreError(RuntimeError):
    """Authoritative state is missing, conflicting, or unverifiable."""


class KillSwitchStateConflict(KillSwitchStateStoreError):
    """A stale writer or conflicting replay attempted to change the latch."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS kill_switch_transitions (
  transition_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  transition_id TEXT UNIQUE NOT NULL,
  action TEXT NOT NULL CHECK (action IN ('BOOTSTRAP','ARM','RESET')),
  recorded_at INTEGER NOT NULL CHECK (recorded_at >= 0),
  previous_state_hash TEXT NOT NULL,
  state_json TEXT NOT NULL,
  state_hash TEXT NOT NULL,
  event_id TEXT UNIQUE,
  event_json TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  causation_id TEXT NOT NULL,
  event_repro_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kill_switch_event_dispatch (
  dispatch_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE NOT NULL,
  dispatched_at INTEGER NOT NULL CHECK (dispatched_at >= 0),
  event_repro_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS kill_switch_transitions_no_update
BEFORE UPDATE ON kill_switch_transitions
BEGIN SELECT RAISE(ABORT, 'kill_switch_transitions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS kill_switch_transitions_no_delete
BEFORE DELETE ON kill_switch_transitions
BEGIN SELECT RAISE(ABORT, 'kill_switch_transitions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS kill_switch_event_dispatch_no_update
BEFORE UPDATE ON kill_switch_event_dispatch
BEGIN SELECT RAISE(ABORT, 'kill_switch_event_dispatch is append-only'); END;
CREATE TRIGGER IF NOT EXISTS kill_switch_event_dispatch_no_delete
BEFORE DELETE ON kill_switch_event_dispatch
BEGIN SELECT RAISE(ABORT, 'kill_switch_event_dispatch is append-only'); END;
"""


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _state_json(state: KillSwitchState) -> str:
    return _canonical(state.model_dump(mode="json"))


def kill_switch_state_hash(state: KillSwitchState) -> str:
    if not isinstance(state, KillSwitchState):
        raise TypeError("state must be a KillSwitchState")
    return _sha256(_state_json(state))


def kill_switch_authority_path(state_mirror_path: str) -> str:
    """Derive the SQLite authority path used beside a compatibility mirror."""
    if not isinstance(state_mirror_path, str) or not state_mirror_path:
        raise ValueError("state_mirror_path must be a non-empty string")
    root, extension = os.path.splitext(state_mirror_path)
    if extension.lower() == ".json":
        return f"{root}.db"
    return f"{state_mirror_path}.db"


def _event_json(message: EventOutboxMessage | None) -> str:
    return "" if message is None else _canonical(message.event.model_dump(mode="json"))


def _record_hash(
    *,
    transition_id: str,
    action: str,
    recorded_at: int,
    previous_state_hash: str,
    state: KillSwitchState,
    message: EventOutboxMessage | None,
    prev_record_hash: str,
) -> str:
    return _sha256(
        _canonical(
            {
                "action": action,
                "causation_id": "" if message is None else message.causation_id,
                "correlation_id": "" if message is None else message.correlation_id,
                "event": None if message is None else message.event.model_dump(mode="json"),
                "prev_record_hash": prev_record_hash,
                "previous_state_hash": previous_state_hash,
                "recorded_at": recorded_at,
                "state": state.model_dump(mode="json"),
                "transition_id": transition_id,
            }
        )
    )


def _dispatch_hash(
    *,
    event_id: str,
    dispatched_at: int,
    event_repro_hash: str,
    prev_record_hash: str,
) -> str:
    return _sha256(
        _canonical(
            {
                "dispatched_at": dispatched_at,
                "event_id": event_id,
                "event_repro_hash": event_repro_hash,
                "prev_record_hash": prev_record_hash,
            }
        )
    )


class KillSwitchStateStore:
    """Single-node CAS state machine whose event outbox shares each state commit."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise KillSwitchStateStoreError(
                f"kill-switch state store unavailable: {path}"
            )
        if create and path != ":memory:":
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        try:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout=5000")
                if create:
                    try:
                        self._conn.execute("PRAGMA journal_mode=WAL")
                    except sqlite3.OperationalError as exc:
                        if "locked" not in str(exc).lower():
                            raise
                        self._conn.execute("BEGIN IMMEDIATE")
                        self._conn.rollback()
                        self._conn.execute("PRAGMA journal_mode=WAL")
                    self._conn.execute("PRAGMA synchronous=FULL")
                    self._conn.executescript(_SCHEMA)
                    self._conn.commit()
                self._verify_locked()
        except Exception:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            raise

    def _transition_rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(
                "SELECT transition_seq,transition_id,action,recorded_at,"
                " previous_state_hash,state_json,state_hash,event_id,event_json,"
                " correlation_id,causation_id,event_repro_hash,prev_record_hash,"
                " record_hash FROM kill_switch_transitions ORDER BY transition_seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise KillSwitchStateStoreError(
                f"kill-switch transition history unreadable: {exc}"
            ) from exc

    def _dispatch_rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(
                "SELECT dispatch_seq,event_id,dispatched_at,event_repro_hash,"
                " prev_record_hash,record_hash"
                " FROM kill_switch_event_dispatch ORDER BY dispatch_seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise KillSwitchStateStoreError(
                f"kill-switch dispatch history unreadable: {exc}"
            ) from exc

    @staticmethod
    def _state_from_row(row: sqlite3.Row) -> KillSwitchState:
        try:
            raw = json.loads(row["state_json"])
            if row["state_json"] != _canonical(raw):
                raise ValueError("state JSON is non-canonical")
            state = KillSwitchState.model_validate(raw)
        except Exception as exc:
            raise KillSwitchStateStoreError(
                f"kill-switch transition {row['transition_seq']} state is invalid"
            ) from exc
        if kill_switch_state_hash(state) != row["state_hash"]:
            raise KillSwitchStateStoreError(
                f"kill-switch transition {row['transition_seq']} state hash mismatch"
            )
        return state

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> EventOutboxMessage | None:
        if row["event_id"] is None:
            if any(
                row[name]
                for name in (
                    "event_json",
                    "correlation_id",
                    "causation_id",
                    "event_repro_hash",
                )
            ):
                raise KillSwitchStateStoreError(
                    f"kill-switch transition {row['transition_seq']} has partial event data"
                )
            return None
        try:
            raw = json.loads(row["event_json"])
            if row["event_json"] != _canonical(raw):
                raise ValueError("event JSON is non-canonical")
            event = Event.model_validate(raw)
        except Exception as exc:
            raise KillSwitchStateStoreError(
                f"kill-switch transition {row['transition_seq']} event is invalid"
            ) from exc
        if event.event_id != row["event_id"]:
            raise KillSwitchStateStoreError(
                f"kill-switch transition {row['transition_seq']} event id mismatch"
            )
        if event.reproducibility_hash != row["event_repro_hash"]:
            raise KillSwitchStateStoreError(
                f"kill-switch transition {row['transition_seq']} event hash mismatch"
            )
        return EventOutboxMessage(
            event=event,
            correlation_id=row["correlation_id"],
            causation_id=row["causation_id"],
        )

    @staticmethod
    def _validate_semantics(
        previous: KillSwitchState,
        state: KillSwitchState,
        *,
        action: str,
        recorded_at: int,
        message: EventOutboxMessage | None,
        first_record: bool,
    ) -> None:
        if action == "BOOTSTRAP":
            if not first_record:
                raise KillSwitchStateStoreError(
                    "BOOTSTRAP is allowed only as the first record"
                )
            if recorded_at != max(state.armed_at, state.reset_at):
                raise KillSwitchStateStoreError(
                    "BOOTSTRAP timestamp must match the imported state"
                )
            if message is None:
                return
            if (
                not state.tripped
                or state.armed_by is None
                or state.trip_count < 1
                or message.causation_id
                or message.correlation_id != state.incident_id
            ):
                raise KillSwitchStateStoreError(
                    "BOOTSTRAP activation requires a reconstructable tripped state"
                )
            try:
                CANONICAL_EVENT_SCHEMAS.validate(message.event)
            except Exception as exc:
                raise KillSwitchStateStoreError(
                    f"BOOTSTRAP event schema validation failed: {exc}"
                ) from exc
            expected_event = kill_switch_activated_event(
                state,
                actor=state.armed_by,
                reason=state.reason,
                at_ms=recorded_at,
                event_version=message.event.event_version,
            )
            if message.event != expected_event:
                raise KillSwitchStateStoreError(
                    "BOOTSTRAP activation event does not match imported state"
                )
            return
        if message is None:
            raise KillSwitchStateStoreError(f"{action} requires a domain event")
        try:
            CANONICAL_EVENT_SCHEMAS.validate(message.event)
        except Exception as exc:
            raise KillSwitchStateStoreError(
                f"{action} event schema validation failed: {exc}"
            ) from exc
        if CANONICAL_EVENT_SCHEMAS.timestamp_milliseconds(message.event) != recorded_at:
            raise KillSwitchStateStoreError(
                f"{action} event timestamp does not match recorded_at"
            )
        if message.correlation_id != state.incident_id:
            raise KillSwitchStateStoreError(
                f"{action} correlation must equal the incident id"
            )

        if action == "ARM":
            if message.event.event_type != EVENT_KILL_SWITCH_ACTIVATED:
                raise KillSwitchStateStoreError("ARM requires an activated event")
            if previous.tripped:
                raise KillSwitchStateStoreError(
                    "ARM cannot rewrite an existing latched incident"
                )
            if not state.tripped or state.trip_count != previous.trip_count + 1:
                raise KillSwitchStateStoreError(
                    "ARM must latch and increment trip_count exactly once"
                )
            if state.armed_at != recorded_at or state.armed_by is None:
                raise KillSwitchStateStoreError(
                    "ARM state must retain its actor and recorded timestamp"
                )
            if recorded_at < max(previous.armed_at, previous.reset_at):
                raise KillSwitchStateStoreError("ARM timestamp cannot move backward")
            if state.reset_by is not None or state.reset_at != 0:
                raise KillSwitchStateStoreError(
                    "ARM must start a new incident without retained reset fields"
                )
            expected_event = kill_switch_activated_event(
                state,
                actor=state.armed_by,
                reason=state.reason,
                at_ms=recorded_at,
                event_version=message.event.event_version,
            )
            if message.event != expected_event or message.causation_id:
                raise KillSwitchStateStoreError("ARM event does not match state")
            return

        if action == "RESET":
            if message.event.event_type != EVENT_KILL_SWITCH_RESET:
                raise KillSwitchStateStoreError("RESET requires a reset event")
            if not previous.tripped or state.tripped:
                raise KillSwitchStateStoreError(
                    "RESET must clear an existing latched state"
                )
            stable_fields = (
                "scope",
                "scope_value",
                "incident_id",
                "armed_by",
                "reason",
                "armed_at",
                "trip_count",
            )
            if any(getattr(previous, name) != getattr(state, name) for name in stable_fields):
                raise KillSwitchStateStoreError(
                    "RESET cannot rewrite the incident that it clears"
                )
            if (
                state.reset_by is None
                or not state.reset_by.is_human
                or state.reset_at != recorded_at
            ):
                raise KillSwitchStateStoreError(
                    "RESET must retain its human actor and recorded timestamp"
                )
            if recorded_at < previous.armed_at:
                raise KillSwitchStateStoreError("RESET timestamp cannot precede ARM")
            expected_event = kill_switch_reset_event(
                previous,
                actor=state.reset_by,
                reset_via="multi_party",
                at_ms=recorded_at,
                event_version=message.event.event_version,
            )
            if message.event != expected_event:
                raise KillSwitchStateStoreError("RESET event does not match state")
            if message.causation_id != kill_switch_activation_event_id(previous):
                raise KillSwitchStateStoreError(
                    "RESET must causally reference the activation event"
                )
            return

        raise KillSwitchStateStoreError(f"unsupported transition action: {action}")

    def _verify_locked(
        self,
    ) -> tuple[
        str,
        KillSwitchState,
        dict[str, tuple[sqlite3.Row, EventOutboxMessage]],
        str,
    ]:
        previous_record_hash = ""
        current_state = KillSwitchState()
        event_index: dict[str, tuple[sqlite3.Row, EventOutboxMessage]] = {}
        rows = self._transition_rows_locked()
        for index, row in enumerate(rows):
            state = self._state_from_row(row)
            message = self._message_from_row(row)
            if row["previous_state_hash"] != kill_switch_state_hash(current_state):
                raise KillSwitchStateStoreError(
                    f"kill-switch transition {row['transition_seq']} previous state mismatch"
                )
            if row["prev_record_hash"] != previous_record_hash:
                raise KillSwitchStateStoreError(
                    f"kill-switch transition {row['transition_seq']} previous record mismatch"
                )
            self._validate_semantics(
                current_state,
                state,
                action=row["action"],
                recorded_at=row["recorded_at"],
                message=message,
                first_record=index == 0,
            )
            expected = _record_hash(
                transition_id=row["transition_id"],
                action=row["action"],
                recorded_at=row["recorded_at"],
                previous_state_hash=row["previous_state_hash"],
                state=state,
                message=message,
                prev_record_hash=previous_record_hash,
            )
            if row["record_hash"] != expected:
                raise KillSwitchStateStoreError(
                    f"kill-switch transition {row['transition_seq']} record hash mismatch"
                )
            if message is not None:
                if message.event.event_id in event_index:
                    raise KillSwitchStateStoreError(
                        f"duplicate kill-switch event id: {message.event.event_id}"
                    )
                event_index[message.event.event_id] = (row, message)
            current_state = state
            previous_record_hash = expected

        dispatch_head = ""
        for row in self._dispatch_rows_locked():
            retained = event_index.get(row["event_id"])
            if (
                retained is None
                or retained[1].event.reproducibility_hash
                != row["event_repro_hash"]
            ):
                raise KillSwitchStateStoreError(
                    f"kill-switch dispatch {row['dispatch_seq']} reference mismatch"
                )
            if row["prev_record_hash"] != dispatch_head:
                raise KillSwitchStateStoreError(
                    f"kill-switch dispatch {row['dispatch_seq']} previous hash mismatch"
                )
            expected = _dispatch_hash(
                event_id=row["event_id"],
                dispatched_at=row["dispatched_at"],
                event_repro_hash=row["event_repro_hash"],
                prev_record_hash=dispatch_head,
            )
            if row["record_hash"] != expected:
                raise KillSwitchStateStoreError(
                    f"kill-switch dispatch {row['dispatch_seq']} record hash mismatch"
                )
            dispatch_head = expected
        return previous_record_hash, current_state, event_index, dispatch_head

    def load_state(self) -> KillSwitchState:
        with self._lock:
            return self._verify_locked()[1]

    def transition_count(self) -> int:
        with self._lock:
            self._verify_locked()
            return int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM kill_switch_transitions"
                ).fetchone()[0]
            )

    def bootstrap(
        self,
        state: KillSwitchState,
        *,
        recorded_at: int = 0,
        event_outbox: EventOutboxMessage | None = None,
    ) -> bool:
        if not isinstance(state, KillSwitchState):
            raise TypeError("state must be a KillSwitchState")
        return self.append_transition(
            expected_state=KillSwitchState(),
            state=state,
            action="BOOTSTRAP",
            recorded_at=recorded_at,
            event_outbox=event_outbox,
        )

    def append_transition(
        self,
        *,
        expected_state: KillSwitchState,
        state: KillSwitchState,
        action: str,
        recorded_at: int,
        event_outbox: EventOutboxMessage | None,
    ) -> bool:
        if not isinstance(expected_state, KillSwitchState) or not isinstance(
            state, KillSwitchState
        ):
            raise TypeError("expected_state and state must be KillSwitchState values")
        if action not in {"BOOTSTRAP", "ARM", "RESET"}:
            raise ValueError("action must be BOOTSTRAP, ARM, or RESET")
        if (
            isinstance(recorded_at, bool)
            or not isinstance(recorded_at, int)
            or recorded_at < 0
        ):
            raise ValueError("recorded_at must be a non-negative integer")
        if event_outbox is not None and not isinstance(
            event_outbox, EventOutboxMessage
        ):
            raise TypeError("event_outbox must be an EventOutboxMessage or None")
        transition_id = (
            f"bootstrap-{kill_switch_state_hash(state)[:32]}"
            if event_outbox is None
            else event_outbox.event.event_id
        )

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                head, current_state, _, _ = self._verify_locked()
                existing = self._conn.execute(
                    "SELECT transition_seq,transition_id,action,recorded_at,"
                    " previous_state_hash,state_json,state_hash,event_id,event_json,"
                    " correlation_id,causation_id,event_repro_hash,prev_record_hash,"
                    " record_hash FROM kill_switch_transitions WHERE transition_id=?",
                    (transition_id,),
                ).fetchone()
                if existing is not None:
                    retained_state = self._state_from_row(existing)
                    retained_message = self._message_from_row(existing)
                    if (
                        retained_state == state
                        and retained_message == event_outbox
                        and existing["action"] == action
                        and existing["recorded_at"] == recorded_at
                        and current_state == state
                    ):
                        self._conn.rollback()
                        return False
                    raise KillSwitchStateConflict(
                        f"kill-switch transition replay conflict: {transition_id}"
                    )
                if current_state != expected_state:
                    raise KillSwitchStateConflict(
                        "kill-switch state changed since the caller loaded it"
                    )
                self._validate_semantics(
                    expected_state,
                    state,
                    action=action,
                    recorded_at=recorded_at,
                    message=event_outbox,
                    first_record=not bool(self._transition_rows_locked()),
                )
                previous_state_hash = kill_switch_state_hash(expected_state)
                digest = _record_hash(
                    transition_id=transition_id,
                    action=action,
                    recorded_at=recorded_at,
                    previous_state_hash=previous_state_hash,
                    state=state,
                    message=event_outbox,
                    prev_record_hash=head,
                )
                event = None if event_outbox is None else event_outbox.event
                self._conn.execute(
                    "INSERT INTO kill_switch_transitions"
                    " (transition_id,action,recorded_at,previous_state_hash,state_json,"
                    " state_hash,event_id,event_json,correlation_id,causation_id,"
                    " event_repro_hash,prev_record_hash,record_hash)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        transition_id,
                        action,
                        recorded_at,
                        previous_state_hash,
                        _state_json(state),
                        kill_switch_state_hash(state),
                        None if event is None else event.event_id,
                        _event_json(event_outbox),
                        "" if event_outbox is None else event_outbox.correlation_id,
                        "" if event_outbox is None else event_outbox.causation_id,
                        "" if event is None else event.reproducibility_hash,
                        head,
                        digest,
                    ),
                )
                self._conn.commit()
                return True
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def pending_event_outbox(self, *, limit: int = 1_000) -> tuple[dict, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            _, _, event_index, _ = self._verify_locked()
            dispatched = {
                row["event_id"] for row in self._dispatch_rows_locked()
            }
            pending = []
            for event_id, (row, message) in event_index.items():
                if event_id in dispatched:
                    continue
                pending.append(
                    {
                        "outbox_id": event_id,
                        "action": row["action"],
                        "event": message.event,
                        "correlation_id": message.correlation_id,
                        "causation_id": message.causation_id,
                    }
                )
                if len(pending) == limit:
                    break
            return tuple(pending)

    def mark_event_dispatched(
        self,
        event_id: str,
        *,
        dispatched_at: int,
        event_repro_hash: str,
    ) -> bool:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("event_id must be non-empty")
        if (
            isinstance(dispatched_at, bool)
            or not isinstance(dispatched_at, int)
            or dispatched_at < 0
        ):
            raise ValueError("dispatched_at must be a non-negative integer")
        if not isinstance(event_repro_hash, str) or not event_repro_hash:
            raise ValueError("event_repro_hash must be non-empty")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                _, _, event_index, dispatch_head = self._verify_locked()
                retained = event_index.get(event_id)
                if (
                    retained is None
                    or retained[1].event.reproducibility_hash
                    != event_repro_hash
                ):
                    raise KillSwitchStateStoreError(
                        "kill-switch dispatch reference mismatch"
                    )
                existing = self._conn.execute(
                    "SELECT dispatch_seq,event_id,dispatched_at,event_repro_hash,"
                    " prev_record_hash,record_hash"
                    " FROM kill_switch_event_dispatch WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["dispatched_at"] == dispatched_at
                        and existing["event_repro_hash"] == event_repro_hash
                    ):
                        self._conn.rollback()
                        return False
                    raise KillSwitchStateStoreError(
                        "kill-switch dispatch acknowledgement conflict"
                    )
                digest = _dispatch_hash(
                    event_id=event_id,
                    dispatched_at=dispatched_at,
                    event_repro_hash=event_repro_hash,
                    prev_record_hash=dispatch_head,
                )
                self._conn.execute(
                    "INSERT INTO kill_switch_event_dispatch"
                    " (event_id,dispatched_at,event_repro_hash,prev_record_hash,record_hash)"
                    " VALUES (?,?,?,?,?)",
                    (
                        event_id,
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

    def chain_valid(self) -> bool:
        with self._lock:
            try:
                self._verify_locked()
                return True
            except (KillSwitchStateStoreError, sqlite3.DatabaseError):
                return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "KillSwitchStateConflict",
    "KillSwitchStateStore",
    "KillSwitchStateStoreError",
    "kill_switch_authority_path",
    "kill_switch_state_hash",
]
