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
_RECEIPT_STAGES = ("cheap", "oos", "realism")
_ARTIFACT_STAGE = "__artifact__"
_RECEIPT_STATES = {"CLAIMED", "DONE", "BLOCKED", "FAILED"}


class TrialLedgerError(RuntimeError):
    pass


class TrialLedgerConflict(TrialLedgerError):
    pass


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


@dataclass(frozen=True)
class ValidationClaim:
    state: str
    attempt: int


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trial_events (trial_id TEXT NOT NULL,event_index INTEGER NOT NULL,alpha_id TEXT NOT NULL,family_id TEXT NOT NULL,exact_definition_id TEXT NOT NULL,validation_policy_digest TEXT NOT NULL,status TEXT NOT NULL,at INTEGER NOT NULL,global_seq INTEGER NOT NULL UNIQUE,prev_hash TEXT NOT NULL,event_hash TEXT NOT NULL,PRIMARY KEY(trial_id,event_index));
CREATE TABLE IF NOT EXISTS trial_heads (trial_id TEXT PRIMARY KEY,event_count INTEGER NOT NULL,head_hash TEXT NOT NULL,global_count INTEGER NOT NULL,global_head_hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS trial_events_no_update BEFORE UPDATE ON trial_events BEGIN SELECT RAISE(ABORT,'trial_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS trial_events_no_delete BEFORE DELETE ON trial_events BEGIN SELECT RAISE(ABORT,'trial_events is append-only'); END;
CREATE TABLE IF NOT EXISTS validation_receipt_events (trial_id TEXT NOT NULL,stage TEXT NOT NULL,event_index INTEGER NOT NULL,state TEXT NOT NULL,payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,global_seq INTEGER NOT NULL UNIQUE,prev_hash TEXT NOT NULL,row_hash TEXT NOT NULL,owner TEXT NOT NULL,lease_until INTEGER NOT NULL,attempt INTEGER NOT NULL,PRIMARY KEY(trial_id,stage,event_index));
CREATE TABLE IF NOT EXISTS validation_receipt_heads (id INTEGER PRIMARY KEY CHECK(id=1),event_count INTEGER NOT NULL,head_hash TEXT NOT NULL);
INSERT OR IGNORE INTO validation_receipt_heads(id,event_count,head_hash) VALUES(1,0,'');
CREATE TRIGGER IF NOT EXISTS validation_receipts_no_update BEFORE UPDATE ON validation_receipt_events BEGIN SELECT RAISE(ABORT,'validation receipts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS validation_receipts_no_delete BEFORE DELETE ON validation_receipt_events BEGIN SELECT RAISE(ABORT,'validation receipts are append-only'); END;
"""
_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS trial_events (trial_id TEXT NOT NULL,event_index INTEGER NOT NULL,alpha_id TEXT NOT NULL,family_id TEXT NOT NULL,exact_definition_id TEXT NOT NULL,validation_policy_digest TEXT NOT NULL,status TEXT NOT NULL,at INTEGER NOT NULL,global_seq INTEGER NOT NULL UNIQUE,prev_hash TEXT NOT NULL,event_hash TEXT NOT NULL,PRIMARY KEY(trial_id,event_index))",
    "CREATE TABLE IF NOT EXISTS trial_heads (trial_id TEXT PRIMARY KEY,event_count INTEGER NOT NULL,head_hash TEXT NOT NULL,global_count INTEGER NOT NULL,global_head_hash TEXT NOT NULL)",
    "CREATE TRIGGER IF NOT EXISTS trial_events_no_update BEFORE UPDATE ON trial_events BEGIN SELECT RAISE(ABORT,'trial_events is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS trial_events_no_delete BEFORE DELETE ON trial_events BEGIN SELECT RAISE(ABORT,'trial_events is append-only'); END",
    "CREATE TABLE IF NOT EXISTS validation_receipt_events (trial_id TEXT NOT NULL,stage TEXT NOT NULL,event_index INTEGER NOT NULL,state TEXT NOT NULL,payload_json TEXT NOT NULL,payload_hash TEXT NOT NULL,global_seq INTEGER NOT NULL UNIQUE,prev_hash TEXT NOT NULL,row_hash TEXT NOT NULL,owner TEXT NOT NULL,lease_until INTEGER NOT NULL,attempt INTEGER NOT NULL,PRIMARY KEY(trial_id,stage,event_index))",
    "CREATE TABLE IF NOT EXISTS validation_receipt_heads (id INTEGER PRIMARY KEY CHECK(id=1),event_count INTEGER NOT NULL,head_hash TEXT NOT NULL)",
    "INSERT OR IGNORE INTO validation_receipt_heads(id,event_count,head_hash) VALUES(1,0,'')",
    "CREATE TRIGGER IF NOT EXISTS validation_receipts_no_update BEFORE UPDATE ON validation_receipt_events BEGIN SELECT RAISE(ABORT,'validation receipts are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS validation_receipts_no_delete BEFORE DELETE ON validation_receipt_events BEGIN SELECT RAISE(ABORT,'validation receipts are append-only'); END",
)
_SELECT = "SELECT trial_id,event_index,alpha_id,family_id,exact_definition_id,validation_policy_digest,status,at,global_seq,prev_hash,event_hash FROM trial_events"


def _hash(data: bytes) -> str: return hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> str:
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value): raise TrialLedgerError("canonical mapping keys must be strings")
        value = {k: _canonical_value(v) for k, v in value.items()}
    else: value = _canonical_value(value)
    try: return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc: raise TrialLedgerError("non-canonical JSON") from exc


def _canonical_value(value):
    if isinstance(value, Mapping):
        if any(type(k) is not str for k in value): raise TrialLedgerError("canonical mapping keys must be strings")
        return {k: _canonical_value(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)): return [_canonical_value(v) for v in value]
    return value


def _record_bytes(record: TrialRecord, seq: int) -> bytes:
    return _canonical({"trial_id":record.trial_id,"alpha_id":record.alpha_id,"family_id":record.family_id,"exact_definition_id":record.exact_definition_id,"validation_policy_digest":record.validation_policy_digest,"status":record.status,"event_index":record.event_index,"at":record.at,"global_seq":seq}).encode()


def _strict_json(raw: str) -> Any:
    """Parse only one canonical JSON representation (including duplicate-key rejection)."""
    if not isinstance(raw, str):
        raise TrialLedgerError("receipt payload must be text")
    def pairs(items):
        out = {}
        local: set[str] = set()
        for key, value in items:
            if key in local:
                raise TrialLedgerError("duplicate JSON key")
            local.add(key)
            out[key] = value
        return out
    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise TrialLedgerError("validation receipt JSON invalid") from exc
    if _canonical(value) != raw:
        raise TrialLedgerError("non-canonical receipt JSON")
    return value



def _preflight_existing_schema(c: sqlite3.Connection) -> None:
    names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not ({"trial_events","trial_heads"} & names): return
    expected={"trial_events": ["trial_id","event_index","alpha_id","family_id","exact_definition_id","validation_policy_digest","status","at","global_seq","prev_hash","event_hash"], "trial_heads": ["trial_id","event_count","head_hash","global_count","global_head_hash"]}
    for table, cols in expected.items():
        if table not in names: raise TrialLedgerError("legacy ledger schema incomplete")
        actual=[r[1] for r in c.execute(f"PRAGMA table_info({table})")]
        if actual != cols: raise TrialLedgerError("legacy ledger schema malformed")
    receipt_expected={
        "validation_receipt_events": ["trial_id","stage","event_index","state","payload_json","payload_hash","global_seq","prev_hash","row_hash","owner","lease_until","attempt"],
        "validation_receipt_heads": ["id","event_count","head_hash"],
    }
    receipt_present=set(receipt_expected) & names
    if receipt_present and receipt_present != set(receipt_expected): raise TrialLedgerError("receipt ledger schema incomplete")
    for table, cols in receipt_expected.items():
        if table in names and [r[1] for r in c.execute(f"PRAGMA table_info({table})")] != cols: raise TrialLedgerError("receipt ledger schema malformed")
    triggers={r[0]: " ".join((r[1] or "").lower().split()) for r in c.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")}
    expected_triggers={
        "trial_events_no_update": "create trigger trial_events_no_update before update on trial_events begin select raise(abort,'trial_events is append-only'); end",
        "trial_events_no_delete": "create trigger trial_events_no_delete before delete on trial_events begin select raise(abort,'trial_events is append-only'); end",
        "validation_receipts_no_update": "create trigger validation_receipts_no_update before update on validation_receipt_events begin select raise(abort,'validation receipts are append-only'); end",
        "validation_receipts_no_delete": "create trigger validation_receipts_no_delete before delete on validation_receipt_events begin select raise(abort,'validation receipts are append-only'); end",
    }
    for name, expected in expected_triggers.items():
        if name in triggers and triggers[name] != expected: raise TrialLedgerError("legacy ledger trigger malformed")

class TrialLedger:
    def __init__(self, db_path: str, create: bool = True):
        if not isinstance(db_path, str): raise TrialLedgerError("db_path must be a string")
        if not create and db_path != ":memory:" and not os.path.isfile(db_path): raise TrialLedgerError("ledger database does not exist")
        self._c = None
        try:
            if create and db_path != ":memory:":
                parent = os.path.dirname(db_path)
                if parent: os.makedirs(parent, exist_ok=True)
            self._c = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
            self._c.isolation_level = None
            self._c.execute("PRAGMA busy_timeout=30000")
            self._lock = threading.RLock()
            # Validate legacy/current core objects before additive schema creation.
            with self._lock:
                _preflight_existing_schema(self._c)
                if not create:
                    self._require_schema()
                else:
                    try:
                        self._c.execute("BEGIN IMMEDIATE")
                        for statement in _SCHEMA_STATEMENTS:
                            self._c.execute(statement)
                        self._require_schema()
                        self._c.execute("COMMIT")
                    except Exception:
                        try: self._c.execute("ROLLBACK")
                        except sqlite3.Error: pass
                        raise
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
        names = {r[0] for r in self._c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"trial_events","trial_heads","validation_receipt_events","validation_receipt_heads"} <= names: raise TrialLedgerError("ledger schema missing")
        expected = {
            "trial_events": [("trial_id","TEXT",1,1),("event_index","INTEGER",1,2),("alpha_id","TEXT",1,0),("family_id","TEXT",1,0),("exact_definition_id","TEXT",1,0),("validation_policy_digest","TEXT",1,0),("status","TEXT",1,0),("at","INTEGER",1,0),("global_seq","INTEGER",1,0),("prev_hash","TEXT",1,0),("event_hash","TEXT",1,0)],
            "trial_heads": [("trial_id","TEXT",0,1),("event_count","INTEGER",1,0),("head_hash","TEXT",1,0),("global_count","INTEGER",1,0),("global_head_hash","TEXT",1,0)],
            "validation_receipt_events": [("trial_id","TEXT",1,1),("stage","TEXT",1,2),("event_index","INTEGER",1,3),("state","TEXT",1,0),("payload_json","TEXT",1,0),("payload_hash","TEXT",1,0),("global_seq","INTEGER",1,0),("prev_hash","TEXT",1,0),("row_hash","TEXT",1,0),("owner","TEXT",1,0),("lease_until","INTEGER",1,0),("attempt","INTEGER",1,0)],
            "validation_receipt_heads": [("id","INTEGER",0,1),("event_count","INTEGER",1,0),("head_hash","TEXT",1,0)],
        }
        for table, shape in expected.items():
            actual=[(r[1],r[2].upper(),r[3],r[5]) for r in self._c.execute(f"PRAGMA table_info({table})")]
            if actual != shape: raise TrialLedgerError("ledger table schema mismatch")
        triggers = {r[0]: " ".join((r[1] or "").lower().split()) for r in self._c.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'")}
        expected_sql = {
            "trial_events_no_update": "create trigger trial_events_no_update before update on trial_events begin select raise(abort,'trial_events is append-only'); end",
            "trial_events_no_delete": "create trigger trial_events_no_delete before delete on trial_events begin select raise(abort,'trial_events is append-only'); end",
            "validation_receipts_no_update": "create trigger validation_receipts_no_update before update on validation_receipt_events begin select raise(abort,'validation receipts are append-only'); end",
            "validation_receipts_no_delete": "create trigger validation_receipts_no_delete before delete on validation_receipt_events begin select raise(abort,'validation receipts are append-only'); end",
        }
        for name, sql in expected_sql.items():
            if triggers.get(name) != sql: raise TrialLedgerError("append-only trigger schema mismatch")
        indexes = self._c.execute("PRAGMA index_list(trial_events)").fetchall()
        if not any(r[2] and [x[2] for x in self._c.execute(f"PRAGMA index_info('{r[1]}')").fetchall()] == ["global_seq"] for r in indexes): raise TrialLedgerError("global sequence commitment missing")

    @staticmethod
    def _validate_record(r):
        if not isinstance(r, TrialRecord): raise TrialLedgerError("record type required")
        for name in _IDENTITY + ("trial_id",):
            if not isinstance(getattr(r,name), str) or not _HEX64.fullmatch(getattr(r,name)): raise TrialLedgerError(f"invalid {name}")
        if not isinstance(r.status, str) or r.status not in _STATES: raise TrialLedgerError("unknown trial status")
        if not isinstance(r.event_index,int) or isinstance(r.event_index,bool) or r.event_index < 0: raise TrialLedgerError("invalid event_index")
        if not isinstance(r.at,int) or isinstance(r.at,bool): raise TrialLedgerError("invalid at")

    def close(self):
        with getattr(self, "_lock", threading.RLock()):
            if self._c is not None:
                try: self._c.close()
                except sqlite3.Error: pass
                self._c = None

    def __enter__(self): return self
    def __exit__(self, exc_type, exc, tb): self.close(); return False

    def _global_head(self):
        row = self._c.execute("SELECT global_count,global_head_hash FROM trial_heads WHERE trial_id='' ").fetchone()
        return row if row else (0, "")

    def _rows(self, trial_id=None):
        return self._c.execute(_SELECT + (" WHERE trial_id=?" if trial_id else "") + " ORDER BY global_seq", (trial_id,) if trial_id else ()).fetchall()

    def _verify_locked(self):
        rows=self._rows(); global_prev=""; trial_prev={}; counts={}; identities={}; statuses={}; last={}
        for pos,row in enumerate(rows,1):
            tid,idx,alpha,family,exact,policy,status,at,seq,prev,event_hash=row
            if seq != pos or idx != counts.get(tid,0) or prev != trial_prev.get(tid,""): raise TrialLedgerError("trial sequence corruption")
            record=TrialRecord(tid,alpha,family,exact,policy,status,idx,at); self._validate_record(record)
            identity=tuple(getattr(record,n) for n in _IDENTITY)
            if tid in identities and identities[tid] != identity: raise TrialLedgerError("trial identity drift")
            identities[tid]=identity; prior=statuses.get(tid)
            if prior is None and status != "PLANNED": raise TrialLedgerError("first event must be PLANNED")
            if prior is not None and status not in _TRANSITIONS[prior]: raise TrialLedgerError("illegal trial transition")
            if event_hash != _hash((prev+global_prev).encode()+_record_bytes(record,seq)): raise TrialLedgerError("event hash mismatch")
            trial_prev[tid]=event_hash; counts[tid]=idx+1; statuses[tid]=status; global_prev=event_hash; last[tid]=(seq,event_hash)
        if self._global_head() != (len(rows),global_prev): raise TrialLedgerError("global commitment mismatch")
        heads=self._c.execute("SELECT trial_id,event_count,head_hash,global_count,global_head_hash FROM trial_heads WHERE trial_id!=''").fetchall()
        if {r[0] for r in heads} != set(counts): raise TrialLedgerError("trial heads mismatch")
        for tid,count,head,gcount,ghead in heads:
            if (count,head)!=(counts[tid],trial_prev[tid]) or (gcount,ghead)!=last[tid]: raise TrialLedgerError("trial commitment mismatch")

    def _aggregate_locked(self, trial_id):
        rows=self._rows(trial_id)
        if not rows: raise TrialLedgerError("unknown trial")
        events = tuple(TrialEvent(tid, alpha, family, exact, policy, status, idx, at, seq, prev, event_hash) for tid, idx, alpha, family, exact, policy, status, at, seq, prev, event_hash in rows)
        return TrialAggregate(trial_id, events[0].alpha_id, events[0].family_id, events[0].exact_definition_id, events[0].validation_policy_digest, events)

    def _append_locked(self, record: TrialRecord) -> TrialAggregate:
        prior=self._rows(record.trial_id)
        if prior:
            identity=tuple(prior[0][i] for i in (2,3,4,5))
            if tuple(getattr(record,n) for n in _IDENTITY) != identity: raise TrialLedgerConflict("trial identity drift")
        existing=self._c.execute(_SELECT+" WHERE trial_id=? AND event_index=?",(record.trial_id,record.event_index)).fetchone()
        if existing:
            stored=TrialRecord(existing[0],existing[2],existing[3],existing[4],existing[5],existing[6],existing[1],existing[7])
            if stored != record: raise TrialLedgerConflict("same trial/event key has different content")
            return self._aggregate_locked(record.trial_id)
        if record.event_index != len(prior): raise TrialLedgerError("event_index is not next")
        if (not prior and record.status != "PLANNED") or (prior and record.status not in _TRANSITIONS[prior[-1][6]]): raise TrialLedgerError("illegal state transition")
        count,head=self._global_head(); seq=count+1; prev=prior[-1][10] if prior else ""; digest=_hash((prev+head).encode()+_record_bytes(record,seq))
        self._c.execute("INSERT INTO trial_events VALUES (?,?,?,?,?,?,?,?,?,?,?)",(record.trial_id,record.event_index,record.alpha_id,record.family_id,record.exact_definition_id,record.validation_policy_digest,record.status,record.at,seq,prev,digest))
        self._c.execute("INSERT INTO trial_heads VALUES (?,?,?,?,?) ON CONFLICT(trial_id) DO UPDATE SET event_count=excluded.event_count,head_hash=excluded.head_hash,global_count=excluded.global_count,global_head_hash=excluded.global_head_hash",(record.trial_id,len(prior)+1,digest,seq,digest))
        self._c.execute("INSERT INTO trial_heads VALUES ('',0,'',?,?) ON CONFLICT(trial_id) DO UPDATE SET global_count=excluded.global_count,global_head_hash=excluded.global_head_hash",(seq,digest))
        return self._aggregate_locked(record.trial_id)

    def append(self, record: TrialRecord):
        self._validate_record(record)
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE"); self._verify_locked(); result=self._append_locked(record); self._c.execute("COMMIT"); return result
            except TrialLedgerError:
                try:self._c.execute("ROLLBACK")
                except sqlite3.Error:pass
                raise
            except sqlite3.IntegrityError as exc:
                try:self._c.execute("ROLLBACK")
                except sqlite3.Error:pass
                raise TrialLedgerConflict("append race or constraint conflict") from exc
            except (sqlite3.Error,OSError) as exc:
                try:self._c.execute("ROLLBACK")
                except sqlite3.Error:pass
                raise TrialLedgerError("ledger database failure") from exc

    def _receipt_rows_locked(self, trial_id):
        rows=self._c.execute("SELECT stage,event_index,state,payload_json,payload_hash,global_seq,prev_hash,row_hash,owner,lease_until,attempt FROM validation_receipt_events WHERE trial_id=? ORDER BY global_seq",(trial_id,)).fetchall(); out={}; previous={}
        for stage,index,state,payload_json,payload_hash,seq,prev,row_hash,owner,lease_until,attempt in rows:
            if stage not in _RECEIPT_STAGES and stage != _ARTIFACT_STAGE: raise TrialLedgerError("invalid receipt stage")
            if _hash(payload_json.encode()) != payload_hash: raise TrialLedgerError("validation receipt hash mismatch")
            payload = _strict_json(payload_json)
            if not isinstance(payload,dict) or index != previous.get(stage,-1)+1: raise TrialLedgerError("validation receipt sequence corruption")
            previous[stage]=index
            if state not in _RECEIPT_STATES: raise TrialLedgerError("validation receipt state invalid")
            if state == "CLAIMED":
                if set(payload) != {"stage","attempt","panel_digest","policy_digest"} or payload.get("stage") != stage or type(payload.get("attempt")) is not int or payload.get("attempt") < 1: raise TrialLedgerError("claimed receipt schema invalid")
            elif stage in _RECEIPT_STAGES:
                required={"trial_id","panel_digest","policy_digest","event_at","effective_trial_count","trial_set_digest","evidence_hash","stage","version","implementation_id","outcome","evidence","reason"}
                if set(payload) != required or payload.get("trial_id") != trial_id or type(payload.get("event_at")) is not int or payload.get("event_at") != self._aggregate_locked(trial_id).events[0].at or type(payload.get("effective_trial_count")) is not int or payload.get("effective_trial_count") < 0 or type(payload.get("trial_set_digest")) is not str or not _HEX64.fullmatch(payload.get("trial_set_digest")) or payload.get("stage") != stage or payload.get("outcome") not in {"PASS","BLOCK"} or any(type(payload.get(k)) is not str or not payload.get(k) for k in ("version","implementation_id")) or (payload.get("reason") is not None and type(payload.get("reason")) is not str) or any(type(payload.get(k)) is not str or not _HEX64.fullmatch(payload.get(k)) for k in ("trial_id","panel_digest","policy_digest","evidence_hash")): raise TrialLedgerError("terminal receipt schema invalid")
                expected_evidence=_hash(_canonical({"stage":payload["stage"],"version":payload["version"],"implementation_id":payload["implementation_id"],"outcome":payload["outcome"],"evidence":payload["evidence"],"reason":payload["reason"]}).encode())
                if payload["evidence_hash"] != expected_evidence: raise TrialLedgerError("terminal receipt evidence hash invalid")
                if (state == "DONE" and payload["outcome"] != "PASS") or (state in {"BLOCKED","FAILED"} and payload["outcome"] != "BLOCK"): raise TrialLedgerError("receipt state/outcome mismatch")
            expected=_hash(_canonical({"trial_id":trial_id,"stage":stage,"event_index":index,"state":state,"payload_hash":payload_hash,"global_seq":seq,"prev_hash":prev,"owner":owner,"lease_until":lease_until,"attempt":attempt}).encode())
            if expected != row_hash: raise TrialLedgerError("validation receipt row hash mismatch")
            out[stage]={"event_index":index,"state":state,"payload":payload,"payload_hash":payload_hash,"owner":owner,"lease_until":lease_until,"attempt":attempt,"global_seq":seq,"row_hash":row_hash}
        return out

    def _verify_receipts_locked(self):
        trial_ids={r[0] for r in self._c.execute("SELECT DISTINCT trial_id FROM validation_receipt_events")}
        valid_trials={r[0] for r in self._c.execute("SELECT trial_id FROM trial_heads WHERE trial_id!=''")}
        if not trial_ids <= valid_trials: raise TrialLedgerError("phantom validation receipt")
        rows=self._c.execute("SELECT trial_id,stage,event_index,state,payload_json,payload_hash,global_seq,prev_hash,row_hash,owner,lease_until,attempt FROM validation_receipt_events ORDER BY global_seq").fetchall(); previous=""; heads=self._c.execute("SELECT event_count,head_hash FROM validation_receipt_heads WHERE id=1").fetchone(); ordered_trials=[]; seen_trials=set()
        for pos,row in enumerate(rows,1):
            tid,stage,index,state,payload_json,payload_hash,seq,prev,row_hash,owner,lease_until,attempt=row
            if seq != pos or prev != previous: raise TrialLedgerError("receipt global chain corruption")
            if tid not in seen_trials: seen_trials.add(tid); ordered_trials.append(tid)
            previous=row_hash
        for tid in ordered_trials: self._receipt_rows_locked(tid)
        if heads != (len(rows),previous): raise TrialLedgerError("receipt head commitment mismatch")

    def _validate_receipt_identity(self, trial_id, stage):
        if not isinstance(trial_id,str) or not _HEX64.fullmatch(trial_id) or stage not in _RECEIPT_STAGES: raise TrialLedgerError("invalid validation receipt identity")

    def _trial_exists_locked(self, trial_id): return self._c.execute("SELECT 1 FROM trial_heads WHERE trial_id=?",(trial_id,)).fetchone() is not None

    def _receipt_insert_locked(self, trial_id, stage, state, payload, owner, lease_until, attempt):
        encoded=_canonical(payload); payload_hash=_hash(encoded.encode()); count,prev=self._c.execute("SELECT event_count,head_hash FROM validation_receipt_heads WHERE id=1").fetchone(); seq=count+1
        latest=self._c.execute("SELECT max(event_index) FROM validation_receipt_events WHERE trial_id=? AND stage=?",(trial_id,stage)).fetchone()[0]
        event_index=0 if latest is None else latest+1
        row_hash=_hash(_canonical({"trial_id":trial_id,"stage":stage,"event_index":event_index,"state":state,"payload_hash":payload_hash,"global_seq":seq,"prev_hash":prev,"owner":owner,"lease_until":lease_until,"attempt":attempt}).encode())
        self._c.execute("INSERT INTO validation_receipt_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(trial_id,stage,event_index,state,encoded,payload_hash,seq,prev,row_hash,owner,lease_until,attempt))
        self._c.execute("UPDATE validation_receipt_heads SET event_count=?,head_hash=? WHERE id=1",(seq,row_hash))

    def claim_validation_stage(self, trial_id: str, stage: str, *, owner: str = "legacy", now: int = 0, lease_seconds: int = 60, panel_digest: str, policy_digest: str) -> ValidationClaim:
        self._validate_receipt_identity(trial_id,stage)
        if not isinstance(owner,str) or not owner or not isinstance(now,int) or isinstance(now,bool) or not isinstance(lease_seconds,int) or lease_seconds <= 0 or type(panel_digest) is not str or not _HEX64.fullmatch(panel_digest) or type(policy_digest) is not str or not _HEX64.fullmatch(policy_digest): raise TrialLedgerError("invalid receipt lease or execution binding")
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE"); self._verify_locked(); self._verify_receipts_locked()
                if not self._trial_exists_locked(trial_id): raise TrialLedgerError("validation receipt requires trial")
                current=self._receipt_rows_locked(trial_id).get(stage)
                if current and current["state"] in {"DONE", "BLOCKED", "FAILED"}: self._c.execute("COMMIT"); return ValidationClaim(current["state"],current["attempt"])
                if self._aggregate_locked(trial_id).status != "RUNNING": raise TrialLedgerError("validation receipt requires RUNNING trial")
                if current and current["state"] == "CLAIMED" and current["lease_until"] > now: self._c.execute("COMMIT"); return ValidationClaim("IN_PROGRESS",current["attempt"])
                attempt=(current["attempt"]+1) if current else 1
                self._receipt_insert_locked(trial_id,stage,"CLAIMED",{"stage":stage,"attempt":attempt,"panel_digest":panel_digest,"policy_digest":policy_digest},owner,now+lease_seconds,attempt); self._c.execute("COMMIT"); return ValidationClaim("CLAIMED",attempt)
            except (TrialLedgerError,sqlite3.Error) as exc:
                try:self._c.execute("ROLLBACK")
                except sqlite3.Error:pass
                if isinstance(exc,TrialLedgerError): raise
                raise TrialLedgerError("ledger database failure") from exc

    def complete_validation_stage(self, trial_id, stage, state, payload, *, owner="legacy", now=0, attempt: int):
        self._validate_receipt_identity(trial_id,stage)
        if state not in {"DONE","BLOCKED","FAILED"} or not isinstance(owner,str) or not isinstance(now,int) or type(attempt) is not int or attempt < 1: raise TrialLedgerError("invalid receipt completion")
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE"); self._verify_locked(); self._verify_receipts_locked();
                if self._aggregate_locked(trial_id).status != "RUNNING": raise TrialLedgerError("validation receipt requires RUNNING trial")
                current=self._receipt_rows_locked(trial_id).get(stage)
                if not current or current["state"] != "CLAIMED" or current["owner"] != owner or current["lease_until"] <= now or current["attempt"] != attempt: raise TrialLedgerConflict("receipt claim ownership lost")
                if not isinstance(payload,Mapping) or set(payload) != {"trial_id","panel_digest","policy_digest","event_at","effective_trial_count","trial_set_digest","evidence_hash","stage","version","implementation_id","outcome","evidence","reason"}: raise TrialLedgerError("terminal receipt payload schema invalid")
                if payload["trial_id"] != trial_id or type(payload["event_at"]) is not int or payload["event_at"] != self._aggregate_locked(trial_id).events[0].at or type(payload["effective_trial_count"]) is not int or payload["effective_trial_count"] < 0 or type(payload["trial_set_digest"]) is not str or not _HEX64.fullmatch(payload["trial_set_digest"]) or payload["stage"] != stage or payload["outcome"] not in {"PASS","BLOCK"}: raise TrialLedgerError("terminal receipt binding invalid")
                if not all(type(payload[k]) is str and _HEX64.fullmatch(payload[k]) for k in ("trial_id","panel_digest","policy_digest","evidence_hash")): raise TrialLedgerError("terminal receipt identity invalid")
                if not all(type(payload[k]) is str and payload[k] for k in ("version","implementation_id")): raise TrialLedgerError("terminal receipt stage identity invalid")
                if current["payload"].get("panel_digest") != payload["panel_digest"] or current["payload"].get("policy_digest") != payload["policy_digest"]: raise TrialLedgerError("terminal receipt execution binding mismatch")
                expected=_hash(_canonical({"stage":payload["stage"],"version":payload["version"],"implementation_id":payload["implementation_id"],"outcome":payload["outcome"],"evidence":payload["evidence"],"reason":payload["reason"]}).encode())
                if payload["evidence_hash"] != expected: raise TrialLedgerError("terminal receipt evidence hash invalid")
                if (state == "DONE" and payload["outcome"] != "PASS") or (state in {"BLOCKED","FAILED"} and payload["outcome"] != "BLOCK"): raise TrialLedgerError("receipt state/outcome mismatch")
                self._receipt_insert_locked(trial_id,stage,state,payload,owner,current["lease_until"],attempt); self._c.execute("COMMIT")
            except (TrialLedgerError,sqlite3.Error) as exc:
                try:self._c.execute("ROLLBACK")
                except sqlite3.Error:pass
                if isinstance(exc,TrialLedgerError): raise
                raise TrialLedgerError("ledger database failure") from exc

    def reopen_validation_stage(self, trial_id, stage, *, owner="legacy", now=0, lease_seconds=60, panel_digest, policy_digest):
        return self.claim_validation_stage(trial_id,stage,owner=owner,now=now,lease_seconds=lease_seconds,panel_digest=panel_digest,policy_digest=policy_digest)
    def recover_validation_stage(self, trial_id, stage, *, owner="recovery", now=0, lease_seconds=60, panel_digest, policy_digest):
        return self.claim_validation_stage(trial_id,stage,owner=owner,now=now,lease_seconds=lease_seconds,panel_digest=panel_digest,policy_digest=policy_digest)

    def _validate_artifact_payload(self, trial_id: str, payload: Mapping[str, Any]) -> None:
        if not isinstance(payload, Mapping): raise TrialLedgerError("artifact must be mapping")
        aggregate=self._aggregate_locked(trial_id)
        required={"trial_id","alpha_id","family_id","exact_definition_id","panel_digest","policy_digest","event_at","effective_trial_ids","trial_set_digest","stage_evidence_hashes","stage_receipt_bindings","effective_trial_count","verdict","reason","stage_metadata","artifact_commitment"}
        if set(payload) != required or payload["trial_id"] != trial_id: raise TrialLedgerError("artifact identity mismatch")
        if any(payload[k] != getattr(aggregate, k if k != "policy_digest" else "validation_policy_digest") for k in ("alpha_id","family_id","exact_definition_id")): raise TrialLedgerError("artifact identity mismatch")
        if payload["policy_digest"] != aggregate.validation_policy_digest or type(payload["event_at"]) is not int or payload["event_at"] != aggregate.events[0].at or payload["verdict"] not in {"PASS","BLOCK"}: raise TrialLedgerError("artifact identity mismatch")
        if type(payload["effective_trial_count"]) is not int or payload["effective_trial_count"] < 0: raise TrialLedgerError("invalid artifact trial count")
        trial_ids=payload["effective_trial_ids"]
        if not isinstance(trial_ids,(tuple,list)) or len(trial_ids) != payload["effective_trial_count"] or any(type(v) is not str or not _HEX64.fullmatch(v) for v in trial_ids) or payload["trial_set_digest"] != _hash(_canonical(list(trial_ids)).encode()): raise TrialLedgerError("artifact trial-set commitment mismatch")
        metadata=payload["stage_metadata"]
        if not isinstance(metadata,Mapping) or set(metadata) != set(_RECEIPT_STAGES): raise TrialLedgerError("artifact stage metadata incomplete")
        hashes=payload["stage_evidence_hashes"]; bindings=payload["stage_receipt_bindings"]
        if not isinstance(bindings,Mapping) or set(bindings) != set(_RECEIPT_STAGES): raise TrialLedgerError("artifact receipt bindings incomplete")
        if not isinstance(hashes,Mapping) or set(hashes) != set(_RECEIPT_STAGES) or any(type(v) is not str or not _HEX64.fullmatch(v) for v in hashes.values()): raise TrialLedgerError("artifact stage hashes incomplete")
        receipts=self._receipt_rows_locked(trial_id)
        for stage in _RECEIPT_STAGES:
            m=metadata[stage]; rp=receipts.get(stage,{}).get("payload")
            if not isinstance(m,Mapping) or set(m) != {"stage","version","implementation_id"}: raise TrialLedgerError("artifact stage identity mismatch")
            if not isinstance(rp,Mapping) or receipts[stage]["state"] not in {"DONE","BLOCKED","FAILED"}: raise TrialLedgerError("artifact receipt binding mismatch")
            b=bindings[stage]
            if not isinstance(b,Mapping) or set(b) != {"global_seq","row_hash"} or type(b["global_seq"]) is not int or not _HEX64.fullmatch(str(b["row_hash"])) or b["global_seq"] != receipts[stage]["global_seq"] or b["row_hash"] != self._c.execute("SELECT row_hash FROM validation_receipt_events WHERE trial_id=? AND stage=? AND global_seq=?",(trial_id,stage,b["global_seq"])).fetchone()[0]: raise TrialLedgerError("artifact receipt row binding mismatch")
            if rp.get("trial_id") != trial_id or type(rp.get("event_at")) is not int or rp.get("event_at") != aggregate.events[0].at or rp.get("effective_trial_count") != payload["effective_trial_count"] or rp.get("trial_set_digest") != payload["trial_set_digest"] or rp.get("panel_digest") != payload["panel_digest"] or rp.get("policy_digest") != payload["policy_digest"] or not _HEX64.fullmatch(str(rp.get("panel_digest",""))) or not _HEX64.fullmatch(str(rp.get("policy_digest",""))) or any(m[k] != rp.get(k) for k in ("stage","version","implementation_id")) or rp.get("evidence_hash") != hashes[stage]: raise TrialLedgerError("artifact stage identity mismatch")
            if set(rp) != {"trial_id","panel_digest","policy_digest","event_at","effective_trial_count","trial_set_digest","evidence_hash","stage","version","implementation_id","outcome","evidence","reason"}: raise TrialLedgerError("artifact receipt evidence malformed")
            expected_evidence=_hash(_canonical({"stage":rp["stage"],"version":rp["version"],"implementation_id":rp["implementation_id"],"outcome":rp["outcome"],"evidence":rp["evidence"],"reason":rp["reason"]}).encode())
            if rp["evidence_hash"] != expected_evidence: raise TrialLedgerError("artifact evidence self-hash mismatch")
        expected_verdict="PASS" if aggregate.status == "PASSED" and all(receipts[x]["payload"].get("outcome") == "PASS" for x in _RECEIPT_STAGES) else "BLOCK"
        if payload["verdict"] != expected_verdict: raise TrialLedgerError("artifact final verdict mismatch")
        commitment=dict(payload); supplied=commitment.pop("artifact_commitment",None)
        if type(supplied) is not str or not _HEX64.fullmatch(supplied) or supplied != _hash(_canonical(commitment).encode()): raise TrialLedgerError("artifact commitment mismatch")

    def publish_validation_terminal(self, record: TrialRecord, payload: Mapping[str, Any]) -> None:
        self._validate_record(record)
        if record.status not in {"PASSED", "FAILED"}:
            raise TrialLedgerError("validation terminal status required")
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE")
                self._verify_locked()
                self._verify_receipts_locked()
                current=self._aggregate_locked(record.trial_id)
                if current.status == "RUNNING":
                    self._append_locked(record)
                elif current.status != record.status:
                    raise TrialLedgerConflict("validation terminal status conflict")
                self._validate_artifact_payload(record.trial_id,payload)
                existing=self._receipt_rows_locked(record.trial_id).get(_ARTIFACT_STAGE)
                encoded=_canonical(payload); digest=_hash(encoded.encode())
                if existing:
                    if existing["payload_hash"] != digest: raise TrialLedgerConflict("validation artifact conflict")
                else:
                    self._receipt_insert_locked(record.trial_id,_ARTIFACT_STAGE,"DONE",payload,"artifact",0,1)
                self._c.execute("COMMIT")
            except TrialLedgerConflict:
                try:self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                raise
            except (TrialLedgerError,sqlite3.Error,TypeError,ValueError,AttributeError) as exc:
                try:self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                if isinstance(exc,TrialLedgerError): raise
                raise TrialLedgerError("invalid validation artifact") from exc

    def save_validation_artifact(self, trial_id: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            try:
                self._c.execute("BEGIN IMMEDIATE"); self._verify_locked(); self._verify_receipts_locked(); self._validate_artifact_payload(trial_id,payload)
                existing=self._receipt_rows_locked(trial_id).get(_ARTIFACT_STAGE); encoded=_canonical(payload); digest=_hash(encoded.encode())
                if existing:
                    if existing["payload_hash"] != digest: raise TrialLedgerConflict("validation artifact conflict")
                else: self._receipt_insert_locked(trial_id,_ARTIFACT_STAGE,"DONE",payload,"artifact",0,1)
                self._c.execute("COMMIT")
            except TrialLedgerConflict:
                try:self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                raise
            except (TrialLedgerError,sqlite3.Error,TypeError,ValueError,AttributeError) as exc:
                try:self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                if isinstance(exc,TrialLedgerError): raise
                raise TrialLedgerError("invalid validation artifact") from exc

    def load_validation_artifact(self, trial_id: str, *, panel_digest: str|None=None, policy_digest: str|None=None) -> Mapping[str,Any]:
        with self._lock:
            try:
                self._c.execute("BEGIN"); self._verify_locked(); self._verify_receipts_locked(); row=self._receipt_rows_locked(trial_id).get(_ARTIFACT_STAGE)
                if not row: raise TrialLedgerError("validation artifact missing")
                payload=row["payload"]; self._validate_artifact_payload(trial_id,payload)
                if panel_digest is not None and payload["panel_digest"] != panel_digest: raise TrialLedgerError("artifact panel mismatch")
                if policy_digest is not None and payload["policy_digest"] != policy_digest: raise TrialLedgerError("artifact policy mismatch")
                self._c.execute("COMMIT"); return payload
            except (TrialLedgerError,sqlite3.Error,TypeError,ValueError,AttributeError) as exc:
                try:self._c.execute("ROLLBACK")
                except sqlite3.Error: pass
                if isinstance(exc,TrialLedgerError): raise
                raise TrialLedgerError("invalid validation artifact") from exc

    def validation_receipt(self, trial_id):
        if not isinstance(trial_id,str) or not _HEX64.fullmatch(trial_id): raise TrialLedgerError("invalid trial_id")
        with self._lock:
            try:
                self._c.execute("BEGIN")
                self._verify_receipts_locked(); result=self._receipt_rows_locked(trial_id)
                self._c.execute("COMMIT"); return result
            except (TrialLedgerError,sqlite3.Error) as exc:
                try:self._c.execute("ROLLBACK")
                except sqlite3.Error:pass
                if isinstance(exc,TrialLedgerError): raise
                raise TrialLedgerError("ledger database failure") from exc

    def verify(self):
        with self._lock:
            try:self._c.execute("BEGIN"); self._verify_locked(); self._verify_receipts_locked(); self._c.execute("COMMIT"); return True
            except (TrialLedgerError,sqlite3.Error,AttributeError) as exc:
                try:self._c.execute("ROLLBACK")
                except (sqlite3.Error,AttributeError):pass
                if isinstance(exc,TrialLedgerError): raise
                raise TrialLedgerError("ledger database failure") from exc

    def load(self, trial_id):
        if not isinstance(trial_id,str) or not _HEX64.fullmatch(trial_id): raise TrialLedgerError("invalid trial_id")
        with self._lock:
            try:self._c.execute("BEGIN"); self._verify_locked(); result=self._aggregate_locked(trial_id); self._c.execute("COMMIT"); return result
            except (TrialLedgerError,sqlite3.Error,AttributeError) as exc:
                try:self._c.execute("ROLLBACK")
                except (sqlite3.Error,AttributeError):pass
                if isinstance(exc,TrialLedgerError): raise
                raise TrialLedgerError("ledger database failure") from exc

    def _effective_trial_count_locked(self, family_id):
        rows=self._c.execute(_SELECT+" WHERE event_index=0 ORDER BY global_seq").fetchall(); seen=set(); selected=[]
        for row in rows:
            if row[3] != family_id: continue
            aggregate=self._aggregate_locked(row[0])
            if aggregate.status == "PLANNED": continue
            key=(aggregate.exact_definition_id,aggregate.validation_policy_digest)
            if key not in seen: seen.add(key); selected.append(row[0])
        return EffectiveTrialCount(len(selected),tuple(selected))

    def effective_trial_count(self, family_id):
        if not isinstance(family_id,str) or not _HEX64.fullmatch(family_id): raise TrialLedgerError("invalid family_id")
        with self._lock:
            try:
                self._c.execute("BEGIN"); self._verify_locked(); rows=self._c.execute(_SELECT+" WHERE event_index=0 ORDER BY global_seq").fetchall(); seen=set(); selected=[]
                for row in rows:
                    if row[3] != family_id: continue
                    aggregate=self._aggregate_locked(row[0]);
                    if aggregate.status == "PLANNED": continue
                    key=(aggregate.exact_definition_id,aggregate.validation_policy_digest)
                    if key not in seen: seen.add(key); selected.append(row[0])
                self._c.execute("COMMIT"); return EffectiveTrialCount(len(selected),tuple(selected))
            except (TrialLedgerError,sqlite3.Error) as exc:
                try:self._c.execute("ROLLBACK")
                except sqlite3.Error:pass
                if isinstance(exc,TrialLedgerError): raise
                raise TrialLedgerError("ledger database failure") from exc


__all__=["TrialLedger","TrialLedgerError","TrialLedgerConflict","TrialRecord","TrialEvent","TrialAggregate","EffectiveTrialCount"]
