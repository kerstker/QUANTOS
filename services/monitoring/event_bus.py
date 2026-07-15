"""Durable append-only event bus with causal replay and tamper evidence.

The bus stores the canonical :class:`quantos.contracts.events.Event` envelope plus
correlation/causation metadata. Every row is chained over the complete event and
bus metadata. Replay verifies the full chain before returning any event.

This remains an offline, synchronous, stdlib-only component. It complements the
governance audit log; it does not grant execution, network, or approval authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

from quantos.contracts.event_schemas import EventSchemaError, EventSchemaRegistry
from quantos.contracts.events import Event

from .consumer_group import (
    ConsumerGroupCoordinator,
    EventConsumerGroupAssignment,
    EventConsumerGroupIntegrityError,
)


class EventBusIntegrityError(RuntimeError):
    """Raised when retained event history cannot be verified."""


@dataclass(frozen=True)
class EventAuthorityCommitment:
    """Portable commitment to all three verified append-only authority chains."""

    event_records: int
    event_max_seq: int
    event_head_hash: str
    consumer_journal_records: int
    consumer_journal_max_seq: int
    consumer_journal_head_hash: str
    coordination_journal_records: int
    coordination_journal_max_seq: int
    coordination_journal_head_hash: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported event authority commitment schema")
        chains = (
            (self.event_records, self.event_max_seq, self.event_head_hash),
            (
                self.consumer_journal_records,
                self.consumer_journal_max_seq,
                self.consumer_journal_head_hash,
            ),
            (
                self.coordination_journal_records,
                self.coordination_journal_max_seq,
                self.coordination_journal_head_hash,
            ),
        )
        for count, max_seq, head_hash in chains:
            if (
                isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                or isinstance(max_seq, bool)
                or not isinstance(max_seq, int)
                or max_seq < 0
            ):
                raise ValueError("commitment counts and sequence values must be non-negative")
            if count == 0 and (max_seq != 0 or head_hash != ""):
                raise ValueError("empty commitment chain must have zero sequence and empty head")
            if count > 0 and (
                max_seq < count
                or not isinstance(head_hash, str)
                or len(head_hash) != 64
                or any(character not in "0123456789abcdef" for character in head_hash)
            ):
                raise ValueError("non-empty commitment chain has an invalid head or sequence")

    def _payload(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "event_records": self.event_records,
            "event_max_seq": self.event_max_seq,
            "event_head_hash": self.event_head_hash,
            "consumer_journal_records": self.consumer_journal_records,
            "consumer_journal_max_seq": self.consumer_journal_max_seq,
            "consumer_journal_head_hash": self.consumer_journal_head_hash,
            "coordination_journal_records": self.coordination_journal_records,
            "coordination_journal_max_seq": self.coordination_journal_max_seq,
            "coordination_journal_head_hash": self.coordination_journal_head_hash,
        }

    @property
    def overall_hash(self) -> str:
        canonical = json.dumps(
            self._payload(),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def to_dict(self) -> dict:
        return {**self._payload(), "overall_hash": self.overall_hash}

    @classmethod
    def from_dict(cls, document: dict) -> "EventAuthorityCommitment":
        if not isinstance(document, dict):
            raise TypeError("commitment document must be a dictionary")
        expected = {
            "schema_version",
            "event_records",
            "event_max_seq",
            "event_head_hash",
            "consumer_journal_records",
            "consumer_journal_max_seq",
            "consumer_journal_head_hash",
            "coordination_journal_records",
            "coordination_journal_max_seq",
            "coordination_journal_head_hash",
            "overall_hash",
        }
        if set(document) != expected:
            raise ValueError("commitment document fields are incomplete or ambiguous")
        overall_hash = document["overall_hash"]
        commitment = cls(
            schema_version=document["schema_version"],
            event_records=document["event_records"],
            event_max_seq=document["event_max_seq"],
            event_head_hash=document["event_head_hash"],
            consumer_journal_records=document["consumer_journal_records"],
            consumer_journal_max_seq=document["consumer_journal_max_seq"],
            consumer_journal_head_hash=document["consumer_journal_head_hash"],
            coordination_journal_records=document["coordination_journal_records"],
            coordination_journal_max_seq=document["coordination_journal_max_seq"],
            coordination_journal_head_hash=document[
                "coordination_journal_head_hash"
            ],
        )
        if overall_hash != commitment.overall_hash:
            raise ValueError("commitment overall hash mismatch")
        return commitment


@dataclass(frozen=True)
class EventDelivery:
    """A leased event delivery that must be acknowledged or failed by its owner."""

    consumer_id: str
    worker_id: str
    event_seq: int
    event: Event
    correlation_id: str
    causation_id: str
    attempt: int
    claim_journal_seq: int
    claimed_at_ms: int
    lease_until_ms: int
    group_id: str = ""
    partition_id: int = 0
    partition_count: int = 1
    group_generation: int = 0


class EventConsumerBusyError(RuntimeError):
    """The next event already has an unexpired delivery lease."""


class EventDeliveryLostError(RuntimeError):
    """A delivery lease expired or was superseded before completion."""


class EventDeliveryError(RuntimeError):
    """A delivery failed and remains eligible for retry."""

    def __init__(
        self,
        delivery: EventDelivery,
        *,
        error_type: str,
        error_fingerprint: str,
    ) -> None:
        self.delivery = delivery
        self.error_type = error_type
        self.error_fingerprint = error_fingerprint
        super().__init__(
            "event delivery failed: "
            f"consumer={delivery.consumer_id} event={delivery.event.event_id} "
            f"attempt={delivery.attempt} error={error_type} "
            f"fingerprint={error_fingerprint}"
        )


class EventDeadLetteredError(EventDeliveryError):
    """A poison event reached its retry limit and was durably quarantined."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE NOT NULL,
  event_type TEXT NOT NULL,
  event_version TEXT NOT NULL,
  timestamp INTEGER NOT NULL,
  source TEXT NOT NULL,
  domain TEXT NOT NULL,
  criticality TEXT NOT NULL,
  correlation_id TEXT NOT NULL,
  causation_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  repro_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
