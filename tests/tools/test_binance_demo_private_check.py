"""Operator Demo connectivity checks cannot manufacture execution evidence."""
import json
import subprocess

import pytest

from tools import binance_demo_private_check as check
from tests.tools.test_binance_demo_readiness import NOW, exchange_info


def credentials(tmp_path):
    directory = tmp_path / 'credentials'
    directory.mkdir(mode=0o700)
    path = directory / 'demo.json'
    path.write_text(json.dumps({'environment': 'BINANCE_USDM_DEMO',
        'rest_base': 'https://demo-fapi.binance.com',
        'api_key': 'a' * 64, 'api_secret': 'b' * 64}))
    path.chmod(0o600)
    return path


def fake_transport(calls, *, fail=None):
    def request(method, path, *, params, headers):
        calls.append((method, path, params, headers))
        if path == fail:
            raise RuntimeError('b' * 64 + ' private signature url')
        if path == '/fapi/v1/time': return {'serverTime': NOW}
        if path == '/fapi/v1/exchangeInfo': return exchange_info()
        if path == '/fapi/v3/account': return {'assets': [], 'positions': []}
        if path in ('/fapi/v1/openOrders', '/fapi/v1/userTrades'): return []
        if path == '/fapi/v1/listenKey': return {'listenKey': 'c' * 64}
        pytest.fail('unexpected request')
    return request


def test_default_never_reads_credentials_or_network(tmp_path):
    def forbidden(*a, **kw): pytest.fail('unrequested private access')
    result = check.collect(credential_file=tmp_path/'missing', request=forbidden,
        websocket_probe=forbidden, clock_ms=lambda: NOW)
    assert result.connectivity_status == 'NOT_REQUESTED'
    assert result.submission_status == 'BLOCKED'
    assert result.ledger_status == 'UNKNOWN'


def test_authenticated_checks_preserve_unknown_delivery_and_execution(tmp_path):
    calls, connections = [], []
    def ws(token):
        connections.append(token)
        return {'connections': 2, 'pongs': 2, 'events': 0}
    result = check.collect(credential_file=credentials(tmp_path), operator_check=True,
        request=fake_transport(calls), websocket_probe=ws, clock_ms=lambda: NOW)
    assert result.connectivity_status == 'CHECKS_PASSED'
    assert result.submission_status == 'BLOCKED' and result.ledger_status == 'UNKNOWN'
    assert result.event_delivery_status == 'UNKNOWN'
    assert result.websocket_connections == 2 and result.websocket_pongs == 2
    assert len(connections) == 1
    assert all(path != '/fapi/v1/order' for _, path, _, _ in calls)
    assert [p for m, p, _, _ in calls if m != 'GET'] == ['/fapi/v1/listenKey']
    assert not any(h for _, p, _, h in calls if p in ('/fapi/v1/time', '/fapi/v1/exchangeInfo'))
    encoded = result.model_dump_json()
    assert all(x * 64 not in encoded for x in ('a', 'b', 'c'))


def test_error_stops_dependent_requests_and_never_leaks_exception(tmp_path):
    calls = []
    result = check.collect(credential_file=credentials(tmp_path), operator_check=True,
        request=fake_transport(calls, fail='/fapi/v1/openOrders'),
        websocket_probe=lambda _: pytest.fail('stream after failed REST'), clock_ms=lambda: NOW)
    assert result.connectivity_status == 'FAILED'
    assert len(calls) == 4
    assert result.steps[-1].reason == 'REQUEST_FAILED'
    assert 'private' not in result.model_dump_json().replace('binance-demo-private-check.v1', '')
    assert 'b' * 64 not in result.model_dump_json()


@pytest.mark.parametrize('change', ['permissions', 'parent', 'symlink', 'mainnet', 'extra', 'large'])
def test_credential_file_rejects_unsafe_or_wrong_environment(tmp_path, change):
    path = credentials(tmp_path)
    if change == 'permissions': path.chmod(0o644)
    elif change == 'parent': path.parent.chmod(0o755)
    elif change == 'symlink':
        link = path.parent/'link'; link.symlink_to(path); path = link
    elif change == 'large': path.write_text('x' * 9000)
    else:
        body = json.loads(path.read_text())
        body['rest_base' if change == 'mainnet' else 'unexpected'] = 'https://fapi.binance.com'
        path.write_text(json.dumps(body))
    with pytest.raises(check.ProbeFailure): check.load_credentials(path)


def test_fixed_transport_rejects_orders_deletion_and_arbitrary_hosts_before_io():
    for method, path in [('POST', '/fapi/v1/order'), ('DELETE', '/fapi/v1/listenKey'),
                         ('GET', 'https://fapi.binance.com/fapi/v3/account')]:
        with pytest.raises(check.ProbeFailure, match='ENDPOINT_NOT_ALLOWED'):
            check.request_json(method, path, params='', headers={})


