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
    assert queued[0]==('ai_factory',{'attempt':1})
    assert client.post('/factory',headers=headers,json={**request,'budget':4}).status_code==409
    assert client.post('/factory',headers=headers,json={'action':'pause','attempt':1}).status_code==200
