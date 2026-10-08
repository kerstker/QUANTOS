import asyncio
import json
import shutil

import pytest

from tests.tools.test_gold_bar_observe import recorded
from tests.research.test_gold_bar_dataset import raw_rows, STEP


def fixture(tmp_path, monkeypatch):
    from tools import gold_bar_forward as forward
    result, journal = recorded(tmp_path, monkeypatch)
    assert result['status'] == 'OBSERVED'
    end = raw_rows()[-1][6] + 1
    definition = dict(definition_hash='d' * 64, candidate='channel', submission_enabled=False,
                      size_and_timing={'entry_timeout_ms': 5000})
    monkeypatch.setattr(forward, 'define', lambda *a, **kw: definition)
    monkeypatch.setattr(forward, 'now_ms', lambda: end + 100)
    monkeypatch.setattr(forward, 'source_identity', lambda: {'fixture': True})

    async def capture(history, output, *, seconds, on_closed):
        registration = json.loads((output.parent / 'registration.json').read_text())
        assert registration['definition'] == definition
        assert registration['registered_at_ms'] < end + STEP
        shutil.copytree(journal, output)
        monkeypatch.setattr(forward, 'now_ms', lambda: end + STEP + 21)
        on_closed(json.loads((output / 'decisions.jsonl').read_text()))
        assert (output.parent / 'live-signals.jsonl').read_bytes()
        assert not (output.parent / 'result.json').exists()
        return result

    monkeypatch.setattr(forward, 'observe', capture)
    return forward, end


def test_preregistered_definition_binds_actual_closed_bar_and_detects_tamper(tmp_path, monkeypatch):
    forward, _ = fixture(tmp_path, monkeypatch)
    out = tmp_path / 'forward'
    result = asyncio.run(forward.run(tmp_path / 'archive', candidate='channel',
        source_directory=tmp_path, history=tmp_path / 'history', output=out, seconds=1))
    assert result['status'] == 'FORWARD_SIGNALS_RECORDED'
    assert result['new_closed_bars'] == 1
    assert result['submission_enabled'] is False
    row = json.loads((out / 'selected-decisions.jsonl').read_text())
    assert row['signal']['entry_side'] == 'BUY'
    assert row['definition_hash'] == 'd' * 64
    assert forward.review(out, archive=tmp_path / 'archive', source_directory=tmp_path)['new_closed_bars'] == 1
    live_path = out / 'live-signals.jsonl'
    original_live = live_path.read_bytes()
    live = json.loads(original_live)
    live['signal_valid_until_ms'] += 1
    live_path.write_text(json.dumps(live) + '\n')
    with pytest.raises(ValueError, match='FORWARD_LIVE_CHANGED'):
        forward.review(out, archive=tmp_path / 'archive', source_directory=tmp_path)
    live_path.write_bytes(original_live)
    row['signal']['entry_side'] = 'SELL'
    (out / 'selected-decisions.jsonl').write_text(json.dumps(row) + '\n')
    with pytest.raises(ValueError, match='FORWARD_DECISIONS_CHANGED'):
        forward.review(out, archive=tmp_path / 'archive', source_directory=tmp_path)


def test_registration_after_close_cannot_relabel_it_forward(tmp_path, monkeypatch):
    forward, end = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(forward, 'now_ms', lambda: end + STEP + 100)

    async def capture(history, output, *, seconds, on_closed):
        shutil.copytree(tmp_path / 'out', output)
        on_closed(json.loads((output / 'decisions.jsonl').read_text()))
        return json.loads((output / 'summary.json').read_text())

    monkeypatch.setattr(forward, 'observe', capture)
    out = tmp_path / 'forward'
    result = asyncio.run(forward.run(tmp_path / 'archive', candidate='channel',
        source_directory=tmp_path, history=tmp_path / 'history', output=out, seconds=1))
    assert result['status'] == 'NO_NEW_CLOSED_BAR'
    assert result['new_closed_bars'] == 0
    assert (out / 'selected-decisions.jsonl').read_bytes() == b''


