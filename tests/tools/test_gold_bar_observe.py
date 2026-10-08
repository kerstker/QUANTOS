import asyncio
import hashlib
import json

import pytest

from tests.research.test_gold_bar_dataset import raw_rows, STEP
from tools import gold_bar_observe as observer


def recorded(tmp_path, monkeypatch, *, values=None, received=None, seconds=1, extended=False, connections=None, on_closed=None, stop_after_close_ms=None, protected_network=False, connection_checks=None, source=None):
    history = tmp_path / 'history'
    history.mkdir()
    values = raw_rows() if values is None else values
    raw = json.dumps(values).encode()
    warmup_end = values[-1][6] + 1
    (history / 'response.json').write_bytes(raw)
    (history / 'receipt.json').write_text(json.dumps(dict(requested_at_ms=warmup_end,
        received_at_ms=warmup_end + 10, response_sha256=hashlib.sha256(raw).hexdigest())))
    now = warmup_end + STEP + 20 if received is None else received
    message = json.dumps(dict(stream='xauusdt@kline_15m', data=dict(e='kline',
        E=now, s='XAUUSDT', k=dict(t=warmup_end, T=warmup_end + STEP - 1,
            s='XAUUSDT', i='15m', x=True, o='100', h='110', l='99', c='109'))))

    class Socket:
        def __init__(self):
            self.actions = connections.pop(0) if connections else ["message"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def recv(self):
            if not self.actions:
                await asyncio.sleep(2)
            action = self.actions.pop(0)
            if action == "gap":
                document = json.loads(message)
                document["data"]["k"]["t"] += STEP
                document["data"]["k"]["T"] += STEP
                return json.dumps(document)
            if isinstance(action, Exception):
                raise action
            return message

    def connect(*args, **kwargs):
        if connection_checks is not None:
            connection_checks(args, kwargs)
        return Socket()
    monkeypatch.setattr(observer, 'connect', connect)
    monkeypatch.setattr(observer, 'source_identity', lambda: {'fixture': True} if source is None else source)
    monkeypatch.setattr(observer.time, 'time_ns', lambda: now * 1_000_000)
    out = tmp_path / 'out'
    result = asyncio.run(observer.observe(history, out, seconds=seconds, extended=extended, on_closed=on_closed,
        **({'stop_after_close_ms': stop_after_close_ms} if stop_after_close_ms is not None else {}),
        **({'protected_network':True} if protected_network else {})))
    return result, out


def test_closed_bar_is_durable_and_produces_three_decisions(tmp_path, monkeypatch):
    result, out = recorded(tmp_path, monkeypatch)
    assert result['status'] == 'OBSERVED' and result['closed_bars'] == 1
    decisions = json.loads((out / 'decisions.jsonl').read_text())
    assert len(decisions['decisions']) == 3
    assert decisions['decisions'][0]['entry_side'] == 'BUY'
    assert result['net_pnl'] is None


def test_reconnect_preserves_cursor_and_raw_duplicates(tmp_path, monkeypatch):
    from tools.gold_bar_journal import restore
    result, out = recorded(tmp_path, monkeypatch, connections=[
        ['message', ConnectionResetError()], ['message']])
    assert result['status'] == 'OBSERVED'
    assert result['messages'] == 2 and result['closed_bars'] == 1
    assert len(result['transport_errors']) == 1
    assert result['transport_errors'][0]['reason'] == 'ConnectionResetError'
    _, review = restore(out)
    assert review['closed_bars'] == 1


@pytest.mark.parametrize('error', [ConnectionResetError, TimeoutError])
def test_reconnect_budget_exhaustion_is_incomplete(tmp_path, monkeypatch, error):
    result, _ = recorded(tmp_path, monkeypatch,
        connections=[[error()] for _ in range(4)])
    assert result['status'] == 'INCOMPLETE'
    assert result['reason'] == 'TRANSPORT_RETRIES_EXHAUSTED'
    assert len(result['transport_errors']) == 4


def test_invalid_stream_is_not_retried(tmp_path, monkeypatch):
    connections = [[ValueError('malformed stream')], ['message']]
    result, _ = recorded(tmp_path, monkeypatch, connections=connections)
    assert result['reason'] == 'STREAM_FAILED_ValueError'
    assert result['status'] == 'INCOMPLETE'
    assert len(connections) == 1


def test_deadline_during_reconnect_is_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(observer, '_RETRY_DELAYS', (2, 2, 2))
    result, _ = recorded(tmp_path, monkeypatch,
        connections=[['message', ConnectionResetError()], ['message']])
    assert result['reason'] == 'DURATION_COMPLETE'
    assert result['status'] == 'INCOMPLETE'
    assert result['connected_at_deadline'] is False


def test_reconnect_does_not_fill_missing_closed_bar(tmp_path, monkeypatch):
    from tools.gold_bar_journal import restore
    end = raw_rows()[-1][6] + 1
    result, out = recorded(tmp_path, monkeypatch, received=end + 2 * STEP + 20,
        connections=[[ConnectionResetError()], ['gap']])
    assert result['status'] == 'OBSERVED'
    cursor, _ = restore(out)
    assert cursor.bars[-1].open_ms == end + STEP
    decisions = json.loads((out / 'decisions.jsonl').read_text())['decisions']
    assert all(item['status'] == 'WARMUP' and item['entry_side'] is None for item in decisions)


def test_processing_connection_error_is_not_a_socket_retry(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise ConnectionResetError('processing failed')
    monkeypatch.setattr(observer.GoldBarStream, 'ingest', fail)
    connections = [['message'], ['message']]
    result, _ = recorded(tmp_path, monkeypatch, connections=connections)
    assert result['status'] == 'INCOMPLETE'
    assert result['reason'] == 'STREAM_FAILED_ConnectionResetError'
    assert result['transport_errors'] == []
    assert len(connections) == 1


def test_closed_signal_callback_runs_after_durable_decision_before_summary(tmp_path, monkeypatch):
    seen = []

    def callback(row):
        out = tmp_path / 'out'
        assert not (out / 'summary.json').exists()
        assert json.loads((out / 'decisions.jsonl').read_text()) == row
        seen.append(row)

    result, _ = recorded(tmp_path, monkeypatch, on_closed=callback)
    assert result['status'] == 'OBSERVED'
    assert len(seen) == 1


def test_callback_failure_stops_without_network_retry(tmp_path, monkeypatch):
    def callback(row):
        raise ConnectionResetError('consumer failed')

    connections = [['message'], ['message']]
    result, _ = recorded(tmp_path, monkeypatch, connections=connections, on_closed=callback)
    assert result['status'] == 'INCOMPLETE'
    assert result['transport_errors'] == []
    assert len(connections) == 1


def test_first_future_close_finishes_without_waiting_for_duration(tmp_path, monkeypatch):
    from tools.gold_bar_journal import restore
    end = raw_rows()[-1][6] + 1
    result, out = recorded(tmp_path, monkeypatch, seconds=20, stop_after_close_ms=end+100)
    assert result['status'] == 'OBSERVED'
    assert result['reason'] == 'FIRST_NEW_CLOSE_COMPLETE'
    assert result['messages'] == 1
    _, checked = restore(out)
    assert checked['closed_bars'] == 1
    plan = json.loads((out/'plan.json').read_text())
    plan['stop_after_close_ms'] = end + STEP
    (out/'plan.json').write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='FIRST_CLOSE'):
        restore(out)


@pytest.mark.parametrize('proxy', [None, 'http://127.0.0.1:8888'])
def test_protected_bar_network_uses_owned_route_and_tls(tmp_path, monkeypatch, proxy):
    from live.exchange import demo_network
    tls = object()
    monkeypatch.setattr(demo_network, 'proxy_url', lambda: proxy)
    monkeypatch.setattr(demo_network, 'tls_context', lambda: tls)
    calls = []
    def inspect(args, kwargs):
        assert args == (observer.URL,)
        assert kwargs['proxy'] == proxy and kwargs['ssl'] is tls
        calls.append(1)
    end = raw_rows()[-1][6]+1
    result, out = recorded(tmp_path, monkeypatch, stop_after_close_ms=end+100,
        protected_network=True, connection_checks=inspect)
    assert result['status'] == 'OBSERVED' and calls == [1]
    assert json.loads((out/'plan.json').read_text())['network_mode'] == 'PROTECTED_DEMO_NETWORK_V1'


def test_invalid_protected_network_fails_before_output_or_socket(tmp_path, monkeypatch):
    from live.exchange import demo_network
    def invalid():
        raise ValueError('DEMO_NETWORK_CONFIG')
    monkeypatch.setattr(demo_network, 'proxy_url', invalid)
    with pytest.raises(ValueError, match='DEMO_NETWORK_CONFIG'):
        asyncio.run(observer.observe(tmp_path/'history', tmp_path/'out', seconds=1, protected_network=True))
    assert not (tmp_path/'out').exists()
