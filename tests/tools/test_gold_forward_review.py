import asyncio
import hashlib
import json

import pytest

from tests.tools.test_gold_forward_observe import mock_socket, strategy_forward_case
from tools import gold_forward_observe as observe_tool


def captured(tmp_path, monkeypatch, *, sell_pressure=True, protected_network=False, strategy_trial=None):
    trial, definition, document, plan, initial = strategy_forward_case(tmp_path, monkeypatch, strategy_trial=strategy_trial)
    if not sell_pressure:
        for packet in document['packets']:
            packet['payload']['q'] = '0.001'
    root = tmp_path/'forward'
    mock_socket(monkeypatch, root, document=document, initial_ms=initial)
    if protected_network:
        from live.exchange import demo_network
        monkeypatch.setattr(demo_network, 'proxy_url', lambda: None)
    asyncio.run(observe_tool.observe(plan, root, strategy_trial=trial, protected_network=protected_network))
    return root, trial, definition


@pytest.mark.parametrize('pressure', [True, False])
def test_review_recomputes_bound_strategy_decisions(tmp_path, monkeypatch, pressure):
    from tools.gold_forward_review import review
    root, trial, definition = captured(tmp_path, monkeypatch, sell_pressure=pressure)
    result = review(root, strategy_trial=trial)
    assert result['status'] == 'RECOMPUTED_LOCAL_OBSERVATION'
    assert len(result['signals']) == int(pressure)
    assert result['strategy_definition_sha256'] == definition['result_hash']
    assert result['submission_enabled'] is False
    assert result['economic_eligible'] is False
    if pressure:
        assert result['signals'][0]['decision']['intent'] == 'LONG_RESEARCH_SIGNAL'
        assert result['signals'][0]['exit_and_size_plan'] == definition['exit_and_size_plan']


def test_changed_decision_rejected_even_with_recomputed_file_hash(tmp_path, monkeypatch):
    from tools.gold_forward_review import review
    root, trial, _ = captured(tmp_path, monkeypatch)
    rows = [json.loads(row) for row in (root/'decisions.jsonl').read_bytes().splitlines()]
    rows[-1]['decision']['intent'] = None
    raw = b''.join(observe_tool.encoded(row)+b'\n' for row in rows)
    (root/'decisions.jsonl').write_bytes(raw)
    result = json.loads((root/'result.json').read_bytes())
    result['decisions_sha256'] = hashlib.sha256(raw).hexdigest()
    (root/'result.json').write_bytes(observe_tool.encoded(result))
    with pytest.raises(ValueError, match='DECISION_REPLAY'):
        review(root, strategy_trial=trial)


def test_unbound_observation_cannot_supply_strategy_signals(tmp_path, monkeypatch):
    from tools.gold_forward_review import review
    root, trial, _ = captured(tmp_path, monkeypatch)
    registration = json.loads((root/'registration.json').read_bytes())
    registration.pop('strategy_definition')
    (root/'registration.json').write_bytes(observe_tool.encoded(registration))
    with pytest.raises(ValueError):
        review(root, strategy_trial=trial)


def test_changed_raw_trades_rejected_despite_updated_hash(tmp_path, monkeypatch):
    from tools.gold_forward_review import review
    root, trial, _ = captured(tmp_path, monkeypatch)
    events = [json.loads(row) for row in (root/'events.jsonl').read_bytes().splitlines()]
    for event in events:
        frame = json.loads(event['raw'])
        frame['data']['q'] = '0.001'
        event['raw'] = json.dumps(frame)
    raw = b''.join(observe_tool.encoded(row)+b'\n' for row in events)
    (root/'events.jsonl').write_bytes(raw)
    result = json.loads((root/'result.json').read_bytes())
    result['events_sha256'] = hashlib.sha256(raw).hexdigest()
    (root/'result.json').write_bytes(observe_tool.encoded(result))
    with pytest.raises(ValueError, match='DECISION_REPLAY'):
        review(root, strategy_trial=trial)


def test_missed_decision_deadline_has_no_handoff_signal(tmp_path, monkeypatch):
    from tools.gold_forward_review import review
    trial, _, document, plan, initial = strategy_forward_case(tmp_path, monkeypatch)
    # The first packet arrives after the original decision deadline.
    document['packets'][0]['received_at_ms'] = plan['decision_times_ms'][0]+plan['max_lateness_ms']+1
    root = tmp_path/'forward'
    mock_socket(monkeypatch, root, document=document, initial_ms=initial)
    result = asyncio.run(observe_tool.observe(plan, root, strategy_trial=trial))
    assert result['status'] == 'INPUT_INCOMPLETE'
    checked = review(root, strategy_trial=trial)
    assert checked['observation_status'] == 'INPUT_INCOMPLETE'
    assert checked['signals'] == []
