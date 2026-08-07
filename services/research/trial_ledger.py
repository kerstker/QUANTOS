"""Append-only, fail-closed trial provenance ledger."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Mapping

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_STATES = {"PLANNED", "RUNNING", "PASSED", "FAILED", "CANCELLED"}
_TRANSITIONS = {"PLANNED": {"RUNNING", "CANCELLED"}, "RUNNING": {"PASSED", "FAILED", "CANCELLED"}, "PASSED": set(), "FAILED": set(), "CANCELLED": set()}
_IDENTITY = ("alpha_id", "family_id", "exact_definition_id", "validation_policy_digest")
_RECEIPT_STATES = {"CLAIMED", "RUNNING", "DONE", "BLOCKED", "FAILED"}


class TrialLedgerError(RuntimeError):
    pass


class TrialLedgerConflict(TrialLedgerError):
    pass


@dataclass(frozen=True)
class TrialRecord:
    trial_id: str
    alpha_id: str
    family_id: str
    exact_definition_id: str
    validation_policy_digest: str
    status: str
    event_index: int
    at: int


@dataclass(frozen=True)
class TrialEvent(TrialRecord):
    global_seq: int
    prev_hash: str
    event_hash: str


@dataclass(frozen=True)
class TrialAggregate:
    trial_id: str
    alpha_id: str
    family_id: str
    exact_definition_id: str
    validation_policy_digest: str
    events: tuple[TrialEvent, ...]

    @property
    def status(self):
        return self.events[-1].status


@dataclass(frozen=True)
class EffectiveTrialCount:
    count: int
    trial_ids: tuple[str, ...]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trial_events (trial_id TEXT NOT NULL,event_index INTEGER NOT NULL,alpha_id TEXT NOT NULL,family_id TEXT NOT NULL,exact_definition_id TEXT NOT NULL,validation_policy_digest TEXT NOT NULL,status TEXT NOT NULL,at INTEGER NOT NULL,global_seq INTEGER NOT NULL UNIQUE,prev_hash TEXT NOT NULL,event_hash TEXT NOT NULL,PRIMARY KEY(trial_id,event_index));
CREATE TABLE IF NOT EXISTS trial_heads (trial_id TEXT PRIMARY KEY,event_count INTEGER NOT NULL,head_hash TEXT NOT NULL,global_count INTEGER NOT NULL,global_head_hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS trial_events_no_update BEFORE UPDATE ON trial_events BEGIN SELECT RAISE(ABORT,'trial_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trial_events_no_delete BEFORE DELETE ON trial_events BEGIN SELECT RAISE(ABORT,'trial_events is append-only'); END;
CREATE TABLE IF NOT EXISTS validation_receipt_events (trial_id TEXT NOT NULL,stage TEXT NOT NULL,event_index INTEGER NOT NULL,state TEXT NOT NULL,payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,PRIMARY KEY(trial_id,stage,event_index));
CREATE TRIGGER IF NOT EXISTS validation_receipts_no_update BEFORE UPDATE ON validation_receipt_events BEGIN SELECT RAISE(ABORT,'validation receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS validation_receipts_no_delete BEFORE DELETE ON validation_receipt_events BEGIN SELECT RAISE(ABORT,'validation receipts are append-only'); END;
"""
_SELECT = "SELECT trial_id,event_index,alpha_id,family_id,exact_definition_id,validation_policy_digest,status,at,global_seq,prev_hash,event_hash FROM trial_events"
_RECEIPT_SELECT = "SELECT stage,event_index,state,payload_json,payload_hash FROM validation_receipt_events WHERE trial_id=? ORDER BY stage,event_index"


