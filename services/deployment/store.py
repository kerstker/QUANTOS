"""Append-only deployment-evaluation authority with transactional event outbox."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import asdict, dataclass
from enum import Enum

from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from quantos.contracts.events import (
    EVENT_DEPLOYMENT_RECORDED,
    Event,
    EventOutboxMessage,
)
from quantos.contracts.governance import Environment

from .events import deployment_recorded_event


class DeploymentStoreError(RuntimeError):
    """Deployment history is unavailable, conflicting, or unverifiable."""


class DeploymentEvaluationState(str, Enum):
    DENIED = "DENIED"
    ELIGIBLE_NOT_APPLIED = "ELIGIBLE_NOT_APPLIED"


@dataclass(frozen=True)
class DeploymentEvaluation:
    decision_id: str
    environment: Environment
    subject: str
    actor_id: str
    state: DeploymentEvaluationState
    allowed: bool
    reasons: tuple[str, ...]
    version_ids: tuple[str, ...]
    evaluated_at_ms: int
    causation_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.decision_id, str) or not self.decision_id:
            raise ValueError("decision_id must be non-empty")
        if not isinstance(self.environment, Environment):
            raise TypeError("environment must be an Environment")
        if not isinstance(self.subject, str):
            raise TypeError("subject must be a string")
        if not isinstance(self.actor_id, str) or not self.actor_id:
            raise ValueError("actor_id must be non-empty")
        if not isinstance(self.state, DeploymentEvaluationState):
            raise ValueError("state must be a DeploymentEvaluationState")
        if not isinstance(self.allowed, bool):
            raise TypeError("allowed must be a bool")
        if (
            not isinstance(self.reasons, tuple)
            or any(not isinstance(reason, str) or not reason for reason in self.reasons)
        ):
            raise ValueError("reasons must be a tuple of non-empty strings")
        if (
            not isinstance(self.version_ids, tuple)
            or any(not isinstance(version, str) or not version for version in self.version_ids)
        ):
            raise ValueError("version_ids must be a tuple of non-empty strings")
        if (
            isinstance(self.evaluated_at_ms, bool)
            or not isinstance(self.evaluated_at_ms, int)
            or self.evaluated_at_ms < 0
        ):
            raise ValueError("evaluated_at_ms must be a non-negative integer")
        if not isinstance(self.causation_id, str):
            raise TypeError("causation_id must be a string")
        expected_state = (
            DeploymentEvaluationState.ELIGIBLE_NOT_APPLIED
            if self.allowed
            else DeploymentEvaluationState.DENIED
        )
        if self.state is not expected_state:
            raise ValueError("state does not match allowed decision")
        if self.allowed and self.reasons:
            raise ValueError("an allowed decision cannot retain rejection reasons")
        if not self.allowed and not self.reasons:
            raise ValueError("a denied decision must retain rejection reasons")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS deployment_evaluations (
  evaluation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_id TEXT UNIQUE NOT NULL,
  environment TEXT NOT NULL,
  subject TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('DENIED','ELIGIBLE_NOT_APPLIED')),
  allowed INTEGER NOT NULL CHECK (allowed IN (0,1)),
  reasons_json TEXT NOT NULL,
  version_ids_json TEXT NOT NULL,
  evaluated_at_ms INTEGER NOT NULL CHECK (evaluated_at_ms >= 0),
  causation_id TEXT NOT NULL,
  event_json TEXT NOT NULL,
  event_repro_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deployment_event_dispatch (
  dispatch_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE NOT NULL,
  dispatched_at INTEGER NOT NULL CHECK (dispatched_at >= 0),
  event_repro_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS deployment_evaluations_no_update
BEFORE UPDATE ON deployment_evaluations
BEGIN SELECT RAISE(ABORT, 'deployment_evaluations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS deployment_evaluations_no_delete
BEFORE DELETE ON deployment_evaluations
BEGIN SELECT RAISE(ABORT, 'deployment_evaluations is append-only'); END;
CREATE TRIGGER IF NOT EXISTS deployment_event_dispatch_no_update
BEFORE UPDATE ON deployment_event_dispatch
BEGIN SELECT RAISE(ABORT, 'deployment_event_dispatch is append-only'); END;
CREATE TRIGGER IF NOT EXISTS deployment_event_dispatch_no_delete
BEFORE DELETE ON deployment_event_dispatch
BEGIN SELECT RAISE(ABORT, 'deployment_event_dispatch is append-only'); END;
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


def _record_object(record: DeploymentEvaluation) -> dict:
    payload = asdict(record)
    payload["environment"] = record.environment.value
    payload["state"] = record.state.value
    payload["reasons"] = list(record.reasons)
    payload["version_ids"] = list(record.version_ids)
    return payload


def _record_hash(
    record: DeploymentEvaluation,
    message: EventOutboxMessage,
    prev_record_hash: str,
) -> str:
    return _sha256(
        _canonical(
            {
                "causation_id": message.causation_id,
                "correlation_id": message.correlation_id,
                "event": message.event.model_dump(mode="json"),
                "evaluation": _record_object(record),
                "prev_record_hash": prev_record_hash,
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


class DeploymentDecisionStore:
    """Single-node authority for evaluated, but never applied, deployments."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise DeploymentStoreError(f"deployment store unavailable: {path}")
        if create and path != ":memory:":
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
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

    def _evaluation_rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(
                "SELECT evaluation_seq,decision_id,environment,subject,actor_id,"
                " state,allowed,reasons_json,version_ids_json,evaluated_at_ms,"
                " causation_id,event_json,event_repro_hash,prev_record_hash,record_hash"
                " FROM deployment_evaluations ORDER BY evaluation_seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise DeploymentStoreError(
                f"deployment evaluations unreadable: {exc}"
            ) from exc

    def _dispatch_rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(
                "SELECT dispatch_seq,event_id,dispatched_at,event_repro_hash,"
                " prev_record_hash,record_hash FROM deployment_event_dispatch"
                " ORDER BY dispatch_seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise DeploymentStoreError(
                f"deployment dispatch history unreadable: {exc}"
            ) from exc

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> DeploymentEvaluation:
        try:
            reasons = json.loads(row["reasons_json"])
            version_ids = json.loads(row["version_ids_json"])
            if not isinstance(reasons, list) or not isinstance(version_ids, list):
                raise ValueError("reasons and version ids must be lists")
            if row["reasons_json"] != _canonical(reasons):
                raise ValueError("reasons are not canonical")
            if row["version_ids_json"] != _canonical(version_ids):
                raise ValueError("version ids are not canonical")
            return DeploymentEvaluation(
                decision_id=row["decision_id"],
                environment=Environment(row["environment"]),
                subject=row["subject"],
                actor_id=row["actor_id"],
                state=DeploymentEvaluationState(row["state"]),
                allowed=bool(row["allowed"]),
                reasons=tuple(reasons),
                version_ids=tuple(version_ids),
                evaluated_at_ms=row["evaluated_at_ms"],
                causation_id=row["causation_id"],
            )
        except Exception as exc:
            raise DeploymentStoreError(
                f"deployment evaluation {row['evaluation_seq']} cannot be reconstructed"
            ) from exc

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> EventOutboxMessage:
        try:
            event = Event.model_validate_json(row["event_json"])
        except Exception as exc:
            raise DeploymentStoreError(
                f"deployment event {row['decision_id']} cannot be reconstructed"
            ) from exc
        if event.reproducibility_hash != row["event_repro_hash"]:
            raise DeploymentStoreError(
                f"deployment event {row['decision_id']} payload hash mismatch"
            )
        return EventOutboxMessage(
            event=event,
            correlation_id=row["subject"],
            causation_id=row["causation_id"],
        )

    @staticmethod
    def _validate_semantics(
        record: DeploymentEvaluation,
        message: EventOutboxMessage,
    ) -> None:
        try:
            CANONICAL_EVENT_SCHEMAS.validate(message.event)
        except Exception as exc:
            raise DeploymentStoreError("deployment event schema is invalid") from exc
        expected = deployment_recorded_event(
            environment=record.environment.value,
            subject=record.subject,
            actor_id=record.actor_id,
            allowed=record.allowed,
            reasons=record.reasons,
            version_ids=record.version_ids,
            at_ms=record.evaluated_at_ms,
        )
        if (
            message.event.event_type != EVENT_DEPLOYMENT_RECORDED
            or message.event != expected
            or record.decision_id != message.event.event_id
            or message.correlation_id != record.subject
            or message.causation_id != record.causation_id
        ):
            raise DeploymentStoreError(
                "deployment state does not match its canonical event"
            )

    def _verify_locked(
        self,
    ) -> tuple[
        str,
        tuple[DeploymentEvaluation, ...],
        dict[str, EventOutboxMessage],
        str,
    ]:
        previous = ""
        records: list[DeploymentEvaluation] = []
        messages: dict[str, EventOutboxMessage] = {}
        latest_at: dict[tuple[Environment, str], int] = {}
        for row in self._evaluation_rows_locked():
            record = self._record_from_row(row)
            message = self._message_from_row(row)
            self._validate_semantics(record, message)
            key = (record.environment, record.subject)
            if record.evaluated_at_ms < latest_at.get(key, 0):
                raise DeploymentStoreError(
                    "deployment evaluation time moved backwards"
                )
            if row["prev_record_hash"] != previous:
                raise DeploymentStoreError(
                    f"deployment evaluation {row['evaluation_seq']} previous hash mismatch"
                )
            expected = _record_hash(record, message, previous)
            if row["record_hash"] != expected:
                raise DeploymentStoreError(
                    f"deployment evaluation {row['evaluation_seq']} record hash mismatch"
                )
            if message.outbox_id in messages:
                raise DeploymentStoreError(
                    f"duplicate deployment event id: {message.outbox_id}"
                )
            records.append(record)
            messages[message.outbox_id] = message
            latest_at[key] = record.evaluated_at_ms
            previous = expected

        dispatch_previous = ""
        for row in self._dispatch_rows_locked():
            message = messages.get(row["event_id"])
            if (
                message is None
                or message.event.reproducibility_hash
                != row["event_repro_hash"]
            ):
                raise DeploymentStoreError(
                    f"deployment dispatch {row['dispatch_seq']} reference mismatch"
                )
            if row["prev_record_hash"] != dispatch_previous:
                raise DeploymentStoreError(
                    f"deployment dispatch {row['dispatch_seq']} previous hash mismatch"
                )
            expected = _dispatch_hash(
                event_id=row["event_id"],
                dispatched_at=row["dispatched_at"],
                event_repro_hash=row["event_repro_hash"],
                prev_record_hash=dispatch_previous,
            )
            if row["record_hash"] != expected:
                raise DeploymentStoreError(
                    f"deployment dispatch {row['dispatch_seq']} record hash mismatch"
                )
            dispatch_previous = expected
        return previous, tuple(records), messages, dispatch_previous

    def record_evaluation(
        self,
        record: DeploymentEvaluation,
        message: EventOutboxMessage,
    ) -> bool:
        if not isinstance(record, DeploymentEvaluation):
            raise TypeError("record must be a DeploymentEvaluation")
        if not isinstance(message, EventOutboxMessage):
            raise TypeError("message must be an EventOutboxMessage")
        self._validate_semantics(record, message)
        event_json = _canonical(message.event.model_dump(mode="json"))
        reasons_json = _canonical(list(record.reasons))
        versions_json = _canonical(list(record.version_ids))

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                head, records, _, _ = self._verify_locked()
                existing = self._conn.execute(
                    "SELECT evaluation_seq,decision_id,environment,subject,actor_id,"
                    " state,allowed,reasons_json,version_ids_json,evaluated_at_ms,"
                    " causation_id,event_json,event_repro_hash,prev_record_hash,record_hash"
                    " FROM deployment_evaluations WHERE decision_id=?",
                    (record.decision_id,),
                ).fetchone()
                if existing is not None:
                    retained_record = self._record_from_row(existing)
                    retained_message = self._message_from_row(existing)
                    if retained_record == record and retained_message == message:
                        self._conn.rollback()
                        return False
                    raise DeploymentStoreError(
                        f"deployment decision conflict: {record.decision_id}"
                    )
                same_subject = [
                    item
                    for item in records
                    if item.environment is record.environment
                    and item.subject == record.subject
                ]
                if (
                    same_subject
                    and record.evaluated_at_ms < same_subject[-1].evaluated_at_ms
                ):
                    raise DeploymentStoreError(
                        "deployment evaluation time moved backwards"
                    )
                digest = _record_hash(record, message, head)
                self._conn.execute(
                    "INSERT INTO deployment_evaluations"
                    " (decision_id,environment,subject,actor_id,state,allowed,"
                    " reasons_json,version_ids_json,evaluated_at_ms,causation_id,"
                    " event_json,event_repro_hash,prev_record_hash,record_hash)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.decision_id,
                        record.environment.value,
                        record.subject,
                        record.actor_id,
                        record.state.value,
                        int(record.allowed),
                        reasons_json,
                        versions_json,
                        record.evaluated_at_ms,
                        record.causation_id,
                        event_json,
                        message.event.reproducibility_hash,
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

    def evaluations(self) -> tuple[DeploymentEvaluation, ...]:
        with self._lock:
            _, records, _, _ = self._verify_locked()
            return records

    def latest_evaluation(
        self,
        *,
        subject: str | None = None,
        environment: Environment | None = None,
    ) -> DeploymentEvaluation | None:
        if subject is not None and not isinstance(subject, str):
            raise TypeError("subject must be a string or None")
        if environment is not None and not isinstance(environment, Environment):
            raise TypeError("environment must be an Environment or None")
        for record in reversed(self.evaluations()):
            if subject is not None and record.subject != subject:
                continue
            if environment is not None and record.environment is not environment:
                continue
            return record
        return None

    def pending_event_outbox(self, *, limit: int = 1_000) -> tuple[dict, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            _, _, messages, _ = self._verify_locked()
            dispatched = {
                row["event_id"] for row in self._dispatch_rows_locked()
            }
            pending = []
            for event_id, message in messages.items():
                if event_id in dispatched:
                    continue
                pending.append(
                    {
                        "outbox_id": event_id,
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
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                _, _, messages, dispatch_head = self._verify_locked()
                message = messages.get(event_id)
                if (
                    message is None
                    or message.event.reproducibility_hash != event_repro_hash
                ):
                    raise DeploymentStoreError(
                        "deployment dispatch reference mismatch"
                    )
                existing = self._conn.execute(
                    "SELECT dispatch_seq,event_id,dispatched_at,event_repro_hash,"
                    " prev_record_hash,record_hash FROM deployment_event_dispatch"
                    " WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["dispatched_at"] == dispatched_at
                        and existing["event_repro_hash"] == event_repro_hash
                    ):
                        self._conn.rollback()
                        return False
                    raise DeploymentStoreError(
                        "deployment dispatch acknowledgement conflict"
                    )
                digest = _dispatch_hash(
                    event_id=event_id,
                    dispatched_at=dispatched_at,
                    event_repro_hash=event_repro_hash,
                    prev_record_hash=dispatch_head,
                )
                self._conn.execute(
                    "INSERT INTO deployment_event_dispatch"
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
            except (DeploymentStoreError, sqlite3.DatabaseError):
                return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "DeploymentDecisionStore",
    "DeploymentEvaluation",
    "DeploymentEvaluationState",
    "DeploymentStoreError",
]
