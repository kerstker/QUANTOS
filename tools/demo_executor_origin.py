"""Bounded fixed-account origin reads inside the protected Demo executor.

Uses the original 16-GET capture and original V4 validation, with no ordinary
order, signing, risk reset, credential override or account-copy operation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from tools import binance_demo_execution_origin as original
from tools.demo_executor_stream import fixed_account, source_hash as stream_source_hash
from tools.demo_resident_recovery import _check_path
from tools.binance_demo_user_stream import writer_lock
from live.exchange import binance_demo_exact as transport
from live.exchange import demo_network
from services.governance.json_integrity import load_json_without_duplicate_keys

now_ms=original.now_ms


def source_hash():
    return hashlib.sha256(Path(__file__).read_bytes()+original.source_hash().encode()+stream_source_hash().encode()).hexdigest()


def capture_worker(symbol):
    if symbol not in ('BTCUSDT','BTCUSDC'):
        raise ValueError('PROTECTED_ORIGIN_SYMBOL')
    _, account = fixed_account()
    opener=demo_network.opener()
    report=original.collect(credential_file=None,symbol=symbol,operator_read=True,clock_ms=now_ms,
        credential_reader=lambda _:transport._credentials(account),
        get=lambda path,**kwargs:original.get_response(path,opener=opener,**kwargs))
    if report['account_ref_sha256'] is not None and report['account_ref_sha256']!=account:
        raise ValueError('PROTECTED_ORIGIN_ACCOUNT')
    report['source_sha256']=source_hash()
    return original.validate(report)


def run(*,symbol,source_commit):
    if symbol not in ('BTCUSDT','BTCUSDC') or type(source_commit) is not str or re.fullmatch('[a-f0-9]{40}',source_commit) is None:
        raise ValueError('PROTECTED_ORIGIN_INPUT')
    root, account = fixed_account()
    with writer_lock(root/'origin-capture.lock',wait_seconds=35):
        return _run_locked(symbol=symbol,source_commit=source_commit,root=root,account=account)


def _run_locked(*,symbol,source_commit,root,account):
    destination=root/'execution-origins'/symbol
    for path in (destination.parent,destination):
        path.mkdir(mode=0o700,exist_ok=True)
        _check_path(path,root.stat().st_uid,directory=True)
    count=size=0
    for path in destination.iterdir():
        _check_path(path,root.stat().st_uid,directory=False)
        count+=1;size+=path.stat().st_size
        if count>=1000 or size>=64*1024*1024:
            raise ValueError('PROTECTED_ORIGIN_RESULT_BUDGET')
    started=now_ms();source=Path(__file__).resolve().parents[1]
    env={'PATH':str(Path(sys.executable).parent)+':/usr/bin:/bin','LANG':'C.UTF-8',
        'PYTHONPATH':str(source),'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','NO_PROXY':'*','no_proxy':'*'}
    command=[sys.executable,'-P','-m','tools.demo_executor_origin','--capture-worker','--symbol',symbol]
    try:
        child=subprocess.run(command,cwd=source,env=env,capture_output=True,check=True,timeout=115)
        if len(child.stdout)>original.MAX_BYTES:
            raise ValueError('PROTECTED_ORIGIN_OUTPUT_BUDGET')
        report=original.validate(load_json_without_duplicate_keys(child.stdout))
        if (report['symbol']!=symbol or report['source_sha256']!=source_hash() or
            report['status'] not in ('CAPTURED','FAILED') or
            report['account_ref_sha256'] not in (None,account) or
            report['status']=='CAPTURED' and report['account_ref_sha256']!=account or
            not started<=report['observed_at_ms']<=report['completed_at_ms']<=now_ms()):
            raise ValueError('PROTECTED_ORIGIN_WORKER_BINDING')
    except Exception as exc:
        report=original.collect(credential_file=None,symbol=symbol,clock_ms=now_ms)
        completed=now_ms()
        report.update(status='FAILED',observed_at_ms=started,completed_at_ms=completed,
            source_sha256=source_hash(),failures=[{'kind':'worker','requested_at_ms':started,'received_at_ms':completed,
                'reason':'CAPTURE_BUDGET_EXHAUSTED' if isinstance(exc,subprocess.TimeoutExpired) else 'REQUEST_FAILED',
                'response_sha256':None,'server_time_ms':None}])
    _check_path(destination,root.stat().st_uid,directory=True)
    path=original.save(destination,original.validate(report))
    return {'schema':'DEMO_EXECUTOR_ORIGIN_CAPTURE_V1','source_commit':source_commit,'status':report['status'],
        'path':str(path),'assessment':original.assess(report,as_of_ms=now_ms()),
        'submission_enabled':False,'source_verified':False,'economic_authority':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit')
    parser.add_argument('--symbol',choices=('BTCUSDT','BTCUSDC'),required=True)
    parser.add_argument('--capture-worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    previous=os.umask(0o077)
    try:
        if args.capture_worker:
            value=capture_worker(args.symbol)
            encoded=json.dumps(value)
            if len(encoded.encode())>original.MAX_BYTES:
                raise ValueError('PROTECTED_ORIGIN_OUTPUT_BUDGET')
            print(encoded);return 0
        result=run(symbol=args.symbol,source_commit=args.source_commit)
        print(json.dumps(result))
        return 0 if result['status']=='CAPTURED' else 2
    except Exception:
        print(json.dumps({'status':'FAILED','reason':'PROTECTED_ORIGIN_UNAVAILABLE','submission_enabled':False}))
        return 3
    finally:
        os.umask(previous)


if __name__=='__main__':raise SystemExit(main())
