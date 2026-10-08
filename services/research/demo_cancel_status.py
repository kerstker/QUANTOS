"""Unverified cancellation display; response observations never close recovery."""
from services.research import demo_display_store
from services.research.demo_oms_status import resolution_digest,_hash,_time,_quantity
from services.validation.demo_cancellation import validate_commands,validate_context,batch_schema_for_envelope,GOLD_BATCH_SCHEMA,LEGACY_BATCH_SCHEMA

SCHEMA='DEMO_CANCEL_STATUS_V1'
GOLD_SCHEMA='DEMO_GOLD_CANCEL_STATUS_V1'


def validate(raw):
    names='schema environment scope phase observed_at_ms order_count claimed_orders response_orders unresolved_responses orders grant_sha256 reservation_sha256 snapshot_sha256 source_verified new_order_submission_enabled economic_authority cash_reconciled'
    if type(raw) is not dict or set(raw)!=set(names.split()):raise ValueError('CANCEL_STATUS_FIELDS')
    if (raw['schema'] not in (SCHEMA,GOLD_SCHEMA) or raw['environment']!='BINANCE_USDM_DEMO' or raw['scope']!='TECHNICAL_VALIDATION_ONLY' or
        raw['phase'] not in ('RESERVED_AWAITING_ATTEMPTS','ORIGINAL_ID_QUERY_RECOVERY_REQUIRED')):raise ValueError('CANCEL_STATUS_SCHEMA')
    _time(raw['observed_at_ms'])
    for k in ('source_verified','new_order_submission_enabled','economic_authority','cash_reconciled'):
        if raw[k] is not False:raise ValueError('CANCEL_STATUS_AUTHORITY')
    for k in ('grant_sha256','reservation_sha256','snapshot_sha256'):_hash(raw[k])
    for k in ('order_count','claimed_orders','response_orders','unresolved_responses'):
        if type(raw[k]) is not int or not 0<=raw[k]<=20:raise ValueError('CANCEL_STATUS_COUNT')
    rows=raw['orders'];n=raw['order_count'];claimed=responses=unknown=0
    if type(rows) is not list or not 1<=len(rows)==n<=20:raise ValueError('CANCEL_STATUS_ORDERS')
    validate_commands([{'symbol':r['symbol'],'origClientOrderId':r['client_order_id'],'orderId':r['venue_order_id']} for r in rows],schema=GOLD_BATCH_SCHEMA if raw['schema']==GOLD_SCHEMA else LEGACY_BATCH_SCHEMA)
    for row in rows:
        if type(row) is not dict or set(row)!=set('symbol client_order_id venue_order_id status claim_sha256 result_sha256 response_sha256 venue_status executed_quantity last_observed_at_ms'.split()):
            raise ValueError('CANCEL_STATUS_ORDER_FIELDS')
        _time(row['last_observed_at_ms'])
        if row['last_observed_at_ms']>raw['observed_at_ms']:raise ValueError('CANCEL_STATUS_FUTURE')
        for k in ('claim_sha256','result_sha256','response_sha256'):
            if row[k] is not None:_hash(row[k])
        if row['status']=='NOT_CLAIMED':
            if any(row[k] is not None for k in ('claim_sha256','result_sha256','response_sha256','venue_status','executed_quantity')):
                raise ValueError('CANCEL_STATUS_UNCLAIMED')
        elif row['status'] in ('CLAIMED_RESPONSE_UNKNOWN','RESPONSE_OBSERVED'):
            _hash(row['claim_sha256']);claimed+=1;responses+=row['result_sha256'] is not None
            if row['status']=='CLAIMED_RESPONSE_UNKNOWN':
                unknown+=1
                if row['venue_status'] is not None or row['executed_quantity'] is not None:raise ValueError('CANCEL_STATUS_UNKNOWN')
                if row['result_sha256'] is None and row['response_sha256'] is not None:raise ValueError('CANCEL_STATUS_RESPONSE')
            else:
                _hash(row['result_sha256']);_hash(row['response_sha256']);_quantity(row['executed_quantity'])
                if row['venue_status'] not in ('NEW','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','EXPIRED_IN_MATCH'):
                    raise ValueError('CANCEL_STATUS_RESPONSE')
                amount=_quantity(row['executed_quantity'])
                if (row['venue_status']=='NEW' and amount!=0 or
                    row['venue_status'] in ('FILLED','PARTIALLY_FILLED') and amount<=0):raise ValueError('CANCEL_STATUS_RESPONSE_QUANTITY')
        else:raise ValueError('CANCEL_STATUS_ORDER_STATE')
    if (claimed,responses,unknown)!=(raw['claimed_orders'],raw['response_orders'],raw['unresolved_responses']):
        raise ValueError('CANCEL_STATUS_COUNT_BINDING')
    if raw['phase']!=('ORIGINAL_ID_QUERY_RECOVERY_REQUIRED' if claimed else 'RESERVED_AWAITING_ATTEMPTS'):
        raise ValueError('CANCEL_STATUS_PHASE_BINDING')
    return raw


