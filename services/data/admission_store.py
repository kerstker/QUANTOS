"""Append-only data-quality admission authority with a transactional outbox."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import hashlib
import json
import os
import re
import sqlite3
import threading
from typing import Literal

from quantos.contracts.event_schemas import CANONICAL_EVENT_SCHEMAS
from quantos.contracts.events import (
    EVENT_DATA_QUALITY_ADMISSION_DECIDED,
    Event,
    EventOutboxMessage,
)

from .events import data_quality_admission_decided_event


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_READINESS = "L2_DRY_RUN_ONLY"
_PURPOSE = "FORWARD_EVIDENCE_DATA_ADMISSION"


class DataQualityAdmissionStoreError(RuntimeError):
    """Admission history is unavailable, conflicting, or unverifiable."""


@dataclass(frozen=True)
class DataQualityAdmissionDecision:
    admission_id: str
    generated_at_ms: int
    recorded_at_ms: int
    evidence_schema_version: int
    readiness: Literal["L2_DRY_RUN_ONLY"]
    purpose: Literal["FORWARD_EVIDENCE_DATA_ADMISSION"]
    verdict: Literal["PASS", "BLOCK"]
    quality_verdict: Literal["PASS", "BLOCK"]
    lineage_verdict: Literal["PASS", "BLOCK"]
    price_basis: str
    instrument_scope: tuple[str, ...]
    block_reasons: tuple[str, ...]
    evidence_sha256: str
    decision_hash: str
    causation_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.admission_id, str) or not self.admission_id:
            raise ValueError("admission_id must be non-empty")
        for name in ("generated_at_ms", "recorded_at_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.recorded_at_ms < self.generated_at_ms:
            raise ValueError("recorded_at_ms cannot precede generated_at_ms")
        if (
            isinstance(self.evidence_schema_version, bool)
            or not isinstance(self.evidence_schema_version, int)
            or self.evidence_schema_version < 1
        ):
            raise ValueError("evidence_schema_version must be positive")
        if self.readiness != _READINESS:
            raise ValueError("readiness must remain L2_DRY_RUN_ONLY")
        if self.purpose != _PURPOSE:
            raise ValueError("purpose is not a data-quality admission decision")
        for name in ("verdict", "quality_verdict", "lineage_verdict"):
            if getattr(self, name) not in {"PASS", "BLOCK"}:
                raise ValueError(f"{name} must be PASS or BLOCK")
        if not isinstance(self.price_basis, str) or not self.price_basis:
            raise ValueError("price_basis must be non-empty")
        if (
            not isinstance(self.instrument_scope, tuple)
            or not self.instrument_scope
            or any(
                not isinstance(instrument, str) or not instrument
                for instrument in self.instrument_scope
            )
        ):
            raise ValueError("instrument_scope must contain non-empty strings")
        if tuple(sorted(set(self.instrument_scope))) != self.instrument_scope:
            raise ValueError("instrument_scope must be sorted and unique")
        if (
            not isinstance(self.block_reasons, tuple)
            or any(
                not isinstance(reason, str) or not reason
                for reason in self.block_reasons
            )
        ):
            raise ValueError("block_reasons must contain non-empty strings")
        for name in ("evidence_sha256", "decision_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
                raise ValueError(f"{name} must be a SHA-256 digest")
        if not isinstance(self.causation_id, str):
            raise TypeError("causation_id must be a string")

        if self.verdict == "PASS":
            if (
                self.quality_verdict != "PASS"
                or self.lineage_verdict != "PASS"
                or self.price_basis != "PERP_MARK_PRICE"
                or self.block_reasons
            ):
                raise ValueError("PASS admission is inconsistent with its evidence")
        elif not self.block_reasons:
            raise ValueError("BLOCK admission must retain block reasons")
        if (
            self.quality_verdict == "BLOCK"
            or self.lineage_verdict == "BLOCK"
        ) and self.verdict != "BLOCK":
            raise ValueError("a failed evidence gate must BLOCK admission")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_quality_admissions (
  admission_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  admission_id TEXT UNIQUE NOT NULL,
  generated_at_ms INTEGER NOT NULL CHECK (generated_at_ms >= 0),
  recorded_at_ms INTEGER NOT NULL CHECK (recorded_at_ms >= generated_at_ms),
  evidence_schema_version INTEGER NOT NULL CHECK (evidence_schema_version >= 1),
  readiness TEXT NOT NULL CHECK (readiness = 'L2_DRY_RUN_ONLY'),
  purpose TEXT NOT NULL CHECK (purpose = 'FORWARD_EVIDENCE_DATA_ADMISSION'),
  verdict TEXT NOT NULL CHECK (verdict IN ('PASS','BLOCK')),
  quality_verdict TEXT NOT NULL CHECK (quality_verdict IN ('PASS','BLOCK')),
  lineage_verdict TEXT NOT NULL CHECK (lineage_verdict IN ('PASS','BLOCK')),
  price_basis TEXT NOT NULL,
  instrument_scope_json TEXT NOT NULL,
  block_reasons_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  evidence_sha256 TEXT NOT NULL,
  decision_hash TEXT NOT NULL,
  causation_id TEXT NOT NULL,
  event_json TEXT NOT NULL,
  event_repro_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS data_quality_admission_dispatch (
  dispatch_seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT UNIQUE NOT NULL,
  dispatched_at INTEGER NOT NULL CHECK (dispatched_at >= 0),
  event_repro_hash TEXT NOT NULL,
  prev_record_hash TEXT NOT NULL,
  record_hash TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS data_quality_admissions_no_update
BEFORE UPDATE ON data_quality_admissions
BEGIN SELECT RAISE(ABORT, 'data_quality_admissions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS data_quality_admissions_no_delete
BEFORE DELETE ON data_quality_admissions
BEGIN SELECT RAISE(ABORT, 'data_quality_admissions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS data_quality_admission_dispatch_no_update
BEFORE UPDATE ON data_quality_admission_dispatch
BEGIN SELECT RAISE(ABORT, 'data_quality_admission_dispatch is append-only'); END;
CREATE TRIGGER IF NOT EXISTS data_quality_admission_dispatch_no_delete
BEFORE DELETE ON data_quality_admission_dispatch
BEGIN SELECT RAISE(ABORT, 'data_quality_admission_dispatch is append-only'); END;
"""


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


