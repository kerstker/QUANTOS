"""Issuer-owned append-only reservations; no cash, executor or credential store."""
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import threading

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import validate_policy, current_key, common_context, amount, require, submission_batch_schema, cancellation_batch_schema, budget_group_binding, GOLD_GROUP_POLICY_SCHEMAS, GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA
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


def require_group_route(groups,value):
    """Only post-continuity spending requires its explicit future protocol.

    Replay checks the state immediately before this reservation, preserving
    every original exact/unsigned reservation preceding the transition.
    """
    state=groups.get(value['policy'].get('budget_group',{}).get('group_id'))
    if state is None or state.get('continuity_event_sha256') is None:return
    require(budget_group_binding(value['policy'])==state['binding'],'ISSUER_GROUP_ID_REBOUND')
    purpose=value['plan']['purpose']
    require((purpose=='submission' and value.get('delegation',{}).get('schema')=='DEMO_GOLD_DELEGATED_ISSUER_RESERVATION_V2')
        or (purpose=='cancellation' and value.get('cancellation',{}).get('schema')=='DEMO_GOLD_TEMPLATE_CANCEL_RESERVATION_V1'),
        'ISSUER_FUTURE_GROUP_ROUTE')


@ledger_decimal_context()
def consume_group(groups,policy,plan):
    binding=budget_group_binding(policy)
    if binding is None:return
    group=policy['budget_group'];identity=group['group_id']
    state=groups.setdefault(identity,{'binding':binding,'submission':[0,0,Decimal(0)],'cancellation':[0,0,Decimal(0)]})
    require(state['binding']==binding,'ISSUER_GROUP_ID_REBOUND')
    purpose=plan['purpose'];counts=state[purpose]
    prefix='max_' if purpose=='submission' else 'max_cancel_'
    grants=counts[0]+1;orders=counts[1]+len(plan['orders'] or plan['commands'])
    require(grants<=group[prefix+'grants'] and orders<=group[prefix+'orders'],'ISSUER_GROUP_COUNT_BUDGET')
    notional=counts[2]+Decimal(plan['notional'])
    if purpose=='submission':require(notional<=amount(group['max_total_notional']),'ISSUER_GROUP_NOTIONAL_BUDGET')
    if purpose=='submission' and policy['schema']==GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA:
        # The remaining slice is reserved for reductions, not a second ledger
        # or permission to exit. Risk, position, mandate and expiry still apply.
        actions={order['reduceOnly'] for order in plan['orders']}
        require(len(actions)==1,'ISSUER_EXIT_RESERVE_MIXED_BATCH')
        if actions=={False}:
            # Include prior reductions: exiting never replenishes the budget.
            # Every new opening must leave the reserve unused at that moment.
            reserve=group['exit_reserve']
            require(grants<=group['max_grants']-reserve['grants']
                and orders<=group['max_orders']-reserve['orders'],'ISSUER_EXIT_RESERVE_COUNT_BUDGET')
            require(notional<=amount(group['max_total_notional'])-amount(reserve['notional']),
                'ISSUER_EXIT_RESERVE_NOTIONAL_BUDGET')
    state[purpose]=[grants,orders,notional]


def approval_id(plan,policy):
    return _digest({'schema':'DEMO_ISSUER_RESERVATION_ID_V1','policy_sha256':_digest(policy),
                    'purpose':plan['purpose'],'proposal_sha256':plan['proposal_sha256']})


def unsigned_envelope(plan,policy):
    purpose=plan['purpose'].upper()
    gold=(cancellation_batch_schema(policy)==cancellation.GOLD_BATCH_SCHEMA if plan['purpose']=='cancellation'
        else submission_batch_schema(policy)==submission.GOLD_BATCH_SCHEMA)
    prefix='DEMO_GOLD' if gold else 'DEMO'
    return {'schema':prefix+'_SIGNED_'+purpose+'_V1','authority_id':policy['authority_id'],
        'key_id':policy['key_id'],'algorithm':'Ed25519','signature_base64url':'',
        'payload':{'schema':prefix+'_'+purpose+'_GRANT_V1','environment':'BINANCE_USDM_DEMO',
            'approval_id':approval_id(plan,policy),'issued_at_ms':plan['issued_at_ms'],
            'expires_at_ms':plan['expires_at_ms'],'context':plan['context']}}


