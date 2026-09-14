"""Operator-only, bounded Demo GET terms, trade history and income capture."""
import argparse
from decimal import Decimal
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from quantos.contracts.binance_demo import DEMO_REST_BASE
from quantos.contracts.binance_demo_account import _symbol
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_statement import SCHEMA, normalize, assess, save_statement, load_statement
from tools.binance_demo_private_check import load_credentials
from tools.binance_demo_readiness import NoRedirect, ProbeFailure

PUBLIC={'/fapi/v1/time','/fapi/v1/exchangeInfo'}
PRIVATE={'/fapi/v1/leverageBracket','/fapi/v1/commissionRate','/fapi/v1/userTrades','/fapi/v1/income'}
DAY=86_400_000


def source_hash():
    root=Path(__file__).resolve().parents[1]
    paths=('tools/binance_demo_statement.py','services/research/demo_statement.py',
        'quantos/contracts/binance_demo_account.py','services/governance/json_integrity.py',
        'tools/binance_demo_private_check.py')
    return hashlib.sha256(b'\0'.join((root/p).read_bytes() for p in paths)).hexdigest()


def get_response(path,*,params,headers,opener=None):
    if path not in PUBLIC|PRIVATE or path in PUBLIC and (params or headers):raise ProbeFailure('REQUEST_FAILED')
    request=urllib.request.Request(DEMO_REST_BASE+path+('?' + params if params else ''),headers=headers,method='GET')
    try:
        with (opener or urllib.request.build_opener(NoRedirect())).open(request,timeout=10) as response:
            raw=response.read(8_000_001)
    except urllib.error.HTTPError as exc:
        status=exc.code;exc.close()
        raise ProbeFailure('RATE_LIMITED' if status in (418,429) else 'AUTHENTICATION_REJECTED' if status in (401,403) else 'REQUEST_FAILED') from None
    except Exception:raise ProbeFailure('REQUEST_FAILED') from None
    if len(raw)>8_000_000:raise ProbeFailure('RESPONSE_TOO_LARGE')
    return raw


