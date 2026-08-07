"""Append-only, fail-closed trial provenance ledger."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_STATES = {"PLANNED", "RUNNING", "PASSED", "FAILED", "CANCELLED"}
_TRANSITIONS = {
    "PLANNED": {"RUNNING", "CANCELLED"},
    "RUNNING": {"PASSED", "FAILED", "CANCELLED"},
    "PASSED": set(), "FAILED": set(), "CANCELLED": set(),
}


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
    def status(self) -> str:
        return self.events[-1].status


@dataclass(frozen=True)
class EffectiveTrialCount:
    count: int
    trial_ids: tuple[str, ...]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trial_events (
  trial_id TEXT NOT NULL, event_index INTEGER NOT NULL,
  alpha_id TEXT NOT NULL, family_id TEXT NOT NULL,
  exact_definition_id TEXT NOT NULL, validation_policy_digest TEXT NOT NULL,
  status TEXT NOT NULL, at INTEGER NOT NULL, global_seq INTEGER NOT NULL UNIQUE,
  prev_hash TEXT NOT NULL, event_hash TEXT NOT NULL,
  PRIMARY KEY (trial_id, event_index)
);
CREATE TABLE IF NOT EXISTS trial_heads (
  trial_id TEXT PRIMARY KEY, event_count INTEGER NOT NULL, head_hash TEXT NOT NULL,
  global_count INTEGER NOT NULL, global_head_hash TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trial_events_no_update BEFORE UPDATE ON trial_events
BEGIN SELECT RAISE(ABORT, 'trial_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trial_events_no_delete BEFORE DELETE ON trial_events
BEGIN SELECT RAISE(ABORT, 'trial_events is append-only'); END;
"""


