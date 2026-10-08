import asyncio
import json
import pytest
from tests.research.test_gold_forward import plan
from tests.tools.test_gold_signal_replay import archive
from tools import gold_forward_observe as tool


def mock_socket(monkeypatch,root,*,disconnect=False,document=None,initial_ms=400):
    current=[initial_ms]
    monkeypatch.setattr(tool,'now_ms',lambda:current[0])
    packets=iter((archive() if document is None else document)['packets'])
    class Socket:
        async def __aenter__(self):
            assert (root/'registration.json').is_file()
            return self
        async def __aexit__(self,*args):return False
        async def recv(self):
            if disconnect:raise ConnectionError('do not expose network detail')
            packet=next(packets)
            current[0]=packet['received_at_ms']
            return json.dumps({'stream':'xauusdt@aggTrade','data':packet['payload']})
    monkeypatch.setattr(tool,'connect',lambda *a,**k:Socket())
    return current


def strategy_forward_case(tmp_path, monkeypatch, *, strategy_trial=None):
    from tests.research.test_gold_ai_research import setup
    from tools.gold_execution_identity import retain_strategy_definition
    if strategy_trial is None:
        research, _, dest = setup(tmp_path, monkeypatch)
        research.run_research({'attempt': 1})
        strategy_trial = dest/'trial'
    definition = retain_strategy_definition(strategy_trial)
    shift = definition['research_identity']['available_at_ms'] + 1000
    document = archive()
    document['start_ms'] += shift
    document['decision_times_ms'] = [t + shift for t in document['decision_times_ms']]
    for key in ('registered_at_ms', 'expires_at_ms'):
        document['zone'][key] += shift
    for packet in document['packets']:
        packet['received_at_ms'] += shift
        for key in ('E', 'T'):
            packet['payload'][key] += shift
    forward = {k: v for k, v in document.items() if k != 'packets'}
    forward.update(schema='GOLD_FORWARD_PLAN_V1', max_lateness_ms=20)
    return strategy_trial, definition, document, forward, shift+400


def test_protected_network_config_error_never_falls_back_to_environment(tmp_path, monkeypatch):
    from live.exchange import demo_network
    calls = []
    def invalid():
        raise ValueError('DEMO_NETWORK_CONFIG')
    monkeypatch.setattr(demo_network, 'proxy_url', invalid)
    monkeypatch.setattr(tool, 'connect', lambda *a, **k: calls.append(1))
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:9')
    with pytest.raises(ValueError, match='DEMO_NETWORK_CONFIG'):
        asyncio.run(tool.observe({}, tmp_path/'protected', protected_network=True))
    assert calls == [] and not (tmp_path/'protected').exists()


@pytest.mark.parametrize('sell_pressure', [True, False])
def test_archived_strategy_drives_new_forward_windows(tmp_path, monkeypatch, sell_pressure):
    trial, definition, document, forward, initial = strategy_forward_case(tmp_path, monkeypatch)
    if not sell_pressure:
        for packet in document['packets']:
            packet['payload']['q'] = '0.001'
    root = tmp_path/'strategy-forward'
    mock_socket(monkeypatch, root, document=document, initial_ms=initial)
    result = asyncio.run(tool.observe(forward, root, strategy_trial=trial))
    assert result['status'] == 'COMPLETE_OBSERVATION'
    registration = json.loads((root/'registration.json').read_bytes())
    assert registration['strategy_definition'] == definition
    assert result['strategy_definition_sha256'] == definition['result_hash']
    rows = [json.loads(line) for line in (root/'decisions.jsonl').read_text().splitlines()]
    assert rows[-1]['decision']['intent'] == ('LONG_RESEARCH_SIGNAL' if sell_pressure else None)
    assert rows[0]['observed_at_ms'] > definition['research_identity']['available_at_ms']
    assert result['net_pnl'] is None and result['submission_enabled'] is False


@pytest.mark.parametrize('change', ['threshold', 'region', 'future'])
def test_strategy_substitution_rejected_before_forward_network(tmp_path, monkeypatch, change):
    trial, _, _, forward, initial = strategy_forward_case(tmp_path, monkeypatch)
    if change == 'threshold':
        forward['rule']['sell_threshold'] = '1'
    elif change == 'region':
        forward['zone']['lower'] = '1'
    else:
        initial = 0
    monkeypatch.setattr(tool, 'now_ms', lambda: initial)
    monkeypatch.setattr(tool, 'connect', lambda *a, **k: pytest.fail('network must not start'))
    with pytest.raises(ValueError, match='GOLD_FORWARD_STRATEGY'):
        asyncio.run(tool.observe(forward, tmp_path/'rejected-forward', strategy_trial=trial))
    assert not (tmp_path/'rejected-forward').exists()


def test_registration_precedes_network_and_decisions_are_saved_as_observed(tmp_path,monkeypatch):
    root=tmp_path/'forward';mock_socket(monkeypatch,root)
    result=asyncio.run(tool.observe(plan(),root))
    assert result['status']=='COMPLETE_OBSERVATION'
    assert result['local_registration_only'] is True and result['authoritative'] is False
    rows=[json.loads(line) for line in (root/'decisions.jsonl').read_text().splitlines()]
    assert rows[-1]['decision']['intent']=='LONG_RESEARCH_SIGNAL'
    assert rows[0]['observed_at_ms']==2010
    assert (root/'events.jsonl').stat().st_size>0


def test_disconnect_is_retained_not_reported_as_complete(tmp_path,monkeypatch):
    root=tmp_path/'forward';mock_socket(monkeypatch,root,disconnect=True)
    result=asyncio.run(tool.observe(plan(),root))
    assert result['status']=='STREAM_INTERRUPTED'
    assert (root/'registration.json').is_file()
    assert 'do not expose' not in (root/'result.json').read_text()


def test_existing_directory_blocks_before_network(tmp_path,monkeypatch):
    root=tmp_path/'forward';root.mkdir();mock_socket(monkeypatch,root)
    monkeypatch.setattr(tool,'connect',lambda *a,**k:pytest.fail('network must not start'))
    with pytest.raises(FileExistsError):asyncio.run(tool.observe(plan(),root))


def test_slow_computation_cannot_claim_an_on_time_signal(tmp_path,monkeypatch):
    root=tmp_path/'forward';clock=mock_socket(monkeypatch,root)
    original=tool.ForwardObservation.advance
    def delayed(self,*args,**kwargs):
        result=original(self,*args,**kwargs)
        if result is not None:clock[0]+=100
        return result
    monkeypatch.setattr(tool.ForwardObservation,'advance',delayed)
    result=asyncio.run(tool.observe(plan(),root))
    row=json.loads((root/'decisions.jsonl').read_text())
    assert result['status']=='INPUT_INCOMPLETE' and row['decision'] is None
    assert row['reason']=='COMPUTATION_DEADLINE_EXCEEDED'
