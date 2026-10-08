"""Operator-only bounded USD-M account inspection. Default is offline --check.

No order endpoints, SDK, credential files, environment credentials, or service.
Only a human runs --operator-read in a terminal; the agent uses mocked tests.
"""
import argparse
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import warnings

from services.research.gold_account_snapshot import SCHEMA, KINDS, FAILURES, normalize, assess
from services.research.gold_trade_observation import load_document, MAX_BYTES
from tools.gold_trade_observation import _write_private, _encode

TIME_PATH = '/fapi/v1/time'
PERMISSION_PATH = '/sapi/v1/account/apiRestrictions'
READ_PATHS = ('/fapi/v1/accountConfig', '/fapi/v3/account', '/fapi/v1/openOrders',
    '/fapi/v1/openAlgoOrders', '/fapi/v3/positionRisk', '/fapi/v1/symbolConfig',
    '/fapi/v1/openOrders', '/fapi/v1/openAlgoOrders', '/fapi/v3/account', '/fapi/v1/accountConfig')
PATHS = frozenset((TIME_PATH, PERMISSION_PATH, *READ_PATHS))
WRITE_PERMISSIONS = ('enableWithdrawals', 'enableInternalTransfer', 'enableMargin',
    'enableFutures', 'permitsUniversalTransfer', 'enableVanillaOptions', 'enableFixApiTrade',
    'enableSpotAndMarginTrading', 'enablePortfolioMarginTrading')
ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('tools/gold_account_capture.py', 'services/research/gold_account_snapshot.py',
           'services/research/gold_trade_observation.py', 'tools/gold_trade_observation.py')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('REDIRECT_REFUSED')


def _validate_request_error(value):
    if (not isinstance(value, dict) or set(value) != {'http_status', 'exchange_code', 'endpoint'}
            or type(value['http_status']) is not int or not 400 <= value['http_status'] <= 599
            or (value['exchange_code'] is not None and
                (type(value['exchange_code']) is not int or not -99999 <= value['exchange_code'] < 0))
            or not isinstance(value['endpoint'], str) or value['endpoint'] not in PATHS):
        raise ValueError('INVALID_REQUEST_DIAGNOSTIC')
    return value


class RequestFailure(ValueError):
    def __init__(self, reason, diagnostic):
        super().__init__(reason)
        self.diagnostic = _validate_request_error(diagnostic)


def get_response(path, *, query, headers):
    if path not in PATHS:
        raise ValueError('ENDPOINT_NOT_ALLOWED')
    if path == TIME_PATH and (query or headers):
        raise ValueError('ENDPOINT_NOT_ALLOWED')
    host = 'https://api.binance.com' if path == PERMISSION_PATH else 'https://fapi.binance.com'
    request = urllib.request.Request(host+path+('?' + query if query else ''),
                                     headers=headers, method='GET')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise ValueError('REQUEST_FAILED')
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('RESPONSE_INVALID')
        return raw
    except urllib.error.HTTPError as exc:
        status = exc.code
        code = None
        try:
            body = exc.read(16385)
            parsed = load_document(body) if len(body) <= 16384 else None
            candidate = parsed.get('code') if isinstance(parsed, dict) else None
            if type(candidate) is int and -99999 <= candidate < 0:
                code = candidate
        except Exception:
            pass
        finally:
            exc.close()
        reason = ('RATE_LIMITED' if status in (418, 429) else
                         'AUTHENTICATION_REJECTED' if status in (401, 403) else
                         'EXCHANGE_UNAVAILABLE' if 500 <= status < 600 else
                         'REQUEST_REJECTED' if 400 <= status < 500 else 'REQUEST_FAILED')
        if not 400 <= status <= 599:
            raise ValueError(reason) from None
        raise RequestFailure(reason, {'http_status': status, 'exchange_code': code, 'endpoint': path}) from None
    except TimeoutError:
        raise ValueError('NETWORK_TIMEOUT') from None
    except ssl.SSLError:
        raise ValueError('TLS_FAILED') from None
    except urllib.error.URLError as exc:
        reason = ('NETWORK_TIMEOUT' if isinstance(exc.reason, TimeoutError) else
                  'TLS_FAILED' if isinstance(exc.reason, ssl.SSLError) else 'NETWORK_UNAVAILABLE')
        raise ValueError(reason) from None
    except ValueError as exc:
        reason = str(exc) if str(exc) in ('REDIRECT_REFUSED', 'RESPONSE_INVALID') else 'REQUEST_FAILED'
        raise ValueError(reason) from None
    except Exception:
        raise ValueError('REQUEST_FAILED') from None