def _canonical(r: TrialRecord, seq: int) -> bytes:
    return json.dumps({"trial_id": r.trial_id, "alpha_id": r.alpha_id, "family_id": r.family_id, "exact_definition_id": r.exact_definition_id, "validation_policy_digest": r.validation_policy_digest, "status": r.status, "event_index": r.event_index, "at": r.at, "global_seq": seq}, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def _hash(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _plain(value):
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value):
            raise TrialLedgerError("receipt payload keys must be strings")
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value


def _canonical_payload(payload: Mapping[str, Any]) -> tuple[str, str]:
    if not isinstance(payload, Mapping) or any(type(key) is not str for key in payload):
        raise TrialLedgerError("receipt payload keys must be strings")
    try:
        encoded = json.dumps(_plain(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TrialLedgerError("receipt payload is not canonical") from exc
    return encoded, _hash(encoded.encode())


class TrialLedger:
    def __init__(self, db_path: str, create: bool = True):
        self._c = None
        try:
            if not isinstance(db_path, str):
                raise TrialLedgerError("db_path must be a string")
            if not create and db_path != ":memory:" and not os.path.isfile(db_path):
                raise TrialLedgerError("ledger database does not exist")
            if create and db_path != ":memory:":
                parent = os.path.dirname(db_path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
            self._c = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
            self._c.isolation_level = None
            self._c.execute("PRAGMA busy_timeout=30000")
            self._lock = threading.RLock()
            if create:
                with self._lock:
                    self._c.executescript(_SCHEMA)
            self._require_schema()
        except TrialLedgerError:
            if self._c is not None:
                self._c.close()
            raise
        except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
            if self._c is not None:
                self._c.close()
            raise TrialLedgerError("ledger database unavailable") from exc

    def _require_schema(self):
        expected_events = [("trial_id", "TEXT", 1, 1), ("event_index", "INTEGER", 1, 2), ("alpha_id", "TEXT", 1, 0), ("family_id", "TEXT", 1, 0), ("exact_definition_id", "TEXT", 1, 0), ("validation_policy_digest", "TEXT", 1, 0), ("status", "TEXT", 1, 0), ("at", "INTEGER", 1, 0), ("global_seq", "INTEGER", 1, 0), ("prev_hash", "TEXT", 1, 0), ("event_hash", "TEXT", 1, 0)]
        expected_heads = [("trial_id", "TEXT", 0, 1), ("event_count", "INTEGER", 1, 0), ("head_hash", "TEXT", 1, 0), ("global_count", "INTEGER", 1, 0), ("global_head_hash", "TEXT", 1, 0)]
        expected_receipts = [("trial_id", "TEXT", 1, 1), ("stage", "TEXT", 1, 2), ("event_index", "INTEGER", 1, 3), ("state", "TEXT", 1, 0), ("payload_json", "TEXT", 1, 0), ("payload_hash", "TEXT", 1, 0)]
        names = {r[0] for r in self._c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if not {"trial_events", "trial_heads", "validation_receipt_events"} <= names:
            raise TrialLedgerError("ledger schema missing")
        def info(table):
            return [(r[1], r[2].upper(), r[3], r[5]) for r in self._c.execute(f"PRAGMA table_info({table})").fetchall()]
        if info("trial_events") != expected_events or info("trial_heads") != expected_heads or info("validation_receipt_events") != expected_receipts:
            raise TrialLedgerError("ledger table schema mismatch")
        indexes = self._c.execute("PRAGMA index_list(trial_events)").fetchall()
        if not any(r[2] and [x[2] for x in self._c.execute(f"PRAGMA index_info('{r[1]}')").fetchall()] == ["global_seq"] for r in indexes):
            raise TrialLedgerError("global_seq UNIQUE commitment missing")
        triggers = {r[0]: " ".join((r[1] or "").lower().split()) for r in self._c.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()}
        expected = {
            "trial_events_no_update": "create trigger trial_events_no_update before update on trial_events begin select raise(abort,'trial_events is append-only'); end",
            "trial_events_no_delete": "create trigger trial_events_no_delete before delete on trial_events begin select raise(abort,'trial_events is append-only'); end",
            "validation_receipts_no_update": "create trigger validation_receipts_no_update before update on validation_receipt_events begin select raise(abort,'validation receipts are append-only'); end",
            "validation_receipts_no_delete": "create trigger validation_receipts_no_delete before delete on validation_receipt_events begin select raise(abort,'validation receipts are append-only'); end",
        }
        if any(triggers.get(name) != sql for name, sql in expected.items()):
            raise TrialLedgerError("append-only trigger schema mismatch")

    @staticmethod
    def _validate_record(r):
        if not isinstance(r, TrialRecord):
            raise TrialLedgerError("record type required")
        for name in _IDENTITY + ("trial_id",):
            if not isinstance(getattr(r, name), str) or not _HEX64.fullmatch(getattr(r, name)):
                raise TrialLedgerError(f"invalid {name}")
        if not isinstance(r.status, str) or r.status not in _STATES:
            raise TrialLedgerError("unknown trial status")
        if not isinstance(r.event_index, int) or isinstance(r.event_index, bool) or r.event_index < 0:
            raise TrialLedgerError("invalid event_index")
        if not isinstance(r.at, int) or isinstance(r.at, bool):
            raise TrialLedgerError("invalid at")

    def close(self):
        with getattr(self, "_lock", threading.RLock()):
            if self._c is not None:
                try:
                    self._c.close()
                except sqlite3.Error:
                    pass
                self._c = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def _global_head(self):
        row = self._c.execute("SELECT global_count,global_head_hash FROM trial_heads WHERE trial_id='' ").fetchone()
        return row if row else (0, "")

    def _rows(self, trial_id=None):
        sql = _SELECT + (" WHERE trial_id=?" if trial_id else "") + " ORDER BY global_seq"
        return self._c.execute(sql, (trial_id,) if trial_id else ()).fetchall()

    def _verify_locked(self):
        rows = self._rows(); global_prev = ""; trial_prev = {}; trial_counts = {}; identities = {}; statuses = {}; last_global = {}
        for pos, row in enumerate(rows, 1):
            tid, idx, alpha, family, exact, policy, status, at, seq, prev, event_hash = row
            if seq != pos or idx != trial_counts.get(tid, 0) or prev != trial_prev.get(tid, ""):
                raise TrialLedgerError("trial sequence corruption")
            record = TrialRecord(tid, alpha, family, exact, policy, status, idx, at)
            self._validate_record(record)
            identity = tuple(getattr(record, n) for n in _IDENTITY)
            if tid in identities and identities[tid] != identity:
                raise TrialLedgerError("trial identity drift")
            identities[tid] = identity
            prior = statuses.get(tid)
            if prior is None and status != "PLANNED":
                raise TrialLedgerError("first event must be PLANNED")
            if prior is not None and status not in _TRANSITIONS[prior]:
                raise TrialLedgerError("illegal trial transition")
            if event_hash != _hash((prev + global_prev).encode() + _canonical(record, seq)):
                raise TrialLedgerError("event hash mismatch")
            trial_prev[tid] = event_hash; trial_counts[tid] = idx + 1; statuses[tid] = status; global_prev = event_hash; last_global[tid] = (seq, event_hash)
        if self._global_head() != (len(rows), global_prev):
            raise TrialLedgerError("global commitment mismatch")
        heads = self._c.execute("SELECT trial_id,event_count,head_hash,global_count,global_head_hash FROM trial_heads WHERE trial_id!=''").fetchall()
        if {r[0] for r in heads} != set(trial_counts):
            raise TrialLedgerError("trial heads mismatch")
        for tid, count, head, gcount, ghead in heads:
            if (count, head) != (trial_counts[tid], trial_prev[tid]) or (gcount, ghead) != last_global[tid]:
                raise TrialLedgerError("trial commitment mismatch")

    def _aggregate_locked(self, trial_id):
        rows = self._rows(trial_id)
        if not rows:
            raise TrialLedgerError("unknown trial")
        events = tuple(TrialEvent(tid, alpha, family, exact, policy, status, idx, at, seq, prev, event_hash) for tid, idx, alpha, family, exact, policy, status, at, seq, prev, event_hash in rows)
        first = events[0]
        return TrialAggregate(trial_id, first.alpha_id, first.family_id, first.exact_definition_id, first.validation_policy_digest, events)

    def append(self, record: TrialRecord) -> TrialAggregate:
        self._validate_record(record)
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE"); self._verify_locked(); prior = self._rows(record.trial_id)
                if prior:
                    identity = tuple(prior[0][i] for i in (2, 3, 4, 5))
                    if tuple(getattr(record, n) for n in _IDENTITY) != identity:
                        raise TrialLedgerConflict("trial identity drift")
                existing = self._c.execute(_SELECT + " WHERE trial_id=? AND event_index=?", (record.trial_id, record.event_index)).fetchone()
                if existing:
                    stored = TrialRecord(existing[0], existing[2], existing[3], existing[4], existing[5], existing[6], existing[1], existing[7])
                    if stored != record:
                        raise TrialLedgerConflict("same trial/event key has different content")
                    aggregate = self._aggregate_locked(record.trial_id); self._c.execute("COMMIT"); return aggregate
                if record.event_index != len(prior):
                    raise TrialLedgerError("event_index is not next")
                if (not prior and record.status != "PLANNED") or (prior and record.status not in _TRANSITIONS[prior[-1][6]]):
                    raise TrialLedgerError("illegal state transition")
                count, global_prev = self._global_head(); seq = count + 1; prev = prior[-1][10] if prior else ""; event_hash = _hash((prev + global_prev).encode() + _canonical(record, seq))
                self._c.execute("INSERT INTO trial_events VALUES (?,?,?,?,?,?,?,?,?,?,?)", (record.trial_id, record.event_index, record.alpha_id, record.family_id, record.exact_definition_id, record.validation_policy_digest, record.status, record.at, seq, prev, event_hash))
                self._c.execute("INSERT INTO trial_heads VALUES (?,?,?,?,?) ON CONFLICT(trial_id) DO UPDATE SET event_count=excluded.event_count,head_hash=excluded.head_hash,global_count=excluded.global_count,global_head_hash=excluded.global_head_hash", (record.trial_id, len(prior) + 1, event_hash, seq, event_hash))
                self._c.execute("INSERT INTO trial_heads VALUES ('',0,'',?,?) ON CONFLICT(trial_id) DO UPDATE SET global_count=excluded.global_count,global_head_hash=excluded.global_head_hash", (seq, event_hash))
                aggregate = self._aggregate_locked(record.trial_id); self._c.execute("COMMIT"); return aggregate
            except TrialLedgerError:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                raise
            except sqlite3.IntegrityError as exc:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                raise TrialLedgerConflict("append race or constraint conflict") from exc
            except (sqlite3.Error, OSError) as exc:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                raise TrialLedgerError("ledger database failure") from exc

    def _receipt_rows_locked(self, trial_id):
        latest = {}
        for stage, index, state, payload_json, payload_hash in self._c.execute(_RECEIPT_SELECT, (trial_id,)).fetchall():
            if _hash(payload_json.encode()) != payload_hash:
                raise TrialLedgerError("validation receipt hash mismatch")
            try: payload = json.loads(payload_json)
            except json.JSONDecodeError as exc: raise TrialLedgerError("validation receipt JSON invalid") from exc
            if not isinstance(payload, dict) or (stage in latest and index != latest[stage]["event_index"] + 1):
                raise TrialLedgerError("validation receipt sequence corruption")
            if state not in _RECEIPT_STATES:
                raise TrialLedgerError("validation receipt state invalid")
            latest[stage] = {"event_index": index, "state": state, "payload": payload, "payload_hash": payload_hash}
        return latest

    def validation_receipt(self, trial_id: str) -> dict[str, dict[str, Any]]:
        if not isinstance(trial_id, str) or not _HEX64.fullmatch(trial_id):
            raise TrialLedgerError("invalid trial_id")
        with self._lock:
            return self._receipt_rows_locked(trial_id)

    def _receipt_insert(self, trial_id, stage, index, state, payload):
        encoded, digest = _canonical_payload(payload)
        self._c.execute("INSERT INTO validation_receipt_events VALUES (?,?,?,?,?,?)", (trial_id, stage, index, state, encoded, digest))

    def claim_validation_stage(self, trial_id: str, stage: str) -> str:
        if not isinstance(trial_id, str) or not _HEX64.fullmatch(trial_id) or not isinstance(stage, str) or not stage:
            raise TrialLedgerError("invalid validation receipt identity")
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE"); current = self._receipt_rows_locked(trial_id).get(stage)
                if current is not None:
                    self._c.execute("COMMIT"); return "DONE" if current["state"] == "DONE" else "IN_PROGRESS"
                self._receipt_insert(trial_id, stage, 0, "CLAIMED", {"stage": stage})
                self._c.execute("COMMIT"); return "CLAIMED"
            except sqlite3.IntegrityError as exc:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                raise TrialLedgerConflict("validation stage claim conflict") from exc
            except (TrialLedgerError, sqlite3.Error) as exc:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                if isinstance(exc, TrialLedgerError): raise
                raise TrialLedgerError("ledger database failure") from exc

    def complete_validation_stage(self, trial_id: str, stage: str, state: str, payload: Mapping[str, Any]) -> None:
        if state not in {"DONE", "BLOCKED", "FAILED"}:
            raise TrialLedgerError("invalid validation receipt state")
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE"); current = self._receipt_rows_locked(trial_id).get(stage)
                if current is None or current["state"] not in {"CLAIMED", "RUNNING"}:
                    raise TrialLedgerConflict("validation stage is not claimed")
                self._receipt_insert(trial_id, stage, current["event_index"] + 1, state, payload); self._c.execute("COMMIT")
            except (TrialLedgerError, sqlite3.Error) as exc:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                if isinstance(exc, TrialLedgerError): raise
                raise TrialLedgerError("ledger database failure") from exc

    def reopen_validation_stage(self, trial_id: str, stage: str) -> str:
        """Append a new claim after a terminal receipt, never mutate history."""
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE"); current = self._receipt_rows_locked(trial_id).get(stage)
                if current is None or current["state"] not in {"DONE", "BLOCKED", "FAILED"}:
                    raise TrialLedgerConflict("validation stage is not terminal")
                self._receipt_insert(trial_id, stage, current["event_index"] + 1, "CLAIMED", {"stage": stage, "retry": current["event_index"] + 1}); self._c.execute("COMMIT"); return "CLAIMED"
            except (TrialLedgerError, sqlite3.Error) as exc:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                if isinstance(exc, TrialLedgerError): raise
                raise TrialLedgerError("ledger database failure") from exc

    def recover_validation_stage(self, trial_id: str, stage: str) -> str:
        """Safely recover a legacy RUNNING receipt as a new claim."""
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE"); current = self._receipt_rows_locked(trial_id).get(stage)
                if current is None or current["state"] != "RUNNING":
                    self._c.execute("COMMIT"); return "IN_PROGRESS" if current else "CLAIMED"
                self._receipt_insert(trial_id, stage, current["event_index"] + 1, "CLAIMED", {"stage": stage, "recovered": True}); self._c.execute("COMMIT"); return "CLAIMED"
            except (TrialLedgerError, sqlite3.Error) as exc:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                if isinstance(exc, TrialLedgerError): raise
                raise TrialLedgerError("ledger database failure") from exc

    def save_validation_artifact(self, trial_id: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE"); current = self._receipt_rows_locked(trial_id).get("__artifact__"); encoded, digest = _canonical_payload(payload)
                if current is not None:
                    if current["payload_hash"] != digest: raise TrialLedgerConflict("validation artifact conflict")
                else:
                    self._receipt_insert(trial_id, "__artifact__", 0, "DONE", payload)
                self._c.execute("COMMIT")
            except (TrialLedgerError, sqlite3.Error) as exc:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                if isinstance(exc, TrialLedgerError): raise
                raise TrialLedgerError("ledger database failure") from exc

    def _verify_receipts_locked(self):
        for (trial_id,) in self._c.execute("SELECT DISTINCT trial_id FROM validation_receipt_events").fetchall():
            self._receipt_rows_locked(trial_id)

    def verify(self) -> bool:
        with self._lock:
            try:
                self._c.execute("BEGIN"); self._verify_locked(); self._verify_receipts_locked(); self._c.execute("COMMIT"); return True
            except TrialLedgerError:
                try: self._c.execute("ROLLBACK")
                except (sqlite3.Error, AttributeError): pass
                raise
            except (sqlite3.Error, AttributeError) as exc:
                try: self._c.execute("ROLLBACK")
                except (sqlite3.Error, AttributeError): pass
                raise TrialLedgerError("ledger database failure") from exc

    def load(self, trial_id: str) -> TrialAggregate:
        if not isinstance(trial_id, str) or not _HEX64.fullmatch(trial_id): raise TrialLedgerError("invalid trial_id")
        with self._lock:
            try:
                self._c.execute("BEGIN"); self._verify_locked(); aggregate = self._aggregate_locked(trial_id); self._c.execute("COMMIT"); return aggregate
            except TrialLedgerError:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                raise
            except sqlite3.Error as exc:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                raise TrialLedgerError("ledger database failure") from exc

    def effective_trial_count(self, family_id: str) -> EffectiveTrialCount:
        if not isinstance(family_id, str) or not _HEX64.fullmatch(family_id): raise TrialLedgerError("invalid family_id")
        with self._lock:
            try:
                self._c.execute("BEGIN"); self._verify_locked(); rows = self._c.execute(_SELECT + " WHERE event_index=0 ORDER BY global_seq").fetchall(); seen = set(); selected = []
                for row in rows:
                    if row[3] != family_id: continue
                    aggregate = self._aggregate_locked(row[0])
                    if aggregate.status == "PLANNED": continue
                    key = (aggregate.exact_definition_id, aggregate.validation_policy_digest)
                    if key not in seen: seen.add(key); selected.append(row[0])
                self._c.execute("COMMIT"); return EffectiveTrialCount(len(selected), tuple(selected))
            except TrialLedgerError:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                raise
            except sqlite3.Error as exc:
                try: self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                raise TrialLedgerError("ledger database failure") from exc


__all__ = ["TrialLedger", "TrialLedgerError", "TrialLedgerConflict", "TrialRecord", "TrialEvent", "TrialAggregate", "EffectiveTrialCount"]