"""


_CONSUMER_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_consumer_journal (
  journal_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  consumer_id TEXT NOT NULL,
  event_seq INTEGER NOT NULL,
  event_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK (
    action IN ('CLAIMED', 'FAILED', 'ACKED', 'DEAD_LETTERED')
  ),
  attempt INTEGER NOT NULL CHECK (attempt >= 1),
  worker_id TEXT NOT NULL,
  at_ms INTEGER NOT NULL CHECK (at_ms >= 0),
  lease_until_ms INTEGER NOT NULL CHECK (lease_until_ms >= 0),
  error_type TEXT NOT NULL,
  error_fingerprint TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL,
  UNIQUE (consumer_id, event_seq, action, attempt)
);
CREATE INDEX IF NOT EXISTS event_consumer_journal_lookup
  ON event_consumer_journal (consumer_id, event_seq, journal_seq);
"""


def _record_hash(
    event: Event,
    *,
    correlation_id: str,
    causation_id: str,
    prev_record_hash: str,
) -> str:
    payload = {
        "event": event.model_dump(mode="json"),
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "prev_record_hash": prev_record_hash,
    }
    canonical = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _consumer_record_hash(
    *,
    consumer_id: str,
    event_seq: int,
    event_id: str,
    action: str,
    attempt: int,
    worker_id: str,
    at_ms: int,
    lease_until_ms: int,
    error_type: str,
    error_fingerprint: str,
    prev_record_hash: str,
) -> str:
    canonical = json.dumps(
        {
            "action": action,
            "at_ms": at_ms,
            "attempt": attempt,
            "consumer_id": consumer_id,
            "error_fingerprint": error_fingerprint,
            "error_type": error_type,
            "event_id": event_id,
            "event_seq": event_seq,
            "lease_until_ms": lease_until_ms,
            "prev_record_hash": prev_record_hash,
            "worker_id": worker_id,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class DurableEventBus:
    """Thread/process-safe append-only event history with deterministic replay."""

    def __init__(
        self,
        db_path: str,
        create: bool = True,
        *,
        schema_registry: Optional[EventSchemaRegistry] = None,
    ):
        if create and db_path != ":memory:":
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._c = sqlite3.connect(db_path, check_same_thread=False)
        self._consumer_groups = ConsumerGroupCoordinator(self._c)
        self._lock = threading.Lock()
        self._subscribers: List[Callable[[Event], None]] = []
        self._head_hash = ""
        self._consumer_head_hash = ""
        self._known_total_changes = 0
        self._known_data_version = 0
        self._schema_registry = schema_registry
        with self._lock:
            self._c.execute("PRAGMA busy_timeout=5000")
            try:
                self._c.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                # A concurrent first opener can hold the lock while switching the
                # file to WAL. Wait on a write reservation, then re-check once.
                self._c.execute("BEGIN IMMEDIATE")
                self._c.rollback()
                self._c.execute("PRAGMA journal_mode=WAL")
            self._c.execute("PRAGMA synchronous=FULL")
            if create:
                self._create_or_migrate_locked()
            self._head_hash = self._verify_rows(self._raw_rows_locked())
            self._consumer_head_hash = self._verify_consumer_rows_locked()
            self._consumer_groups.verify_locked()
            self._record_connection_state_locked()

    def _create_or_migrate_locked(self) -> None:
        try:
            self._c.execute("BEGIN IMMEDIATE")
            self._c.execute(_SCHEMA)
            columns = {
                row[1]
                for row in self._c.execute("PRAGMA table_info(event_log)").fetchall()
            }
            if "prev_record_hash" not in columns:
                self._c.execute(
                    "ALTER TABLE event_log ADD COLUMN prev_record_hash "
                    "TEXT NOT NULL DEFAULT ''"
                )
            if "record_hash" not in columns:
                self._c.execute(
                    "ALTER TABLE event_log ADD COLUMN record_hash "
                    "TEXT NOT NULL DEFAULT ''"
                )

            rows = self._raw_rows_locked()
            if rows:
                empty_chain = [not row[12] and not row[13] for row in rows]
                if all(empty_chain):
                    self._backfill_legacy_chain_locked(rows)
                elif any(empty_chain):
                    raise EventBusIntegrityError(
                        "event history contains a partial chain migration"
                    )
                else:
                    self._verify_rows(rows)

            self._c.execute(
                "CREATE TABLE IF NOT EXISTS event_consumer_journal ("
                " journal_seq INTEGER PRIMARY KEY AUTOINCREMENT,"
                " consumer_id TEXT NOT NULL, event_seq INTEGER NOT NULL,"
                " event_id TEXT NOT NULL, action TEXT NOT NULL CHECK ("
                " action IN ('CLAIMED', 'FAILED', 'ACKED', 'DEAD_LETTERED')),"
                " attempt INTEGER NOT NULL CHECK (attempt >= 1),"
                " worker_id TEXT NOT NULL, at_ms INTEGER NOT NULL CHECK (at_ms >= 0),"
                " lease_until_ms INTEGER NOT NULL CHECK (lease_until_ms >= 0),"
                " error_type TEXT NOT NULL, error_fingerprint TEXT NOT NULL,"
                " prev_record_hash TEXT NOT NULL, record_hash TEXT NOT NULL,"
                " UNIQUE (consumer_id, event_seq, action, attempt))"
            )
            self._c.execute(
                "CREATE INDEX IF NOT EXISTS event_consumer_journal_lookup "
                "ON event_consumer_journal (consumer_id, event_seq, journal_seq)"
            )
            self._consumer_head_hash = self._verify_consumer_rows_locked()
            self._consumer_groups.create_or_migrate_locked()

            for name, timing, table in (
                ("event_log_no_update", "UPDATE", "event_log"),
                ("event_log_no_delete", "DELETE", "event_log"),
                (
                    "event_consumer_journal_no_update",
                    "UPDATE",
                    "event_consumer_journal",
                ),
                (
                    "event_consumer_journal_no_delete",
                    "DELETE",
                    "event_consumer_journal",
                ),
            ):
                self._c.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {timing} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
                )
            self._c.commit()
        except Exception:
            if self._c.in_transaction:
                self._c.rollback()
            raise

    def _backfill_legacy_chain_locked(self, rows: list[tuple]) -> None:
        previous = ""
        for row in rows:
            event = self._event_from_row(row)
            digest = _record_hash(
                event,
                correlation_id=row[8],
                causation_id=row[9],
                prev_record_hash=previous,
            )
            self._c.execute(
                "UPDATE event_log SET prev_record_hash=?, record_hash=? WHERE seq=?",
                (previous, digest, row[0]),
            )
            previous = digest

    def _raw_rows_locked(self) -> list[tuple]:
        try:
            return self._c.execute(
                "SELECT seq, event_id, event_type, event_version, timestamp, source, domain,"
                " criticality, correlation_id, causation_id, payload_json, repro_hash,"
                " prev_record_hash, record_hash FROM event_log ORDER BY seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise EventBusIntegrityError(f"event history unreadable: {exc}") from exc

    def _consumer_rows_locked(self) -> list[tuple]:
        try:
            return self._c.execute(
                "SELECT journal_seq, consumer_id, event_seq, event_id, action, attempt,"
                " worker_id, at_ms, lease_until_ms, error_type, error_fingerprint,"
                " prev_record_hash, record_hash"
                " FROM event_consumer_journal ORDER BY journal_seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise EventBusIntegrityError(
                f"event consumer journal unreadable: {exc}"
            ) from exc

    def _verify_consumer_rows_locked(self) -> str:
        event_ids = {
            row[0]: row[1]
            for row in self._c.execute(
                "SELECT seq, event_id FROM event_log ORDER BY seq"
            ).fetchall()
        }
        previous = ""
        for row in self._consumer_rows_locked():
            if event_ids.get(row[2]) != row[3]:
                raise EventBusIntegrityError(
                    f"consumer journal row {row[0]} references an unknown event"
                )
            if row[11] != previous:
                raise EventBusIntegrityError(
                    f"consumer journal row {row[0]} previous hash mismatch"
                )
            expected = _consumer_record_hash(
                consumer_id=row[1],
                event_seq=row[2],
                event_id=row[3],
                action=row[4],
                attempt=row[5],
                worker_id=row[6],
                at_ms=row[7],
                lease_until_ms=row[8],
                error_type=row[9],
                error_fingerprint=row[10],
                prev_record_hash=previous,
            )
            if row[12] != expected:
                raise EventBusIntegrityError(
                    f"consumer journal row {row[0]} record hash mismatch"
                )
            previous = expected
        return previous

    @staticmethod
    def _event_from_row(row: tuple) -> Event:
        try:
            event = Event(
                event_id=row[1],
                event_type=row[2],
                event_version=row[3],
                timestamp=row[4],
                source=row[5],
                domain=row[6],
                criticality=row[7],
                payload=json.loads(row[10]),
            )
        except Exception as exc:
            raise EventBusIntegrityError(
                f"event row {row[0]} cannot be reconstructed: {type(exc).__name__}"
            ) from exc
        if event.reproducibility_hash != row[11]:
            raise EventBusIntegrityError(f"event row {row[0]} payload hash mismatch")
        return event

    def _verify_rows(self, rows: list[tuple]) -> str:
        previous = ""
        for row in rows:
            event = self._event_from_row(row)
            if self._schema_registry is not None:
                try:
                    self._schema_registry.validate(event)
                except EventSchemaError as exc:
                    raise EventBusIntegrityError(
                        f"event row {row[0]} schema validation failed: {exc}"
                    ) from exc
            if row[12] != previous:
                raise EventBusIntegrityError(f"event row {row[0]} previous hash mismatch")
            expected = _record_hash(
                event,
                correlation_id=row[8],
                causation_id=row[9],
                prev_record_hash=previous,
            )
            if row[13] != expected:
                raise EventBusIntegrityError(f"event row {row[0]} record hash mismatch")
            previous = expected
        return previous

    def _record_connection_state_locked(self) -> None:
        self._known_total_changes = self._c.total_changes
        self._known_data_version = int(
            self._c.execute("PRAGMA data_version").fetchone()[0]
        )

    def _refresh_integrity_locked(self, *, force: bool = False) -> None:
        data_version = int(self._c.execute("PRAGMA data_version").fetchone()[0])
        db_head_row = self._c.execute(
            "SELECT record_hash FROM event_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        db_head = db_head_row[0] if db_head_row else ""
        changed = (
            self._c.total_changes != self._known_total_changes
            or data_version != self._known_data_version
            or db_head != self._head_hash
        )
        if force or changed:
            self._head_hash = self._verify_rows(self._raw_rows_locked())
            self._record_connection_state_locked()

    def _refresh_consumer_integrity_locked(self) -> None:
        self._consumer_head_hash = self._verify_consumer_rows_locked()

    def _refresh_group_integrity_locked(self) -> None:
        self._consumer_groups.verify_locked()

    @staticmethod
    def _require_identity(value: object, *, name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{name} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{name} must be non-empty")
        return normalized

    @staticmethod
    def _require_non_negative_int(value: object, *, name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        if value < 0:
            raise ValueError(f"{name} must be non-negative")
        return value

    @staticmethod
    def _require_positive_int(value: object, *, name: str) -> int:
        checked = DurableEventBus._require_non_negative_int(value, name=name)
        if checked == 0:
            raise ValueError(f"{name} must be positive")
        return checked

    def _append_consumer_record_locked(
        self,
        *,
        consumer_id: str,
        event_seq: int,
        event_id: str,
        action: str,
        attempt: int,
        worker_id: str,
        at_ms: int,
        lease_until_ms: int,
        error_type: str = "",
        error_fingerprint: str = "",
    ) -> int:
        digest = _consumer_record_hash(
            consumer_id=consumer_id,
            event_seq=event_seq,
            event_id=event_id,
            action=action,
            attempt=attempt,
            worker_id=worker_id,
            at_ms=at_ms,
            lease_until_ms=lease_until_ms,
            error_type=error_type,
            error_fingerprint=error_fingerprint,
            prev_record_hash=self._consumer_head_hash,
        )
        cursor = self._c.execute(
            "INSERT INTO event_consumer_journal"
            " (consumer_id,event_seq,event_id,action,attempt,worker_id,at_ms,"
            " lease_until_ms,error_type,error_fingerprint,prev_record_hash,record_hash)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                consumer_id,
                event_seq,
                event_id,
                action,
                attempt,
                worker_id,
                at_ms,
                lease_until_ms,
                error_type,
                error_fingerprint,
                self._consumer_head_hash,
                digest,
            ),
        )
        self._consumer_head_hash = digest
        return int(cursor.lastrowid)

    def _consumer_offset_locked(self, consumer_id: str) -> int:
        row = self._c.execute(
            "SELECT COALESCE(MAX(event_seq), 0) FROM event_consumer_journal"
            " WHERE consumer_id=? AND action IN ('ACKED','DEAD_LETTERED')",
            (consumer_id,),
        ).fetchone()
        return int(row[0])

    def _latest_consumer_record_locked(
        self, consumer_id: str, event_seq: int
    ) -> Optional[tuple]:
        return self._c.execute(
            "SELECT journal_seq, consumer_id, event_seq, event_id, action, attempt,"
            " worker_id, at_ms, lease_until_ms, error_type, error_fingerprint,"
            " prev_record_hash, record_hash FROM event_consumer_journal"
            " WHERE consumer_id=? AND event_seq=? ORDER BY journal_seq DESC LIMIT 1",
            (consumer_id, event_seq),
        ).fetchone()

    def _require_active_delivery_locked(
        self, delivery: EventDelivery, *, at_ms: int
    ) -> tuple:
        if delivery.group_id:
            self._consumer_groups.require_ownership_locked(
                EventConsumerGroupAssignment(
                    group_id=delivery.group_id,
                    worker_id=delivery.worker_id,
                    generation=delivery.group_generation,
                    partition_count=delivery.partition_count,
                    partitions=(delivery.partition_id,),
                    heartbeat_at_ms=delivery.claimed_at_ms,
                    expires_at_ms=delivery.lease_until_ms,
                ),
                partition_id=delivery.partition_id,
                now_ms=at_ms,
            )
        latest = self._latest_consumer_record_locked(
            delivery.consumer_id, delivery.event_seq
        )
        if (
            latest is None
            or latest[0] != delivery.claim_journal_seq
            or latest[3] != delivery.event.event_id
            or latest[4] != "CLAIMED"
            or latest[5] != delivery.attempt
            or latest[6] != delivery.worker_id
            or latest[8] != delivery.lease_until_ms
            or at_ms < delivery.claimed_at_ms
            or at_ms > delivery.lease_until_ms
        ):
            raise EventDeliveryLostError(
                "event delivery lease expired or was superseded"
            )
        return latest

    def _publish(
        self,
        event: Event,
        *,
        correlation_id: str,
        causation_id: str,
        idempotent: bool,
    ) -> Event:
        if not isinstance(event, Event):
            raise TypeError("event_bus.publish requires a quantos.contracts.events.Event")
        if not isinstance(correlation_id, str) or not isinstance(causation_id, str):
            raise TypeError("correlation_id and causation_id must be strings")
        if self._schema_registry is not None:
            self._schema_registry.validate(event)

        subscribers: tuple[Callable[[Event], None], ...] = ()
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._refresh_integrity_locked()
                existing = self._c.execute(
                    "SELECT seq, event_id, event_type, event_version, timestamp, source, domain,"
                    " criticality, correlation_id, causation_id, payload_json, repro_hash,"
                    " prev_record_hash, record_hash FROM event_log WHERE event_id=?",
                    (event.event_id,),
                ).fetchone()
                if existing is not None:
                    if not idempotent:
                        raise ValueError(f"duplicate event_id (replay): {event.event_id}")
                    retained = self._event_from_row(existing)
                    if (
                        retained != event
                        or existing[8] != correlation_id
                        or existing[9] != causation_id
                    ):
                        raise ValueError(
                            f"event_id conflict with retained content: {event.event_id}"
                        )
                    self._c.rollback()
                    self._record_connection_state_locked()
                    return retained

                digest = _record_hash(
                    event,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    prev_record_hash=self._head_hash,
                )
                self._c.execute(
                    "INSERT INTO event_log (event_id, event_type, event_version, timestamp,"
                    " source, domain, criticality, correlation_id, causation_id, payload_json,"
                    " repro_hash, prev_record_hash, record_hash)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event.event_id,
                        event.event_type,
                        event.event_version,
                        event.timestamp,
                        event.source,
                        event.domain,
                        event.criticality,
                        correlation_id,
                        causation_id,
                        json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
                        event.reproducibility_hash,
                        self._head_hash,
                        digest,
                    ),
                )
                self._c.commit()
                self._head_hash = digest
                self._record_connection_state_locked()
                subscribers = tuple(self._subscribers)
            except Exception:
                if self._c.in_transaction:
                    self._c.rollback()
                raise

        for handler in subscribers:
            handler(event)
        return event

    def publish(
        self, event: Event, *, correlation_id: str = "", causation_id: str = ""
    ) -> Event:
        """Persist a new event; a duplicate ID is always rejected."""
        return self._publish(
            event,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotent=False,
        )

    def publish_once(
        self, event: Event, *, correlation_id: str = "", causation_id: str = ""
    ) -> Event:
        """Idempotently retain an identical deterministic event.

        A repeated ID is accepted only when the full canonical event and both
        causal identifiers exactly match the retained row.
        """
        return self._publish(
            event,
            correlation_id=correlation_id,
            causation_id=causation_id,
            idempotent=True,
        )

    def records(
        self,
        *,
        correlation_id: Optional[str] = None,
        since_seq: Optional[int] = None,
    ) -> Tuple[dict, ...]:
        """Verify the complete chain, then return ordered records and causal metadata."""
        with self._lock:
            rows = self._raw_rows_locked()
            self._head_hash = self._verify_rows(rows)
            self._record_connection_state_locked()
            return tuple(
                {
                    "seq": row[0],
                    "event": self._event_from_row(row),
                    "correlation_id": row[8],
                    "causation_id": row[9],
                }
                for row in rows
                if (correlation_id is None or row[8] == correlation_id)
                and (since_seq is None or row[0] > since_seq)
            )

    def replay(
        self,
        *,
        correlation_id: Optional[str] = None,
        since_seq: Optional[int] = None,
    ) -> Tuple[Event, ...]:
        """Verify and replay events in append order."""
        return tuple(
            record["event"]
            for record in self.records(
                correlation_id=correlation_id, since_seq=since_seq
            )
        )

    def heartbeat_consumer_group(
        self,
        group_id: str,
        *,
        worker_id: str,
        now_ms: int,
        partition_count: int,
        session_timeout_ms: int,
    ) -> EventConsumerGroupAssignment:
        """Heartbeat a worker and atomically apply any deterministic rebalance."""
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._refresh_integrity_locked(force=True)
                self._refresh_consumer_integrity_locked()
                self._refresh_group_integrity_locked()
                assignment = self._consumer_groups.heartbeat_locked(
                    group_id,
                    worker_id=worker_id,
                    now_ms=now_ms,
                    partition_count=partition_count,
                    session_timeout_ms=session_timeout_ms,
                )
                self._c.commit()
                self._record_connection_state_locked()
                return assignment
            except Exception:
                if self._c.in_transaction:
                    self._c.rollback()
                raise

    def leave_consumer_group(
        self,
        group_id: str,
        *,
        worker_id: str,
        now_ms: int,
    ) -> dict:
        """Leave a group and atomically rebalance the remaining live workers."""
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._refresh_integrity_locked(force=True)
                self._refresh_consumer_integrity_locked()
                self._refresh_group_integrity_locked()
                status = self._consumer_groups.leave_locked(
                    group_id,
                    worker_id=worker_id,
                    now_ms=now_ms,
                )
                self._c.commit()
                self._record_connection_state_locked()
                return status
            except Exception:
                if self._c.in_transaction:
                    self._c.rollback()
                raise

    def consumer_group_status(self, group_id: str, *, now_ms: int) -> dict:
        """Return verified liveness and assignment state without mutating it."""
        with self._lock:
            self._refresh_integrity_locked(force=True)
            self._refresh_consumer_integrity_locked()
            self._refresh_group_integrity_locked()
            status = self._consumer_groups.status_locked(group_id, now_ms=now_ms)
            self._record_connection_state_locked()
            return status

    def consumer_group_offsets(self, group_id: str) -> tuple[int, ...]:
        """Return durable offsets for every immutable group partition."""
        with self._lock:
            self._refresh_integrity_locked(force=True)
            self._refresh_consumer_integrity_locked()
            self._refresh_group_integrity_locked()
            partition_count = self._consumer_groups.partition_count_locked(group_id)
            offsets = tuple(
                self._consumer_offset_locked(
                    self._consumer_groups.partition_consumer_id(
                        group_id,
                        partition_id=partition_id,
                        partition_count=partition_count,
                    )
                )
                for partition_id in range(partition_count)
            )
            self._record_connection_state_locked()
            return offsets

    def _partition_event_row_locked(
        self,
        *,
        offset: int,
        partition_id: int,
        partition_count: int,
    ) -> Optional[tuple]:
        rows = self._c.execute(
            "SELECT seq, event_id, event_type, event_version, timestamp, source,"
            " domain, criticality, correlation_id, causation_id, payload_json,"
            " repro_hash, prev_record_hash, record_hash FROM event_log"
            " WHERE seq>? ORDER BY seq",
            (offset,),
        ).fetchall()
        for row in rows:
            assigned = self._consumer_groups.event_partition(
                event_id=str(row[1]),
                correlation_id=str(row[8]),
                partition_count=partition_count,
            )
            if assigned == partition_id:
                return row
        return None

    def claim_next(
        self,
        consumer_id: str,
        *,
        worker_id: str,
        now_ms: int,
        lease_ms: int,
    ) -> Optional[EventDelivery]:
        """Lease the next uncheckpointed event for deterministic at-least-once delivery."""
        consumer_id = self._require_identity(consumer_id, name="consumer_id")
        worker_id = self._require_identity(worker_id, name="worker_id")
        now_ms = self._require_non_negative_int(now_ms, name="now_ms")
        lease_ms = self._require_positive_int(lease_ms, name="lease_ms")
        lease_until_ms = now_ms + lease_ms

        event_row: Optional[tuple] = None
        claim_journal_seq = 0
        attempt = 0
        with self._lock:
            previous_consumer_head = self._consumer_head_hash
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._refresh_integrity_locked(force=True)
                self._refresh_consumer_integrity_locked()
                self._refresh_group_integrity_locked()
                previous_consumer_head = self._consumer_head_hash
                offset = self._consumer_offset_locked(consumer_id)
                event_row = self._c.execute(
                    "SELECT seq, event_id, event_type, event_version, timestamp, source,"
                    " domain, criticality, correlation_id, causation_id, payload_json,"
                    " repro_hash, prev_record_hash, record_hash FROM event_log"
                    " WHERE seq>? ORDER BY seq LIMIT 1",
                    (offset,),
                ).fetchone()
                if event_row is None:
                    self._c.rollback()
                    self._record_connection_state_locked()
                    return None

                latest = self._latest_consumer_record_locked(
                    consumer_id, int(event_row[0])
                )
                if latest is not None and latest[4] == "CLAIMED" and now_ms <= latest[8]:
                    raise EventConsumerBusyError(
                        f"consumer {consumer_id} has an active lease for {event_row[1]}"
                    )
                if latest is not None and latest[4] in {"ACKED", "DEAD_LETTERED"}:
                    raise EventBusIntegrityError(
                        "consumer checkpoint does not agree with its next event"
                    )
                if latest is not None and latest[4] not in {"CLAIMED", "FAILED"}:
                    raise EventBusIntegrityError(
                        f"consumer event has invalid latest action: {latest[4]}"
                    )
                attempt = 1 if latest is None else int(latest[5]) + 1
                claim_journal_seq = self._append_consumer_record_locked(
                    consumer_id=consumer_id,
                    event_seq=int(event_row[0]),
                    event_id=str(event_row[1]),
                    action="CLAIMED",
                    attempt=attempt,
                    worker_id=worker_id,
                    at_ms=now_ms,
                    lease_until_ms=lease_until_ms,
                )
                self._c.commit()
                self._record_connection_state_locked()
            except Exception:
                if self._c.in_transaction:
                    self._c.rollback()
                self._consumer_head_hash = previous_consumer_head
                raise

        assert event_row is not None
        return EventDelivery(
            consumer_id=consumer_id,
            worker_id=worker_id,
            event_seq=int(event_row[0]),
            event=self._event_from_row(event_row),
            correlation_id=str(event_row[8]),
            causation_id=str(event_row[9]),
            attempt=attempt,
            claim_journal_seq=claim_journal_seq,
            claimed_at_ms=now_ms,
            lease_until_ms=lease_until_ms,
        )

    def claim_partition(
        self,
        assignment: EventConsumerGroupAssignment,
        partition_id: int,
        *,
        now_ms: int,
        lease_ms: int,
    ) -> Optional[EventDelivery]:
        """Lease the next event in a currently owned, generation-fenced partition."""
        if not isinstance(assignment, EventConsumerGroupAssignment):
            raise TypeError("assignment must be an EventConsumerGroupAssignment")
        partition_id = self._require_non_negative_int(
            partition_id, name="partition_id"
        )
        now_ms = self._require_non_negative_int(now_ms, name="now_ms")
        lease_ms = self._require_positive_int(lease_ms, name="lease_ms")
        lease_until_ms = now_ms + lease_ms
        consumer_id = self._consumer_groups.partition_consumer_id(
            assignment.group_id,
            partition_id=partition_id,
            partition_count=assignment.partition_count,
        )

        event_row: Optional[tuple] = None
        claim_journal_seq = 0
        attempt = 0
        with self._lock:
            previous_consumer_head = self._consumer_head_hash
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._refresh_integrity_locked(force=True)
                self._refresh_consumer_integrity_locked()
                self._refresh_group_integrity_locked()
                previous_consumer_head = self._consumer_head_hash
                self._consumer_groups.require_ownership_locked(
                    assignment,
                    partition_id=partition_id,
                    now_ms=now_ms,
                )
                offset = self._consumer_offset_locked(consumer_id)
                event_row = self._partition_event_row_locked(
                    offset=offset,
                    partition_id=partition_id,
                    partition_count=assignment.partition_count,
                )
                if event_row is None:
                    self._c.rollback()
                    self._record_connection_state_locked()
                    return None

                latest = self._latest_consumer_record_locked(
                    consumer_id, int(event_row[0])
                )
                if latest is not None and latest[4] == "CLAIMED" and now_ms <= latest[8]:
                    raise EventConsumerBusyError(
                        f"consumer {consumer_id} has an active lease for {event_row[1]}"
                    )
                if latest is not None and latest[4] in {"ACKED", "DEAD_LETTERED"}:
                    raise EventBusIntegrityError(
                        "consumer checkpoint does not agree with its next event"
                    )
                if latest is not None and latest[4] not in {"CLAIMED", "FAILED"}:
                    raise EventBusIntegrityError(
                        f"consumer event has invalid latest action: {latest[4]}"
                    )
                attempt = 1 if latest is None else int(latest[5]) + 1
                claim_journal_seq = self._append_consumer_record_locked(
                    consumer_id=consumer_id,
                    event_seq=int(event_row[0]),
                    event_id=str(event_row[1]),
                    action="CLAIMED",
                    attempt=attempt,
                    worker_id=assignment.worker_id,
                    at_ms=now_ms,
                    lease_until_ms=lease_until_ms,
                )
                self._c.commit()
                self._record_connection_state_locked()
            except Exception:
                if self._c.in_transaction:
                    self._c.rollback()
                self._consumer_head_hash = previous_consumer_head
                raise

        assert event_row is not None
        return EventDelivery(
            consumer_id=consumer_id,
            worker_id=assignment.worker_id,
            event_seq=int(event_row[0]),
            event=self._event_from_row(event_row),
            correlation_id=str(event_row[8]),
            causation_id=str(event_row[9]),
            attempt=attempt,
            claim_journal_seq=claim_journal_seq,
            claimed_at_ms=now_ms,
            lease_until_ms=lease_until_ms,
            group_id=assignment.group_id,
            partition_id=partition_id,
            partition_count=assignment.partition_count,
            group_generation=assignment.generation,
        )

    def ack_delivery(self, delivery: EventDelivery, *, at_ms: int) -> EventDelivery:
        """Append an ACK checkpoint when the caller still owns an unexpired lease."""
        if not isinstance(delivery, EventDelivery):
            raise TypeError("delivery must be an EventDelivery")
        at_ms = self._require_non_negative_int(at_ms, name="at_ms")
        with self._lock:
            previous_consumer_head = self._consumer_head_hash
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._refresh_integrity_locked(force=True)
                self._refresh_consumer_integrity_locked()
                self._refresh_group_integrity_locked()
                previous_consumer_head = self._consumer_head_hash
                self._require_active_delivery_locked(delivery, at_ms=at_ms)
                self._append_consumer_record_locked(
                    consumer_id=delivery.consumer_id,
                    event_seq=delivery.event_seq,
                    event_id=delivery.event.event_id,
                    action="ACKED",
                    attempt=delivery.attempt,
                    worker_id=delivery.worker_id,
                    at_ms=at_ms,
                    lease_until_ms=delivery.lease_until_ms,
                )
                self._c.commit()
                self._record_connection_state_locked()
            except Exception:
                if self._c.in_transaction:
                    self._c.rollback()
                self._consumer_head_hash = previous_consumer_head
                raise
        return delivery

    def fail_delivery(
        self,
        delivery: EventDelivery,
        error: Exception,
        *,
        at_ms: int,
        max_attempts: int,
    ) -> None:
        """Record a retryable failure or quarantine a poison event at its limit."""
        if not isinstance(delivery, EventDelivery):
            raise TypeError("delivery must be an EventDelivery")
        if not isinstance(error, Exception):
            raise TypeError("error must be an Exception")
        at_ms = self._require_non_negative_int(at_ms, name="at_ms")
        max_attempts = self._require_positive_int(
            max_attempts, name="max_attempts"
        )
        error_type = type(error).__name__
        error_fingerprint = hashlib.sha256(
            (
                f"{type(error).__module__}.{type(error).__qualname__}:"
                f"{error}"
            ).encode("utf-8", errors="replace")
        ).hexdigest()
        dead_lettered = delivery.attempt >= max_attempts

        with self._lock:
            previous_consumer_head = self._consumer_head_hash
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._refresh_integrity_locked(force=True)
                self._refresh_consumer_integrity_locked()
                self._refresh_group_integrity_locked()
                previous_consumer_head = self._consumer_head_hash
                self._require_active_delivery_locked(delivery, at_ms=at_ms)
                self._append_consumer_record_locked(
                    consumer_id=delivery.consumer_id,
                    event_seq=delivery.event_seq,
                    event_id=delivery.event.event_id,
                    action="DEAD_LETTERED" if dead_lettered else "FAILED",
                    attempt=delivery.attempt,
                    worker_id=delivery.worker_id,
                    at_ms=at_ms,
                    lease_until_ms=delivery.lease_until_ms,
                    error_type=error_type,
                    error_fingerprint=error_fingerprint,
                )
                self._c.commit()
                self._record_connection_state_locked()
            except Exception:
                if self._c.in_transaction:
                    self._c.rollback()
                self._consumer_head_hash = previous_consumer_head
                raise

        error_class = EventDeadLetteredError if dead_lettered else EventDeliveryError
        raise error_class(
            delivery,
            error_type=error_type,
            error_fingerprint=error_fingerprint,
        ) from error

    def consumer_offset(self, consumer_id: str) -> int:
        consumer_id = self._require_identity(consumer_id, name="consumer_id")
        with self._lock:
            self._refresh_integrity_locked(force=True)
            self._refresh_consumer_integrity_locked()
            self._refresh_group_integrity_locked()
            self._record_connection_state_locked()
            return self._consumer_offset_locked(consumer_id)

    def dead_letters(self, *, consumer_id: Optional[str] = None) -> Tuple[dict, ...]:
        if consumer_id is not None:
            consumer_id = self._require_identity(consumer_id, name="consumer_id")
        with self._lock:
            self._refresh_integrity_locked(force=True)
            self._refresh_consumer_integrity_locked()
            self._refresh_group_integrity_locked()
            sql = (
                "SELECT journal_seq, consumer_id, event_seq, event_id, action, attempt,"
                " worker_id, at_ms, lease_until_ms, error_type, error_fingerprint,"
                " prev_record_hash, record_hash FROM event_consumer_journal"
                " WHERE action='DEAD_LETTERED'"
            )
            params: tuple = ()
            if consumer_id is not None:
                sql += " AND consumer_id=?"
                params = (consumer_id,)
            sql += " ORDER BY journal_seq"
            rows = self._c.execute(sql, params).fetchall()
            event_rows = {
                row[0]: row
                for row in self._raw_rows_locked()
                if any(row[0] == dead[2] for dead in rows)
            }
            self._record_connection_state_locked()
            return tuple(
                {
                    "journal_seq": row[0],
                    "consumer_id": row[1],
                    "event_seq": row[2],
                    "event": self._event_from_row(event_rows[row[2]]),
                    "attempt": row[5],
                    "worker_id": row[6],
                    "at_ms": row[7],
                    "error_type": row[9],
                    "error_fingerprint": row[10],
                }
                for row in rows
            )

    def operational_snapshot(self, group_id: str, *, now_ms: int) -> dict:
        """Return verified lag, poison-event, and storage metrics for one group."""
        group_id = self._require_identity(group_id, name="group_id")
        now_ms = self._require_non_negative_int(now_ms, name="now_ms")
        with self._lock:
            self._refresh_integrity_locked(force=True)
            self._refresh_consumer_integrity_locked()
            self._refresh_group_integrity_locked()
            status = self._consumer_groups.status_locked(group_id, now_ms=now_ms)
            partition_count = int(status["partition_count"])
            rows = self._raw_rows_locked()
            partitions = []
            consumer_ids = []
            for partition_id in range(partition_count):
                consumer_id = self._consumer_groups.partition_consumer_id(
                    group_id,
                    partition_id=partition_id,
                    partition_count=partition_count,
                )
                consumer_ids.append(consumer_id)
                offset = self._consumer_offset_locked(consumer_id)
                retained = [
                    row
                    for row in rows
                    if self._consumer_groups.event_partition(
                        event_id=str(row[1]),
                        correlation_id=str(row[8]),
                        partition_count=partition_count,
                    )
                    == partition_id
                ]
                pending = [row for row in retained if int(row[0]) > offset]
                oldest_pending_at_ms = None
                if pending and self._schema_registry is not None:
                    oldest_pending_at_ms = min(
                        self._schema_registry.timestamp_milliseconds(
                            self._event_from_row(row)
                        )
                        for row in pending
                    )
                partitions.append(
                    {
                        "consumer_id": consumer_id,
                        "high_watermark": max(
                            (int(row[0]) for row in retained), default=0
                        ),
                        "offset": offset,
                        "oldest_pending_age_ms": (
                            max(0, now_ms - oldest_pending_at_ms)
                            if oldest_pending_at_ms is not None
                            else None
                        ),
                        "oldest_pending_at_ms": oldest_pending_at_ms,
                        "partition_id": partition_id,
                        "pending_events": len(pending),
                    }
                )

            placeholders = ",".join("?" for _ in consumer_ids)
            dead_letters = int(
                self._c.execute(
                    "SELECT COUNT(*) FROM event_consumer_journal"
                    " WHERE action='DEAD_LETTERED' AND consumer_id IN ("
                    f"{placeholders})",
                    tuple(consumer_ids),
                ).fetchone()[0]
            )
            page_count = int(self._c.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(self._c.execute("PRAGMA page_size").fetchone()[0])
            snapshot = {
                "checked_at_ms": now_ms,
                "group": status,
                "integrity": {
                    "event_chain_valid": True,
                    "consumer_chain_valid": True,
                    "coordination_chain_valid": True,
                },
                "partitions": tuple(partitions),
                "totals": {
                    "allocated_bytes": page_count * page_size,
                    "consumer_journal_records": int(
                        self._c.execute(
                            "SELECT COUNT(*) FROM event_consumer_journal"
                        ).fetchone()[0]
                    ),
                    "coordination_journal_records": int(
                        self._c.execute(
                            "SELECT COUNT(*) FROM event_consumer_group_journal"
                        ).fetchone()[0]
                    ),
                    "dead_letters": dead_letters,
                    "event_records": len(rows),
                    "pending_events": sum(
                        item["pending_events"] for item in partitions
                    ),
                },
            }
            self._record_connection_state_locked()
            return snapshot

    def authority_commitment(self) -> EventAuthorityCommitment:
        """Verify and commit to event, delivery, and coordination chain heads."""
        with self._lock:
            self._refresh_integrity_locked(force=True)
            self._refresh_consumer_integrity_locked()
            self._refresh_group_integrity_locked()

            def _count_and_max(table: str, column: str) -> tuple[int, int]:
                row = self._c.execute(
                    f"SELECT COUNT(*), COALESCE(MAX({column}), 0) FROM {table}"
                ).fetchone()
                return int(row[0]), int(row[1])

            event_count, event_max = _count_and_max("event_log", "seq")
            consumer_count, consumer_max = _count_and_max(
                "event_consumer_journal", "journal_seq"
            )
            coordination_count, coordination_max = _count_and_max(
                "event_consumer_group_journal", "coordination_seq"
            )
            commitment = EventAuthorityCommitment(
                event_records=event_count,
                event_max_seq=event_max,
                event_head_hash=self._head_hash,
                consumer_journal_records=consumer_count,
                consumer_journal_max_seq=consumer_max,
                consumer_journal_head_hash=self._consumer_head_hash,
                coordination_journal_records=coordination_count,
                coordination_journal_max_seq=coordination_max,
                coordination_journal_head_hash=self._consumer_groups.head_hash,
            )
            self._record_connection_state_locked()
            return commitment

    def extends_authority_commitment(
        self, commitment: EventAuthorityCommitment
    ) -> bool:
        """Prove all current verified chains retain the exact anchored prefixes."""
        if not isinstance(commitment, EventAuthorityCommitment):
            raise TypeError("commitment must be an EventAuthorityCommitment")
        with self._lock:
            try:
                self._refresh_integrity_locked(force=True)
                self._refresh_consumer_integrity_locked()
                self._refresh_group_integrity_locked()

                def _extends(
                    table: str,
                    sequence_column: str,
                    hash_column: str,
                    anchored_count: int,
                    anchored_max_seq: int,
                    anchored_head_hash: str,
                ) -> bool:
                    current_count, current_max = self._c.execute(
                        f"SELECT COUNT(*), COALESCE(MAX({sequence_column}), 0) "
                        f"FROM {table}"
                    ).fetchone()
                    if current_count < anchored_count or current_max < anchored_max_seq:
                        return False
                    if anchored_count == 0:
                        return True
                    prefix_count = int(
                        self._c.execute(
                            f"SELECT COUNT(*) FROM {table} "
                            f"WHERE {sequence_column} <= ?",
                            (anchored_max_seq,),
                        ).fetchone()[0]
                    )
                    head_row = self._c.execute(
                        f"SELECT {hash_column} FROM {table} "
                        f"WHERE {sequence_column}=?",
                        (anchored_max_seq,),
                    ).fetchone()
                    return (
                        prefix_count == anchored_count
                        and head_row is not None
                        and head_row[0] == anchored_head_hash
                    )

                valid = all(
                    (
                        _extends(
                            "event_log",
                            "seq",
                            "record_hash",
                            commitment.event_records,
                            commitment.event_max_seq,
                            commitment.event_head_hash,
                        ),
                        _extends(
                            "event_consumer_journal",
                            "journal_seq",
                            "record_hash",
                            commitment.consumer_journal_records,
                            commitment.consumer_journal_max_seq,
                            commitment.consumer_journal_head_hash,
                        ),
                        _extends(
                            "event_consumer_group_journal",
                            "coordination_seq",
                            "record_hash",
                            commitment.coordination_journal_records,
                            commitment.coordination_journal_max_seq,
                            commitment.coordination_journal_head_hash,
                        ),
                    )
                )
                self._record_connection_state_locked()
                return valid
            except (
                EventBusIntegrityError,
                EventConsumerGroupIntegrityError,
                sqlite3.DatabaseError,
            ):
                return False

    def subscribe(self, handler: Callable[[Event], None]) -> None:
        with self._lock:
            self._subscribers.append(handler)

    def count(self) -> int:
        with self._lock:
            return int(self._c.execute("SELECT COUNT(*) FROM event_log").fetchone()[0])

    def chain_valid(self) -> bool:
        with self._lock:
            try:
                self._head_hash = self._verify_rows(self._raw_rows_locked())
                self._consumer_head_hash = self._verify_consumer_rows_locked()
                self._consumer_groups.verify_locked()
                self._record_connection_state_locked()
                return True
            except (
                EventBusIntegrityError,
                EventConsumerGroupIntegrityError,
                sqlite3.DatabaseError,
            ):
                return False

    def consumer_chain_valid(self) -> bool:
        with self._lock:
            try:
                self._head_hash = self._verify_rows(self._raw_rows_locked())
                self._consumer_head_hash = self._verify_consumer_rows_locked()
                self._consumer_groups.verify_locked()
                self._record_connection_state_locked()
                return True
            except (
                EventBusIntegrityError,
                EventConsumerGroupIntegrityError,
                sqlite3.DatabaseError,
            ):
                return False

    def consumer_group_chain_valid(self) -> bool:
        with self._lock:
            try:
                self._head_hash = self._verify_rows(self._raw_rows_locked())
                self._consumer_head_hash = self._verify_consumer_rows_locked()
                self._consumer_groups.verify_locked()
                self._record_connection_state_locked()
                return True
            except (
                EventBusIntegrityError,
                EventConsumerGroupIntegrityError,
                sqlite3.DatabaseError,
            ):
                return False

    @property
    def head_hash(self) -> str:
        with self._lock:
            self._refresh_integrity_locked(force=True)
            return self._head_hash

    def close(self) -> None:
        with self._lock:
            self._c.close()


__all__ = [
    "DurableEventBus",
    "EventAuthorityCommitment",
    "EventBusIntegrityError",
    "EventConsumerBusyError",
    "EventDeadLetteredError",
    "EventDelivery",
    "EventDeliveryError",
    "EventDeliveryLostError",
]
