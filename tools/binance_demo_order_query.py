"""Operator-owned bounded Demo order GETs. Default makes no network requests."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import get_args
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal

from quantos.contracts.binance_demo import DEMO_REST_BASE
from quantos.contracts.binance_demo_orders import QueryPlan, QueryCapture, QueryResult, OrderObservation, QueryReason
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory_store import digest
from services.research.demo_execution_journal import DemoExecutionJournal
from services.research.demo_order_recovery import build_query_plan, save_capture, load_capture, MAX_CAPTURE_BYTES
from tools.binance_demo_readiness import NoRedirect, ProbeFailure


def now_ms():
    return time.time_ns() // 1_000_000


def get_json(path, *, params, headers):
    if path not in ('/fapi/v1/time','/fapi/v1/order'):
        raise ValueError('ENDPOINT_NOT_ALLOWED')
    request=urllib.request.Request(DEMO_REST_BASE+path+('?' + params if params else ''), headers=headers, method='GET')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=10) as response:
            raw=response.read(16_385)
            if not 200 <= response.status < 300: raise ProbeFailure('REQUEST_FAILED')
    except urllib.error.HTTPError as exc:
        reason='RATE_LIMITED' if exc.code in (418,429) else 'AUTHENTICATION_REJECTED' if exc.code in (401,403) else 'REQUEST_FAILED'
        try:
            body=load_json_without_duplicate_keys(exc.read(16_385).decode())
            if reason=='REQUEST_FAILED' and isinstance(body,dict) and body.get('code')==-2013:
                reason='ORDER_NOT_FOUND_UNCONFIRMED'
        except Exception:
            pass
        finally:
            exc.close()
        raise ProbeFailure(reason) from None
    except ProbeFailure:
        raise
    except Exception:
        raise ProbeFailure('REQUEST_FAILED') from None
    if len(raw)>16_384: raise ProbeFailure('RESPONSE_TOO_LARGE')
    try:
        body=load_json_without_duplicate_keys(raw.decode())
        if not isinstance(body,dict): raise ValueError
        return body, hashlib.sha256(raw).hexdigest()
    except (ValueError,UnicodeError):
        raise ProbeFailure('RESPONSE_INVALID') from None


def _reply(get, path, **kwargs):
    value=get(path,**kwargs)
    # Injected offline test transports return dictionaries. Real transport keeps
    # the digest of original bytes, never signed URLs or headers.
    return value if isinstance(value,tuple) else (value,digest(value))


def _reason(exc):
    return str(exc) if isinstance(exc,ProbeFailure) and str(exc) in get_args(QueryReason) else 'REQUEST_FAILED'


def _observation(raw, fixed, symbol):
    try:
        if (raw['symbol']!=symbol or raw['clientOrderId']!=fixed.client_order_id
            or type(raw['orderId']) is not int or str(raw['orderId'])!=fixed.venue_order_id
            or raw['side']!=fixed.side or raw['type']!='LIMIT' or raw['timeInForce']!='GTX'
            or raw['positionSide']!='BOTH' or raw['reduceOnly'] is not False
            or raw.get('closePosition',False) is not False or raw.get('origType','LIMIT')!='LIMIT'
            or not isinstance(raw['origQty'],str) or not isinstance(raw['price'],str)
            or Decimal(raw['origQty'])!=Decimal(fixed.quantity) or Decimal(raw['price'])!=Decimal(fixed.price)):
            raise ProbeFailure('IDENTITY_MISMATCH')
        return OrderObservation(status=raw['status'], executed_quantity=raw['executedQty'],
            created_at_ms=raw['time'],updated_at_ms=raw['updateTime'])
    except ProbeFailure:
        raise
    except Exception:
        raise ProbeFailure('RESPONSE_INVALID') from None


def failure_capture(plan, reason, *, started, completed, provenance='OPERATOR_DEMO_REST'):
    return QueryCapture(plan=plan,provenance=provenance,started_at_ms=started,completed_at_ms=completed,
        queries=[QueryResult(order_id=o.order_id,outcome='UNKNOWN',reason=reason,
                             requested_at_ms=started,received_at_ms=completed) for o in plan.orders])


def collect(plan, *, get=None, clock_ms=None, account_read=False, environ=None, provenance='OPERATOR_DEMO_REST'):
    plan=QueryPlan.model_validate(plan.model_dump())
    get,clock_ms=get or get_json,clock_ms or now_ms
    start=clock_ms(); offset=None; results=[]
    if not account_read:
        return QueryCapture(plan=plan,provenance=provenance,started_at_ms=start,completed_at_ms=start,
            queries=[QueryResult(order_id=o.order_id,outcome='NOT_REQUESTED',reason='ACCOUNT_NOT_REQUESTED',
                                 requested_at_ms=start,received_at_ms=start) for o in plan.orders])
    try:
        env=os.environ if environ is None else environ
        key=env.get('BINANCE_TESTNET_API_KEY','').strip();secret=env.get('BINANCE_TESTNET_API_SECRET','').strip()
        if not key or not secret: raise ProbeFailure('CREDENTIALS_MISSING')
        clock_data,_=_reply(get,'/fapi/v1/time',params='',headers={})
        received=clock_ms();server=clock_data.get('serverTime')
        if type(server) is not int or server<=0: raise ProbeFailure('RESPONSE_INVALID')
        offset=server-(start+received)//2
        if not 0<=received-start<=5000 or abs(offset)>2000: raise ProbeFailure('CLOCK_UNRELIABLE')
    except Exception as exc:
        return failure_capture(plan,_reason(exc),started=start,completed=clock_ms(),provenance=provenance)
    failed=False
    for fixed in plan.orders:
        at=clock_ms()
        if failed:
            results.append(QueryResult(order_id=fixed.order_id,outcome='SKIPPED',reason='PREVIOUS_QUERY_FAILED',requested_at_ms=at,received_at_ms=at))
            continue
        try:
            params=urllib.parse.urlencode({'symbol':plan.symbol,'origClientOrderId':fixed.client_order_id,'timestamp':at+offset,'recvWindow':5000})
            signature=hmac.new(secret.encode(),params.encode(),hashlib.sha256).hexdigest()
            raw,sha=_reply(get,'/fapi/v1/order',params=params+'&signature='+signature,headers={'X-MBX-APIKEY':key})
            observation=_observation(raw,fixed,plan.symbol)
            candidate=QueryResult(order_id=fixed.order_id,outcome='FOUND',reason='ORDER_QUERY_SUCCEEDED',
                requested_at_ms=at,received_at_ms=clock_ms(),observation=observation,response_sha256=sha)
            # Validate the found observation against the frozen parent before
            # publishing. Malformed venue state becomes an unknown attempt.
            QueryCapture(plan=plan.model_copy(update={'orders':[fixed]}),provenance=provenance,
                started_at_ms=start,completed_at_ms=candidate.received_at_ms,clock_offset_ms=offset,queries=[candidate])
            results.append(candidate)
        except Exception as exc:
            reason=_reason(exc) if isinstance(exc,ProbeFailure) else 'RESPONSE_INVALID'
            results.append(QueryResult(order_id=fixed.order_id,outcome='UNKNOWN',reason=reason,requested_at_ms=at,received_at_ms=clock_ms()))
            failed=True
    return QueryCapture(plan=plan,provenance=provenance,started_at_ms=start,completed_at_ms=clock_ms(),clock_offset_ms=offset,queries=results)


def collect_bounded(plan, *, account_read=False):
    start=now_ms()
    if not account_read: return collect(plan,account_read=False)
    names=['PATH','LANG','LC_ALL','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
           'http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE','SSL_CERT_DIR',
           'BINANCE_TESTNET_API_KEY','BINANCE_TESTNET_API_SECRET']
    try:
        result=subprocess.run([sys.executable,'-m','tools.binance_demo_order_query','--capture-worker'],
            cwd=Path(__file__).resolve().parents[1],env={k:os.environ[k] for k in names if k in os.environ},
            input=plan.model_dump_json().encode(),capture_output=True,timeout=35,check=True)
        if len(result.stdout)>MAX_CAPTURE_BYTES: raise ValueError
        capture=QueryCapture.model_validate_json(result.stdout)
        if capture.plan!=plan or capture.started_at_ms<start or capture.completed_at_ms>now_ms(): raise ValueError
        return capture
    except subprocess.TimeoutExpired:
        reason='REQUEST_TIMEOUT'
    except Exception:
        reason='REQUEST_FAILED'
    return failure_capture(plan,reason,started=start,completed=now_ms())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--journal',type=Path)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--account-read',action='store_true')
    parser.add_argument('--capture-worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    try:
        if args.capture_worker:
            raw=sys.stdin.buffer.read(MAX_CAPTURE_BYTES+1)
            if len(raw)>MAX_CAPTURE_BYTES: return 3
            print(collect(QueryPlan.model_validate_json(raw),account_read=True).model_dump_json())
            return 0
        if args.journal is None or args.output_dir is None: parser.error('--journal and --output-dir required')
        with DemoExecutionJournal(args.journal,read_only=True) as journal: plan=build_query_plan(journal)
        capture=collect_bounded(plan,account_read=args.account_read)
        identity=save_capture(args.output_dir,capture)
        print(json.dumps({'capture_hash':identity,'queries':len(capture.queries),
                          'found':sum(q.outcome=='FOUND' for q in capture.queries),'submission_enabled':False}))
        return 0 if all(q.outcome in ('FOUND','NOT_REQUESTED') for q in capture.queries) else 2
    except Exception:
        print(json.dumps({'status':'FAILED','reason':'DEMO_QUERY_INPUT_CAPTURE_OR_STORE_INVALID'}))
        return 3


if __name__=='__main__':
    raise SystemExit(main())
