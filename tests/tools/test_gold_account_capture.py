import hashlib
import json
import io

import pytest

from tests.research.test_gold_account_snapshot import payload
from tools import gold_account_capture as tool


def permissions():
    return {name: False for name in tool.WRITE_PERMISSIONS} | {'enableReading': True, 'ipRestrict': True}


def transport(calls, change=None):
    kinds = iter(tool.KINDS)
    def get(path, *, query, headers):
        calls.append((path, query, headers))
        if path == tool.TIME_PATH:
            value = {'serverTime': 1001}
        elif path == tool.PERMISSION_PATH:
            value = permissions()
        else:
            kind = next(kinds)
            value = payload(kind)
            if change:
                value = change(kind, value)
        return json.dumps(value).encode()
    return get


def clock():
    ticks = iter(range(1000, 10000))
    return lambda: next(ticks)


def test_default_check_never_prompts_or_calls_network(monkeypatch, capsys):
    def forbidden(*a, **k):
        pytest.fail('unexpected private operation')
    monkeypatch.setattr(tool, 'get_response', forbidden)
    monkeypatch.setattr(tool.getpass, 'getpass', forbidden)
    assert tool.main([]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'READY_FOR_OPERATOR_READ'


def test_collect_and_verify_preserve_private_prefix_and_never_secrets(tmp_path):
    calls = []
    root = tmp_path/'capture'
    root.mkdir(mode=0o700)
    result = tool.collect(root, api_key='test-key', api_secret='test-secret',
                          get=transport(calls), clock_ms=clock())
    assert result['status'] == 'STABLE_OBSERVATIONS'
    assert len(calls) == len(tool.KINDS)+2
    assert all(path in tool.PATHS for path, _, _ in calls)
    raw = b''.join(p.read_bytes() for p in root.iterdir())
    assert b'test-secret' not in raw and b'test-key' not in raw and b'signature=' not in raw
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in root.iterdir())
    assert tool.verify(root)['status'] == 'STABLE_OBSERVATIONS'
    assert result['history_complete'] is False


@pytest.mark.parametrize('field,value', [('enableFutures', True), ('enableReading', False),
    ('enableWithdrawals', True), ('ipRestrict', False), ('enableMargin', 'false')])
def test_permissions_stop_before_account_reads(tmp_path, field, value):
    calls = []
    base = transport(calls)
    def get(path, **kwargs):
        raw = base(path, **kwargs)
        if path == tool.PERMISSION_PATH:
            p = json.loads(raw)
            p[field] = value
            return json.dumps(p).encode()
        return raw
    root = tmp_path/'capture'
    root.mkdir()
    result = tool.collect(root, api_key='k', api_secret='s', get=get, clock_ms=clock())
    assert result['status'] == 'INCOMPLETE'
    assert result['reason'] == 'READ_ONLY_PERMISSION_REQUIRED'
    assert len(calls) == 2
    expected = ('INVALID_TYPE' if type(value) is not bool else 'ENABLED' if value else 'DISABLED')
    assert result['permission_check']['fields'][field] == expected
    assert tool.verify(root)['permission_check'] == result['permission_check']


def test_permission_diagnostics_distinguish_missing_without_echoing_unknown_input():
    p = permissions()
    del p['enableFutures']
    p['enableReading'] = 'private-value'
    p['enable-private-secret'] = True
    check = tool.permission_diagnostics(json.dumps(p).encode())
    assert check['fields']['enableFutures'] == 'MISSING'
    assert check['fields']['enableReading'] == 'INVALID_TYPE'
    assert check['unknown_permission_fields'] is True
    assert 'private' not in json.dumps(check)
    assert check['status'] == 'REJECTED'


def test_failure_retains_prefix_and_does_not_retry_or_print_exchange_message(tmp_path):
    calls = []
    def fail(kind, value):
        if kind == 'algos_before':
            raise RuntimeError('secret-and-private-exchange-error')
        return value
    root = tmp_path/'capture'
    root.mkdir()
    report = tool.collect(root, api_key='k', api_secret='s',
                          get=transport(calls, fail), clock_ms=clock())
    assert report['status'] == 'INCOMPLETE' and report['read_count'] == 3
    assert len(calls) == 6
    assert b'secret-and-private' not in b''.join(p.read_bytes() for p in root.iterdir())
    assert tool.verify(root)['status'] == 'INCOMPLETE'