def _admission_id(evidence_sha256: str) -> str:
    return f"data-admission-{evidence_sha256[:32]}"


def _decision_object(decision: DataQualityAdmissionDecision) -> dict:
    document = asdict(decision)
    document["instrument_scope"] = list(decision.instrument_scope)
    document["block_reasons"] = list(decision.block_reasons)
    return document


def _decision_hash(document: Mapping[str, object]) -> str:
    return _sha256(_canonical(dict(document)))


def _record_hash(
    decision: DataQualityAdmissionDecision,
    message: EventOutboxMessage,
    prev_record_hash: str,
) -> str:
    return _sha256(
        _canonical(
            {
                "decision": _decision_object(decision),
                "event": message.event.model_dump(mode="json"),
                "correlation_id": message.correlation_id,
                "causation_id": message.causation_id,
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
                "event_id": event_id,
                "dispatched_at": dispatched_at,
                "event_repro_hash": event_repro_hash,
                "prev_record_hash": prev_record_hash,
            }
        )
    )


def _validated_evidence(evidence: Mapping) -> tuple[str, dict]:
    if not isinstance(evidence, Mapping):
        raise TypeError("evidence must be a mapping")
    try:
        evidence_json = _canonical(dict(evidence))
        document = json.loads(evidence_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("evidence must be finite JSON data") from exc
    if not isinstance(document, dict):
        raise ValueError("evidence must be a JSON object")

    schema_version = document.get("schema_version")
    generated_at_ms = document.get("generated_at_ms")
    scope = document.get("instrument_scope")
    block_reasons = document.get("block_reasons")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 1
    ):
        raise ValueError("evidence schema_version must be positive")
    if (
        isinstance(generated_at_ms, bool)
        or not isinstance(generated_at_ms, int)
        or generated_at_ms < 0
    ):
        raise ValueError("evidence generated_at_ms must be non-negative")
    if document.get("readiness") != _READINESS:
        raise ValueError("evidence readiness must remain L2_DRY_RUN_ONLY")
    if document.get("purpose") != _PURPOSE:
        raise ValueError("evidence purpose is not supported")
    if (
        not isinstance(scope, list)
        or not scope
        or any(not isinstance(item, str) or not item for item in scope)
    ):
        raise ValueError("evidence instrument_scope must be non-empty")
    if sorted(set(scope)) != scope:
        raise ValueError("evidence instrument_scope must be sorted and unique")
    if not isinstance(block_reasons, list) or any(
        not isinstance(item, str) or not item for item in block_reasons
    ):
        raise ValueError("evidence block_reasons must be a list of strings")
    for name in ("verdict", "quality_verdict", "lineage_verdict"):
        if document.get(name) not in {"PASS", "BLOCK"}:
            raise ValueError(f"evidence {name} must be PASS or BLOCK")
    if not isinstance(document.get("price_basis"), str) or not document["price_basis"]:
        raise ValueError("evidence price_basis must be non-empty")
    return evidence_json, document


