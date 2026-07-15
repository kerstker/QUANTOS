"""Single-node authority for artifact registry snapshots and admissions.

Registry snapshots and decisions share one append-only, hash-chained event log.
The store has no verifier, signer, network, deployment, or execution capability.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from typing import Callable, Literal

from .artifact_admission import (
    ArtifactAdmissionDecision,
    load_json_without_duplicate_keys,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STORE_KIND = "QUANTOS_ARTIFACT_ADMISSION_AUTHORITY"
_STORE_SCHEMA_VERSION = 4
ARTIFACT_ADMISSION_STORE_KIND = _STORE_KIND
ARTIFACT_ADMISSION_STORE_SCHEMA_VERSION = _STORE_SCHEMA_VERSION
ARTIFACT_ADMISSION_MAX_FUTURE_SKEW_MS = 5 * 60_000

POLICY_REGISTRY_KIND = "POLICY_REGISTRY"
RECEIPT_AUTHORITY_REGISTRY_KIND = "RECEIPT_AUTHORITY_REGISTRY"
RegistryKind = Literal["POLICY_REGISTRY", "RECEIPT_AUTHORITY_REGISTRY"]
_REGISTRY_KINDS = frozenset({POLICY_REGISTRY_KIND, RECEIPT_AUTHORITY_REGISTRY_KIND})
_REGISTRY_COLLECTION = {
    POLICY_REGISTRY_KIND: "policies",
    RECEIPT_AUTHORITY_REGISTRY_KIND: "keys",
}
_REGISTRY_EVENT_KIND = {
    POLICY_REGISTRY_KIND: "POLICY_REGISTRY_SNAPSHOT",
    RECEIPT_AUTHORITY_REGISTRY_KIND: "RECEIPT_AUTHORITY_REGISTRY_SNAPSHOT",
}
_DECISION_EVENT_KIND = "DECISION"

_TRIGGERS = frozenset(
    {
        "artifact_admission_meta_no_update",
        "artifact_admission_meta_no_delete",
        "artifact_admission_events_no_update",
        "artifact_admission_events_no_delete",
        "artifact_admission_registry_snapshots_no_update",
        "artifact_admission_registry_snapshots_no_delete",
        "artifact_admission_decisions_no_update",
        "artifact_admission_decisions_no_delete",
    }
)
_EXPECTED_COLUMNS = {
    "artifact_admission_authority_meta": (
        "singleton",
        "store_kind",
        "schema_version",
    ),
    "artifact_admission_authority_events": (
        "event_seq",
        "event_kind",
        "event_id",
        "recorded_at_ms",
        "payload_sha256",
        "previous_event_hash",
        "event_hash",
    ),
    "artifact_admission_registry_snapshots": (
        "authority_event_seq",
        "registry_kind",
        "generation",
        "registry_sha256",
        "registry_bytes",
    ),
    "artifact_admission_decisions": (
        "decision_seq",
        "authority_event_seq",
        "decision_id",
        "recorded_at_ms",
        "policy_sha256",
        "receipt_sha256",
        "artifact_sha256",
        "decision_json",
        "decision_sha256",
    ),
}


def _local_authority_clock_ms() -> Callable[[], int]:
    base_wall_ms = time.time_ns() // 1_000_000
    base_monotonic_ns = time.monotonic_ns()

    def current() -> int:
        elapsed_ms = (time.monotonic_ns() - base_monotonic_ns) // 1_000_000
        return base_wall_ms + elapsed_ms

    return current


class ArtifactAdmissionStoreError(RuntimeError):
    """The admission authority is unavailable, conflicting, or unverifiable."""


class ArtifactAdmissionStoreConflict(ArtifactAdmissionStoreError):
    """A decision id already binds a different immutable record."""


class ArtifactAdmissionStoreClockRollback(ArtifactAdmissionStoreError):
    """The authority clock moved behind its last committed event."""


class ArtifactAdmissionStoreFutureClock(ArtifactAdmissionStoreError):
    """A proposed event is too far ahead of the authority clock."""

    reason_code = "ARTIFACT_ADMISSION_AUTHORITY_FUTURE_CLOCK"

    def __init__(self, *, recorded_at_ms: int, authority_now_ms: int) -> None:
        super().__init__(self.reason_code)
        self.recorded_at_ms = recorded_at_ms
        self.authority_now_ms = authority_now_ms


class _RegistryStateError(ArtifactAdmissionStoreError):
    def __init__(
        self,
        reason_code: str,
        *,
        expected_head: ArtifactAdmissionRegistryHead | None = None,
        actual_head: ArtifactAdmissionRegistryHead | None = None,
    ) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.expected_head = expected_head
        self.actual_head = actual_head


class ArtifactAdmissionStoreRegistryUnprovisioned(_RegistryStateError):
    """A required registry has no retained authority snapshot."""


class ArtifactAdmissionStoreRegistryHeadMismatch(_RegistryStateError):
    """A caller or decision did not bind the required authority head."""


class ArtifactAdmissionStoreRegistryHeadChanged(_RegistryStateError):
    """A registry head changed after a caller acquired its CAS token."""


class ArtifactAdmissionStoreRegistryRollback(_RegistryStateError):
    """A registry generation moved behind its current authority head."""


class ArtifactAdmissionStoreRegistryConflict(_RegistryStateError):
    """One registry generation was presented with different exact bytes."""


class ArtifactAdmissionStoreRegistryGenerationGap(_RegistryStateError):
    """A registry generation skipped required append-only history."""


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_sha256(name: str, value: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _require_optional_sha256(name: str, value: str) -> str:
    if value == "":
        return value
    return _require_sha256(name, value)


def _require_registry_kind(value: str) -> RegistryKind:
    if value not in _REGISTRY_KINDS:
        raise ValueError(f"unsupported registry kind: {value!r}")
    return value  # type: ignore[return-value]


def _require_recorded_at_ms(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("recorded_at_ms must be a non-negative integer")
    return value


def _registry_generation(registry_kind: RegistryKind, content: bytes) -> int:
    if not isinstance(content, bytes) or not content:
        raise ValueError("registry_bytes must be non-empty bytes")
    try:
        document = load_json_without_duplicate_keys(content)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("registry_bytes must be strict JSON") from exc
    collection = _REGISTRY_COLLECTION[registry_kind]
    if (
        not isinstance(document, dict)
        or set(document) != {"schema_version", "readiness", "generation", collection}
        or document.get("schema_version") != 2
        or document.get("readiness") != "L2_DRY_RUN_ONLY"
        or type(document.get("generation")) is not int
        or document["generation"] < 0
        or not isinstance(document.get(collection), dict)
    ):
        raise ValueError("registry_bytes do not match the registry contract")
    return document["generation"]


def _registry_event_id(
    registry_kind: RegistryKind,
    generation: int,
    registry_sha256: str,
) -> str:
    return f"registry:{registry_kind}:{generation}:{registry_sha256}"


def _registry_payload_sha256(
    registry_kind: RegistryKind,
    generation: int,
    registry_sha256: str,
) -> str:
    return _sha256(
        _canonical(
            {
                "generation": generation,
                "registry_kind": registry_kind,
                "registry_sha256": registry_sha256,
            }
        )
    )


def _decision_json(decision: ArtifactAdmissionDecision) -> str:
    if not isinstance(decision, ArtifactAdmissionDecision):
        raise TypeError("decision must be an ArtifactAdmissionDecision")
    return _canonical(decision.model_dump(mode="json"))


def _default_decision_id(decision_json: str, recorded_at_ms: int) -> str:
    identity = _canonical(
        {
            "decision": json.loads(decision_json),
            "recorded_at_ms": recorded_at_ms,
        }
    )
    return f"artifact-admission-{_sha256(identity)}"


def _decision_payload_sha256(
    *,
    decision_id: str,
    recorded_at_ms: int,
    policy_sha256: str,
    receipt_sha256: str,
    artifact_sha256: str,
    decision_json: str,
    decision_sha256: str,
) -> str:
    return _sha256(
        _canonical(
            {
                "artifact_sha256": artifact_sha256,
                "decision": json.loads(decision_json),
                "decision_id": decision_id,
                "decision_sha256": decision_sha256,
                "policy_sha256": policy_sha256,
                "receipt_sha256": receipt_sha256,
                "recorded_at_ms": recorded_at_ms,
            }
        )
    )


def _authority_event_hash(
    *,
    event_seq: int,
    event_kind: str,
    event_id: str,
    recorded_at_ms: int,
    payload_sha256: str,
    previous_event_hash: str,
) -> str:
    return _sha256(
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


def _validate_decision_binding(
    decision: ArtifactAdmissionDecision,
    *,
    policy_sha256: str,
    receipt_sha256: str,
    artifact_sha256: str,
) -> None:
    if decision.policy_sha256:
        _require_sha256("decision.policy_sha256", decision.policy_sha256)
        if decision.policy_sha256 != policy_sha256:
            raise ValueError("policy_sha256 does not match the decision")
    if decision.artifact_sha256:
        _require_sha256("decision.artifact_sha256", decision.artifact_sha256)
        if decision.artifact_sha256 != artifact_sha256:
            raise ValueError("artifact_sha256 does not match the decision")
    if decision.verification_receipt_sha256:
        _require_sha256(
            "decision.verification_receipt_sha256",
            decision.verification_receipt_sha256,
        )
        if decision.verification_receipt_sha256 != receipt_sha256:
            raise ValueError("receipt_sha256 does not match the decision")
    registry_snapshots = (
        (
            "policy registry",
            decision.policy_registry_generation,
            decision.policy_registry_sha256,
        ),
        (
            "receipt authority registry",
            decision.receipt_authority_registry_generation,
            decision.receipt_authority_registry_sha256,
        ),
    )
    for name, generation, digest in registry_snapshots:
        if generation is None:
            if decision.verdict == "ALLOW":
                raise ValueError(f"an ALLOW decision must bind the {name} generation")
            continue
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError(f"{name} generation must be a non-negative integer")
        _require_sha256(f"{name} sha256", digest)
    if decision.verdict == "ALLOW" and (
        not policy_sha256
        or not receipt_sha256
        or not artifact_sha256
        or not decision.policy_sha256
        or not decision.policy_registry_sha256
        or decision.policy_registry_event_seq is None
        or decision.policy_registry_event_seq <= 0
        or not decision.policy_registry_event_hash
        or not decision.artifact_sha256
        or not decision.verification_receipt_sha256
        or not decision.receipt_envelope_sha256
        or not decision.receipt_authority_registry_sha256
        or decision.receipt_authority_registry_event_seq is None
        or decision.receipt_authority_registry_event_seq <= 0
        or not decision.receipt_authority_registry_event_hash
        or not decision.receipt_authority_id
        or not decision.receipt_key_id
        or not decision.receipt_public_key_sha256
        or decision.receipt_signature_algorithm != "Ed25519"
        or decision.receipt_signature_verified is not True
        or not decision.receipt_id
        or not decision.verifier_id
        or not decision.verifier_runtime_id
        or not decision.certificate_sha256
        or decision.verified_at_ms is None
        or decision.transparency_integrated_at_ms is None
        or decision.transparency_checkpoint_at_ms is None
        or decision.revocation_status != "GOOD"
        or decision.revocation_checked_at_ms is None
        or not decision.revocation_source
    ):
        raise ValueError("an ALLOW decision must bind authenticated policy and receipt evidence")


def _validate_allow_authority_binding(
    decision: ArtifactAdmissionDecision,
    *,
    recorded_at_ms: int,
    decision_id: str,
) -> None:
    if decision.verdict != "ALLOW":
        return
    if decision.admission_at_ms != recorded_at_ms:
        raise ValueError("an ALLOW decision admission time must equal its authority record time")
    if decision.authority_store_kind != _STORE_KIND:
        raise ValueError("an ALLOW decision must bind the authority store kind")
    if decision.authority_store_schema_version != _STORE_SCHEMA_VERSION:
        raise ValueError("an ALLOW decision must bind the authority store schema")
    if decision.authority_decision_id != decision_id:
        raise ValueError("an ALLOW decision must bind its authority decision id")


@dataclass(frozen=True)
class ArtifactAdmissionRegistryHead:
    registry_kind: RegistryKind
    generation: int
    registry_sha256: str
    registry_bytes: bytes
    authority_event_seq: int
    recorded_at_ms: int
    previous_event_hash: str
    event_hash: str

    def __post_init__(self) -> None:
        kind = _require_registry_kind(self.registry_kind)
        generation = _registry_generation(kind, self.registry_bytes)
        if generation != self.generation:
            raise ValueError("registry generation does not match exact bytes")
        _require_sha256("registry_sha256", self.registry_sha256)
        if _sha256_bytes(self.registry_bytes) != self.registry_sha256:
            raise ValueError("registry_sha256 does not match exact bytes")
        if (
            isinstance(self.authority_event_seq, bool)
            or not isinstance(self.authority_event_seq, int)
            or self.authority_event_seq <= 0
        ):
            raise ValueError("authority_event_seq must be a positive integer")
        _require_recorded_at_ms(self.recorded_at_ms)
        if self.previous_event_hash:
            _require_sha256("previous_event_hash", self.previous_event_hash)
        _require_sha256("event_hash", self.event_hash)
        expected = _authority_event_hash(
            event_seq=self.authority_event_seq,
            event_kind=_REGISTRY_EVENT_KIND[kind],
            event_id=_registry_event_id(kind, generation, self.registry_sha256),
            recorded_at_ms=self.recorded_at_ms,
            payload_sha256=_registry_payload_sha256(kind, generation, self.registry_sha256),
            previous_event_hash=self.previous_event_hash,
        )
        if self.event_hash != expected:
            raise ValueError("registry authority event hash is invalid")


@dataclass(frozen=True)
class ArtifactAdmissionRecord:
    authority_event_seq: int
    decision_id: str
    recorded_at_ms: int
    policy_sha256: str
    receipt_sha256: str
    artifact_sha256: str
    decision_sha256: str
    decision: ArtifactAdmissionDecision
    previous_record_hash: str
    record_hash: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.authority_event_seq, bool)
            or not isinstance(self.authority_event_seq, int)
            or self.authority_event_seq <= 0
        ):
            raise ValueError("authority_event_seq must be a positive integer")
        if (
            not isinstance(self.decision_id, str)
            or not self.decision_id
            or self.decision_id != self.decision_id.strip()
        ):
            raise ValueError("decision_id must be a non-empty canonical string")
        if len(self.decision_id) > 255:
            raise ValueError("decision_id exceeds 255 characters")
        _require_recorded_at_ms(self.recorded_at_ms)
        _require_optional_sha256("policy_sha256", self.policy_sha256)
        _require_optional_sha256("receipt_sha256", self.receipt_sha256)
        _require_optional_sha256("artifact_sha256", self.artifact_sha256)
        _require_sha256("decision_sha256", self.decision_sha256)
        if self.previous_record_hash:
            _require_sha256("previous_record_hash", self.previous_record_hash)
        _require_sha256("record_hash", self.record_hash)

        decision_json = _decision_json(self.decision)
        if _sha256(decision_json) != self.decision_sha256:
            raise ValueError("decision_sha256 does not match the canonical decision")
        _validate_decision_binding(
            self.decision,
            policy_sha256=self.policy_sha256,
            receipt_sha256=self.receipt_sha256,
            artifact_sha256=self.artifact_sha256,
        )
        if self.decision.authority_decision_id and self.decision.authority_decision_id != self.decision_id:
            raise ValueError("decision_id does not match the decision authority id")
        payload_sha256 = _decision_payload_sha256(
            decision_id=self.decision_id,
            recorded_at_ms=self.recorded_at_ms,
            policy_sha256=self.policy_sha256,
            receipt_sha256=self.receipt_sha256,
            artifact_sha256=self.artifact_sha256,
            decision_json=decision_json,
            decision_sha256=self.decision_sha256,
        )
        expected = _authority_event_hash(
            event_seq=self.authority_event_seq,
            event_kind=_DECISION_EVENT_KIND,
            event_id=self.decision_id,
            recorded_at_ms=self.recorded_at_ms,
            payload_sha256=payload_sha256,
            previous_event_hash=self.previous_record_hash,
        )
        if self.record_hash != expected:
            raise ValueError("record_hash does not match the authority event")


@dataclass(frozen=True)
class _AuthorityEvent:
    event_seq: int
    event_kind: str
    event_id: str
    recorded_at_ms: int
    payload_sha256: str
    previous_event_hash: str
    event_hash: str


_SCHEMA = f"""
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS artifact_admission_authority_meta (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  store_kind TEXT NOT NULL,
  schema_version INTEGER NOT NULL CHECK (schema_version = {_STORE_SCHEMA_VERSION})
);
INSERT OR IGNORE INTO artifact_admission_authority_meta
  (singleton, store_kind, schema_version)
