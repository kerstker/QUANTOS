"""Private, fixed Demo transport for the verified prepared executor.

No SDK, configurable host, redirect, POST retry, float formatting or public API.
This is trusted boundary code; the coordinator owns verification and durable
send claims. Research/HTTP handlers must never import this transport.
"""
import hashlib
import hmac
from pathlib import Path
import re

from live.execution import demo_authority
from live.exchange.binance_futures import (
    _DemoNoRedirect as _NoRedirect, _demo_exact_encode_params,
    _demo_exact_post_once as _http_once, _demo_exact_get_once as _http_get_once,
    _demo_exact_delete_once as _http_delete_once,
)
from quantos.contracts.binance_demo import DEMO_REST_BASE
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.validation.demo_submission import validate_orders
from services.validation.demo_cancellation import validate_commands

_CREDENTIAL_FILE=Path('/etc/quantos/demo-submission/credentials.json')


def request_payload(order,timestamp_ms):
    validate_orders([order])
    if type(timestamp_ms) is not int or not 0<timestamp_ms<=9007199254740991:
        raise ValueError('DEMO_WIRE_TIMESTAMP')
    params={key:('true' if value else 'false') if type(value) is bool else value for key,value in order.items()}
    params.update(timestamp=timestamp_ms,recvWindow=5000)
    return _demo_exact_encode_params(params)


def request_sha256(order,timestamp_ms):
    return hashlib.sha256(b'POST\0'+DEMO_REST_BASE.encode()+b'/fapi/v1/order\0'+request_payload(order,timestamp_ms)).hexdigest()


def _credentials(account_ref):
    demo_authority.load_registry_for_executor()
    try:
        raw=load_json_without_duplicate_keys(demo_authority._read_root_owned_json(_CREDENTIAL_FILE))
        if type(raw) is not dict or set(raw)!=set(('api_key','api_secret')):raise ValueError()
        if any(type(v) is not str or re.fullmatch('[A-Za-z0-9]{16,128}',v) is None for v in raw.values()):raise ValueError()
        if hashlib.sha256(('BINANCE_USDM_DEMO:'+raw['api_key']).encode()).hexdigest()!=account_ref:raise ValueError()
        return raw['api_key'],raw['api_secret']
    except Exception:
        raise ValueError('DEMO_EXECUTOR_CREDENTIALS_UNAVAILABLE_OR_MISMATCH') from None


def _submit_claimed(order, *,timestamp_ms,account_ref,final_check):
    payload=request_payload(order,timestamp_ms)
    key,secret=_credentials(account_ref)
    body=payload+b'&signature='+hmac.new(secret.encode(),payload,hashlib.sha256).hexdigest().encode()
    headers={'X-MBX-APIKEY':key,'Content-Type':'application/x-www-form-urlencoded'}
    # Read current revocation/time/risk after loading credentials, immediately
    # before the one network attempt. Exceptions never trigger a retry.
    final_check()
    return _http_once(body,headers)


def _query_reserved(order, *,timestamp_ms,account_ref):
    validate_orders([order])
    if type(timestamp_ms) is not int or not 0<timestamp_ms<=9007199254740991:raise ValueError('DEMO_QUERY_TIME')
    key,secret=_credentials(account_ref)
    query=_demo_exact_encode_params({'symbol':order['symbol'],'origClientOrderId':order['newClientOrderId'],
        'timestamp':timestamp_ms,'recvWindow':5000})
    query+=b'&signature='+hmac.new(secret.encode(),query,hashlib.sha256).hexdigest().encode()
    return _http_get_once('/fapi/v1/order',query,{'X-MBX-APIKEY':key})


def cancellation_payload(command,timestamp_ms):
    validate_commands([command])
    if type(timestamp_ms) is not int or not 0<timestamp_ms<=9007199254740991:raise ValueError('DEMO_CANCEL_TIME')
    return _demo_exact_encode_params({**command,'timestamp':timestamp_ms,'recvWindow':5000})


def cancellation_sha256(command,timestamp_ms):
    return hashlib.sha256(b'DELETE\0'+DEMO_REST_BASE.encode()+b'/fapi/v1/order\0'+cancellation_payload(command,timestamp_ms)).hexdigest()


def _cancel_claimed(command,*,timestamp_ms,account_ref,final_check):
    query=cancellation_payload(command,timestamp_ms)
    key,secret=_credentials(account_ref)
    query+=b'&signature='+hmac.new(secret.encode(),query,hashlib.sha256).hexdigest().encode()
    final_check()
    return _http_delete_once(query,{'X-MBX-APIKEY':key})