def collect(*,credential_file,symbols,start_ms,end_ms,operator_read=False,get=None,clock=None,credential_reader=None):
    get=get or get_response;clock=clock or (lambda:time.time_ns()//1_000_000)
    started=clock();deadline=time.monotonic()+480
    if (not 1<=len(symbols)<=30 or len(set(symbols))!=len(symbols) or
        type(start_ms) is not int or type(end_ms) is not int or not 0<start_ms<end_ms<=started or end_ms-start_ms>84*DAY):
        raise ValueError('bounded historical window and symbols required')
    for s in symbols:_symbol(s)
    report={'schema':SCHEMA,'status':'NOT_REQUESTED','observed_at_ms':started,'completed_at_ms':started,
        'start_ms':start_ms,'end_ms':end_ms,'symbols':sorted(symbols),
        'source_sha256':source_hash(),
        'reads':[],'trades':[],'income':[],'gaps':[],'attempt_failures':[]}
    if not operator_read:return report
    current_kind='credentials';current_symbol='';offset=0;request_count=0
    try:
        key,secret=(credential_reader or load_credentials)(credential_file)
        def read(kind,path,symbol='',start=None,end=None):
            nonlocal current_kind,current_symbol,request_count
            current_kind=kind;current_symbol=symbol
            at=clock();params={};headers={}
            if path in PRIVATE:
                if symbol:params['symbol']=symbol
                if start is not None:params.update(startTime=start,endTime=end,limit=1000)
                params.update(timestamp=at+offset,recvWindow=5000)
                query=urllib.parse.urlencode(params)
                query+='&signature='+hmac.new(secret.encode(),query.encode(),hashlib.sha256).hexdigest()
                headers={'X-MBX-APIKEY':key}
            else:query=''
            for attempt in range(3):
                if request_count>=300 or time.monotonic()>deadline:raise ProbeFailure('CAPTURE_BUDGET_EXHAUSTED')
                request_count+=1;attempt_at=clock()
                if path in PRIVATE:
                    params['timestamp']=attempt_at+offset
                    query=urllib.parse.urlencode(params)
                    query+='&signature='+hmac.new(secret.encode(),query.encode(),hashlib.sha256).hexdigest()
                try:
                    raw=get(path,params=query,headers=headers);received=clock();break
                except ProbeFailure as exc:
                    report['attempt_failures'].append({'kind':kind,'symbol':symbol,
                        'requested_at_ms':attempt_at,'received_at_ms':clock(),
                        'reason':str(exc) if str(exc) in ('REQUEST_FAILED','RATE_LIMITED','AUTHENTICATION_REJECTED','RESPONSE_TOO_LARGE') else 'REQUEST_FAILED'})
                    if str(exc)!='REQUEST_FAILED' or attempt==2:raise
                    time.sleep(attempt+1)
            load_json_without_duplicate_keys(raw)
            data=normalize(kind,json.loads(raw,parse_float=Decimal),[symbol] if symbol else symbols)
            if kind in ('trades','income') and any(not start<=r['time']<=end for r in data):raise ProbeFailure('RESPONSE_INVALID')
            report['reads'].append({'kind':kind,'symbol':symbol,'start_ms':start,'end_ms':end,
                'requested_at_ms':at,'received_at_ms':received,'response_sha256':hashlib.sha256(raw).hexdigest(),'data':data})
            if kind in ('trades','income'):
                report[kind].extend(data)
                if len(data)==1000:report['gaps'].append({'kind':kind,'symbol':symbol,'start_ms':start,'end_ms':end,'reason':'PAGE_LIMIT_REACHED'})
            return data,at,received
        data,sent,received=read('time','/fapi/v1/time')
        offset=data['serverTime']-(sent+received)//2
        if not 0<=received-sent<=5000 or abs(offset)>2000:raise ProbeFailure('CLOCK_UNRELIABLE')
        read('exchange','/fapi/v1/exchangeInfo')
        for symbol in sorted(symbols):
            read('brackets','/fapi/v1/leverageBracket',symbol)
            read('commission','/fapi/v1/commissionRate',symbol)
        for start in range(start_ms,end_ms+1,7*DAY):
            end=min(end_ms,start+7*DAY-1)
            for symbol in sorted(symbols):read('trades','/fapi/v1/userTrades',symbol,start,end)
            read('income','/fapi/v1/income','',start,end)
        report['status']='PARTIAL' if report['gaps'] else 'CAPTURED'
    except Exception as exc:
        reason=str(exc) if isinstance(exc,ProbeFailure) else 'REQUEST_FAILED'
        if reason not in {'REQUEST_FAILED','RATE_LIMITED','AUTHENTICATION_REJECTED','RESPONSE_TOO_LARGE','RESPONSE_INVALID','CAPTURE_BUDGET_EXHAUSTED','CLOCK_UNRELIABLE'}:reason='REQUEST_FAILED'
        report['gaps'].append({'kind':current_kind,'symbol':current_symbol,'start_ms':start_ms,'end_ms':end_ms,'reason':reason})
        report['status']='PARTIAL' if report['reads'] else 'FAILED'
    for kind,keys in (('trades',('symbol','id')),('income',('incomeType','tranId'))):
        unique={}
        for row in report[kind]:
            k=tuple(row[x] for x in keys)
            if k in unique and unique[k]!=row:raise ValueError('conflicting flow identity')
            unique[k]=row
        report[kind]=sorted(unique.values(),key=lambda r:(r['time'],tuple(str(r[k]) for k in keys)))
    report['completed_at_ms']=clock()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credential-file',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--symbols',nargs='+',required=True)
    parser.add_argument('--days',type=int,choices=range(1,85),default=7)
    parser.add_argument('--operator-read',action='store_true')
    parser.add_argument('--capture-worker',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--end-ms',type=int,help=argparse.SUPPRESS)
    args=parser.parse_args(argv);end=time.time_ns()//1_000_000
    if args.end_ms is not None:end=args.end_ms
    if args.capture_worker:
        print(json.dumps(collect(credential_file=args.credential_file,symbols=args.symbols,
            start_ms=end-args.days*DAY+1,end_ms=end,operator_read=args.operator_read)))
        return 0
    if args.operator_read:
        argv=[sys.executable,'-m','tools.binance_demo_statement','--capture-worker','--operator-read',
            '--credential-file',str(args.credential_file),'--output-dir',str(args.output_dir),
            '--days',str(args.days),'--end-ms',str(end),'--symbols',*args.symbols]
        names=('PATH','LANG','LC_ALL','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
            'http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE','SSL_CERT_DIR')
        try:
            child=subprocess.run(argv,cwd=Path(__file__).resolve().parents[1],capture_output=True,check=True,timeout=510,
                env={n:os.environ[n] for n in names if n in os.environ})
            if len(child.stdout)>32_000_000:raise ValueError('worker response too large')
            report=load_json_without_duplicate_keys(child.stdout)
            if not end<=report['observed_at_ms']<=report['completed_at_ms']<=time.time_ns()//1_000_000:raise ValueError('worker clock')
        except Exception as exc:
            report=collect(credential_file=args.credential_file,symbols=args.symbols,
                start_ms=end-args.days*DAY+1,end_ms=end)
            report['status']='FAILED'
            report['gaps']=[{'kind':'time','symbol':'','start_ms':report['start_ms'],'end_ms':end,
                'reason':'CAPTURE_BUDGET_EXHAUSTED' if isinstance(exc,subprocess.TimeoutExpired) else 'REQUEST_FAILED'}]
    else:
        report=collect(credential_file=args.credential_file,symbols=args.symbols,start_ms=end-args.days*DAY+1,end_ms=end)
    save_statement(args.output_dir,report)
    result=assess(report)
    print(json.dumps({k:result[k] for k in ('status','read_count','trade_count','income_count','gaps','venue_cash_reconciliation')}))
    return 0 if report['status']=='CAPTURED' else 2


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        print('{"status":"FAILED","reason":"STATEMENT_CAPTURE_FAILED"}');raise SystemExit(3)
