"""Append-only authority for identity registries and authorization decisions.

The store has no token verifier, signer, network, broker, deployment, execution,
or live-trading capability.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Callable

from .identity_authorization import (
    IdentityAuthorizationDecision,
    IdentityAuthorizationRegistry,
    IdentityAuthorizationRegistryError,
    load_identity_authorization_registry,
    validate_registry_successor,
)


IDENTITY_AUTHORIZATION_STORE_KIND = "QUANTOS_IDENTITY_AUTHORIZATION_AUTHORITY"
IDENTITY_AUTHORIZATION_STORE_SCHEMA_VERSION = 1
IDENTITY_AUTHORIZATION_MAX_FUTURE_SKEW_MS = 5 * 60_000
_REGISTRY_EVENT_KIND = "REGISTRY_SNAPSHOT"
_DECISION_EVENT_KIND = "AUTHORIZATION_DECISION"
_TRIGGERS = frozenset(
    {
        "identity_authorization_meta_no_update",
        "identity_authorization_meta_no_delete",
        "identity_authorization_events_no_update",
        "identity_authorization_events_no_delete",
        "identity_authorization_registry_snapshots_no_update",
        "identity_authorization_registry_snapshots_no_delete",
        "identity_authorization_decisions_no_update",
        "identity_authorization_decisions_no_delete",
    }
)


def _local_clock_ms() -> Callable[[], int]:
    wall_ms = time.time_ns() // 1_000_000
    monotonic_ns = time.monotonic_ns()

    def current() -> int:
        return wall_ms + (time.monotonic_ns() - monotonic_ns) // 1_000_000

    return current


class IdentityAuthorizationStoreError(RuntimeError):
    """The authority is unavailable, conflicting, or unverifiable."""


class IdentityAuthorizationStoreConflict(IdentityAuthorizationStoreError):
    """A decision id already binds a different immutable record."""


class IdentityAuthorizationStoreClockRollback(IdentityAuthorizationStoreError):
    """A proposed event precedes retained authority history."""


class IdentityAuthorizationStoreFutureClock(IdentityAuthorizationStoreError):
    reason_code = "IDENTITY_AUTHORIZATION_AUTHORITY_FUTURE_CLOCK"

    def __init__(self, *, recorded_at_ms: int, authority_now_ms: int) -> None:
        super().__init__(self.reason_code)
        self.recorded_at_ms = recorded_at_ms
        self.authority_now_ms = authority_now_ms


class _RegistryStateError(IdentityAuthorizationStoreError):
    def __init__(
        self,
        reason_code: str,
        *,
        expected_head: IdentityAuthorizationRegistryHead | None = None,
        actual_head: IdentityAuthorizationRegistryHead | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.expected_head = expected_head
        self.actual_head = actual_head


class IdentityAuthorizationStoreRegistryUnprovisioned(_RegistryStateError):
    pass


class IdentityAuthorizationStoreRegistryHeadMismatch(_RegistryStateError):
    pass


class IdentityAuthorizationStoreRegistryHeadChanged(_RegistryStateError):
    pass


class IdentityAuthorizationStoreRegistryRollback(_RegistryStateError):
    pass


class IdentityAuthorizationStoreRegistryConflict(_RegistryStateError):
    pass


class IdentityAuthorizationStoreRegistryGenerationGap(_RegistryStateError):
    pass


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_time(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("recorded_at_ms must be a non-negative integer")
    return value


def _registry(content: bytes) -> IdentityAuthorizationRegistry:
    if not isinstance(content, bytes) or not content:
        raise ValueError("registry_bytes must be non-empty bytes")
    return load_identity_authorization_registry(content)


def _registry_payload_sha256(generation: int, digest: str) -> str:
    return _sha256_text(_canonical({"generation": generation, "registry_sha256": digest}))


def _decision_json(decision: IdentityAuthorizationDecision) -> str:
    if not isinstance(decision, IdentityAuthorizationDecision):
        raise TypeError("decision must be an IdentityAuthorizationDecision")
    return _canonical(decision.model_dump(mode="json"))


def _decision_payload_sha256(
    *,
    decision_id: str,
    recorded_at_ms: int,
    registry_generation: int,
    registry_sha256: str,
    registry_event_seq: int,
    registry_event_hash: str,
    decision_json: str,
    decision_sha256: str,
) -> str:
    return _sha256_text(
        _canonical(
            {
                "decision": json.loads(decision_json),
                "decision_id": decision_id,
                "decision_sha256": decision_sha256,
                "recorded_at_ms": recorded_at_ms,
                "registry_event_hash": registry_event_hash,
                "registry_event_seq": registry_event_seq,
                "registry_generation": registry_generation,
                "registry_sha256": registry_sha256,
            }
        )
    )


def _event_hash(
    *,
    event_seq: int,
    event_kind: str,
    event_id: str,
    recorded_at_ms: int,
    payload_sha256: str,
    previous_event_hash: str,
) -> str:
    return _sha256_text(
        _canonical(
            {
                "event_id": event_id,
                "event_kind": event_kind,
                "event_seq": event_seq,
                "payload_sha256": payload_sha256,
                "previous_event_hash": previous_event_hash,
                "recorded_at_ms": recorded_at_ms,
            }
        )
    )


@dataclass(frozen=True)
class IdentityAuthorizationRegistryHead:
    generation: int
    registry_sha256: str
    registry_bytes: bytes
    authority_event_seq: int
    recorded_at_ms: int
    previous_event_hash: str
    event_hash: str


@dataclass(frozen=True)
class IdentityAuthorizationRecord:
    authority_event_seq: int
    decision_id: str
    recorded_at_ms: int
    registry_generation: int
    registry_sha256: str
    registry_event_seq: int
    registry_event_hash: str
    decision_sha256: str
    decision: IdentityAuthorizationDecision
    previous_event_hash: str
    event_hash: str


@dataclass(frozen=True)
class IdentityAuthorizationAuthoritySnapshot:
    registry_head: IdentityAuthorizationRegistryHead | None
    registry_snapshots: tuple[IdentityAuthorizationRegistryHead, ...]
    decisions: tuple[IdentityAuthorizationRecord, ...]
    authority_head_hash: str
    authority_time_floor_ms: int | None


_SCHEMA = f"""
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS identity_authorization_meta (
  singleton INTEGER PRIMARY KEY CHECK (singleton=1),
  store_kind TEXT NOT NULL,
  schema_version INTEGER NOT NULL CHECK (
    schema_version={IDENTITY_AUTHORIZATION_STORE_SCHEMA_VERSION}
  )
);
INSERT OR IGNORE INTO identity_authorization_meta
  (singleton,store_kind,schema_version)
