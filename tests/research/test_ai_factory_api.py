import pytest
from fastapi.testclient import TestClient
from services.console.api.ai_factory import factory_payload
from services.research.ai_factory_store import FactoryStore

def test_factory_payload_empty_and_actual(tmp_path):
    empty=factory_payload(tmp_path)
    assert empty['snapshot_status']=='NO_SNAPSHOT' and empty['selected'] is None
    with FactoryStore(tmp_path/'data/cache/ai_factory/factory.db') as s:
        s.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'})
    actual=factory_payload(tmp_path)
    assert actual['selected']['state']=='READY' and actual['authoritative'] is False

def test_factory_resident_health_is_read_only_and_integrity_checked(tmp_path):
    from tools.btc_factory_research import seal
    from tools.cache_io import atomic_write_json
    path=tmp_path/'data/cache/ai_factory/resident.json';path.parent.mkdir(parents=True)
    assert factory_payload(tmp_path)['resident'] is None
    atomic_write_json(str(path),seal({'status':'RUNNING','observed_at_ms':1,'authoritative':False,'account_pnl_computed':False}))
    assert factory_payload(tmp_path)['resident']['status']=='RUNNING'
    path.write_text('{"status":"RUNNING"}')
    assert 'RESIDENT_UNAVAILABLE' in factory_payload(tmp_path)['errors']

def test_factory_mutation_requires_auth(monkeypatch,tmp_path):
    from services.research_runner import main
    monkeypatch.setattr(main,'_STATE_ROOT',tmp_path)
    client=TestClient(main.app)
    assert client.post('/factory',json={'action':'create','attempt':1}).status_code==401
    headers={'X-Runner-Token':main._RUNNER_TOKEN,'X-Operator':'test'}
    queued=[]
    monkeypatch.setattr(main._queue,'submit',lambda *args,**kw:queued.append(args) or 'test-run')
    request={'action':'create','attempt':1,'mode':'baseline','budget':3,'objective':'BTC'}
    assert client.post('/factory',headers=headers,json=request).status_code==200
    assert queued[0]==('ai_factory_pipeline',{'attempt':1})
    assert client.post('/factory',headers=headers,json={**request,'budget':4}).status_code==409
    assert client.post('/factory',headers=headers,json={'action':'pause','attempt':1}).status_code==200


def test_profitability_import_requires_auth_and_preserves_registered_revision(monkeypatch,tmp_path):
    import json
    from services.research_runner import main
    from tests.research.test_profitability_basis_panel import observation
    monkeypatch.setattr(main,'_STATE_ROOT',tmp_path)
    client=TestClient(main.app)
    body={'attempt':1,'kind':'basis','content':json.dumps({'quote':'USDC','observations':[observation(3600000)]})}
    assert client.post('/profitability-input',json=body).status_code==401
    headers={'X-Runner-Token':main._RUNNER_TOKEN,'X-Operator':'test'}
    with FactoryStore(tmp_path/'data/cache/ai_factory/factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'})
        assert client.post('/profitability-input',json=body,headers=headers).status_code==400
        store.update(1,'COMPLETED',{})
    calls=[]
    monkeypatch.setattr(main._queue,'submit',lambda *args,**kwargs:calls.append(args) or 'input-run')
    response=client.post('/profitability-input',json=body,headers=headers)
    assert response.status_code==200 and response.json()['input']['revision']==1
    assert calls==[('profitability_input',{'attempt':1,'input_kind':0,'revision':1})]
    assert client.post('/profitability-input',json={**body,'kind':'../key'},headers=headers).status_code==400
    assert client.post('/profitability-input',json={**body,'content':'{"quote":"USDC","quote":"USDT"}'},headers=headers).status_code==400


@pytest.mark.parametrize(("action","kind"),[("profitability","profitability_study"),("forward_cost","profitability_forward"),("information","profitability_information")])
def test_profitability_action_uses_only_frozen_attempt_and_whitelisted_job(monkeypatch,tmp_path,action,kind):
    from services.research_runner import main
    monkeypatch.setattr(main,'_STATE_ROOT',tmp_path)
    client=TestClient(main.app);body={'action':action,'attempt':1}
    assert client.post('/factory',json=body).status_code==401
    with FactoryStore(tmp_path/'data/cache/ai_factory/factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC'})
    headers={'X-Runner-Token':main._RUNNER_TOKEN,'X-Operator':'test'}
    assert client.post('/factory',headers=headers,json=body).status_code==409
    with FactoryStore(tmp_path/'data/cache/ai_factory/factory.db') as store:store.update(1,'COMPLETED',{})
    calls=[]
    monkeypatch.setattr(main._queue,'submit',lambda *args,**kwargs:calls.append(args) or 'research-only-run')
    assert client.post('/factory',headers=headers,json=body).status_code==200
    assert calls==[(kind,{'attempt':1})]

def test_provider_success_requires_current_configuration_and_recent_receipt(tmp_path,monkeypatch):
    import time
    from services.research.ai_factory_provider_contract import provider_identity
    env={'QUANTOS_FACTORY_ENDPOINT':'http://127.0.0.1:8001/v1/chat/completions',
         'QUANTOS_FACTORY_MODEL':'qwen3.8-27b','QUANTOS_FACTORY_TRANSPORT':'LOCAL_HTTP'}
    for key,value in env.items():monkeypatch.setenv(key,value)
    assert factory_payload(tmp_path)['provider']['connected'] is False
    with FactoryStore(tmp_path/'data/cache/ai_factory/factory.db') as store:
        store.create({'attempt':1,'mode':'ai','budget':3,'objective':'BTC'})
        store.record_trial(1,0,{'status':'PASSED','proposal':{'provider_id':provider_identity(env),
            'received_at_ms':int(time.time()*1000),'model':'qwen3.8-27b'}})
    assert factory_payload(tmp_path)['provider']['status']=='RECENT_CALL_SUCCEEDED'
    monkeypatch.setenv('QUANTOS_FACTORY_MODEL','different-model')
    assert factory_payload(tmp_path)['provider']['connected'] is False
    monkeypatch.setenv('QUANTOS_FACTORY_MODEL','qwen3.8-27b')
    monkeypatch.setattr('services.console.api.ai_factory.time.time_ns',lambda:time.time()*1e9+901*1e9)
    assert factory_payload(tmp_path)['provider']['connected'] is False


def test_lifecycle_contract_reads_36_sources_and_leaves_missing_state_untouched(tmp_path):
    from services.console.api.ai_factory_lifecycle import lifecycle_payload
    payload=lifecycle_payload(tmp_path)
    assert len(payload['sources'])==36 and len({r['id'] for r in payload['sources']})==36
    assert payload['models']==[] and payload['campaigns']==[]
    assert payload['read_only'] and not payload['authoritative']
    assert not (tmp_path/'data').exists()


def test_campaign_control_requires_token_and_immutable_policy(monkeypatch,tmp_path):
    from services.research_runner import main
    monkeypatch.setattr(main,'_STATE_ROOT',tmp_path)
    client=TestClient(main.app);body={'action':'create','campaign_id':1}
    assert client.post('/factory-campaign',json=body).status_code==401
    headers={'X-Runner-Token':main._RUNNER_TOKEN,'X-Operator':'test'}
    assert client.post('/factory-campaign',headers=headers,json=body).status_code==200
    assert client.post('/factory-campaign',headers=headers,json=body).status_code==200
    assert client.post('/factory-campaign',headers=headers,json={**body,'budget':6}).status_code==409
    assert client.post('/factory-campaign',headers=headers,json={'action':'stop','campaign_id':1}).status_code==200
