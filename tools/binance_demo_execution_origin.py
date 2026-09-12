"""Bounded operator GET capture of Demo execution inputs; never submits orders."""
import argparse
from decimal import Decimal
import gzip
import hashlib
import hmac
import io
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
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_execution_origin import (
    ACCOUNT_KINDS, KINDS, MAX_BYTES, PUBLIC, REASONS, ACCOUNT_TERMS_SCHEMA, assess, normalize, save, validate,
    target_configuration,
)
from tools.binance_demo_private_check import load_credentials
from tools.binance_demo_readiness import NoRedirect, ProbeFailure

PATHS = {
    'clock_before': '/fapi/v1/time', 'clock_after': '/fapi/v1/time',
    'config_before': '/fapi/v1/accountConfig', 'config_after': '/fapi/v1/accountConfig',
    'account_before': '/fapi/v3/account', 'account_after': '/fapi/v3/account',
    'symbols_before': '/fapi/v1/symbolConfig', 'symbols_after': '/fapi/v1/symbolConfig',
    'positions': '/fapi/v3/positionRisk', 'orders': '/fapi/v1/openOrders',
    'algo_orders': '/fapi/v1/openAlgoOrders', 'rules': '/fapi/v1/exchangeInfo',
    'fees': '/fapi/v1/commissionRate', 'brackets': '/fapi/v1/leverageBracket',
    'mark': '/fapi/v1/premiumIndex', 'book': '/fapi/v1/ticker/bookTicker',
}
READ_PLAN = tuple((kind, PATHS[kind]) for kind in KINDS)
PUBLIC_PATHS = {PATHS[kind] for kind in PUBLIC}
SYMBOL_KINDS = {'fees', 'brackets', 'mark', 'book'}


def now_ms():
    return time.time_ns() // 1_000_000


def source_hash():
    root = Path(__file__).resolve().parents[1]
    names = ('tools/binance_demo_execution_origin.py', 'services/research/demo_execution_origin.py',
             'quantos/contracts/binance_demo_account.py', 'services/research/demo_account_baseline.py',
             'services/research/demo_statement.py', 'tools/binance_demo_private_check.py',
             'tools/binance_demo_readiness.py', 'quantos/contracts/binance_demo.py',
             'services/governance/json_integrity.py', 'services/execution/perpetual_ledger_v2.py')
    return hashlib.sha256(b'\0'.join((root/name).read_bytes() for name in names)).hexdigest()


def get_response(path, *, params, headers):
    if path not in PATHS.values() or path in PUBLIC_PATHS and headers:
        raise ProbeFailure('REQUEST_FAILED')
    if path in PUBLIC_PATHS:
        expected = ('symbol=BTCUSDT', 'symbol=BTCUSDC') if path in (PATHS['mark'], PATHS['book']) else ('',)
        if params not in expected:
            raise ProbeFailure('REQUEST_FAILED')
    request_headers=dict(headers)
    if path in (PATHS['rules'],PATHS['symbols_before']):request_headers['Accept-Encoding']='gzip'
    request = urllib.request.Request(DEMO_REST_BASE+path+('?' + params if params else ''), headers=request_headers, method='GET')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise ProbeFailure('RESPONSE_INVALID')
            raw = response.read(2_000_001)
            encoding=response.headers.get('Content-Encoding','identity').strip().lower()
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise ProbeFailure('RATE_LIMITED' if status in (418, 429) else
                           'AUTHENTICATION_REJECTED' if status in (401, 403) else
                           'REQUEST_FAILED' if status == 408 or 500 <= status < 600 else 'RESPONSE_INVALID') from None
    except ProbeFailure:
        raise
    except Exception:
        raise ProbeFailure('REQUEST_FAILED') from None
    if len(raw) > 2_000_000:
        raise ProbeFailure('RESPONSE_TOO_LARGE')
    if encoding=='gzip':
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as body:raw=body.read(2_000_001)
        except Exception:
            raise ProbeFailure('RESPONSE_INVALID') from None
        if len(raw)>2_000_000:raise ProbeFailure('RESPONSE_TOO_LARGE')
    elif encoding!='identity':raise ProbeFailure('RESPONSE_INVALID')
    # The retained response digest is of the bounded decoded JSON entity,
    # independent of the optional HTTP compression transport representation.
    return raw


