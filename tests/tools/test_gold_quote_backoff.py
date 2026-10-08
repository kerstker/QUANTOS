"""Retain network failures without restarting an experiment or masking gaps."""
import asyncio
import io
import json
import ssl
import urllib.error

import pytest

from tools import perpetual_public_capture as capture


@pytest.mark.parametrize('failures', [2, 4])
def test_bounded_backoff_retains_errors_and_uses_fresh_requests(monkeypatch, failures):
    requests, errors, sleeps = [], [], []

    class Response(io.BytesIO):
        def geturl(self):
            return requests[-1].full_url

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            if len(requests) <= failures:
                raise urllib.error.URLError(ssl.SSLEOFError('fixture'))
            return Response(b'{"fixture": true}')

    monkeypatch.setattr(capture.urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(capture.time, 'sleep', sleeps.append)
    kwargs = dict(symbol='XAUUSDT', samples=3, on_error=errors.append,
                  retry_profile='BOUNDED_BACKOFF_V1')
    if failures == 4:
        with pytest.raises(urllib.error.URLError):
            capture.capture_public(**kwargs)
        assert sleeps == [1, 2, 4]
        assert errors[-1]['retry_scheduled'] is False
        assert len(requests) == 4
    else:
        result = capture.capture_public(**kwargs)
        assert len(result['samples']) == 3
        assert sleeps[:2] == [1, 2]
    assert len(errors) == failures
    assert len({id(request) for request in requests}) == len(requests)
    assert [row['attempt'] for row in errors] == list(range(1, failures + 1))
    assert all(row['retry_profile'] == 'BOUNDED_BACKOFF_V1' for row in errors)


@pytest.mark.parametrize('error', [
    urllib.error.URLError(ssl.SSLCertVerificationError('fixture')),
    urllib.error.HTTPError('https://fapi.binance.com', 429, 'fixture', {}, None),
    urllib.error.URLError('unclassified fixture'),
])
def test_backoff_does_not_retry_rejection_or_unclassified_failure(monkeypatch, error):
    errors, calls = [], []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            raise error

    monkeypatch.setattr(capture.urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(type(error)):
        capture.capture_public(samples=3, retry_profile='BOUNDED_BACKOFF_V1',
                               on_error=errors.append)
    assert len(calls) == 1 and errors[0]['retry_scheduled'] is False


@pytest.mark.parametrize('profile', ['BOUNDED_BACKOFF_V1', 'FRESH_CADENCE_V1'])
def test_program_registers_profile_before_collection_and_preserves_failure(tmp_path, monkeypatch, profile):
    from tools import gold_bar_program as program
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    monkeypatch.setattr(program, 'source_identity', lambda: {'fixture': True})
    plan_path = tmp_path / 'data/cache/gold_bar_research/programs/1/plan.json'

    async def stopped(target):
        plan = json.loads(plan_path.read_text())
        assert plan['quote_transport']['profile'] == profile
        assert plan['quote_transport']['delays_seconds'] == ([1, 2, 4] if profile == 'BOUNDED_BACKOFF_V1' else [0.25])
    async def observed(*args, **kwargs):
        return {'status': 'OBSERVED'}

    def failed_quotes(**kwargs):
        assert kwargs['retry_profile'] == profile
        raise urllib.error.URLError(ssl.SSLEOFError('fixture'))

    monkeypatch.setattr(program, 'observe', observed)
    monkeypatch.setattr(program, 'capture', lambda path: path.mkdir())
    monkeypatch.setattr(program, 'capture_public', failed_quotes)

    monkeypatch.setattr(program, 'wait_for_capture', stopped)
    with pytest.raises(ValueError, match='JOINT_CAPTURE_FAILED'):
        asyncio.run(program.run_program(program_id=1, first_attempt=1, rounds=1,
                                        quote_retry_profile=profile))
    summary = json.loads((plan_path.parent / 'summary.json').read_text())
    assert summary['status'] == 'RESEARCH_PROGRAM_INCOMPLETE'
    assert summary['rounds'] == []
    with pytest.raises(FileExistsError):
        asyncio.run(program.run_program(program_id=1, first_attempt=1, rounds=1,
                                        quote_retry_profile=profile))


@pytest.mark.parametrize('profile', [None, True, 'unbounded'])
def test_unknown_retry_budget_rejected_before_network(profile):
    with pytest.raises(ValueError, match='INVALID_QUOTE_RETRY_PROFILE'):
        capture.capture_public(samples=3, retry_profile=profile)


def test_cancel_during_backoff_does_not_start_another_request(monkeypatch):
    calls = []

    class Cancel:
        cancelled = False

        def is_set(self):
            return self.cancelled

        def wait(self, seconds):
            assert seconds == 1
            self.cancelled = True

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            raise TimeoutError('fixture')

    monkeypatch.setattr(capture.urllib.request, 'build_opener', lambda *args: Opener())
    with pytest.raises(InterruptedError, match='PUBLIC_CAPTURE_CANCELLED'):
        capture.capture_public(samples=3, retry_profile='BOUNDED_BACKOFF_V1',
                               cancel_event=Cancel())
    assert len(calls) == 1
