"""Capture private Demo events into the original protected executor journal.

The independently installed identity, namespace and stores must already exist.
Only listen-key POST/PUT and the fixed Demo websocket are exposed; ordinary
orders, cancellations, signing, source promotion and cash mutation are absent.
"""
import argparse
import asyncio
from contextlib import suppress
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import sys

from live.exchange import binance_demo_exact as transport
from live.execution.demo_pre_submit import _retain
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_user_stream import UserStreamJournal, MAX_MESSAGE_BYTES
from quantos.contracts.binance_demo import DEMO_WEBSOCKET_BASE
from tools.demo_resident_recovery import require_custody, _check_path
from tools.binance_demo_user_stream import (
    CapturePolicy, capture, lease_request, writer_lock, source_hash as original_source_hash, now_ms, _token,
)
from tools.binance_demo_readiness import ProbeFailure

MAX_RECORDS = 200000
MAX_BYTES = 128*1024*1024


def source_hash():
    return hashlib.sha256(Path(__file__).read_bytes()+original_source_hash().encode()+
        Path(transport.__file__).read_bytes()+Path(transport.demo_authority.__file__).read_bytes()).hexdigest()


def _budget(store):
    size = 0
    for suffix in ('', '-wal', '-shm', '-journal'):
        path = Path(str(store.path)+suffix)
        if path.exists() or path.is_symlink():
            _check_path(path, store.path.parent.stat().st_uid, directory=False)
            size += path.stat().st_size
    # Reserve space for the next bounded message and SQLite page bookkeeping.
    if size > MAX_BYTES-1024*1024:
        raise ValueError('PROTECTED_STREAM_BYTE_BUDGET')
    count = store.db.execute('SELECT COUNT(*) FROM stream_records').fetchone()[0]
    if count >= MAX_RECORDS:
        raise ValueError('PROTECTED_STREAM_RECORD_BUDGET')


class ProtectedJournal(UserStreamJournal):
    def record(self, kind, *, at_ms, **data):
        _budget(self)
        return super().record(kind, at_ms=at_ms, **data)


def fixed_account():
    root = require_custody()
    with UserStreamJournal(root/'user-stream.sqlite3', read_only=True) as store:
        _budget(store)
        store.verify()
        return root, store.meta['account_ref_sha256']


def lease_worker(method):
    if method not in ('POST','PUT'):
        raise ValueError('PROTECTED_STREAM_LEASE_METHOD')
    _, account = fixed_account()
    key, secret = transport._credentials(account)
    del secret
    return lease_request(method, key)


async def lease_child(method):
    if method not in ('POST','PUT'):
        raise ValueError('PROTECTED_STREAM_LEASE_METHOD')
    source = Path(__file__).resolve().parents[1]
    env = {'PATH': str(Path(sys.executable).parent)+':/usr/bin:/bin', 'LANG':'C.UTF-8',
        'PYTHONPATH':str(source), 'PYTHONNOUSERSITE':'1', 'PYTHONDONTWRITEBYTECODE':'1',
        'NO_PROXY':'*','no_proxy':'*'}
    child = await asyncio.create_subprocess_exec(sys.executable,'-P','-m','tools.demo_executor_stream',
        '--lease-worker',method,cwd=source,env=env,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        output, _ = await asyncio.wait_for(child.communicate(),20)
        if child.returncode or len(output)>4096:
            raise ProbeFailure('REQUEST_FAILED')
        data = load_json_without_duplicate_keys(output)
        if isinstance(data,dict) and data.get('reason') in ('AUTHENTICATION_REJECTED','RATE_LIMITED'):
            raise ProbeFailure(data['reason'])
        if not isinstance(data,dict) or set(data)!={'listenKey'}:
            raise ProbeFailure('REQUEST_FAILED')
        return _token(data['listenKey'])
    except asyncio.CancelledError:
        raise
    except ProbeFailure:
        raise
    except Exception:
        raise ProbeFailure('REQUEST_FAILED') from None
    finally:
        if child.returncode is None:
            with suppress(ProcessLookupError):child.kill()
            await child.wait()


def connect_socket(token):
    from websockets.asyncio.client import connect
    logger = logging.Logger('quantos.demo.executor.stream',level=logging.CRITICAL)
    logger.addHandler(logging.NullHandler()); logger.propagate=False
    return connect(DEMO_WEBSOCKET_BASE+'/private/ws/'+_token(token),proxy=None,
        open_timeout=10,close_timeout=2,ping_interval=None,max_size=MAX_MESSAGE_BYTES,max_queue=128,logger=logger)


async def run(*,source_commit,stop,policy=None):
    if type(source_commit) is not str or re.fullmatch('[a-f0-9]{40}',source_commit) is None:
        raise ValueError('PROTECTED_STREAM_SOURCE_ID')
    root, account = fixed_account()
    with writer_lock(root/'user-stream.lock'):
        require_custody()
        with ProtectedJournal(root/'user-stream.sqlite3',account_ref_sha256=account) as store:
            _budget(store)
            reason = await capture(store,source_sha256=source_hash(),lease=lease_child,connect=connect_socket,
                stop=stop,policy=policy or CapturePolicy(),clock_ms=now_ms)
            report = {'schema':'DEMO_EXECUTOR_STREAM_CAPTURE_V1','source_commit':source_commit,
                'source_sha256':source_hash(),'observed_at_ms':now_ms(),'reason':reason,
                'snapshot':store.snapshot(as_of_ms=now_ms()),'new_order_submission_enabled':False,
                'cash_reconciled':False,'source_verified':False,'economic_authority':False}
            destination=root/'stream-capture-results'
            destination.mkdir(mode=0o700,exist_ok=True)
            _check_path(destination,root.stat().st_uid,directory=True)
            total=count=0
            for entry in destination.iterdir():
                _check_path(entry,root.stat().st_uid,directory=False)
                count+=1;total+=entry.stat().st_size
                if count>=1000 or total>=64*1024*1024:
                    raise ValueError('PROTECTED_STREAM_RESULT_BUDGET')
            _retain(destination,report)
            return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit')
    parser.add_argument('--lease-worker',choices=('POST','PUT'),help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    previous=os.umask(0o077)
    try:
        if args.lease_worker:
            try: print(json.dumps({'listenKey':lease_worker(args.lease_worker)}))
            except ProbeFailure as exc:
                reason=str(exc) if str(exc) in ('AUTHENTICATION_REJECTED','RATE_LIMITED') else 'REQUEST_FAILED'
                print(json.dumps({'reason':reason}))
            return 0
        async def controlled():
            stop=asyncio.Event();loop=asyncio.get_running_loop()
            for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,stop.set)
            try:return await run(source_commit=args.source_commit,stop=stop)
            finally:
                for sig in (signal.SIGINT,signal.SIGTERM):loop.remove_signal_handler(sig)
        report=asyncio.run(controlled())
        print(json.dumps({k:v for k,v in report.items() if k!='snapshot'}))
        return 0 if report['reason'] in ('SIGNAL','BUDGET_EXHAUSTED') else 78 if report['reason']=='AUTHENTICATION_REJECTED' else 1
    except Exception:
        print(json.dumps({'reason':'PROTECTED_STREAM_UNAVAILABLE','new_order_submission_enabled':False}))
        return 1
    finally:
        os.umask(previous)


if __name__=='__main__':raise SystemExit(main())
