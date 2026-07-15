"""Transactional maintenance snapshots with an append-only domain-event outbox."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Sequence

from quantos.contracts.events import Event, EventOutboxMessage


class MaintenanceStoreError(RuntimeError):
    """Maintenance history is missing, conflicting, or unverifiable."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS maintenance_records (
  record_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT UNIQUE NOT NULL,
  generated_at INTEGER NOT NULL,
  snapshot_json TEXT NOT NULL,
  snapshot_hash TEXT NOT NULL,
  events_json TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS maintenance_event_dispatch (
  dispatch_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE NOT NULL,
  run_id TEXT NOT NULL,
  dispatched_at INTEGER NOT NULL,
  event_repro_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS maintenance_records_no_update
BEFORE UPDATE ON maintenance_records
BEGIN SELECT RAISE(ABORT, 'maintenance_records is append-only'); END;
CREATE TRIGGER IF NOT EXISTS maintenance_records_no_delete
BEFORE DELETE ON maintenance_records
BEGIN SELECT RAISE(ABORT, 'maintenance_records is append-only'); END;
CREATE TRIGGER IF NOT EXISTS maintenance_event_dispatch_no_update
BEFORE UPDATE ON maintenance_event_dispatch
BEGIN SELECT RAISE(ABORT, 'maintenance_event_dispatch is append-only'); END;
CREATE TRIGGER IF NOT EXISTS maintenance_event_dispatch_no_delete
BEFORE DELETE ON maintenance_event_dispatch
BEGIN SELECT RAISE(ABORT, 'maintenance_event_dispatch is append-only'); END;
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


def _message_object(message: EventOutboxMessage) -> dict:
    return {
        "causation_id": message.causation_id,
        "correlation_id": message.correlation_id,
        "event": message.event.model_dump(mode="json"),
    }


def _record_hash(
    *,
    run_id: str,
    generated_at: int,
    snapshot_json: str,
    events_json: str,
    prev_record_hash: str,
) -> str:
    return _sha256(
        _canonical(
            {
                "events": json.loads(events_json),
                "generated_at": generated_at,
                "prev_record_hash": prev_record_hash,
                "run_id": run_id,
                "snapshot": json.loads(snapshot_json),
            }
        )
    )


def _dispatch_hash(
    *,
    event_id: str,
    run_id: str,
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
                "run_id": run_id,
            }
        )
    )


