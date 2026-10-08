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
from pathlib import Path
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
class VerifiedFrontier:
    """Ephemeral trust anchor for one completely verified append-only chain."""

    chain_name: str
    last_sequence: int
    row_count: int
    last_record_hash: str
    state: str = "ACTIVE"

    def invalidated(self) -> "VerifiedFrontier":
        return VerifiedFrontier(
            chain_name=self.chain_name,
            last_sequence=self.last_sequence,
            row_count=self.row_count,
            last_record_hash=self.last_record_hash,
            state="INVALID",
        )


@dataclass(frozen=True)
class ConnectionEpoch:
    """Connection-global freshness facts associated with all owned frontiers."""

    connection_id: int
    owner_generation: int
    total_changes: int
    data_version: int
    schema_version: int
    state: str = "ACTIVE"

    def invalidated(self) -> "ConnectionEpoch":
        return ConnectionEpoch(
            connection_id=self.connection_id,
            owner_generation=self.owner_generation,
            total_changes=self.total_changes,
            data_version=self.data_version,
            schema_version=self.schema_version,
            state="INVALID",
        )


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

    _PARTITION_FETCH_CHUNK_SIZE = 256
    _CHAIN_SPECS = {
        "event": ("event_log", "seq"),
        "consumer": ("event_consumer_journal", "journal_seq"),
        "consumer_group": (
            "event_consumer_group_journal",
            "coordination_seq",
        ),
    }
    _REQUIRED_TRIGGERS = frozenset(
        {
            "event_log_no_update",
            "event_log_no_delete",
            "event_consumer_journal_no_update",
            "event_consumer_journal_no_delete",
            "event_consumer_group_journal_no_update",
            "event_consumer_group_journal_no_delete",
        }
    )
    _REQUIRED_INDEXES = frozenset(
        {
            "event_consumer_journal_lookup",
            "event_consumer_group_lookup",
        }
    )

    def __init__(
        self,
        db_path: str,
        create: bool = True,
        *,
        schema_registry: Optional[EventSchemaRegistry] = None,
        read_only: bool = False,
        immutable_read_only: bool = False,
    ):
        if not isinstance(read_only, bool):
            raise TypeError("read_only must be a bool")
        if read_only and create:
            raise ValueError("read_only event authority cannot be created")
        if not isinstance(immutable_read_only, bool):
            raise TypeError("immutable_read_only must be a bool")
        if immutable_read_only and not read_only:
            raise ValueError("immutable_read_only requires read_only")
        if create and db_path != ":memory:":
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        connection_target = db_path
        connection_uri = False
        if read_only:
            if db_path == ":memory:":
                raise ValueError("in-memory event authority cannot be read-only")
            query = "mode=ro&immutable=1" if immutable_read_only else "mode=ro"
            connection_target = f"{Path(db_path).resolve().as_uri()}?{query}"
            connection_uri = True
        self._c = sqlite3.connect(
            connection_target,
            check_same_thread=False,
            timeout=5.0,
            uri=connection_uri,
        )
        self._consumer_groups = ConsumerGroupCoordinator(self._c)
        self._lock = threading.Lock()
        self._subscribers: List[Callable[[Event], None]] = []
        self._head_hash = ""
        self._consumer_head_hash = ""
        self._known_total_changes = 0
        self._known_data_version = 0
        self._epoch: ConnectionEpoch | None = None
        self._frontiers: dict[str, VerifiedFrontier] = {}
        self._failed_closed_reason = ""
        self._schema_registry = schema_registry
        with self._lock:
            self._c.execute("PRAGMA busy_timeout=5000")
            if read_only:
                self._c.execute("PRAGMA query_only=ON")
            else:
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
            self._full_verify_all_locked()

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
        """Retain legacy diagnostics without advancing trusted frontiers."""
        self._known_total_changes = self._c.total_changes
        data_version = int(self._c.execute("PRAGMA data_version").fetchone()[0])
        self._known_data_version = data_version

    def _verify_required_schema_locked(self) -> None:
        required_columns = {
            "event_log": {
                "seq",
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
            },
            "event_consumer_journal": {
                "journal_seq",
                "consumer_id",
                "event_seq",
                "event_id",
                "action",
                "attempt",
                "worker_id",
                "at_ms",
                "lease_until_ms",
                "error_type",
                "error_fingerprint",
                "prev_record_hash",
                "record_hash",
            },
            "event_consumer_group_journal": {
                "coordination_seq",
                "group_id",
                "action",
                "worker_id",
                "at_ms",
                "partition_count",
                "session_timeout_ms",
                "generation",
                "assignments_json",
                "prev_record_hash",
                "record_hash",
            },
        }
        for table, expected in required_columns.items():
            actual = {
                str(row[1])
                for row in self._c.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if not expected.issubset(actual):
                raise EventBusIntegrityError(
                    f"event authority schema is missing required columns for {table}"
                )

        schema_rows = self._c.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE type IN ('trigger','index')"
        ).fetchall()
        triggers = {
            str(row[1]): (str(row[2]), " ".join(str(row[3] or "").upper().split()))
            for row in schema_rows
            if row[0] == "trigger"
        }
        indexes = {str(row[1]) for row in schema_rows if row[0] == "index"}
        expected_trigger_shape = {
            "event_log_no_update": ("event_log", "BEFORE UPDATE"),
            "event_log_no_delete": ("event_log", "BEFORE DELETE"),
            "event_consumer_journal_no_update": (
                "event_consumer_journal",
                "BEFORE UPDATE",
            ),
            "event_consumer_journal_no_delete": (
                "event_consumer_journal",
                "BEFORE DELETE",
            ),
            "event_consumer_group_journal_no_update": (
                "event_consumer_group_journal",
                "BEFORE UPDATE",
            ),
            "event_consumer_group_journal_no_delete": (
                "event_consumer_group_journal",
                "BEFORE DELETE",
            ),
        }
        for name, (table, operation) in expected_trigger_shape.items():
            actual = triggers.get(name)
            if (
                actual is None
                or actual[0] != table
                or operation not in actual[1]
                or "RAISE(ABORT" not in actual[1]
            ):
                error_class = (
                    EventConsumerGroupIntegrityError
                    if name.startswith("event_consumer_group_")
                    else EventBusIntegrityError
                )
                raise error_class(
                    f"event authority append-only trigger is missing or drifted: {name}"
                )
        if not self._REQUIRED_INDEXES.issubset(indexes):
            raise EventBusIntegrityError("event authority lookup indexes are incomplete")

    def _chain_tail_locked(self, chain_name: str) -> tuple[int, str]:
        table, sequence_column = self._CHAIN_SPECS[chain_name]
        row = self._c.execute(
            f"SELECT {sequence_column},record_hash FROM {table} "
            f"ORDER BY {sequence_column} DESC LIMIT 1"
        ).fetchone()
        return (int(row[0]), str(row[1])) if row is not None else (0, "")

    def _sentinel_locked(
        self,
    ) -> tuple[int, int, int, dict[str, tuple[int, str]]]:
        data_version = int(self._c.execute("PRAGMA data_version").fetchone()[0])
        schema_version = int(self._c.execute("PRAGMA schema_version").fetchone()[0])
        tails = {
            chain_name: self._chain_tail_locked(chain_name)
            for chain_name in self._CHAIN_SPECS
        }
        return self._c.total_changes, data_version, schema_version, tails

    def _invalidate_frontiers_locked(self) -> None:
        if self._epoch is not None:
            self._epoch = self._epoch.invalidated()
        self._frontiers = {
            name: frontier.invalidated()
            for name, frontier in self._frontiers.items()
        }
        self._consumer_groups.discard_staged_locked()

    def _assert_monotonic_extension_locked(
        self,
        chain_name: str,
        current: VerifiedFrontier,
    ) -> None:
        anchor = self._frontiers.get(chain_name)
        if anchor is None:
            return
        if (
            current.row_count < anchor.row_count
            or current.last_sequence < anchor.last_sequence
        ):
            raise EventBusIntegrityError(
                f"{chain_name} history no longer extends its verified frontier"
            )
        if anchor.row_count == 0:
            return
        table, sequence_column = self._CHAIN_SPECS[chain_name]
        prefix_count = int(
            self._c.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {sequence_column}<=?",
                (anchor.last_sequence,),
            ).fetchone()[0]
        )
        anchor_row = self._c.execute(
            f"SELECT record_hash FROM {table} WHERE {sequence_column}=?",
            (anchor.last_sequence,),
        ).fetchone()
        if (
            prefix_count != anchor.row_count
            or anchor_row is None
            or str(anchor_row[0]) != anchor.last_record_hash
        ):
            raise EventBusIntegrityError(
                f"{chain_name} history replaced its verified prefix"
            )

    def _full_verify_all_locked(self) -> None:
        """Completely verify every owned chain and atomically re-anchor it."""
        if self._failed_closed_reason:
            raise EventBusIntegrityError(self._failed_closed_reason)
        try:
            self._verify_required_schema_locked()
            event_rows = self._raw_rows_locked()
            event_head = self._verify_rows(event_rows)
            consumer_head = self._verify_consumer_rows_locked()
            consumer_count, consumer_max = self._c.execute(
                "SELECT COUNT(*),COALESCE(MAX(journal_seq),0) "
                "FROM event_consumer_journal"
            ).fetchone()
            self._consumer_groups.verify_locked()
            current = {
                "event": VerifiedFrontier(
                    "event",
                    int(event_rows[-1][0]) if event_rows else 0,
                    len(event_rows),
                    event_head,
                ),
                "consumer": VerifiedFrontier(
                    "consumer",
                    int(consumer_max),
                    int(consumer_count),
                    consumer_head,
                ),
                "consumer_group": VerifiedFrontier(
                    "consumer_group",
                    self._consumer_groups.last_sequence,
                    self._consumer_groups.row_count,
                    self._consumer_groups.head_hash,
                ),
            }
            for name, frontier in current.items():
                self._assert_monotonic_extension_locked(name, frontier)
            total_changes, data_version, schema_version, tails = self._sentinel_locked()
            if any(
                tails[name]
                != (frontier.last_sequence, frontier.last_record_hash)
                for name, frontier in current.items()
            ):
                raise EventBusIntegrityError(
                    "event authority changed during complete verification"
                )
        except BaseException as exc:
            self._invalidate_frontiers_locked()
            if isinstance(exc, EventBusIntegrityError) and (
                "verified frontier" in str(exc) or "verified prefix" in str(exc)
            ):
                self._failed_closed_reason = str(exc)
            raise

        generation = 1 if self._epoch is None else self._epoch.owner_generation + 1
        self._frontiers = current
        self._head_hash = current["event"].last_record_hash
        self._consumer_head_hash = current["consumer"].last_record_hash
        self._epoch = ConnectionEpoch(
            connection_id=id(self._c),
            owner_generation=generation,
            total_changes=total_changes,
            data_version=data_version,
            schema_version=schema_version,
        )
        self._known_total_changes = total_changes
        self._known_data_version = data_version

    def _ensure_fresh_locked(self) -> None:
        if self._failed_closed_reason:
            raise EventBusIntegrityError(self._failed_closed_reason)
        if self._epoch is None or len(self._frontiers) != len(self._CHAIN_SPECS):
            self._full_verify_all_locked()
            return
        try:
            total_changes, data_version, schema_version, tails = self._sentinel_locked()
        except sqlite3.DatabaseError as exc:
            self._invalidate_frontiers_locked()
            raise EventBusIntegrityError(
                f"event authority freshness sentinel failed: {exc}"
            ) from exc
        epoch = self._epoch
        warm = (
            epoch.state == "ACTIVE"
            and epoch.connection_id == id(self._c)
            and epoch.total_changes == total_changes
            and epoch.data_version == data_version
            and epoch.schema_version == schema_version
            and all(
                frontier.state == "ACTIVE"
                and tails[name]
                == (frontier.last_sequence, frontier.last_record_hash)
                for name, frontier in self._frontiers.items()
            )
        )
        if warm:
            return
        self._invalidate_frontiers_locked()
        self._full_verify_all_locked()

    def _refresh_integrity_locked(self, *, force: bool = False) -> None:
        # ``force`` remains a compatibility argument; explicit audit APIs call
        # the complete verifier directly. Runtime operations always use the
        # constant-size freshness sentinel.
        del force
        self._ensure_fresh_locked()

    def _refresh_consumer_integrity_locked(self) -> None:
        # Event-bus freshness is connection-global and verifies all three chains.
        return None

    def _refresh_group_integrity_locked(self) -> None:
        # Event-bus freshness is connection-global and verifies all three chains.
        return None

    def _stage_frontier_locked(
        self,
        chain_name: str,
        *,
        last_sequence: int,
        last_record_hash: str,
        row_increment: int,
    ) -> VerifiedFrontier:
        frontier = self._frontiers[chain_name]
        if frontier.state != "ACTIVE" or self._epoch is None or self._epoch.state != "ACTIVE":
            raise EventBusIntegrityError("cannot append against an invalid frontier")
        if row_increment < 0 or last_sequence < frontier.last_sequence:
            raise EventBusIntegrityError("tentative frontier moves backwards")
        return VerifiedFrontier(
            chain_name=chain_name,
            last_sequence=last_sequence,
            row_count=frontier.row_count + row_increment,
            last_record_hash=last_record_hash,
        )

    def _commit_connection_locked(self) -> None:
        """Commit seam kept small so failure-path tests can inject uncertainty."""
        self._c.commit()

    def _commit_staged_locked(
        self,
        staged: dict[str, VerifiedFrontier],
        *,
        promote_group: bool = False,
    ) -> None:
        epoch = self._epoch
        if epoch is None or epoch.state != "ACTIVE":
            raise EventBusIntegrityError("cannot commit against an invalid epoch")
        expected = dict(self._frontiers)
        expected.update(staged)
        logical_inserts = sum(
            expected[name].row_count - self._frontiers[name].row_count
            for name in expected
        )
        expected_total_changes = epoch.total_changes + logical_inserts
        if self._c.total_changes != expected_total_changes:
            raise EventBusIntegrityError(
                "unexpected same-connection changes preceded event authority commit"
            )
        try:
            self._commit_connection_locked()
        except BaseException as exc:
            self._failed_closed_reason = (
                "event authority commit outcome is uncertain; reopen is required"
            )
            self._invalidate_frontiers_locked()
            try:
                if self._c.in_transaction:
                    self._c.rollback()
            except BaseException:
                pass
            raise EventBusIntegrityError(self._failed_closed_reason) from exc

        total_changes, data_version, schema_version, tails = self._sentinel_locked()
        postcommit_matches = (
            total_changes == expected_total_changes
            and data_version == epoch.data_version
            and schema_version == epoch.schema_version
            and all(
                tails[name]
                == (frontier.last_sequence, frontier.last_record_hash)
                for name, frontier in expected.items()
            )
        )
        if not postcommit_matches:
            self._invalidate_frontiers_locked()
            self._full_verify_all_locked()
            return

        self._frontiers = expected
        self._head_hash = expected["event"].last_record_hash
        self._consumer_head_hash = expected["consumer"].last_record_hash
        if promote_group:
            self._consumer_groups.promote_staged_locked()
        self._epoch = ConnectionEpoch(
            connection_id=id(self._c),
            owner_generation=epoch.owner_generation + 1,
            total_changes=total_changes,
            data_version=data_version,
            schema_version=schema_version,
        )
        self._known_total_changes = total_changes
        self._known_data_version = data_version

    def _abort_transaction_locked(self) -> None:
        try:
            if self._c.in_transaction:
                self._c.rollback()
        except BaseException:
            self._failed_closed_reason = (
                "event authority rollback outcome is uncertain; reopen is required"
            )
        finally:
            self._invalidate_frontiers_locked()

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
        prev_record_hash: str | None = None,
    ) -> tuple[int, str]:
        frontier = self._frontiers["consumer"]
        previous = (
            frontier.last_record_hash
            if prev_record_hash is None
            else prev_record_hash
        )
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
            prev_record_hash=previous,
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
                previous,
                digest,
            ),
        )
        return int(cursor.lastrowid), digest

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
                    self._commit_staged_locked({})
                    return retained

                event_frontier = self._frontiers["event"]
                digest = _record_hash(
                    event,
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                    prev_record_hash=event_frontier.last_record_hash,
                )
                cursor = self._c.execute(
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
                        event_frontier.last_record_hash,
                        digest,
                    ),
                )
                staged = self._stage_frontier_locked(
                    "event",
                    last_sequence=int(cursor.lastrowid),
                    last_record_hash=digest,
                    row_increment=1,
                )
                self._commit_staged_locked({"event": staged})
                subscribers = tuple(self._subscribers)
            except BaseException:
                self._abort_transaction_locked()
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
                tentative = self._consumer_groups.tentative_frontier_locked()
                if tentative is None:
                    raise EventBusIntegrityError(
                        "consumer-group heartbeat did not stage a frontier"
                    )
                sequence, row_count, head_hash = tentative
                group_frontier = self._frontiers["consumer_group"]
                staged = self._stage_frontier_locked(
                    "consumer_group",
                    last_sequence=sequence,
                    last_record_hash=head_hash,
                    row_increment=row_count - group_frontier.row_count,
                )
                self._commit_staged_locked(
                    {"consumer_group": staged}, promote_group=True
                )
                return assignment
            except BaseException:
                self._abort_transaction_locked()
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
                tentative = self._consumer_groups.tentative_frontier_locked()
                if tentative is None:
                    raise EventBusIntegrityError(
                        "consumer-group leave did not stage a frontier"
                    )
                sequence, row_count, head_hash = tentative
                group_frontier = self._frontiers["consumer_group"]
                staged = self._stage_frontier_locked(
                    "consumer_group",
                    last_sequence=sequence,
                    last_record_hash=head_hash,
                    row_increment=row_count - group_frontier.row_count,
                )
                self._commit_staged_locked(
                    {"consumer_group": staged}, promote_group=True
                )
                return status
            except BaseException:
                self._abort_transaction_locked()
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
        if partition_count == 1:
            return self._c.execute(
                "SELECT seq, event_id, event_type, event_version, timestamp, source,"
                " domain, criticality, correlation_id, causation_id, payload_json,"
                " repro_hash, prev_record_hash, record_hash FROM event_log"
                " WHERE seq>? ORDER BY seq LIMIT 1",
                (offset,),
            ).fetchone()

        cursor = self._c.execute(
            "SELECT seq, event_id, event_type, event_version, timestamp, source,"
            " domain, criticality, correlation_id, causation_id, payload_json,"
            " repro_hash, prev_record_hash, record_hash FROM event_log"
            " WHERE seq>? ORDER BY seq",
            (offset,),
        )
        while True:
            rows = cursor.fetchmany(self._PARTITION_FETCH_CHUNK_SIZE)
            if not rows:
                break
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
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._refresh_integrity_locked()
                self._refresh_consumer_integrity_locked()
                self._refresh_group_integrity_locked()
                offset = self._consumer_offset_locked(consumer_id)
                event_row = self._c.execute(
                    "SELECT seq, event_id, event_type, event_version, timestamp, source,"
                    " domain, criticality, correlation_id, causation_id, payload_json,"
                    " repro_hash, prev_record_hash, record_hash FROM event_log"
                    " WHERE seq>? ORDER BY seq LIMIT 1",
                    (offset,),
                ).fetchone()
                if event_row is None:
                    self._commit_staged_locked({})
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
                claim_journal_seq, consumer_head = self._append_consumer_record_locked(
                    consumer_id=consumer_id,
                    event_seq=int(event_row[0]),
                    event_id=str(event_row[1]),
                    action="CLAIMED",
                    attempt=attempt,
                    worker_id=worker_id,
                    at_ms=now_ms,
                    lease_until_ms=lease_until_ms,
                )
                staged = self._stage_frontier_locked(
                    "consumer",
                    last_sequence=claim_journal_seq,
                    last_record_hash=consumer_head,
                    row_increment=1,
                )
                self._commit_staged_locked({"consumer": staged})
            except BaseException:
                self._abort_transaction_locked()
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
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._refresh_integrity_locked()
                self._refresh_consumer_integrity_locked()
                self._refresh_group_integrity_locked()
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
                    self._commit_staged_locked({})
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
                claim_journal_seq, consumer_head = self._append_consumer_record_locked(
                    consumer_id=consumer_id,
                    event_seq=int(event_row[0]),
                    event_id=str(event_row[1]),
                    action="CLAIMED",
                    attempt=attempt,
                    worker_id=assignment.worker_id,
                    at_ms=now_ms,
                    lease_until_ms=lease_until_ms,
                )
                staged = self._stage_frontier_locked(
                    "consumer",
                    last_sequence=claim_journal_seq,
                    last_record_hash=consumer_head,
                    row_increment=1,
                )
                self._commit_staged_locked({"consumer": staged})
            except BaseException:
                self._abort_transaction_locked()
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
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._refresh_integrity_locked()
                self._refresh_consumer_integrity_locked()
                self._refresh_group_integrity_locked()
                self._require_active_delivery_locked(delivery, at_ms=at_ms)
                consumer_sequence, consumer_head = self._append_consumer_record_locked(
                    consumer_id=delivery.consumer_id,
                    event_seq=delivery.event_seq,
                    event_id=delivery.event.event_id,
                    action="ACKED",
                    attempt=delivery.attempt,
                    worker_id=delivery.worker_id,
                    at_ms=at_ms,
                    lease_until_ms=delivery.lease_until_ms,
                )
                staged = self._stage_frontier_locked(
                    "consumer",
                    last_sequence=consumer_sequence,
                    last_record_hash=consumer_head,
                    row_increment=1,
                )
                self._commit_staged_locked({"consumer": staged})
            except BaseException:
                self._abort_transaction_locked()
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
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._refresh_integrity_locked()
                self._refresh_consumer_integrity_locked()
                self._refresh_group_integrity_locked()
                self._require_active_delivery_locked(delivery, at_ms=at_ms)
                consumer_sequence, consumer_head = self._append_consumer_record_locked(
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
                staged = self._stage_frontier_locked(
                    "consumer",
                    last_sequence=consumer_sequence,
                    last_record_hash=consumer_head,
                    row_increment=1,
                )
                self._commit_staged_locked({"consumer": staged})
            except BaseException:
                self._abort_transaction_locked()
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
                " AND NOT EXISTS ("
                "   SELECT 1 FROM event_consumer_journal newer"
                "   WHERE newer.consumer_id=event_consumer_journal.consumer_id"
                "     AND newer.event_seq=event_consumer_journal.event_seq"
                "     AND newer.journal_seq>event_consumer_journal.journal_seq"
                " )"
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
                    " AS dead WHERE action='DEAD_LETTERED' AND consumer_id IN ("
                    f"{placeholders})"
                    " AND NOT EXISTS ("
                    "   SELECT 1 FROM event_consumer_journal newer"
                    "   WHERE newer.consumer_id=dead.consumer_id"
                    "     AND newer.event_seq=dead.event_seq"
                    "     AND newer.journal_seq>dead.journal_seq"
                    " )",
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
            self._full_verify_all_locked()

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
                self._full_verify_all_locked()

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
                self._full_verify_all_locked()
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
                self._full_verify_all_locked()
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
                self._full_verify_all_locked()
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