def source_hashes():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def permission_diagnostics(raw):
    value = load_document(raw)
    names = (*WRITE_PERMISSIONS, 'enableReading', 'ipRestrict')
    value = value if isinstance(value, dict) else {}
    fields = {k: 'MISSING' if k not in value else 'INVALID_TYPE' if type(value[k]) is not bool
              else 'ENABLED' if value[k] else 'DISABLED' for k in names}
    unknown = any((k.startswith('enable') or k.startswith('permits')) and k not in names
                  and not (k == 'enableFixReadOnly' and type(v) is bool) for k, v in value.items())
    check = {'fields': fields, 'unknown_permission_fields': unknown}
    check['status'] = _permission_verdict(check)
    return check


def _permission_verdict(check):
    fields = check['fields']
    safe = (not check['unknown_permission_fields']
            and all(fields[k] == 'DISABLED' for k in WRITE_PERMISSIONS)
            and all(fields[k] == 'ENABLED' for k in ('enableReading', 'ipRestrict')))
    return 'ACCEPTED' if safe else 'REJECTED'


def _validate_permission_check(check):
    if (not isinstance(check, dict) or set(check) != {'fields', 'unknown_permission_fields', 'status'}
            or not isinstance(check['fields'], dict)
            or set(check['fields']) != {*WRITE_PERMISSIONS, 'enableReading', 'ipRestrict'}
            or any(v not in ('MISSING', 'INVALID_TYPE', 'ENABLED', 'DISABLED') for v in check['fields'].values())
            or type(check['unknown_permission_fields']) is not bool
            or check['status'] != _permission_verdict(check)):
        raise ValueError('INVALID_PERMISSION_DIAGNOSTICS')
    return check


def _permissions(raw):
    if permission_diagnostics(raw)['status'] != 'ACCEPTED':
        raise ValueError('READ_ONLY_PERMISSION_REQUIRED')
    value = load_document(raw)
    return {k: value[k] for k in (*WRITE_PERMISSIONS, 'enableReading', 'ipRestrict')}


def _report(capture, permission_check=None, request_error=None):
    report = assess(capture, as_of_ms=capture['completed_at_ms']) | {'reason': capture['reason']}
    if permission_check is not None:
        report['permission_check'] = _validate_permission_check(permission_check)
    if request_error is not None:
        if capture['status'] != 'FAILED':
            raise ValueError('REQUEST_DIAGNOSTIC_STATUS_MISMATCH')
        report['request_error'] = _validate_request_error(request_error)
    return report


