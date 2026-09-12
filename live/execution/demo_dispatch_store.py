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
from pathlib import Path
import sqlite3
import threading

from live.execution.demo_authority import verify_for_executor
from services.validation.demo_submission import batch_sha256, parse_envelope, validate_context

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
'''


class DemoDispatchConflict(RuntimeError):
    pass


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False,ensure_ascii=True)


def _hash(record):
    return hashlib.sha256(_canonical(record).encode()).hexdigest()


class DemoDispatchStore:
    """Use only an executor-controlled state path; deployment enforces custody."""
    def __init__(self,path, *, create=False):
        self.path=Path(path).absolute();self._lock=threading.RLock()
        if self.path.is_symlink():raise DemoDispatchConflict('DEMO_DISPATCH_STATE_SYMLINK')
        if create:
            fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
            os.close(fd)
        self._conn=sqlite3.connect(self.path.as_uri()+'?mode=rw',uri=True,timeout=5,check_same_thread=False)
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

    def __enter__(self):return self

    def __exit__(self,*args):self.close()