def test_future_warmup_rejected_before_socket_or_output(tmp_path, monkeypatch):
    forward, end = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(forward, 'now_ms', lambda: end)
    out = tmp_path / 'forward'
    with pytest.raises(ValueError, match='FUTURE_WARMUP'):
        asyncio.run(forward.run(tmp_path / 'archive', candidate='channel',
            source_directory=tmp_path, history=tmp_path / 'history', output=out, seconds=1))
    assert not out.exists()


def test_failed_capture_never_publishes_selected_signals(tmp_path, monkeypatch):
    forward, _ = fixture(tmp_path, monkeypatch)

    async def fail(*args, **kwargs):
        return dict(status='INCOMPLETE', reason='TRANSPORT_RETRIES_EXHAUSTED')

    monkeypatch.setattr(forward, 'observe', fail)
    out = tmp_path / 'forward'
    result = asyncio.run(forward.run(tmp_path / 'archive', candidate='channel',
        source_directory=tmp_path, history=tmp_path / 'history', output=out, seconds=1))
    assert result['status'] == 'INCOMPLETE'
    assert not (out / 'selected-decisions.jsonl').exists()


def test_preparation_latency_never_extends_original_entry_deadline():
    from tools.gold_bar_forward import live_row
    registration = {'definition': {'candidate': 'reversion', 'definition_hash': 'd' * 64,
                                  'size_and_timing': {'entry_timeout_ms': 5000}}}
    row = dict(close_ms=1000, received_at_ms=1001,
               decisions=[dict(candidate='reversion', decision_ms=1001)])
    assert live_row(row, registration, 6000)['entry_window_status'] == 'TIMELY'
    delayed = live_row(row, registration, 6001)
    assert delayed['entry_window_status'] == 'EXPIRED'
    assert delayed['signal_valid_until_ms'] == 6001
    with pytest.raises(ValueError, match='PREPARATION_CLOCK'):
        live_row(row, registration, 1000)


@pytest.mark.parametrize('protected', [False, True])
def test_first_close_forward_is_complete_immediately_and_reviewable(tmp_path, monkeypatch, protected):
    from tools import gold_bar_forward as forward
    result, _ = recorded(tmp_path, monkeypatch)
    if protected:
        from live.exchange import demo_network
        monkeypatch.setattr(demo_network, 'proxy_url', lambda: None)
        monkeypatch.setattr(demo_network, 'tls_context', lambda: object())
    assert result['status'] == 'OBSERVED'
    end = raw_rows()[-1][6] + 1
    definition = dict(definition_hash='d'*64, candidate='channel', submission_enabled=False,
        size_and_timing={'entry_timeout_ms':5000})
    monkeypatch.setattr(forward, 'define', lambda *a, **kw: definition)
    monkeypatch.setattr(forward, 'source_identity', lambda: {'fixture':True})
    times = iter([end+100, end+STEP+21])
    monkeypatch.setattr(forward, 'now_ms', lambda: next(times))
    out = tmp_path/'first'
    result = asyncio.run(forward.run(tmp_path/'archive', candidate='channel',
        source_directory=tmp_path, history=tmp_path/'history', output=out, seconds=20, first_close=True, protected_network=protected))
    assert result['status'] == 'FORWARD_SIGNALS_RECORDED'
    assert json.loads((out/'journal'/'summary.json').read_text())['reason'] == 'FIRST_NEW_CLOSE_COMPLETE'
    assert forward.review(out, archive=tmp_path/'archive', source_directory=tmp_path) == result
    live = json.loads((out/'live-signals.jsonl').read_text())
    assert live['signal_valid_until_ms'] == live['received_at_ms']+5000
    registration = json.loads((out/'registration.json').read_text())
    if protected:
        assert registration['network_mode'] == 'PROTECTED_DEMO_NETWORK_V1'
        registration['network_mode'] = 'UNTRUSTED'
        (out/'registration.json').write_text(json.dumps(registration))
        with pytest.raises(ValueError, match='NETWORK_BINDING'):
            forward.review(out, archive=tmp_path/'archive', source_directory=tmp_path)
        registration['network_mode'] = 'PROTECTED_DEMO_NETWORK_V1'
    registration.pop('first_close')
    (out/'registration.json').write_text(json.dumps(registration))
    with pytest.raises(ValueError, match='FIRST_CLOSE_BINDING'):
        forward.review(out, archive=tmp_path/'archive', source_directory=tmp_path)
