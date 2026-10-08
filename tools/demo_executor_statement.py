"""Fixed-account Demo historical GET capture for subsequent receipt recovery.

The controlled child uses the existing executor credential binding and network
adapter. Retaining a statement never projects an order, fee or cash balance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from tools import binance_demo_statement as original
from tools.demo_executor_stream import fixed_account
from tools.demo_resident_recovery import _check_path
from tools.binance_demo_user_stream import writer_lock
from live.exchange import binance_demo_exact as transport, demo_network
from live.execution.demo_pre_submit import _retain
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_statement import validate as validate_statement

SCHEMA = 'DEMO_EXECUTOR_STATEMENT_V1'
DAY_SCHEMA = 'DEMO_EXECUTOR_DAY_STATEMENT_V1'
GOLD_SCHEMA = 'DEMO_GOLD_EXECUTOR_STATEMENT_V1'
GOLD_DAY_SCHEMA = 'DEMO_GOLD_EXECUTOR_DAY_STATEMENT_V1'
MAX_BYTES = 12_000_000


def now_ms(): return time.time_ns()//1_000_000


def source_hash():
    return hashlib.sha256(Path(__file__).read_bytes()+original.source_hash().encode()+
        Path(transport.__file__).read_bytes()+Path(transport.demo_authority.__file__).read_bytes()+
        demo_network.source_bytes()).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _scope(symbol, start, end, at, *, day_replay=False):
    if type(day_replay) is not bool or symbol not in ('BTCUSDT', 'BTCUSDC','XAUUSDT') or type(start) is not int or type(end) is not int or not 0 < start < end <= at or end-start > (7 if day_replay else 1)*86_400_000:
        raise ValueError('EXECUTOR_STATEMENT_SCOPE')


def statement_schema(symbol,*,day_replay=False):
    if symbol=='XAUUSDT':return GOLD_DAY_SCHEMA if day_replay else GOLD_SCHEMA
    return DAY_SCHEMA if day_replay else SCHEMA


def _report(account, symbol, start, end, observed, completed, statement=None, reason=None, *, day_replay=False):
    return {'schema': statement_schema(symbol,day_replay=day_replay), 'account_ref_sha256': account, 'source_sha256': source_hash(),
        'symbol': symbol, 'start_ms': start, 'end_ms': end, 'observed_at_ms': observed,
        'completed_at_ms': completed, 'status': statement['status'] if statement is not None else 'FAILED',
        'statement': statement, 'statement_sha256': _digest(statement) if statement is not None else None,
        'reason': reason, 'source_verified': False, 'canonical_ledger_reconciled': False,
        'orders_submitted': False, 'economic_authority': False}


def validate(report):
    keys = set('schema account_ref_sha256 source_sha256 symbol start_ms end_ms observed_at_ms completed_at_ms status statement statement_sha256 reason source_verified canonical_ledger_reconciled orders_submitted economic_authority'.split())
    if not isinstance(report, dict) or set(report) != keys or report['schema'] not in (SCHEMA,DAY_SCHEMA,GOLD_SCHEMA,GOLD_DAY_SCHEMA):
        raise ValueError('EXECUTOR_STATEMENT_SCHEMA')
    for name in ('account_ref_sha256', 'source_sha256'):
        if not isinstance(report[name], str) or re.fullmatch('[a-f0-9]{64}', report[name]) is None:
            raise ValueError('EXECUTOR_STATEMENT_IDENTITY')
    for name in ('observed_at_ms', 'completed_at_ms'):
        if type(report[name]) is not int or not 0 < report[name] <= 2**53-1:
            raise ValueError('EXECUTOR_STATEMENT_CLOCK')
    day=report['schema'] in (DAY_SCHEMA,GOLD_DAY_SCHEMA)
    _scope(report['symbol'], report['start_ms'], report['end_ms'], report['observed_at_ms'],day_replay=day)
    if report['schema']!=statement_schema(report['symbol'],day_replay=day):
        raise ValueError('EXECUTOR_STATEMENT_MARKET_SCHEMA')
    if report['observed_at_ms'] > report['completed_at_ms']:
        raise ValueError('EXECUTOR_STATEMENT_CLOCK')
    if any(report[name] is not False for name in ('source_verified','canonical_ledger_reconciled','orders_submitted','economic_authority')):
        raise ValueError('EXECUTOR_STATEMENT_NOT_AUTHORITY')
    raw = report['statement']
    if raw is None:
        if report['status'] != 'FAILED' or report['statement_sha256'] is not None or report['reason'] not in ('CAPTURE_BUDGET_EXHAUSTED','WORKER_UNAVAILABLE'):
            raise ValueError('EXECUTOR_STATEMENT_FAILURE')
    else:
        validate_statement(raw)
        if (raw['status'] not in ('CAPTURED','PARTIAL','FAILED') or report['status'] != raw['status'] or
            report['reason'] is not None or report['statement_sha256'] != _digest(raw) or
            raw['symbols'] != [report['symbol']] or
            any(raw[name] != report[name] for name in ('start_ms','end_ms','observed_at_ms','completed_at_ms'))):
            raise ValueError('EXECUTOR_STATEMENT_BINDING')
    return report


def capture_worker(symbol, start_ms, end_ms, *, day_replay=False):
    _scope(symbol, start_ms, end_ms, now_ms(),day_replay=day_replay)
    _, account = fixed_account()
    opener = demo_network.opener()
    report = original.collect(credential_file=None, symbols=[symbol], start_ms=start_ms, end_ms=end_ms,
        operator_read=True, clock=now_ms, credential_reader=lambda _: transport._credentials(account),
        get=lambda path, **kwargs: original.get_response(path, opener=opener, **kwargs))
    return validate(_report(account, symbol, start_ms, end_ms, report['observed_at_ms'], report['completed_at_ms'], report,day_replay=day_replay))


def run(*, symbol, start_ms, end_ms, source_commit,day_replay=False):
    _scope(symbol, start_ms, end_ms, now_ms(),day_replay=day_replay)
    if not isinstance(source_commit, str) or re.fullmatch('[a-f0-9]{40}', source_commit) is None:
        raise ValueError('EXECUTOR_STATEMENT_SOURCE')
    root, account = fixed_account()
    directory = root/'receipt-reconciliation'
    directory.mkdir(mode=0o700, exist_ok=True)
    _check_path(directory, root.stat().st_uid, directory=True)
    destination = directory/'statements'
    destination.mkdir(mode=0o700, exist_ok=True)
    _check_path(destination, root.stat().st_uid, directory=True)
    # Share the existing fixed-account REST capture lease. This also preserves
    # the current backup namespace; no unrecognized lock file is introduced.
    with writer_lock(root/'origin-capture.lock',wait_seconds=35):
        count = size = 0
        for path in destination.iterdir():
            _check_path(path, root.stat().st_uid, directory=False)
            count += 1; size += path.stat().st_size
        if count >= 1000 or size > 64*1024*1024-MAX_BYTES:
            raise ValueError('EXECUTOR_STATEMENT_STORAGE_BUDGET')
        observed = now_ms(); source = Path(__file__).resolve().parents[1]
        env = {'PATH': str(Path(sys.executable).parent)+':/usr/bin:/bin', 'LANG':'C.UTF-8',
            'PYTHONPATH':str(source),'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','NO_PROXY':'*','no_proxy':'*'}
        command = [sys.executable,'-P','-m','tools.demo_executor_statement','--capture-worker',
            '--symbol',symbol,'--start-ms',str(start_ms),'--end-ms',str(end_ms)]
        if day_replay:command.append('--day-replay')
        try:
            child = subprocess.run(command,cwd=source,env=env,capture_output=True,check=True,timeout=115)
            if len(child.stdout) > MAX_BYTES: raise ValueError('EXECUTOR_STATEMENT_OUTPUT_BUDGET')
            report = validate(load_json_without_duplicate_keys(child.stdout))
            if (report['schema'] != statement_schema(symbol,day_replay=day_replay) or report['source_sha256'] != source_hash() or report['account_ref_sha256'] != account or
                report['symbol'] != symbol or report['start_ms'] != start_ms or report['end_ms'] != end_ms or
                report['statement'] is not None and report['statement']['source_sha256'] != original.source_hash() or
                not observed <= report['observed_at_ms'] <= report['completed_at_ms'] <= now_ms()):
                raise ValueError('EXECUTOR_STATEMENT_WORKER_BINDING')
        except Exception as error:
            report = _report(account,symbol,start_ms,end_ms,observed,now_ms(),reason=
                'CAPTURE_BUDGET_EXHAUSTED' if isinstance(error,subprocess.TimeoutExpired) else 'WORKER_UNAVAILABLE',day_replay=day_replay)
        validate(report)
        _check_path(destination,root.stat().st_uid,directory=True)
        path = _retain(destination,report)
        return {'schema':report['schema'],'source_commit':source_commit,'status':report['status'],'path':path,
                'orders_submitted':False,'canonical_ledger_reconciled':False,'economic_authority':False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol',choices=('BTCUSDT','BTCUSDC','XAUUSDT'),required=True)
    parser.add_argument('--start-ms',type=int,required=True)
    parser.add_argument('--end-ms',type=int,required=True)
    parser.add_argument('--source-commit')
    parser.add_argument('--capture-worker',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--day-replay',action='store_true',help='Bounded seven-day accounting capture; no execution authority.')
    args = parser.parse_args(argv); previous = os.umask(0o077)
    try:
        if args.capture_worker:
            report = capture_worker(args.symbol,args.start_ms,args.end_ms,day_replay=args.day_replay)
            encoded=json.dumps(report)
            if len(encoded.encode()) > MAX_BYTES: raise ValueError('EXECUTOR_STATEMENT_OUTPUT_BUDGET')
            print(encoded); return 0
        result=run(symbol=args.symbol,start_ms=args.start_ms,end_ms=args.end_ms,source_commit=args.source_commit,day_replay=args.day_replay)
        print(json.dumps({**result,'path':str(result['path'])}))
        return 0 if result['status']=='CAPTURED' else 2
    except Exception:
        print(json.dumps({'status':'FAILED','reason':'EXECUTOR_STATEMENT_UNAVAILABLE','orders_submitted':False}))
        return 3
    finally:os.umask(previous)


if __name__=='__main__': raise SystemExit(main())
