import pytest
from fastapi.testclient import TestClient
from services.research_runner import main
from services.research_runner.runner import JobQueue
from services.research.run_registry.registry import RunRegistry
from services.research.run_registry.store import ResearchRunStore
from services.research.ai_factory_store import FactoryStore
from tests.research.test_gold_input_collection import setup
from tests.research.test_gold_factory_campaign import policy


@pytest.mark.parametrize('damage_memory',[False,True])
@pytest.mark.parametrize('decision_policy',[None,'FIRST_RECEIVED_WATERMARK_V1','AI_DESIGNED_WATERMARK_V1','AI_DESIGNED_ZONE_V1'])
def test_one_api_request_collects_then_registers_original_campaign(tmp_path, monkeypatch, damage_memory, decision_policy, damage_design=False):
    _, base, calls = setup(tmp_path, monkeypatch)
    tick_start=10**15
    second_tick=None
    design_calls=[]
    if decision_policy in ('AI_DESIGNED_WATERMARK_V1','AI_DESIGNED_ZONE_V1'):
        from tools import gold_input_collection
        from tools import gold_collection_campaign
        from tools.gold_capture_design import propose_design
        from tests.research.test_ai_factory_provider import LOCAL, Transport
        from tests.execution.test_perpetual_public_replay import START, DAY, sealed
        from types import SimpleNamespace
        collection_now=[START+DAY]
        monkeypatch.setattr(gold_collection_campaign,'time',SimpleNamespace(time_ns=lambda:collection_now[0]*1000000))
        if decision_policy=='AI_DESIGNED_ZONE_V1':
            from services.research import ai_factory_store
            monkeypatch.setattr(ai_factory_store,'now_ms',lambda:START+3000)
            tick_start=START+5000
            second_tick=START+2*DAY+5000
            collection_now[0]=START+3000
        original_collect=gold_input_collection.collect_pair
        async def later_capture(*args):
            from copy import deepcopy
            from tools.gold_public_capture import PublicCapture
            import json
            pair=deepcopy(await original_collect(*args))
            if len(calls)>1:
                def shift(value):
                    if type(value) is int and value>=START-31*DAY:return value+2*DAY
                    if type(value) is list:return [shift(v) for v in value]
                    if type(value) is dict:return {k:shift(v) for k,v in value.items()}
                    return value
                pair['quotes']=sealed(shift(pair['quotes']))
                old=pair['trades'];capture=PublicCapture(started_at_ms=old['started_at_ms']+2*DAY)
                for row in old['packets']:
                    payload=dict(row['payload']);payload['T']+=2*DAY;payload['E']+=2*DAY
                    capture.ingest(json.dumps({'stream':'xauusdt@aggTrade','data':payload}),received_at_ms=row['received_at_ms']+2*DAY)
                pair['trades']=capture.finish(ended_at_ms=old['ended_at_ms']+2*DAY,reason=old['stop_reason'])
            return pair
        monkeypatch.setattr(gold_input_collection,'collect_pair',later_capture)
        def design(**kwargs):
            design_calls.append(1)
            assert len(calls) in (0,1)
            if calls:
                memory=kwargs.get('capture_memory')
                assert memory is not None
                assert memory['records'][0]['input_id']==2
                assert memory['records'][0]['capture_status']=='CAPTURED_PUBLIC_PREFIX'
                assert memory['records'][0]['unique_aggregates']>0
                assert 'prices' not in memory['records'][0]
                if decision_policy=='AI_DESIGNED_ZONE_V1':
                    research=kwargs.get('research_memory')
                    assert research is not None
                    assert research['records'][0]['attempt']==1
                    assert 'signal_diagnostics' in research['records'][0]
            output={'window_ms':1000,'max_age_ms':100,'window_count':2}
            if decision_policy=='AI_DESIGNED_ZONE_V1':
                output.update(zone_lower_bps=-50,zone_upper_bps=0,stop_bps=-100,target_bps=200)
            receipt=propose_design(**kwargs,environ=LOCAL,transport=Transport(output))
            from quantos.local_artifacts import with_result_hash
            receipt['received_at_ms']=kwargs.get('capture_memory',{}).get('as_of_ms',1)
            return with_result_hash({k:v for k,v in receipt.items() if k!='result_hash'})
        monkeypatch.setattr(gold_input_collection,'propose_design',design)
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue = JobQueue(registry)
    monkeypatch.setattr(main, '_STATE_ROOT', tmp_path)
    monkeypatch.setattr(main, '_queue', queue)
    body = dict(policy((base/'gold-inputs/1.json').read_bytes()),action='collect_create',collection_input_id=2,collection_seconds=30)
    body.update(collection_batches=2,max_cycles=1)
    if decision_policy:body['collection_decision_policy']=decision_policy
    client = TestClient(main.app)
    headers = {'X-Runner-Token':main._RUNNER_TOKEN,'X-Operator':'test'}
    response = client.post('/factory-campaign',json=body,headers=headers)
    assert response.status_code == 200, response.text
    queue.join()
    run_id = response.json()['run_id']
    assert registry.get(run_id).status == 'done', registry.get(run_id).error
    import json
    recorded=json.loads((base/'gold-collections/2/request.json').read_bytes())
    assert recorded['policy']==(decision_policy or 'FIXED_TEMPLATE_RETROSPECTIVE_WINDOW_V1')
    if decision_policy in ('AI_DESIGNED_WATERMARK_V1','AI_DESIGNED_ZONE_V1'):
        metadata=json.loads((base/'gold-inputs/2.metadata.json').read_bytes())
        assert 'design.json' in metadata['source_sha256']
    with FactoryStore(base/'factory.db') as store:
        snapshot = store.campaign_snapshot()
        assert snapshot['campaigns'][0]['config']['input_id'] == 2
        assert snapshot['campaigns'][0]['state'] == 'RUNNING'
    assert client.post('/factory-campaign',json=body,headers=headers).json()['run_id'] == run_id
    assert calls == [30]
    changed = client.post('/factory-campaign',json={**body,'collection_seconds':60},headers=headers)
    assert changed.status_code == 409

    from tools import gold_ai_research, gold_cost_research
    from tools.gold_cost_inputs import load_inputs
    from tools.ai_factory_provider import propose
    from tests.research.test_ai_factory_provider import LOCAL, Transport
    from services.research_runner.factory_campaign import campaign_tick
    from services.console.api.ai_factory_lifecycle import lifecycle_payload
    release=load_inputs((base/'gold-inputs/2.json').read_bytes())['release_identity']
    monkeypatch.setattr(gold_ai_research,'source_identity',lambda:release)
    monkeypatch.setattr(gold_cost_research,'source_identity',lambda:release)
    memories=[]
    def choose(objective,remaining,history,**kwargs):
        memories.append(kwargs.get('research_memory'))
        return propose(objective,remaining,history,**kwargs,environ=LOCAL,
                       transport=Transport({'candidate_id':remaining[0],'reason':'one-request workflow'}))
    monkeypatch.setattr(gold_ai_research,'propose',choose)
    assert campaign_tick(tmp_path,queue,registry,now=tick_start)['status']=='SUBMITTED'
    queue.join()
    campaign_tick(tmp_path,queue,registry,now=tick_start+1)
    assert lifecycle_payload(tmp_path)['gold_results'][0]['report_status']=='AVAILABLE'
    from services.research_runner.gold_collection_program import program_tick
    campaign_tick(tmp_path,queue,registry,now=tick_start+2)
    assert program_tick(tmp_path,queue,registry,now=tick_start+3599999)['status']=='NO_COLLECTION_READY'
    if decision_policy=='AI_DESIGNED_ZONE_V1':collection_now[0]=START+DAY
    if damage_design:(base/'gold/results/1/trial/report.json').write_text('{}')
    followup=program_tick(tmp_path,queue,registry,now=tick_start+3600002)
    assert followup['status']=='SUBMITTED_COLLECTION_BATCH'
    queue.join()
    if damage_design:
        assert registry.get(followup['run_id']).status=='error'
        assert calls==[30] and design_calls==[1]
        assert not (base/'gold-collections/3/design-started.json').exists()
        return
    assert registry.get(followup['run_id']).status=='done', registry.get(followup['run_id']).error
    assert json.loads((base/'gold-collections/3/request.json').read_bytes())['policy']==recorded['policy']
    original_report=(base/'gold/results/1/trial/report.json').read_bytes()
    if damage_memory:(base/'gold/results/1/trial/report.json').write_text('{}')
    second_tick=second_tick or tick_start+3600003
    submitted=campaign_tick(tmp_path,queue,registry,now=second_tick)
    assert submitted['status']=='SUBMITTED'
    queue.join()
    if damage_memory:
        assert registry.get(submitted['run_id']).status=='error'
        assert len(memories)==1
        assert not (base/'gold/results/2').exists()
        return
    assert (base/'gold/results/1/trial/report.json').read_bytes()==original_report
    campaign_tick(tmp_path,queue,registry,now=second_tick+1)
    campaign_tick(tmp_path,queue,registry,now=second_tick+2)
    program_tick(tmp_path,queue,registry,now=second_tick+3)
    view=lifecycle_payload(tmp_path)
    assert len(view['gold_results'])==2
    assert all(r['report_status']=='AVAILABLE' for r in view['gold_results'])
    assert view['collection_programs'][0]['state']=='COMPLETED'
    assert calls==[30,30]
    assert memories[0] is None
    assert memories[1]['records'][0]['attempt']==1
    assert memories[1]['records'][0]['outcome'] in ('NO_SIGNAL','OPEN_AT_END','FLAT_TERMINAL')
    assert memories[1]['usage']=='TECHNICAL_DIAGNOSTIC_NOTES'
    import json
    retained=json.loads((base/'gold/results/2/request.json').read_text())
    assert retained['research_memory']==memories[1]
    assert json.loads((base/'gold/results/2/trial/research-origin.json').read_text())['schema']=='GOLD_AI_COST_ORIGIN_V2'
    from tools.gold_execution_identity import from_archive
    assert from_archive(base/'gold/results/2/trial')['attempt']==2
    from tools.gold_factory_research import recover_session
    assert recover_session({'attempt':2})['candidate_id']==view['gold_results'][0]['candidate']['id']
    assert len(memories)==2
    assert json.loads((base/'gold/results/2/request.json').read_text())==retained
    if decision_policy=='AI_DESIGNED_ZONE_V1':
        from tools.gold_research_memory import collection_strategy_memory
        with FactoryStore(base/'factory.db') as store:
            first_entry=store.get_collection(1)
            second_entry=store.get_collection(2)
        assert collection_strategy_memory(base,first_entry,now=second_tick+10) is None
        frozen=json.loads((base/'gold-collections/3/research-memory.json').read_bytes())
        assert collection_strategy_memory(base,second_entry,now=second_tick+10)==frozen
        assert [row['attempt'] for row in frozen['records']]==[1]


