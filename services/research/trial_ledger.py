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
_TRANSITIONS = {"PLANNED": {"RUNNING", "CANCELLED"}, "RUNNING": {"PASSED", "FAILED", "CANCELLED"}, "PASSED": set(), "FAILED": set(), "CANCELLED": set()}
_IDENTITY = ("alpha_id", "family_id", "exact_definition_id", "validation_policy_digest")

class TrialLedgerError(RuntimeError): pass
class TrialLedgerConflict(TrialLedgerError): pass

@dataclass(frozen=True)
class TrialRecord:
    trial_id: str; alpha_id: str; family_id: str; exact_definition_id: str; validation_policy_digest: str; status: str; event_index: int; at: int
@dataclass(frozen=True)
class TrialEvent(TrialRecord):
    global_seq: int; prev_hash: str; event_hash: str
@dataclass(frozen=True)
class TrialAggregate:
    trial_id: str; alpha_id: str; family_id: str; exact_definition_id: str; validation_policy_digest: str; events: tuple[TrialEvent, ...]
    @property
    def status(self): return self.events[-1].status
@dataclass(frozen=True)
class EffectiveTrialCount:
    count: int; trial_ids: tuple[str, ...]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trial_events (trial_id TEXT NOT NULL,event_index INTEGER NOT NULL,alpha_id TEXT NOT NULL,family_id TEXT NOT NULL,exact_definition_id TEXT NOT NULL,validation_policy_digest TEXT NOT NULL,status TEXT NOT NULL,at INTEGER NOT NULL,global_seq INTEGER NOT NULL UNIQUE,prev_hash TEXT NOT NULL,event_hash TEXT NOT NULL,PRIMARY KEY(trial_id,event_index));
