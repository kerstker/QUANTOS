import hashlib

from services.research.ai_factory_store import FactoryStore
from tests.research.test_gold_factory_campaign import policy


def finish(store,campaign_id,now):
    entry=store.get_collection(campaign_id);request=entry['request']
    config={**request['config'],'input_id':request['input_id'],
            'input_sha256':hashlib.sha256(str(request['input_id']).encode()).hexdigest()}
    store.create_campaign(config,operator='test',now=now,collection_hash=entry['request_hash'])
    row=store.reserve_campaign(campaign_id,now=now)
    store.record_trial(row['attempt'],0,{'candidate_id':0,'status':'PASSED'})
    store.update(row['attempt'],'COMPLETED')
    store.finish_campaign_round(campaign_id,0,'COMPLETED',now=now)
    store.reserve_campaign(campaign_id,now=now)


def test_budget_cooldown_and_reopened_store_keep_one_followup(tmp_path):
    path=tmp_path/'factory.db'
    with FactoryStore(path) as store:
        store.register_collection({**policy(b'base'),'max_cycles':1},input_id=2,seconds=30,operator='test',batches=2)
        assert store.advance_collection_program(1,now=0,input_id=3)['campaign_id']==1
        finish(store,1,100)
        assert store.advance_collection_program(1,now=3600099,input_id=3) is None
    with FactoryStore(path) as store:
        next_=store.advance_collection_program(1,now=3600100,input_id=3)
        assert next_['campaign_id']==2 and next_['request']['input_id']==3
        assert store.advance_collection_program(1,now=3600101,input_id=4)['campaign_id']==2
        finish(store,2,3600200)
        assert store.advance_collection_program(1,now=7200200,input_id=4) is None
        program=store.collection_programs_snapshot()[0]
        assert program['state']=='COMPLETED' and len(program['batches'])==2


def test_pause_and_stop_before_capture_completion_apply_to_late_campaign(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as store:
        store.register_collection({**policy(b'base'),'max_cycles':1},input_id=2,seconds=30,operator='test',batches=2)
        store.control_collection_program(1,'pause',operator='test')
        entry=store.get_collection(1)
        store.create_campaign({**entry['request']['config'],'input_id':2},operator='test',now=1,collection_hash=entry['request_hash'])
        assert store.campaign_snapshot()['campaigns'][0]['state']=='PAUSED'
        store.control_collection_program(1,'resume',operator='test')
        assert store.campaign_snapshot()['campaigns'][0]['state']=='RUNNING'
        store.control_collection_program(1,'stop',operator='test')
        assert store.campaign_snapshot()['campaigns'][0]['state']=='STOPPED'
        assert store.advance_collection_program(1,now=10**15,input_id=3) is None


def test_failed_collection_consumes_batch_and_restarts_after_cooldown(tmp_path, monkeypatch):
    from tests.research.test_gold_input_collection import setup
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.store import ResearchRunStore
    from services.research_runner.runner import JobQueue
    from services.research_runner.gold_collection_program import program_tick
    tool, base, calls = setup(tmp_path, monkeypatch)
    original = tool.collect_pair
    async def failed(*args):
        raise ValueError('disconnected')
    monkeypatch.setattr(tool, 'collect_pair', failed)
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue = JobQueue(registry)
    with FactoryStore(base/'factory.db') as store:
        store.register_collection({**policy((base/'gold-inputs/1.json').read_bytes()),'max_cycles':1},
                                  input_id=2,seconds=30,operator='test',batches=2)
    first = program_tick(tmp_path,queue,registry,now=1000)
    queue.join()
    run = registry.get(first['run_id'])
    assert run.status == 'error'
    finished = 2000
    assert program_tick(tmp_path,queue,registry,now=finished)['status']=='NO_COLLECTION_READY'
    assert program_tick(tmp_path,queue,registry,now=finished+3599999)['status']=='NO_COLLECTION_READY'
    monkeypatch.setattr(tool,'collect_pair',original)
    second = program_tick(tmp_path,queue,registry,now=finished+3600000)
    assert second['status']=='SUBMITTED_COLLECTION_BATCH'
    queue.join()
    assert registry.get(second['run_id']).status=='done'
    assert calls == [30]
    with FactoryStore(base/'factory.db') as store:
        program = store.collection_programs_snapshot()[0]
        assert [b['input_id'] for b in program['batches']] == [2,3]
        assert store.get_collection(1) is not None
    assert registry.get(first['run_id']).status=='error'


def test_all_failed_batches_exhaust_budget_and_cannot_resume(tmp_path):
    import pytest
    with FactoryStore(tmp_path/'factory.db') as store:
        store.register_collection({**policy(b'base'),'max_cycles':1},input_id=2,seconds=30,operator='test',batches=2)
        assert store.advance_collection_program(1,now=100,input_id=3,failed=True) is None
        assert store.advance_collection_program(1,now=3600100,input_id=3,failed=True)['campaign_id']==2
        assert store.advance_collection_program(1,now=3600200,input_id=4,failed=True) is None
        assert store.collection_programs_snapshot()[0]['state']=='EXHAUSTED'
        with pytest.raises(ValueError):store.control_collection_program(1,'resume',operator='test')
        assert len(store.read_collections(tmp_path/'factory.db'))==2


def test_cancelled_capture_stops_entire_program(tmp_path,monkeypatch):
    from tests.research.test_gold_input_collection import setup
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.store import ResearchRunStore
    from services.research_runner.runner import JobQueue
    from services.research_runner.gold_collection_program import program_tick
    tool,base,_=setup(tmp_path,monkeypatch)
    registry=RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')));queue=JobQueue(registry)
    original=tool.collect_pair
    async def cancel(*args):
        run=next(r for r in registry.list() if r.status=='running')
        queue.cancel(run.run_id)
        return await original(*args)
    monkeypatch.setattr(tool,'collect_pair',cancel)
    with FactoryStore(base/'factory.db') as store:
        store.register_collection(policy((base/'gold-inputs/1.json').read_bytes()),input_id=2,seconds=30,operator='test',batches=2)
    run=program_tick(tmp_path,queue,registry,now=1000)
    queue.join()
    assert registry.get(run['run_id']).status=='cancelled'
    program_tick(tmp_path,queue,registry,now=10**15)
    with FactoryStore(base/'factory.db') as store:
        assert store.collection_programs_snapshot()[0]['state']=='STOPPED'
        assert store.campaign_snapshot()['campaigns'][0]['state']=='STOPPED'
        assert len(store.read_collections(base/'factory.db'))==1
