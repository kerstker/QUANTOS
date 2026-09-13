"""Issuer-owned append-only reservations; no cash, executor or credential store."""
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import threading

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import validate_policy, current_key, common_context, amount, require
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.validation import demo_submission as submission, demo_cancellation as cancellation

MAX_RECORDS=20000
MAX_BYTES=128*1024*1024
_SCHEMA='''
CREATE TABLE issuer_events(
 seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, proposal_sha256 TEXT NOT NULL,
 at_ms INTEGER NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL,
 record_hash TEXT NOT NULL UNIQUE, UNIQUE(kind,proposal_sha256));
CREATE TABLE issuer_order_claims(
 account_ref_sha256 TEXT NOT NULL,purpose TEXT NOT NULL,client_order_id TEXT NOT NULL,
 proposal_sha256 TEXT NOT NULL,PRIMARY KEY(account_ref_sha256,purpose,client_order_id));
CREATE TRIGGER issuer_events_no_update BEFORE UPDATE ON issuer_events BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER issuer_events_no_delete BEFORE DELETE ON issuer_events BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER issuer_order_claims_no_update BEFORE UPDATE ON issuer_order_claims BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER issuer_order_claims_no_delete BEFORE DELETE ON issuer_order_claims BEGIN SELECT RAISE(ABORT,'append only'); END;
'''


def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def approval_id(plan,policy):
    return _digest({'schema':'DEMO_ISSUER_RESERVATION_ID_V1','policy_sha256':_digest(policy),
                    'purpose':plan['purpose'],'proposal_sha256':plan['proposal_sha256']})


def unsigned_envelope(plan,policy):
    purpose=plan['purpose'].upper()
    return {'schema':'DEMO_SIGNED_'+purpose+'_V1','authority_id':policy['authority_id'],
        'key_id':policy['key_id'],'algorithm':'Ed25519','signature_base64url':'',
        'payload':{'schema':'DEMO_'+purpose+'_GRANT_V1','environment':'BINANCE_USDM_DEMO',
            'approval_id':approval_id(plan,policy),'issued_at_ms':plan['issued_at_ms'],
            'expires_at_ms':plan['expires_at_ms'],'context':plan['context']}}


@ledger_decimal_context()
def validate_reservation(value):
    submission._fields(value,'policy registry plan')
    policy,registry,plan=(value[k] for k in ('policy','registry','plan'))
    validate_policy(policy)
    submission._fields(plan,'purpose proposal_sha256 context orders commands issued_at_ms expires_at_ms notional')
    submission._digest(plan['proposal_sha256']);at=submission._integer(plan['issued_at_ms'])
    key=current_key(policy,registry,at);expiry=submission._integer(plan['expires_at_ms'])
    require(at<expiry<=min(at+policy['grant_ttl_ms'],policy['valid_until_ms'],key['valid_until_ms']),'ISSUER_STORED_LIFETIME')
    require(plan['purpose'] in ('submission','cancellation'),'ISSUER_STORED_PURPOSE')
    common_context(plan['context'],policy)
    if plan['purpose']=='submission':
        submission.validate_context(plan['context']);submission.validate_orders(plan['orders'])
        require(plan['commands']==[] and submission.batch_sha256(plan['orders'])==plan['context']['batch_sha256'],'ISSUER_STORED_BATCH')
        calculated=sum((amount(o['quantity'])*amount(o['price']) for o in plan['orders']),Decimal(0))
        require(amount(plan['notional'])==calculated,'ISSUER_STORED_NOTIONAL')
        return [o['newClientOrderId'] for o in plan['orders']]
    cancellation.validate_context(plan['context']);cancellation.validate_commands(plan['commands'])
    require(plan['orders']==[] and plan['notional']=='0' and
            cancellation.batch_sha256(plan['commands'])==plan['context']['cancel_batch_sha256'],'ISSUER_STORED_CANCEL')
    return [o['origClientOrderId'] for o in plan['commands']]


