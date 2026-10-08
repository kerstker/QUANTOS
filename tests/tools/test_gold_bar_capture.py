import io
import json

import pytest

from tools import gold_bar_capture as capture
from tests.research.test_gold_bar_dataset import raw_rows, STEP


def test_fixed_public_get_retains_bytes_and_never_overwrites(tmp_path, monkeypatch):
    raw = json.dumps(raw_rows()).encode()
    calls = []

    class Response(io.BytesIO):
        headers = {}

        def geturl(self):
            return calls[-1].full_url

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            assert request.get_method() == 'GET'
            assert request.full_url == capture.HOST + (
                '/fapi/v1/klines?symbol=XAUUSDT&interval=15m&limit=1500'
                f'&endTime={1000 * STEP - 1}')
            assert set(request.headers) == {'Accept', 'Accept-encoding'}
            return Response(raw)

    monkeypatch.setattr(capture.time, 'time_ns', lambda: (1000 * STEP + 100) * 1_000_000)
    monkeypatch.setattr(capture.urllib.request, 'build_opener', lambda *args: Opener())
    target = tmp_path / 'new'
    result = capture.capture(target)
    assert result['count'] == 1000 and result['historical_pit_proven'] is False
    assert (target / 'response.json').read_bytes() == raw
    assert (target / 'response.wire').read_bytes() == raw
    with pytest.raises(FileExistsError):
        capture.capture(target)
    assert len(calls) == 1


def test_transport_failure_retains_request_without_success_summary(tmp_path, monkeypatch):
    class Opener:
        def open(self, *args, **kwargs):
            raise OSError('synthetic failure')

    monkeypatch.setattr(capture.urllib.request, 'build_opener', lambda *args: Opener())
    target = tmp_path / 'failed'
    assert capture.main(['--output-dir', str(target)]) == 2
    assert (target / 'request.json').is_file()
    assert not (target / 'summary.json').exists()


@pytest.mark.parametrize('failure', ['transient', 'persistent', 'http'])
def test_history_connection_retry_retains_each_attempt_and_real_receipt(tmp_path, monkeypatch, failure):
    import urllib.error
    clock = [1000 * STEP + 100]
    calls = []
    raw = json.dumps(raw_rows()).encode()

    class Response(io.BytesIO):
        headers = {}

        def geturl(self):
            return calls[-1].full_url

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            clock[0] += 50
            if failure == 'http':
                raise urllib.error.HTTPError(request.full_url, 429, 'limited', {}, None)
            if failure == 'persistent' or len(calls) == 1:
                raise urllib.error.URLError(ConnectionResetError())
            return Response(raw)

    monkeypatch.setattr(capture.urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(capture.time, 'time_ns', lambda: clock[0] * 1_000_000)
    monkeypatch.setattr(capture.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + int(seconds * 1000)))
    target = tmp_path / 'capture'
    code = capture.main(['--output-dir', str(target)])
    assert len(calls) == (1 if failure == 'http' else 2)
    error = json.loads((target / 'attempt-1-error.json').read_text())
    assert error['retry_scheduled'] is (failure != 'http')
    if failure == 'transient':
        assert code == 0
        first = json.loads((target / 'request.json').read_text())
        second = json.loads((target / 'attempt-2-request.json').read_text())
        receipt = json.loads((target / 'receipt.json').read_text())
        assert first['requested_at_ms'] < second['requested_at_ms'] == receipt['requested_at_ms']
        assert receipt['received_at_ms'] > receipt['requested_at_ms']
        assert (target / 'response.json').read_bytes() == raw
    else:
        assert code == 2 and not (target / 'summary.json').exists()


def test_funding_output_uses_successful_attempt_receipt_clock(tmp_path, monkeypatch):
    import hashlib
    import json
    from types import SimpleNamespace
    from tools import gold_bar_capture as tool
    history = tmp_path / 'history'
    history.mkdir()
    (history / 'response.json').write_bytes(b'[]')
    (history / 'receipt.json').write_text(json.dumps(dict(
        response_sha256=hashlib.sha256(b'[]').hexdigest(), requested_at_ms=310, received_at_ms=320)))
    monkeypatch.setattr(tool, 'load_bars', lambda *a, **kw: [SimpleNamespace(open_ms=0, close_ms=300)])
    monkeypatch.setattr(tool.time, 'time_ns', lambda: 400000000)
    def fetch(output, endpoint, requested):
        assert requested == 400
        (output / 'receipt.json').write_text(json.dumps(dict(requested_at_ms=450, received_at_ms=460)))
        return json.dumps([dict(symbol='XAUUSDT', fundingTime=100,
            fundingRate='.001', markPrice='100')]).encode(), 460
    monkeypatch.setattr(tool, '_fetch', fetch)
    out = tmp_path / 'funding'
    result = tool.capture_funding(out, history)
    observed = json.loads((out / 'observed-settlements.json').read_text())
    assert result['count'] == 1
    assert observed[0]['requested_at_ms'] == 450
    assert observed[0]['available_at_ms'] == 460
    assert observed[0]['settlement_at_ms'] == 100


def test_protected_history_retry_uses_fresh_request_and_retains_network_mode(tmp_path, monkeypatch):
    import urllib.error
    from tools import gold_bar_network
    calls = []
    raw = json.dumps(raw_rows()).encode()
    class Response(io.BytesIO):
        headers = {}
        def geturl(self):
            return calls[-1].full_url
    class Transport:
        def open(self, request, timeout):
            assert request.host == 'fapi.binance.com'
            calls.append(request)
            if len(calls) == 1:
                request.set_proxy('127.0.0.1:8888', 'http')
                raise urllib.error.URLError(ConnectionResetError())
            return Response(raw)
    monkeypatch.setattr(gold_bar_network, 'opener', lambda: Transport())
    monkeypatch.setattr(capture.time, 'time_ns', lambda: (1000*STEP+100)*1_000_000)
    monkeypatch.setattr(capture.time, 'sleep', lambda _: None)
    target = tmp_path/'protected'
    result = capture.capture(target, protected_network=True)
    assert result['count'] == 1000 and len(calls) == 2
    assert calls[0] is not calls[1]
    assert json.loads((target/'receipt.json').read_text())['network_mode'] == 'PROTECTED_DEMO_NETWORK_V1'
    assert json.loads((target/'attempt-1-error.json').read_text())['retry_scheduled'] is True
