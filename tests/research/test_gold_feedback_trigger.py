"""Feedback can request bounded research, never supply labels or permission."""
import pytest
from services.research.demo_oms_status import resolution_digest as digest
from services.research.gold_execution_identity import identify,SCHEMA


def feedback():
    identity=identify({'schema':SCHEMA,'attempt':1,'candidate_id':0,'trial_id':'a'*64,
        'definition_id':'b'*64,'base_input_sha256':'c'*64,'input_sha256':'d'*64,
        'report_sha256':'e'*64,'research_origin_sha256':'f'*64,'manifest_sha256':'1'*64,
        'review_request_sha256':'2'*64,'available_at_ms':100,
        'scope':'TECHNICAL_RESEARCH_IDENTITY_ONLY','economic_eligible':False,'submission_enabled':False})
    value={'schema':'DEMO_EXECUTION_RESEARCH_FEEDBACK_V1','environment':'BINANCE_USDM_DEMO',
        'scope':'TECHNICAL_REVIEW_ONLY','symbol':'XAUUSDT','strategy_id':identity['strategy_id'],
        'research_identity':identity,'candidate_attribution':'CONTENT_BOUND_TO_SIGNED_STRATEGY',
        'available_at_ms':200,'research_trigger_enabled':False,'source_verified':False,
        'coverage_verified':False,'economic_eligible':False,'submission_enabled':False,'strategy_pnl':None}
    return value|{'result_hash':digest(value)}


def test_feedback_ready_only_after_pinned_same_input_material_available():
    from services.research.gold_execution_feedback import research_trigger
    value=feedback();config={'input_sha256':'c'*64,'execution_feedback_sha256':value['result_hash']}
    assert not research_trigger(config,value,now=199)['ready']
    result=research_trigger(config,value,now=200)
    assert result=={'ready':True,'reason':'EXECUTION_REVIEW_AVAILABLE','key':value['result_hash']}
    for changed in (dict(config,input_sha256='d'*64),dict(config,execution_feedback_sha256='a'*64)):
        with pytest.raises(ValueError):research_trigger(changed,value,now=200)
    forged=dict(value,strategy_pnl='100')
    forged['result_hash']=digest({k:v for k,v in forged.items() if k!='result_hash'})
    with pytest.raises(ValueError):research_trigger(dict(config,execution_feedback_sha256=forged['result_hash']),forged,now=200)


def test_feedback_consumed_once_across_campaigns_without_resetting_budget(tmp_path):
    from services.research.ai_factory_store import FactoryStore
    from tests.research.test_gold_factory_campaign import policy
    value=feedback();base=policy(b'base');base['input_sha256']='c'*64
    with FactoryStore(tmp_path/'factory.db') as store:
        store.create_campaign(base,operator='test',now=0)
        first=store.reserve_campaign(1,now=0)
        assert first['attempt']==1
        store.record_trial(1,0,{'candidate_id':0,'status':'PASSED','trial_id':'a'*64})
        store.update(1,'COMPLETED')
        store.finish_campaign_round(1,0,'COMPLETED',now=100)
        config=dict(base,campaign_id=2,execution_feedback_sha256=value['result_hash'])
        store.create_campaign(config,operator='test',now=200)
        assert store.reserve_campaign(2,now=3600100) is None
        assert store.reserve_campaign(2,now=201,execution_feedback=value) is None
        second=store.reserve_campaign(2,now=3600100,execution_feedback=value)
        assert second['trigger_key']==value['result_hash']
        assert 0 not in store.get(second['attempt'])['config']['remaining_candidates']
        store.record_trial(second['attempt'],0,{'candidate_id':1,'status':'FAILED'})
        store.finish_campaign_round(2,0,'FAILED',now=3600200)
        store.create_campaign(dict(config,campaign_id=3),operator='test',now=3600300)
        assert store.reserve_campaign(3,now=7200200,execution_feedback=value) is None
        store.verify_campaigns()


def test_scheduler_reads_pinned_feedback_and_submits_original_gold_job(tmp_path):
    import json
    from services.research.ai_factory_store import FactoryStore
    from services.research_runner.factory_campaign import campaign_tick
    from tests.research.test_gold_factory_campaign import policy
    value=feedback();base=policy(b'base');base.update(input_sha256='c'*64,max_cycles=1)
    root=tmp_path/'data/cache/ai_factory'
    with FactoryStore(root/'factory.db') as store:
        store.create_campaign(base,operator='test',now=0)
        store.reserve_campaign(1,now=0)
        store.record_trial(1,0,{'candidate_id':0,'status':'PASSED','trial_id':'a'*64})
        store.update(1,'COMPLETED');store.finish_campaign_round(1,0,'COMPLETED',now=100)
        store.create_campaign(dict(base,campaign_id=2,execution_feedback_sha256=value['result_hash']),operator='test',now=200)
    class Registry:
        def get_by_invocation(self,key):return None
        def list(self,**kwargs):return []
    class Queue:
        def __init__(self):self.calls=[]
        def submit(self,*args,**kwargs):self.calls.append((args,kwargs));return 'isolated-job'
    queue=Queue();registry=Registry()
    campaign_tick(tmp_path,queue,registry,now=3600100)
    assert queue.calls==[]
    directory=root/'execution-feedback';directory.mkdir()
    (directory/(value['result_hash']+'.json')).write_text(json.dumps(value))
    result=campaign_tick(tmp_path,queue,registry,now=3600100)
    assert result['status']=='SUBMITTED' and result['campaign_id']==2
    assert queue.calls[0][0]==('gold_factory_research',{'attempt':2})
    with FactoryStore(root/'factory.db') as store:
        assert store.get(2)['config']['execution_feedback_sha256']==value['result_hash']
        store.verify_campaigns()


def test_execution_feedback_digest_does_not_accept_research_store_encoding():
    from services.research.ai_factory_store import digest as store_digest
    from services.research.gold_execution_feedback import research_trigger
    value=feedback();body={k:v for k,v in value.items() if k!='result_hash'}
    assert store_digest(body)!=value['result_hash']
    value['result_hash']=store_digest(body)
    with pytest.raises(ValueError,match='SCOPE_OR_DIGEST'):
        research_trigger({'input_sha256':'c'*64,'execution_feedback_sha256':value['result_hash']},value,now=200)
