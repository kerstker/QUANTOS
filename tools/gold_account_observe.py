"""Human-operated, bounded mainnet account stream. Default command is offline.

Creates only a data-stream lease, never an order. Each invocation is an isolated
session; reconnection, historical gap filling and cash reconciliation are pending.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
import urllib.request

from services.research.gold_account_journal import AccountJournal, MAX_EVENT_BYTES
from services.research.gold_trade_observation import load_document
from tools.gold_account_capture import collect, NoRedirect, source_hashes
from tools.gold_trade_observation import _write_private, _encode
from tools.gold_credentials import get_credentials

ROOT=Path(__file__).resolve().parents[1]
SOURCES=('tools/gold_account_observe.py','services/research/gold_account_journal.py','tools/gold_credentials.py')


def sources():
    return source_hashes() | {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in SOURCES}


@contextmanager
def open_stream(api_key):
    # No caller-controlled host/path/query and no redirect. No lease persistence.
    request=urllib.request.Request('https://fapi.binance.com/fapi/v1/listenKey',
        headers={'X-MBX-APIKEY':api_key},data=b'',method='POST')
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=10) as response:
        raw=response.read(4097)
        if response.status!=200 or len(raw)>4096:raise ValueError('LEASE_FAILED')
    value=load_document(raw)
    token=value.get('listenKey') if isinstance(value,dict) else None
    if not isinstance(token,str) or re.fullmatch('[A-Za-z0-9_-]{16,256}',token) is None:
        raise ValueError('LEASE_FAILED')
    from websockets.sync.client import connect
    # A WebSocket URL contains the bearer lease: never log it or exception text.
    logger=logging.Logger('gold-private-stream',level=logging.CRITICAL+1)
    logger.disabled=True;logger.propagate=False
    with connect('wss://fstream.binance.com/private/ws/'+token,
                 logger=logger,open_timeout=10,close_timeout=5,max_size=MAX_EVENT_BYTES,
                 max_queue=16,ping_interval=20,ping_timeout=20) as connection:
        yield connection


def observe(destination,*,api_key,api_secret,seconds=300,capture=None,
            open_stream=None,clock_ms=None,monotonic=None):
    destination=Path(destination).absolute()
    if type(seconds) is not int or not 1<=seconds<=1800:raise ValueError('INVALID_DURATION')
    if destination.resolve()!=destination or not destination.is_dir() or any(destination.iterdir()):
        raise ValueError('EMPTY_PRIVATE_DIRECTORY_REQUIRED')
    os.chmod(destination,0o700)
    clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000)
    monotonic=monotonic or time.monotonic
    capture=capture or collect
    connect_stream=open_stream or globals()['open_stream']
    hashes=sources()
    baseline=destination/'baseline';baseline.mkdir(mode=0o700)
    preflight=capture(baseline,api_key=api_key,api_secret=api_secret)
    if preflight['status']!='STABLE_OBSERVATIONS':
        report={'status':'INCOMPLETE','reason':'PREFLIGHT_INCOMPLETE',
                'baseline_status':preflight['status'],'submission_enabled':False}
        _write_private(destination/'report.json',_encode(report))
        return report
    ref=load_document((baseline/'capture.json').read_bytes())['account_ref']
    _write_private(destination/'session.json',_encode({'schema':'GOLD_STREAM_SESSION_V1',
        'account_ref':ref,'source_sha256':hashes,'seconds':seconds,
        'account_binding':'SESSION_ONLY_UNAUTHENTICATED'}))
    with AccountJournal(destination/'journal.db',account_ref=ref) as journal:
        journal.start(at_ms=clock_ms(),source_sha256=hashlib.sha256(_encode(hashes)).hexdigest())
        journal.gap('BASELINE_TO_STREAM',at_ms=clock_ms())
        reason='DURATION_COMPLETE'
        try:
            if sources()!=hashes:raise ValueError('SOURCE_CHANGED')
            deadline=monotonic()+seconds
            with connect_stream(api_key) as connection:
                journal.lifecycle('CONNECTED',at_ms=clock_ms())
                while monotonic()<deadline:
                    try:raw=connection.recv(timeout=min(20,max(0.001,deadline-monotonic())))
                    except TimeoutError:
                        if monotonic()>=deadline:break
                        if not connection.ping().wait(timeout=min(5,max(0.001,deadline-monotonic()))):
                            raise ValueError('PONG_TIMEOUT')
                        journal.lifecycle('HEARTBEAT',at_ms=clock_ms())
                        continue
                    if not journal.message(raw,at_ms=clock_ms()):
                        reason='EVENT_UNSUPPORTED_OR_INVALID';break
                    if load_document(raw.encode())['e']=='listenKeyExpired':
                        reason='LEASE_EXPIRED';break
        except KeyboardInterrupt:
            reason='OPERATOR_STOPPED'
            journal.gap('INTERRUPTED',at_ms=clock_ms())
        except Exception:
            reason='STREAM_FAILED'
            journal.lifecycle('DISCONNECTED',at_ms=clock_ms())
        if sources()!=hashes:
            journal.gap('SOURCE_CHANGED',at_ms=clock_ms());reason='SOURCE_CHANGED'
        journal.lifecycle('STOPPED',at_ms=clock_ms())
        report=journal.summary(as_of_ms=clock_ms()) | {'reason':reason}
    _write_private(destination/'report.json',_encode(report))
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    action=parser.add_mutually_exclusive_group()
    action.add_argument('--operator-observe',action='store_true')
    action.add_argument('--verify-monitor-dir',type=Path)
    parser.add_argument('--output-dir',type=Path)
    parser.add_argument('--monitor',action='store_true',help='Enable bounded reconnect, checkpoints and history checks')
    parser.add_argument('--resume-dir',type=Path,help='Resume a monitor directory with the same read-only key')
    parser.add_argument('--seconds',type=int,default=300)
    parser.add_argument('--credentials',choices=('auto','prompt'),default='auto',help='Use saved private credentials when present; prompt forces manual entry')
    args=parser.parse_args(argv)
    try:
        if not 1<=args.seconds<=(86400 if args.monitor else 1800):raise ValueError('INVALID_DURATION')
        if args.verify_monitor_dir:
            if args.output_dir or args.resume_dir:raise ValueError('OUTPUT_NOT_ALLOWED')
            from tools.gold_account_monitor import verify_monitor
            report=verify_monitor(args.verify_monitor_dir)
        elif not args.operator_observe:
            report={'status':'READY_FOR_OPERATOR_OBSERVATION','network_used':False,
                'submission_enabled':False,'max_seconds':86400 if args.monitor else 1800,
                'limitations':(['NO_INDEPENDENT_HISTORY_AUTHORITY','ORDER_HISTORY_GAPS_REMAIN'] if args.monitor else
                    ['NO_AUTOMATIC_RECONNECT','NO_GAP_BACKFILL','NO_CASH_RECONCILIATION','SESSION_ONLY_ACCOUNT_BINDING','UNSUPPORTED_EVENT_STOPS'])}
        else:
            if not sys.stdin.isatty() or not sys.stderr.isatty():raise ValueError('INTERACTIVE_TERMINAL_REQUIRED')
            if args.resume_dir and (not args.monitor or args.output_dir):raise ValueError('INVALID_RESUME')
            target=args.resume_dir or args.output_dir
            root=target.absolute() if target else None
            if root is None or root.resolve()!=root or (not root.is_dir() if args.resume_dir else root.exists()) or not root.parent.is_dir():
                raise ValueError('NEW_OUTPUT_DIRECTORY_REQUIRED')
            key,secret=get_credentials(source=args.credentials)
            if not args.resume_dir:root.mkdir(mode=0o700)
            print(json.dumps({'status':'STARTING_BOUNDED_OBSERVATION','seconds':args.seconds,
                              'submission_enabled':False}),flush=True)
            try:
                if args.monitor:
                    from tools.gold_account_monitor import monitor
                    report=monitor(root,api_key=key,api_secret=secret,seconds=args.seconds,resume=bool(args.resume_dir),
                        progress=lambda value:print(json.dumps(value,sort_keys=True),flush=True))
                else:report=observe(root,api_key=key,api_secret=secret,seconds=args.seconds)
            finally:del key,secret
        print(json.dumps(report,sort_keys=True))
        return 0 if (report.get('reason')=='DURATION_COMPLETE' and report['status']=='STOPPED') or report['status'] in ('READY_FOR_OPERATOR_OBSERVATION','LOCAL_ARCHIVE_VERIFIED') else 2
    except (Exception,KeyboardInterrupt) as exc:
        safe={'PRIVATE_CREDENTIAL_DIRECTORY_REQUIRED','PRIVATE_CREDENTIAL_FILE_REQUIRED','INVALID_STORED_CREDENTIALS',
              'LINKED_CREDENTIAL_DIRECTORY','CREDENTIAL_FILE_UNAVAILABLE','INVALID_CREDENTIAL_FORMAT'}
        reason=str(exc) if type(exc) is ValueError and str(exc) in safe else 'OBSERVATION_FAILED'
        print(json.dumps({'status':'FAILED','reason':reason,'submission_enabled':False}))
        return 2


if __name__=='__main__':raise SystemExit(main())
