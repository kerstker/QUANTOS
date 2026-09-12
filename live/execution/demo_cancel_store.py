"""Append-only cancellation claims and responses; never an order or cash ledger."""
import hashlib
from decimal import Decimal
import os
from pathlib import Path
import re
import sqlite3
import threading

from live.execution import demo_authority
from services.validation import demo_cancellation as auth
from services.validation.demo_submission import _canonical,_fields,_integer,_digest
from services.governance.json_integrity import load_json_without_duplicate_keys

_SCHEMA='''
CREATE TABLE demo_cancel_events(seq INTEGER PRIMARY KEY,kind TEXT NOT NULL,identity TEXT NOT NULL,
 at_ms INTEGER NOT NULL,payload TEXT NOT NULL,prev_hash TEXT NOT NULL,record_hash TEXT NOT NULL UNIQUE,
 UNIQUE(kind,identity));
CREATE TRIGGER demo_cancel_no_update BEFORE UPDATE ON demo_cancel_events BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER demo_cancel_no_delete BEFORE DELETE ON demo_cancel_events BEGIN SELECT RAISE(ABORT,'append only'); END;
'''


class DemoCancelConflict(RuntimeError):pass


def _hash(value):return hashlib.sha256(_canonical(value)).hexdigest()


def validate_outcome(value):
    _fields(value,'status http_status response_sha256 venue_status executed_quantity')
    http=value['http_status'];digest=value['response_sha256']
    if http is not None and (type(http) is not int or not 100<=http<=599):raise DemoCancelConflict('DEMO_CANCEL_RESPONSE')
    if digest is not None:_digest(digest)
    if value['status']=='ORDER_STATE_OBSERVED':
        if (http is None or not 200<=http<300 or digest is None or
            value['venue_status'] not in ('NEW','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','EXPIRED_IN_MATCH') or
            type(value['executed_quantity']) is not str or re.fullmatch(r'\d{1,24}(\.\d{1,24})?',value['executed_quantity']) is None):
            raise DemoCancelConflict('DEMO_CANCEL_RESPONSE')
        amount=Decimal(value['executed_quantity'])
        if (value['venue_status']=='NEW' and amount!=0 or
            value['venue_status'] in ('FILLED','PARTIALLY_FILLED') and amount<=0):raise DemoCancelConflict('DEMO_CANCEL_RESPONSE_QUANTITY')
    elif value['status']!='UNKNOWN' or value['venue_status'] is not None or value['executed_quantity'] is not None:
        raise DemoCancelConflict('DEMO_CANCEL_RESPONSE')
    return value


