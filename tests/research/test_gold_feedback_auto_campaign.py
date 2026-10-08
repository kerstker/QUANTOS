"""One enrolled campaign continues only after its preceding execution review."""
import pytest
from copy import deepcopy

from services.research.ai_factory_store import FactoryStore
from services.research.demo_oms_status import resolution_digest as digest
from tests.research.test_gold_factory_campaign import policy
from tests.research.test_gold_feedback_trigger import feedback


def completed_feedback():
    value=feedback()
    value.update(schema='DEMO_ROUNDTRIP_RESEARCH_FEEDBACK_V1',position_flat=True,positions={},wallet_residual='0',
        batches=[{k:str(i)*64 for k in ('reservation_sha256','proposal_sha256','resolution_plan_sha256','manifest_hash','journal_tail')} for i in (1,2)])
    value['result_hash']=digest({k:v for k,v in value.items() if k!='result_hash'})
    return value


def test_same_campaign_waits_for_feedback_and_keeps_original_budget(tmp_path):
    value=completed_feedback();config=dict(policy(b'base'),input_sha256='c'*64,execution_feedback_mode='after_first_round')
    with FactoryStore(tmp_path/'factory.db') as store:
        store.create_campaign(config,operator='test',now=0)
        first=store.reserve_campaign(1,now=0)
        store.record_trial(first['attempt'],0,{'candidate_id':0,'status':'PASSED','trial_id':'a'*64})
        store.update(first['attempt'],'COMPLETED');store.finish_campaign_round(1,0,'COMPLETED',now=100)
        assert store.reserve_campaign(1,now=3600100) is None
        assert store.reserve_campaign(1,now=200,execution_feedback=value) is None
        before=store.campaign_snapshot()
        for damage in ('missing_hash','preceding_round','single_batch'):
            bad=deepcopy(value)
            if damage=='missing_hash':bad.pop('result_hash')
            elif damage=='preceding_round':bad['research_identity']['attempt']=2
            else:bad['schema']='DEMO_EXECUTION_RESEARCH_FEEDBACK_V1'
            with pytest.raises(ValueError):store.reserve_campaign(1,now=3600100,execution_feedback=bad)
            assert store.campaign_snapshot()==before
        store.control_campaign(1,'pause',operator='test')
        assert store.reserve_campaign(1,now=3600100,execution_feedback=value) is None
        store.control_campaign(1,'resume',operator='test')
        second=store.reserve_campaign(1,now=3600100,execution_feedback=value)
        assert second['attempt']==2 and second['trigger_key']==value['result_hash']
        session=store.get(2)['config']
        assert session['execution_feedback_sha256']==value['result_hash'] and 0 not in session['remaining_candidates']
        assert store.reserve_campaign(1,now=3600101,execution_feedback=value)==second
        store.record_trial(2,0,{'candidate_id':1,'status':'FAILED'});store.finish_campaign_round(1,1,'FAILED',now=3600200)
        assert store.reserve_campaign(1,now=7200200,execution_feedback=value) is None
        assert len(store.campaign_snapshot()['campaigns'])==1
        store.verify_campaigns()


@pytest.mark.parametrize('kind',['fifo','symlink','hardlink','oversize'])
def test_scheduler_feedback_reader_refuses_nonregular_or_unbounded_files(tmp_path,kind):
    import os
    from services.research_runner.factory_campaign import _feedback
    directory=tmp_path/'data/cache/ai_factory/execution-feedback';directory.mkdir(parents=True)
    path=directory/('a'*64+'.json');other=tmp_path/'outside';other.write_text('{}')
    if kind=='fifo':os.mkfifo(path)
    elif kind=='symlink':path.symlink_to(other)
    elif kind=='hardlink':os.link(other,path)
    else:
        with path.open('wb') as handle:handle.truncate(2000001)
    with pytest.raises(ValueError):_feedback(tmp_path,'a'*64)


@pytest.mark.parametrize('change',[{'execution_feedback_mode':'always'},
    {'execution_feedback_mode':True},
    {'execution_feedback_mode':'after_first_round','execution_feedback_sha256':'a'*64}])
def test_automatic_mode_cannot_mix_or_replace_fixed_feedback_policy(change):
    from services.research.gold_factory_campaign import validate_campaign
    with pytest.raises(ValueError):validate_campaign(dict(policy(b'base'),**change))
