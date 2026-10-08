"""Real Runner jobs consume pinned review triggers without cash model labels."""
import json

import pytest

from services.research.ai_factory_store import FactoryStore
from services.research.demo_oms_status import resolution_digest as digest
from services.research_runner.factory_campaign import campaign_tick
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research_runner.runner import JobQueue
from tests.research.test_gold_factory_campaign import policy
from tests.tools.test_gold_cost_replay import inputs
from tests.research.test_ai_factory_provider import LOCAL,Transport
from tests.research.test_gold_feedback_trigger import feedback
from tools.gold_cost_inputs import dump_inputs
from tools.gold_execution_identity import from_archive
from tools.ai_factory_provider import propose


@pytest.mark.parametrize('damage',[None,'feedback','research'])
def test_feedback_real_queue_retains_trigger_and_rejects_changed_source(tmp_path,monkeypatch,damage):
    from tools import gold_ai_research,gold_cost_research
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    args=inputs();raw=dump_inputs(args)
    monkeypatch.setattr(gold_ai_research,'source_identity',lambda:args['release_identity'])
    monkeypatch.setattr(gold_cost_research,'source_identity',lambda:args['release_identity'])
    choices=[];memories=[]
    def proposal(objective,remaining,history,**kwargs):
        assert history==[] and 'execution_feedback' not in kwargs
        choices.append(remaining[0])
        memories.append(kwargs.get('research_memory'))
        return propose(objective,remaining,history,**kwargs,environ=LOCAL,
                       transport=Transport({'candidate_id':remaining[0],'reason':'bounded synthetic choice'}))
    monkeypatch.setattr(gold_ai_research,'propose',proposal)
    base=tmp_path/'data/cache/ai_factory';(base/'gold-inputs').mkdir(parents=True)
    (base/'gold-inputs/1.json').write_bytes(raw)
    first_policy=dict(policy(raw),max_cycles=1)
    with FactoryStore(base/'factory.db') as store:store.create_campaign(first_policy,operator='test',now=0)
    registry=RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')));queue=JobQueue(registry)
    assert campaign_tick(tmp_path,queue,registry,now=0)['status']=='SUBMITTED'
    queue.join();campaign_tick(tmp_path,queue,registry,now=1)
    identity=from_archive(base/'gold/results/1/trial')
    value=feedback();value.update(research_identity=identity,strategy_id=identity['strategy_id'],
                                 available_at_ms=identity['available_at_ms'])
    value['result_hash']=digest({k:v for k,v in value.items() if k!='result_hash'})
    directory=base/'execution-feedback';directory.mkdir()
    path=directory/(value['result_hash']+'.json');path.write_text(json.dumps(value))
    body=path.read_bytes()
    with FactoryStore(base/'factory.db') as store:
        at=max(identity['available_at_ms'],store.get(1)['updated'])+1
        store.create_campaign(dict(first_policy,campaign_id=2,execution_feedback_sha256=value['result_hash']),operator='test',now=at)
    if damage:
        # Persist the legitimate outbox reservation, then change the supplied file
        # before the actual worker reads it. No model should be invoked again.
        with FactoryStore(base/'factory.db') as store:
            row=store.reserve_campaign(2,now=at,execution_feedback=value)
            assert row['attempt']==2
        if damage=='feedback':
            path.write_text('{}')
        else:
            (base/'gold/results/1/trial/report.json').write_text('{}')
        queue.submit('gold_factory_research',{'attempt':2},at//1000,invocation_id='changed-feedback')
        queue.join()
        assert choices==[0]
        assert registry.list()[0].status=='error'
        assert not (base/'gold/results/2').exists()
    else:
        result=campaign_tick(tmp_path,queue,registry,now=at)
        assert result['status']=='SUBMITTED' and result['attempt']==2
        queue.join();campaign_tick(tmp_path,queue,registry,now=at+1)
        assert choices==[0,1]
        assert memories[0] is None
        assert memories[1] is not None
        assert memories[1]['records'][0]['attempt']==1
        assert memories[1]['records'][0]['trial_id']==identity['trial_id']
        assert memories[1]['records'][0]['report_sha256']==identity['report_sha256']
        assert 'strategy_pnl' not in memories[1]['records'][0]
        with FactoryStore(base/'factory.db') as store:
            store.verify_campaigns()
            original=store.trials(1)[0];fresh=store.trials(2)[0]
            assert original['trial_id']==identity['trial_id'] and fresh['trial_id']!=original['trial_id']
            assert store.get(2)['state']=='COMPLETED'
        assert (base/'gold/requests/2/execution-feedback.json').read_bytes()==body
        assert json.loads((base/'gold/results/2/trial/manifest.json').read_bytes())['trial_id']==fresh['trial_id']
        assert all(run.verdict=='TECHNICAL_VALIDATION_ONLY' for run in registry.list())
        from services.console.api.ai_factory_lifecycle import lifecycle_payload
        rows=lifecycle_payload(tmp_path)['gold_results']
        view=next(r for r in rows if r['attempt']==1)['execution_review']
        assert view['status']=='AVAILABLE'
        assert view['records'][0]['feedback_sha256']==value['result_hash']
        assert view['records'][0]['next_attempts']==[{'attempt':2,'state':'COMPLETED'}]
        assert view['records'][0]['source_verified'] is False
        path.write_text('{}')
        assert next(r for r in lifecycle_payload(tmp_path)['gold_results'] if r['attempt']==1)['execution_review']['status']=='UNAVAILABLE'

    assert (base/'gold-inputs/1.json').read_bytes()==raw
