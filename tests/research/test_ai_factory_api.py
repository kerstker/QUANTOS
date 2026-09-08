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
