"""Small unverified display snapshots of Demo order recovery, never cash authority."""
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from services.governance.json_integrity import load_json_without_duplicate_keys

SCHEMA='DEMO_OMS_STATUS_V1'
PHASES=('INPUTS_UNRESOLVED','AWAITING_PROJECTION','PROJECTION_INTERRUPTED','OMS_TERMINAL_PROJECTED')
MAX_BYTES=65536


def resolution_digest(value):
    """Match the executor's exact compact ASCII JSON plan identity."""
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _hash(value):
    if type(value) is not str or re.fullmatch('[a-f0-9]{64}',value) is None:raise ValueError('OMS_STATUS_HASH')


def _time(value):
    if type(value) is not int or not 0<value<=8640000000000000:raise ValueError('OMS_STATUS_TIME')


def _quantity(value):
    if type(value) is not str or re.fullmatch(r'\d{1,24}(\.\d{1,24})?',value) is None:raise ValueError('OMS_STATUS_QUANTITY')
    return Decimal(value)


def validate(raw):
    names='schema environment scope phase observed_at_ms order_count matched_orders projected_orders orders issues reservation_sha256 proposal_sha256 plan_sha256 import_sha256 manifest_hash journal_tail source_verified authoritative submission_enabled economic_authority cash_reconciled'
    if type(raw) is not dict or set(raw)!=set(names.split()):raise ValueError('OMS_STATUS_FIELDS')
    if raw['schema']!=SCHEMA or raw['environment']!='BINANCE_USDM_DEMO' or raw['scope']!='TECHNICAL_VALIDATION_ONLY' or raw['phase'] not in PHASES:
        raise ValueError('OMS_STATUS_SCHEMA')
    _time(raw['observed_at_ms'])
    for key in ('source_verified','authoritative','submission_enabled','economic_authority','cash_reconciled'):
        if raw[key] is not False:raise ValueError('OMS_STATUS_UNSUPPORTED_AUTHORITY')
    for key in ('reservation_sha256','proposal_sha256','plan_sha256','import_sha256'):_hash(raw[key])
    for key in ('manifest_hash','journal_tail'):
        if raw[key] is not None:_hash(raw[key])
    n=raw['order_count'];matched=raw['matched_orders'];done=raw['projected_orders']
    if type(n) is not int or not 1<=n<=20 or type(matched) is not int or not 0<=matched<=n:
        raise ValueError('OMS_STATUS_COUNT')
    if type(raw['orders']) is not list or len(raw['orders'])!=matched:raise ValueError('OMS_STATUS_ORDER_COUNT')
    ids=set();venues=set()
    for row in raw['orders']:
        if type(row) is not dict or set(row)!=set('client_order_id venue_order_id terminal_status filled_quantity query_native_status private_event_count last_received_at_ms query_record_sha256'.split()):
            raise ValueError('OMS_STATUS_ORDER_FIELDS')
        client=row['client_order_id'];venue=row['venue_order_id']
        if (type(client) is not str or re.fullmatch(r'[.A-Z:/a-z0-9_-]{1,36}',client) is None or client in ids or
            type(venue) is not str or re.fullmatch('[1-9][0-9]{0,18}',venue) is None or int(venue)>2**63-1 or venue in venues):
            raise ValueError('OMS_STATUS_ORDER_ID')
        ids.add(client);venues.add(venue);_quantity(row['filled_quantity'])
        if (row['terminal_status'] not in ('FILLED','CANCELED','EXPIRED') or
            ('EXPIRED' if row['query_native_status']=='EXPIRED_IN_MATCH' else row['query_native_status'])!=row['terminal_status']):
            raise ValueError('OMS_STATUS_TERMINAL')
        if row['terminal_status']=='FILLED' and _quantity(row['filled_quantity'])<=0:raise ValueError('OMS_STATUS_FILLED_QUANTITY')
        if type(row['private_event_count']) is not int or not 1<=row['private_event_count']<=10000:
            raise ValueError('OMS_STATUS_EVENT_COUNT')
        _time(row['last_received_at_ms']);_hash(row['query_record_sha256'])
        if row['last_received_at_ms']>raw['observed_at_ms']:raise ValueError('OMS_STATUS_FUTURE_RECEIPT')
    issues=raw['issues']
    if type(issues) is not list or len(issues)>64 or len(set(issues))!=len(issues) or any(type(v) is not str or re.fullmatch('[A-Z_]{1,80}',v) is None for v in issues):
        raise ValueError('OMS_STATUS_ISSUES')
    if raw['phase']=='INPUTS_UNRESOLVED':
        if not issues or done is not None:raise ValueError('OMS_STATUS_INCOMPLETE')
    else:
        if matched!=n or raw['manifest_hash'] is None or raw['journal_tail'] is None:raise ValueError('OMS_STATUS_MATCHED_COUNT')
        if raw['phase']=='PROJECTION_INTERRUPTED':
            if issues!=['OMS_PROJECTION_INTERRUPTED']:raise ValueError('OMS_STATUS_INTERRUPTION')
        elif issues:raise ValueError('OMS_STATUS_SUCCESS_ISSUES')
        if raw['phase']=='OMS_TERMINAL_PROJECTED':
            if type(done) is not int or done!=n:raise ValueError('OMS_STATUS_PROJECTED_COUNT')
        elif done is not None:raise ValueError('OMS_STATUS_UNCONFIRMED_PROJECTION')
    return raw


