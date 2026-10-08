import hashlib

import pytest

from services.research.ai_factory_store import FactoryStore
from tests.tools.test_gold_cost_replay import inputs
from tools.gold_cost_inputs import dump_inputs


def policy(raw):
    return {'campaign_id': 1, 'market': 'XAUUSDT', 'mode': 'ai', 'budget': 1,
            'objective': 'Compare gold absorption hypotheses', 'max_cycles': 2,
            'interval_hours': 1, 'input_id': 1, 'input_sha256': hashlib.sha256(raw).hexdigest()}


def test_gold_budget_reuses_rounds_and_excludes_consumed_choice(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as store:
        store.create_campaign(policy(b'base'), operator='test', now=0)
        first = store.reserve_campaign(1, now=0)
        assert store.get(first['attempt'])['config']['remaining_candidates'] == list(range(6))
        store.record_trial(first['attempt'], 0, {'candidate_id': 2, 'status': 'FAILED'})
        store.finish_campaign_round(1, 0, 'FAILED', now=100)
        assert store.reserve_campaign(1, now=101) is None
        second = store.reserve_campaign(1, now=3600100)
        assert store.get(second['attempt'])['config']['remaining_candidates'] == [0, 1, 3, 4, 5]
        store.finish_campaign_round(1, 1, 'FAILED', now=3600200)
        assert store.reserve_campaign(1, now=7200300) is None
        store.verify_campaigns()


@pytest.mark.parametrize('change', [{'budget': 2}, {'max_cycles': 7}, {'mode': 'baseline'},
                                   {'trigger': 'drift'}, {'input_sha256': 'bad'}])
def test_gold_policy_cannot_borrow_hourly_budget_or_drift(tmp_path, change):
    with FactoryStore(tmp_path/'factory.db') as store:
        with pytest.raises(ValueError):
            store.create_campaign({**policy(b'base'), **change}, operator='test', now=0)


@pytest.mark.parametrize('damage_diagnostics', [False, True])
def test_two_rounds_use_original_scheduler_queue_and_distinct_choices(tmp_path, monkeypatch, damage_diagnostics):
    from tools import gold_ai_research, gold_cost_research
    from tools.ai_factory_provider import propose
    from tests.research.test_ai_factory_provider import LOCAL, Transport
    from services.research_runner.factory_campaign import campaign_tick
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.registry import RunRegistry
    from services.research_runner.runner import JobQueue
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    from services.research import ai_factory_store
    monkeypatch.setattr(ai_factory_store, 'now_ms', lambda: 1)
    args = inputs()
    monkeypatch.setattr(gold_ai_research, 'source_identity', lambda: args['release_identity'])
    monkeypatch.setattr(gold_cost_research, 'source_identity', lambda: args['release_identity'])
    raw = dump_inputs(args)
    base = tmp_path/'data/cache/ai_factory'
    (base/'gold-inputs').mkdir(parents=True)
    (base/'gold-inputs/1.json').write_bytes(raw)
    choices = []
    memories = []
    def proposal(objective, remaining, history, **kwargs):
        assert history == []
        memories.append(kwargs.get('research_memory'))
        choices.append(remaining[0])
        return propose(objective, remaining, history, **kwargs, environ=LOCAL,
                       transport=Transport({'candidate_id': remaining[0], 'reason': 'bounded test'}))
    monkeypatch.setattr(gold_ai_research, 'propose', proposal)
    # Exercise the user-facing entrypoint through the actual campaign scheduler
    # and JobQueue, rather than provisioning the store behind the API.
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    from services.research_runner import main
    monkeypatch.setattr(main, '_STATE_ROOT', tmp_path)
    monkeypatch.setattr(main, 'time', SimpleNamespace(time_ns=lambda: 0))
    response = TestClient(main.app).post('/factory-campaign', json=dict(policy(raw), action='create'),
        headers={'X-Runner-Token': main._RUNNER_TOKEN, 'X-Operator': 'isolated-test'})
    assert response.status_code == 200, response.text
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue = JobQueue(registry)
    assert campaign_tick(tmp_path, queue, registry, now=0)['status'] == 'SUBMITTED'
    queue.join()
    campaign_tick(tmp_path, queue, registry, now=1)
    assert choices == [0]
    with FactoryStore(base/'factory.db') as store:
        first = store.get(1)['result']
        diagnostics = first['signal_diagnostics']
        assert diagnostics['reason_counts']['SELL_PRESSURE_WITH_RECLAIM'] == 1
        assert diagnostics['reason_counts']['LATER_WINDOW_CONFIRMED'] == 1
        assert diagnostics['final_state'] == 'SIGNALLED'
        if damage_diagnostics:
            diagnostics['reason_counts']['LATER_WINDOW_CONFIRMED'] = 2
            diagnostics['evaluated_windows'] += 1
            store.update(1, 'COMPLETED', first)
    assert campaign_tick(tmp_path, queue, registry, now=3600001)['status'] == 'SUBMITTED'
    queue.join()
    if damage_diagnostics:
        assert choices == [0]
        assert not (base/'gold/results/2').exists()
        assert any(r.status == 'error' for r in registry.list())
        return
    campaign_tick(tmp_path, queue, registry, now=3600002)
    campaign_tick(tmp_path, queue, registry, now=7200002)
    assert choices == [0, 1]
    assert memories[0] is None
    assert memories[1] is not None
    assert [r['candidate_id'] for r in memories[1]['records']] == [0]
    assert memories[1]['schema'] == 'GOLD_RESEARCH_MEMORY_V3'
    assert memories[1]['records'][0]['signal_diagnostics'] == diagnostics
    import json
    original_request = (base/'gold/results/2/request.json').read_bytes()
    assert json.loads(original_request)['research_memory'] == memories[1]
    from tools.gold_factory_research import recover_session
    strategy_path = base/'gold/results/2/trial/strategy-definition.json'
    original_definition = strategy_path.read_bytes()
    definition = json.loads(original_definition)
    assert definition['research_identity']['candidate_id'] == 1
    assert definition['entry_rule']['confirm_windows'] == 3
    assert definition['prospective_registration_required'] is True
    recover_session({'attempt': 2})
    assert choices == [0, 1]
    assert (base/'gold/results/2/request.json').read_bytes() == original_request
    assert strategy_path.read_bytes() == original_definition
    with FactoryStore(base/'factory.db') as store:
        store.verify_campaigns()
        assert [r['state'] for r in store.campaign_snapshot()['rounds']] == ['COMPLETED', 'COMPLETED']
        assert store.campaign_snapshot()['campaigns'][0]['state'] == 'COMPLETED'
        assert all(store.trials(n)[0]['trial_id'] for n in (1, 2))
    assert (base/'gold-inputs/1.json').read_bytes() == raw
    assert all(r.verdict == 'TECHNICAL_VALIDATION_ONLY' for r in registry.list())
    from services.console.api.ai_factory_lifecycle import lifecycle_payload
    view = lifecycle_payload(tmp_path)
    assert {r['attempt'] for r in view['gold_results']} == {1, 2}
    assert all(r['report_status'] == 'AVAILABLE' and r['costs'] is not None for r in view['gold_results'])


def test_paused_gold_campaign_does_not_reserve_next_round(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as store:
        store.create_campaign(policy(b'base'), operator='test', now=0)
        store.control_campaign(1, 'pause', operator='test')
        assert store.reserve_campaign(1, now=1) is None
        assert store.campaign_snapshot()['rounds'] == []


def test_gold_session_cannot_enter_hourly_execution(tmp_path):
    from tools.ai_quant_factory import execute
    with FactoryStore(tmp_path/'factory.db') as store:
        store.create_campaign(policy(b'base'), operator='test', now=0)
        row = store.reserve_campaign(1, now=0)
        with pytest.raises(ValueError, match='market-specific'):
            execute(store, row['attempt'], None, None, None, tmp_path/'hourly')
        assert not (tmp_path/'hourly').exists()


def test_changed_frozen_input_is_rejected_before_model(tmp_path, monkeypatch):
    from tools.gold_factory_research import run_session
    from tools import gold_ai_research
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    base = tmp_path/'data/cache/ai_factory'
    with FactoryStore(base/'factory.db') as store:
        store.create_campaign(policy(b'original'), operator='test', now=0)
        row = store.reserve_campaign(1, now=0)
    (base/'gold-inputs').mkdir()
    (base/'gold-inputs/1.json').write_bytes(b'changed')
    monkeypatch.setattr(gold_ai_research, 'propose', lambda *a, **k: pytest.fail('input not bound'))
    with pytest.raises(ValueError, match='INPUT_CHANGED'):
        run_session({'attempt': row['attempt']})
    assert not (base/'gold/results').exists()


def test_extra_trial_receipt_cannot_hide_budget_overrun(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as store:
        store.create_campaign(policy(b'base'), operator='test', now=0)
        row = store.reserve_campaign(1, now=0)
        for ordinal in (0, 1):
            store.record_trial(row['attempt'], ordinal, {'candidate_id': ordinal, 'status': 'FAILED'})
        with pytest.raises(ValueError, match='budget'):
            store.reserve_campaign(1, now=100)


def test_interrupted_cost_trial_still_consumes_known_choice(tmp_path, monkeypatch):
    from services.research import ai_factory_store
    monkeypatch.setattr(ai_factory_store, 'now_ms', lambda: 1)
    from tools import gold_ai_research, gold_factory_research
    from tools.ai_factory_provider import propose
    from tests.research.test_ai_factory_provider import LOCAL, Transport
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    args = inputs()
    monkeypatch.setattr(gold_ai_research, 'source_identity', lambda: args['release_identity'])
    raw = dump_inputs(args)
    base = tmp_path/'data/cache/ai_factory'
    (base/'gold-inputs').mkdir(parents=True)
    (base/'gold-inputs/1.json').write_bytes(raw)
    with FactoryStore(base/'factory.db') as store:
        store.create_campaign(policy(raw), operator='test', now=0)
        first = store.reserve_campaign(1, now=0)
    monkeypatch.setattr(gold_ai_research, 'propose', lambda *a, **k: propose(*a, **k,
        environ=LOCAL, transport=Transport({'candidate_id': 2, 'reason': 'test'})))
    def stop(*a, **k):
        raise KeyboardInterrupt()
    monkeypatch.setattr(gold_ai_research, 'archive_experiment', stop)
    with pytest.raises(KeyboardInterrupt):
        gold_factory_research.run_session({'attempt': first['attempt']})
    with FactoryStore(base/'factory.db') as store:
        assert store.trials(first['attempt']) == [{'candidate_id': 2, 'status': 'FAILED'}]
        store.finish_campaign_round(1, 0, 'FAILED', now=1)
        second = store.reserve_campaign(1, now=3600001)
        assert 2 not in store.get(second['attempt'])['config']['remaining_candidates']