def collect(destination, *, api_key, api_secret, get=None, clock_ms=None):
    """A bounded human-controlled read, injectable for entirely offline tests.

    Destination must be an empty reserved directory. Prefix files survive a
    failure; no fallback endpoint, retries, or writes to an exchange exist.
    """
    destination = Path(destination).absolute()
    if destination.resolve() != destination or not destination.is_dir() or any(destination.iterdir()):
        raise ValueError('EMPTY_PRIVATE_DIRECTORY_REQUIRED')
    os.chmod(destination, 0o700)
    get = get or get_response
    clock_ms = clock_ms or (lambda: time.time_ns()//1_000_000)
    sources = source_hashes()
    start = clock_ms()
    capture = {'schema': SCHEMA, 'environment': 'BINANCE_USDM_MAINNET',
        'account_ref': _digest(uuid.uuid4().bytes), 'started_at_ms': start,
        'completed_at_ms': start, 'status': 'FAILED', 'reason': 'REQUEST_FAILED', 'reads': []}
    files = {}
    def save(name, value):
        raw = _encode(value)
        if len(raw) > MAX_BYTES:
            raise ValueError('ARCHIVE_SIZE_LIMIT')
        _write_private(destination/name, raw)
        files[name] = _digest(raw)
    previous = start
    permission_check = None
    request_error = None
    def now():
        nonlocal previous
        value = clock_ms()
        if type(value) is not int or value < previous:
            raise ValueError('CLOCK_UNRELIABLE')
        if value-start > 150000:
            raise ValueError('CAPTURE_TIMEOUT')
        previous = value
        return value
    try:
        sent = now()
        raw = get(TIME_PATH, query='', headers={})
        received = now()
        server = load_document(raw).get('serverTime')
        if type(server) is not int or server <= 0 or received-sent > 5000:
            raise ValueError('CLOCK_UNRELIABLE')
        offset = server-(sent+received)//2
        if abs(offset) > 2000:
            raise ValueError('CLOCK_UNRELIABLE')
        save('clock.json', {'sent_ms': sent, 'received_ms': received, 'server_ms': server,
                            'response_sha256': _digest(raw)})
        def signed(path):
            sent = now()
            query = urllib.parse.urlencode({'timestamp': sent+offset, 'recvWindow': 5000})
            signature = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
            raw = get(path, query=query+'&signature='+signature, headers={'X-MBX-APIKEY': api_key})
            return sent, now(), raw
        sent, received, raw = signed(PERMISSION_PATH)
        permission_check = permission_diagnostics(raw)
        save('permission-check.json', permission_check)
        permission = _permissions(raw)
        save('permissions.json', {'started_at_ms': sent, 'received_at_ms': received,
            'response_sha256': _digest(raw), 'data': permission})
        for index, (kind, path) in enumerate(zip(KINDS, READ_PATHS)):
            sent, received, raw = signed(path)
            try:
                data = normalize(kind, load_document(raw))
            except Exception:
                raise ValueError('RESPONSE_INVALID') from None
            row = {'kind': kind, 'started_at_ms': sent, 'received_at_ms': received,
                   'response_sha256': _digest(raw), 'data': data}
            save(f'read-{index:02d}.json', row)
            capture['reads'].append(row)
        if sources != source_hashes():
            raise ValueError('SOURCE_CHANGED')
        capture.update(status='CAPTURED', reason='READS_COMPLETE')
    except KeyboardInterrupt:
        capture['reason'] = 'INTERRUPTED'
    except Exception as exc:
        capture['reason'] = str(exc) if type(exc) in (ValueError, RequestFailure) and str(exc) in FAILURES else 'REQUEST_FAILED'
        if type(exc) is RequestFailure:
            request_error = exc.diagnostic
            save('request-error.json', request_error)
    capture['completed_at_ms'] = previous
    report = _report(capture, permission_check, request_error)
    save('capture.json', capture)
    save('report.json', report)
    manifest = {'schema': 'GOLD_ACCOUNT_ARCHIVE_V1', 'source_sha256': sources,
                'files': files, 'authoritative': False, 'source_authenticated': False}
    _write_private(destination/'manifest.json', _encode(manifest))
    descriptor = os.open(destination, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return report


def verify(directory):
    root = Path(directory).absolute()
    if root.resolve() != root:
        raise ValueError('LINKED_ARCHIVE')
    def read(name):
        if name not in {'clock.json', 'permissions.json', 'permission-check.json', 'request-error.json', 'capture.json', 'report.json', 'manifest.json',
                        *(f'read-{i:02d}.json' for i in range(len(KINDS)))}:
            raise ValueError('INVALID_ARCHIVE_FILE')
        path = root/name
        if path.resolve() != path or not path.is_file():
            raise ValueError('INVALID_ARCHIVE_FILE')
        with path.open('rb') as source:
            raw = source.read(MAX_BYTES+1)
        if len(raw) > MAX_BYTES:
            raise ValueError('INVALID_ARCHIVE_FILE')
        return raw
    manifest = load_document(read('manifest.json'))
    sources = source_hashes()
    if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'source_sha256', 'files',
            'authoritative', 'source_authenticated'} or manifest['schema'] != 'GOLD_ACCOUNT_ARCHIVE_V1'
            or manifest['source_sha256'] != sources or manifest['authoritative'] is not False
            or manifest['source_authenticated'] is not False or not isinstance(manifest['files'], dict)):
        raise ValueError('INVALID_MANIFEST')
    for name, digest in manifest['files'].items():
        if _digest(read(name)) != digest:
            raise ValueError('ARCHIVE_HASH_MISMATCH')
    c = load_document(read('capture.json'))
    check = load_document(read('permission-check.json')) if 'permission-check.json' in manifest['files'] else None
    request_error = load_document(read('request-error.json')) if 'request-error.json' in manifest['files'] else None
    report = _report(c, check, request_error)
    required = {'capture.json', 'report.json', *(f'read-{i:02d}.json' for i in range(len(c['reads'])))}
    if request_error is not None:
        required.add('request-error.json')
    if check is not None:
        required.add('permission-check.json')
        if check['status'] == 'REJECTED' and (c['reason'] != 'READ_ONLY_PERMISSION_REQUIRED' or c['reads']):
            raise ValueError('PERMISSION_STATUS_MISMATCH')
    allowed = required | {'clock.json', 'permissions.json'}
    if not required <= manifest['files'].keys() <= allowed:
        raise ValueError('INVALID_ARCHIVE_MEMBERS')
    if (c['status'] == 'CAPTURED' or c['reads']) and set(manifest['files']) != allowed:
        raise ValueError('MISSING_PREFLIGHT')
    if 'clock.json' in manifest['files']:
        clock = load_document(read('clock.json'))
        if (not isinstance(clock, dict) or set(clock) != {'sent_ms', 'received_ms', 'server_ms', 'response_sha256'}
                or any(type(clock[k]) is not int for k in ('sent_ms', 'received_ms', 'server_ms'))
                or not c['started_at_ms'] <= clock['sent_ms'] <= clock['received_ms'] <= c['completed_at_ms']
                or clock['received_ms']-clock['sent_ms'] > 5000 or clock['server_ms'] <= 0
                or abs(clock['server_ms']-(clock['sent_ms']+clock['received_ms'])//2) > 2000
                or not isinstance(clock['response_sha256'], str)
                or re.fullmatch('[a-f0-9]{64}', clock['response_sha256']) is None):
            raise ValueError('INVALID_CLOCK_EVIDENCE')
    if 'permissions.json' in manifest['files']:
        permission = load_document(read('permissions.json'))
        if (not isinstance(permission, dict) or set(permission) != {'started_at_ms', 'received_at_ms', 'response_sha256', 'data'}
                or 'clock.json' not in manifest['files']
                or type(permission['started_at_ms']) is not int or type(permission['received_at_ms']) is not int
                or not clock['received_ms'] <= permission['started_at_ms'] <= permission['received_at_ms'] <= c['completed_at_ms']
                or (c['reads'] and permission['received_at_ms'] > c['reads'][0]['started_at_ms'])
                or not isinstance(permission['response_sha256'], str)
                or re.fullmatch('[a-f0-9]{64}', permission['response_sha256']) is None):
            raise ValueError('INVALID_PERMISSION_EVIDENCE')
        _permissions(_encode(permission['data']))
        if check is not None and check != permission_diagnostics(_encode(permission['data'])):
            raise ValueError('PERMISSION_DIAGNOSTICS_MISMATCH')
    if any(read(f'read-{i:02d}.json') != _encode(row) for i, row in enumerate(c['reads'])):
        raise ValueError('READ_PREFIX_MISMATCH')
    if read('report.json') != _encode(report) or sources != source_hashes():
        raise ValueError('RECOMPUTATION_MISMATCH')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--check', action='store_true')
    action.add_argument('--operator-read', action='store_true')
    action.add_argument('--verify-dir', type=Path)
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.verify_dir:
            if args.output_dir:
                raise ValueError('OUTPUT_NOT_ALLOWED')
            report = verify(args.verify_dir)
        elif not args.operator_read:
            report = {'status': 'READY_FOR_OPERATOR_READ', 'network_used': False,
                'credential_access': False, 'submission_enabled': False, 'read_count': len(KINDS),
                'limitations': ['BOUNDED_SNAPSHOT_ONLY', 'NO_CONTINUOUS_STREAM', 'NO_HISTORY_RECONCILIATION']}
        else:
            if not sys.stdin.isatty() or not sys.stderr.isatty():
                raise ValueError('INTERACTIVE_TERMINAL_REQUIRED')
            if args.output_dir is None:
                raise ValueError('OUTPUT_DIRECTORY_REQUIRED')
            root = args.output_dir.absolute()
            if root.resolve() != root or root.exists() or not root.parent.is_dir():
                raise ValueError('NEW_OUTPUT_DIRECTORY_REQUIRED')
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                key = getpass.getpass('Binance read-only API key (hidden): ')
                secret = getpass.getpass('Binance API secret (hidden): ')
            if any(re.fullmatch('[A-Za-z0-9]{16,256}', value) is None for value in (key, secret)):
                raise ValueError('INVALID_CREDENTIAL_FORMAT')
            root.mkdir(mode=0o700)
            try:
                report = collect(root, api_key=key, api_secret=secret)
            finally:
                del key, secret
        print(json.dumps(report, sort_keys=True))
        return 0 if report['status'] in ('READY_FOR_OPERATOR_READ', 'STABLE_OBSERVATIONS') else 2
    except (Exception, KeyboardInterrupt) as exc:
        reasons = {'INTERACTIVE_TERMINAL_REQUIRED', 'OUTPUT_DIRECTORY_REQUIRED',
                   'NEW_OUTPUT_DIRECTORY_REQUIRED', 'INVALID_CREDENTIAL_FORMAT', 'OUTPUT_NOT_ALLOWED'}
        reason = str(exc) if type(exc) is ValueError and str(exc) in reasons else 'ACCOUNT_CAPTURE_FAILED'
        print(json.dumps({'status': 'FAILED', 'reason': reason, 'submission_enabled': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
