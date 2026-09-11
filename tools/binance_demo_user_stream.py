"""Operator-owned Demo event capture. Default offline; never submits orders.

Only the fixed Demo listen-key POST/PUT resource is exposed. Leases live in
memory; no DELETE is sent because other readers can share the account lease.
REST DNS/network work has a killable child deadline. Socket errors are reduced
to fixed codes, never formatted with their credential-bearing URL.
"""
import argparse
import asyncio
from contextlib import contextmanager, suppress
from dataclasses import dataclass
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import urllib.error
import urllib.request

from quantos.contracts.binance_demo import DEMO_REST_BASE, DEMO_WEBSOCKET_BASE
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_user_stream import UserStreamJournal, normalize_message, MAX_MESSAGE_BYTES
from tools.binance_demo_private_check import load_credentials
from tools.binance_demo_readiness import NoRedirect, ProbeFailure


def now_ms():return time.time_ns()//1_000_000


def source_hash():
    root=Path(__file__).resolve().parents[1]
    names=('tools/binance_demo_user_stream.py','services/research/demo_user_stream.py',
           'quantos/contracts/binance_demo.py','quantos/contracts/binance_demo_account.py',
           'services/research/binance_order_receipts.py','services/governance/json_integrity.py',
           'tools/binance_demo_private_check.py','tools/binance_demo_readiness.py')
    return hashlib.sha256(b'\0'.join((root/name).read_bytes() for name in names)).hexdigest()


def _token(value):
    if not isinstance(value,str) or not re.fullmatch('[A-Za-z0-9_-]{32,256}',value):
        raise ProbeFailure('RESPONSE_INVALID')
    return value


def lease_request(method,key):
    if method not in ('POST','PUT'):raise ValueError('STREAM_ENDPOINT_NOT_ALLOWED')
    request=urllib.request.Request(DEMO_REST_BASE+'/fapi/v1/listenKey',method=method,headers={'X-MBX-APIKEY':key})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=10) as response:
            if not 200<=response.status<300:raise ProbeFailure('REQUEST_FAILED')
            raw=response.read(4097)
        if len(raw)>4096:raise ProbeFailure('RESPONSE_INVALID')
        body=load_json_without_duplicate_keys(raw.decode())
        if not isinstance(body,dict) or set(body)!={'listenKey'}:raise ProbeFailure('RESPONSE_INVALID')
        return _token(body['listenKey'])
    except urllib.error.HTTPError as exc:
        code=exc.code;exc.close()
        raise ProbeFailure('AUTHENTICATION_REJECTED' if code in (401,403) else
                           'RATE_LIMITED' if code in (418,429) else 'REQUEST_FAILED') from None
    except ProbeFailure:raise
    except Exception:raise ProbeFailure('REQUEST_FAILED') from None