def test_changed_prior_report_blocks_next_price_design_and_capture(tmp_path,monkeypatch):
    test_one_api_request_collects_then_registers_original_campaign(
        tmp_path,monkeypatch,False,'AI_DESIGNED_ZONE_V1',damage_design=True)




@pytest.mark.parametrize('error,reason', [('GOLD_SIGNAL_INPUT_INCOMPLETE','GOLD_SIGNAL_INPUT_INCOMPLETE'),
                                       ('private diagnostic detail','COLLECTION_FAILED')])
def test_failed_capture_keeps_request_without_starting_research(tmp_path,monkeypatch,error,reason):
    tool,base,calls=setup(tmp_path,monkeypatch)
    async def failed(*args):
        raise ValueError(error)
    monkeypatch.setattr(tool,'collect_pair',failed)
    registry=RunRegistry(ResearchRunStore(str(tmp_path/'data/cache/research_runs.db')));queue=JobQueue(registry)
    monkeypatch.setattr(main,'_STATE_ROOT',tmp_path);monkeypatch.setattr(main,'_queue',queue)
    response=TestClient(main.app).post('/factory-campaign',
        json=dict(policy((base/'gold-inputs/1.json').read_bytes()),action='collect_create',collection_input_id=2,collection_seconds=30),
        headers={'X-Runner-Token':main._RUNNER_TOKEN,'X-Operator':'test'})
    queue.join()
    assert registry.get(response.json()['run_id']).status=='error'
    assert FactoryStore.read_campaigns(base/'factory.db')['campaigns']==[]
    assert len(FactoryStore.read_collections(base/'factory.db'))==1
    from services.console.api.ai_factory_lifecycle import lifecycle_payload
    observation=lifecycle_payload(tmp_path)['collection_requests'][0]['observation']
    assert observation['status']=='FAILED'
    assert observation['reason']==reason
    assert observation['run_id']==response.json()['run_id']
    assert observation['research_created'] is False
    with registry._s._c:
        registry._s._c.execute('UPDATE runs SET identity_hash=? WHERE run_id=?',
                              ('0'*64, response.json()['run_id']))
    corrupted=lifecycle_payload(tmp_path)['collection_requests'][0]['observation']
    assert corrupted['status']=='UNKNOWN' and corrupted['run_id'] is None


def test_soft_cancelled_collection_cannot_release_research(tmp_path,monkeypatch):
    tool,base,_=setup(tmp_path,monkeypatch)
    registry=RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')));queue=JobQueue(registry)
    monkeypatch.setattr(main,'_STATE_ROOT',tmp_path);monkeypatch.setattr(main,'_queue',queue)
    original=tool.collect_pair
    async def cancel_during_capture(*args):
        running=next(r for r in registry.list() if r.status=='running')
        queue.cancel(running.run_id)
        return await original(*args)
    monkeypatch.setattr(tool,'collect_pair',cancel_during_capture)
    response=TestClient(main.app).post('/factory-campaign',
        json=dict(policy((base/'gold-inputs/1.json').read_bytes()),action='collect_create',collection_input_id=2,collection_seconds=30),
        headers={'X-Runner-Token':main._RUNNER_TOKEN,'X-Operator':'test'})
    queue.join()
    assert registry.get(response.json()['run_id']).status=='cancelled'
    from services.research_runner.factory_campaign import campaign_tick
    assert campaign_tick(tmp_path,queue,registry,now=10**15)['status']!='SUBMITTED'
    assert FactoryStore.read_campaigns(base/'factory.db')['rounds']==[]