def collect(*, credential_file, symbol, operator_read=False, get=None, clock_ms=None, credential_reader=None):
    get, clock_ms = get or get_response, clock_ms or now_ms
    started = clock_ms()
    report = {'schema': ACCOUNT_TERMS_SCHEMA, 'status': 'NOT_REQUESTED', 'symbol': symbol,
              'observed_at_ms': started, 'completed_at_ms': started,
              'account_ref_sha256': None, 'source_sha256': source_hash(), 'reads': [], 'failures': []}
    validate(report)
    if not operator_read:
        return report
    deadline = time.monotonic()+105
    kind, sent, offset = 'credentials', started, 0
    response_hash, server_time = None, None
    try:
        # Internal composition seam; the CLI still has only its original
        # operator-owned file contract. Protected tools supply their fixed
        # independently controlled reader without patching this module.
        key, secret = (credential_reader or load_credentials)(credential_file)
        report['account_ref_sha256'] = hashlib.sha256(('BINANCE_USDM_DEMO:'+key).encode()).hexdigest()
        for kind, path in READ_PLAN:
            sent = clock_ms()
            response_hash, server_time = None, None
            if time.monotonic()+10 > deadline:
                raise ProbeFailure('CAPTURE_BUDGET_EXHAUSTED')
            params = {'symbol': symbol} if kind in SYMBOL_KINDS else {}
            headers = {}
            if kind not in PUBLIC:
                params.update(timestamp=sent+offset, recvWindow=5000)
                query = urllib.parse.urlencode(params)
                query += '&signature='+hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
                headers = {'X-MBX-APIKEY': key}
            else:
                query = urllib.parse.urlencode(params)
            raw = get(path, params=query, headers=headers)
            received = clock_ms()
            if type(raw) is not bytes or len(raw) > 2_000_000:
                raise ProbeFailure('RESPONSE_TOO_LARGE')
            response_hash = hashlib.sha256(raw).hexdigest()
            try:
                load_json_without_duplicate_keys(raw)
                native = json.loads(raw, parse_float=Decimal)
                data = normalize(kind, native, symbol)
                config = target_configuration(native,symbol) if kind.startswith('symbols') else None
            except Exception:
                raise ProbeFailure('RESPONSE_INVALID') from None
            if not 0 <= received-sent <= 10000:
                raise ProbeFailure('CLOCK_UNRELIABLE')
            if kind.startswith('clock'):
                server_time = data['serverTime']
                candidate = data['serverTime']-(sent+received)//2
                if received-sent > 5000 or abs(candidate) > 2000 or kind == 'clock_after' and abs(candidate-offset) > 500:
                    raise ProbeFailure('CLOCK_UNRELIABLE')
                offset = candidate
            read = {'kind': kind, 'requested_at_ms': sent, 'received_at_ms': received,
                    'account_ref_sha256': None if kind in PUBLIC else report['account_ref_sha256'],
                    'response_sha256': response_hash, 'data': data,
                    **({'target_config':config} if kind.startswith('symbols') else {})}
            # Validate the retained prefix before accepting it as an observation.
            candidate_report = dict(report, status='FAILED', reads=[*report['reads'], read], completed_at_ms=received,
                                    failures=[{'kind': 'worker', 'requested_at_ms': received,
                                               'received_at_ms': received, 'reason': 'REQUEST_FAILED',
                                               'response_sha256': None, 'server_time_ms': None}])
            validate(candidate_report)
            report['reads'].append(read)
        report['status'] = 'CAPTURED'
    except Exception as exc:
        report['status'] = 'FAILED'
        reason = str(exc) if isinstance(exc, ProbeFailure) and str(exc) in REASONS else 'REQUEST_FAILED'
        report['failures'] = [{'kind': kind, 'requested_at_ms': sent, 'received_at_ms': clock_ms(), 'reason': reason,
                               'response_sha256': response_hash, 'server_time_ms': server_time}]
    report['completed_at_ms'] = clock_ms()
    return validate(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credential-file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--symbol', choices=('BTCUSDT', 'BTCUSDC'), required=True)
    parser.add_argument('--operator-read', action='store_true')
    parser.add_argument('--capture-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.capture_worker:
        print(json.dumps(collect(credential_file=args.credential_file, symbol=args.symbol, operator_read=args.operator_read)))
        return 0
    started = now_ms()
    if args.operator_read:
        command = [sys.executable, '-m', 'tools.binance_demo_execution_origin', '--capture-worker', '--operator-read',
                   '--credential-file', str(args.credential_file), '--output-dir', str(args.output_dir), '--symbol', args.symbol]
        names = ('PATH', 'LANG', 'LC_ALL', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SSL_CERT_FILE', 'SSL_CERT_DIR')
        try:
            child = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, check=True,
                                   timeout=115, env={n: os.environ[n] for n in names if n in os.environ})
            if len(child.stdout) > MAX_BYTES:
                raise ValueError('ORIGIN_SIZE')
            report = validate(load_json_without_duplicate_keys(child.stdout))
            if not started <= report['observed_at_ms'] <= report['completed_at_ms'] <= now_ms():
                raise ValueError('ORIGIN_WORKER_CLOCK')
        except Exception as exc:
            report = collect(credential_file=args.credential_file, symbol=args.symbol)
            completed = now_ms()
            report.update(status='FAILED', observed_at_ms=started, completed_at_ms=completed,
                          failures=[{'kind': 'worker', 'requested_at_ms': started, 'received_at_ms': completed,
                                     'reason': 'CAPTURE_BUDGET_EXHAUSTED' if isinstance(exc, subprocess.TimeoutExpired) else 'REQUEST_FAILED',
                                     'response_sha256': None, 'server_time_ms': None}])
    else:
        report = collect(credential_file=args.credential_file, symbol=args.symbol)
    path = save(args.output_dir, report)
    summary = assess(report, as_of_ms=now_ms())
    print(json.dumps({'path': str(path), 'assessment': summary}))
    return 0 if report['status'] == 'CAPTURED' else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('{"status":"FAILED","reason":"ORIGIN_CAPTURE_FAILED"}')
        raise SystemExit(3)
