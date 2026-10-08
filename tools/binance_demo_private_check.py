"""Explicit operator Demo account/stream checks; no order or cash mutation API.

Default is offline. --operator-check reads one permission-checked Demo file.
The entire child has a 120 second deadline. Closed connections do not delete the
account's potentially shared listen key. Every completed attempt is retained.
"""
import argparse
import asyncio
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from quantos.contracts.binance_demo import DEMO_REST_BASE, DEMO_WEBSOCKET_BASE
from quantos.contracts.binance_demo_private import DemoPrivateCheck, PrivateCheckStep
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_private_checks import load_report, save_report
from tools.binance_demo_readiness import NoRedirect, ProbeFailure, collect as readiness, write_receipt

_GET = {'/fapi/v1/time', '/fapi/v1/exchangeInfo', '/fapi/v3/account',
        '/fapi/v1/openOrders', '/fapi/v1/userTrades'}


def now_ms(): return time.time_ns() // 1_000_000


def source_hash():
    root = Path(__file__).resolve().parents[1]
    names = [__file__, root/'quantos/contracts/binance_demo_private.py',
             root/'tools/binance_demo_readiness.py', root/'services/research/demo_private_checks.py']
    return hashlib.sha256(b'\0'.join(Path(p).read_bytes() for p in names)).hexdigest()


def load_credentials(path):
    """Never source a shell file or fall back to production credentials."""
    try:
        path = Path(path)
        if path.is_symlink() or path.parent.is_symlink(): raise ValueError
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) & 0o077:
            raise ProbeFailure('CREDENTIAL_FILE_PERMISSIONS')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as f:
            metadata = os.fstat(f.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                    or stat.S_IMODE(metadata.st_mode) & 0o077):
                raise ProbeFailure('CREDENTIAL_FILE_PERMISSIONS')
            raw = f.read(8193)
        if len(raw) > 8192: raise ValueError
        value = load_json_without_duplicate_keys(raw.decode())
        if (not isinstance(value, dict) or set(value) != {'environment', 'rest_base', 'api_key', 'api_secret'}
                or value['environment'] != 'BINANCE_USDM_DEMO' or value['rest_base'] != DEMO_REST_BASE
                or any(not isinstance(value[n], str) or not re.fullmatch('[A-Za-z0-9]{64}', value[n])
                       for n in ('api_key', 'api_secret'))): raise ValueError
        return value['api_key'], value['api_secret']
    except ProbeFailure: raise
    except Exception: raise ProbeFailure('CREDENTIAL_FILE_INVALID') from None