VALUES (1, '{_STORE_KIND}', {_STORE_SCHEMA_VERSION});
CREATE TABLE IF NOT EXISTS artifact_admission_authority_events (
  event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_kind TEXT NOT NULL CHECK (
    event_kind IN (
      'POLICY_REGISTRY_SNAPSHOT',
      'RECEIPT_AUTHORITY_REGISTRY_SNAPSHOT',
      'DECISION'
    )
  ),
  event_id TEXT UNIQUE NOT NULL CHECK (length(event_id) BETWEEN 1 AND 255),
  recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0),
  payload_sha256 TEXT NOT NULL CHECK (
    length(payload_sha256) = 64 AND payload_sha256 NOT GLOB '*[^0-9a-f]*'
  ),
  previous_event_hash TEXT NOT NULL CHECK (
    previous_event_hash = '' OR (
      length(previous_event_hash) = 64
      AND previous_event_hash NOT GLOB '*[^0-9a-f]*'
    )
  ),
  event_hash TEXT UNIQUE NOT NULL CHECK (
    length(event_hash) = 64 AND event_hash NOT GLOB '*[^0-9a-f]*'
  )
);
CREATE TABLE IF NOT EXISTS artifact_admission_registry_snapshots (
  authority_event_seq INTEGER PRIMARY KEY,
  registry_kind TEXT NOT NULL CHECK (
    registry_kind IN ('POLICY_REGISTRY', 'RECEIPT_AUTHORITY_REGISTRY')
  ),
  generation INTEGER NOT NULL CHECK (generation >= 0),
  registry_sha256 TEXT NOT NULL CHECK (
    length(registry_sha256) = 64 AND registry_sha256 NOT GLOB '*[^0-9a-f]*'
  ),
  registry_bytes BLOB NOT NULL CHECK (length(registry_bytes) > 0),
  UNIQUE (registry_kind, generation),
  FOREIGN KEY (authority_event_seq)
    REFERENCES artifact_admission_authority_events(event_seq)
    ON UPDATE RESTRICT ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS artifact_admission_decisions (
  decision_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  authority_event_seq INTEGER UNIQUE NOT NULL,
  decision_id TEXT UNIQUE NOT NULL CHECK (length(decision_id) BETWEEN 1 AND 255),
  recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= 0),
  policy_sha256 TEXT NOT NULL CHECK (
    policy_sha256 = '' OR (
      length(policy_sha256) = 64 AND policy_sha256 NOT GLOB '*[^0-9a-f]*'
    )
  ),
  receipt_sha256 TEXT NOT NULL CHECK (
    receipt_sha256 = '' OR (
      length(receipt_sha256) = 64 AND receipt_sha256 NOT GLOB '*[^0-9a-f]*'
    )
  ),
  artifact_sha256 TEXT NOT NULL CHECK (
    artifact_sha256 = '' OR (
      length(artifact_sha256) = 64 AND artifact_sha256 NOT GLOB '*[^0-9a-f]*'
    )
  ),
  decision_json TEXT NOT NULL,
  decision_sha256 TEXT NOT NULL CHECK (
    length(decision_sha256) = 64 AND decision_sha256 NOT GLOB '*[^0-9a-f]*'
  ),
  FOREIGN KEY (authority_event_seq)
    REFERENCES artifact_admission_authority_events(event_seq)
    ON UPDATE RESTRICT ON DELETE RESTRICT
);
CREATE TRIGGER IF NOT EXISTS artifact_admission_meta_no_update
BEFORE UPDATE ON artifact_admission_authority_meta
BEGIN SELECT RAISE(ABORT, 'artifact admission metadata is immutable'); END;
CREATE TRIGGER IF NOT EXISTS artifact_admission_meta_no_delete
BEFORE DELETE ON artifact_admission_authority_meta
BEGIN SELECT RAISE(ABORT, 'artifact admission metadata is immutable'); END;
CREATE TRIGGER IF NOT EXISTS artifact_admission_events_no_update
BEFORE UPDATE ON artifact_admission_authority_events
BEGIN SELECT RAISE(ABORT, 'artifact admission events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS artifact_admission_events_no_delete
BEFORE DELETE ON artifact_admission_authority_events
BEGIN SELECT RAISE(ABORT, 'artifact admission events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS artifact_admission_registry_snapshots_no_update
BEFORE UPDATE ON artifact_admission_registry_snapshots
BEGIN SELECT RAISE(ABORT, 'artifact admission registry snapshots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS artifact_admission_registry_snapshots_no_delete
BEFORE DELETE ON artifact_admission_registry_snapshots
BEGIN SELECT RAISE(ABORT, 'artifact admission registry snapshots are append-only'); END;
CREATE TRIGGER IF NOT EXISTS artifact_admission_decisions_no_update
BEFORE UPDATE ON artifact_admission_decisions
BEGIN SELECT RAISE(ABORT, 'artifact admission decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS artifact_admission_decisions_no_delete
BEFORE DELETE ON artifact_admission_decisions
BEGIN SELECT RAISE(ABORT, 'artifact admission decisions are append-only'); END;
COMMIT;
"""


class ArtifactAdmissionStore:
    """Append-only single-node registry and decision authority."""

    def __init__(
        self,
        path: str,
        *,
        create: bool = False,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string")
        if not isinstance(create, bool):
            raise TypeError("create must be a bool")
        if clock_ms is not None and not callable(clock_ms):
            raise TypeError("clock_ms must be callable or None")
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise ArtifactAdmissionStoreError(f"artifact admission store unavailable: {path}")
        if create and path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)

        self._conn = sqlite3.connect(
            path,
            check_same_thread=False,
            isolation_level=None,
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._path = path
        self._clock_ms = clock_ms if clock_ms is not None else _local_authority_clock_ms()
        try:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout=5000")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._verify_foreign_keys_locked()
                if create:
                    self._configure_durability_locked()
                    self._conn.executescript(_SCHEMA)
                self._read_verified_locked()
                if not create:
                    self._verify_wal_locked()
                    self._conn.execute("PRAGMA synchronous=FULL")
                    self._verify_full_sync_locked()
        except BaseException as exc:
            self._conn.close()
            if isinstance(exc, (ArtifactAdmissionStoreError, ValueError, TypeError)):
                raise
            raise ArtifactAdmissionStoreError(f"artifact admission store unavailable: {exc}") from exc

    def _configure_durability_locked(self) -> None:
        mode = str(self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0])
        if self._path != ":memory:" and mode.lower() != "wal":
            raise ArtifactAdmissionStoreError("artifact admission store requires WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._verify_full_sync_locked()

    def _verify_wal_locked(self) -> None:
        mode = str(self._conn.execute("PRAGMA journal_mode").fetchone()[0])
        if self._path != ":memory:" and mode.lower() != "wal":
            raise ArtifactAdmissionStoreError("artifact admission store is not WAL")

    def _verify_full_sync_locked(self) -> None:
        synchronous = int(self._conn.execute("PRAGMA synchronous").fetchone()[0])
        if synchronous != 2:
            raise ArtifactAdmissionStoreError("artifact admission store requires synchronous=FULL")

    def _verify_foreign_keys_locked(self) -> None:
        enabled = int(self._conn.execute("PRAGMA foreign_keys").fetchone()[0])
        if enabled != 1:
            raise ArtifactAdmissionStoreError("artifact admission store requires foreign_keys=ON")

    def _verify_schema_locked(self) -> None:
        try:
            self._verify_foreign_keys_locked()
            rows = self._conn.execute(
                "SELECT singleton,store_kind,schema_version FROM artifact_admission_authority_meta"
            ).fetchall()
            if len(rows) != 1 or tuple(rows[0]) != (
                1,
                _STORE_KIND,
                _STORE_SCHEMA_VERSION,
            ):
                raise ArtifactAdmissionStoreError("artifact admission metadata is invalid")
            for table, expected in _EXPECTED_COLUMNS.items():
                columns = tuple(
                    str(row["name"]) for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
                )
                if columns != expected:
                    raise ArtifactAdmissionStoreError(f"artifact admission schema is invalid: {table}")
            trigger_rows = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name LIKE 'artifact_admission_%'"
            ).fetchall()
            if {str(row["name"]) for row in trigger_rows} != _TRIGGERS:
                raise ArtifactAdmissionStoreError("artifact admission immutable triggers are invalid")
            violations = self._conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise ArtifactAdmissionStoreError("artifact admission foreign-key integrity is invalid")
        except ArtifactAdmissionStoreError:
            raise
        except sqlite3.DatabaseError as exc:
            raise ArtifactAdmissionStoreError(f"artifact admission schema is unreadable: {exc}") from exc

    def _event_rows_locked(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT event_seq,event_kind,event_id,recorded_at_ms,payload_sha256,"
            " previous_event_hash,event_hash"
            " FROM artifact_admission_authority_events ORDER BY event_seq"
        ).fetchall()

    def _snapshot_rows_locked(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT s.authority_event_seq,s.registry_kind,s.generation,"
            " s.registry_sha256,s.registry_bytes,e.event_kind,e.event_id,"
            " e.recorded_at_ms,e.payload_sha256,e.previous_event_hash,e.event_hash"
            " FROM artifact_admission_registry_snapshots AS s"
            " JOIN artifact_admission_authority_events AS e"
            " ON e.event_seq=s.authority_event_seq"
            " ORDER BY s.authority_event_seq"
        ).fetchall()

    def _decision_rows_locked(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT d.decision_seq,d.authority_event_seq,d.decision_id,"
            " d.recorded_at_ms,d.policy_sha256,d.receipt_sha256,"
            " d.artifact_sha256,d.decision_json,d.decision_sha256,"
            " e.event_kind,e.event_id,e.recorded_at_ms AS event_recorded_at_ms,"
            " e.payload_sha256,e.previous_event_hash,e.event_hash"
            " FROM artifact_admission_decisions AS d"
            " JOIN artifact_admission_authority_events AS e"
            " ON e.event_seq=d.authority_event_seq"
            " ORDER BY d.decision_seq"
        ).fetchall()

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ArtifactAdmissionRecord:
        try:
            raw = json.loads(row["decision_json"])
            if not isinstance(raw, dict) or _canonical(raw) != row["decision_json"]:
                raise ValueError("decision JSON is not a canonical object")
            decision = ArtifactAdmissionDecision.model_validate(raw)
            if _decision_json(decision) != row["decision_json"]:
                raise ValueError("decision JSON is not the full canonical contract")
            if row["event_kind"] != _DECISION_EVENT_KIND:
                raise ValueError("decision event kind is invalid")
            if row["event_id"] != row["decision_id"]:
                raise ValueError("decision event id is invalid")
            if row["event_recorded_at_ms"] != row["recorded_at_ms"]:
                raise ValueError("decision event time is invalid")
            record = ArtifactAdmissionRecord(
                authority_event_seq=row["authority_event_seq"],
                decision_id=row["decision_id"],
                recorded_at_ms=row["recorded_at_ms"],
                policy_sha256=row["policy_sha256"],
                receipt_sha256=row["receipt_sha256"],
                artifact_sha256=row["artifact_sha256"],
                decision_sha256=row["decision_sha256"],
                decision=decision,
                previous_record_hash=row["previous_event_hash"],
                record_hash=row["event_hash"],
            )
            payload_sha256 = _decision_payload_sha256(
                decision_id=record.decision_id,
                recorded_at_ms=record.recorded_at_ms,
                policy_sha256=record.policy_sha256,
                receipt_sha256=record.receipt_sha256,
                artifact_sha256=record.artifact_sha256,
                decision_json=row["decision_json"],
                decision_sha256=record.decision_sha256,
            )
            if payload_sha256 != row["payload_sha256"]:
                raise ValueError("decision authority payload hash is invalid")
            return record
        except Exception as exc:
            raise ArtifactAdmissionStoreError(
                f"artifact admission decision {row['decision_seq']} cannot be reconstructed"
            ) from exc

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> ArtifactAdmissionRegistryHead:
        try:
            kind = _require_registry_kind(row["registry_kind"])
            if row["event_kind"] != _REGISTRY_EVENT_KIND[kind]:
                raise ValueError("registry event kind is invalid")
            head = ArtifactAdmissionRegistryHead(
                registry_kind=kind,
                generation=row["generation"],
                registry_sha256=row["registry_sha256"],
                registry_bytes=bytes(row["registry_bytes"]),
                authority_event_seq=row["authority_event_seq"],
                recorded_at_ms=row["recorded_at_ms"],
                previous_event_hash=row["previous_event_hash"],
                event_hash=row["event_hash"],
            )
            if row["event_id"] != _registry_event_id(kind, head.generation, head.registry_sha256):
                raise ValueError("registry event id is invalid")
            if row["payload_sha256"] != _registry_payload_sha256(kind, head.generation, head.registry_sha256):
                raise ValueError("registry event payload hash is invalid")
            return head
        except Exception as exc:
            raise ArtifactAdmissionStoreError(
                f"artifact admission registry snapshot {row['authority_event_seq']} cannot be reconstructed"
            ) from exc

    def _verify_sequence_locked(self, table: str, expected_count: int) -> None:
        row = self._conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
        if expected_count:
            if row is None or int(row["seq"]) != expected_count:
                raise ArtifactAdmissionStoreError(f"artifact admission {table} sequence head mismatch")
        elif row is not None and int(row["seq"]) != 0:
            raise ArtifactAdmissionStoreError(f"artifact admission {table} history was truncated")

    def _verify_locked(
        self,
    ) -> tuple[
        str,
        tuple[ArtifactAdmissionRecord, ...],
        tuple[ArtifactAdmissionRegistryHead, ...],
    ]:
        self._verify_schema_locked()
        try:
            event_rows = self._event_rows_locked()
            events: dict[int, _AuthorityEvent] = {}
            previous_hash = ""
            previous_time = -1
            for expected_seq, row in enumerate(event_rows, start=1):
                if row["event_seq"] != expected_seq:
                    raise ArtifactAdmissionStoreError("artifact admission event sequence is not contiguous")
                event = _AuthorityEvent(
                    event_seq=row["event_seq"],
                    event_kind=row["event_kind"],
                    event_id=row["event_id"],
                    recorded_at_ms=row["recorded_at_ms"],
                    payload_sha256=row["payload_sha256"],
                    previous_event_hash=row["previous_event_hash"],
                    event_hash=row["event_hash"],
                )
                _require_recorded_at_ms(event.recorded_at_ms)
                _require_sha256("payload_sha256", event.payload_sha256)
                if event.previous_event_hash != previous_hash:
                    raise ArtifactAdmissionStoreError(f"artifact admission event {expected_seq} previous hash mismatch")
                if event.recorded_at_ms < previous_time:
                    raise ArtifactAdmissionStoreError("artifact admission authority clock history rolled back")
                expected_hash = _authority_event_hash(
                    event_seq=event.event_seq,
                    event_kind=event.event_kind,
                    event_id=event.event_id,
                    recorded_at_ms=event.recorded_at_ms,
                    payload_sha256=event.payload_sha256,
                    previous_event_hash=event.previous_event_hash,
                )
                if event.event_hash != expected_hash:
                    raise ArtifactAdmissionStoreError(f"artifact admission event {expected_seq} hash mismatch")
                events[event.event_seq] = event
                previous_hash = event.event_hash
                previous_time = event.recorded_at_ms

            snapshot_rows = self._snapshot_rows_locked()
            snapshots = tuple(self._snapshot_from_row(row) for row in snapshot_rows)
            previous_generation = {
                POLICY_REGISTRY_KIND: -1,
                RECEIPT_AUTHORITY_REGISTRY_KIND: -1,
            }
            for snapshot in snapshots:
                expected_generation = previous_generation[snapshot.registry_kind] + 1
                if snapshot.generation != expected_generation:
                    raise ArtifactAdmissionStoreError(f"{snapshot.registry_kind}_GENERATION_HISTORY_INVALID")
                previous_generation[snapshot.registry_kind] = snapshot.generation

            decision_rows = self._decision_rows_locked()
            records: list[ArtifactAdmissionRecord] = []
            for expected_seq, row in enumerate(decision_rows, start=1):
                if row["decision_seq"] != expected_seq:
                    raise ArtifactAdmissionStoreError("artifact admission decision sequence is not contiguous")
                records.append(self._record_from_row(row))

            snapshot_event_seqs = {snapshot.authority_event_seq for snapshot in snapshots}
            decision_event_seqs = {record.authority_event_seq for record in records}
            if snapshot_event_seqs & decision_event_seqs:
                raise ArtifactAdmissionStoreError("artifact admission event has multiple typed records")
            typed_event_seqs = snapshot_event_seqs | decision_event_seqs
            if typed_event_seqs != set(events):
                raise ArtifactAdmissionStoreError("artifact admission event is missing its typed record")

            self._verify_sequence_locked("artifact_admission_authority_events", len(events))
            self._verify_sequence_locked("artifact_admission_decisions", len(records))
            return previous_hash, tuple(records), snapshots
        except ArtifactAdmissionStoreError:
            raise
        except (sqlite3.DatabaseError, TypeError, ValueError) as exc:
            raise ArtifactAdmissionStoreError(f"artifact admission authority history is invalid: {exc}") from exc

    def _read_verified_locked(
        self,
    ) -> tuple[
        str,
        tuple[ArtifactAdmissionRecord, ...],
        tuple[ArtifactAdmissionRegistryHead, ...],
    ]:
        """Verify all authority tables from one SQLite read snapshot."""
        if self._conn.in_transaction:
            return self._verify_locked()
        try:
            self._conn.execute("BEGIN")
            return self._verify_locked()
        finally:
            if self._conn.in_transaction:
                self._conn.rollback()

    @staticmethod
    def _heads(
        snapshots: tuple[ArtifactAdmissionRegistryHead, ...],
    ) -> dict[RegistryKind, ArtifactAdmissionRegistryHead]:
        heads: dict[RegistryKind, ArtifactAdmissionRegistryHead] = {}
        for snapshot in snapshots:
            heads[snapshot.registry_kind] = snapshot
        return heads

    @staticmethod
    def _last_event_time(
        records: tuple[ArtifactAdmissionRecord, ...],
        snapshots: tuple[ArtifactAdmissionRegistryHead, ...],
    ) -> int | None:
        retained = [
            *(record for record in records),
            *(snapshot for snapshot in snapshots),
        ]
        if not retained:
            return None
        latest = max(retained, key=lambda item: item.authority_event_seq)
        return latest.recorded_at_ms

    def _require_not_future_locked(self, recorded_at_ms: int) -> None:
        authority_now_ms = self._clock_ms()
        _require_recorded_at_ms(authority_now_ms)
        if recorded_at_ms > authority_now_ms + ARTIFACT_ADMISSION_MAX_FUTURE_SKEW_MS:
            raise ArtifactAdmissionStoreFutureClock(
                recorded_at_ms=recorded_at_ms,
                authority_now_ms=authority_now_ms,
            )

    @staticmethod
    def _build_record(
        decision: ArtifactAdmissionDecision,
        *,
        authority_event_seq: int,
        decision_id: str,
        recorded_at_ms: int,
        policy_sha256: str,
        receipt_sha256: str,
        artifact_sha256: str,
        previous_event_hash: str,
    ) -> tuple[ArtifactAdmissionRecord, str]:
        decision_json = _decision_json(decision)
        decision_sha256 = _sha256(decision_json)
        payload_sha256 = _decision_payload_sha256(
            decision_id=decision_id,
            recorded_at_ms=recorded_at_ms,
            policy_sha256=policy_sha256,
            receipt_sha256=receipt_sha256,
            artifact_sha256=artifact_sha256,
            decision_json=decision_json,
            decision_sha256=decision_sha256,
        )
        record_hash = _authority_event_hash(
            event_seq=authority_event_seq,
            event_kind=_DECISION_EVENT_KIND,
            event_id=decision_id,
            recorded_at_ms=recorded_at_ms,
            payload_sha256=payload_sha256,
            previous_event_hash=previous_event_hash,
        )
        return (
            ArtifactAdmissionRecord(
                authority_event_seq=authority_event_seq,
                decision_id=decision_id,
                recorded_at_ms=recorded_at_ms,
                policy_sha256=policy_sha256,
                receipt_sha256=receipt_sha256,
                artifact_sha256=artifact_sha256,
                decision_sha256=decision_sha256,
                decision=decision,
                previous_record_hash=previous_event_hash,
                record_hash=record_hash,
            ),
            payload_sha256,
        )

    @staticmethod
    def _require_expected_head(
        *,
        registry_kind: RegistryKind,
        expected_head: ArtifactAdmissionRegistryHead | None,
        actual_head: ArtifactAdmissionRegistryHead | None,
        required: bool,
    ) -> ArtifactAdmissionRegistryHead | None:
        prefix = registry_kind
        if actual_head is None:
            if required or expected_head is not None:
                raise ArtifactAdmissionStoreRegistryUnprovisioned(
                    f"{prefix}_UNPROVISIONED",
                    expected_head=expected_head,
                    actual_head=None,
                )
            return None
        if expected_head is None:
            if required:
                raise ArtifactAdmissionStoreRegistryHeadMismatch(
                    f"{prefix}_HEAD_REQUIRED",
                    expected_head=None,
                    actual_head=actual_head,
                )
            return actual_head
        if not isinstance(expected_head, ArtifactAdmissionRegistryHead):
            raise TypeError("expected registry head must be a RegistryHead")
        if expected_head.registry_kind != registry_kind:
            raise ArtifactAdmissionStoreRegistryHeadMismatch(
                f"{prefix}_HEAD_KIND_MISMATCH",
                expected_head=expected_head,
                actual_head=actual_head,
            )
        if expected_head != actual_head:
            raise ArtifactAdmissionStoreRegistryHeadChanged(
                f"{prefix}_HEAD_CHANGED",
                expected_head=expected_head,
                actual_head=actual_head,
            )
        return actual_head

    @staticmethod
    def _validate_allow_registry_binding(
        decision: ArtifactAdmissionDecision,
        *,
        policy_head: ArtifactAdmissionRegistryHead,
        receipt_head: ArtifactAdmissionRegistryHead,
    ) -> None:
        bindings = (
            (
                POLICY_REGISTRY_KIND,
                decision.policy_registry_generation,
                decision.policy_registry_sha256,
                decision.policy_registry_event_seq,
                decision.policy_registry_event_hash,
                policy_head,
            ),
            (
                RECEIPT_AUTHORITY_REGISTRY_KIND,
                decision.receipt_authority_registry_generation,
                decision.receipt_authority_registry_sha256,
                decision.receipt_authority_registry_event_seq,
                decision.receipt_authority_registry_event_hash,
                receipt_head,
            ),
        )
        for kind, generation, digest, event_seq, event_hash, head in bindings:
            if (
                generation != head.generation
                or digest != head.registry_sha256
                or event_seq != head.authority_event_seq
                or event_hash != head.event_hash
            ):
                raise ArtifactAdmissionStoreRegistryHeadMismatch(
                    f"{kind}_DECISION_HEAD_MISMATCH",
                    expected_head=head,
                    actual_head=head,
                )

    def append_registry_snapshot(
        self,
        registry_kind: RegistryKind,
        registry_bytes: bytes,
        *,
        recorded_at_ms: int,
        expected_head: ArtifactAdmissionRegistryHead | None,
    ) -> tuple[ArtifactAdmissionRegistryHead, bool]:
        """CAS-append one exact registry snapshot to the authority history."""
        kind = _require_registry_kind(registry_kind)
        generation = _registry_generation(kind, registry_bytes)
        registry_sha256 = _sha256_bytes(registry_bytes)
        _require_recorded_at_ms(recorded_at_ms)
        if expected_head is not None and not isinstance(expected_head, ArtifactAdmissionRegistryHead):
            raise TypeError("expected_head must be a RegistryHead or None")

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._require_not_future_locked(recorded_at_ms)
                authority_hash, records, snapshots = self._verify_locked()
                heads = self._heads(snapshots)
                current = heads.get(kind)
                if current is None:
                    if expected_head is not None:
                        raise ArtifactAdmissionStoreRegistryHeadChanged(
                            f"{kind}_HEAD_CHANGED",
                            expected_head=expected_head,
                            actual_head=None,
                        )
                    if generation != 0:
                        raise ArtifactAdmissionStoreRegistryGenerationGap(
                            f"{kind}_GENERATION_GAP",
                            actual_head=None,
                        )
                else:
                    if expected_head is None or expected_head != current:
                        raise ArtifactAdmissionStoreRegistryHeadChanged(
                            f"{kind}_HEAD_CHANGED",
                            expected_head=expected_head,
                            actual_head=current,
                        )
                    if generation < current.generation:
                        raise ArtifactAdmissionStoreRegistryRollback(
                            f"{kind}_GENERATION_ROLLBACK",
                            expected_head=expected_head,
                            actual_head=current,
                        )
                    if generation == current.generation:
                        if registry_sha256 == current.registry_sha256 and registry_bytes == current.registry_bytes:
                            self._conn.rollback()
                            return current, False
                        raise ArtifactAdmissionStoreRegistryConflict(
                            f"{kind}_GENERATION_CONFLICT",
                            expected_head=expected_head,
                            actual_head=current,
                        )
                    if generation != current.generation + 1:
                        raise ArtifactAdmissionStoreRegistryGenerationGap(
                            f"{kind}_GENERATION_GAP",
                            expected_head=expected_head,
                            actual_head=current,
                        )

                last_time = self._last_event_time(records, snapshots)
                if last_time is not None and recorded_at_ms < last_time:
                    raise ArtifactAdmissionStoreClockRollback("artifact admission authority clock rollback")
                event_seq = len(records) + len(snapshots) + 1
                event_kind = _REGISTRY_EVENT_KIND[kind]
                event_id = _registry_event_id(kind, generation, registry_sha256)
                payload_sha256 = _registry_payload_sha256(kind, generation, registry_sha256)
                event_hash = _authority_event_hash(
                    event_seq=event_seq,
                    event_kind=event_kind,
                    event_id=event_id,
                    recorded_at_ms=recorded_at_ms,
                    payload_sha256=payload_sha256,
                    previous_event_hash=authority_hash,
                )
                self._conn.execute(
                    "INSERT INTO artifact_admission_authority_events"
                    " (event_seq,event_kind,event_id,recorded_at_ms,payload_sha256,"
                    " previous_event_hash,event_hash) VALUES (?,?,?,?,?,?,?)",
                    (
                        event_seq,
                        event_kind,
                        event_id,
                        recorded_at_ms,
                        payload_sha256,
                        authority_hash,
                        event_hash,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO artifact_admission_registry_snapshots"
                    " (authority_event_seq,registry_kind,generation,registry_sha256,"
                    " registry_bytes) VALUES (?,?,?,?,?)",
                    (
                        event_seq,
                        kind,
                        generation,
                        registry_sha256,
                        sqlite3.Binary(registry_bytes),
                    ),
                )
                _, _, retained_snapshots = self._verify_locked()
                inserted = next(
                    snapshot for snapshot in retained_snapshots if snapshot.authority_event_seq == event_seq
                )
                self._conn.commit()
                return inserted, True
            except Exception as exc:
                if self._conn.in_transaction:
                    self._conn.rollback()
                if isinstance(exc, ArtifactAdmissionStoreError):
                    raise
                if isinstance(exc, sqlite3.DatabaseError):
                    raise ArtifactAdmissionStoreError(
                        f"artifact admission registry could not be recorded: {exc}"
                    ) from exc
                raise

    def registry_snapshots(
        self,
        registry_kind: RegistryKind | None = None,
    ) -> tuple[ArtifactAdmissionRegistryHead, ...]:
        kind = _require_registry_kind(registry_kind) if registry_kind is not None else None
        with self._lock:
            _, _, snapshots = self._read_verified_locked()
        if kind is None:
            return snapshots
        return tuple(snapshot for snapshot in snapshots if snapshot.registry_kind == kind)

    def registry_heads(
        self,
    ) -> dict[RegistryKind, ArtifactAdmissionRegistryHead]:
        """Return all retained heads from one consistent read snapshot."""
        with self._lock:
            _, _, snapshots = self._read_verified_locked()
            return self._heads(snapshots)

    def registry_head(self, registry_kind: RegistryKind) -> ArtifactAdmissionRegistryHead:
        kind = _require_registry_kind(registry_kind)
        snapshots = self.registry_snapshots(kind)
        if not snapshots:
            raise ArtifactAdmissionStoreRegistryUnprovisioned(f"{kind}_UNPROVISIONED")
        return snapshots[-1]

    def record(
        self,
        decision: ArtifactAdmissionDecision,
        *,
        policy_sha256: str,
        receipt_sha256: str,
        artifact_sha256: str,
        recorded_at_ms: int,
        decision_id: str | None = None,
        expected_policy_registry_head: ArtifactAdmissionRegistryHead | None = None,
        expected_receipt_authority_registry_head: (ArtifactAdmissionRegistryHead | None) = None,
    ) -> tuple[ArtifactAdmissionRecord, bool]:
        """CAS-append a decision against exact retained registry heads."""
        decision_json = _decision_json(decision)
        _require_recorded_at_ms(recorded_at_ms)
        policy_sha256 = _require_optional_sha256("policy_sha256", policy_sha256)
        receipt_sha256 = _require_optional_sha256("receipt_sha256", receipt_sha256)
        artifact_sha256 = _require_optional_sha256("artifact_sha256", artifact_sha256)
        _validate_decision_binding(
            decision,
            policy_sha256=policy_sha256,
            receipt_sha256=receipt_sha256,
            artifact_sha256=artifact_sha256,
        )
        resolved_id = _default_decision_id(decision_json, recorded_at_ms) if decision_id is None else decision_id
        if (
            not isinstance(resolved_id, str)
            or not resolved_id
            or resolved_id != resolved_id.strip()
            or len(resolved_id) > 255
        ):
            raise ValueError("decision_id must be a non-empty canonical string")
        if decision.authority_decision_id and decision.authority_decision_id != resolved_id:
            raise ValueError("decision_id does not match the decision authority id")
        _validate_allow_authority_binding(
            decision,
            recorded_at_ms=recorded_at_ms,
            decision_id=resolved_id,
        )

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._require_not_future_locked(recorded_at_ms)
                authority_hash, records, snapshots = self._verify_locked()
                heads = self._heads(snapshots)
                require_heads = decision.verdict == "ALLOW"
                policy_head = self._require_expected_head(
                    registry_kind=POLICY_REGISTRY_KIND,
                    expected_head=expected_policy_registry_head,
                    actual_head=heads.get(POLICY_REGISTRY_KIND),
                    required=require_heads,
                )
                receipt_head = self._require_expected_head(
                    registry_kind=RECEIPT_AUTHORITY_REGISTRY_KIND,
                    expected_head=expected_receipt_authority_registry_head,
                    actual_head=heads.get(RECEIPT_AUTHORITY_REGISTRY_KIND),
                    required=require_heads,
                )
                if require_heads:
                    assert policy_head is not None
                    assert receipt_head is not None
                    self._validate_allow_registry_binding(
                        decision,
                        policy_head=policy_head,
                        receipt_head=receipt_head,
                    )

                existing = next(
                    (record for record in records if record.decision_id == resolved_id),
                    None,
                )
                if existing is not None:
                    replay, _ = self._build_record(
                        decision,
                        authority_event_seq=existing.authority_event_seq,
                        decision_id=resolved_id,
                        recorded_at_ms=recorded_at_ms,
                        policy_sha256=policy_sha256,
                        receipt_sha256=receipt_sha256,
                        artifact_sha256=artifact_sha256,
                        previous_event_hash=existing.previous_record_hash,
                    )
                    if replay == existing:
                        self._conn.rollback()
                        return existing, False
                    raise ArtifactAdmissionStoreConflict(f"artifact admission decision conflict: {resolved_id}")

                last_time = self._last_event_time(records, snapshots)
                if last_time is not None and recorded_at_ms < last_time:
                    raise ArtifactAdmissionStoreClockRollback("artifact admission authority clock rollback")

                event_seq = len(records) + len(snapshots) + 1
                record, payload_sha256 = self._build_record(
                    decision,
                    authority_event_seq=event_seq,
                    decision_id=resolved_id,
                    recorded_at_ms=recorded_at_ms,
                    policy_sha256=policy_sha256,
                    receipt_sha256=receipt_sha256,
                    artifact_sha256=artifact_sha256,
                    previous_event_hash=authority_hash,
                )
                self._conn.execute(
                    "INSERT INTO artifact_admission_authority_events"
                    " (event_seq,event_kind,event_id,recorded_at_ms,payload_sha256,"
                    " previous_event_hash,event_hash) VALUES (?,?,?,?,?,?,?)",
                    (
                        record.authority_event_seq,
                        _DECISION_EVENT_KIND,
                        record.decision_id,
                        record.recorded_at_ms,
                        payload_sha256,
                        record.previous_record_hash,
                        record.record_hash,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO artifact_admission_decisions"
                    " (authority_event_seq,decision_id,recorded_at_ms,policy_sha256,"
                    " receipt_sha256,artifact_sha256,decision_json,decision_sha256)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (
                        record.authority_event_seq,
                        record.decision_id,
                        record.recorded_at_ms,
                        record.policy_sha256,
                        record.receipt_sha256,
                        record.artifact_sha256,
                        decision_json,
                        record.decision_sha256,
                    ),
                )
                _, retained_records, _ = self._verify_locked()
                retained = next(item for item in retained_records if item.authority_event_seq == event_seq)
                self._conn.commit()
                return retained, True
            except Exception as exc:
                if self._conn.in_transaction:
                    self._conn.rollback()
                if isinstance(exc, ArtifactAdmissionStoreError):
                    raise
                if isinstance(exc, sqlite3.DatabaseError):
                    raise ArtifactAdmissionStoreError(
                        f"artifact admission decision could not be recorded: {exc}"
                    ) from exc
                raise

    def record_decision(
        self,
        decision: ArtifactAdmissionDecision,
        *,
        policy_sha256: str,
        receipt_sha256: str,
        artifact_sha256: str,
        recorded_at_ms: int,
        decision_id: str | None = None,
        expected_policy_registry_head: ArtifactAdmissionRegistryHead | None = None,
        expected_receipt_authority_registry_head: (ArtifactAdmissionRegistryHead | None) = None,
    ) -> tuple[ArtifactAdmissionRecord, bool]:
        """Named alias for callers that prefer an explicit command verb."""
        return self.record(
            decision,
            policy_sha256=policy_sha256,
            receipt_sha256=receipt_sha256,
            artifact_sha256=artifact_sha256,
            recorded_at_ms=recorded_at_ms,
            decision_id=decision_id,
            expected_policy_registry_head=expected_policy_registry_head,
            expected_receipt_authority_registry_head=(expected_receipt_authority_registry_head),
        )

    def decisions(self) -> tuple[ArtifactAdmissionRecord, ...]:
        with self._lock:
            _, records, _ = self._read_verified_locked()
            return records

    def authority_time_floor(self) -> int | None:
        """Return the latest retained logical time from one read snapshot."""
        with self._lock:
            _, records, snapshots = self._read_verified_locked()
            return self._last_event_time(records, snapshots)

    def latest(self) -> ArtifactAdmissionRecord | None:
        records = self.decisions()
        return records[-1] if records else None

    def latest_decision(self) -> ArtifactAdmissionRecord | None:
        """Compatibility alias for stores that use the longer query name."""
        return self.latest()

    def chain_valid(self) -> bool:
        try:
            with self._lock:
                self._read_verified_locked()
            return True
        except (
            ArtifactAdmissionStoreError,
            sqlite3.DatabaseError,
            ValueError,
            TypeError,
        ):
            return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "ARTIFACT_ADMISSION_STORE_KIND",
    "ARTIFACT_ADMISSION_STORE_SCHEMA_VERSION",
    "ARTIFACT_ADMISSION_MAX_FUTURE_SKEW_MS",
    "POLICY_REGISTRY_KIND",
    "RECEIPT_AUTHORITY_REGISTRY_KIND",
    "RegistryKind",
    "ArtifactAdmissionRecord",
    "ArtifactAdmissionRegistryHead",
    "ArtifactAdmissionStore",
    "ArtifactAdmissionStoreClockRollback",
    "ArtifactAdmissionStoreFutureClock",
    "ArtifactAdmissionStoreConflict",
    "ArtifactAdmissionStoreError",
    "ArtifactAdmissionStoreRegistryConflict",
    "ArtifactAdmissionStoreRegistryGenerationGap",
    "ArtifactAdmissionStoreRegistryHeadChanged",
    "ArtifactAdmissionStoreRegistryHeadMismatch",
    "ArtifactAdmissionStoreRegistryRollback",
    "ArtifactAdmissionStoreRegistryUnprovisioned",
]