def test_capture_changed_inventory_is_not_stable(tmp_path):
    def change(kind, value):
        if kind == 'account_after':
            value['assets'][0]['walletBalance'] = '999'
        return value
    root = tmp_path/'capture'
    root.mkdir()
    report = tool.collect(root, api_key='k', api_secret='s',
                          get=transport([], change), clock_ms=clock())
    assert report['status'] == 'CHANGED_DURING_CAPTURE'


@pytest.mark.parametrize('path', ['/fapi/v1/order', '/fapi/v1/listenKey',
    'https://evil.example/fapi/v3/account', '/fapi/v1/leverage'])
def test_transport_has_no_order_or_mutation_endpoint(path):
    with pytest.raises(ValueError, match='ENDPOINT_NOT_ALLOWED'):
        tool.get_response(path, query='', headers={})


def test_redirects_rejected():
    with pytest.raises(ValueError, match='REDIRECT_REFUSED'):
        tool.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example')


def test_manifest_cannot_be_rehashed_to_hide_changed_result(tmp_path):
    root = tmp_path/'capture'
    root.mkdir()
    tool.collect(root, api_key='k', api_secret='s', get=transport([]), clock_ms=clock())
    report = json.loads((root/'report.json').read_bytes())
    report['history_complete'] = True
    raw = json.dumps(report).encode()
    (root/'report.json').write_bytes(raw)
    manifest = json.loads((root/'manifest.json').read_bytes())
    manifest['files']['report.json'] = hashlib.sha256(raw).hexdigest()
    (root/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        tool.verify(root)


def test_operator_mode_requires_real_terminal_before_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tool.sys.stdin, 'isatty', lambda: False)
    monkeypatch.setattr(tool.getpass, 'getpass', lambda *a: pytest.fail('credential prompt'))
    assert tool.main(['--operator-read', '--output-dir', str(tmp_path/'capture')]) == 2
    assert json.loads(capsys.readouterr().out)['reason'] == 'INTERACTIVE_TERMINAL_REQUIRED'
    assert not (tmp_path/'capture').exists()


def test_clock_skew_stops_before_any_signed_request(tmp_path):
    calls = []
    def get(path, **kwargs):
        calls.append(path)
        return b'{"serverTime": 999999}'
    root = tmp_path/'capture'
    root.mkdir()
    report = tool.collect(root, api_key='k', api_secret='s', get=get, clock_ms=clock())
    assert report['reason'] == 'CLOCK_UNRELIABLE'
    assert calls == [tool.TIME_PATH]
    assert tool.verify(root)['status'] == 'INCOMPLETE'


def test_interrupted_read_has_no_success_and_can_be_reviewed(tmp_path):
    base = transport([])
    def get(path, **kwargs):
        if path == '/fapi/v3/positionRisk':
            raise KeyboardInterrupt
        return base(path, **kwargs)
    root = tmp_path/'capture'
    root.mkdir()
    report = tool.collect(root, api_key='k', api_secret='s', get=get, clock_ms=clock())
    assert report['reason'] == 'INTERRUPTED' and report['read_count'] == 4
    assert tool.verify(root)['status'] == 'INCOMPLETE'


def test_no_missing_permission_field_is_assumed_safe():
    p = permissions()
    del p['enableFutures']
    with pytest.raises(ValueError, match='READ_ONLY_PERMISSION_REQUIRED'):
        tool._permissions(json.dumps(p).encode())


def test_transport_uses_get_fixed_hosts_and_request_signatures(monkeypatch):
    requests = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            assert limit == 2_000_001
            return b'{}'
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert timeout == 10
            return Response()
    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *args: Opener())
    tool.get_response(tool.PERMISSION_PATH, query='timestamp=1&signature=test', headers={'X-MBX-APIKEY': 'test'})
    tool.get_response('/fapi/v3/account', query='timestamp=1&signature=test', headers={'X-MBX-APIKEY': 'test'})
    assert all(r.method == 'GET' and r.data is None for r in requests)
    assert requests[0].full_url.startswith('https://api.binance.com/sapi/v1/account/apiRestrictions?')
    assert requests[1].full_url.startswith('https://fapi.binance.com/fapi/v3/account?')


