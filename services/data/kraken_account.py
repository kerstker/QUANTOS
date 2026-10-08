"""Offline Kraken account observations; no credential, network or execution surface.

Two balance/order sweeps detect changes but do not constitute an atomic snapshot.
A stable observation never grants account authority or order permission.
"""
from decimal import Context, Decimal, localcontext
import hashlib
import json
import math
import re

from services.data.kraken_public import require
from services.data.kraken_order_state import state as order_state

MAX_BYTES=4*1024*1024


def number(value, *, signed=False):
    require(type(value) in (str,int,Decimal),'KRAKEN_ACCOUNT_DECIMAL')
    text=str(value)
    pattern=r'-?\d{1,36}(\.\d{1,18})?' if signed else r'\d{1,36}(\.\d{1,18})?'
    require(bool(re.fullmatch(pattern,text)), 'KRAKEN_ACCOUNT_DECIMAL')
    return Decimal(text)


def canonical(value):
    if value==0:return '0'
    text=format(value,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def payload(body):
    require(isinstance(body,str) and len(body.encode())<=MAX_BYTES,'KRAKEN_ACCOUNT_BODY')
    def pairs(items):
        result={}
        for key,value in items:
            require(key not in result,'KRAKEN_ACCOUNT_DUPLICATE_JSON_KEY')
            result[key]=value
        return result
    def exact_number(text):
        # Preserve the existing finite-number bound without rounding the value
        # that is returned to accounting. JSON decimals are parsed only once.
        require(math.isfinite(float(text)),'KRAKEN_ACCOUNT_NONFINITE_JSON_NUMBER')
        return Decimal(text)
    def constant(_):
        raise ValueError('KRAKEN_ACCOUNT_NONSTANDARD_JSON_CONSTANT')
    value=json.loads(body,object_pairs_hook=pairs,parse_float=exact_number,parse_constant=constant)
    pending=[value]
    while pending:
        item=pending.pop()
        if isinstance(item,dict):pending.extend(item.keys());pending.extend(item.values())
        elif isinstance(item,list):pending.extend(item)
        elif isinstance(item,str):
            require(not any(0xD800<=ord(c)<=0xDFFF for c in item),'KRAKEN_ACCOUNT_JSON_SURROGATE')
    require(isinstance(value,dict) and value.get('error')==[] and isinstance(value.get('result'),dict),'KRAKEN_ACCOUNT_RESPONSE')
    return value['result']


def balances(body):
    rows=payload(body);require(len(rows)<=10000,'KRAKEN_ACCOUNT_SIZE')
    output={}
    for asset,row in sorted(rows.items()):
        require(bool(re.fullmatch(r'[A-Za-z0-9._]{1,48}',asset)) and isinstance(row,dict),'KRAKEN_ACCOUNT_ASSET')
        balance=number(row.get('balance'),signed=True);held=number(row.get('hold_trade'))
        credit=number(row.get('credit','0'));used=number(row.get('credit_used','0'))
        output[asset]={'balance':canonical(balance),'hold_trade':canonical(held),'credit':canonical(credit),
                       'credit_used':canonical(used),'available':canonical(balance+credit-used-held)}
    return output


def orders(body):
    raw=payload(body);require(set(raw)=={'open'} and isinstance(raw['open'],dict) and len(raw['open'])<=10000,'KRAKEN_OPEN_ORDERS')
    output=[]
    for oid,row in sorted(raw['open'].items()):
        require(bool(re.fullmatch(r'[A-Za-z0-9-]{1,80}',oid)) and isinstance(row,dict),'KRAKEN_ORDER_ID')
        descr=row.get('descr');require(isinstance(descr,dict),'KRAKEN_ORDER_DESCRIPTION')
        require(row.get('status') in ('pending','open') and descr.get('type') in ('buy','sell'),'KRAKEN_ORDER_STATUS')
        require(isinstance(descr.get('pair'),str) and bool(re.fullmatch(r'[A-Za-z0-9./]{1,48}',descr['pair'])),'KRAKEN_ORDER_PAIR')
        require(isinstance(descr.get('ordertype'),str) and bool(re.fullmatch(r'[a-z-]{1,40}',descr['ordertype'])),'KRAKEN_ORDER_TYPE')
        require(isinstance(descr.get('leverage'),str) and bool(re.fullmatch(r'[a-z0-9:.]{1,20}',descr['leverage'])),'KRAKEN_ORDER_LEVERAGE')
        volume=number(row.get('vol'));filled=number(row.get('vol_exec'))
        require(volume>0 and 0<=filled<=volume,'KRAKEN_ORDER_VOLUME')
        output.append({'order_id':oid,'status':row['status'],'pair':descr['pair'],'side':descr['type'],
                       'canonical_state':order_state(row['status'],volume,filled).value,
                       'order_type':descr['ordertype'],'price':canonical(number(descr.get('price'))),
                       'price2':canonical(number(descr.get('price2'))),'leverage':descr['leverage'],
                       'volume':canonical(volume),'filled':canonical(filled),'remaining':canonical(volume-filled)})
    return output


def assess(reads, *, as_of_ms):
    require(type(as_of_ms) is int and as_of_ms>0,'KRAKEN_ACCOUNT_AS_OF')
    require(isinstance(reads,list) and len(reads)==4,'KRAKEN_ACCOUNT_SWEEPS')
    expected=('BalanceEx','OpenOrders','BalanceEx','OpenOrders')
    reference=None;previous=0;receipts=[]
    for index,read in enumerate(reads):
        require(isinstance(read,dict) and read.get('endpoint')==expected[index] and
                read.get('params')=={'rebase_multiplier':'base'},'KRAKEN_ACCOUNT_READ_SCOPE')
        ref=read.get('account_ref_sha256')
        require(isinstance(ref,str) and bool(re.fullmatch(r'[a-f0-9]{64}',ref)),'KRAKEN_ACCOUNT_REFERENCE')
        if reference is None:reference=ref
        require(ref==reference,'KRAKEN_ACCOUNT_REFERENCE_CHANGED')
        sent,received=read.get('requested_at_ms'),read.get('received_at_ms')
        require(type(sent) is int and type(received) is int and 0<sent<=received<=as_of_ms and
                previous<=sent and received-sent<=30000,'KRAKEN_ACCOUNT_READ_CLOCK')
        previous=received
        body=read.get('body');require(isinstance(body,str) and len(body.encode())<=MAX_BYTES,'KRAKEN_ACCOUNT_BODY')
        receipts.append({'endpoint':read['endpoint'],'requested_at_ms':sent,'received_at_ms':received,
                         'body_sha256':hashlib.sha256(body.encode()).hexdigest()})
    require(previous-reads[0]['requested_at_ms']<=120000,'KRAKEN_ACCOUNT_SWEEP_DURATION')
    with localcontext(Context(prec=80)):
        before,after=balances(reads[0]['body']),balances(reads[2]['body'])
        open_before,open_after=orders(reads[1]['body']),orders(reads[3]['body'])
    issues=[]
    if before!=after:issues.append('BALANCES_CHANGED')
    if open_before!=open_after:issues.append('OPEN_ORDERS_CHANGED')
    if any(Decimal(v['credit_used'])!=0 or Decimal(v['balance'])<0 for v in after.values()):issues.append('CREDIT_EXPOSURE_PRESENT')
    if any(Decimal(v['available'])<0 for v in after.values()):issues.append('NEGATIVE_AVAILABLE_BALANCE')
    if any(v['leverage']!='none' for v in open_after):issues.append('MARGIN_ORDER_PRESENT')
    stale=as_of_ms-reads[0]['requested_at_ms']>120000
    if stale:issues.append('ACCOUNT_OBSERVATION_STALE')
    return {'schema':'KRAKEN_ACCOUNT_OBSERVATION_V1','status':'STALE' if stale else 'UNRESOLVED' if issues else 'OBSERVED_STABLE',
            'observed_at_ms':previous,'as_of_ms':as_of_ms,'account_ref_sha256':reference,
            'quantity_basis':'base','balances':after,'open_orders':open_after,'issues':issues,'reads':receipts,
            'atomic_snapshot':False,'cost_basis_status':'UNKNOWN','open_positions_status':'NOT_OBSERVED',
            'account_scope_verified':False,'submission_enabled':False,'authoritative':False}