def _decision_from_evidence(
    document: Mapping,
    *,
    evidence_sha256: str,
    recorded_at_ms: int,
    causation_id: str,
) -> DataQualityAdmissionDecision:
    base = {
        "admission_id": _admission_id(evidence_sha256),
        "generated_at_ms": document["generated_at_ms"],
        "recorded_at_ms": recorded_at_ms,
        "evidence_schema_version": document["schema_version"],
        "readiness": document["readiness"],
        "purpose": document["purpose"],
        "verdict": document["verdict"],
        "quality_verdict": document["quality_verdict"],
        "lineage_verdict": document["lineage_verdict"],
        "price_basis": document["price_basis"],
        "instrument_scope": tuple(document["instrument_scope"]),
        "block_reasons": tuple(document["block_reasons"]),
        "evidence_sha256": evidence_sha256,
        "causation_id": causation_id,
    }
    digest_document = dict(base)
    digest_document["instrument_scope"] = list(base["instrument_scope"])
    digest_document["block_reasons"] = list(base["block_reasons"])
    return DataQualityAdmissionDecision(
        **base,
        decision_hash=_decision_hash(digest_document),
    )


def _message_for(decision: DataQualityAdmissionDecision) -> EventOutboxMessage:
    event = data_quality_admission_decided_event(
        admission_id=decision.admission_id,
        verdict=decision.verdict,
        quality_verdict=decision.quality_verdict,
        lineage_verdict=decision.lineage_verdict,
        evidence_schema_version=decision.evidence_schema_version,
        evidence_sha256=decision.evidence_sha256,
        decision_hash=decision.decision_hash,
        instrument_count=len(decision.instrument_scope),
        block_reason_count=len(decision.block_reasons),
        price_basis=decision.price_basis,
        readiness=decision.readiness,
        purpose=decision.purpose,
        at_ms=decision.recorded_at_ms,
    )
    return EventOutboxMessage(
        event=event,
        correlation_id=decision.admission_id,
        causation_id=decision.causation_id,
    )