def test_http_auth_error_does_not_leak_response_or_request(monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raise tool.urllib.error.HTTPError('private-url', 401, 'private-message', {}, None)
    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(ValueError, match='^AUTHENTICATION_REJECTED$'):
        tool.get_response('/fapi/v3/account', query='signature=private', headers={})


def test_existing_archive_is_not_modified_or_queried(tmp_path):
    root = tmp_path/'capture'
    root.mkdir()
    (root/'kept').write_text('old')
    with pytest.raises(ValueError, match='EMPTY_PRIVATE_DIRECTORY_REQUIRED'):
        tool.collect(root, api_key='k', api_secret='s', get=lambda *a, **k: pytest.fail('network'))
    assert (root/'kept').read_text() == 'old'


@pytest.mark.parametrize('failure,reason', [
    (TimeoutError('private-url'), 'NETWORK_TIMEOUT'),
    (tool.urllib.error.URLError('private-proxy'), 'NETWORK_UNAVAILABLE'),
    (ValueError('REDIRECT_REFUSED'), 'REDIRECT_REFUSED'),
    (tool.urllib.error.HTTPError('private-url', 502, 'private-body', {}, None), 'EXCHANGE_UNAVAILABLE'),
    (tool.urllib.error.HTTPError('private-url', 400, 'private-body', {}, None), 'REQUEST_REJECTED')])
def test_transport_reports_safe_specific_failures(monkeypatch, failure, reason):
    class Opener:
        def open(self, *args, **kwargs):
            raise failure
    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(ValueError, match='^'+reason+'$'):
        tool.get_response(tool.TIME_PATH, query='', headers={})


def test_public_network_failure_survives_archive_recomputation(tmp_path):
    root = tmp_path/'capture'
    root.mkdir()
    def get(*args, **kwargs):
        raise ValueError('NETWORK_TIMEOUT')
    report = tool.collect(root, api_key='k', api_secret='s', get=get, clock_ms=clock())
    assert report['reason'] == 'NETWORK_TIMEOUT'
    assert tool.verify(root)['reason'] == 'NETWORK_TIMEOUT'


def test_http_error_retains_only_bounded_numeric_code_and_fixed_endpoint(tmp_path, monkeypatch):
    class Opener:
        def open(self, *a, **k):
            raise tool.urllib.error.HTTPError('private-signature-url', 400, 'private-message', {},
                io.BytesIO(b'{"code":-2015,"msg":"private-key-ip-details"}'))
    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *a: Opener())
    root = tmp_path/'capture'
    root.mkdir()
    base = transport([])
    def get(path, **kwargs):
        return base(path, **kwargs) if path == tool.TIME_PATH else tool.get_response(path, **kwargs)
    report = tool.collect(root, api_key='test-key', api_secret='test-secret', get=get, clock_ms=clock())
    assert report['request_error'] == {'http_status':400, 'exchange_code':-2015,
                                     'endpoint':tool.PERMISSION_PATH}
    assert tool.verify(root)['request_error'] == report['request_error']
    assert b'private-' not in b''.join(p.read_bytes() for p in root.iterdir())


@pytest.mark.parametrize('code', [True, '-2015', 'secret', -999999999])
def test_http_error_never_echoes_untrusted_code_types(monkeypatch, code):
    class Opener:
        def open(self, *a, **k):
            raise tool.urllib.error.HTTPError('private', 400, '', {},
                io.BytesIO(json.dumps({'code':code,'msg':'private'}).encode()))
    monkeypatch.setattr(tool.urllib.request, 'build_opener', lambda *a: Opener())
    with pytest.raises(ValueError) as exc:
        tool.get_response(tool.PERMISSION_PATH, query='', headers={})
    assert exc.value.diagnostic['exchange_code'] is None