@ledger_decimal_context()
def validate_reservation(value):
    delegated=type(value) is dict and 'delegation' in value
    template_cancel=type(value) is dict and 'cancellation' in value
    require(not (delegated and template_cancel),'ISSUER_STORED_PURPOSE_PAIR')
    submission._fields(value,'policy registry plan'+(' delegation' if delegated else '')+(' cancellation' if template_cancel else ''))
    policy,registry,plan=(value[k] for k in ('policy','registry','plan'))
    from live.execution.demo_issuer_group_continuity import CANCEL_VIEW_SCHEMA,validate_cancel_view
    require(template_cancel==(policy.get('schema')==CANCEL_VIEW_SCHEMA),'ISSUER_STORED_CANCEL_PAIR')
    (validate_cancel_view if template_cancel else validate_policy)(policy)
    submission._fields(plan,'purpose proposal_sha256 context orders commands issued_at_ms expires_at_ms notional')
    submission._digest(plan['proposal_sha256']);at=submission._integer(plan['issued_at_ms'])
    from live.execution.demo_issuer_policy import current_cancellation_key
    require(not template_cancel or plan['purpose']=='cancellation','ISSUER_STORED_CANCEL_ONLY')
    key=(current_cancellation_key if template_cancel else current_key)(policy,registry,at);expiry=submission._integer(plan['expires_at_ms'])
    require(at<expiry<=min(at+policy['grant_ttl_ms'],policy['valid_until_ms'],key['valid_until_ms']),'ISSUER_STORED_LIFETIME')
    require(plan['purpose'] in ('submission','cancellation'),'ISSUER_STORED_PURPOSE')
    common_context(plan['context'],policy)
    require(delegated==('delegated_admission_sha256' in plan['context']),
        'ISSUER_STORED_DELEGATION_PAIR')
    if delegated:
        compact=value['delegation']
        future=compact.get('schema')=='DEMO_GOLD_DELEGATED_ISSUER_RESERVATION_V2'
        submission._fields(compact,'schema issuer_anchor enable_issuer_anchor delegated_admission_sha256 enable_sha256 '
            'delegation_sha256 base_policy_sha256'+(' continuity_event_sha256' if future else ''))
        require(compact['schema'] in ('DEMO_GOLD_DELEGATED_ISSUER_RESERVATION_V1','DEMO_GOLD_DELEGATED_ISSUER_RESERVATION_V2')
            and plan['purpose']=='submission' and policy['schema']==GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA,
            'ISSUER_STORED_DELEGATION_SCOPE')
        for name in ('delegated_admission_sha256','enable_sha256','delegation_sha256','base_policy_sha256'):
            submission._digest(compact[name])
        if future:submission._digest(compact['continuity_event_sha256'])
        require(compact['delegated_admission_sha256']==plan['context']['delegated_admission_sha256']
            and policy['policy_id']=='gold-delegated-'+compact['delegated_admission_sha256'],
            'ISSUER_STORED_DELEGATION_BINDING')
    if plan['purpose']=='submission':
        if policy['schema'] in GOLD_GROUP_POLICY_SCHEMAS:
            require(plan['context'].get('execution_constraints_sha256')==policy['bindings']['execution_constraints_sha256'],
                    'ISSUER_GROUP_STORED_CONSTRAINTS')
        schema=submission_batch_schema(policy)
        submission.validate_context(plan['context']);submission.validate_orders(plan['orders'],schema=schema)
        require(plan['commands']==[] and submission.batch_sha256(plan['orders'],schema=schema)==plan['context']['batch_sha256'],'ISSUER_STORED_BATCH')
        calculated=sum((amount(o['quantity'])*amount(o['price']) for o in plan['orders']),Decimal(0))
        require(amount(plan['notional'])==calculated,'ISSUER_STORED_NOTIONAL')
        return [o['newClientOrderId'] for o in plan['orders']]
    schema=cancellation_batch_schema(policy)
    cancellation.validate_context(plan['context']);cancellation.validate_commands(plan['commands'],schema=schema)
    require(plan['orders']==[] and plan['notional']=='0' and
            cancellation.batch_sha256(plan['commands'],schema=schema)==plan['context']['cancel_batch_sha256'],'ISSUER_STORED_CANCEL')
    return [o['origClientOrderId'] for o in plan['commands']]