def test_bad_websocket_result_cannot_be_called_connected(tmp_path):
    result = check.collect(credential_file=credentials(tmp_path), operator_check=True,
        request=fake_transport([]), websocket_probe=lambda _: {'connections': 2, 'pongs': 0, 'events': 0},
        clock_ms=lambda: NOW)
    assert result.connectivity_status == 'FAILED'
    assert result.event_delivery_status == 'UNKNOWN'


def test_query_page_limit_is_incomplete_not_a_complete_backfill(tmp_path):
    request = fake_transport([])
    def full(method, path, **kwargs):
        if path == '/fapi/v1/userTrades':
            return [{'symbol': 'BTCUSDT', 'id': i, 'orderId': i+1, 'time': NOW-1,
                     'qty': '0.001', 'price': '50000', 'commission': '0.01',
                     'commissionAsset': 'USDT', 'realizedPnl': '0', 'side': 'BUY',
                     'positionSide': 'BOTH', 'maker': False, 'buyer': True} for i in range(100)]
        return request(method, path, **kwargs)
    result = check.collect(credential_file=credentials(tmp_path), operator_check=True,
        request=full, websocket_probe=lambda _: pytest.fail('incomplete history'), clock_ms=lambda: NOW)
    assert result.connectivity_status == 'FAILED'
    assert result.steps[-1].reason == 'HISTORY_PAGE_INCOMPLETE'


def test_saved_report_retains_prior_attempts_and_rejects_tampering(tmp_path):
    result = check.collect(credential_file=tmp_path/'missing', clock_ms=lambda: NOW)
    check.save_report(tmp_path/'output', result)
    assert check.load_report(tmp_path/'output') == result
    pointer = json.loads((tmp_path/'output/latest.json').read_text())
    (tmp_path/'output'/f"{pointer['sha256']}.json").write_text('{}')
    with pytest.raises(ValueError): check.load_report(tmp_path/'output')


def test_process_deadline_records_failure_without_inheriting_other_credentials(tmp_path, monkeypatch, capsys):
    path = credentials(tmp_path)
    monkeypatch.setenv('BINANCE_API_SECRET', 'unrelated-real-key')
    monkeypatch.setenv('BINANCE_TESTNET_API_SECRET', 'unrelated-old-key')
    monkeypatch.setenv('PYTHONPATH', 'untrusted-injection')
    def timeout(command, **kwargs):
        assert kwargs['timeout'] == 120
        assert not any(k in kwargs['env'] for k in ('BINANCE_API_SECRET', 'BINANCE_TESTNET_API_SECRET', 'PYTHONPATH'))
        assert 'a'*64 not in str(command) and 'b'*64 not in str(command)
        raise subprocess.TimeoutExpired(command, kwargs['timeout'])
    monkeypatch.setattr(check.subprocess, 'run', timeout)
    assert check.main(['--credential-file', str(path), '--operator-check', '--output-dir', str(tmp_path/'out')]) == 2
    body = json.loads(capsys.readouterr().out)
    assert body['steps'][-1]['reason'] == 'REQUEST_TIMEOUT'
    assert check.load_report(tmp_path/'out').connectivity_status == 'FAILED'


def test_transport_redacts_http_errors_and_refuses_redirects(monkeypatch):
    import io
    import urllib.error
    class Opener:
        def open(self, request, timeout):
            assert timeout == 10
            assert request.full_url.startswith('https://demo-fapi.binance.com/')
            raise urllib.error.HTTPError(request.full_url, 401, 'private-secret', {}, io.BytesIO(b'private-secret'))
    def opener(*handlers):
        assert any(isinstance(h, check.NoRedirect) for h in handlers)
        return Opener()
    monkeypatch.setattr(check.urllib.request, 'build_opener', opener)
    with pytest.raises(check.ProbeFailure) as captured:
        check.request_json('GET', '/fapi/v3/account', params='signature=private', headers={'X-MBX-APIKEY':'private'})
    assert str(captured.value) == 'AUTHENTICATION_REJECTED'


def test_unrequested_or_contradictory_steps_cannot_claim_observed_events(tmp_path):
    from quantos.contracts.binance_demo_private import DemoPrivateCheck, PrivateCheckStep
    body = check.collect(credential_file=tmp_path/'missing', clock_ms=lambda: NOW).model_dump()
    body.update(websocket_events=1, event_delivery_status='EVENTS_OBSERVED')
    with pytest.raises(ValueError): DemoPrivateCheck.model_validate(body)
    with pytest.raises(ValueError):
        PrivateCheckStep(name='account', status='PASSED', reason='AUTHENTICATION_REJECTED')
