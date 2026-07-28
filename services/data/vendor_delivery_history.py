"""Append-only authority for licensed vendor delivery decisions.

The authority retains redacted identifiers, input digests, SLA metrics and the
terminal admission decision in one SQLite hash chain.  It has no network,
credential, contract-verification or market-data acquisition capability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
import re
import sqlite3
from typing import Mapping


VENDOR_DELIVERY_HISTORY_KIND = "QUANTOS_VENDOR_DELIVERY_HISTORY_AUTHORITY"
VENDOR_DELIVERY_HISTORY_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_REASON = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class VendorDeliveryHistoryError(RuntimeError):
    """The delivery-history authority is unavailable or unverifiable."""


class VendorDeliveryHistoryConflict(VendorDeliveryHistoryError):
    """An immutable delivery ID already binds different content."""


class VendorDeliveryHistoryClockRollback(VendorDeliveryHistoryError):
    """A new record predates the retained authority time floor."""


@dataclass(frozen=True)
class VendorDeliveryHead:
    event_seq: int
    event_hash: str
    recorded_at_ms: int


@dataclass(frozen=True)
class VendorDeliveryRecord:
    event_id: str
    delivery_id: str
    recorded_at_ms: int
    vendor_id: str
    dataset_id: str
    instrument_id: str
    grant_id: str
    contract_id: str
    usage: str
    content_sha256: str
    grant_sha256: str
    contract_sha256: str
    policy_sha256: str
    verdict: str
    reasons: tuple[str, ...]
    coverage_ratio: str
    missing_ratio: str
    duplicate_ratio: str
    delivery_lag_ms: int
    history_span_ms: int
    archive_artifact_sha256: str = ""

    def __post_init__(self) -> None:
        for name in (
            "event_id",
            "delivery_id",
            "vendor_id",
            "dataset_id",
            "instrument_id",
            "grant_id",
            "contract_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
                raise ValueError(f"{name} is invalid")
        if type(self.recorded_at_ms) is not int or self.recorded_at_ms < 0:
            raise ValueError("recorded_at_ms must be a non-negative integer")
        if self.usage not in {"RESEARCH", "PAPER", "LIVE"}:
            raise ValueError("usage must be RESEARCH, PAPER or LIVE")
        if self.verdict not in {"ARCHIVE_PENDING", "ADMITTED", "DENIED"}:
            raise ValueError(
                "verdict must be ARCHIVE_PENDING, ADMITTED or DENIED"
            )
        for name in (
            "content_sha256",
            "grant_sha256",
            "contract_sha256",
            "policy_sha256",
        ):
            if _SHA256.fullmatch(getattr(self, name)) is None:
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if self.archive_artifact_sha256 and (
            _SHA256.fullmatch(self.archive_artifact_sha256) is None
        ):
            raise ValueError(
                "archive_artifact_sha256 must be empty or a lowercase SHA-256 digest"
            )
        if not isinstance(self.reasons, tuple) or any(
            not isinstance(reason, str) or _REASON.fullmatch(reason) is None
            for reason in self.reasons
        ):
            raise ValueError("reasons must contain canonical reason codes")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("reasons must not contain duplicates")
        if self.verdict == "ARCHIVE_PENDING" and (
            self.reasons or self.archive_artifact_sha256
        ):
            raise ValueError(
                "pending delivery cannot claim reasons or an archive digest"
            )
        if self.verdict == "ADMITTED" and (
            self.reasons or self.archive_artifact_sha256 != self.content_sha256
        ):
            raise ValueError(
                "admitted delivery requires no reasons and an exact archive digest"
            )
        if self.verdict == "DENIED" and (
            not self.reasons or self.archive_artifact_sha256
        ):
            raise ValueError(
                "denied delivery requires reasons and cannot claim an archive digest"
            )
        for name in ("delivery_lag_ms", "history_span_ms"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        for name in ("coverage_ratio", "missing_ratio", "duplicate_ratio"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a decimal string")

    def as_payload(self) -> dict:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


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


def vendor_delivery_event_id(delivery_id: str, verdict: str) -> str:
    return _sha256(f"{delivery_id}|{verdict}")


def _payload_sha256(record: VendorDeliveryRecord) -> str:
    return _sha256(_canonical(record.as_payload()))


def _event_hash(
    *,
    event_seq: int,
    event_id: str,
    delivery_id: str,
    recorded_at_ms: int,
    payload_sha256: str,
    previous_event_hash: str,
) -> str:
    return _sha256(
        _canonical(
            {
                "delivery_id": delivery_id,
                "event_id": event_id,
                "event_seq": event_seq,
                "payload_sha256": payload_sha256,
                "previous_event_hash": previous_event_hash,
                "recorded_at_ms": recorded_at_ms,
            }
        )
    )


_SCHEMA = f"""
CREATE TABLE vendor_delivery_history_meta (
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  store_kind TEXT NOT NULL,
  schema_version INTEGER NOT NULL
) STRICT;
CREATE TABLE vendor_delivery_history_events (
  event_seq INTEGER PRIMARY KEY,
  event_id TEXT UNIQUE NOT NULL,
  delivery_id TEXT NOT NULL,
  recorded_at_ms INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  previous_event_hash TEXT NOT NULL,
  event_hash TEXT UNIQUE NOT NULL
) STRICT;
INSERT INTO vendor_delivery_history_meta
  (singleton,store_kind,schema_version)