class DataQualityAdmissionStore:
    """Single-node authority for forward evidence admission decisions."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if not create and (path == ":memory:" or not os.path.isfile(path)):
            raise DataQualityAdmissionStoreError(
                f"data-quality admission store unavailable: {path}"
            )
        if create and path != ":memory:":
            parent = os.path.dirname(os.path.abspath(path))
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        try:
            with self._lock:
                self._conn.execute("PRAGMA busy_timeout=5000")
                if create:
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    self._conn.execute("PRAGMA synchronous=FULL")
                    self._conn.executescript(_SCHEMA)
                    self._conn.commit()
                self._verify_locked()
        except BaseException:
            self._conn.close()
            raise

    def _decision_rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(
                "SELECT admission_seq,admission_id,generated_at_ms,recorded_at_ms,"
                " evidence_schema_version,readiness,purpose,verdict,quality_verdict,"
                " lineage_verdict,price_basis,instrument_scope_json,block_reasons_json,"
                " evidence_json,evidence_sha256,decision_hash,causation_id,event_json,"
                " event_repro_hash,prev_record_hash,record_hash"
                " FROM data_quality_admissions ORDER BY admission_seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise DataQualityAdmissionStoreError(
                f"data-quality admission schema is unreadable: {exc}"
            ) from exc

    def _dispatch_rows_locked(self) -> list[sqlite3.Row]:
        try:
            return self._conn.execute(
                "SELECT dispatch_seq,event_id,dispatched_at,event_repro_hash,"
                " prev_record_hash,record_hash"
                " FROM data_quality_admission_dispatch ORDER BY dispatch_seq"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise DataQualityAdmissionStoreError(
                f"data-quality admission dispatch is unreadable: {exc}"
            ) from exc

    @staticmethod
    def _evidence_from_row(row: sqlite3.Row) -> dict:
        try:
            evidence_json, document = _validated_evidence(
                json.loads(row["evidence_json"])
            )
        except Exception as exc:
            raise DataQualityAdmissionStoreError(
                f"admission {row['admission_seq']} evidence cannot be reconstructed"
            ) from exc
        if evidence_json != row["evidence_json"]:
            raise DataQualityAdmissionStoreError(
                f"admission {row['admission_seq']} evidence is not canonical"
            )
        if _sha256(evidence_json) != row["evidence_sha256"]:
            raise DataQualityAdmissionStoreError(
                f"admission {row['admission_seq']} evidence hash mismatch"
            )
        return document

    @classmethod
    def _decision_from_row(cls, row: sqlite3.Row) -> DataQualityAdmissionDecision:
        document = cls._evidence_from_row(row)
        try:
            decision = DataQualityAdmissionDecision(
                admission_id=row["admission_id"],
                generated_at_ms=row["generated_at_ms"],
                recorded_at_ms=row["recorded_at_ms"],
                evidence_schema_version=row["evidence_schema_version"],
                readiness=row["readiness"],
                purpose=row["purpose"],
                verdict=row["verdict"],
                quality_verdict=row["quality_verdict"],
                lineage_verdict=row["lineage_verdict"],
                price_basis=row["price_basis"],
                instrument_scope=tuple(json.loads(row["instrument_scope_json"])),
                block_reasons=tuple(json.loads(row["block_reasons_json"])),
                evidence_sha256=row["evidence_sha256"],
                decision_hash=row["decision_hash"],
                causation_id=row["causation_id"],
            )
        except Exception as exc:
            raise DataQualityAdmissionStoreError(
                f"admission {row['admission_seq']} cannot be reconstructed"
            ) from exc
        expected = _decision_from_evidence(
            document,
            evidence_sha256=decision.evidence_sha256,
            recorded_at_ms=decision.recorded_at_ms,
            causation_id=decision.causation_id,
        )
        if decision != expected:
            raise DataQualityAdmissionStoreError(
                f"admission {row['admission_seq']} does not match evidence"
            )
        if row["instrument_scope_json"] != _canonical(list(decision.instrument_scope)):
            raise DataQualityAdmissionStoreError(
                f"admission {row['admission_seq']} scope is not canonical"
            )
        if row["block_reasons_json"] != _canonical(list(decision.block_reasons)):
            raise DataQualityAdmissionStoreError(
                f"admission {row['admission_seq']} reasons are not canonical"
            )
        return decision

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> EventOutboxMessage:
        try:
            event = Event.model_validate_json(row["event_json"])
        except Exception as exc:
            raise DataQualityAdmissionStoreError(
                f"admission {row['admission_seq']} event cannot be reconstructed"
            ) from exc
        if event.reproducibility_hash != row["event_repro_hash"]:
            raise DataQualityAdmissionStoreError(
                f"admission {row['admission_seq']} event hash mismatch"
            )
        return EventOutboxMessage(
            event=event,
            correlation_id=row["admission_id"],
            causation_id=row["causation_id"],
        )

    @staticmethod
    def _validate_semantics(
        decision: DataQualityAdmissionDecision,
        message: EventOutboxMessage,
    ) -> None:
        try:
            CANONICAL_EVENT_SCHEMAS.validate(message.event)
        except Exception as exc:
            raise DataQualityAdmissionStoreError(
                "data-quality admission event schema is invalid"
            ) from exc
        if (
            message.event.event_type != EVENT_DATA_QUALITY_ADMISSION_DECIDED
            or message != _message_for(decision)
            or message.event.event_id != decision.admission_id
        ):
            raise DataQualityAdmissionStoreError(
                "data-quality decision does not match its canonical event"
            )

    def _verify_locked(
        self,
    ) -> tuple[
        str,
        tuple[DataQualityAdmissionDecision, ...],
        dict[str, EventOutboxMessage],
        str,
    ]:
        previous = ""
        decisions: list[DataQualityAdmissionDecision] = []
        messages: dict[str, EventOutboxMessage] = {}
        latest_generated_at = 0
        for row in self._decision_rows_locked():
            decision = self._decision_from_row(row)
            message = self._message_from_row(row)
            self._validate_semantics(decision, message)
            if decision.generated_at_ms < latest_generated_at:
                raise DataQualityAdmissionStoreError(
                    "data-quality evidence generation time moved backwards"
                )
            if row["prev_record_hash"] != previous:
                raise DataQualityAdmissionStoreError(
                    f"admission {row['admission_seq']} previous hash mismatch"
                )
            digest = _record_hash(decision, message, previous)
            if row["record_hash"] != digest:
                raise DataQualityAdmissionStoreError(
                    f"admission {row['admission_seq']} record hash mismatch"
                )
            if message.outbox_id in messages:
                raise DataQualityAdmissionStoreError(
                    f"duplicate data-quality event id: {message.outbox_id}"
                )
            decisions.append(decision)
            messages[message.outbox_id] = message
            previous = digest
            latest_generated_at = decision.generated_at_ms

        dispatch_previous = ""
        for row in self._dispatch_rows_locked():
            message = messages.get(row["event_id"])
            if (
                message is None
                or message.event.reproducibility_hash != row["event_repro_hash"]
            ):
                raise DataQualityAdmissionStoreError(
                    f"admission dispatch {row['dispatch_seq']} reference mismatch"
                )
            if row["prev_record_hash"] != dispatch_previous:
                raise DataQualityAdmissionStoreError(
                    f"admission dispatch {row['dispatch_seq']} previous hash mismatch"
                )
            digest = _dispatch_hash(
                event_id=row["event_id"],
                dispatched_at=row["dispatched_at"],
                event_repro_hash=row["event_repro_hash"],
                prev_record_hash=dispatch_previous,
            )
            if row["record_hash"] != digest:
                raise DataQualityAdmissionStoreError(
                    f"admission dispatch {row['dispatch_seq']} record hash mismatch"
                )
            dispatch_previous = digest
        return previous, tuple(decisions), messages, dispatch_previous

    def record_evidence(
        self,
        evidence: Mapping,
        *,
        recorded_at_ms: int,
        causation_id: str = "",
    ) -> tuple[DataQualityAdmissionDecision, bool]:
        if (
            isinstance(recorded_at_ms, bool)
            or not isinstance(recorded_at_ms, int)
            or recorded_at_ms < 0
        ):
            raise ValueError("recorded_at_ms must be a non-negative integer")
        if not isinstance(causation_id, str):
            raise TypeError("causation_id must be a string")
        evidence_json, document = _validated_evidence(evidence)
        evidence_sha256 = _sha256(evidence_json)
        admission_id = _admission_id(evidence_sha256)

        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                head, decisions, _, _ = self._verify_locked()
                existing = self._conn.execute(
                    "SELECT admission_seq,admission_id,generated_at_ms,recorded_at_ms,"
                    " evidence_schema_version,readiness,purpose,verdict,quality_verdict,"
                    " lineage_verdict,price_basis,instrument_scope_json,block_reasons_json,"
                    " evidence_json,evidence_sha256,decision_hash,causation_id,event_json,"
                    " event_repro_hash,prev_record_hash,record_hash"
                    " FROM data_quality_admissions WHERE admission_id=?",
                    (admission_id,),
                ).fetchone()
                if existing is not None:
                    retained = self._decision_from_row(existing)
                    if (
                        existing["evidence_json"] == evidence_json
                        and retained.causation_id == causation_id
                    ):
                        self._conn.rollback()
                        return retained, False
                    raise DataQualityAdmissionStoreError(
                        f"data-quality admission conflict: {admission_id}"
                    )

                decision = _decision_from_evidence(
                    document,
                    evidence_sha256=evidence_sha256,
                    recorded_at_ms=recorded_at_ms,
                    causation_id=causation_id,
                )
                if (
                    decisions
                    and decision.generated_at_ms < decisions[-1].generated_at_ms
                ):
                    raise DataQualityAdmissionStoreError(
                        "data-quality evidence generation time moved backwards"
                    )
                message = _message_for(decision)
                self._validate_semantics(decision, message)
                digest = _record_hash(decision, message, head)
                self._conn.execute(
                    "INSERT INTO data_quality_admissions"
                    " (admission_id,generated_at_ms,recorded_at_ms,"
                    " evidence_schema_version,readiness,purpose,verdict,"
                    " quality_verdict,lineage_verdict,price_basis,"
                    " instrument_scope_json,block_reasons_json,evidence_json,"
                    " evidence_sha256,decision_hash,causation_id,event_json,"
                    " event_repro_hash,prev_record_hash,record_hash)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        decision.admission_id,
                        decision.generated_at_ms,
                        decision.recorded_at_ms,
                        decision.evidence_schema_version,
                        decision.readiness,
                        decision.purpose,
                        decision.verdict,
                        decision.quality_verdict,
                        decision.lineage_verdict,
                        decision.price_basis,
                        _canonical(list(decision.instrument_scope)),
                        _canonical(list(decision.block_reasons)),
                        evidence_json,
                        decision.evidence_sha256,
                        decision.decision_hash,
                        decision.causation_id,
                        _canonical(message.event.model_dump(mode="json")),
                        message.event.reproducibility_hash,
                        head,
                        digest,
                    ),
                )
                self._conn.commit()
                return decision, True
            except Exception:
                if self._conn.in_transaction:
                    self._conn.rollback()
                raise

    def decisions(self) -> tuple[DataQualityAdmissionDecision, ...]:
        with self._lock:
            _, decisions, _, _ = self._verify_locked()
            return decisions

    def latest_decision(self) -> DataQualityAdmissionDecision | None:
        decisions = self.decisions()
        return decisions[-1] if decisions else None

    def evidence(self, admission_id: str) -> dict:
        if not isinstance(admission_id, str) or not admission_id:
            raise ValueError("admission_id must be non-empty")
        with self._lock:
            self._verify_locked()
            row = self._conn.execute(
                "SELECT admission_seq,admission_id,generated_at_ms,recorded_at_ms,"
                " evidence_schema_version,readiness,purpose,verdict,quality_verdict,"
                " lineage_verdict,price_basis,instrument_scope_json,block_reasons_json,"
                " evidence_json,evidence_sha256,decision_hash,causation_id,event_json,"
                " event_repro_hash,prev_record_hash,record_hash"
                " FROM data_quality_admissions WHERE admission_id=?",
                (admission_id,),
            ).fetchone()
            if row is None:
                raise KeyError(admission_id)
            return self._evidence_from_row(row)

    def pending_event_outbox(self, *, limit: int = 1_000) -> tuple[dict, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise TypeError("limit must be an integer")
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._lock:
            _, _, messages, _ = self._verify_locked()
            dispatched = {row["event_id"] for row in self._dispatch_rows_locked()}
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
        if not isinstance(event_repro_hash, str):
            raise TypeError("event_repro_hash must be a string")
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                _, _, messages, dispatch_head = self._verify_locked()
                message = messages.get(event_id)
                if (
                    message is None
                    or message.event.reproducibility_hash != event_repro_hash
                ):
                    raise DataQualityAdmissionStoreError(
                        "data-quality dispatch reference mismatch"
                    )
                existing = self._conn.execute(
                    "SELECT dispatch_seq,event_id,dispatched_at,event_repro_hash,"
                    " prev_record_hash,record_hash"
                    " FROM data_quality_admission_dispatch WHERE event_id=?",
                    (event_id,),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["dispatched_at"] == dispatched_at
                        and existing["event_repro_hash"] == event_repro_hash
                    ):
                        self._conn.rollback()
                        return False
                    raise DataQualityAdmissionStoreError(
                        "data-quality dispatch acknowledgement conflict"
                    )
                digest = _dispatch_hash(
                    event_id=event_id,
                    dispatched_at=dispatched_at,
                    event_repro_hash=event_repro_hash,
                    prev_record_hash=dispatch_head,
                )
                self._conn.execute(
                    "INSERT INTO data_quality_admission_dispatch"
                    " (event_id,dispatched_at,event_repro_hash,prev_record_hash,"
                    " record_hash) VALUES (?,?,?,?,?)",
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
            except (DataQualityAdmissionStoreError, sqlite3.DatabaseError, ValueError):
                return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "DataQualityAdmissionDecision",
    "DataQualityAdmissionStore",
    "DataQualityAdmissionStoreError",
]