CREATE TABLE IF NOT EXISTS trial_heads (trial_id TEXT PRIMARY KEY,event_count INTEGER NOT NULL,head_hash TEXT NOT NULL,global_count INTEGER NOT NULL,global_head_hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS trial_events_no_update BEFORE UPDATE ON trial_events BEGIN SELECT RAISE(ABORT,'trial_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trial_events_no_delete BEFORE DELETE ON trial_events BEGIN SELECT RAISE(ABORT,'trial_events is append-only'); END;
"""
_SELECT = "SELECT trial_id,event_index,alpha_id,family_id,exact_definition_id,validation_policy_digest,status,at,global_seq,prev_hash,event_hash FROM trial_events"

def _canonical(r: TrialRecord, seq: int) -> bytes:
    return json.dumps({"trial_id":r.trial_id,"alpha_id":r.alpha_id,"family_id":r.family_id,"exact_definition_id":r.exact_definition_id,"validation_policy_digest":r.validation_policy_digest,"status":r.status,"event_index":r.event_index,"at":r.at,"global_seq":seq}, sort_keys=True, separators=(",",":"), ensure_ascii=True, allow_nan=False).encode()
def _hash(b: bytes) -> str: return hashlib.sha256(b).hexdigest()

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
                if parent: os.makedirs(parent, exist_ok=True)
            self._c = sqlite3.connect(db_path, check_same_thread=False)
            self._c.isolation_level = None
            self._lock = threading.RLock()
            if create:
                with self._lock: self._c.executescript(_SCHEMA)
            self._require_schema()
        except TrialLedgerError:
            if self._c is not None:
                try: self._c.close()
                except sqlite3.Error: pass
            raise
        except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
            if self._c is not None:
                try: self._c.close()
                except sqlite3.Error: pass
            raise TrialLedgerError("ledger database unavailable") from exc

    def _require_schema(self):
        expected_events = [("trial_id", "TEXT", 1, 1), ("event_index", "INTEGER", 1, 2), ("alpha_id", "TEXT", 1, 0), ("family_id", "TEXT", 1, 0), ("exact_definition_id", "TEXT", 1, 0), ("validation_policy_digest", "TEXT", 1, 0), ("status", "TEXT", 1, 0), ("at", "INTEGER", 1, 0), ("global_seq", "INTEGER", 1, 0), ("prev_hash", "TEXT", 1, 0), ("event_hash", "TEXT", 1, 0)]
        expected_heads = [("trial_id", "TEXT", 0, 1), ("event_count", "INTEGER", 1, 0), ("head_hash", "TEXT", 1, 0), ("global_count", "INTEGER", 1, 0), ("global_head_hash", "TEXT", 1, 0)]
        names = {r[0] for r in self._c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if not {"trial_events", "trial_heads"} <= names: raise TrialLedgerError("ledger schema missing")
        def info(table):
            return [(r[1], r[2].upper(), r[3], r[5]) for r in self._c.execute(f"PRAGMA table_info({table})").fetchall()]
        if info("trial_events") != expected_events or info("trial_heads") != expected_heads:
            raise TrialLedgerError("ledger table schema mismatch")
        indexes = self._c.execute("PRAGMA index_list(trial_events)").fetchall()
        if not any(r[2] and [x[2] for x in self._c.execute(f"PRAGMA index_info('{r[1]}')").fetchall()] == ["global_seq"] for r in indexes):
            raise TrialLedgerError("global_seq UNIQUE commitment missing")
        triggers = {r[0]: (r[1] or "").lower() for r in self._c.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'").fetchall()}
        for name, action in (("trial_events_no_update", "before update on trial_events"), ("trial_events_no_delete", "before delete on trial_events")):
            sql = triggers.get(name, "")
            if action not in sql or "raise(abort" not in sql: raise TrialLedgerError("append-only trigger schema mismatch")

    @staticmethod
    def _validate_record(r):
        if not isinstance(r, TrialRecord): raise TrialLedgerError("record type required")
        for name in ("trial_id", "alpha_id", "family_id", "exact_definition_id", "validation_policy_digest"):
            if not isinstance(getattr(r,name), str) or not _HEX64.fullmatch(getattr(r,name)): raise TrialLedgerError(f"invalid {name}")
        if not isinstance(r.status, str) or r.status not in _STATES: raise TrialLedgerError("unknown trial status")
        if not isinstance(r.event_index,int) or isinstance(r.event_index,bool) or r.event_index < 0: raise TrialLedgerError("invalid event_index")
        if not isinstance(r.at,int) or isinstance(r.at,bool): raise TrialLedgerError("invalid at")
        try: _canonical(r,0)
        except (TypeError, ValueError, OverflowError) as exc: raise TrialLedgerError("non-canonical record") from exc

    def _global_head(self):
        row = self._c.execute("SELECT global_count,global_head_hash FROM trial_heads WHERE trial_id='' ").fetchone()
        return row if row else (0, "")
    def _rows(self, trial_id=None):
        sql = _SELECT + (" WHERE trial_id=?" if trial_id else "") + " ORDER BY global_seq"
        return self._c.execute(sql, (trial_id,) if trial_id else ()).fetchall()

    def _verify_locked(self):
        rows = self._rows(); global_prev = ""; trial_prev={}; trial_counts={}; identities={}; statuses={}; last_global={}
        for pos,row in enumerate(rows,1):
            tid,idx,alpha,family,exact,policy,status,at,seq,prev,event_hash=row
            if seq != pos or idx != trial_counts.get(tid,0) or prev != trial_prev.get(tid,""): raise TrialLedgerError("trial sequence corruption")
            r=TrialRecord(tid,alpha,family,exact,policy,status,idx,at); self._validate_record(r)
            identity=tuple(getattr(r,n) for n in _IDENTITY)
            if tid in identities and identities[tid] != identity: raise TrialLedgerError("trial identity drift")
            identities[tid]=identity
            prior=statuses.get(tid)
            if prior is None and status != "PLANNED": raise TrialLedgerError("first event must be PLANNED")
            if prior is not None and status not in _TRANSITIONS[prior]: raise TrialLedgerError("illegal trial transition")
            if event_hash != _hash((prev+global_prev).encode()+_canonical(r,seq)): raise TrialLedgerError("event hash mismatch")
            trial_prev[tid]=event_hash; trial_counts[tid]=idx+1; statuses[tid]=status; global_prev=event_hash; last_global[tid]=(seq,event_hash)
        gh=self._global_head()
        if gh != (len(rows),global_prev): raise TrialLedgerError("global commitment mismatch")
        heads=self._c.execute("SELECT trial_id,event_count,head_hash,global_count,global_head_hash FROM trial_heads WHERE trial_id!=''").fetchall()
        if {r[0] for r in heads} != set(trial_counts): raise TrialLedgerError("trial heads mismatch")
        for tid,count,head,gcount,ghead in heads:
            if (count,head) != (trial_counts[tid],trial_prev[tid]) or (gcount,ghead) != last_global[tid]: raise TrialLedgerError("trial commitment mismatch")

    def _aggregate_locked(self, trial_id):
        rows=self._rows(trial_id)
        if not rows: raise TrialLedgerError("unknown trial")
        events=tuple(TrialEvent(tid,alpha,family,exact,policy,status,idx,at,seq,prev,event_hash) for tid,idx,alpha,family,exact,policy,status,at,seq,prev,event_hash in rows)
        first=events[0]; return TrialAggregate(trial_id,first.alpha_id,first.family_id,first.exact_definition_id,first.validation_policy_digest,events)

    def append(self, record: TrialRecord) -> TrialAggregate:
        self._validate_record(record); started=False
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE"); started=True; self._verify_locked()
                prior_rows=self._rows(record.trial_id)
                if prior_rows:
                    first=prior_rows[0]; stored_identity=(first[2],first[3],first[4],first[5]); incoming_identity=tuple(getattr(record,n) for n in _IDENTITY)
                    if incoming_identity != stored_identity: raise TrialLedgerConflict("trial identity drift")
                existing=self._c.execute(_SELECT+" WHERE trial_id=? AND event_index=?",(record.trial_id,record.event_index)).fetchone()
                if existing:
                    stored=TrialRecord(existing[0],existing[2],existing[3],existing[4],existing[5],existing[6],existing[1],existing[7])
                    if stored != record or existing[10] != _hash(_canonical(record,existing[8])): raise TrialLedgerConflict("same trial/event key has different content")
                    aggregate=self._aggregate_locked(record.trial_id); self._c.execute("COMMIT"); return aggregate
                expected=len(prior_rows)
                if record.event_index != expected: raise TrialLedgerError("event_index is not next")
                if not prior_rows and record.status != "PLANNED": raise TrialLedgerError("first event must be PLANNED")
                if prior_rows and record.status not in _TRANSITIONS[prior_rows[-1][6]]: raise TrialLedgerError("illegal state transition")
                count,global_prev=self._global_head(); seq=count+1; prev=prior_rows[-1][10] if prior_rows else ""; event_hash=_hash((prev+global_prev).encode()+_canonical(record,seq))
                self._c.execute("INSERT INTO trial_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",(record.trial_id,record.event_index,record.alpha_id,record.family_id,record.exact_definition_id,record.validation_policy_digest,record.status,record.at,seq,prev,event_hash))
                self._c.execute("INSERT INTO trial_heads VALUES (?,?,?,?,?) ON CONFLICT(trial_id) DO UPDATE SET event_count=excluded.event_count,head_hash=excluded.head_hash,global_count=excluded.global_count,global_head_hash=excluded.global_head_hash",(record.trial_id,expected+1,event_hash,seq,event_hash))
                self._c.execute("INSERT INTO trial_heads VALUES ('',0,'',?,?) ON CONFLICT(trial_id) DO UPDATE SET global_count=excluded.global_count,global_head_hash=excluded.global_head_hash",(seq,event_hash))
                aggregate=self._aggregate_locked(record.trial_id); self._c.execute("COMMIT"); return aggregate
            except TrialLedgerError:
                if started:
                    try:self._c.execute("ROLLBACK")
                    except sqlite3.Error: pass
                raise
            except sqlite3.IntegrityError as exc:
                if started:
                    try:self._c.execute("ROLLBACK")
                    except sqlite3.Error: pass
                raise TrialLedgerConflict("append race or constraint conflict") from exc
            except (sqlite3.Error, OSError) as exc:
                if started:
                    try:self._c.execute("ROLLBACK")
                    except sqlite3.Error: pass
                raise TrialLedgerError("ledger database failure") from exc

    def verify(self) -> bool:
        with self._lock:
            started=False
            try:
                self._c.execute("BEGIN"); started=True; self._verify_locked(); self._c.execute("COMMIT"); return True
            except TrialLedgerError:
                if started:
                    try:self._c.execute("ROLLBACK")
                    except sqlite3.Error: pass
                raise
            except sqlite3.Error as exc:
                if started:
                    try:self._c.execute("ROLLBACK")
                    except sqlite3.Error: pass
                raise TrialLedgerError("ledger database failure") from exc

    def load(self, trial_id: str) -> TrialAggregate:
        if not isinstance(trial_id,str) or not _HEX64.fullmatch(trial_id): raise TrialLedgerError("invalid trial_id")
        with self._lock:
            started=False
            try:
                self._c.execute("BEGIN"); started=True; self._verify_locked(); aggregate=self._aggregate_locked(trial_id); self._c.execute("COMMIT"); return aggregate
            except TrialLedgerError:
                if started:
                    try:self._c.execute("ROLLBACK")
                    except sqlite3.Error: pass
                raise
            except sqlite3.Error as exc:
                if started:
                    try:self._c.execute("ROLLBACK")
                    except sqlite3.Error: pass
                raise TrialLedgerError("ledger database failure") from exc

    def effective_trial_count(self, family_id: str) -> EffectiveTrialCount:
        if not isinstance(family_id,str) or not _HEX64.fullmatch(family_id): raise TrialLedgerError("invalid family_id")
        with self._lock:
            started=False
            try:
                self._c.execute("BEGIN"); started=True; self._verify_locked()
                rows=self._c.execute(_SELECT+" WHERE event_index=0 ORDER BY global_seq").fetchall(); seen=set(); selected=[]
                for row in rows:
                    if row[3] != family_id: continue
                    agg=self._aggregate_locked(row[0])
                    if agg.status == "PLANNED": continue
                    key=(agg.exact_definition_id,agg.validation_policy_digest)
                    if key not in seen: seen.add(key); selected.append(row[0])
                self._c.execute("COMMIT"); return EffectiveTrialCount(len(selected),tuple(selected))
            except TrialLedgerError:
                if started:
                    try:self._c.execute("ROLLBACK")
                    except sqlite3.Error: pass
                raise
            except sqlite3.Error as exc:
                if started:
                    try:self._c.execute("ROLLBACK")
                    except sqlite3.Error: pass
                raise TrialLedgerError("ledger database failure") from exc