VALUES (
  1,
  '{IDENTITY_AUTHORIZATION_STORE_KIND}',
  {IDENTITY_AUTHORIZATION_STORE_SCHEMA_VERSION}
);
CREATE TABLE IF NOT EXISTS identity_authorization_events (
  event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_kind TEXT NOT NULL CHECK (
    event_kind IN ('REGISTRY_SNAPSHOT','AUTHORIZATION_DECISION')
  ),
  event_id TEXT UNIQUE NOT NULL CHECK (length(event_id) BETWEEN 1 AND 255),
  recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms>=0),
  payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256)=64),
  previous_event_hash TEXT NOT NULL CHECK (
    previous_event_hash='' OR length(previous_event_hash)=64
  ),
  event_hash TEXT UNIQUE NOT NULL CHECK (length(event_hash)=64)
);
CREATE TABLE IF NOT EXISTS identity_authorization_registry_snapshots (
  authority_event_seq INTEGER PRIMARY KEY,
  generation INTEGER UNIQUE NOT NULL CHECK (generation>=0),
  registry_sha256 TEXT NOT NULL CHECK (length(registry_sha256)=64),
  registry_bytes BLOB NOT NULL CHECK (length(registry_bytes)>0),
  FOREIGN KEY(authority_event_seq)
    REFERENCES identity_authorization_events(event_seq)
    ON UPDATE RESTRICT ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS identity_authorization_decisions (
  decision_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  authority_event_seq INTEGER UNIQUE NOT NULL,
  decision_id TEXT UNIQUE NOT NULL CHECK (length(decision_id) BETWEEN 1 AND 255),
  recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms>=0),
  registry_generation INTEGER NOT NULL CHECK (registry_generation>=0),
  registry_sha256 TEXT NOT NULL CHECK (length(registry_sha256)=64),
  registry_event_seq INTEGER NOT NULL CHECK (registry_event_seq>0),
  registry_event_hash TEXT NOT NULL CHECK (length(registry_event_hash)=64),
  decision_json TEXT NOT NULL,
  decision_sha256 TEXT NOT NULL CHECK (length(decision_sha256)=64),
  FOREIGN KEY(authority_event_seq)
    REFERENCES identity_authorization_events(event_seq)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  FOREIGN KEY(registry_event_seq)
    REFERENCES identity_authorization_registry_snapshots(authority_event_seq)
    ON UPDATE RESTRICT ON DELETE RESTRICT
);
CREATE TRIGGER IF NOT EXISTS identity_authorization_meta_no_update
BEFORE UPDATE ON identity_authorization_meta
BEGIN SELECT RAISE(ABORT,'identity authorization metadata is immutable'); END;
CREATE TRIGGER IF NOT EXISTS identity_authorization_meta_no_delete
BEFORE DELETE ON identity_authorization_meta
BEGIN SELECT RAISE(ABORT,'identity authorization metadata is immutable'); END;
CREATE TRIGGER IF NOT EXISTS identity_authorization_events_no_update
BEFORE UPDATE ON identity_authorization_events
BEGIN SELECT RAISE(ABORT,'identity authorization events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS identity_authorization_events_no_delete
BEFORE DELETE ON identity_authorization_events
BEGIN SELECT RAISE(ABORT,'identity authorization events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS identity_authorization_registry_snapshots_no_update
BEFORE UPDATE ON identity_authorization_registry_snapshots
BEGIN SELECT RAISE(ABORT,'identity authorization registry snapshots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS identity_authorization_registry_snapshots_no_delete
BEFORE DELETE ON identity_authorization_registry_snapshots
BEGIN SELECT RAISE(ABORT,'identity authorization registry snapshots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS identity_authorization_decisions_no_update
BEFORE UPDATE ON identity_authorization_decisions
BEGIN SELECT RAISE(ABORT,'identity authorization decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS identity_authorization_decisions_no_delete
BEFORE DELETE ON identity_authorization_decisions
BEGIN SELECT RAISE(ABORT,'identity authorization decisions are append-only'); END;
COMMIT;
"""


class IdentityAuthorizationStore:
    def __init__(
        self,
        path: str,
        *,
        create: bool = False,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string")
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise IdentityAuthorizationStoreError(f"authority unavailable: {path}")
        if clock_ms is not None and not callable(clock_ms):
            raise TypeError("clock_ms must be callable or None")
        if create and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._path = path
        self._clock_ms = clock_ms or _local_clock_ms()
        try:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout=5000")
                self._conn.execute("PRAGMA foreign_keys=ON")
                if create:
                    mode = self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                    if path != ":memory:" and str(mode).lower() != "wal":
                        raise IdentityAuthorizationStoreError("authority requires WAL")
                    self._conn.execute("PRAGMA synchronous=FULL")
                    self._conn.executescript(_SCHEMA)
                if not create:
                    self._conn.execute("PRAGMA synchronous=FULL")
                if self._conn.execute("PRAGMA synchronous").fetchone()[0] != 2:
                    raise IdentityAuthorizationStoreError("authority requires FULL sync")
                self._read_verified_locked()
                if not create:
                    mode = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
                    if path != ":memory:" and str(mode).lower() != "wal":
                        raise IdentityAuthorizationStoreError("authority is not WAL")
        except BaseException as exc:
            self._conn.close()
            if isinstance(exc, IdentityAuthorizationStoreError):
                raise
            raise IdentityAuthorizationStoreError(f"authority unavailable: {exc}") from exc

    def _verify_schema_locked(self) -> None:
        try:
            if self._conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
                raise IdentityAuthorizationStoreError("authority requires foreign_keys=ON")
            row = self._conn.execute(
                "SELECT singleton,store_kind,schema_version FROM identity_authorization_meta"
            ).fetchall()
            expected = (
                1,
                IDENTITY_AUTHORIZATION_STORE_KIND,
                IDENTITY_AUTHORIZATION_STORE_SCHEMA_VERSION,
            )
            if len(row) != 1 or tuple(row[0]) != expected:
                raise IdentityAuthorizationStoreError("authority schema is invalid")
            triggers = {
                item["name"]
                for item in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'identity_authorization_%'"
                ).fetchall()
            }
            if triggers != _TRIGGERS:
                raise IdentityAuthorizationStoreError("authority triggers are invalid")
            if self._conn.execute("PRAGMA foreign_key_check").fetchall():
                raise IdentityAuthorizationStoreError("authority foreign keys are invalid")
        except sqlite3.DatabaseError as exc:
            raise IdentityAuthorizationStoreError(f"authority schema is unreadable: {exc}") from exc

    def _require_not_future_locked(self, recorded_at_ms: int) -> None:
        authority_now = self._clock_ms()
        _require_time(authority_now)
        if recorded_at_ms > authority_now + IDENTITY_AUTHORIZATION_MAX_FUTURE_SKEW_MS:
            raise IdentityAuthorizationStoreFutureClock(
                recorded_at_ms=recorded_at_ms,
                authority_now_ms=authority_now,
            )

    def _event_rows_locked(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM identity_authorization_events ORDER BY event_seq").fetchall()

    def _snapshot_rows_locked(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT s.*,e.recorded_at_ms,e.previous_event_hash,e.event_hash,"
            " e.event_kind,e.event_id,e.payload_sha256"
            " FROM identity_authorization_registry_snapshots s"
            " JOIN identity_authorization_events e"
            " ON e.event_seq=s.authority_event_seq"
            " ORDER BY s.authority_event_seq"
        ).fetchall()

    def _decision_rows_locked(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT d.*,e.previous_event_hash,e.event_hash,e.event_kind,"
            " e.event_id,e.payload_sha256,e.recorded_at_ms AS event_time"
            " FROM identity_authorization_decisions d"
            " JOIN identity_authorization_events e"
            " ON e.event_seq=d.authority_event_seq"
            " ORDER BY d.decision_seq"
        ).fetchall()

    def _read_verified_locked(self) -> IdentityAuthorizationAuthoritySnapshot:
        self._verify_schema_locked()
        try:
            event_rows = self._event_rows_locked()
            previous_hash = ""
            previous_time = -1
            events: dict[int, sqlite3.Row] = {}
            for expected_seq, row in enumerate(event_rows, start=1):
                if row["event_seq"] != expected_seq:
                    raise IdentityAuthorizationStoreError("event sequence is not contiguous")
                expected_hash = _event_hash(
                    event_seq=row["event_seq"],
                    event_kind=row["event_kind"],
                    event_id=row["event_id"],
                    recorded_at_ms=row["recorded_at_ms"],
                    payload_sha256=row["payload_sha256"],
                    previous_event_hash=row["previous_event_hash"],
                )
                if row["previous_event_hash"] != previous_hash or row["event_hash"] != expected_hash:
                    raise IdentityAuthorizationStoreError("authority event hash is invalid")
                if row["recorded_at_ms"] < previous_time:
                    raise IdentityAuthorizationStoreError("authority clock history rolled back")
                events[row["event_seq"]] = row
                previous_hash = row["event_hash"]
                previous_time = row["recorded_at_ms"]

            snapshot_rows = self._snapshot_rows_locked()
            snapshots: list[IdentityAuthorizationRegistryHead] = []
            for expected_generation, row in enumerate(snapshot_rows):
                content = bytes(row["registry_bytes"])
                registry = _registry(content)
                digest = _sha256_bytes(content)
                if (
                    registry.generation != expected_generation
                    or row["generation"] != expected_generation
                    or row["registry_sha256"] != digest
                    or row["event_kind"] != _REGISTRY_EVENT_KIND
                    or row["event_id"] != f"registry:{expected_generation}:{digest}"
                    or row["payload_sha256"] != _registry_payload_sha256(expected_generation, digest)
                ):
                    raise IdentityAuthorizationStoreError("registry snapshot is invalid")
                snapshots.append(
                    IdentityAuthorizationRegistryHead(
                        generation=expected_generation,
                        registry_sha256=digest,
                        registry_bytes=content,
                        authority_event_seq=row["authority_event_seq"],
                        recorded_at_ms=row["recorded_at_ms"],
                        previous_event_hash=row["previous_event_hash"],
                        event_hash=row["event_hash"],
                    )
                )

            decision_rows = self._decision_rows_locked()
            records: list[IdentityAuthorizationRecord] = []
            snapshots_by_event = {item.authority_event_seq: item for item in snapshots}
            for expected_seq, row in enumerate(decision_rows, start=1):
                decision = IdentityAuthorizationDecision.model_validate_json(row["decision_json"])
                decision_json = _decision_json(decision)
                decision_sha = _sha256_text(decision_json)
                payload = _decision_payload_sha256(
                    decision_id=row["decision_id"],
                    recorded_at_ms=row["recorded_at_ms"],
                    registry_generation=row["registry_generation"],
                    registry_sha256=row["registry_sha256"],
                    registry_event_seq=row["registry_event_seq"],
                    registry_event_hash=row["registry_event_hash"],
                    decision_json=decision_json,
                    decision_sha256=decision_sha,
                )
                retained_registry = snapshots_by_event.get(row["registry_event_seq"])
                if (
                    row["decision_seq"] != expected_seq
                    or row["event_kind"] != _DECISION_EVENT_KIND
                    or row["event_id"] != row["decision_id"]
                    or row["event_time"] != row["recorded_at_ms"]
                    or row["decision_json"] != decision_json
                    or row["decision_sha256"] != decision_sha
                    or row["payload_sha256"] != payload
                    or retained_registry is None
                    or row["registry_generation"] != retained_registry.generation
                    or decision.registry_generation != retained_registry.generation
                    or row["registry_sha256"] != retained_registry.registry_sha256
                    or row["registry_event_hash"] != retained_registry.event_hash
                ):
                    raise IdentityAuthorizationStoreError("authorization decision is invalid")
                records.append(
                    IdentityAuthorizationRecord(
                        authority_event_seq=row["authority_event_seq"],
                        decision_id=row["decision_id"],
                        recorded_at_ms=row["recorded_at_ms"],
                        registry_generation=row["registry_generation"],
                        registry_sha256=row["registry_sha256"],
                        registry_event_seq=row["registry_event_seq"],
                        registry_event_hash=row["registry_event_hash"],
                        decision_sha256=decision_sha,
                        decision=decision,
                        previous_event_hash=row["previous_event_hash"],
                        event_hash=row["event_hash"],
                    )
                )

            typed = {item.authority_event_seq for item in snapshots} | {item.authority_event_seq for item in records}
            if len(typed) != len(snapshots) + len(records) or typed != set(events):
                raise IdentityAuthorizationStoreError("authority event typing is invalid")
            for table, count in (
                ("identity_authorization_events", len(events)),
                ("identity_authorization_decisions", len(records)),
            ):
                sequence = self._conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
                if count and (sequence is None or sequence["seq"] != count):
                    raise IdentityAuthorizationStoreError("authority history was truncated")
                if not count and sequence is not None and sequence["seq"] != 0:
                    raise IdentityAuthorizationStoreError("authority history was truncated")
            return IdentityAuthorizationAuthoritySnapshot(
                registry_head=snapshots[-1] if snapshots else None,
                registry_snapshots=tuple(snapshots),
                decisions=tuple(records),
                authority_head_hash=previous_hash,
                authority_time_floor_ms=previous_time if events else None,
            )
        except IdentityAuthorizationStoreError:
            raise
        except (json.JSONDecodeError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
            raise IdentityAuthorizationStoreError(f"authority history is invalid: {exc}") from exc

    def append_registry_snapshot(
        self,
        registry_bytes: bytes,
        *,
        recorded_at_ms: int,
        expected_head: IdentityAuthorizationRegistryHead | None,
    ) -> tuple[IdentityAuthorizationRegistryHead, bool]:
        candidate = _registry(registry_bytes)
        digest = _sha256_bytes(registry_bytes)
        _require_time(recorded_at_ms)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._require_not_future_locked(recorded_at_ms)
                snapshot = self._read_verified_locked()
                current = snapshot.registry_head
                if current is None:
                    if expected_head is not None:
                        raise IdentityAuthorizationStoreRegistryHeadChanged(
                            "REGISTRY_HEAD_CHANGED",
                            expected_head=expected_head,
                        )
                    if candidate.generation != 0:
                        raise IdentityAuthorizationStoreRegistryGenerationGap("REGISTRY_GENERATION_GAP")
                else:
                    if expected_head != current:
                        raise IdentityAuthorizationStoreRegistryHeadChanged(
                            "REGISTRY_HEAD_CHANGED",
                            expected_head=expected_head,
                            actual_head=current,
                        )
                    if candidate.generation == current.generation:
                        if digest == current.registry_sha256 and registry_bytes == current.registry_bytes:
                            self._conn.rollback()
                            return current, False
                        raise IdentityAuthorizationStoreRegistryConflict(
                            "REGISTRY_GENERATION_CONFLICT",
                            expected_head=current,
                            actual_head=current,
                        )
                    try:
                        validate_registry_successor(_registry(current.registry_bytes), candidate)
                    except IdentityAuthorizationRegistryError as exc:
                        error_type = {
                            "REGISTRY_GENERATION_ROLLBACK": IdentityAuthorizationStoreRegistryRollback,
                            "REGISTRY_GENERATION_GAP": IdentityAuthorizationStoreRegistryGenerationGap,
                        }.get(exc.code, IdentityAuthorizationStoreRegistryConflict)
                        raise error_type(
                            exc.code,
                            expected_head=current,
                            actual_head=current,
                        ) from exc
                if snapshot.authority_time_floor_ms is not None and recorded_at_ms < snapshot.authority_time_floor_ms:
                    raise IdentityAuthorizationStoreClockRollback("identity authorization authority clock rollback")
                event_seq = self._conn.execute("SELECT COUNT(*) FROM identity_authorization_events").fetchone()[0] + 1
                payload = _registry_payload_sha256(candidate.generation, digest)
                event_id = f"registry:{candidate.generation}:{digest}"
                event_hash = _event_hash(
                    event_seq=event_seq,
                    event_kind=_REGISTRY_EVENT_KIND,
                    event_id=event_id,
                    recorded_at_ms=recorded_at_ms,
                    payload_sha256=payload,
                    previous_event_hash=snapshot.authority_head_hash,
                )
                self._conn.execute(
                    "INSERT INTO identity_authorization_events VALUES (?,?,?,?,?,?,?)",
                    (
                        event_seq,
                        _REGISTRY_EVENT_KIND,
                        event_id,
                        recorded_at_ms,
                        payload,
                        snapshot.authority_head_hash,
                        event_hash,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO identity_authorization_registry_snapshots VALUES (?,?,?,?)",
                    (event_seq, candidate.generation, digest, sqlite3.Binary(registry_bytes)),
                )
                retained = self._read_verified_locked().registry_head
                assert retained is not None
                self._conn.commit()
                return retained, True
            except Exception as exc:
                if self._conn.in_transaction:
                    self._conn.rollback()
                if isinstance(exc, IdentityAuthorizationStoreError):
                    raise
                if isinstance(exc, sqlite3.DatabaseError):
                    raise IdentityAuthorizationStoreError(f"registry could not be recorded: {exc}") from exc
                raise

    def record(
        self,
        decision: IdentityAuthorizationDecision,
        *,
        recorded_at_ms: int,
        expected_registry_head: IdentityAuthorizationRegistryHead | None,
        decision_id: str | None = None,
    ) -> tuple[IdentityAuthorizationRecord, bool]:
        decision_json = _decision_json(decision)
        _require_time(recorded_at_ms)
        resolved_id = decision_id or f"identity-authz-{_sha256_text(decision_json)}"
        if not isinstance(resolved_id, str) or not resolved_id or len(resolved_id) > 255:
            raise ValueError("decision_id must be a non-empty string")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._require_not_future_locked(recorded_at_ms)
                snapshot = self._read_verified_locked()
                current = snapshot.registry_head
                if current is None:
                    raise IdentityAuthorizationStoreRegistryUnprovisioned("REGISTRY_UNPROVISIONED")
                if expected_registry_head is None:
                    raise IdentityAuthorizationStoreRegistryHeadMismatch("REGISTRY_HEAD_REQUIRED", actual_head=current)
                if expected_registry_head != current:
                    raise IdentityAuthorizationStoreRegistryHeadChanged(
                        "REGISTRY_HEAD_CHANGED",
                        expected_head=expected_registry_head,
                        actual_head=current,
                    )
                if decision.registry_generation != current.generation:
                    raise IdentityAuthorizationStoreRegistryHeadMismatch(
                        "DECISION_REGISTRY_HEAD_MISMATCH",
                        expected_head=current,
                        actual_head=current,
                    )
                existing = next(
                    (item for item in snapshot.decisions if item.decision_id == resolved_id),
                    None,
                )
                decision_sha = _sha256_text(decision_json)
                payload = _decision_payload_sha256(
                    decision_id=resolved_id,
                    recorded_at_ms=recorded_at_ms,
                    registry_generation=current.generation,
                    registry_sha256=current.registry_sha256,
                    registry_event_seq=current.authority_event_seq,
                    registry_event_hash=current.event_hash,
                    decision_json=decision_json,
                    decision_sha256=decision_sha,
                )
                if existing is not None:
                    if (
                        existing.recorded_at_ms == recorded_at_ms
                        and existing.registry_generation == current.generation
                        and existing.registry_sha256 == current.registry_sha256
                        and existing.registry_event_seq == current.authority_event_seq
                        and existing.registry_event_hash == current.event_hash
                        and existing.decision == decision
                    ):
                        self._conn.rollback()
                        return existing, False
                    raise IdentityAuthorizationStoreConflict(f"authorization decision conflict: {resolved_id}")
                if snapshot.authority_time_floor_ms is not None and recorded_at_ms < snapshot.authority_time_floor_ms:
                    raise IdentityAuthorizationStoreClockRollback("identity authorization authority clock rollback")
                event_seq = self._conn.execute("SELECT COUNT(*) FROM identity_authorization_events").fetchone()[0] + 1
                event_hash = _event_hash(
                    event_seq=event_seq,
                    event_kind=_DECISION_EVENT_KIND,
                    event_id=resolved_id,
                    recorded_at_ms=recorded_at_ms,
                    payload_sha256=payload,
                    previous_event_hash=snapshot.authority_head_hash,
                )
                self._conn.execute(
                    "INSERT INTO identity_authorization_events VALUES (?,?,?,?,?,?,?)",
                    (
                        event_seq,
                        _DECISION_EVENT_KIND,
                        resolved_id,
                        recorded_at_ms,
                        payload,
                        snapshot.authority_head_hash,
                        event_hash,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO identity_authorization_decisions"
                    " (authority_event_seq,decision_id,recorded_at_ms,registry_generation,"
                    " registry_sha256,registry_event_seq,registry_event_hash,decision_json,"
                    " decision_sha256) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        event_seq,
                        resolved_id,
                        recorded_at_ms,
                        current.generation,
                        current.registry_sha256,
                        current.authority_event_seq,
                        current.event_hash,
                        decision_json,
                        decision_sha,
                    ),
                )
                retained = self._read_verified_locked().decisions[-1]
                self._conn.commit()
                return retained, True
            except Exception as exc:
                if self._conn.in_transaction:
                    self._conn.rollback()
                if isinstance(exc, IdentityAuthorizationStoreError):
                    raise
                if isinstance(exc, sqlite3.DatabaseError):
                    raise IdentityAuthorizationStoreError(f"decision could not be recorded: {exc}") from exc
                raise

    def read_snapshot(self) -> IdentityAuthorizationAuthoritySnapshot:
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                return self._read_verified_locked()
            finally:
                if self._conn.in_transaction:
                    self._conn.rollback()

    def registry_snapshots(self) -> tuple[IdentityAuthorizationRegistryHead, ...]:
        return self.read_snapshot().registry_snapshots

    def registry_head(self) -> IdentityAuthorizationRegistryHead:
        snapshot = self.read_snapshot()
        if snapshot.registry_head is None:
            raise IdentityAuthorizationStoreRegistryUnprovisioned("REGISTRY_UNPROVISIONED")
        return snapshot.registry_head

    def decisions(self) -> tuple[IdentityAuthorizationRecord, ...]:
        return self.read_snapshot().decisions

    def chain_valid(self) -> bool:
        try:
            self.read_snapshot()
            return True
        except (IdentityAuthorizationStoreError, sqlite3.DatabaseError, ValueError):
            return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "IDENTITY_AUTHORIZATION_MAX_FUTURE_SKEW_MS",
    "IDENTITY_AUTHORIZATION_STORE_KIND",
    "IDENTITY_AUTHORIZATION_STORE_SCHEMA_VERSION",
    "IdentityAuthorizationAuthoritySnapshot",
    "IdentityAuthorizationRecord",
    "IdentityAuthorizationRegistryHead",
    "IdentityAuthorizationStore",
    "IdentityAuthorizationStoreClockRollback",
    "IdentityAuthorizationStoreConflict",
    "IdentityAuthorizationStoreError",
    "IdentityAuthorizationStoreFutureClock",
    "IdentityAuthorizationStoreRegistryConflict",
    "IdentityAuthorizationStoreRegistryGenerationGap",
    "IdentityAuthorizationStoreRegistryHeadChanged",
    "IdentityAuthorizationStoreRegistryHeadMismatch",
    "IdentityAuthorizationStoreRegistryRollback",
    "IdentityAuthorizationStoreRegistryUnprovisioned",
]