class IssuerStore:
    """Runtime opens an existing store. Creation is explicit installation work."""
    def __init__(self,path,*,create=False,read_only=False):
        self.path=Path(path).absolute();self.read_only=read_only;self.lock=threading.RLock()
        require(not self.path.is_symlink() and not (create and read_only),'ISSUER_STORE_PATH')
        if create:
            fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
        self.db=sqlite3.connect(self.path.as_uri()+('?mode=ro' if read_only else '?mode=rw'),
            uri=True,timeout=5,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        try:
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA busy_timeout=5000')
            if create:
                self.db.executescript(_SCHEMA);self.db.commit()
                fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
                try:os.fsync(fd)
                finally:os.close(fd)
            self.snapshot()
        except BaseException:self.db.close();raise

    def close(self):self.db.close()

    def _rows(self):
        size=sum(p.stat().st_size for p in (Path(str(self.path)+suffix) for suffix in ('','-wal','-shm','-journal')) if p.exists())
        require(size<=MAX_BYTES,'ISSUER_STORE_BYTE_BUDGET')
        triggers={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        require({'issuer_events_no_update','issuer_events_no_delete','issuer_order_claims_no_update','issuer_order_claims_no_delete'}<=triggers,
                'ISSUER_STORE_TRIGGERS')
        rows=self.db.execute('SELECT * FROM issuer_events ORDER BY seq LIMIT ?', (MAX_RECORDS+1,)).fetchall()
        require(len(rows)<=MAX_RECORDS,'ISSUER_STORE_RECORD_BUDGET')
        previous='';last_at=0;result=[];reserved={};signed=set();claims=[];policies={}
        for seq,row in enumerate(rows,1):
            require(row['seq']==seq and row['prev_hash']==previous and 0<len(row['payload'])<=131072,'ISSUER_STORE_CHAIN')
            payload=json.loads(row['payload']);at=submission._integer(row['at_ms'])
            require(at>=last_at and encoded(payload)==row['payload'],'ISSUER_STORE_TIME_OR_ENCODING')
            record={'seq':seq,'kind':row['kind'],'proposal_sha256':row['proposal_sha256'],
                    'at_ms':at,'payload':payload,'prev_hash':previous}
            require(_digest(record)==row['record_hash'],'ISSUER_STORE_CHAIN')
            identity=row['proposal_sha256'];submission._digest(identity)
            if row['kind']=='RESERVED':
                ids=validate_reservation(payload);policy=payload['policy'];plan=payload['plan']
                require(identity not in reserved and plan['proposal_sha256']==identity and plan['issued_at_ms']==at,'ISSUER_STORE_RESERVATION')
                binding=_digest(policy)
                require(policies.get(policy['policy_id'],binding)==binding,'ISSUER_POLICY_ID_REBOUND')
                policies[policy['policy_id']]=binding;reserved[identity]=payload
                claims.extend((policy['account_ref_sha256'],plan['purpose'],client,identity) for client in ids)
            else:
                require(row['kind']=='SIGNED' and identity in reserved and identity not in signed,'ISSUER_STORE_SIGNATURE_EVENT')
                submission._fields(payload,'envelope');original=reserved[identity];plan=original['plan'];policy=original['policy']
                envelope=payload['envelope'];expected=unsigned_envelope(plan,policy)
                require({k:v for k,v in envelope.items() if k!='signature_base64url'}==
                        {k:v for k,v in expected.items() if k!='signature_base64url'},'ISSUER_STORED_ENVELOPE')
                common=dict(registry=original['registry'],expected_context=plan['context'],now_ms=at)
                if plan['purpose']=='submission':submission.verify(envelope,orders=plan['orders'],**common)
                else:cancellation.verify(envelope,commands=plan['commands'],**common)
                signed.add(identity)
            previous=row['record_hash'];last_at=at;result.append({**record,'record_hash':previous})
        actual=[tuple(row) for row in self.db.execute('SELECT account_ref_sha256,purpose,client_order_id,proposal_sha256 FROM issuer_order_claims')]
        require(sorted(actual)==sorted(claims),'ISSUER_ORDER_CLAIM_INDEX')
        return result

    def snapshot(self):
        with self.lock:
            if self.db.in_transaction:return self._rows()
            self.db.execute('BEGIN')
            try:return self._rows()
            finally:self.db.rollback()

    def _append(self,kind,identity,payload,at,rows):
        require(len(rows)<MAX_RECORDS and len(encoded(payload).encode())<=131072,'ISSUER_APPEND_BUDGET')
        require(not rows or at>=rows[-1]['at_ms'],'ISSUER_APPEND_CLOCK')
        record={'seq':len(rows)+1,'kind':kind,'proposal_sha256':identity,'at_ms':at,
                'payload':payload,'prev_hash':rows[-1]['record_hash'] if rows else ''}
        self.db.execute('INSERT INTO issuer_events VALUES(?,?,?,?,?,?,?)',
            (record['seq'],kind,identity,at,encoded(payload),record['prev_hash'],_digest(record)))

    @ledger_decimal_context()
    def reserve(self,plan,*,policy,registry):
        require(not self.read_only,'ISSUER_READ_ONLY')
        value=json.loads(encoded({'plan':plan,'policy':policy,'registry':registry}));ids=validate_reservation(value)
        plan,policy=value['plan'],value['policy']
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                rows=self._rows();previous=[row['payload'] for row in rows if row['kind']=='RESERVED']
                require(all(row['proposal_sha256']!=plan['proposal_sha256'] for row in rows),'ISSUER_PROPOSAL_ALREADY_RESERVED')
                own=[r for r in previous if r['policy']['policy_id']==policy['policy_id']]
                require(all(r['policy']==policy for r in own),'ISSUER_POLICY_ID_REBOUND')
                own=[r for r in own if r['plan']['purpose']==plan['purpose']]
                prefix='max_' if plan['purpose']=='submission' else 'max_cancel_'
                require(len(own)<policy[prefix+'grants'] and
                        sum(len(r['plan']['orders'] or r['plan']['commands']) for r in own)+len(ids)<=policy[prefix+'orders'],
                        'ISSUER_CUMULATIVE_COUNT_BUDGET')
                if plan['purpose']=='submission':
                    total=sum((Decimal(r['plan']['notional']) for r in own),Decimal(0))+Decimal(plan['notional'])
                    require(total<=amount(policy['max_total_notional']),'ISSUER_CUMULATIVE_NOTIONAL_BUDGET')
                for client in ids:
                    require(self.db.execute('SELECT 1 FROM issuer_order_claims WHERE account_ref_sha256=? AND purpose=? AND client_order_id=?',
                        (policy['account_ref_sha256'],plan['purpose'],client)).fetchone() is None,'ISSUER_ORDER_ALREADY_RESERVED')
                self._append('RESERVED',plan['proposal_sha256'],value,plan['issued_at_ms'],rows)
                self.db.executemany('INSERT INTO issuer_order_claims VALUES(?,?,?,?)',
                    [(policy['account_ref_sha256'],plan['purpose'],client,plan['proposal_sha256']) for client in ids])
                self.db.commit()
            except BaseException:self.db.rollback();raise

    def signed(self,proposal_sha256,envelope,*,now_ms):
        require(not self.read_only,'ISSUER_READ_ONLY');submission._integer(now_ms)
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                rows=self._rows()
                require(any(r['kind']=='RESERVED' and r['proposal_sha256']==proposal_sha256 for r in rows) and
                        not any(r['kind']=='SIGNED' and r['proposal_sha256']==proposal_sha256 for r in rows),'ISSUER_SIGNATURE_ALREADY_RECORDED')
                self._append('SIGNED',proposal_sha256,{'envelope':envelope},now_ms,rows)
                self._rows();self.db.commit()
            except BaseException:self.db.rollback();raise
