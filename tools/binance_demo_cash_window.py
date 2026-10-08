"""Bounded operator GET capture for a forward Demo wallet/income comparison.

One credential identity is loaded once per run. This collector cannot submit
orders or change balances. Parent timeout also covers a blocked network read.
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
import urllib.error
import urllib.parse
import urllib.request

from quantos.contracts.binance_demo import DEMO_REST_BASE
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_cash_window import (
    RETRY_SCHEMA, LONG_SCHEMA, LONG_MAX_SECONDS, MAX_ATTEMPTS, MAX_BYTES, MAX_PAGES, GUARD_MS, REASONS, assess, load_capture, normalize_wallet,
    save_capture, validate, window_bounds,
)
from services.research.demo_statement import normalize as normalize_statement
from tools.binance_demo_private_check import load_credentials
from tools.binance_demo_readiness import NoRedirect, ProbeFailure

PRIVATE = {'/fapi/v3/account', '/fapi/v1/income'}


def now_ms():
    return time.time_ns()//1_000_000


def source_hash():
    root = Path(__file__).resolve().parents[1]
    names = ('tools/binance_demo_cash_window.py', 'services/research/demo_cash_window.py',
             'services/research/demo_statement.py', 'quantos/contracts/binance_demo_account.py',
             'tools/binance_demo_private_check.py', 'tools/binance_demo_readiness.py',
             'quantos/contracts/binance_demo.py', 'services/governance/json_integrity.py',
             'services/execution/perpetual_ledger_v2.py')
    return hashlib.sha256(b'\0'.join((root/name).read_bytes() for name in names)).hexdigest()


def get_response(path, *, params, headers, opener=None):
    if path not in PRIVATE | {'/fapi/v1/time'} or path == '/fapi/v1/time' and (params or headers):
        raise ProbeFailure('REQUEST_FAILED')
    request = urllib.request.Request(DEMO_REST_BASE+path+('?' + params if params else ''), headers=headers, method='GET')
    try:
        with (opener or urllib.request.build_opener(NoRedirect())).open(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise ProbeFailure('REQUEST_FAILED')
            raw = response.read(2_000_001)
    except urllib.error.HTTPError as exc:
        status = exc.code; exc.close()
        raise ProbeFailure('RATE_LIMITED' if status in (418,429) else
                           'AUTHENTICATION_REJECTED' if status in (401,403) else
                           'REQUEST_FAILED' if status==408 or 500<=status<600 else 'RESPONSE_INVALID') from None
    except ProbeFailure:
        raise
    except Exception:
        raise ProbeFailure('REQUEST_FAILED') from None
    if len(raw) > 2_000_000:
        raise ProbeFailure('RESPONSE_TOO_LARGE')
    return raw


def collect(*, credential_file, observe_seconds=60, operator_read=False, get=None, clock_ms=None, sleep=None,
            credential_reader=None, window_opened=None, observe_wait=None, opening_prefix=None, opening_only=False, long_window=False):
    if type(opening_only) is not bool or opening_only and opening_prefix is not None:
        raise ValueError('CASH_WINDOW_OPENING_MODE')
    if type(long_window) is not bool:
        raise ValueError('CASH_WINDOW_LONG_MODE')
    schema = LONG_SCHEMA if long_window else RETRY_SCHEMA
    maximum = LONG_MAX_SECONDS if long_window else 3600
    get = get or get_response
    clock_ms = clock_ms or now_ms
    sleep = sleep or time.sleep
    started = clock_ms()
    report = {'schema': schema, 'status': 'NOT_REQUESTED', 'observed_at_ms': started, 'completed_at_ms': started,
              'account_ref_sha256': None, 'source_sha256': source_hash(), 'observe_seconds': observe_seconds,
              'reads': [], 'failures': [], 'attempts': []}
    validate(report)
    resumed_credentials=None
    if opening_prefix is not None:
        validate(opening_prefix)
        if not operator_read or opening_prefix['schema']!=schema or opening_prefix['status']!='OPENING_CAPTURED' or \
            opening_prefix['source_sha256']!=source_hash() or not 0<=started-opening_prefix['completed_at_ms']<=(maximum-observe_seconds)*1000:
            raise ValueError('CASH_WINDOW_RESUME_PREFIX')
        resumed_credentials=(credential_reader or load_credentials)(credential_file)
        account=hashlib.sha256(('BINANCE_USDM_DEMO:'+resumed_credentials[0]).encode()).hexdigest()
        if account!=opening_prefix['account_ref_sha256']:raise ValueError('CASH_WINDOW_RESUME_ACCOUNT')
        report=json.loads(json.dumps(opening_prefix,allow_nan=False))
        report['observe_seconds']=max(30,(started-report['completed_at_ms'])//1000+observe_seconds)
    if not operator_read:
        return report
    current_kind, current_sweep, current_page, current_at = 'credentials', 0, 0, started
    deadline = time.monotonic()+observe_seconds+220
    offset = 0
    try:
        key, secret = resumed_credentials or (credential_reader or load_credentials)(credential_file)
        report['account_ref_sha256'] = hashlib.sha256(('BINANCE_USDM_DEMO:'+key).encode()).hexdigest()

        def read(kind, path, *, sweep=0, page=0, params=None):
            nonlocal current_kind, current_sweep, current_page, current_at, offset
            current_kind, current_sweep, current_page = kind, sweep, page
            for number in range(1, MAX_ATTEMPTS+1):
                current_at = clock_ms()
                attempt = {'kind': kind, 'sweep': sweep, 'page': page, 'attempt': number,
                           'requested_at_ms': current_at, 'received_at_ms': current_at,
                           'account_ref_sha256': report['account_ref_sha256'] if path in PRIVATE else None,
                           'outcome': 'FAILED', 'reason': None, 'response_sha256': None, 'server_time_ms': None}
                try:
                    if time.monotonic()+10 > deadline:
                        raise ProbeFailure('CAPTURE_BUDGET_EXHAUSTED')
                    query, headers = '', {}
                    if path in PRIVATE:
                        values = dict(params or {}, timestamp=current_at+offset, recvWindow=5000)
                        query = urllib.parse.urlencode(values)
                        query += '&signature='+hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
                        headers = {'X-MBX-APIKEY': key}
                    try:
                        raw = get(path, params=query, headers=headers)
                    except ProbeFailure:
                        raise
                    except Exception:
                        raise ProbeFailure('REQUEST_FAILED') from None
                    received = clock_ms()
                    attempt['response_sha256'] = hashlib.sha256(raw).hexdigest()
                    if not current_at <= received <= current_at+10000:
                        raise ProbeFailure('CLOCK_UNRELIABLE')
                    try:
                        payload = load_json_without_duplicate_keys(raw)
                        if kind.startswith('clock'):
                            server = payload['serverTime']
                            if type(server) is not int or server <= 0:
                                raise ValueError('invalid server time')
                            attempt['server_time_ms'] = server
                            new_offset = server-(current_at+received)//2
                            if received-current_at > 5000 or abs(new_offset) > 2000 or (kind=='clock_after' and abs(new_offset-offset)>500):
                                raise ProbeFailure('CLOCK_UNRELIABLE')
                            offset = new_offset
                            data = {'serverTime': server}
                        elif kind.startswith('wallet'):
                            data = normalize_wallet([{'asset': a['asset'], 'wallet': a['walletBalance'],
                                                      'updated_at_ms': a['updateTime']} for a in payload['assets']])
                            if any(a['updated_at_ms'] > received+GUARD_MS for a in data):
                                raise ValueError('wallet update from future')
                        else:
                            data = normalize_statement('income', payload, [])
                            if any(not params['startTime'] <= r['time'] <= params['endTime'] for r in data):
                                raise ValueError('out of query window')
                    except ProbeFailure:
                        raise
                    except Exception:
                        raise ProbeFailure('RESPONSE_INVALID') from None
                except ProbeFailure as exc:
                    reason = str(exc) if str(exc) in REASONS else 'REQUEST_FAILED'
                    attempt.update(received_at_ms=clock_ms(), reason=reason)
                    report['attempts'].append(attempt)
                    if reason=='REQUEST_FAILED' and number<MAX_ATTEMPTS:
                        sleep(.25*2**(number-1))
                        continue
                    raise ProbeFailure(reason) from None
                attempt.update(received_at_ms=received, outcome='SUCCESS')
                report['attempts'].append(attempt)
                report['reads'].append({'kind': kind, 'requested_at_ms': current_at, 'received_at_ms': received,
                                        'account_ref_sha256': attempt['account_ref_sha256'],
                                        'response_sha256': attempt['response_sha256'], 'sweep': sweep, 'page': page, 'data': data})
                return data

        if opening_prefix is None:
            read('clock_before', '/fapi/v1/time')
            read('wallet_before', '/fapi/v3/account')
        else:
            original=report['reads'][0]
            offset=original['data']['serverTime']-(original['requested_at_ms']+original['received_at_ms'])//2
        current_kind,current_sweep,current_page,current_at='worker',0,0,clock_ms()
        if (window_opened is not None or opening_only) and opening_prefix is None:
            # Retain the opening wallet before releasing a bounded Demo task.
            # The callback receives a copy, never mutable collector state.
            prefix=json.loads(json.dumps(report,allow_nan=False))
            prefix.update(status='OPENING_CAPTURED',completed_at_ms=prefix['reads'][-1]['received_at_ms'])
            validate(prefix)
            if window_opened is not None:
                window_opened(json.loads(json.dumps(prefix,allow_nan=False)))
            if opening_only:
                return prefix
        if observe_wait is None:
            sleep(observe_seconds)
        else:
            actual=observe_wait(observe_seconds)
            if type(actual) is not int or not 30<=actual<=observe_seconds or (
                clock_ms()-report['reads'][-1]['received_at_ms']<actual*1000):
                raise ProbeFailure('CLOCK_UNRELIABLE')
            report['observe_seconds']=actual
        if opening_prefix is not None:
            report['observe_seconds']=(clock_ms()-report['reads'][1]['received_at_ms'])//1000
            if not 30<=report['observe_seconds']<=maximum:raise ProbeFailure('CAPTURE_BUDGET_EXHAUSTED')
        read('wallet_after', '/fapi/v3/account')
        read('clock_after', '/fapi/v1/time')
        bounds = window_bounds(report['reads'])
        sleep(max(0, (bounds['query_end_ms']+15000-clock_ms())/1000))
        for sweep in (1, 2):
            if sweep == 2:
                sleep(10)
            for page in range(1, MAX_PAGES+1):
                rows = read('income', '/fapi/v1/income', sweep=sweep, page=page,
                            params={'startTime': bounds['query_start_ms'], 'endTime': bounds['query_end_ms'],
                                    'page': page, 'limit': 1000})
                if len(rows) < 1000:
                    break
        report['status'] = 'CAPTURED'
    except Exception as exc:
        reason = str(exc) if isinstance(exc, ProbeFailure) and str(exc) in REASONS else 'REQUEST_FAILED'
        report['failures'].append({'kind': current_kind, 'sweep': current_sweep, 'page': current_page,
                                   'requested_at_ms': current_at, 'received_at_ms': report['attempts'][-1]['received_at_ms'] if report['attempts'] and current_kind!='worker' else clock_ms(), 'reason': reason})
        report['status'] = 'PARTIAL' if report['reads'] else 'FAILED'
    report['completed_at_ms'] = clock_ms()
    return validate(report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--credential-file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--observe-seconds', type=int, choices=range(30,3601), default=60, metavar='30..3600')
    parser.add_argument('--operator-read', action='store_true')
    parser.add_argument('--capture-worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.capture_worker:
        print(json.dumps(collect(credential_file=args.credential_file, observe_seconds=args.observe_seconds,
                                 operator_read=args.operator_read)))
        return 0
    started = now_ms()
    if args.operator_read:
        command = [sys.executable, '-m', 'tools.binance_demo_cash_window', '--capture-worker', '--operator-read',
                   '--credential-file', str(args.credential_file), '--output-dir', str(args.output_dir),
                   '--observe-seconds', str(args.observe_seconds)]
        allowed = ('PATH','LANG','LC_ALL','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
                   'http_proxy','https_proxy','all_proxy','no_proxy','SSL_CERT_FILE','SSL_CERT_DIR')
        try:
            child = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                   check=True, timeout=args.observe_seconds+230,
                                   env={k: os.environ[k] for k in allowed if k in os.environ})
            if len(child.stdout) > MAX_BYTES:
                raise ValueError('worker output too large')
            report = validate(load_json_without_duplicate_keys(child.stdout))
            if not started <= report['observed_at_ms'] <= report['completed_at_ms'] <= now_ms():
                raise ValueError('worker output clock')
            if report['source_sha256'] != source_hash() or report['observe_seconds'] != args.observe_seconds:
                raise ValueError('worker output identity')
        except Exception as exc:
            report = collect(credential_file=args.credential_file, observe_seconds=args.observe_seconds)
            report.update(status='FAILED', observed_at_ms=started, completed_at_ms=now_ms())
            report['failures'] = [{'kind': 'worker', 'sweep': 0, 'page': 0, 'requested_at_ms': started,
                                   'received_at_ms': report['completed_at_ms'],
                                   'reason': 'CAPTURE_BUDGET_EXHAUSTED' if isinstance(exc,subprocess.TimeoutExpired) else 'REQUEST_FAILED'}]
    else:
        report = collect(credential_file=args.credential_file, observe_seconds=args.observe_seconds)
    save_capture(args.output_dir, report)
    result = assess(report)
    print(json.dumps({k: result[k] for k in ('status','capture_status','read_count','income_count','issues','ledger_reconciliation')}))
    return 0 if report['status'] == 'CAPTURED' else 2


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('{"status":"FAILED","reason":"CASH_WINDOW_CAPTURE_FAILED"}')
        raise SystemExit(3)
