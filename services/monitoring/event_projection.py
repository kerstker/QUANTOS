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
from services.monitoring.event_bus import (
    ConnectionEpoch,
    EventDelivery,
    VerifiedFrontier,
)


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
        self._epoch: ConnectionEpoch | None = None
        self._frontier: VerifiedFrontier | None = None
        self._failed_closed_reason = ""
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
            self._full_verify_locked()

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

    def _verify_required_schema_locked(self) -> None:
        required = {
            "projection_seq",
            "event_seq",
            "event_id",
            "event_type",
            "event_version",
            "timestamp",
            "source",
            "domain",
            "criticality",
            "correlation_id",
            "causation_id",
            "payload_json",
            "repro_hash",
            "prev_record_hash",
            "record_hash",
        }
        columns = {
            str(row[1])
            for row in self._c.execute(
                "PRAGMA table_info(projected_events)"
            ).fetchall()
        }
        if not required.issubset(columns):
            raise EventProjectionIntegrityError(
                "domain event projection schema is incomplete"
            )
        trigger_rows = self._c.execute(
            "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
        triggers = {
            str(row[0]): (str(row[1]), " ".join(str(row[2] or "").upper().split()))
            for row in trigger_rows
        }
        for name, operation in (
            ("projected_events_no_update", "BEFORE UPDATE"),
            ("projected_events_no_delete", "BEFORE DELETE"),
        ):
            actual = triggers.get(name)
            if (
                actual is None
                or actual[0] != "projected_events"
                or operation not in actual[1]
                or "RAISE(ABORT" not in actual[1]
            ):
                raise EventProjectionIntegrityError(
                    f"projection append-only trigger is missing or drifted: {name}"
                )

    def _tail_locked(self) -> tuple[int, str]:
        row = self._c.execute(
            "SELECT projection_seq,record_hash FROM projected_events "
            "ORDER BY projection_seq DESC LIMIT 1"
        ).fetchone()
        return (int(row[0]), str(row[1])) if row is not None else (0, "")

    def _sentinel_locked(self) -> tuple[int, int, int, tuple[int, str]]:
        data_version = int(self._c.execute("PRAGMA data_version").fetchone()[0])
        schema_version = int(self._c.execute("PRAGMA schema_version").fetchone()[0])
        return self._c.total_changes, data_version, schema_version, self._tail_locked()

    def _invalidate_locked(self) -> None:
        if self._epoch is not None:
            self._epoch = self._epoch.invalidated()
        if self._frontier is not None:
            self._frontier = self._frontier.invalidated()

    def _assert_monotonic_extension_locked(
        self, current: VerifiedFrontier
    ) -> None:
        anchor = self._frontier
        if anchor is None:
            return
        if (
            current.row_count < anchor.row_count
            or current.last_sequence < anchor.last_sequence
        ):
            raise EventProjectionIntegrityError(
                "projection history no longer extends its verified frontier"
            )
        if anchor.row_count == 0:
            return
        prefix_count = int(
            self._c.execute(
                "SELECT COUNT(*) FROM projected_events WHERE projection_seq<=?",
                (anchor.last_sequence,),
            ).fetchone()[0]
        )
        anchor_row = self._c.execute(
            "SELECT record_hash FROM projected_events WHERE projection_seq=?",
            (anchor.last_sequence,),
        ).fetchone()
        if (
            prefix_count != anchor.row_count
            or anchor_row is None
            or str(anchor_row[0]) != anchor.last_record_hash
        ):
            raise EventProjectionIntegrityError(
                "projection history replaced its verified prefix"
            )

    def _full_verify_locked(self) -> None:
        if self._failed_closed_reason:
            raise EventProjectionIntegrityError(self._failed_closed_reason)
        try:
            self._verify_required_schema_locked()
            rows = self._rows_locked()
            head = self._verify_rows(rows)
            current = VerifiedFrontier(
                chain_name="projection",
                last_sequence=int(rows[-1][0]) if rows else 0,
                row_count=len(rows),
                last_record_hash=head,
            )
            self._assert_monotonic_extension_locked(current)
            total_changes, data_version, schema_version, tail = self._sentinel_locked()
            if tail != (current.last_sequence, current.last_record_hash):
                raise EventProjectionIntegrityError(
                    "projection changed during complete verification"
                )
        except BaseException as exc:
            self._invalidate_locked()
            if isinstance(exc, EventProjectionIntegrityError) and (
                "verified frontier" in str(exc) or "verified prefix" in str(exc)
            ):
                self._failed_closed_reason = str(exc)
            raise

        generation = 1 if self._epoch is None else self._epoch.owner_generation + 1
        self._frontier = current
        self._head_hash = current.last_record_hash
        self._epoch = ConnectionEpoch(
            connection_id=id(self._c),
            owner_generation=generation,
            total_changes=total_changes,
            data_version=data_version,
            schema_version=schema_version,
        )

    def _ensure_fresh_locked(self) -> None:
        if self._failed_closed_reason:
            raise EventProjectionIntegrityError(self._failed_closed_reason)
        if self._epoch is None or self._frontier is None:
            self._full_verify_locked()
            return
        try:
            total_changes, data_version, schema_version, tail = self._sentinel_locked()
        except sqlite3.DatabaseError as exc:
            self._invalidate_locked()
            raise EventProjectionIntegrityError(
                f"projection freshness sentinel failed: {exc}"
            ) from exc
        epoch = self._epoch
        frontier = self._frontier
        if (
            epoch.state == "ACTIVE"
            and frontier.state == "ACTIVE"
            and epoch.connection_id == id(self._c)
            and epoch.total_changes == total_changes
            and epoch.data_version == data_version
            and epoch.schema_version == schema_version
            and tail == (frontier.last_sequence, frontier.last_record_hash)
        ):
            return
        self._invalidate_locked()
        self._full_verify_locked()

    def _commit_connection_locked(self) -> None:
        self._c.commit()

    def _commit_staged_locked(self, tentative: VerifiedFrontier) -> None:
        epoch = self._epoch
        frontier = self._frontier
        if (
            epoch is None
            or frontier is None
            or epoch.state != "ACTIVE"
            or frontier.state != "ACTIVE"
        ):
            raise EventProjectionIntegrityError(
                "cannot commit projection against an invalid frontier"
            )
        expected_total_changes = (
            epoch.total_changes + tentative.row_count - frontier.row_count
        )
        if self._c.total_changes != expected_total_changes:
            raise EventProjectionIntegrityError(
                "unexpected same-connection changes preceded projection commit"
            )
        try:
            self._commit_connection_locked()
        except BaseException as exc:
            self._failed_closed_reason = (
                "projection commit outcome is uncertain; reopen is required"
            )
            self._invalidate_locked()
            try:
                if self._c.in_transaction:
                    self._c.rollback()
            except BaseException:
                pass
            raise EventProjectionIntegrityError(self._failed_closed_reason) from exc

        total_changes, data_version, schema_version, tail = self._sentinel_locked()
        if (
            total_changes != expected_total_changes
            or data_version != epoch.data_version
            or schema_version != epoch.schema_version
            or tail != (tentative.last_sequence, tentative.last_record_hash)
        ):
            self._invalidate_locked()
            self._full_verify_locked()
            return
        self._frontier = tentative
        self._head_hash = tentative.last_record_hash
        self._epoch = ConnectionEpoch(
            connection_id=id(self._c),
            owner_generation=epoch.owner_generation + 1,
            total_changes=total_changes,
            data_version=data_version,
            schema_version=schema_version,
        )

    def _abort_transaction_locked(self) -> None:
        try:
            if self._c.in_transaction:
                self._c.rollback()
        except BaseException:
            self._failed_closed_reason = (
                "projection rollback outcome is uncertain; reopen is required"
            )
        finally:
            self._invalidate_locked()

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
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._ensure_fresh_locked()
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
                        assert self._frontier is not None
                        self._commit_staged_locked(self._frontier)
                        return False
                    raise EventProjectionIntegrityError(
                        "projected event id or sequence conflict"
                    )

                frontier = self._frontier
                if frontier is None:
                    raise EventProjectionIntegrityError(
                        "projection frontier is unavailable"
                    )
                digest = _projection_hash(
                    event_seq=delivery.event_seq,
                    event=delivery.event,
                    correlation_id=delivery.correlation_id,
                    causation_id=delivery.causation_id,
                    prev_record_hash=frontier.last_record_hash,
                )
                event = delivery.event
                cursor = self._c.execute(
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
                        frontier.last_record_hash,
                        digest,
                    ),
                )
                tentative = VerifiedFrontier(
                    chain_name="projection",
                    last_sequence=int(cursor.lastrowid),
                    row_count=frontier.row_count + 1,
                    last_record_hash=digest,
                )
                self._commit_staged_locked(tentative)
                return True
            except BaseException:
                self._abort_transaction_locked()
                raise

    def records(self, *, limit: Optional[int] = None) -> Tuple[dict, ...]:
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int):
                raise TypeError("limit must be an integer")
            if limit < 1:
                raise ValueError("limit must be positive")
        with self._lock:
            self._full_verify_locked()
            rows = self._rows_locked()
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
                self._full_verify_locked()
                return True
            except (EventProjectionIntegrityError, sqlite3.DatabaseError):
                return False

    def close(self) -> None:
        with self._lock:
            self._c.close()


__all__ = ["DomainEventProjection", "EventProjectionIntegrityError"]
