"""Append-only authority for recurring QUANTOS L2 restore evidence.

The authority records attempt starts and immutable terminal outcomes in one
hash chain.  It stores digests and structured proof only: no passphrases,
destination paths, arbitrary exception text, network, broker, or execution
capability belongs in this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from typing import Callable, Literal


L2_RESTORE_HISTORY_STORE_KIND = "QUANTOS_L2_RESTORE_HISTORY_AUTHORITY"
L2_RESTORE_HISTORY_STORE_SCHEMA_VERSION = 1
L2_RESTORE_HISTORY_MAX_FUTURE_SKEW_MS = 5 * 60_000

_STARTED = "STARTED"
_OUTCOME = "OUTCOME"
_OPERATION_KIND = "BACKUP_AND_ISOLATED_RESTORE"
_MODE = "L2_AUTHORITY_STATE"
_EXTERNAL_DESTINATIONS = frozenset({"OFF_HOST_UNC", "SEPARATE_VOLUME"})
_DESTINATION_CLASSES = frozenset(
    {*_EXTERNAL_DESTINATIONS, "LOCAL_ONLY", "UNVERIFIED"}
)
_STAGES = frozenset(
    {
        "COMPLETE",
        "PREFLIGHT",
        "SNAPSHOT",
        "ENCRYPTION",
        "RESTORE_VERIFICATION",
        "PUBLISH",
        "AUTHORITY",
        "TIMEOUT",
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


def _local_authority_clock_ms() -> Callable[[], int]:
    base_wall_ms = time.time_ns() // 1_000_000
    base_monotonic_ns = time.monotonic_ns()

    def current() -> int:
        elapsed_ms = (time.monotonic_ns() - base_monotonic_ns) // 1_000_000
        return base_wall_ms + elapsed_ms

    return current


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


def _require_sha256(name: str, value: str, *, optional: bool = False) -> str:
    if optional and value == "":
        return value
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _require_nonnegative_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _require_positive_int(name: str, value: int) -> int:
    _require_nonnegative_int(name, value)
    if value == 0:
        raise ValueError(f"{name} must be positive")
    return value


def _require_bool(name: str, value: bool) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be a bool")
    return value


class L2RestoreHistoryStoreError(RuntimeError):
    """The restore-history authority is unavailable or unverifiable."""


class L2RestoreHistoryConflict(L2RestoreHistoryStoreError):
    """An immutable id already binds different content."""


class L2RestoreHistoryClockRollback(L2RestoreHistoryStoreError):
    """A proposed event predates the retained authority time floor."""


class L2RestoreHistoryFutureClock(L2RestoreHistoryStoreError):
    """A proposed event is too far ahead of the authority clock."""

    reason_code = "L2_RESTORE_HISTORY_FUTURE_CLOCK"

    def __init__(self, *, recorded_at_ms: int, authority_now_ms: int) -> None:
        super().__init__(self.reason_code)
        self.recorded_at_ms = recorded_at_ms
        self.authority_now_ms = authority_now_ms


class L2RestoreHistoryHeadChanged(L2RestoreHistoryStoreError):
    """The global authority head changed after the caller read it."""

    def __init__(
        self,
        *,
        expected_head: AuthorityHead | None,
        actual_head: AuthorityHead | None,
    ) -> None:
        super().__init__("RESTORE_HISTORY_HEAD_CHANGED")
        self.expected_head = expected_head
        self.actual_head = actual_head


class L2RestoreHistoryActiveAttempt(L2RestoreHistoryStoreError):
    """A second restore attempt cannot overlap the current open attempt."""


class L2RestoreHistoryStartMismatch(L2RestoreHistoryStoreError):
    """A terminal outcome did not bind the exact retained start record."""


class L2RestoreHistoryAttemptNotFound(L2RestoreHistoryStoreError):
    """No retained start record exists for the requested attempt."""


class L2RestoreHistoryNotOverdue(L2RestoreHistoryStoreError):
    """An open attempt has not exceeded its explicit timeout."""


@dataclass(frozen=True)
class AuthorityHead:
    event_seq: int
    event_hash: str
    recorded_at_ms: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.event_seq, bool)
            or not isinstance(self.event_seq, int)
            or self.event_seq <= 0
        ):
            raise ValueError("event_seq must be a positive integer")
        _require_sha256("event_hash", self.event_hash)
        _require_nonnegative_int("recorded_at_ms", self.recorded_at_ms)


@dataclass(frozen=True)
class RestoreAttempt:
    attempt_id: str
    started_at_ms: int
    operation_kind: str
    mode: str
    source_contract_sha256: str
    destination_ref_sha256: str
    rto_ms: int
    max_rpo_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or _IDENTIFIER.fullmatch(
            self.attempt_id
        ) is None:
            raise ValueError("attempt_id must be a canonical identifier")
        if self.operation_kind != _OPERATION_KIND:
            raise ValueError("unsupported restore operation kind")
        if self.mode != _MODE:
            raise ValueError("unsupported restore mode")
        _require_nonnegative_int("started_at_ms", self.started_at_ms)
        _require_sha256("source_contract_sha256", self.source_contract_sha256)
        _require_sha256("destination_ref_sha256", self.destination_ref_sha256)
        _require_positive_int("rto_ms", self.rto_ms)
        _require_positive_int("max_rpo_ms", self.max_rpo_ms)


@dataclass(frozen=True)
class RestoreProof:
    source_contract_sha256: str
    destination_class: str
    external_destination_verified: bool
    encrypted: bool
    manifest_verified: bool
    source_contract_verified: bool
    restore_verified: bool
    sqlite_integrity_verified: bool
    restore_elapsed_ms: int | None
    source_age_ms: int | None
    rto_met: bool
    rpo_met: bool
    batch_sha256: str

    def __post_init__(self) -> None:
        _require_sha256("source_contract_sha256", self.source_contract_sha256)
        if self.destination_class not in _DESTINATION_CLASSES:
            raise ValueError("unsupported destination_class")
        for name in (
            "external_destination_verified",
            "encrypted",
            "manifest_verified",
            "source_contract_verified",
            "restore_verified",
            "sqlite_integrity_verified",
            "rto_met",
            "rpo_met",
        ):
            _require_bool(name, getattr(self, name))
        for name in ("restore_elapsed_ms", "source_age_ms"):
            value = getattr(self, name)
            if value is not None:
                _require_nonnegative_int(name, value)
        _require_sha256("batch_sha256", self.batch_sha256, optional=True)
        if self.external_destination_verified and (
            self.destination_class not in _EXTERNAL_DESTINATIONS
        ):
            raise ValueError(
                "external_destination_verified requires an external destination"
            )
        if self.rto_met and self.restore_elapsed_ms is None:
            raise ValueError("rto_met requires restore_elapsed_ms")
        if self.rpo_met and self.source_age_ms is None:
            raise ValueError("rpo_met requires source_age_ms")


@dataclass(frozen=True)
class RestoreOutcome:
    attempt_id: str
    completed_at_ms: int
    verdict: Literal["PASS", "FAIL"]
    stage: str
    reason_code: str
    proof: RestoreProof

    def __post_init__(self) -> None:
        if not isinstance(self.attempt_id, str) or _IDENTIFIER.fullmatch(
            self.attempt_id
        ) is None:
            raise ValueError("attempt_id must be a canonical identifier")
        _require_nonnegative_int("completed_at_ms", self.completed_at_ms)
        if self.verdict not in {"PASS", "FAIL"}:
            raise ValueError("verdict must be PASS or FAIL")
        if self.stage not in _STAGES:
            raise ValueError("unsupported restore stage")
        if not isinstance(self.reason_code, str) or _REASON_CODE.fullmatch(
            self.reason_code
        ) is None:
            raise ValueError("reason_code must be a stable redacted code")
        if not isinstance(self.proof, RestoreProof):
            raise TypeError("proof must be a RestoreProof")
        if self.verdict == "PASS":
            required_flags = (
                self.proof.external_destination_verified,
                self.proof.encrypted,
                self.proof.manifest_verified,
                self.proof.source_contract_verified,
                self.proof.restore_verified,
                self.proof.sqlite_integrity_verified,
                self.proof.rto_met,
                self.proof.rpo_met,
            )
            if (
                self.stage != "COMPLETE"
                or self.reason_code != "RESTORE_VERIFIED"
                or self.proof.destination_class not in _EXTERNAL_DESTINATIONS
                or not all(required_flags)
                or self.proof.restore_elapsed_ms is None
                or self.proof.source_age_ms is None
                or not self.proof.batch_sha256
            ):
                raise ValueError("PASS requires complete external restore proof")
        elif self.stage == "COMPLETE" or self.reason_code == "RESTORE_VERIFIED":
            raise ValueError("FAIL cannot claim complete verified restore proof")


def _attempt_json(attempt: RestoreAttempt) -> str:
    if not isinstance(attempt, RestoreAttempt):
        raise TypeError("attempt must be a RestoreAttempt")
    return _canonical(asdict(attempt))


def _proof_json(proof: RestoreProof) -> str:
    return _canonical(asdict(proof))


def _outcome_json(outcome: RestoreOutcome) -> str:
    if not isinstance(outcome, RestoreOutcome):
        raise TypeError("outcome must be a RestoreOutcome")
    return _canonical(asdict(outcome))


def _attempt_from_json(value: str) -> RestoreAttempt:
    raw = json.loads(value)
    expected = {
        "attempt_id",
        "started_at_ms",
        "operation_kind",
        "mode",
        "source_contract_sha256",
        "destination_ref_sha256",
        "rto_ms",
        "max_rpo_ms",
    }
    if not isinstance(raw, dict) or set(raw) != expected or _canonical(raw) != value:
        raise ValueError("attempt JSON is not canonical or strict")
    return RestoreAttempt(**raw)


def _proof_from_dict(raw: object) -> RestoreProof:
    expected = {
        "source_contract_sha256",
        "destination_class",
        "external_destination_verified",
        "encrypted",
        "manifest_verified",
        "source_contract_verified",
        "restore_verified",
        "sqlite_integrity_verified",
        "restore_elapsed_ms",
        "source_age_ms",
        "rto_met",
        "rpo_met",
        "batch_sha256",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("proof JSON is not strict")
    return RestoreProof(**raw)


def _outcome_from_json(value: str) -> RestoreOutcome:
    raw = json.loads(value)
    expected = {
        "attempt_id",
        "completed_at_ms",
        "verdict",
        "stage",
        "reason_code",
        "proof",
    }
    if not isinstance(raw, dict) or set(raw) != expected or _canonical(raw) != value:
        raise ValueError("outcome JSON is not canonical or strict")
    proof = _proof_from_dict(raw.pop("proof"))
    return RestoreOutcome(proof=proof, **raw)


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


def _started_event_id(attempt_id: str) -> str:
    return f"attempt:{attempt_id}:started"


def _outcome_event_id(attempt_id: str) -> str:
    return f"attempt:{attempt_id}:outcome"


def _attempt_payload_sha256(policy_json: str, policy_sha256: str) -> str:
    return _sha256_text(
        _canonical(
            {
                "attempt": json.loads(policy_json),
                "policy_sha256": policy_sha256,
            }
        )
    )


def _outcome_payload_sha256(
    *,
    outcome_json: str,
    outcome_sha256: str,
    proof_sha256: str,
    started_event_seq: int,
    started_event_hash: str,
) -> str:
    return _sha256_text(
        _canonical(
            {
                "outcome": json.loads(outcome_json),
                "outcome_sha256": outcome_sha256,
                "proof_sha256": proof_sha256,
                "started_event_hash": started_event_hash,
                "started_event_seq": started_event_seq,
            }
        )
    )


@dataclass(frozen=True)
class AttemptRecord:
    authority_event_seq: int
    attempt: RestoreAttempt
    policy_sha256: str
    previous_event_hash: str
    event_hash: str

    def as_head(self) -> AuthorityHead:
        return AuthorityHead(
            event_seq=self.authority_event_seq,
            event_hash=self.event_hash,
            recorded_at_ms=self.attempt.started_at_ms,
        )


@dataclass(frozen=True)
class OutcomeRecord:
    authority_event_seq: int
    outcome: RestoreOutcome
    outcome_sha256: str
    proof_sha256: str
    started_event_seq: int
    started_event_hash: str
    previous_event_hash: str
    event_hash: str

    def as_head(self) -> AuthorityHead:
        return AuthorityHead(
            event_seq=self.authority_event_seq,
            event_hash=self.event_hash,
            recorded_at_ms=self.outcome.completed_at_ms,
        )


@dataclass(frozen=True)
class RestoreHistorySnapshot:
    attempts: tuple[AttemptRecord, ...]
    outcomes: tuple[OutcomeRecord, ...]
    open_attempts: tuple[AttemptRecord, ...]
    authority_head: AuthorityHead | None
    authority_time_floor_ms: int | None


@dataclass(frozen=True)
class _AuthorityEvent:
    event_seq: int
    event_kind: str
    event_id: str
    recorded_at_ms: int
    payload_sha256: str
    previous_event_hash: str
    event_hash: str


@dataclass(frozen=True)
class RestoreCadencePolicy:
    source_contract_sha256: str
    max_attempt_age_ms: int
    max_success_age_ms: int
    max_attempt_gap_ms: int
    max_success_gap_ms: int
    minimum_history_span_ms: int
    minimum_successes: int
    clean_lookback_ms: int
    attempt_timeout_ms: int
    operation_kind: str = _OPERATION_KIND
    mode: str = _MODE

    def __post_init__(self) -> None:
        _require_sha256("source_contract_sha256", self.source_contract_sha256)
        if self.operation_kind != _OPERATION_KIND or self.mode != _MODE:
            raise ValueError("unsupported restore cadence contract")
        for name in (
            "max_attempt_age_ms",
            "max_success_age_ms",
            "max_attempt_gap_ms",
            "max_success_gap_ms",
            "clean_lookback_ms",
            "attempt_timeout_ms",
        ):
            _require_positive_int(name, getattr(self, name))
        _require_nonnegative_int(
            "minimum_history_span_ms", self.minimum_history_span_ms
        )
        _require_positive_int("minimum_successes", self.minimum_successes)


@dataclass(frozen=True)
class RestoreHistoryHealth:
    verdict: Literal["PASS", "BREACH", "UNAVAILABLE"]
    reason_codes: tuple[str, ...]
    attempt_count: int
    success_count: int
    failure_count_in_clean_window: int
    overdue_open_count: int
    latest_terminal_verdict: str | None
    latest_attempt_age_ms: int | None
    latest_success_age_ms: int | None
    maximum_attempt_gap_ms: int | None
    maximum_success_gap_ms: int | None
    history_span_ms: int
    authority_head: AuthorityHead | None


_TRIGGERS = frozenset(
    {
        "l2_restore_history_meta_no_update",
        "l2_restore_history_meta_no_delete",
        "l2_restore_history_events_no_update",
        "l2_restore_history_events_no_delete",
        "l2_restore_history_attempts_no_update",
        "l2_restore_history_attempts_no_delete",
        "l2_restore_history_outcomes_no_update",
        "l2_restore_history_outcomes_no_delete",
    }
)

_EXPECTED_COLUMNS = {
    "l2_restore_history_meta": ("singleton", "store_kind", "schema_version"),
    "l2_restore_history_events": (
        "event_seq",
        "event_kind",
        "event_id",
        "recorded_at_ms",
        "payload_sha256",
        "previous_event_hash",
        "event_hash",
    ),
    "l2_restore_attempts": (
        "attempt_seq",
        "authority_event_seq",
        "attempt_id",
        "started_at_ms",
        "operation_kind",
        "mode",
        "source_contract_sha256",
        "destination_ref_sha256",
        "rto_ms",
        "max_rpo_ms",
        "policy_json",
        "policy_sha256",
    ),
    "l2_restore_outcomes": (
        "outcome_seq",
        "authority_event_seq",
        "attempt_id",
        "started_event_seq",
        "started_event_hash",
        "completed_at_ms",
        "verdict",
        "stage",
        "reason_code",
        "proof_json",
        "proof_sha256",
        "outcome_json",
        "outcome_sha256",
    ),
}

_SCHEMA = f"""
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS l2_restore_history_meta (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  store_kind TEXT NOT NULL,
  schema_version INTEGER NOT NULL CHECK (
    schema_version = {L2_RESTORE_HISTORY_STORE_SCHEMA_VERSION}
  )
);
INSERT OR IGNORE INTO l2_restore_history_meta
  (singleton, store_kind, schema_version)