async def lease_child(method,credential_file):
    names=('PATH','LANG','LC_ALL','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
           'http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE','SSL_CERT_DIR')
    child=await asyncio.create_subprocess_exec(sys.executable,'-m','tools.binance_demo_user_stream',
        '--operator-read','--lease-worker',method,'--credential-file',str(credential_file),
        cwd=Path(__file__).resolve().parents[1],env={k:os.environ[k] for k in names if k in os.environ},
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        stdout,_=await asyncio.wait_for(child.communicate(),20)
        if child.returncode or len(stdout)>4096:raise ProbeFailure('REQUEST_FAILED')
        body=load_json_without_duplicate_keys(stdout.decode())
        if isinstance(body,dict) and body.get('reason') in ('AUTHENTICATION_REJECTED','RATE_LIMITED'):
            raise ProbeFailure(body['reason'])
        if not isinstance(body,dict) or set(body)!={'listenKey'}:raise ProbeFailure('REQUEST_FAILED')
        return _token(body['listenKey'])
    except asyncio.CancelledError:raise
    except ProbeFailure:raise
    except Exception:raise ProbeFailure('REQUEST_FAILED') from None
    finally:
        if child.returncode is None:
            with suppress(ProcessLookupError):child.kill()
            await child.wait()


def connect_socket(token):
    from websockets.asyncio.client import connect
    logger=logging.Logger('quantos.demo.stream',level=logging.CRITICAL)
    logger.addHandler(logging.NullHandler());logger.propagate=False
    return connect(DEMO_WEBSOCKET_BASE+'/private/ws/'+_token(token),open_timeout=10,close_timeout=2,
        ping_interval=None,max_size=MAX_MESSAGE_BYTES,max_queue=128,logger=logger)


@dataclass(frozen=True)
class CapturePolicy:
    seconds:float=21600
    heartbeat_seconds:float=15
    keepalive_seconds:float=1500
    backoff_seconds:float=2
    max_messages:int=100000
    max_reconnects:int=8

    def __post_init__(self):
        for value,low,high in ((self.seconds,.01,21600),(self.heartbeat_seconds,.01,30),
                (self.keepalive_seconds,.01,1500),(self.backoff_seconds,.001,30)):
            if type(value) not in (int,float) or not low<=value<=high:raise ValueError('STREAM_POLICY_INVALID')
        if (type(self.max_messages) is not int or not 1<=self.max_messages<=100000 or
                type(self.max_reconnects) is not int or not 0<=self.max_reconnects<=20):
            raise ValueError('STREAM_POLICY_INVALID')


class StreamStop(Exception):pass
class StreamGap(Exception):pass


async def _interruptible(awaitable,stop,timeout):
    operation=asyncio.ensure_future(awaitable);signal_task=asyncio.create_task(stop.wait())
    try:
        done,_=await asyncio.wait((operation,signal_task),timeout=max(0,timeout),return_when=asyncio.FIRST_COMPLETED)
        if signal_task in done:raise StreamStop('SIGNAL')
        if operation not in done:raise TimeoutError
        return operation.result()
    finally:
        for task in (operation,signal_task):
            if not task.done():task.cancel()
        await asyncio.gather(operation,signal_task,return_exceptions=True)


async def capture(journal,*,source_sha256,lease,connect,stop,policy=None,clock_ms=None):
    policy=policy or CapturePolicy();clock_ms=clock_ms or now_ms
    loop=asyncio.get_running_loop();deadline=loop.time()+policy.seconds
    messages=0;reconnects=0;previous_wall=0;reason='FAILED'

    def record(kind,**data):
        nonlocal previous_wall
        at=clock_ms()
        if at<previous_wall:
            journal.record('GAP',at_ms=at,reason='CLOCK_REGRESSION')
            raise StreamStop('FAILED')
        previous_wall=at;journal.record(kind,at_ms=at,**data)

    async def connection(token):
        nonlocal messages
        # The enclosing interruptible deadline bounds open/close and idle reads.
        async with connect(token) as socket:
            record('CONNECTED');ordered={}
            next_ping=loop.time();next_keepalive=loop.time()+policy.keepalive_seconds
            while True:
                if loop.time()>=next_ping:
                    pong=await socket.ping();await asyncio.wait_for(pong,5)
                    record('HEARTBEAT');next_ping=loop.time()+policy.heartbeat_seconds
                if loop.time()>=next_keepalive:
                    try:renewed=_token(await lease('PUT'))
                    except ProbeFailure as exc:
                        if str(exc) in ('AUTHENTICATION_REJECTED','RATE_LIMITED'):raise
                        raise StreamGap('KEEPALIVE_FAILED') from None
                    if renewed!=token:raise StreamGap('LEASE_EXPIRED')
                    record('LEASE_RENEWED');next_keepalive=loop.time()+policy.keepalive_seconds
                try:
                    raw=await asyncio.wait_for(socket.recv(),max(.001,min(next_ping,next_keepalive)-loop.time()))
                except TimeoutError:continue
                messages+=1
                try:message=normalize_message(raw)
                except ValueError:
                    digest=hashlib.sha256(raw.encode() if isinstance(raw,str) else raw).hexdigest()
                    record('GAP',reason='MESSAGE_REJECTED',response_sha256=digest)
                else:
                    # Compare clocks only within the same event type/connection.
                    # The exchange supplies no sequence proving complete delivery.
                    pair=(message['event_ms'],message['transaction_ms'])
                    prior=ordered.get(message['event_kind'])
                    if prior and (pair[0]<prior[0] or pair[1] is not None and prior[1] is not None and pair[1]<prior[1]):
                        record('GAP',reason='OUT_OF_ORDER_EVENT')
                    ordered[message['event_kind']]=pair
                    record('MESSAGE',message=message)
                    if message['event_kind']=='listenKeyExpired':raise StreamGap('LEASE_EXPIRED')
                if messages>=policy.max_messages:raise StreamStop('BUDGET_EXHAUSTED')

    journal.start_session(source_sha256=source_sha256,at_ms=clock_ms())
    try:
        while True:
            if stop.is_set():raise StreamStop('SIGNAL')
            if loop.time()>=deadline:raise StreamStop('BUDGET_EXHAUSTED')
            stage='CONNECT_FAILED'
            try:
                token=_token(await _interruptible(lease('POST'),stop,min(25,deadline-loop.time())))
                stage='STREAM_DISCONNECTED'
                await _interruptible(connection(token),stop,deadline-loop.time())
            except StreamStop:raise
            except ProbeFailure as exc:
                if str(exc) in ('AUTHENTICATION_REJECTED','RATE_LIMITED'):
                    record('GAP',reason=stage);raise StreamStop(str(exc)) from None
                record('GAP',reason=stage)
            except StreamGap as exc:record('GAP',reason=str(exc))
            except TimeoutError:
                if loop.time()>=deadline:raise StreamStop('BUDGET_EXHAUSTED') from None
                record('GAP',reason=stage)
            except Exception:record('GAP',reason=stage)
            if messages>=policy.max_messages:raise StreamStop('BUDGET_EXHAUSTED')
            if reconnects>=policy.max_reconnects:
                record('GAP',reason='RECONNECT_BUDGET_EXHAUSTED');raise StreamStop('FAILED')
            reconnects+=1
            delay=min(30,policy.backoff_seconds*2**min(reconnects-1,4),max(0,deadline-loop.time()))
            await _interruptible(asyncio.sleep(delay),stop,delay+1)
    except StreamStop as exc:reason=str(exc)
    except asyncio.CancelledError:
        reason='SIGNAL';raise
    finally:journal.record('STOPPED',at_ms=clock_ms(),reason=reason)
    return reason


@contextmanager
def writer_lock(path):
    path=Path(path);path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)&0o077:
            raise ValueError('STREAM_LOCK_PERMISSIONS')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('STREAM_WRITER_ALREADY_RUNNING') from None
        yield
    finally:os.close(fd)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operator-read',action='store_true')
    parser.add_argument('--credential-file',type=Path)
    parser.add_argument('--journal',type=Path)
    parser.add_argument('--seconds',type=int,default=21600)
    parser.add_argument('--max-messages',type=int,default=100000)
    parser.add_argument('--lease-worker',choices=('POST','PUT'),help=argparse.SUPPRESS)
    args=parser.parse_args(argv)
    if not args.operator_read:
        print(json.dumps({'status':'NOT_REQUESTED','submission_enabled':False}));return 0
    if args.credential_file is None or args.lease_worker is None and args.journal is None:
        parser.error('explicit credential file and journal required')
    try:
        policy=CapturePolicy(seconds=args.seconds,max_messages=args.max_messages)
        key,secret=load_credentials(args.credential_file)
        if args.lease_worker:
            try:print(json.dumps({'listenKey':lease_request(args.lease_worker,key)}))
            except ProbeFailure as exc:
                reason=str(exc) if str(exc) in ('AUTHENTICATION_REJECTED','RATE_LIMITED') else 'REQUEST_FAILED'
                print(json.dumps({'reason':reason}))
            return 0
        account_ref=hashlib.sha256(('BINANCE_USDM_DEMO:'+key).encode()).hexdigest()
        del key,secret
        async def run():
            stop=asyncio.Event();loop=asyncio.get_running_loop()
            for sig in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(sig,stop.set)
            try:
                with writer_lock(args.journal.with_suffix('.lock')):
                    with UserStreamJournal(args.journal,account_ref_sha256=account_ref) as store:
                        reason=await capture(store,source_sha256=source_hash(),
                            lease=lambda method:lease_child(method,args.credential_file),connect=connect_socket,
                            stop=stop,policy=policy)
                        print(json.dumps({'reason':reason,'snapshot':store.snapshot()}))
                        return 0 if reason in ('SIGNAL','BUDGET_EXHAUSTED') else 78 if reason=='AUTHENTICATION_REJECTED' else 1
            finally:
                for sig in (signal.SIGINT,signal.SIGTERM):loop.remove_signal_handler(sig)
        os.umask(0o077)
        return asyncio.run(run())
    except Exception:
        # No formatted exception or traceback may expose a lease or API header.
        print(json.dumps({'reason':'STREAM_CAPTURE_FAILED','submission_enabled':False}));return 1


if __name__=='__main__':raise SystemExit(main())
