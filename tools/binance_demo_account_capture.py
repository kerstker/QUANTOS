"""Operator-only, bounded GET observations around an existing Demo account.

Nine GETs; no stream or account/order mutations. Default does not read secrets.
The account is sampled on both sides of position/config/order observations.
Stable values are not proof of an atomic snapshot or exact historic cost basis.
"""
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

from quantos.contracts.binance_demo import DEMO_REST_BASE
from quantos.contracts.binance_demo_account import AccountRead,DemoAccountCapture,normalize_account_read
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_account_baseline import save_capture,build_baseline,MAX_CAPTURE_BYTES
from tools.binance_demo_private_check import load_credentials
from tools.binance_demo_readiness import NoRedirect,ProbeFailure

READS=(('config_before','/fapi/v1/accountConfig'),('account_before','/fapi/v3/account'),
       ('symbols_before','/fapi/v1/symbolConfig'),('positions','/fapi/v3/positionRisk'),
       ('orders','/fapi/v1/openOrders'),('symbols_after','/fapi/v1/symbolConfig'),
       ('account_after','/fapi/v3/account'),('config_after','/fapi/v1/accountConfig'))


def now_ms():return time.time_ns()//1_000_000


def source_hash():
    root=Path(__file__).resolve().parents[1]
    paths=(Path(__file__),root/'quantos/contracts/binance_demo_account.py',
        root/'services/research/demo_account_baseline.py',root/'tools/binance_demo_private_check.py')
    return hashlib.sha256(b'\0'.join(p.read_bytes() for p in paths)).hexdigest()


def get_response(path,*,params,headers):
    if path not in {'/fapi/v1/time',*(p for _,p in READS)}:raise ProbeFailure('REQUEST_FAILED')
    if path=='/fapi/v1/time' and (params or headers):raise ProbeFailure('REQUEST_FAILED')
    request=urllib.request.Request(DEMO_REST_BASE+path+('?' + params if params else ''),headers=headers,method='GET')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=10) as response:
            if not 200<=response.status<300:raise ProbeFailure('REQUEST_FAILED')
            raw=response.read(2_000_001)
    except urllib.error.HTTPError as exc:
        status=exc.code;exc.close()
        raise ProbeFailure('RATE_LIMITED' if status in (418,429) else
            'AUTHENTICATION_REJECTED' if status in (401,403) else 'REQUEST_FAILED') from None
    except ProbeFailure:raise
    except Exception:raise ProbeFailure('REQUEST_FAILED') from None
    if len(raw)>2_000_000:raise ProbeFailure('RESPONSE_TOO_LARGE')
    try:return load_json_without_duplicate_keys(raw.decode()),hashlib.sha256(raw).hexdigest()
    except Exception:raise ProbeFailure('RESPONSE_INVALID') from None


def collect(*,credential_file,operator_read=False,get=None,clock_ms=None):
    get=get or get_response;clock_ms=clock_ms or now_ms
    start=clock_ms();reads=[];status='NOT_REQUESTED';reason='ACCOUNT_NOT_REQUESTED'
    if operator_read:
        try:
            key,secret=load_credentials(credential_file)
            sent=clock_ms();raw,_=get('/fapi/v1/time',params='',headers={});received=clock_ms()
            server=raw.get('serverTime') if isinstance(raw,dict) else None
            if type(server) is not int or server<=0:raise ProbeFailure('RESPONSE_INVALID')
            offset=server-(sent+received)//2
            if not 0<=received-sent<=5000 or abs(offset)>2000:raise ProbeFailure('CLOCK_UNRELIABLE')
            for kind,path in READS:
                at=clock_ms()
                query=urllib.parse.urlencode({'timestamp':at+offset,'recvWindow':5000})
                signature=hmac.new(secret.encode(),query.encode(),hashlib.sha256).hexdigest()
                raw,digest=get(path,params=query+'&signature='+signature,headers={'X-MBX-APIKEY':key})
                received=clock_ms()
                try:data=normalize_account_read(kind,raw)
                except Exception:raise ProbeFailure('RESPONSE_INVALID') from None
                reads.append(AccountRead(kind=kind,started_at_ms=at,received_at_ms=received,response_sha256=digest,data=data))
            status='CAPTURED';reason='CAPTURE_COMPLETE'
        except Exception as exc:
            status='FAILED';reason=str(exc) if isinstance(exc,ProbeFailure) else 'REQUEST_FAILED'
            if reason not in get_args(DemoAccountCapture.model_fields['reason'].annotation):reason='REQUEST_FAILED'
    return DemoAccountCapture(observed_at_ms=start,completed_at_ms=clock_ms(),source_sha256=source_hash(),
        status=status,reason=reason,reads=reads)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credential-file',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--operator-read',action='store_true')
    parser.add_argument('--capture-worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv);start=now_ms()
    if args.capture_worker:
        print(collect(credential_file=args.credential_file,operator_read=args.operator_read).model_dump_json());return 0
    if args.operator_read:
        cmd=[sys.executable,'-m','tools.binance_demo_account_capture','--capture-worker','--operator-read',
             '--credential-file',str(args.credential_file),'--output-dir',str(args.output_dir)]
        names=('PATH','LANG','LC_ALL','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
               'http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE','SSL_CERT_DIR')
        try:
            child=subprocess.run(cmd,cwd=Path(__file__).resolve().parents[1],capture_output=True,check=True,timeout=110,
                env={n:os.environ[n] for n in names if n in os.environ})
            if len(child.stdout)>MAX_CAPTURE_BYTES:raise ValueError
            result=DemoAccountCapture.model_validate_json(child.stdout)
            if result.observed_at_ms<start or result.completed_at_ms>now_ms():raise ValueError
        except Exception as exc:
            result=DemoAccountCapture(observed_at_ms=start,completed_at_ms=now_ms(),source_sha256=source_hash(),
                status='FAILED',reason='REQUEST_TIMEOUT' if isinstance(exc,subprocess.TimeoutExpired) else 'REQUEST_FAILED')
    else:result=collect(credential_file=args.credential_file)
    save_capture(args.output_dir,result)
    report=build_baseline(result,as_of_ms=now_ms())
    print(json.dumps({'capture_status':result.status,'capture_reason':result.reason,
        'inventory_status':report['inventory_status'],'nonzero_positions':report['nonzero_positions'],
        'open_orders':len(report['open_orders']) if result.status=='CAPTURED' else None,
        'submission_enabled':False,'ledger_reconciliation':'UNKNOWN'}))
    return 0 if result.status=='CAPTURED' and report['inventory_status']=='STABLE_OBSERVATIONS' else 2


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print(json.dumps({'status':'FAILED','reason':'ACCOUNT_CAPTURE_FAILED'}));raise SystemExit(3)