class MaintenanceStore:
    def __init__(self, path: str, *, create: bool = False) -> None:
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise MaintenanceStoreError(f"maintenance store unavailable: {path}")
        if create and path != ":memory:":
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            if create:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
            self._verify_locked()

    def _record_rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(
                "SELECT record_seq,run_id,generated_at,snapshot_json,snapshot_hash,"
                " events_json,prev_record_hash,record_hash"
                " FROM maintenance_records ORDER BY record_seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise MaintenanceStoreError(
                f"maintenance records unreadable: {exc}"
            ) from exc

    def _dispatch_rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(
                "SELECT dispatch_seq,event_id,run_id,dispatched_at,event_repro_hash,"
                " prev_record_hash,record_hash"
                " FROM maintenance_event_dispatch ORDER BY dispatch_seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise MaintenanceStoreError(
                f"maintenance dispatch history unreadable: {exc}"
            ) from exc

    @staticmethod
    def _messages_from_row(row: sqlite3.Row) -> tuple[EventOutboxMessage, ...]:
        try:
            raw = json.loads(row["events_json"])
            messages = tuple(
                EventOutboxMessage(
                    event=Event.model_validate(item["event"]),
                    correlation_id=item["correlation_id"],
                    causation_id=item["causation_id"],
                )
                for item in raw
            )
        except Exception as exc:
            raise MaintenanceStoreError(
                f"maintenance record {row['record_seq']} events cannot be reconstructed"
            ) from exc
        return messages

    def _verify_locked(
        self,
    ) -> tuple[str, dict[str, tuple[str, EventOutboxMessage]], str]:
        previous = ""
        event_index: dict[str, tuple[str, EventOutboxMessage]] = {}
        for row in self._record_rows_locked():
            try:
                snapshot = json.loads(row["snapshot_json"])
                events_payload = json.loads(row["events_json"])
            except Exception as exc:
                raise MaintenanceStoreError(
                    f"maintenance record {row['record_seq']} snapshot is invalid"
                ) from exc
            if row["snapshot_json"] != _canonical(snapshot):
                raise MaintenanceStoreError(
                    f"maintenance record {row['record_seq']} snapshot is non-canonical"
                )
            if row["events_json"] != _canonical(events_payload):
                raise MaintenanceStoreError(
                    f"maintenance record {row['record_seq']} events are non-canonical"
                )
            if _sha256(_canonical(snapshot)) != row["snapshot_hash"]:
                raise MaintenanceStoreError(
                    f"maintenance record {row['record_seq']} snapshot hash mismatch"
                )
            if snapshot.get("generated_at") != row["generated_at"]:
                raise MaintenanceStoreError(
                    f"maintenance record {row['record_seq']} timestamp mismatch"
                )
            if row["prev_record_hash"] != previous:
                raise MaintenanceStoreError(
                    f"maintenance record {row['record_seq']} previous hash mismatch"
                )
            expected = _record_hash(
                run_id=row["run_id"],
                generated_at=row["generated_at"],
                snapshot_json=row["snapshot_json"],
                events_json=row["events_json"],
                prev_record_hash=previous,
            )
            if expected != row["record_hash"]:
                raise MaintenanceStoreError(
                    f"maintenance record {row['record_seq']} record hash mismatch"
                )
            for message in self._messages_from_row(row):
                if message.event.event_id in event_index:
                    raise MaintenanceStoreError(
                        f"duplicate maintenance event id: {message.event.event_id}"
                    )
                event_index[message.event.event_id] = (row["run_id"], message)
            previous = expected

        dispatch_previous = ""
        for row in self._dispatch_rows_locked():
            retained = event_index.get(row["event_id"])
            if (
                retained is None
                or retained[0] != row["run_id"]
                or retained[1].event.reproducibility_hash
                != row["event_repro_hash"]
            ):
                raise MaintenanceStoreError(
                    f"maintenance dispatch {row['dispatch_seq']} reference mismatch"
                )
            if row["prev_record_hash"] != dispatch_previous:
                raise MaintenanceStoreError(
                    f"maintenance dispatch {row['dispatch_seq']} previous hash mismatch"
                )
            expected = _dispatch_hash(
                event_id=row["event_id"],
                run_id=row["run_id"],
                dispatched_at=row["dispatched_at"],
                event_repro_hash=row["event_repro_hash"],
                prev_record_hash=dispatch_previous,
            )
            if expected != row["record_hash"]:
                raise MaintenanceStoreError(
                    f"maintenance dispatch {row['dispatch_seq']} record hash mismatch"
                )
            dispatch_previous = expected
        return previous, event_index, dispatch_previous

    def save_snapshot(
        self,
        snapshot: dict,
        event_outbox: Sequence[EventOutboxMessage] = (),
    ) -> bool:
        if not isinstance(snapshot, dict):
            raise TypeError("snapshot must be a dict")
        generated_at = snapshot.get("generated_at")
        if (
            isinstance(generated_at, bool)
            or not isinstance(generated_at, int)
            or generated_at < 0
        ):
            raise ValueError("snapshot generated_at must be a non-negative integer")
        messages = tuple(event_outbox)
        if any(not isinstance(message, EventOutboxMessage) for message in messages):
            raise TypeError("event_outbox must contain EventOutboxMessage values")
        run_id = f"alpha-maintenance:{generated_at}"
        snapshot_json = _canonical(snapshot)
        events_json = _canonical([_message_object(message) for message in messages])

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                head, _, _ = self._verify_locked()
                existing = self._conn.execute(
                    "SELECT record_seq,run_id,generated_at,snapshot_json,snapshot_hash,"
                    " events_json,prev_record_hash,record_hash"
                    " FROM maintenance_records WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["snapshot_json"] == snapshot_json
                        and existing["events_json"] == events_json
                    ):
                        self._conn.rollback()
                        return False
                    raise MaintenanceStoreError(
                        f"maintenance run conflict: {run_id}"
                    )
                digest = _record_hash(
                    run_id=run_id,
                    generated_at=generated_at,
                    snapshot_json=snapshot_json,
                    events_json=events_json,
                    prev_record_hash=head,
                )
                self._conn.execute(
                    "INSERT INTO maintenance_records"
                    " (run_id,generated_at,snapshot_json,snapshot_hash,events_json,"
                    " prev_record_hash,record_hash) VALUES (?,?,?,?,?,?,?)",
                    (
                        run_id,
                        generated_at,
                        snapshot_json,
                        _sha256(snapshot_json),
                        events_json,
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

    def latest_snapshot(self) -> dict | None:
        with self._lock:
            self._verify_locked()
            row = self._conn.execute(
                "SELECT snapshot_json FROM maintenance_records"
                " ORDER BY record_seq DESC LIMIT 1"
            ).fetchone()
            return json.loads(row["snapshot_json"]) if row is not None else None

    def pending_event_outbox(self, *, limit: int = 1_000) -> tuple[dict, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            _, event_index, _ = self._verify_locked()
            dispatched = {
                row["event_id"] for row in self._dispatch_rows_locked()
            }
            pending = []
            for event_id, (run_id, message) in event_index.items():
                if event_id in dispatched:
                    continue
                pending.append(
                    {
                        "outbox_id": event_id,
                        "run_id": run_id,
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
                _, event_index, dispatch_head = self._verify_locked()
                retained = event_index.get(event_id)
                if (
                    retained is None
                    or retained[1].event.reproducibility_hash
                    != event_repro_hash
                ):
                    raise MaintenanceStoreError(
                        "maintenance dispatch reference mismatch"
                    )
                existing = self._conn.execute(
                    "SELECT dispatch_seq,event_id,run_id,dispatched_at,event_repro_hash,"
                    " prev_record_hash,record_hash"
                    " FROM maintenance_event_dispatch WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["dispatched_at"] == dispatched_at
                        and existing["event_repro_hash"] == event_repro_hash
                    ):
                        self._conn.rollback()
                        return False
                    raise MaintenanceStoreError(
                        "maintenance dispatch acknowledgement conflict"
                    )
                digest = _dispatch_hash(
                    event_id=event_id,
                    run_id=retained[0],
                    dispatched_at=dispatched_at,
                    event_repro_hash=event_repro_hash,
                    prev_record_hash=dispatch_head,
                )
                self._conn.execute(
                    "INSERT INTO maintenance_event_dispatch"
                    " (event_id,run_id,dispatched_at,event_repro_hash,"
                    " prev_record_hash,record_hash) VALUES (?,?,?,?,?,?)",
                    (
                        event_id,
                        retained[0],
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
            except (MaintenanceStoreError, sqlite3.DatabaseError):
                return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = ["MaintenanceStore", "MaintenanceStoreError"]