def verify_delegation_history(value,rows):
    compact=value['delegation'];policy=value['policy']
    if compact['schema']=='DEMO_GOLD_DELEGATED_ISSUER_RESERVATION_V1':
        from live.execution.demo_gold_delegation_enable import verify_history_prefix
        verify_history_prefix(compact['issuer_anchor'],rows,policy=policy)
        verify_history_prefix(compact['enable_issuer_anchor'],rows,policy=policy)
        return
    from live.execution.demo_gold_future_template import PERMISSION_FIELDS
    from live.execution.demo_issuer_group_continuity import GROUP_KINDS,verify_history_prefix,verify_group_change_history
    matched=[r for r in rows if r['kind'] in GROUP_KINDS and r['record_hash']==compact['continuity_event_sha256']]
    require(len(matched)==1,'ISSUER_STORED_CONTINUITY_REQUIRED')
    template=verify_group_change_history(matched[0])['future_template']
    require(_digest(template)==compact['base_policy_sha256'],'ISSUER_STORED_TEMPLATE_BINDING')
    expected={k:json.loads(encoded(template[k])) for k in PERMISSION_FIELDS.split()}
    expected.update(schema=GOLD_STRATEGY_EXIT_BUDGET_POLICY_SCHEMA,policy_id='gold-delegated-'+compact['delegated_admission_sha256'])
    expected['bindings']['execution_constraints_sha256']=value['plan']['context']['execution_constraints_sha256']
    require(policy==expected,'ISSUER_STORED_TEMPLATE_DERIVATION')
    issued=[r for r in rows if r['kind']=='DELEGATION_V2_SIGNED'
        and r['payload']['artifact_sha256']==compact['delegation_sha256']]
    require(len(issued)==1 and issued[0]['payload']['artifact']['future_template']==template
        and issued[0]['payload']['artifact']['continuity_event_sha256']==matched[0]['record_hash'],
        'ISSUER_STORED_NATIVE_DELEGATION_REQUIRED')
    for name in ('issuer_anchor','enable_issuer_anchor'):
        verify_history_prefix(compact[name],rows,authorization=policy)
        require(compact[name]['records']>=issued[0]['seq'],'ISSUER_STORED_DELEGATION_ANCHOR')


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
        previous='';last_at=0;result=[];reserved={};signed=set();claims=[];policies={};groups={}
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
                from live.execution.demo_gold_exact_scope import verify_history
                verify_history(policy,result)
                require_group_route(groups,payload)
                if 'delegation' in payload:
                    verify_delegation_history(payload,result)
                if 'cancellation' in payload:
                    from live.execution.demo_issuer_group_continuity import verify_cancellation_history
                    verify_cancellation_history(payload,result)
                require(identity not in reserved and plan['proposal_sha256']==identity and plan['issued_at_ms']==at,'ISSUER_STORE_RESERVATION')
                binding=_digest(policy)
                require(policies.get(policy['policy_id'],binding)==binding,'ISSUER_POLICY_ID_REBOUND')
                policies[policy['policy_id']]=binding;reserved[identity]=payload
                consume_group(groups,policy,plan)
                claims.extend((policy['account_ref_sha256'],plan['purpose'],client,identity) for client in ids)
            elif row['kind']=='DELEGATION_V2_SIGNED':
                from live.execution.demo_issuer_group_continuity import verify_delegation_sign_history
                verify_delegation_sign_history({**record,'record_hash':row['record_hash']},result)
            elif row['kind'] in ('GROUP_INITIALIZED','GROUP_EXIT_RESERVE_MIGRATED'):
                from live.execution.demo_issuer_group_continuity import apply_group_event
                apply_group_event(groups,{**record,'record_hash':row['record_hash']},result)
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
    def reserve(self,plan,*,policy,registry,delegation=None,cancellation=None):
        require(not self.read_only,'ISSUER_READ_ONLY')
        value={'plan':plan,'policy':policy,'registry':registry}
        if delegation is not None:
            value['delegation']=delegation
        if cancellation is not None:
            value['cancellation']=cancellation
        value=json.loads(encoded(value))
        ids=validate_reservation(value)
        plan,policy=value['plan'],value['policy']
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                rows=self._rows();previous=[row['payload'] for row in rows if row['kind']=='RESERVED']
                from live.execution.demo_gold_exact_scope import verify_history
                verify_history(policy,rows)
                if 'delegation' in value:
                    # The public installation anchor is a verified prefix,
                    # never a replacement budget or a frozen DB-byte image.
                    # Check it inside the same transaction that claims IDs and
                    # consumes signed AND unsigned original reservations.
                    verify_delegation_history(value,rows)
                if 'cancellation' in value:
                    from live.execution.demo_issuer_group_continuity import verify_cancellation_history
                    verify_cancellation_history(value,rows)
                from live.execution.demo_issuer_group_continuity import reduce_groups
                groups=reduce_groups(rows)
                require_group_route(groups,value)
                consume_group(groups,policy,plan)
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

    def group_change(self,operation,*,documents,registry,installed_authorization,key,now_ms,clock_ms=None,recheck=None):
        """Commit one separately confirmed change on this existing original chain."""
        from live.execution import demo_issuer_group_continuity as continuity
        require(not self.read_only,'ISSUER_READ_ONLY')
        submission._integer(now_ms)
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                rows=self._rows();identity=_digest(operation)
                require(not any(r['proposal_sha256']==identity for r in rows),'ISSUER_ADMIN_OPERATION_EXISTS')
                certificate=continuity.authorize_group_change(operation,documents=documents,registry=registry,
                    installed_authorization=installed_authorization,rows=rows,now_ms=now_ms)
                certificate=continuity.sign_group_certificate(certificate,key,now_ms=now_ms)
                at=continuity.final_group_time(certificate,started_at_ms=now_ms,clock_ms=clock_ms)
                kind='GROUP_INITIALIZED' if operation['subject']['action']=='INIT_SCOPE' else 'GROUP_EXIT_RESERVE_MIGRATED'
                self._append(kind,identity,certificate,at,rows)
                changed=self._rows();result=changed[-1]
                if recheck is not None:recheck()
                continuity.final_group_time(certificate,started_at_ms=now_ms,clock_ms=clock_ms,last_at_ms=at)
                self.db.commit()
                return result
            except BaseException:self.db.rollback();raise

    def sign_delegation(self,operation,*,documents,registry,installed_template,key,now_ms,clock_ms=None,recheck=None):
        """Sign only the installed template after its native continuity commit."""
        from live.execution import demo_issuer_group_continuity as continuity
        require(not self.read_only,'ISSUER_READ_ONLY');submission._integer(now_ms)
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                rows=self._rows();identity=_digest(operation)
                require(not any(r['proposal_sha256']==identity for r in rows),'ISSUER_ADMIN_OPERATION_EXISTS')
                receipt=continuity.authorize_delegation_sign(operation,documents=documents,registry=registry,
                    installed_template=installed_template,rows=rows,now_ms=now_ms)
                receipt=continuity.sign_delegation_receipt(receipt,key,now_ms=now_ms)
                at=continuity.final_sign_time(receipt,started_at_ms=now_ms,clock_ms=clock_ms,rows=rows)
                self._append('DELEGATION_V2_SIGNED',identity,receipt,at,rows)
                changed=self._rows();result=changed[-1]
                if recheck is not None:recheck()
                continuity.final_sign_time(receipt,started_at_ms=now_ms,clock_ms=clock_ms,rows=rows,last_at_ms=at)
                self.db.commit()
                return result
            except BaseException:self.db.rollback();raise

    def query_admin(self,operation_sha256,*,purpose):
        from live.execution.demo_issuer_group_continuity import GROUP_KINDS
        submission._digest(operation_sha256)
        require(purpose in ('GROUP_CONTINUITY','DELEGATION_V2_SIGN'),'ISSUER_ADMIN_PURPOSE')
        kinds=GROUP_KINDS if purpose=='GROUP_CONTINUITY' else ('DELEGATION_V2_SIGNED',)
        rows=[r for r in self.snapshot() if r['kind'] in kinds and r['proposal_sha256']==operation_sha256]
        require(len(rows)<=1,'ISSUER_ADMIN_OPERATION_AMBIGUOUS')
        return rows[0] if rows else None

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
