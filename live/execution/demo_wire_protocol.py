"""Exact Demo wire facts. An ACK identity is not a fill or a cash posting."""
from decimal import Decimal
import hashlib
import re

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.validation.demo_submission import validate_orders

UNKNOWN_REASONS = ('HTTP_UNCERTAIN','REQUEST_FAILED','RESPONSE_INVALID','RESPONSE_TOO_LARGE','IDENTITY_MISMATCH','QUERY_CLOCK_INCONSISTENT')
ACK_FIELDS = frozenset('symbol orderId clientOrderId status side origQty price positionSide reduceOnly timeInForce type executedQty cumQty cumQuote avgPrice updateTime time'.split())
AMOUNTS = frozenset('origQty price executedQty cumQty cumQuote avgPrice'.split())
STATUSES = frozenset(('NEW','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','EXPIRED_IN_MATCH'))


def ack_identity(data, order):
    validate_orders([order])
    if type(data) is not dict or not {'symbol','orderId','clientOrderId'}<=set(data):
        raise ValueError('RESPONSE_INVALID')
    result={k:v for k,v in data.items() if k in ACK_FIELDS}
    vid=data['orderId']
    if (type(vid) not in (int,str) or re.fullmatch('[1-9][0-9]{0,18}',str(vid)) is None
        or int(vid)>2**63-1):raise ValueError('RESPONSE_INVALID')
    result['orderId']=str(vid)
    for key,expected in [('symbol',order['symbol']),('clientOrderId',order['newClientOrderId']),
                         *[(k,order[k]) for k in ('side','positionSide','reduceOnly','timeInForce','type') if k in data]]:
        if type(result[key]) is not type(expected) or result[key]!=expected:raise ValueError('IDENTITY_MISMATCH')
    for key in AMOUNTS & set(result):
        v=result[key]
        if type(v) is not str or re.fullmatch(r'\d{1,24}(\.\d{1,24})?',v) is None:
            raise ValueError('RESPONSE_INVALID')
    for key in ('origQty','price'):
        expected='quantity' if key=='origQty' else key
        if key in result and Decimal(result[key])!=Decimal(order[expected]):raise ValueError('IDENTITY_MISMATCH')
    quantities=[Decimal(result[k]) for k in ('executedQty','cumQty') if k in result]
    if any(q>Decimal(order['quantity']) for q in quantities) or len(set(quantities))>1:raise ValueError('RESPONSE_INVALID')
    if 'status' in result:
        if result['status'] not in STATUSES:raise ValueError('RESPONSE_INVALID')
        for qty in quantities:
            if (result['status']=='NEW' and qty!=0 or result['status']=='FILLED' and qty!=Decimal(order['quantity'])
                or result['status']=='PARTIALLY_FILLED' and not 0<qty<Decimal(order['quantity'])):
                raise ValueError('RESPONSE_INVALID')
    for key in ('time','updateTime'):
        if key in result and (type(result[key]) is not int or not 0<result[key]<=9007199254740991):
            raise ValueError('RESPONSE_INVALID')
    if 'time' in result and 'updateTime' in result and result['time']>result['updateTime']:
        raise ValueError('RESPONSE_INVALID')
    return result


def outcome_for_response(order, http_status, raw):
    result={'status':'UNKNOWN','http_status':http_status,'response_sha256':None,'code':None,'order':None,'reason':'REQUEST_FAILED'}
    if type(http_status) is not int or not 100<=http_status<=599 or type(raw) is not bytes:
        result['http_status']=None
        return result
    if len(raw)>65536:
        result['reason']='RESPONSE_TOO_LARGE';return result
    result['response_sha256']=hashlib.sha256(raw).hexdigest()
    try:
        data=load_json_without_duplicate_keys(raw)
        if type(data) is dict and type(data.get('code')) is int and -(2**31)<=data['code']<2**31:
            result['code']=data['code']
        if not 200<=http_status<300:
            result['reason']='HTTP_UNCERTAIN';return result
        result['order']=ack_identity(data,order)
        result.update(status='ACKNOWLEDGED',reason='ORDER_IDENTITY_OBSERVED')
    except Exception as exc:
        result['reason']='IDENTITY_MISMATCH' if str(exc)=='IDENTITY_MISMATCH' else 'RESPONSE_INVALID'
    return result


def validate_outcome(value, order):
    if type(value) is not dict or set(value)!=set('status http_status response_sha256 code order reason'.split()):
        raise ValueError('DEMO_WIRE_OUTCOME')
    http=value['http_status'];code=value['code'];digest=value['response_sha256']
    if http is not None and (type(http) is not int or not 100<=http<=599):raise ValueError('DEMO_WIRE_OUTCOME')
    if code is not None and (type(code) is not int or not -(2**31)<=code<2**31):raise ValueError('DEMO_WIRE_OUTCOME')
    if digest is not None and (type(digest) is not str or re.fullmatch('[a-f0-9]{64}',digest) is None):raise ValueError('DEMO_WIRE_OUTCOME')
    if value['status'] in ('ACKNOWLEDGED','FOUND'):
        reason='ORDER_IDENTITY_OBSERVED' if value['status']=='ACKNOWLEDGED' else 'ORDER_SNAPSHOT_OBSERVED'
        if (http is None or not 200<=http<300 or digest is None or value['reason']!=reason
            or ack_identity(value['order'],order)!=value['order']):raise ValueError('DEMO_WIRE_OUTCOME')
        if value['status']=='FOUND' and not QUERY_FIELDS<=set(value['order']):raise ValueError('DEMO_WIRE_QUERY_INCOMPLETE')
    elif value['status']!='UNKNOWN' or value['order'] is not None or value['reason'] not in UNKNOWN_REASONS:
        raise ValueError('DEMO_WIRE_OUTCOME')
    return value


QUERY_FIELDS=frozenset('symbol orderId clientOrderId status side origQty price positionSide reduceOnly timeInForce type executedQty time updateTime'.split())


def outcome_for_query(order,http_status,raw):
    result=outcome_for_response(order,http_status,raw)
    if result['status']=='ACKNOWLEDGED':
        if not QUERY_FIELDS<=set(result['order']):
            result.update(status='UNKNOWN',order=None,reason='RESPONSE_INVALID')
        else:result.update(status='FOUND',reason='ORDER_SNAPSHOT_OBSERVED')
    return result