VALUES (1,'{VENDOR_DELIVERY_HISTORY_KIND}',{VENDOR_DELIVERY_HISTORY_SCHEMA_VERSION});
CREATE TRIGGER vendor_delivery_history_meta_no_update
BEFORE UPDATE ON vendor_delivery_history_meta
BEGIN SELECT RAISE(ABORT, 'vendor delivery history meta is immutable'); END;
CREATE TRIGGER vendor_delivery_history_meta_no_delete
BEFORE DELETE ON vendor_delivery_history_meta
BEGIN SELECT RAISE(ABORT, 'vendor delivery history meta is immutable'); END;
CREATE TRIGGER vendor_delivery_history_events_no_update
BEFORE UPDATE ON vendor_delivery_history_events
BEGIN SELECT RAISE(ABORT, 'vendor delivery history is append-only'); END;
CREATE TRIGGER vendor_delivery_history_events_no_delete
BEFORE DELETE ON vendor_delivery_history_events
BEGIN SELECT RAISE(ABORT, 'vendor delivery history is append-only'); END;
"""


class VendorDeliveryHistoryStore:
    """Strict, append-only SQLite authority with CAS and exact replay."""

    def __init__(self, path: str, *, create: bool = False) -> None:
        if not isinstance(path, str) or not path:
            raise ValueError("path must be non-empty")
        if os.path.islink(path):
            raise VendorDeliveryHistoryError(
                "vendor delivery history authority path must not be a symlink"
            )
        if not create and not os.path.isfile(path):
            raise VendorDeliveryHistoryError(
                "vendor delivery history authority is unavailable"
            )
        try:
            self._conn = sqlite3.connect(path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys=ON")
            if create:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA synchronous=FULL")
                tables = self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if not tables:
                    self._conn.executescript(_SCHEMA)
                    self._conn.commit()
            self._verify_store()
        except (sqlite3.DatabaseError, VendorDeliveryHistoryError):
            if hasattr(self, "_conn"):
                self._conn.close()
            raise

    def _rows(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT event_seq,event_id,delivery_id,recorded_at_ms,payload_json,"
            "payload_sha256,previous_event_hash,event_hash "
            "FROM vendor_delivery_history_events ORDER BY event_seq"
        ).fetchall()

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> VendorDeliveryRecord:
        try:
            payload = json.loads(row["payload_json"])
            if not isinstance(payload, Mapping):
                raise TypeError
            payload = dict(payload)
            payload["reasons"] = tuple(payload["reasons"])
            return VendorDeliveryRecord(**payload)
        except Exception as exc:
            raise VendorDeliveryHistoryError(
                f"delivery history event {row['event_seq']} payload is invalid"
            ) from exc

    def _verify_store(self) -> None:
        try:
            if (
                self._conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
                != "wal"
                or self._conn.execute("PRAGMA synchronous").fetchone()[0] != 2
                or self._conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1
            ):
                raise VendorDeliveryHistoryError(
                    "delivery history durability pragmas are invalid"
                )
            integrity = self._conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise VendorDeliveryHistoryError("delivery history integrity failed")
            tables = {
                row[0]
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if tables != {
                "vendor_delivery_history_meta",
                "vendor_delivery_history_events",
            }:
                raise VendorDeliveryHistoryError("delivery history schema is invalid")
            expected_columns = {
                "vendor_delivery_history_meta": (
                    ("singleton", "INTEGER", 0, 1),
                    ("store_kind", "TEXT", 1, 0),
                    ("schema_version", "INTEGER", 1, 0),
                ),
                "vendor_delivery_history_events": (
                    ("event_seq", "INTEGER", 0, 1),
                    ("event_id", "TEXT", 1, 0),
                    ("delivery_id", "TEXT", 1, 0),
                    ("recorded_at_ms", "INTEGER", 1, 0),
                    ("payload_json", "TEXT", 1, 0),
                    ("payload_sha256", "TEXT", 1, 0),
                    ("previous_event_hash", "TEXT", 1, 0),
                    ("event_hash", "TEXT", 1, 0),
                ),
            }
            for table, expected in expected_columns.items():
                actual = tuple(
                    (row["name"], row["type"], row["notnull"], row["pk"])
                    for row in self._conn.execute(f"PRAGMA table_info({table})")
                )
                strict = self._conn.execute(
                    "SELECT strict FROM pragma_table_list WHERE name=?",
                    (table,),
                ).fetchone()
                if actual != expected or strict is None or strict[0] != 1:
                    raise VendorDeliveryHistoryError(
                        "delivery history table contract is invalid"
                    )
            triggers = {
                row[0]
                for row in self._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            if triggers != {
                "vendor_delivery_history_meta_no_update",
                "vendor_delivery_history_meta_no_delete",
                "vendor_delivery_history_events_no_update",
                "vendor_delivery_history_events_no_delete",
            }:
                raise VendorDeliveryHistoryError("delivery history triggers are invalid")
            trigger_sql = {
                row["name"]: " ".join(row["sql"].split())
                for row in self._conn.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='trigger'"
                )
            }
            expected_trigger_fragments = {
                "vendor_delivery_history_meta_no_update": (
                    "BEFORE UPDATE ON vendor_delivery_history_meta",
                    "RAISE(ABORT, 'vendor delivery history meta is immutable')",
                ),
                "vendor_delivery_history_meta_no_delete": (
                    "BEFORE DELETE ON vendor_delivery_history_meta",
                    "RAISE(ABORT, 'vendor delivery history meta is immutable')",
                ),
                "vendor_delivery_history_events_no_update": (
                    "BEFORE UPDATE ON vendor_delivery_history_events",
                    "RAISE(ABORT, 'vendor delivery history is append-only')",
                ),
                "vendor_delivery_history_events_no_delete": (
                    "BEFORE DELETE ON vendor_delivery_history_events",
                    "RAISE(ABORT, 'vendor delivery history is append-only')",
                ),
            }
            if any(
                any(fragment not in trigger_sql[name] for fragment in fragments)
                for name, fragments in expected_trigger_fragments.items()
            ):
                raise VendorDeliveryHistoryError(
                    "delivery history trigger contract is invalid"
                )
            meta = self._conn.execute(
                "SELECT store_kind,schema_version FROM vendor_delivery_history_meta "
                "WHERE singleton=1"
            ).fetchone()
            if (
                meta is None
                or meta["store_kind"] != VENDOR_DELIVERY_HISTORY_KIND
                or meta["schema_version"] != VENDOR_DELIVERY_HISTORY_SCHEMA_VERSION
            ):
                raise VendorDeliveryHistoryError("delivery history metadata is invalid")
            previous = ""
            states: dict[str, str] = {}
            for index, row in enumerate(self._rows(), start=1):
                if row["event_seq"] != index:
                    raise VendorDeliveryHistoryError("delivery history is not contiguous")
                record = self._record_from_row(row)
                if record.event_id != vendor_delivery_event_id(
                    record.delivery_id,
                    record.verdict,
                ):
                    raise VendorDeliveryHistoryError(
                        "delivery history event identity is invalid"
                    )
                previous_state = states.get(record.delivery_id)
                if record.verdict == "ARCHIVE_PENDING":
                    if previous_state is not None:
                        raise VendorDeliveryHistoryError(
                            "delivery history lifecycle is invalid"
                        )
                    states[record.delivery_id] = "ARCHIVE_PENDING"
                elif record.verdict == "ADMITTED":
                    if previous_state != "ARCHIVE_PENDING":
                        raise VendorDeliveryHistoryError(
                            "delivery history lifecycle is invalid"
                        )
                    states[record.delivery_id] = "ADMITTED"
                else:
                    if previous_state is not None:
                        raise VendorDeliveryHistoryError(
                            "delivery history lifecycle is invalid"
                        )
                    states[record.delivery_id] = "DENIED"
                payload_sha256 = _payload_sha256(record)
                if (
                    row["event_id"] != record.event_id
                    or
                    row["delivery_id"] != record.delivery_id
                    or row["recorded_at_ms"] != record.recorded_at_ms
                    or row["payload_json"] != _canonical(record.as_payload())
                    or row["payload_sha256"] != payload_sha256
                    or row["previous_event_hash"] != previous
                    or row["event_hash"]
                    != _event_hash(
                        event_seq=index,
                        event_id=record.event_id,
                        delivery_id=record.delivery_id,
                        recorded_at_ms=record.recorded_at_ms,
                        payload_sha256=payload_sha256,
                        previous_event_hash=previous,
                    )
                ):
                    raise VendorDeliveryHistoryError(
                        "delivery history hash chain is invalid"
                    )
                previous = row["event_hash"]
        except sqlite3.DatabaseError as exc:
            raise VendorDeliveryHistoryError(
                "delivery history authority is unreadable"
            ) from exc

    def head(self) -> VendorDeliveryHead | None:
        row = self._conn.execute(
            "SELECT event_seq,event_hash,recorded_at_ms "
            "FROM vendor_delivery_history_events ORDER BY event_seq DESC LIMIT 1"
        ).fetchone()
        return VendorDeliveryHead(**dict(row)) if row is not None else None

    def append(
        self,
        record: VendorDeliveryRecord,
        *,
        expected_head: VendorDeliveryHead | None,
    ) -> tuple[VendorDeliveryRecord, bool]:
        if not isinstance(record, VendorDeliveryRecord):
            raise TypeError("record must be a VendorDeliveryRecord")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._verify_store()
            existing = self._conn.execute(
                "SELECT * FROM vendor_delivery_history_events WHERE event_id=?",
                (record.event_id,),
            ).fetchone()
            if existing is not None:
                retained = self._record_from_row(existing)
                if retained != record:
                    raise VendorDeliveryHistoryConflict(record.event_id)
                self._conn.rollback()
                return retained, False
            actual_head = self.head()
            if actual_head != expected_head:
                raise VendorDeliveryHistoryError("VENDOR_DELIVERY_HISTORY_HEAD_CHANGED")
            if actual_head is not None and record.recorded_at_ms < actual_head.recorded_at_ms:
                raise VendorDeliveryHistoryClockRollback(record.delivery_id)
            event_seq = 1 if actual_head is None else actual_head.event_seq + 1
            previous = "" if actual_head is None else actual_head.event_hash
            payload_json = _canonical(record.as_payload())
            payload_sha256 = _sha256(payload_json)
            digest = _event_hash(
                event_seq=event_seq,
                event_id=record.event_id,
                delivery_id=record.delivery_id,
                recorded_at_ms=record.recorded_at_ms,
                payload_sha256=payload_sha256,
                previous_event_hash=previous,
            )
            self._conn.execute(
                "INSERT INTO vendor_delivery_history_events "
                "(event_seq,event_id,delivery_id,recorded_at_ms,payload_json,"
                "payload_sha256,previous_event_hash,event_hash) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    event_seq,
                    record.event_id,
                    record.delivery_id,
                    record.recorded_at_ms,
                    payload_json,
                    payload_sha256,
                    previous,
                    digest,
                ),
            )
            self._verify_store()
            self._conn.commit()
            return record, True
        except Exception:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    def records(self) -> tuple[VendorDeliveryRecord, ...]:
        self._verify_store()
        return tuple(self._record_from_row(row) for row in self._rows())

    def open_deliveries(self) -> tuple[VendorDeliveryRecord, ...]:
        records = self.records()
        terminal = {
            record.delivery_id
            for record in records
            if record.verdict in {"ADMITTED", "DENIED"}
        }
        return tuple(
            record
            for record in records
            if record.verdict == "ARCHIVE_PENDING"
            and record.delivery_id not in terminal
        )

    def chain_valid(self) -> bool:
        try:
            self._verify_store()
            return True
        except VendorDeliveryHistoryError:
            return False

    def close(self) -> None:
        self._conn.close()


__all__ = [
    "VENDOR_DELIVERY_HISTORY_KIND",
    "VENDOR_DELIVERY_HISTORY_SCHEMA_VERSION",
    "VendorDeliveryHead",
    "VendorDeliveryHistoryClockRollback",
    "VendorDeliveryHistoryConflict",
    "VendorDeliveryHistoryError",
    "VendorDeliveryHistoryStore",
    "VendorDeliveryRecord",
    "vendor_delivery_event_id",
]