def build_status(records,*,observed_at_ms):
    _time(observed_at_ms)
    grants=[r for r in records if r['kind']=='GRANT']
    if not grants:return None
    grant=grants[-1];schema=batch_schema_for_envelope(grant['payload']['envelope'])
    context=validate_context(grant['payload']['context']);commands=validate_commands(grant['payload']['commands'],schema=schema)
    rows=[]
    for command in commands:
        claims=[r for r in records if r['kind']=='CLAIM' and r['payload']['grant_sha256']==grant['record_hash'] and
                r['payload']['client_order_id']==command['origClientOrderId']]
        if len(claims)>1:raise ValueError('CANCEL_STATUS_DUPLICATE_CLAIM')
        claim=claims[0] if claims else None
        results=[r for r in records if r['kind']=='RESULT' and claim is not None and r['payload']['claim_sha256']==claim['record_hash']]
        if len(results)>1:raise ValueError('CANCEL_STATUS_DUPLICATE_RESPONSE')
        result=results[0] if results else None;outcome=result['payload']['outcome'] if result else None
        seen=outcome is not None and outcome['status']=='ORDER_STATE_OBSERVED'
        rows.append({'symbol':command['symbol'],'client_order_id':command['origClientOrderId'],'venue_order_id':command['orderId'],
            'status':'RESPONSE_OBSERVED' if seen else 'CLAIMED_RESPONSE_UNKNOWN' if claim else 'NOT_CLAIMED',
            'claim_sha256':claim['record_hash'] if claim else None,'result_sha256':result['record_hash'] if result else None,
            'response_sha256':outcome['response_sha256'] if outcome else None,
            'venue_status':outcome['venue_status'] if seen else None,'executed_quantity':outcome['executed_quantity'] if seen else None,
            'last_observed_at_ms':(result or claim or grant)['at_ms']})
    claimed=sum(r['claim_sha256'] is not None for r in rows)
    return validate({'schema':GOLD_SCHEMA if schema==GOLD_BATCH_SCHEMA else SCHEMA,'environment':'BINANCE_USDM_DEMO','scope':'TECHNICAL_VALIDATION_ONLY',
        'phase':'ORIGINAL_ID_QUERY_RECOVERY_REQUIRED' if claimed else 'RESERVED_AWAITING_ATTEMPTS',
        'observed_at_ms':observed_at_ms,'order_count':len(rows),'claimed_orders':claimed,
        'response_orders':sum(r['result_sha256'] is not None for r in rows),
        'unresolved_responses':sum(r['status']=='CLAIMED_RESPONSE_UNKNOWN' for r in rows),'orders':rows,
        'grant_sha256':grant['record_hash'],'reservation_sha256':context['reservation_sha256'],'snapshot_sha256':resolution_digest(records),
        'source_verified':False,'new_order_submission_enabled':False,'economic_authority':False,'cash_reconciled':False})


def save(directory,raw):return demo_display_store.save(directory,raw,validator=validate)
def load_latest(directory):return demo_display_store.load_latest(directory,validator=validate)