def build_status(prepared,plan,*,observed_at_ms,result=None,interrupted=False):
    _time(observed_at_ms)
    if (plan['schema']!='DEMO_OMS_RECEIPT_RESOLUTION_V1' or plan['status'] not in ('UNRESOLVED','READY_FOR_OMS_PROJECTION') or
        any(plan[k]!=prepared[k] for k in ('reservation_sha256','proposal_sha256')) or
        any(plan[k] is not False for k in ('submission_enabled','source_verified','economic_authority','cash_reconciled')) or
        type(interrupted) is not bool or interrupted and result is not None):raise ValueError('OMS_STATUS_PLAN_BINDING')
    fixed={o['newClientOrderId']:o for o in prepared['orders']}
    if len(fixed)!=len(prepared['orders']):raise ValueError('OMS_STATUS_FIXED_IDS')
    ready=plan['status']=='READY_FOR_OMS_PROJECTION'
    if ready and (type(plan['resolution_at_ms']) is not int or plan['resolution_at_ms']>observed_at_ms):
        raise ValueError('OMS_STATUS_FUTURE_QUERY')
    digest=resolution_digest(plan)
    if result is not None and (not ready or result.get('schema')!=plan['schema'] or result.get('status')!='OMS_TERMINAL_PROJECTED' or
        result.get('plan_sha256')!=digest or type(result.get('orders')) is not int or result['orders']!=len(fixed) or
        any(result.get(k) is not False for k in ('submission_enabled','economic_authority','cash_reconciled'))):
        raise ValueError('OMS_STATUS_RESULT_BINDING')
    if interrupted and not ready:raise ValueError('OMS_STATUS_INTERRUPTION_WITHOUT_PLAN')
    rows=[]
    for row in plan['orders']:
        client=row['client_order_id'];events=row['events']
        if client not in fixed or not events or _quantity(row['filled_quantity'])>_quantity(fixed[client]['quantity']) or events[-1]['filled_quantity']!=row['filled_quantity']:
            raise ValueError('OMS_STATUS_PRIVATE_BINDING')
        if row['terminal_status']=='FILLED' and _quantity(row['filled_quantity'])!=_quantity(fixed[client]['quantity']):
            raise ValueError('OMS_STATUS_FILLED_QUANTITY')
        rows.append({k:row[k] for k in ('client_order_id','venue_order_id','terminal_status','filled_quantity','query_native_status','query_record_sha256')}|
                    {'private_event_count':len(events),'last_received_at_ms':events[-1]['received_at_ms']})
    phase='OMS_TERMINAL_PROJECTED' if result is not None else 'PROJECTION_INTERRUPTED' if interrupted else 'AWAITING_PROJECTION' if ready else 'INPUTS_UNRESOLVED'
    return validate({'schema':SCHEMA,'environment':'BINANCE_USDM_DEMO','scope':'TECHNICAL_VALIDATION_ONLY',
        'phase':phase,'observed_at_ms':observed_at_ms,'order_count':len(fixed),'matched_orders':len(rows),
        'projected_orders':len(fixed) if result is not None else None,'orders':rows,
        'issues':['OMS_PROJECTION_INTERRUPTED'] if interrupted else list(plan['issues']),
        **{k:plan[k] for k in ('reservation_sha256','proposal_sha256','import_sha256','manifest_hash','journal_tail')},
        'plan_sha256':digest,'source_verified':False,'authoritative':False,'submission_enabled':False,
        'economic_authority':False,'cash_reconciled':False})


def save(directory,raw):
    validate(raw);directory=Path(directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    data=(json.dumps(raw,sort_keys=True,indent=2)+'\n').encode()
    if len(data)>MAX_BYTES:raise ValueError('OMS_STATUS_SIZE')
    path=directory/(hashlib.sha256(data).hexdigest()+'.json')
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    except FileExistsError:
        if path.is_symlink() or path.read_bytes()!=data:raise ValueError('OMS_STATUS_COLLISION') from None
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=directory,delete=False) as f:
            temporary=Path(f.name);json.dump({'sha256':path.stem},f);f.flush();os.fsync(f.fileno())
        os.replace(temporary,directory/'latest.json')
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return path


def load_latest(directory):
    directory=Path(directory);pointer=directory/'latest.json'
    if pointer.is_symlink():raise ValueError('OMS_STATUS_SYMLINK')
    with pointer.open('rb') as f:body=f.read(1025)
    if len(body)>1024:raise ValueError('OMS_STATUS_POINTER_SIZE')
    value=load_json_without_duplicate_keys(body)
    if type(value) is not dict or set(value)!={'sha256'}:raise ValueError('OMS_STATUS_POINTER')
    _hash(value['sha256']);path=directory/(value['sha256']+'.json')
    if path.is_symlink():raise ValueError('OMS_STATUS_SYMLINK')
    with path.open('rb') as f:body=f.read(MAX_BYTES+1)
    if len(body)>MAX_BYTES or hashlib.sha256(body).hexdigest()!=value['sha256']:raise ValueError('OMS_STATUS_DIGEST')
    return validate(load_json_without_duplicate_keys(body))
