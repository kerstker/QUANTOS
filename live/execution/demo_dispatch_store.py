"""Atomic one-time Demo grant and exact client-order reservations.

This journal owns no cash or positions and has no network capability. A returned
reservation is not an order receipt. Dispatch must still run risk/OMS barriers
and retain unknown-order intent before sending. A crash never refunds a committed
grant: recovery queries the original IDs, and an old boot grant cannot resume.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import threading

from live.execution.demo_authority import verify_for_executor
from services.validation.demo_submission import batch_sha256, parse_envelope, validate_context
from live.execution.demo_wire_protocol import validate_outcome

_MAX_BATCHES=1000
_SCHEMA='''
CREATE TABLE demo_dispatch_reservations (
 seq INTEGER PRIMARY KEY,
 approval_id TEXT NOT NULL UNIQUE,
 account_ref_sha256 TEXT NOT NULL,
 payload TEXT NOT NULL,
 prev_hash TEXT NOT NULL,
 record_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE demo_dispatch_reserved_orders (
 account_ref_sha256 TEXT NOT NULL,
 client_order_id TEXT NOT NULL,
 approval_id TEXT NOT NULL REFERENCES demo_dispatch_reservations(approval_id),
 order_slot INTEGER NOT NULL CHECK(order_slot>=0),
 PRIMARY KEY(account_ref_sha256,client_order_id)
);
CREATE TRIGGER demo_dispatch_reservations_no_update BEFORE UPDATE ON demo_dispatch_reservations
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER demo_dispatch_reservations_no_delete BEFORE DELETE ON demo_dispatch_reservations
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER demo_dispatch_reserved_orders_no_update BEFORE UPDATE ON demo_dispatch_reserved_orders
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER demo_dispatch_reserved_orders_no_delete BEFORE DELETE ON demo_dispatch_reserved_orders
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TABLE demo_wire_events (
 seq INTEGER PRIMARY KEY,
 kind TEXT NOT NULL CHECK(kind IN ('SEND_CLAIM','SEND_RESULT','QUERY_RESULT')),
 reservation_sha256 TEXT NOT NULL REFERENCES demo_dispatch_reservations(record_hash),
 client_order_id TEXT NOT NULL,
 at_ms INTEGER NOT NULL,
 payload TEXT NOT NULL,
 prev_hash TEXT NOT NULL,
 record_hash TEXT NOT NULL UNIQUE
);
CREATE UNIQUE INDEX demo_wire_once ON demo_wire_events(reservation_sha256,client_order_id,kind) WHERE kind IN ('SEND_CLAIM','SEND_RESULT');
CREATE TRIGGER demo_wire_events_no_update BEFORE UPDATE ON demo_wire_events
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER demo_wire_events_no_delete BEFORE DELETE ON demo_wire_events
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
'''


class DemoDispatchConflict(RuntimeError):
    pass


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False,ensure_ascii=True)


def _hash(record):
    return hashlib.sha256(_canonical(record).encode()).hexdigest()


class DemoDispatchStore:
    """Use only an executor-controlled state path; deployment enforces custody."""
    def __init__(self,path, *, create=False, read_only=False):
        if create and read_only:raise DemoDispatchConflict('DEMO_DISPATCH_READ_ONLY')
        self.path=Path(path).absolute();self._lock=threading.RLock();self.read_only=read_only
        if self.path.is_symlink():raise DemoDispatchConflict('DEMO_DISPATCH_STATE_SYMLINK')
        if create:
            fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
            os.close(fd)
        self._conn=sqlite3.connect(self.path.as_uri()+('?mode=ro' if read_only else '?mode=rw'),uri=True,timeout=5,check_same_thread=False)
        self._conn.row_factory=sqlite3.Row
        try:
            self._conn.execute('PRAGMA synchronous=FULL')
            self._conn.execute('PRAGMA foreign_keys=ON')
            self._conn.execute('PRAGMA busy_timeout=5000')
            if create:
                self._conn.executescript(_SCHEMA);self._conn.commit()
                directory=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(directory)
                finally:os.close(directory)
            self.snapshot()
        except BaseException:
            self._conn.close();raise

    def _verified_rows(self):
        rows=self._conn.execute('SELECT * FROM demo_dispatch_reservations ORDER BY seq LIMIT ?',(_MAX_BATCHES+1,)).fetchall()
        if len(rows)>_MAX_BATCHES:raise DemoDispatchConflict('DEMO_DISPATCH_JOURNAL_BUDGET')
        previous='';records=[];expected_orders=[]
        for seq,row in enumerate(rows,1):
            if row['seq']!=seq or row['prev_hash']!=previous or len(row['payload'])>98304:
                raise DemoDispatchConflict('DEMO_DISPATCH_CHAIN')
            payload=json.loads(row['payload'])
            if set(payload)!={'envelope','orders','context','reserved_at_ms'}:
                raise DemoDispatchConflict('DEMO_DISPATCH_PAYLOAD')
            envelope=parse_envelope(_canonical(payload['envelope']).encode());grant=envelope['payload']
            context=validate_context(payload['context'])
            if context!=grant['context'] or batch_sha256(payload['orders'])!=context['batch_sha256']:
                raise DemoDispatchConflict('DEMO_DISPATCH_PAYLOAD_BINDING')
            at=payload['reserved_at_ms']
            if type(at) is not int or not grant['issued_at_ms']<=at<grant['expires_at_ms']:
                raise DemoDispatchConflict('DEMO_DISPATCH_TIME')
            if row['approval_id']!=grant['approval_id'] or row['account_ref_sha256']!=context['account_ref_sha256']:
                raise DemoDispatchConflict('DEMO_DISPATCH_IDENTITY')
            record={'seq':seq,'approval_id':row['approval_id'],'account_ref_sha256':row['account_ref_sha256'],
                    'payload':payload,'prev_hash':previous}
            digest=_hash(record)
            if digest!=row['record_hash'] or _canonical(payload)!=row['payload']:
                raise DemoDispatchConflict('DEMO_DISPATCH_CHAIN')
            records.append({**record,'record_hash':digest});previous=digest
            expected_orders.extend((row['account_ref_sha256'],o['newClientOrderId'],row['approval_id'],i)
                                   for i,o in enumerate(payload['orders']))
        actual=[tuple(r) for r in self._conn.execute('SELECT account_ref_sha256,client_order_id,approval_id,order_slot FROM demo_dispatch_reserved_orders')]
        if sorted(expected_orders)!=sorted(actual):raise DemoDispatchConflict('DEMO_DISPATCH_ORDER_INDEX')
        return records

    def snapshot(self):
        with self._lock:
            try:
                self._conn.execute('BEGIN')
                return self._verified_rows()
            except (sqlite3.Error,ValueError,TypeError,KeyError):
                raise DemoDispatchConflict('DEMO_DISPATCH_STATE_INVALID') from None
            finally:self._conn.rollback()

    def reserve(self,envelope_bytes, *, expected_context,orders,now_ms):
        if self.read_only:raise DemoDispatchConflict('DEMO_DISPATCH_READ_ONLY')
        # Detached exact input bytes prevent caller mutations after verification.
        frozen_orders=json.loads(_canonical(orders));context=json.loads(_canonical(expected_context))
        with self._lock:
            try:
                self._conn.execute('BEGIN IMMEDIATE')
                prior=self._verified_rows()
                verified=verify_for_executor(envelope_bytes,expected_context=context,orders=frozen_orders,now_ms=now_ms)
                if len(prior)>=_MAX_BATCHES:raise DemoDispatchConflict('DEMO_DISPATCH_JOURNAL_BUDGET')
                envelope=parse_envelope(envelope_bytes)
                payload={'envelope':envelope,'orders':frozen_orders,'context':context,'reserved_at_ms':now_ms}
                record={'seq':len(prior)+1,'approval_id':verified['approval_id'],'account_ref_sha256':context['account_ref_sha256'],
                        'payload':payload,'prev_hash':prior[-1]['record_hash'] if prior else ''}
                digest=_hash(record)
                self._conn.execute('INSERT INTO demo_dispatch_reservations VALUES (?,?,?,?,?,?)',
                    (record['seq'],record['approval_id'],record['account_ref_sha256'],_canonical(payload),record['prev_hash'],digest))
                self._conn.executemany('INSERT INTO demo_dispatch_reserved_orders VALUES (?,?,?,?)',
                    [(record['account_ref_sha256'],o['newClientOrderId'],record['approval_id'],i) for i,o in enumerate(frozen_orders)])
                self._conn.commit()
                return {**record,'record_hash':digest}
            except sqlite3.Error:
                self._conn.rollback()
                raise DemoDispatchConflict('DEMO_DISPATCH_RESERVATION_REJECTED') from None
            except BaseException:
                self._conn.rollback();raise

    def close(self):
        with self._lock:self._conn.close()

    def _wire_rows(self,reservations):
        by_hash={r['record_hash']:r for r in reservations}
        rows=self._conn.execute('SELECT * FROM demo_wire_events ORDER BY seq LIMIT 40001').fetchall()
        if len(rows)>40000:raise DemoDispatchConflict('DEMO_WIRE_BUDGET')
        previous='';records=[];claims={};results=set()
        for seq,row in enumerate(rows,1):
            record=dict(row);digest=record.pop('record_hash');body=record['payload']
            if len(body)>65536:raise DemoDispatchConflict('DEMO_WIRE_BUDGET')
            record['payload']=json.loads(body);payload=record['payload']
            if (record['seq']!=seq or record['prev_hash']!=previous or _hash(record)!=digest
                or _canonical(payload)!=body):raise DemoDispatchConflict('DEMO_WIRE_CHAIN')
            reservation=by_hash.get(record['reservation_sha256'])
            if reservation is None:raise DemoDispatchConflict('DEMO_WIRE_RESERVATION')
            planned=[o for o in reservation['payload']['orders'] if o['newClientOrderId']==record['client_order_id']]
            if len(planned)!=1 or type(record['at_ms']) is not int or not reservation['payload']['reserved_at_ms']<=record['at_ms']<=9007199254740991:
                raise DemoDispatchConflict('DEMO_WIRE_IDENTITY')
            identity=(record['reservation_sha256'],record['client_order_id'])
            if record['kind']=='SEND_CLAIM':
                if (identity in claims or set(payload)!=set(('request_sha256','preflight_sha256')) or
                    any(type(v) is not str or re.fullmatch('[a-f0-9]{64}',v) is None for v in payload.values())):
                    raise DemoDispatchConflict('DEMO_WIRE_CLAIM')
                if not record['at_ms']<reservation['payload']['envelope']['payload']['expires_at_ms']:
                    raise DemoDispatchConflict('DEMO_WIRE_EXPIRED')
                claims[identity]={**record,'record_hash':digest}
            elif record['kind']=='SEND_RESULT':
                claim=claims.get(identity)
                if (claim is None or identity in results or set(payload)!=set(('claim_sha256','outcome')) or
                    payload['claim_sha256']!=claim['record_hash'] or record['at_ms']<claim['at_ms']):
                    raise DemoDispatchConflict('DEMO_WIRE_RESULT')
                validate_outcome(payload['outcome'],planned[0])
                if payload['outcome']['status'] not in ('ACKNOWLEDGED','UNKNOWN'):raise DemoDispatchConflict('DEMO_WIRE_RESULT')
                results.add(identity)
            elif record['kind']=='QUERY_RESULT':
                if (set(payload)!=set(('requested_at_ms','outcome')) or type(payload['requested_at_ms']) is not int or
                    not reservation['payload']['reserved_at_ms']<=payload['requested_at_ms']<=record['at_ms']):
                    raise DemoDispatchConflict('DEMO_WIRE_QUERY')
                validate_outcome(payload['outcome'],planned[0])
                if payload['outcome']['status'] not in ('FOUND','UNKNOWN'):raise DemoDispatchConflict('DEMO_WIRE_QUERY')
            else:raise DemoDispatchConflict('DEMO_WIRE_KIND')
            records.append({**record,'record_hash':digest});previous=digest
        return records

    def wire_snapshot(self):
        with self._lock:
            try:
                self._conn.execute('BEGIN')
                return self._wire_rows(self._verified_rows())
            except (sqlite3.Error,ValueError,TypeError,KeyError):
                raise DemoDispatchConflict('DEMO_WIRE_STATE_INVALID') from None
            finally:self._conn.rollback()

    def evidence_snapshot(self):
        """Read reservations and their wire chain from one verified SQLite view."""
        with self._lock:
            try:
                self._conn.execute('BEGIN')
                reservations = self._verified_rows()
                return {'reservations': reservations, 'wire': self._wire_rows(reservations)}
            except (sqlite3.Error, ValueError, TypeError, KeyError):
                raise DemoDispatchConflict('DEMO_DISPATCH_EVIDENCE_INVALID') from None
            finally:
                self._conn.rollback()

    def _append_wire(self, *,kind,reservation_sha256,client_order_id,at_ms,payload):
        if self.read_only:raise DemoDispatchConflict('DEMO_DISPATCH_READ_ONLY')
        with self._lock:
            try:
                self._conn.execute('BEGIN IMMEDIATE')
                reservations=self._verified_rows();rows=self._wire_rows(reservations)
                record={'seq':len(rows)+1,'kind':kind,'reservation_sha256':reservation_sha256,
                        'client_order_id':client_order_id,'at_ms':at_ms,'payload':json.loads(_canonical(payload)),
                        'prev_hash':rows[-1]['record_hash'] if rows else ''}
                for prior in rows:
                    if (prior['kind'],prior['reservation_sha256'],prior['client_order_id'])==(kind,reservation_sha256,client_order_id):
                        if kind in ('SEND_RESULT','QUERY_RESULT') and prior['payload']==record['payload'] and prior['at_ms']==at_ms:
                            self._conn.rollback();return prior
                        if kind=='QUERY_RESULT':continue
                        raise DemoDispatchConflict('DEMO_WIRE_ALREADY_CLAIMED_OR_RECORDED')
                digest=_hash(record)
                self._conn.execute('INSERT INTO demo_wire_events VALUES(?,?,?,?,?,?,?,?)',
                    (record['seq'],kind,reservation_sha256,client_order_id,at_ms,_canonical(record['payload']),record['prev_hash'],digest))
                self._wire_rows(reservations)
                self._conn.commit()
                return {**record,'record_hash':digest}
            except (sqlite3.Error,ValueError,TypeError,KeyError):
                self._conn.rollback();raise DemoDispatchConflict('DEMO_WIRE_WRITE_REJECTED') from None
            except BaseException:
                self._conn.rollback();raise

    def claim_send(self, *,reservation_sha256,client_order_id,request_sha256,preflight_sha256,now_ms):
        """Commit before calling transport. A crash consumes this ID forever."""
        return self._append_wire(kind='SEND_CLAIM',reservation_sha256=reservation_sha256,client_order_id=client_order_id,
            at_ms=now_ms,payload={'request_sha256':request_sha256,'preflight_sha256':preflight_sha256})

    def record_wire_result(self, *,claim_sha256,outcome,now_ms):
        claims=[r for r in self.wire_snapshot() if r['kind']=='SEND_CLAIM' and r['record_hash']==claim_sha256]
        if len(claims)!=1:raise DemoDispatchConflict('DEMO_WIRE_UNKNOWN_CLAIM')
        claim=claims[0]
        return self._append_wire(kind='SEND_RESULT',reservation_sha256=claim['reservation_sha256'],client_order_id=claim['client_order_id'],
            at_ms=now_ms,payload={'claim_sha256':claim_sha256,'outcome':outcome})

    def record_query_result(self, *,reservation_sha256,client_order_id,requested_at_ms,outcome,now_ms):
        return self._append_wire(kind='QUERY_RESULT',reservation_sha256=reservation_sha256,client_order_id=client_order_id,
            at_ms=now_ms,payload={'requested_at_ms':requested_at_ms,'outcome':outcome})

    def __enter__(self):return self

    def __exit__(self,*args):self.close()
