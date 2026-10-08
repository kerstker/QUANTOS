"""Prospective cadence subtracts request latency without rewriting receipts."""
import io

import pytest

from tools import perpetual_public_capture as capture


@pytest.mark.parametrize('latency', [2, 6])
def test_request_time_is_part_of_cadence_and_retained(monkeypatch, latency):
    now = [1000.0]
    requests, waits = [], []

    class Response(io.BytesIO):
        def geturl(self):
            return requests[-1]

    class Opener:
        def open(self, request, timeout):
            assert timeout == 5
            requests.append(request.full_url)
            now[0] += latency
            return Response(b'{"fixture":true}')

    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(capture.time, 'time', lambda: now[0])
    monkeypatch.setattr(capture.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(capture.time, 'sleep', sleep)
    monkeypatch.setattr(capture.urllib.request, 'build_opener', lambda *args: Opener())
    result = capture.capture_public(symbol='XAUUSDT', samples=3, interval_seconds=10,
                                    retry_profile='FRESH_CADENCE_V1')
    rows = result['samples']
    assert rows[1]['mark']['requested_at']-rows[0]['mark']['requested_at'] == max(10, 2*latency)*1000
    assert rows[0]['depth']['received_at']-rows[0]['mark']['requested_at'] == latency*2000
    assert waits == ([6, 6] if latency == 2 else [])
    assert result['quote_transport']['profile'] == 'FRESH_CADENCE_V1'
    assert result['max_gap_ms'] == 30000


def test_fresh_cadence_preserves_terminal_network_failure(monkeypatch):
    calls, errors = [], []

    class Opener:
        def open(self, request, timeout):
            calls.append(timeout)
            raise TimeoutError('fixture')

    monkeypatch.setattr(capture.urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(capture.time, 'sleep', lambda _: None)
    with pytest.raises(TimeoutError):
        capture.capture_public(samples=3, retry_profile='FRESH_CADENCE_V1', on_error=errors.append)
    assert calls == [5, 5]
    assert [r['retry_scheduled'] for r in errors] == [True, False]