def request_json(method, path, *, params, headers):
    if not (method == 'GET' and path in _GET or method == 'POST' and path == '/fapi/v1/listenKey'):
        raise ProbeFailure('ENDPOINT_NOT_ALLOWED')
    request = urllib.request.Request(DEMO_REST_BASE + path + ('?' + params if params else ''),
                                     headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
            if not 200 <= response.status < 300: raise ProbeFailure('REQUEST_FAILED')
            raw = response.read(2_000_001)
    except urllib.error.HTTPError as exc:
        status = exc.code; exc.close()
        raise ProbeFailure('RATE_LIMITED' if status in (418, 429) else
            'AUTHENTICATION_REJECTED' if status in (401, 403) else 'REQUEST_FAILED') from None
    except ProbeFailure: raise
    except Exception: raise ProbeFailure('REQUEST_FAILED') from None
    if len(raw) > 2_000_000: raise ProbeFailure('RESPONSE_TOO_LARGE')
    try: return load_json_without_duplicate_keys(raw.decode())
    except Exception: raise ProbeFailure('RESPONSE_INVALID') from None


def websocket_probe(token):
    from websockets.asyncio.client import connect
    logger = logging.Logger('quantos.demo.private.check', level=logging.CRITICAL)
    logger.addHandler(logging.NullHandler()); logger.propagate = False
    async def run():
        result = {'connections': 0, 'pongs': 0, 'events': 0}
        for _ in range(2):
            async with connect(DEMO_WEBSOCKET_BASE + '/private/ws/' + token,
                    open_timeout=10, close_timeout=2, ping_interval=None,
                    max_size=16384, max_queue=16, logger=logger) as websocket:
                result['connections'] += 1
                pong = await websocket.ping()
                await asyncio.wait_for(pong, timeout=5)
                result['pongs'] += 1
                deadline = time.monotonic() + 3
                for _ in range(100):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: break
                    try: raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
                    except asyncio.TimeoutError: break
                    body = load_json_without_duplicate_keys(raw)
                    if (not isinstance(body, dict) or body.get('e') == 'listenKeyExpired'
                            or not isinstance(body.get('e'), str)):
                        raise ProbeFailure('WEBSOCKET_CHECK_FAILED')
                    result['events'] += 1
        return result
    try: return asyncio.run(asyncio.wait_for(run(), timeout=40))
    except Exception: raise ProbeFailure('WEBSOCKET_CHECK_FAILED') from None


def collect(*, credential_file, operator_check=False, request=None, websocket_probe=None,
            clock_ms=None, readiness_output=None):
    request = request or request_json
    websocket_probe = websocket_probe or globals()['websocket_probe']
    clock_ms = clock_ms or now_ms
    start = clock_ms(); steps = []; stage = 'credentials'
    status = 'NOT_REQUESTED'; ws = {'connections': 0, 'pongs': 0, 'events': 0}
    if operator_check:
        try:
            key, secret = load_credentials(credential_file)
            def passed(name, rows=None):
                steps.append(PrivateCheckStep(name=name, status='PASSED', reason='CHECK_SUCCEEDED', rows=rows))
            passed(stage)
            stage = 'public'
            receipt = readiness(get=lambda path, **kw: request('GET', path, **kw),
                clock_ms=clock_ms, account_read=True,
                environ={'BINANCE_TESTNET_API_KEY': key, 'BINANCE_TESTNET_API_SECRET': secret})
            if readiness_output is not None: write_receipt(readiness_output, receipt)
            if receipt.public.status != 'READY': raise ProbeFailure(receipt.public.reason)
            passed(stage); stage = 'account'
            if receipt.account.status != 'READY': raise ProbeFailure(receipt.account.reason)
            passed(stage)
            offset = receipt.public.clock_offset_ms
            def signed(path, parameters):
                query = urllib.parse.urlencode(parameters | {'timestamp': clock_ms()+offset, 'recvWindow': 5000})
                signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
                return request('GET', path, params=query+'&signature='+signature, headers={'X-MBX-APIKEY': key})
            for symbol in ('BTCUSDT', 'BTCUSDC'):
                for kind, path in (('orders', '/fapi/v1/openOrders'), ('trades', '/fapi/v1/userTrades')):
                    stage = symbol+'_'+kind
                    params = {'symbol': symbol}
                    if kind == 'trades': params |= {'startTime': start-900_000, 'endTime': start, 'limit': 100}
                    rows = signed(path, params)
                    if not isinstance(rows, list) or len(rows) > 1000: raise ProbeFailure('RESPONSE_INVALID')
                    for row in rows:
                        if (not isinstance(row, dict) or row.get('symbol') != symbol
                                or type(row.get('orderId')) is not int or row['orderId'] <= 0):
                            raise ProbeFailure('RESPONSE_INVALID')
                    if kind == 'trades' and len(rows) >= 100: raise ProbeFailure('HISTORY_PAGE_INCOMPLETE')
                    passed(stage, len(rows))
            stage = 'listen_key'
            lease = request('POST', '/fapi/v1/listenKey', params='', headers={'X-MBX-APIKEY': key})
            if (not isinstance(lease, dict) or not isinstance(lease.get('listenKey'), str)
                    or not re.fullmatch('[A-Za-z0-9_-]{32,256}', lease['listenKey'])):
                raise ProbeFailure('RESPONSE_INVALID')
            passed(stage); stage = 'websocket'
            observed = websocket_probe(lease['listenKey'])
            if (not isinstance(observed, dict) or set(observed) != {'connections', 'pongs', 'events'}
                    or any(type(observed[k]) is not int for k in observed)
                    or observed['connections'] != 2 or observed['pongs'] != 2
                    or not 0 <= observed['events'] <= 200): raise ProbeFailure('WEBSOCKET_CHECK_FAILED')
            ws = observed; passed(stage); status = 'CHECKS_PASSED'
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ProbeFailure) else 'REQUEST_FAILED'
            allowed = PrivateCheckStep.model_fields['reason'].annotation.__args__
            if reason not in allowed: reason = 'REQUEST_FAILED'
            steps.append(PrivateCheckStep(name=stage, status='FAILED', reason=reason))
            status = 'FAILED'
    return DemoPrivateCheck(observed_at_ms=start, completed_at_ms=clock_ms(), source_sha256=source_hash(),
        connectivity_status=status, steps=steps, websocket_connections=ws['connections'],
        websocket_pongs=ws['pongs'], websocket_events=ws['events'],
        event_delivery_status='EVENTS_OBSERVED' if ws['events'] else 'UNKNOWN')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credential-file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--operator-check', action='store_true')
    parser.add_argument('--capture-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    start = now_ms()
    if args.capture_worker:
        result = collect(credential_file=args.credential_file, operator_check=args.operator_check,
            readiness_output=args.output_dir.parent/'binance_demo_readiness.json')
        print(result.model_dump_json()); return 0
    if args.operator_check:
        cmd = [sys.executable, '-m', 'tools.binance_demo_private_check', '--capture-worker',
               '--operator-check', '--credential-file', str(args.credential_file), '--output-dir', str(args.output_dir)]
        names = ('PATH', 'LANG', 'LC_ALL', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR')
        env = {n: os.environ[n] for n in names if n in os.environ}
        reason = 'REQUEST_FAILED'
        try:
            child = subprocess.run(cmd, cwd=Path(__file__).resolve().parents[1], env=env,
                capture_output=True, timeout=120, check=True)
            if len(child.stdout) > 16384: raise ValueError
            result = DemoPrivateCheck.model_validate_json(child.stdout)
            if result.observed_at_ms < start or result.completed_at_ms > now_ms(): raise ValueError
        except Exception as exc:
            if isinstance(exc, subprocess.TimeoutExpired): reason = 'REQUEST_TIMEOUT'
            result = DemoPrivateCheck(observed_at_ms=start, completed_at_ms=now_ms(), source_sha256=source_hash(),
                connectivity_status='FAILED', steps=[PrivateCheckStep(name='worker', status='FAILED', reason=reason)])
    else:
        result = collect(credential_file=args.credential_file)
    save_report(args.output_dir, result)
    print(result.model_dump_json())
    return 0 if result.connectivity_status == 'CHECKS_PASSED' else 2


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception:
        print(json.dumps({'status': 'FAILED', 'reason': 'PRIVATE_CHECK_FAILED'})); raise SystemExit(3)