class DemoCancelStore:
    def __init__(self,path,*,create=False,read_only=False):
        if create and read_only:raise DemoCancelConflict('DEMO_CANCEL_READ_ONLY')
        self.path=Path(path).absolute();self.read_only=read_only;self._lock=threading.RLock()
        if self.path.is_symlink():raise DemoCancelConflict('DEMO_CANCEL_SYMLINK')
        if create:
            fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600);os.close(fd)
        self._conn=sqlite3.connect(self.path.as_uri()+('?mode=ro' if read_only else '?mode=rw'),uri=True,timeout=5,check_same_thread=False)
        self._conn.row_factory=sqlite3.Row
        try:
            self._conn.execute('PRAGMA synchronous=FULL');self._conn.execute('PRAGMA busy_timeout=5000')
            if create:
                self._conn.executescript(_SCHEMA);self._conn.commit()
                fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
            self.snapshot()
        except BaseException:self._conn.close();raise

    def _rows(self):
        rows=self._conn.execute('SELECT * FROM demo_cancel_events ORDER BY seq LIMIT 41001').fetchall()
        if len(rows)>41000:raise DemoCancelConflict('DEMO_CANCEL_JOURNAL_BUDGET')
        records=[];tail='0'*64;grants={};claims={};seen=set();previous=0
        for row in rows:
            if len(row['payload'])>65536:raise DemoCancelConflict('DEMO_CANCEL_RECORD_BUDGET')
            r=dict(row);r['payload']=load_json_without_duplicate_keys(r['payload'])
            raw={k:v for k,v in r.items() if k!='record_hash'}
            if (r['seq']!=len(records)+1 or r['prev_hash']!=tail or _hash(raw)!=r['record_hash'] or
                (r['kind'],r['identity']) in seen or _integer(r['at_ms'])<previous):raise DemoCancelConflict('DEMO_CANCEL_CHAIN')
            p=r['payload']
            if r['kind']=='GRANT':
                _fields(p,'envelope context commands')
                envelope=auth.parse_envelope(_canonical(p['envelope']));auth.validate_context(p['context'])
                if (envelope['payload']['context']!=p['context'] or auth.batch_sha256(p['commands'])!=p['context']['cancel_batch_sha256'] or
                    envelope['payload']['approval_id']!=r['identity'] or
                    not envelope['payload']['issued_at_ms']<=r['at_ms']<envelope['payload']['expires_at_ms']):raise DemoCancelConflict('DEMO_CANCEL_GRANT_BINDING')
                grants[r['record_hash']]=r
                if len(grants)>1000:raise DemoCancelConflict('DEMO_CANCEL_GRANT_BUDGET')
            elif r['kind']=='CLAIM':
                _fields(p,'grant_sha256 client_order_id request_sha256');_digest(p['request_sha256'])
                grant=grants.get(p['grant_sha256'])
                if (grant is None or r['identity']!=p['grant_sha256']+':'+p['client_order_id'] or
                    p['client_order_id'] not in {c['origClientOrderId'] for c in grant['payload']['commands']} or
                    r['at_ms']>=grant['payload']['envelope']['payload']['expires_at_ms']):raise DemoCancelConflict('DEMO_CANCEL_CLAIM_BINDING')
                claims[r['record_hash']]=r
            elif r['kind']=='RESULT':
                _fields(p,'claim_sha256 outcome');validate_outcome(p['outcome'])
                if p['claim_sha256'] not in claims or r['identity']!=p['claim_sha256']:raise DemoCancelConflict('DEMO_CANCEL_RESULT_BINDING')
            else:raise DemoCancelConflict('DEMO_CANCEL_EVENT_KIND')
            seen.add((r['kind'],r['identity']));previous=r['at_ms'];tail=r['record_hash'];records.append(r)
        return records

    def snapshot(self):
        with self._lock:
            try:
                self._conn.execute('BEGIN');rows=self._rows();self._conn.commit();return rows
            except Exception:
                self._conn.rollback();raise DemoCancelConflict('DEMO_CANCEL_JOURNAL_INVALID') from None

    def _append(self,kind,identity,at_ms,payload):
        if self.read_only:raise DemoCancelConflict('DEMO_CANCEL_READ_ONLY')
        with self._lock:
            try:
                self._conn.execute('BEGIN IMMEDIATE');rows=self._rows()
                previous=next((r for r in rows if r['kind']==kind and r['identity']==identity),None)
                if previous is not None:
                    if kind=='RESULT' and previous['payload']==payload and previous['at_ms']==at_ms:
                        self._conn.commit();return previous
                    raise DemoCancelConflict('DEMO_CANCEL_ALREADY_CONSUMED')
                record={'seq':len(rows)+1,'kind':kind,'identity':identity,'at_ms':at_ms,'payload':payload,
                    'prev_hash':rows[-1]['record_hash'] if rows else '0'*64}
                record['record_hash']=_hash(record)
                self._conn.execute('INSERT INTO demo_cancel_events VALUES(?,?,?,?,?,?,?)',
                    (record['seq'],kind,identity,at_ms,_canonical(payload).decode(),record['prev_hash'],record['record_hash']))
                self._rows();self._conn.commit();return record
            except Exception as exc:
                self._conn.rollback()
                if isinstance(exc,DemoCancelConflict):raise
                raise DemoCancelConflict('DEMO_CANCEL_APPEND_REJECTED') from None

    def reserve(self,envelope,*,context,commands,now_ms):
        if self.read_only:raise DemoCancelConflict('DEMO_CANCEL_READ_ONLY')
        registry=demo_authority.load_registry_for_executor()
        if context.get('executor_boot_sha256')!=demo_authority.executor_boot_sha256():raise DemoCancelConflict('DEMO_CANCEL_BOOT')
        parsed=auth.parse_envelope(envelope)
        checked=auth.verify(parsed,registry=registry,expected_context=context,commands=commands,now_ms=now_ms)
        return self._append('GRANT',checked['approval_id'],now_ms,{'envelope':parsed,'context':context,'commands':commands})

    def claim(self,grant_sha256,client_order_id,*,request_sha256,now_ms):
        return self._append('CLAIM',grant_sha256+':'+client_order_id,now_ms,
            {'grant_sha256':grant_sha256,'client_order_id':client_order_id,'request_sha256':request_sha256})

    def result(self,claim_sha256,*,outcome,now_ms):
        return self._append('RESULT',claim_sha256,now_ms,{'claim_sha256':claim_sha256,'outcome':outcome})

    def close(self):self._conn.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
