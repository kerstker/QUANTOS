import json
import pytest
from services.research.ai_factory_store import digest
from services.research.gold_research_memory import validate_memory
from tools.ai_factory_provider import propose
from tests.research.test_ai_factory_provider import LOCAL,Transport


def memory():
    value={'schema':'GOLD_RESEARCH_MEMORY_V1','usage':'TECHNICAL_DIAGNOSTIC_NOTES','as_of_ms':100,
           'economic_eligible':False,'records':[{'attempt':1,'candidate_id':0,'trial_id':'a'*64,
           'input_sha256':'b'*64,'report_sha256':'c'*64,'known_at_ms':99,'outcome':'NO_SIGNAL'}]}
    return {**value,'result_hash':digest(value)}


@pytest.mark.parametrize('include_diagnostics', [False, True])
def test_model_receives_diagnostic_notes_without_scores(include_diagnostics):
    value=memory();transport=Transport({'candidate_id':1,'reason':'inspect another hypothesis'})
    if include_diagnostics:
        from services.research.gold_research_memory import signal_diagnostics
        from tools.gold_cost_replay import replay_costs
        from tests.tools.test_gold_cost_replay import inputs
        arguments = inputs()
        arguments['document']['rule']['sell_threshold'] = '1000000'
        report = replay_costs(**arguments)
        assert report['outcome'] == 'NO_SIGNAL'
        diagnostics = signal_diagnostics(report)
        assert diagnostics == {'evaluated_windows': 2,
            'reason_counts': {'CONDITIONS_NOT_MET': 2}, 'final_state': 'WATCHING'}
        value['schema'] = 'GOLD_RESEARCH_MEMORY_V3'
        value['records'][0]['signal_diagnostics'] = diagnostics
        value['result_hash'] = digest({k:v for k,v in value.items() if k!='result_hash'})
    response=propose('gold',[1],[],candidate_catalog='GOLD_ABSORPTION_V1',research_memory=value,
                     environ=LOCAL,transport=transport)
    body=json.loads(transport.request['messages'][1]['content'])
    assert body['prior_technical_experiments']==value
    assert body['task']['inner_validation']==[]
    assert response['research_memory_hash']==value['result_hash']


@pytest.mark.parametrize('change', [
    {'reason_counts': {'PROFITABLE': 1}}, {'evaluated_windows': True},
    {'evaluated_windows': 2}, {'net_pnl': '10'}, {'final_state': 'APPROVED'}])
def test_invalid_signal_diagnostics_never_reach_model(change):
    value = memory()
    value['schema'] = 'GOLD_RESEARCH_MEMORY_V3'
    value['records'][0]['signal_diagnostics'] = {
        'evaluated_windows': 1, 'reason_counts': {'CONDITIONS_NOT_MET': 1},
        'final_state': 'WATCHING', **change}
    value['result_hash'] = digest({k:v for k,v in value.items() if k!='result_hash'})
    transport = Transport({'candidate_id': 1, 'reason': 'unused'})
    with pytest.raises(ValueError):
        propose('gold', [1], [], candidate_catalog='GOLD_ABSORPTION_V1', research_memory=value,
                environ=LOCAL, transport=transport)
    assert transport.request is None


@pytest.mark.parametrize('failed', [False, True])
@pytest.mark.parametrize('change',[{'known_at_ms':101},{'outer_score':10},{'outcome':'PROFITABLE'},{'candidate_id':True}])
def test_future_or_performance_fields_are_rejected_before_model_call(change, failed):
    value=memory()
    if failed:
        value['schema']='GOLD_RESEARCH_MEMORY_V2'
        row=value['records'][0]
        row['config_sha256']=row.pop('trial_id')
        row['trial_receipts_sha256']=row.pop('report_sha256')
        row['outcome']='RESEARCH_FAILED'
    value['records'][0].update(change)
    value['result_hash']=digest({k:v for k,v in value.items() if k!='result_hash'})
    transport=Transport({'candidate_id':1,'reason':'unused'})
    with pytest.raises(ValueError):
        propose('gold',[1],[],candidate_catalog='GOLD_ABSORPTION_V1',research_memory=value,
                environ=LOCAL,transport=transport)
    assert transport.request is None


def test_memory_is_hash_bound_and_cannot_be_used_as_hourly_search_history():
    value=memory();value['records'][0]['outcome']='OPEN_AT_END'
    with pytest.raises(ValueError):validate_memory(value)
    with pytest.raises(ValueError):
        propose('hourly',[1],[],research_memory=memory(),environ=LOCAL,transport=Transport({}))


def test_round_memory_is_scoped_and_frozen_across_reopening(tmp_path, monkeypatch):
    from services.research import ai_factory_store
    from services.research.ai_factory_store import FactoryStore
    from tests.research.test_gold_factory_campaign import policy
    monkeypatch.setattr(ai_factory_store, 'now_ms', lambda: 10)
    path = tmp_path/'factory.db'
    with FactoryStore(path) as store:
        for campaign_id in (1, 2):
            store.create_campaign(dict(policy(str(campaign_id).encode()), campaign_id=campaign_id),
                                  operator='test', now=0)
            row = store.reserve_campaign(campaign_id, now=0)
            summary = {'candidate_id': 0, 'trial_id': str(campaign_id)*64,
                       'report_sha256': 'c'*64, 'outcome': 'NO_SIGNAL'}
            store.record_trial(row['attempt'], 0, dict(summary, status='PASSED'))
            store.update(row['attempt'], 'COMPLETED', summary)
            store.finish_campaign_round(campaign_id, 0, 'COMPLETED', now=11)
        second = store.reserve_campaign(1, now=3600011)
        config = store.get(second['attempt'])['config']
        assert [r['attempt'] for r in config['research_memory']['records']] == [1]
        assert config['research_memory']['as_of_ms'] == 3600011
    with FactoryStore(path) as store:
        assert store.reserve_campaign(1, now=7200011) == second
        assert store.get(second['attempt'])['config'] == config


def test_future_round_memory_does_not_consume_next_reservation(tmp_path, monkeypatch):
    from services.research import ai_factory_store
    from services.research.ai_factory_store import FactoryStore
    from tests.research.test_gold_factory_campaign import policy
    monkeypatch.setattr(ai_factory_store, 'now_ms', lambda: 7200000)
    with FactoryStore(tmp_path/'factory.db') as store:
        store.create_campaign(policy(b'base'), operator='test', now=0)
        first = store.reserve_campaign(1, now=0)
        store.record_trial(first['attempt'], 0, {'candidate_id': 0, 'status': 'PASSED'})
        store.update(first['attempt'], 'COMPLETED', {'candidate_id': 0, 'trial_id': 'a'*64,
                     'report_sha256': 'c'*64, 'outcome': 'NO_SIGNAL'})
        store.finish_campaign_round(1, 0, 'COMPLETED', now=10)
        before = store.campaign_snapshot()
        with pytest.raises(ValueError, match='future gold research memory'):
            store.reserve_campaign(1, now=3600010)
        assert store.campaign_snapshot() == before
        assert store.c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 1
