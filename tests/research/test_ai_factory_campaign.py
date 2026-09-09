import pytest
from services.research.ai_factory_store import FactoryStore


def policy():
    return {'campaign_id':1,'objective':'BTC 成本后研究','mode':'baseline','budget':3,'max_cycles':2,'interval_hours':1,'source_ids':['aqr','buildalpha']}


def test_campaign_reserves_the_same_frozen_research_protocol(tmp_path):
    config={**policy(),'mode':'ai','research_protocol':'THREE_ROLES_V1'}
    with FactoryStore(tmp_path/'factory.db') as store:
        store.create_campaign(config,operator='fixture',now=0)
        first=store.reserve_campaign(1,now=0)
        assert store.get(first['attempt'])['config']['research_protocol']=='THREE_ROLES_V1'
        store.verify_campaigns()


def test_reservation_survives_restart_and_counts_failures(tmp_path):
    path=tmp_path/'factory.db'
    with FactoryStore(path) as s:
        s.create_campaign(policy(),operator='test',now=0)
        first=s.reserve_campaign(1,now=0)
        assert first['ordinal']==0 and first['attempt']==1
    with FactoryStore(path) as s:
        assert s.reserve_campaign(1,now=100)==first
        s.finish_campaign_round(1,0,'FAILED',now=100)
        assert s.reserve_campaign(1,now=101) is None
        second=s.reserve_campaign(1,now=3600100)
        assert second['attempt']==2
        s.finish_campaign_round(1,1,'FAILED',now=3600200)
        assert s.reserve_campaign(1,now=7200300) is None
        assert len(s.campaign_snapshot()['rounds'])==2


def test_pause_and_policy_identity_are_persistent(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as s:
        s.create_campaign(policy(),operator='test',now=0)
        with pytest.raises(ValueError):s.create_campaign({**policy(),'max_cycles':3},operator='test',now=0)
        s.control_campaign(1,'pause',operator='test')
        assert s.reserve_campaign(1,now=999999) is None
        s.control_campaign(1,'resume',operator='test')
        assert s.reserve_campaign(1,now=999999) is not None
        s.control_campaign(1,'stop',operator='test')
        with pytest.raises(ValueError):s.control_campaign(1,'resume',operator='test')


def test_real_queue_scheduler_preserves_invocation_after_enqueue_crash(tmp_path,monkeypatch):
    from dataclasses import replace
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.store import ResearchRunStore
    from services.research_runner.runner import JobQueue
    from services.research_runner.run_kinds import RUN_KINDS
    from services.research_runner.factory_campaign import campaign_tick
    db=tmp_path/'data/cache/ai_factory/factory.db'
    with FactoryStore(db) as store:store.create_campaign(policy(),operator='test',now=0)
    calls=[]
    def worker(params):
        calls.append(params)
        with FactoryStore(db) as s:s.update(params['attempt'],'COMPLETED')
        return {'state':'COMPLETED','attempt':params['attempt'],'result':{}}
    monkeypatch.setitem(RUN_KINDS,'ai_factory_pipeline',replace(RUN_KINDS['ai_factory_pipeline'],runner=worker))
    registry=RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')));queue=JobQueue(registry,max_workers=1)
    original=FactoryStore.event
    crashed=[]
    def crash_after_enqueue(self,attempt,kind,payload):
        if kind=='OPERATOR_ACTION' and not crashed:
            crashed.append(True);raise RuntimeError('crash after queue commit')
        return original(self,attempt,kind,payload)
    monkeypatch.setattr(FactoryStore,'event',crash_after_enqueue)
    with pytest.raises(RuntimeError):campaign_tick(tmp_path,queue,registry,now=0)
    queue.join()
    campaign_tick(tmp_path,queue,registry,now=1)
    assert len(calls)==1
    with FactoryStore(db) as s:
        rounds=s.campaign_snapshot()['rounds']
        assert rounds[0]['state']=='COMPLETED'
        assert any(e['kind']=='OPERATOR_ACTION' and e['payload'].get('run_id') for e in s.events(1))
        s.control_campaign(1,'stop',operator='test')
    campaign_tick(tmp_path,queue,registry,now=999999999)
    assert len(calls)==1