VALUES (
  1,
  '{L2_RESTORE_HISTORY_STORE_KIND}',
  {L2_RESTORE_HISTORY_STORE_SCHEMA_VERSION}
);
CREATE TABLE IF NOT EXISTS l2_restore_history_events (
  event_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_kind TEXT NOT NULL CHECK (event_kind IN ('STARTED', 'OUTCOME')),
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
CREATE TABLE IF NOT EXISTS l2_restore_attempts (
  attempt_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  authority_event_seq INTEGER UNIQUE NOT NULL,
  attempt_id TEXT UNIQUE NOT NULL CHECK (length(attempt_id) BETWEEN 1 AND 255),
  started_at_ms INTEGER NOT NULL CHECK (started_at_ms >= 0),
  operation_kind TEXT NOT NULL CHECK (
    operation_kind = 'BACKUP_AND_ISOLATED_RESTORE'
  ),
  mode TEXT NOT NULL CHECK (mode = 'L2_AUTHORITY_STATE'),
  source_contract_sha256 TEXT NOT NULL CHECK (
    length(source_contract_sha256) = 64
    AND source_contract_sha256 NOT GLOB '*[^0-9a-f]*'
  ),
  destination_ref_sha256 TEXT NOT NULL CHECK (
    length(destination_ref_sha256) = 64
    AND destination_ref_sha256 NOT GLOB '*[^0-9a-f]*'
  ),
  rto_ms INTEGER NOT NULL CHECK (rto_ms > 0),
  max_rpo_ms INTEGER NOT NULL CHECK (max_rpo_ms > 0),
  policy_json TEXT NOT NULL,
  policy_sha256 TEXT NOT NULL CHECK (
    length(policy_sha256) = 64 AND policy_sha256 NOT GLOB '*[^0-9a-f]*'
  ),
  FOREIGN KEY (authority_event_seq)
    REFERENCES l2_restore_history_events(event_seq)
    ON UPDATE RESTRICT ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS l2_restore_outcomes (
  outcome_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  authority_event_seq INTEGER UNIQUE NOT NULL,
  attempt_id TEXT UNIQUE NOT NULL,
  started_event_seq INTEGER UNIQUE NOT NULL,
  started_event_hash TEXT NOT NULL CHECK (
    length(started_event_hash) = 64
    AND started_event_hash NOT GLOB '*[^0-9a-f]*'
  ),
  completed_at_ms INTEGER NOT NULL CHECK (completed_at_ms >= 0),
  verdict TEXT NOT NULL CHECK (verdict IN ('PASS', 'FAIL')),
  stage TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  proof_json TEXT NOT NULL,
  proof_sha256 TEXT NOT NULL CHECK (
    length(proof_sha256) = 64 AND proof_sha256 NOT GLOB '*[^0-9a-f]*'
  ),
  outcome_json TEXT NOT NULL,
  outcome_sha256 TEXT NOT NULL CHECK (
    length(outcome_sha256) = 64 AND outcome_sha256 NOT GLOB '*[^0-9a-f]*'
  ),
  FOREIGN KEY (authority_event_seq)
    REFERENCES l2_restore_history_events(event_seq)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  FOREIGN KEY (attempt_id)
    REFERENCES l2_restore_attempts(attempt_id)
    ON UPDATE RESTRICT ON DELETE RESTRICT,
  FOREIGN KEY (started_event_seq)
    REFERENCES l2_restore_attempts(authority_event_seq)
    ON UPDATE RESTRICT ON DELETE RESTRICT
);
CREATE TRIGGER IF NOT EXISTS l2_restore_history_meta_no_update
BEFORE UPDATE ON l2_restore_history_meta
BEGIN SELECT RAISE(ABORT, 'restore history metadata is immutable'); END;
CREATE TRIGGER IF NOT EXISTS l2_restore_history_meta_no_delete
BEFORE DELETE ON l2_restore_history_meta
BEGIN SELECT RAISE(ABORT, 'restore history metadata is immutable'); END;
CREATE TRIGGER IF NOT EXISTS l2_restore_history_events_no_update
BEFORE UPDATE ON l2_restore_history_events
BEGIN SELECT RAISE(ABORT, 'restore history events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS l2_restore_history_events_no_delete
BEFORE DELETE ON l2_restore_history_events
BEGIN SELECT RAISE(ABORT, 'restore history events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS l2_restore_history_attempts_no_update
BEFORE UPDATE ON l2_restore_attempts
BEGIN SELECT RAISE(ABORT, 'restore history attempts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS l2_restore_history_attempts_no_delete
BEFORE DELETE ON l2_restore_attempts
BEGIN SELECT RAISE(ABORT, 'restore history attempts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS l2_restore_history_outcomes_no_update
BEFORE UPDATE ON l2_restore_outcomes
BEGIN SELECT RAISE(ABORT, 'restore history outcomes are append-only'); END;
CREATE TRIGGER IF NOT EXISTS l2_restore_history_outcomes_no_delete
BEFORE DELETE ON l2_restore_outcomes
BEGIN SELECT RAISE(ABORT, 'restore history outcomes are append-only'); END;
COMMIT;
"""


class L2RestoreHistoryStore:
    """Single-node, append-only restore-history authority."""

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
            raise L2RestoreHistoryStoreError(
                f"restore history authority unavailable: {path}"
            )
        if create and path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)

        self._conn = sqlite3.connect(
            path,
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._path = path
        self._clock_ms = clock_ms or _local_authority_clock_ms()
        try:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout=5000")
                self._conn.execute("PRAGMA foreign_keys=ON")
                if create:
                    mode = str(
                        self._conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                    )
                    if path != ":memory:" and mode.lower() != "wal":
                        raise L2RestoreHistoryStoreError(
                            "restore history authority requires WAL"
                        )
                    self._conn.execute("PRAGMA synchronous=FULL")
                    self._conn.executescript(_SCHEMA)
                else:
                    self._conn.execute("PRAGMA synchronous=FULL")
                if create:
                    self._verify_durability_locked()
                self._read_verified_locked()
        except BaseException as exc:
            self._conn.close()
            if isinstance(
                exc,
                (L2RestoreHistoryStoreError, ValueError, TypeError),
            ):
                raise
            raise L2RestoreHistoryStoreError(
                f"restore history authority unavailable: {exc}"
            ) from exc

    def _verify_durability_locked(self) -> None:
        if int(self._conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise L2RestoreHistoryStoreError(
                "restore history authority requires foreign_keys=ON"
            )
        mode = str(self._conn.execute("PRAGMA journal_mode").fetchone()[0])
        if self._path != ":memory:" and mode.lower() != "wal":
            raise L2RestoreHistoryStoreError(
                "restore history authority requires WAL"
            )
        if int(self._conn.execute("PRAGMA synchronous").fetchone()[0]) != 2:
            raise L2RestoreHistoryStoreError(
                "restore history authority requires synchronous=FULL"
            )

    def _verify_schema_locked(self) -> None:
        try:
            tables = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            if tables != set(_EXPECTED_COLUMNS):
                raise L2RestoreHistoryStoreError(
                    "restore history authority schema tables are invalid"
                )
            self._verify_durability_locked()
            rows = self._conn.execute(
                "SELECT singleton,store_kind,schema_version "
                "FROM l2_restore_history_meta"
            ).fetchall()
            expected_meta = (
                1,
                L2_RESTORE_HISTORY_STORE_KIND,
                L2_RESTORE_HISTORY_STORE_SCHEMA_VERSION,
            )
            if len(rows) != 1 or tuple(rows[0]) != expected_meta:
                raise L2RestoreHistoryStoreError(
                    "restore history authority metadata is invalid"
                )
            for table, expected in _EXPECTED_COLUMNS.items():
                columns = tuple(
                    str(row["name"])
                    for row in self._conn.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                )
                if columns != expected:
                    raise L2RestoreHistoryStoreError(
                        f"restore history authority schema is invalid: {table}"
                    )
            triggers = {
                str(row["name"])
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='trigger' AND name LIKE 'l2_restore_history_%'"
                ).fetchall()
            }
            if triggers != _TRIGGERS:
                raise L2RestoreHistoryStoreError(
                    "restore history authority triggers are invalid"
                )
            foreign_keys = {
                (
                    table,
                    str(row["table"]),
                    str(row["from"]),
                    str(row["to"]),
                    str(row["on_update"]),
                    str(row["on_delete"]),
                )
                for table in ("l2_restore_attempts", "l2_restore_outcomes")
                for row in self._conn.execute(
                    f"PRAGMA foreign_key_list({table})"
                ).fetchall()
            }
            expected_foreign_keys = {
                (
                    "l2_restore_attempts",
                    "l2_restore_history_events",
                    "authority_event_seq",
                    "event_seq",
                    "RESTRICT",
                    "RESTRICT",
                ),
                (
                    "l2_restore_outcomes",
                    "l2_restore_history_events",
                    "authority_event_seq",
                    "event_seq",
                    "RESTRICT",
                    "RESTRICT",
                ),
                (
                    "l2_restore_outcomes",
                    "l2_restore_attempts",
                    "attempt_id",
                    "attempt_id",
                    "RESTRICT",
                    "RESTRICT",
                ),
                (
                    "l2_restore_outcomes",
                    "l2_restore_attempts",
                    "started_event_seq",
                    "authority_event_seq",
                    "RESTRICT",
                    "RESTRICT",
                ),
            }
            if foreign_keys != expected_foreign_keys:
                raise L2RestoreHistoryStoreError(
                    "restore history authority foreign-key schema is invalid"
                )
            if self._conn.execute("PRAGMA foreign_key_check").fetchall():
                raise L2RestoreHistoryStoreError(
                    "restore history authority foreign-key integrity is invalid"
                )
        except L2RestoreHistoryStoreError:
            raise
        except sqlite3.DatabaseError as exc:
            raise L2RestoreHistoryStoreError(
                f"restore history authority schema is unreadable: {exc}"
            ) from exc

    def _event_rows_locked(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT event_seq,event_kind,event_id,recorded_at_ms,payload_sha256,"
            " previous_event_hash,event_hash FROM l2_restore_history_events "
            "ORDER BY event_seq"
        ).fetchall()

    def _attempt_rows_locked(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT a.attempt_seq,a.authority_event_seq,a.attempt_id,"
            " a.started_at_ms,a.operation_kind,a.mode,a.source_contract_sha256,"
            " a.destination_ref_sha256,a.rto_ms,a.max_rpo_ms,a.policy_json,"
            " a.policy_sha256,e.event_kind,e.event_id,e.recorded_at_ms,"
            " e.payload_sha256,e.previous_event_hash,e.event_hash "
            "FROM l2_restore_attempts AS a JOIN l2_restore_history_events AS e "
            "ON e.event_seq=a.authority_event_seq ORDER BY a.attempt_seq"
        ).fetchall()

    def _outcome_rows_locked(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT o.outcome_seq,o.authority_event_seq,o.attempt_id,"
            " o.started_event_seq,o.started_event_hash,o.completed_at_ms,"
            " o.verdict,o.stage,o.reason_code,o.proof_json,o.proof_sha256,"
            " o.outcome_json,o.outcome_sha256,e.event_kind,e.event_id,"
            " e.recorded_at_ms,e.payload_sha256,e.previous_event_hash,"
            " e.event_hash FROM l2_restore_outcomes AS o "
            "JOIN l2_restore_history_events AS e "
            "ON e.event_seq=o.authority_event_seq ORDER BY o.outcome_seq"
        ).fetchall()

    def _sequence_is_exact_locked(self, table: str, count: int) -> bool:
        row = self._conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name=?",
            (table,),
        ).fetchone()
        if count == 0:
            return row is None or int(row["seq"]) == 0
        return row is not None and int(row["seq"]) == count

    def _read_verified_locked(self) -> RestoreHistorySnapshot:
        self._verify_schema_locked()
        try:
            event_rows = self._event_rows_locked()
            events: dict[int, _AuthorityEvent] = {}
            previous_hash = ""
            previous_time = -1
            for expected_seq, row in enumerate(event_rows, start=1):
                event = _AuthorityEvent(
                    event_seq=row["event_seq"],
                    event_kind=row["event_kind"],
                    event_id=row["event_id"],
                    recorded_at_ms=row["recorded_at_ms"],
                    payload_sha256=row["payload_sha256"],
                    previous_event_hash=row["previous_event_hash"],
                    event_hash=row["event_hash"],
                )
                expected_hash = _event_hash(
                    event_seq=event.event_seq,
                    event_kind=event.event_kind,
                    event_id=event.event_id,
                    recorded_at_ms=event.recorded_at_ms,
                    payload_sha256=event.payload_sha256,
                    previous_event_hash=event.previous_event_hash,
                )
                if (
                    event.event_seq != expected_seq
                    or event.event_kind not in {_STARTED, _OUTCOME}
                    or event.previous_event_hash != previous_hash
                    or event.event_hash != expected_hash
                    or event.recorded_at_ms < previous_time
                ):
                    raise L2RestoreHistoryStoreError(
                        "restore history authority event chain is invalid"
                    )
                events[event.event_seq] = event
                previous_hash = event.event_hash
                previous_time = event.recorded_at_ms

            attempts: list[AttemptRecord] = []
            attempts_by_id: dict[str, AttemptRecord] = {}
            for expected_seq, row in enumerate(
                self._attempt_rows_locked(), start=1
            ):
                attempt = _attempt_from_json(row["policy_json"])
                policy_sha256 = _sha256_text(row["policy_json"])
                payload_sha256 = _attempt_payload_sha256(
                    row["policy_json"], policy_sha256
                )
                event = events.get(row["authority_event_seq"])
                if (
                    row["attempt_seq"] != expected_seq
                    or event is None
                    or event.event_kind != _STARTED
                    or event.event_id != _started_event_id(attempt.attempt_id)
                    or event.recorded_at_ms != attempt.started_at_ms
                    or event.payload_sha256 != payload_sha256
                    or row["attempt_id"] != attempt.attempt_id
                    or row["started_at_ms"] != attempt.started_at_ms
                    or row["operation_kind"] != attempt.operation_kind
                    or row["mode"] != attempt.mode
                    or row["source_contract_sha256"]
                    != attempt.source_contract_sha256
                    or row["destination_ref_sha256"]
                    != attempt.destination_ref_sha256
                    or row["rto_ms"] != attempt.rto_ms
                    or row["max_rpo_ms"] != attempt.max_rpo_ms
                    or row["policy_sha256"] != policy_sha256
                ):
                    raise L2RestoreHistoryStoreError(
                        "restore history attempt record is invalid"
                    )
                record = AttemptRecord(
                    authority_event_seq=event.event_seq,
                    attempt=attempt,
                    policy_sha256=policy_sha256,
                    previous_event_hash=event.previous_event_hash,
                    event_hash=event.event_hash,
                )
                attempts.append(record)
                attempts_by_id[attempt.attempt_id] = record

            outcomes: list[OutcomeRecord] = []
            outcomes_by_id: dict[str, OutcomeRecord] = {}
            for expected_seq, row in enumerate(
                self._outcome_rows_locked(), start=1
            ):
                outcome = _outcome_from_json(row["outcome_json"])
                proof_json = _proof_json(outcome.proof)
                proof_sha256 = _sha256_text(proof_json)
                outcome_sha256 = _sha256_text(row["outcome_json"])
                payload_sha256 = _outcome_payload_sha256(
                    outcome_json=row["outcome_json"],
                    outcome_sha256=outcome_sha256,
                    proof_sha256=proof_sha256,
                    started_event_seq=row["started_event_seq"],
                    started_event_hash=row["started_event_hash"],
                )
                event = events.get(row["authority_event_seq"])
                start = attempts_by_id.get(outcome.attempt_id)
                if (
                    row["outcome_seq"] != expected_seq
                    or event is None
                    or start is None
                    or event.event_kind != _OUTCOME
                    or event.event_id != _outcome_event_id(outcome.attempt_id)
                    or event.recorded_at_ms != outcome.completed_at_ms
                    or event.payload_sha256 != payload_sha256
                    or row["attempt_id"] != outcome.attempt_id
                    or row["started_event_seq"] != start.authority_event_seq
                    or row["started_event_hash"] != start.event_hash
                    or row["completed_at_ms"] != outcome.completed_at_ms
                    or row["verdict"] != outcome.verdict
                    or row["stage"] != outcome.stage
                    or row["reason_code"] != outcome.reason_code
                    or row["proof_json"] != proof_json
                    or row["proof_sha256"] != proof_sha256
                    or row["outcome_sha256"] != outcome_sha256
                    or event.event_seq <= start.authority_event_seq
                    or outcome.completed_at_ms < start.attempt.started_at_ms
                ):
                    raise L2RestoreHistoryStoreError(
                        "restore history outcome record is invalid"
                    )
                self._validate_outcome_for_attempt(outcome, start.attempt)
                record = OutcomeRecord(
                    authority_event_seq=event.event_seq,
                    outcome=outcome,
                    outcome_sha256=outcome_sha256,
                    proof_sha256=proof_sha256,
                    started_event_seq=start.authority_event_seq,
                    started_event_hash=start.event_hash,
                    previous_event_hash=event.previous_event_hash,
                    event_hash=event.event_hash,
                )
                outcomes.append(record)
                outcomes_by_id[outcome.attempt_id] = record

            typed_events = {
                item.authority_event_seq for item in attempts
            } | {item.authority_event_seq for item in outcomes}
            if (
                len(typed_events) != len(attempts) + len(outcomes)
                or typed_events != set(events)
            ):
                raise L2RestoreHistoryStoreError(
                    "restore history authority event typing is invalid"
                )

            active: AttemptRecord | None = None
            attempts_by_event = {
                item.authority_event_seq: item for item in attempts
            }
            outcomes_by_event = {
                item.authority_event_seq: item for item in outcomes
            }
            for event_seq in events:
                if event_seq in attempts_by_event:
                    if active is not None:
                        raise L2RestoreHistoryStoreError(
                            "restore history contains overlapping attempts"
                        )
                    active = attempts_by_event[event_seq]
                else:
                    outcome = outcomes_by_event[event_seq]
                    if (
                        active is None
                        or outcome.outcome.attempt_id
                        != active.attempt.attempt_id
                    ):
                        raise L2RestoreHistoryStoreError(
                            "restore history lifecycle is invalid"
                        )
                    active = None

            for table, count in (
                ("l2_restore_history_events", len(events)),
                ("l2_restore_attempts", len(attempts)),
                ("l2_restore_outcomes", len(outcomes)),
            ):
                if not self._sequence_is_exact_locked(table, count):
                    raise L2RestoreHistoryStoreError(
                        "restore history authority was truncated"
                    )

            head = (
                None
                if not events
                else AuthorityHead(
                    event_seq=len(events),
                    event_hash=previous_hash,
                    recorded_at_ms=previous_time,
                )
            )
            open_attempts = () if active is None else (active,)
            return RestoreHistorySnapshot(
                attempts=tuple(attempts),
                outcomes=tuple(outcomes),
                open_attempts=open_attempts,
                authority_head=head,
                authority_time_floor_ms=(previous_time if events else None),
            )
        except L2RestoreHistoryStoreError:
            raise
        except (
            json.JSONDecodeError,
            sqlite3.DatabaseError,
            TypeError,
            ValueError,
        ) as exc:
            raise L2RestoreHistoryStoreError(
                f"restore history authority is invalid: {exc}"
            ) from exc

    def _require_not_future_locked(self, recorded_at_ms: int) -> None:
        authority_now_ms = self._clock_ms()
        _require_nonnegative_int("authority clock", authority_now_ms)
        if (
            recorded_at_ms
            > authority_now_ms + L2_RESTORE_HISTORY_MAX_FUTURE_SKEW_MS
        ):
            raise L2RestoreHistoryFutureClock(
                recorded_at_ms=recorded_at_ms,
                authority_now_ms=authority_now_ms,
            )

    @staticmethod
    def _require_expected_head(
        expected_head: AuthorityHead | None,
        actual_head: AuthorityHead | None,
    ) -> None:
        if expected_head != actual_head:
            raise L2RestoreHistoryHeadChanged(
                expected_head=expected_head,
                actual_head=actual_head,
            )

    @staticmethod
    def _validate_outcome_for_attempt(
        outcome: RestoreOutcome,
        attempt: RestoreAttempt,
    ) -> None:
        if outcome.attempt_id != attempt.attempt_id:
            raise ValueError("outcome attempt_id does not match the start")
        if outcome.completed_at_ms < attempt.started_at_ms:
            raise ValueError("outcome predates the start")
        if outcome.proof.source_contract_sha256 != attempt.source_contract_sha256:
            raise ValueError("proof source contract does not match the attempt")
        if outcome.verdict == "PASS":
            assert outcome.proof.restore_elapsed_ms is not None
            assert outcome.proof.source_age_ms is not None
            if outcome.proof.restore_elapsed_ms > attempt.rto_ms:
                raise ValueError("PASS proof exceeds the attempt RTO")
            if outcome.proof.source_age_ms > attempt.max_rpo_ms:
                raise ValueError("PASS proof exceeds the attempt RPO")

    def begin_attempt(
        self,
        attempt: RestoreAttempt,
        *,
        expected_head: AuthorityHead | None,
    ) -> tuple[AttemptRecord, bool]:
        policy_json = _attempt_json(attempt)
        policy_sha256 = _sha256_text(policy_json)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._require_not_future_locked(attempt.started_at_ms)
                snapshot = self._read_verified_locked()
                existing = next(
                    (
                        item
                        for item in snapshot.attempts
                        if item.attempt.attempt_id == attempt.attempt_id
                    ),
                    None,
                )
                if existing is not None:
                    if existing.attempt == attempt:
                        self._conn.rollback()
                        return existing, False
                    raise L2RestoreHistoryConflict(
                        f"restore attempt conflict: {attempt.attempt_id}"
                    )
                self._require_expected_head(expected_head, snapshot.authority_head)
                if snapshot.open_attempts:
                    raise L2RestoreHistoryActiveAttempt(
                        "another restore attempt is active"
                    )
                if (
                    snapshot.authority_time_floor_ms is not None
                    and attempt.started_at_ms < snapshot.authority_time_floor_ms
                ):
                    raise L2RestoreHistoryClockRollback(
                        "restore history authority clock rollback"
                    )
                event_seq = (
                    1
                    if snapshot.authority_head is None
                    else snapshot.authority_head.event_seq + 1
                )
                previous_hash = (
                    ""
                    if snapshot.authority_head is None
                    else snapshot.authority_head.event_hash
                )
                payload_sha256 = _attempt_payload_sha256(
                    policy_json, policy_sha256
                )
                event_hash = _event_hash(
                    event_seq=event_seq,
                    event_kind=_STARTED,
                    event_id=_started_event_id(attempt.attempt_id),
                    recorded_at_ms=attempt.started_at_ms,
                    payload_sha256=payload_sha256,
                    previous_event_hash=previous_hash,
                )
                self._conn.execute(
                    "INSERT INTO l2_restore_history_events "
                    "(event_seq,event_kind,event_id,recorded_at_ms,"
                    "payload_sha256,previous_event_hash,event_hash) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        event_seq,
                        _STARTED,
                        _started_event_id(attempt.attempt_id),
                        attempt.started_at_ms,
                        payload_sha256,
                        previous_hash,
                        event_hash,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO l2_restore_attempts "
                    "(authority_event_seq,attempt_id,started_at_ms,"
                    "operation_kind,mode,source_contract_sha256,"
                    "destination_ref_sha256,rto_ms,max_rpo_ms,policy_json,"
                    "policy_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_seq,
                        attempt.attempt_id,
                        attempt.started_at_ms,
                        attempt.operation_kind,
                        attempt.mode,
                        attempt.source_contract_sha256,
                        attempt.destination_ref_sha256,
                        attempt.rto_ms,
                        attempt.max_rpo_ms,
                        policy_json,
                        policy_sha256,
                    ),
                )
                retained = self._read_verified_locked().open_attempts[0]
                self._conn.commit()
                return retained, True
            except Exception as exc:
                if self._conn.in_transaction:
                    self._conn.rollback()
                if isinstance(exc, L2RestoreHistoryStoreError):
                    raise
                if isinstance(exc, sqlite3.DatabaseError):
                    raise L2RestoreHistoryStoreError(
                        f"restore attempt could not be recorded: {exc}"
                    ) from exc
                raise

    def complete_attempt(
        self,
        outcome: RestoreOutcome,
        *,
        expected_start: AttemptRecord,
        expected_head: AuthorityHead | None,
    ) -> tuple[OutcomeRecord, bool]:
        if not isinstance(expected_start, AttemptRecord):
            raise TypeError("expected_start must be an AttemptRecord")
        outcome_json = _outcome_json(outcome)
        proof_json = _proof_json(outcome.proof)
        outcome_sha256 = _sha256_text(outcome_json)
        proof_sha256 = _sha256_text(proof_json)
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._require_not_future_locked(outcome.completed_at_ms)
                snapshot = self._read_verified_locked()
                start = next(
                    (
                        item
                        for item in snapshot.attempts
                        if item.attempt.attempt_id == outcome.attempt_id
                    ),
                    None,
                )
                if start is None:
                    raise L2RestoreHistoryAttemptNotFound(
                        f"restore attempt not found: {outcome.attempt_id}"
                    )
                if start != expected_start:
                    raise L2RestoreHistoryStartMismatch(
                        "restore outcome start binding changed"
                    )
                existing = next(
                    (
                        item
                        for item in snapshot.outcomes
                        if item.outcome.attempt_id == outcome.attempt_id
                    ),
                    None,
                )
                if existing is not None:
                    if existing.outcome == outcome:
                        self._conn.rollback()
                        return existing, False
                    raise L2RestoreHistoryConflict(
                        f"restore outcome conflict: {outcome.attempt_id}"
                    )
                self._validate_outcome_for_attempt(outcome, start.attempt)
                self._require_expected_head(expected_head, snapshot.authority_head)
                if snapshot.open_attempts != (start,):
                    raise L2RestoreHistoryConflict(
                        "restore attempt is not the active attempt"
                    )
                if (
                    snapshot.authority_time_floor_ms is not None
                    and outcome.completed_at_ms
                    < snapshot.authority_time_floor_ms
                ):
                    raise L2RestoreHistoryClockRollback(
                        "restore history authority clock rollback"
                    )
                assert snapshot.authority_head is not None
                event_seq = snapshot.authority_head.event_seq + 1
                payload_sha256 = _outcome_payload_sha256(
                    outcome_json=outcome_json,
                    outcome_sha256=outcome_sha256,
                    proof_sha256=proof_sha256,
                    started_event_seq=start.authority_event_seq,
                    started_event_hash=start.event_hash,
                )
                event_hash = _event_hash(
                    event_seq=event_seq,
                    event_kind=_OUTCOME,
                    event_id=_outcome_event_id(outcome.attempt_id),
                    recorded_at_ms=outcome.completed_at_ms,
                    payload_sha256=payload_sha256,
                    previous_event_hash=snapshot.authority_head.event_hash,
                )
                self._conn.execute(
                    "INSERT INTO l2_restore_history_events "
                    "(event_seq,event_kind,event_id,recorded_at_ms,"
                    "payload_sha256,previous_event_hash,event_hash) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        event_seq,
                        _OUTCOME,
                        _outcome_event_id(outcome.attempt_id),
                        outcome.completed_at_ms,
                        payload_sha256,
                        snapshot.authority_head.event_hash,
                        event_hash,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO l2_restore_outcomes "
                    "(authority_event_seq,attempt_id,started_event_seq,"
                    "started_event_hash,completed_at_ms,verdict,stage,"
                    "reason_code,proof_json,proof_sha256,outcome_json,"
                    "outcome_sha256) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_seq,
                        outcome.attempt_id,
                        start.authority_event_seq,
                        start.event_hash,
                        outcome.completed_at_ms,
                        outcome.verdict,
                        outcome.stage,
                        outcome.reason_code,
                        proof_json,
                        proof_sha256,
                        outcome_json,
                        outcome_sha256,
                    ),
                )
                retained = self._read_verified_locked().outcomes[-1]
                self._conn.commit()
                return retained, True
            except Exception as exc:
                if self._conn.in_transaction:
                    self._conn.rollback()
                if isinstance(exc, L2RestoreHistoryStoreError):
                    raise
                if isinstance(exc, sqlite3.DatabaseError):
                    raise L2RestoreHistoryStoreError(
                        f"restore outcome could not be recorded: {exc}"
                    ) from exc
                raise

    def fail_overdue(
        self,
        attempt_id: str,
        *,
        now_ms: int,
        timeout_ms: int,
    ) -> tuple[OutcomeRecord, bool]:
        if not isinstance(attempt_id, str) or _IDENTIFIER.fullmatch(
            attempt_id
        ) is None:
            raise ValueError("attempt_id must be a canonical identifier")
        _require_nonnegative_int("now_ms", now_ms)
        _require_positive_int("timeout_ms", timeout_ms)
        snapshot = self.read_snapshot()
        existing = next(
            (
                item
                for item in snapshot.outcomes
                if item.outcome.attempt_id == attempt_id
            ),
            None,
        )
        if existing is not None:
            if (
                existing.outcome.verdict == "FAIL"
                and existing.outcome.stage == "TIMEOUT"
                and existing.outcome.reason_code == "ATTEMPT_TIMEOUT"
                and existing.outcome.completed_at_ms == now_ms
            ):
                return existing, False
            raise L2RestoreHistoryConflict(
                f"restore outcome conflict: {attempt_id}"
            )
        start = next(
            (
                item
                for item in snapshot.open_attempts
                if item.attempt.attempt_id == attempt_id
            ),
            None,
        )
        if start is None:
            raise L2RestoreHistoryAttemptNotFound(
                f"open restore attempt not found: {attempt_id}"
            )
        if now_ms - start.attempt.started_at_ms <= timeout_ms:
            raise L2RestoreHistoryNotOverdue(
                "restore attempt has not exceeded its timeout"
            )
        proof = RestoreProof(
            source_contract_sha256=start.attempt.source_contract_sha256,
            destination_class="UNVERIFIED",
            external_destination_verified=False,
            encrypted=False,
            manifest_verified=False,
            source_contract_verified=False,
            restore_verified=False,
            sqlite_integrity_verified=False,
            restore_elapsed_ms=None,
            source_age_ms=None,
            rto_met=False,
            rpo_met=False,
            batch_sha256="",
        )
        return self.complete_attempt(
            RestoreOutcome(
                attempt_id=attempt_id,
                completed_at_ms=now_ms,
                verdict="FAIL",
                stage="TIMEOUT",
                reason_code="ATTEMPT_TIMEOUT",
                proof=proof,
            ),
            expected_start=start,
            expected_head=snapshot.authority_head,
        )

    def read_snapshot(self) -> RestoreHistorySnapshot:
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                return self._read_verified_locked()
            finally:
                if self._conn.in_transaction:
                    self._conn.rollback()

    def authority_head(self) -> AuthorityHead | None:
        return self.read_snapshot().authority_head

    def chain_valid(self) -> bool:
        try:
            self.read_snapshot()
            return True
        except (
            L2RestoreHistoryStoreError,
            sqlite3.DatabaseError,
            TypeError,
            ValueError,
        ):
            return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _maximum_gap(values: list[int]) -> int:
    if len(values) < 2:
        return 0
    ordered = sorted(values)
    return max(right - left for left, right in zip(ordered, ordered[1:]))


def evaluate_cadence(
    snapshot: RestoreHistorySnapshot,
    policy: RestoreCadencePolicy,
    *,
    now_ms: int,
) -> RestoreHistoryHealth:
    """Evaluate current M4 cadence facts without mutating authority state."""

    if not isinstance(snapshot, RestoreHistorySnapshot):
        raise TypeError("snapshot must be a RestoreHistorySnapshot")
    if not isinstance(policy, RestoreCadencePolicy):
        raise TypeError("policy must be a RestoreCadencePolicy")
    _require_nonnegative_int("now_ms", now_ms)
    if not snapshot.attempts:
        return RestoreHistoryHealth(
            verdict="UNAVAILABLE",
            reason_codes=("NO_ATTEMPTS",),
            attempt_count=0,
            success_count=0,
            failure_count_in_clean_window=0,
            overdue_open_count=0,
            latest_terminal_verdict=None,
            latest_attempt_age_ms=None,
            latest_success_age_ms=None,
            maximum_attempt_gap_ms=None,
            maximum_success_gap_ms=None,
            history_span_ms=0,
            authority_head=snapshot.authority_head,
        )

    reasons: list[str] = []

    def add(reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    all_attempts = list(snapshot.attempts)
    all_outcomes = list(snapshot.outcomes)
    if any(item.attempt.started_at_ms > now_ms for item in all_attempts) or any(
        item.outcome.completed_at_ms > now_ms for item in all_outcomes
    ):
        add("HISTORY_FROM_FUTURE")

    matching_attempts = [
        item
        for item in all_attempts
        if item.attempt.source_contract_sha256 == policy.source_contract_sha256
        and item.attempt.operation_kind == policy.operation_kind
        and item.attempt.mode == policy.mode
    ]
    if len(matching_attempts) != len(all_attempts):
        add("SOURCE_CONTRACT_MISMATCH")
    matching_ids = {item.attempt.attempt_id for item in matching_attempts}

    latest_attempt = all_attempts[-1]
    latest_attempt_age_ms = max(0, now_ms - latest_attempt.attempt.started_at_ms)
    if latest_attempt_age_ms > policy.max_attempt_age_ms:
        add("LATEST_ATTEMPT_STALE")

    latest_terminal = all_outcomes[-1] if all_outcomes else None
    latest_terminal_verdict = (
        None if latest_terminal is None else latest_terminal.outcome.verdict
    )
    if latest_terminal_verdict != "PASS":
        add("LATEST_TERMINAL_NOT_PASS")

    clean_floor = now_ms - policy.clean_lookback_ms
    clean_failures = [
        item
        for item in all_outcomes
        if item.outcome.verdict == "FAIL"
        and item.outcome.completed_at_ms >= clean_floor
    ]
    if clean_failures:
        add("FAILURE_IN_CLEAN_LOOKBACK")

    overdue_open = [
        item
        for item in snapshot.open_attempts
        if now_ms >= item.attempt.started_at_ms
        and now_ms - item.attempt.started_at_ms > policy.attempt_timeout_ms
    ]
    if overdue_open:
        add("OVERDUE_OPEN_ATTEMPT")

    valid_successes: list[OutcomeRecord] = []
    for item in all_outcomes:
        if item.outcome.verdict != "PASS" or item.outcome.attempt_id not in matching_ids:
            continue
        proof = item.outcome.proof
        if (
            proof.source_contract_sha256 != policy.source_contract_sha256
            or proof.destination_class not in _EXTERNAL_DESTINATIONS
            or not proof.external_destination_verified
            or not proof.encrypted
            or not proof.manifest_verified
            or not proof.source_contract_verified
            or not proof.restore_verified
            or not proof.sqlite_integrity_verified
            or not proof.rto_met
            or not proof.rpo_met
            or not proof.batch_sha256
        ):
            add("EXTERNAL_RESTORE_PROOF_INVALID")
            continue
        valid_successes.append(item)

    latest_success_age_ms: int | None = None
    if valid_successes:
        latest_success_age_ms = max(
            0, now_ms - valid_successes[-1].outcome.completed_at_ms
        )
        if latest_success_age_ms > policy.max_success_age_ms:
            add("LATEST_SUCCESS_STALE")
    else:
        add("NO_VALID_SUCCESS")

    attempt_times = [item.attempt.started_at_ms for item in matching_attempts]
    success_times = [item.outcome.completed_at_ms for item in valid_successes]
    maximum_attempt_gap_ms = _maximum_gap(attempt_times)
    maximum_success_gap_ms = _maximum_gap(success_times)
    if maximum_attempt_gap_ms > policy.max_attempt_gap_ms:
        add("ATTEMPT_GAP_EXCEEDED")
    if maximum_success_gap_ms > policy.max_success_gap_ms:
        add("SUCCESS_GAP_EXCEEDED")

    history_span_ms = (
        0 if len(success_times) < 2 else max(success_times) - min(success_times)
    )
    if history_span_ms < policy.minimum_history_span_ms:
        add("HISTORY_SPAN_INSUFFICIENT")
    if len(valid_successes) < policy.minimum_successes:
        add("SUCCESS_COUNT_INSUFFICIENT")

    return RestoreHistoryHealth(
        verdict="PASS" if not reasons else "BREACH",
        reason_codes=tuple(reasons),
        attempt_count=len(all_attempts),
        success_count=len(valid_successes),
        failure_count_in_clean_window=len(clean_failures),
        overdue_open_count=len(overdue_open),
        latest_terminal_verdict=latest_terminal_verdict,
        latest_attempt_age_ms=latest_attempt_age_ms,
        latest_success_age_ms=latest_success_age_ms,
        maximum_attempt_gap_ms=maximum_attempt_gap_ms,
        maximum_success_gap_ms=maximum_success_gap_ms,
        history_span_ms=history_span_ms,
        authority_head=snapshot.authority_head,
    )


__all__ = [
    "L2_RESTORE_HISTORY_MAX_FUTURE_SKEW_MS",
    "L2_RESTORE_HISTORY_STORE_KIND",
    "L2_RESTORE_HISTORY_STORE_SCHEMA_VERSION",
    "AuthorityHead",
    "AttemptRecord",
    "OutcomeRecord",
    "RestoreAttempt",
    "RestoreProof",
    "RestoreOutcome",
    "RestoreCadencePolicy",
    "RestoreHistorySnapshot",
    "RestoreHistoryHealth",
    "L2RestoreHistoryStore",
    "L2RestoreHistoryStoreError",
    "L2RestoreHistoryConflict",
    "L2RestoreHistoryClockRollback",
    "L2RestoreHistoryFutureClock",
    "L2RestoreHistoryHeadChanged",
    "L2RestoreHistoryActiveAttempt",
    "L2RestoreHistoryStartMismatch",
    "L2RestoreHistoryAttemptNotFound",
    "L2RestoreHistoryNotOverdue",
    "evaluate_cadence",
]