def _canonical(record: TrialRecord, global_seq: int) -> bytes:
    return json.dumps({
        "trial_id": record.trial_id, "alpha_id": record.alpha_id,
        "family_id": record.family_id, "exact_definition_id": record.exact_definition_id,
        "validation_policy_digest": record.validation_policy_digest,
        "status": record.status, "event_index": record.event_index,
        "at": record.at, "global_seq": global_seq,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class TrialLedger:
    def __init__(self, db_path: str, create: bool = True):
        if create and db_path != ":memory:":
            parent = os.path.dirname(db_path)
            if parent: os.makedirs(parent, exist_ok=True)
        self._c = sqlite3.connect(db_path, check_same_thread=False)
        self._c.isolation_level = None
        self._lock = threading.RLock()
        if create:
            with self._lock: self._c.executescript(_SCHEMA)

    @staticmethod
    def _validate_record(r: TrialRecord) -> None:
        if not isinstance(r, TrialRecord): raise TrialLedgerError("record type required")
        for name in ("trial_id", "alpha_id", "family_id", "exact_definition_id", "validation_policy_digest"):
            value = getattr(r, name)
            if not isinstance(value, str) or not _HEX64.fullmatch(value):
                raise TrialLedgerError(f"invalid {name}")
        if r.status not in _STATES: raise TrialLedgerError("unknown trial status")
        if not isinstance(r.event_index, int) or isinstance(r.event_index, bool) or r.event_index < 0:
            raise TrialLedgerError("invalid event_index")
        if not isinstance(r.at, int) or isinstance(r.at, bool): raise TrialLedgerError("invalid at")
        try: _canonical(r, 0)
        except (TypeError, ValueError, OverflowError) as exc: raise TrialLedgerError("non-canonical record") from exc

    def _head(self, trial_id: str):
        return self._c.execute("SELECT event_count,head_hash FROM trial_heads WHERE trial_id=?", (trial_id,)).fetchone()

    def _global_head(self):
        return self._c.execute("SELECT global_count,global_head_hash FROM trial_heads WHERE trial_id='' ").fetchone() or (0, "")

    def append(self, record: TrialRecord) -> TrialAggregate:
        self._validate_record(record)
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                existing = self._c.execute("SELECT alpha_id,family_id,exact_definition_id,validation_policy_digest,status,at,global_seq,prev_hash,event_hash FROM trial_events WHERE trial_id=? AND event_index=?", (record.trial_id, record.event_index)).fetchone()
                if existing:
                    candidate = TrialRecord(record.trial_id, existing[0], existing[1], existing[2], existing[3], existing[4], record.event_index, existing[5])
                    seq = existing[6]
                    if _hash(_canonical(record, seq)) != existing[8] or tuple(record.__dict__.values())[:6] != tuple(candidate.__dict__.values())[:6] or record.at != existing[5]:
                        raise TrialLedgerConflict("same trial/event key has different content")
                    self._c.execute("COMMIT")
                    return self.load(record.trial_id)
                head = self._head(record.trial_id)
                expected = 0 if head is None else head[0]
                if record.event_index != expected: raise TrialLedgerError("event_index is not next")
                if head is None:
                    if record.status != "PLANNED": raise TrialLedgerError("first event must be PLANNED")
                    prev = ""
                else:
                    prior = self._c.execute("SELECT status FROM trial_events WHERE trial_id=? AND event_index=?", (record.trial_id, expected - 1)).fetchone()
                    if prior is None or record.status not in _TRANSITIONS[prior[0]]: raise TrialLedgerError("illegal state transition")
                    prev = head[1]
                count, global_prev = self._global_head(); seq = count + 1
                event_hash = _hash((prev + global_prev).encode() + _canonical(record, seq))
                self._c.execute("INSERT INTO trial_events VALUES (?,?,?,?,?,?,?,?,?,?,?)", (record.trial_id, record.event_index, record.alpha_id, record.family_id, record.exact_definition_id, record.validation_policy_digest, record.status, record.at, seq, prev, event_hash))
                self._c.execute("INSERT INTO trial_heads VALUES (?,?,?,?,?) ON CONFLICT(trial_id) DO UPDATE SET event_count=excluded.event_count,head_hash=excluded.head_hash,global_count=excluded.global_count,global_head_hash=excluded.global_head_hash", (record.trial_id, expected + 1, event_hash, seq, event_hash))
                self._c.execute("INSERT INTO trial_heads(trial_id,event_count,head_hash,global_count,global_head_hash) VALUES ('',0,'',?,?) ON CONFLICT(trial_id) DO UPDATE SET global_count=excluded.global_count,global_head_hash=excluded.global_head_hash", (seq, event_hash))
                self._c.execute("COMMIT")
            except TrialLedgerError:
                self._c.execute("ROLLBACK"); raise
            except sqlite3.IntegrityError as exc:
                self._c.execute("ROLLBACK"); raise TrialLedgerConflict("append race or constraint conflict") from exc
            except Exception:
                self._c.execute("ROLLBACK"); raise
        return self.load(record.trial_id)

    def _verify_locked(self) -> None:
        rows = self._c.execute("SELECT trial_id,event_index,alpha_id,family_id,exact_definition_id,validation_policy_digest,status,at,global_seq,prev_hash,event_hash FROM trial_events ORDER BY global_seq").fetchall()
        trial_prev: dict[str, str] = {}; trial_counts: dict[str, int] = {}; global_prev = ""
        for pos, row in enumerate(rows, 1):
            tid, idx, alpha, family, exact, policy, status, at, seq, prev, event_hash = row
            if seq != pos or idx != trial_counts.get(tid, 0) or prev != trial_prev.get(tid, "") or status not in _STATES: raise TrialLedgerError("trial chain corruption")
            r = TrialRecord(tid, alpha, family, exact, policy, status, idx, at)
            self._validate_record(r)
            if event_hash != _hash((prev + global_prev).encode() + _canonical(r, seq)): raise TrialLedgerError("event hash mismatch")
            trial_prev[tid] = event_hash; trial_counts[tid] = idx + 1; global_prev = event_hash
        gh = self._global_head()
        if gh != (len(rows), global_prev): raise TrialLedgerError("global commitment mismatch")
        heads = self._c.execute("SELECT trial_id,event_count,head_hash FROM trial_heads WHERE trial_id!=''").fetchall()
        if {r[0] for r in heads} != set(trial_counts): raise TrialLedgerError("trial heads mismatch")
        for tid, count, head in heads:
            if (count, head) != (trial_counts[tid], trial_prev[tid]): raise TrialLedgerError("trial commitment mismatch")

    def verify(self) -> bool:
        with self._lock: self._verify_locked()
        return True

    def load(self, trial_id: str) -> TrialAggregate:
        if not isinstance(trial_id, str) or not _HEX64.fullmatch(trial_id): raise TrialLedgerError("invalid trial_id")
        self.verify()
        with self._lock:
            rows = self._c.execute("SELECT trial_id,event_index,alpha_id,family_id,exact_definition_id,validation_policy_digest,status,at,global_seq,prev_hash,event_hash FROM trial_events WHERE trial_id=? ORDER BY event_index", (trial_id,)).fetchall()
        if not rows: raise TrialLedgerError("unknown trial")
        events = tuple(TrialEvent(tid, alpha, family, exact, policy, status, idx, at, seq, prev, event_hash)
                       for tid, idx, alpha, family, exact, policy, status, at, seq, prev, event_hash in rows)
        first = events[0]
        return TrialAggregate(trial_id, first.alpha_id, first.family_id, first.exact_definition_id, first.validation_policy_digest, events)

    def effective_trial_count(self, family_id: str) -> EffectiveTrialCount:
        if not isinstance(family_id, str) or not _HEX64.fullmatch(family_id): raise TrialLedgerError("invalid family_id")
        self.verify()
        with self._lock:
            ids = self._c.execute("SELECT trial_id FROM trial_events WHERE family_id=? GROUP BY trial_id ORDER BY min(global_seq),trial_id", (family_id,)).fetchall()
        seen = set(); selected = []
        for (tid,) in ids:
            agg = self.load(tid)
            if agg.status == "PLANNED": continue
            key = (agg.exact_definition_id, agg.validation_policy_digest)
            if key not in seen: seen.add(key); selected.append(tid)
        return EffectiveTrialCount(len(selected), tuple(selected))
