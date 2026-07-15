"""Idempotent, append-only read projection for durable domain-event consumers."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from typing import Optional, Tuple

from quantos.contracts.event_schemas import (
    EventSchemaError,
    EventSchemaRegistry,
)
from quantos.contracts.events import Event
from services.monitoring.event_bus import EventDelivery


class EventProjectionIntegrityError(RuntimeError):
    """Projected event history is missing, conflicting, or unverifiable."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS projected_events (
  projection_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_seq INTEGER UNIQUE NOT NULL,
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
CREATE TRIGGER IF NOT EXISTS projected_events_no_update
BEFORE UPDATE ON projected_events
BEGIN SELECT RAISE(ABORT, 'projected_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS projected_events_no_delete
BEFORE DELETE ON projected_events
BEGIN SELECT RAISE(ABORT, 'projected_events is append-only'); END;
"""


def _projection_hash(
    *,
    event_seq: int,
    event: Event,
    correlation_id: str,
    causation_id: str,
    prev_record_hash: str,
) -> str:
    canonical = json.dumps(
        {
            "causation_id": causation_id,
            "correlation_id": correlation_id,
            "event": event.model_dump(mode="json"),
            "event_seq": event_seq,
            "prev_record_hash": prev_record_hash,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class DomainEventProjection:
    """A tamper-evident read model whose apply operation is event-idempotent."""

    def __init__(
        self,
        db_path: str,
        create: bool = True,
        *,
        schema_registry: Optional[EventSchemaRegistry] = None,
        consumer_versions: Optional[dict[str, str]] = None,
    ):
        if not create and (db_path == ":memory:" or not os.path.isfile(db_path)):
            raise EventProjectionIntegrityError(
                f"domain event projection unavailable: {db_path}"
            )
        if create and db_path != ":memory:":
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._c = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        self._head_hash = ""
        self._read_only = not create
        self._schema_registry = schema_registry
        self._consumer_versions = (
            schema_registry.latest_consumer_versions()
            if schema_registry is not None and consumer_versions is None
            else dict(consumer_versions or {})
        )
        with self._lock:
            if create:
                self._c.execute("PRAGMA journal_mode=WAL")
                self._c.execute("PRAGMA synchronous=FULL")
                self._c.executescript(_SCHEMA)
                self._c.commit()
            else:
                self._c.execute("PRAGMA query_only=ON")
            self._head_hash = self._verify_rows(self._rows_locked())

    def _rows_locked(self) -> list[tuple]:
        try:
            return self._c.execute(
                "SELECT projection_seq,event_seq,event_id,event_type,event_version,"
                " timestamp,source,domain,criticality,correlation_id,causation_id,"
                " payload_json,repro_hash,prev_record_hash,record_hash"
                " FROM projected_events ORDER BY projection_seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise EventProjectionIntegrityError(
                f"projected event history unreadable: {exc}"
            ) from exc

    @staticmethod
    def _event_from_row(row: tuple) -> Event:
        try:
            event = Event(
                event_id=row[2],
                event_type=row[3],
                event_version=row[4],
                timestamp=row[5],
                source=row[6],
                domain=row[7],
                criticality=row[8],
                payload=json.loads(row[11]),
            )
        except Exception as exc:
            raise EventProjectionIntegrityError(
                f"projected event row {row[0]} cannot be reconstructed"
            ) from exc
        if event.reproducibility_hash != row[12]:
            raise EventProjectionIntegrityError(
                f"projected event row {row[0]} payload hash mismatch"
            )
        return event

    def _validate_event_schema(self, event: Event) -> None:
        if self._schema_registry is None:
            return
        consumer_version = self._consumer_versions.get(event.event_type)
        if consumer_version is None:
            raise EventProjectionIntegrityError(
                f"projection has no declared consumer version for {event.event_type}"
            )
        try:
            self._schema_registry.validate(
                event,
                consumer_version=consumer_version,
            )
        except EventSchemaError as exc:
            raise EventProjectionIntegrityError(
                f"projected event schema validation failed: {exc}"
            ) from exc

    def _verify_rows(self, rows: list[tuple]) -> str:
        previous = ""
        for row in rows:
            event = self._event_from_row(row)
            self._validate_event_schema(event)
            if row[13] != previous:
                raise EventProjectionIntegrityError(
                    f"projected event row {row[0]} previous hash mismatch"
                )
            expected = _projection_hash(
                event_seq=row[1],
                event=event,
                correlation_id=row[9],
                causation_id=row[10],
                prev_record_hash=previous,
            )
            if row[14] != expected:
                raise EventProjectionIntegrityError(
                    f"projected event row {row[0]} record hash mismatch"
                )
            previous = expected
        return previous

    @classmethod
    def _matches(cls, row: tuple, delivery: EventDelivery) -> bool:
        return (
            row[1] == delivery.event_seq
            and cls._event_from_row(row) == delivery.event
            and row[9] == delivery.correlation_id
            and row[10] == delivery.causation_id
        )

    def apply(self, delivery: EventDelivery) -> bool:
        """Append a new event, or verify and no-op an exact replay."""
        if not isinstance(delivery, EventDelivery):
            raise TypeError("delivery must be an EventDelivery")
        self._validate_event_schema(delivery.event)
        if self._read_only:
            raise PermissionError("read-only domain event projection cannot apply events")
        with self._lock:
            previous_head = self._head_hash
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._head_hash = self._verify_rows(self._rows_locked())
                existing = self._c.execute(
                    "SELECT projection_seq,event_seq,event_id,event_type,event_version,"
                    " timestamp,source,domain,criticality,correlation_id,causation_id,"
                    " payload_json,repro_hash,prev_record_hash,record_hash"
                    " FROM projected_events WHERE event_id=? OR event_seq=?"
                    " ORDER BY projection_seq",
                    (delivery.event.event_id, delivery.event_seq),
                ).fetchall()
                if existing:
                    if len(existing) == 1 and self._matches(existing[0], delivery):
                        self._c.rollback()
                        return False
                    raise EventProjectionIntegrityError(
                        "projected event id or sequence conflict"
                    )

                digest = _projection_hash(
                    event_seq=delivery.event_seq,
                    event=delivery.event,
                    correlation_id=delivery.correlation_id,
                    causation_id=delivery.causation_id,
                    prev_record_hash=self._head_hash,
                )
                event = delivery.event
                self._c.execute(
                    "INSERT INTO projected_events"
                    " (event_seq,event_id,event_type,event_version,timestamp,source,domain,"
                    " criticality,correlation_id,causation_id,payload_json,repro_hash,"
                    " prev_record_hash,record_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        delivery.event_seq,
                        event.event_id,
                        event.event_type,
                        event.event_version,
                        event.timestamp,
                        event.source,
                        event.domain,
                        event.criticality,
                        delivery.correlation_id,
                        delivery.causation_id,
                        json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
                        event.reproducibility_hash,
                        self._head_hash,
                        digest,
                    ),
                )
                self._c.commit()
                self._head_hash = digest
                return True
            except Exception:
                if self._c.in_transaction:
                    self._c.rollback()
                self._head_hash = previous_head
                raise

    def records(self, *, limit: Optional[int] = None) -> Tuple[dict, ...]:
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise TypeError("limit must be an integer")
            if limit < 1:
                raise ValueError("limit must be positive")
        with self._lock:
            rows = self._rows_locked()
            self._head_hash = self._verify_rows(rows)
            if limit is not None:
                rows = rows[-limit:]
            return tuple(
                {
                    "projection_seq": row[0],
                    "event_seq": row[1],
                    "event": self._event_from_row(row),
                    "correlation_id": row[9],
                    "causation_id": row[10],
                }
                for row in rows
            )

    def count(self) -> int:
        with self._lock:
            return int(
                self._c.execute("SELECT COUNT(*) FROM projected_events").fetchone()[0]
            )

    def chain_valid(self) -> bool:
        with self._lock:
            try:
                self._head_hash = self._verify_rows(self._rows_locked())
                return True
            except (EventProjectionIntegrityError, sqlite3.DatabaseError):
                return False

    def close(self) -> None:
        with self._lock:
            self._c.close()


__all__ = ["DomainEventProjection", "EventProjectionIntegrityError"]
