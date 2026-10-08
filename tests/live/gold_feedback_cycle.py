"""Isolated real research queues around synthetic execution integration tests."""
import json
import os
from types import SimpleNamespace

from services.research.ai_factory_store import FactoryStore
from services.research_runner.factory_campaign import campaign_tick
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research_runner.runner import JobQueue
from tests.research.test_gold_factory_campaign import policy
from tests.research.test_gold_ai_research import setup
from tests.research.test_ai_factory_provider import LOCAL,Transport
from tools.ai_factory_provider import propose
from tools.gold_execution_identity import from_archive


def start(root,monkeypatch,*,automatic=False):
    tool,_,_=setup(root,monkeypatch)
    raw=(root/'data/cache/gold_ai_research/requests/1/input.json').read_bytes()
    base=root/'data/cache/ai_factory';(base/'gold-inputs').mkdir(parents=True)
    (base/'gold-inputs/1.json').write_bytes(raw)
    choices=[]
    def select(objective,remaining,history,**kwargs):
        assert history==[] and 'execution_feedback' not in kwargs
        choices.append(remaining[0])
        return propose(objective,remaining,history,**kwargs,environ=LOCAL,
            transport=Transport({'candidate_id':remaining[0],'reason':'synthetic roundtrip review'}))
    monkeypatch.setattr(tool,'propose',select)
    config=dict(policy(raw),max_cycles=2 if automatic else 1)
    if automatic:config['execution_feedback_mode']='after_first_round'
    with FactoryStore(base/'factory.db') as store:store.create_campaign(config,operator='test',now=0)
    runs=RunRegistry(ResearchRunStore(str(root/'runs.db')));queue=JobQueue(runs)
    assert campaign_tick(root,queue,runs,now=0)['status']=='SUBMITTED'
    queue.join();campaign_tick(root,queue,runs,now=1)
    identity=from_archive(base/'gold/results/1/trial')
    assert choices==[0]
    return dict(root=root,base=base,config=config,runs=runs,queue=queue,identity=identity,choices=choices,raw=raw)


def resume(state,feedback,monkeypatch):
    from tools import gold_factory_research,gold_cost_research
    root=state['root'];base=state['base'];at=feedback['available_at_ms']+1
    from tools.demo_feedback_publish import validate_export,publish
    validate_export(feedback,source_commit='a'*40,feedback_sha256=feedback['result_hash'],now=at)
    # Isolated research tree custody, as the deployed research UID would own it.
    for directory in (root,root/'data',root/'data/cache',base):directory.chmod(0o700)
    with monkeypatch.context() as research_uid:
        research_uid.setattr(os,'geteuid',os.getuid)
        publish(root,feedback);publish(root,feedback)
    raw=(base/'execution-feedback'/(feedback['result_hash']+'.json')).read_bytes()
    automatic=state['config'].get('execution_feedback_mode')=='after_first_round'
    if not automatic:
        with FactoryStore(base/'factory.db') as store:
            store.create_campaign(dict(state['config'],campaign_id=2,
                execution_feedback_sha256=feedback['result_hash']),operator='test',now=at)
    for module in (gold_factory_research,gold_cost_research):
        monkeypatch.setattr(module,'time',SimpleNamespace(time_ns=lambda:at*1000000))
    result=campaign_tick(root,state['queue'],state['runs'],now=at)
    assert result['status']=='SUBMITTED' and result['attempt']==2
    state['queue'].join();campaign_tick(root,state['queue'],state['runs'],now=at+1)
    assert state['choices']==[0,1]
    with FactoryStore(base/'factory.db') as store:
        store.verify_campaigns()
        if automatic:
            snapshot=store.campaign_snapshot()
            assert len(snapshot['campaigns'])==1
            assert len(snapshot['rounds'])==2
            assert snapshot['rounds'][0]['trigger_key']==feedback['result_hash']
        trial=store.trials(2)[0]
        assert store.get(2)['state']=='COMPLETED' and trial['trial_id']!=state['identity']['trial_id']
        assert any(e['kind']=='EXECUTION_REVIEW_INPUT' and e['payload']['feedback_sha256']==feedback['result_hash']
            for e in store.events(2))
    assert (base/'gold/requests/2/execution-feedback.json').read_bytes()==raw
    assert (base/'gold-inputs/1.json').read_bytes()==state['raw']
    assert json.loads((base/'gold/results/2/trial/manifest.json').read_bytes())['trial_id']==trial['trial_id']
