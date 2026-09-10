"""Frozen component reuse must preserve chronology, failed groups and trial identity."""
import json
import numpy as np
import pytest

from services.research.ai_factory import HOUR, candidates
from services.research.ai_factory_store import digest
from services.research.ai_factory_walkforward import folds
from tools.ai_factory_portfolio import run_study
from tools.btc_factory_research import verified, seal
from tools.profitability_study import write_new


def materials(tmp_path):
    upstream=tmp_path/'upstream';upstream.mkdir()
    t=np.arange(600,dtype=np.int64)*HOUR
    x=np.random.default_rng(3).normal(size=(600,5));y=x[:,0]*.001
    panel=t,x,y
    identity={'groups':['baseline','random'],'folds':3,'source':{'commit':'a'*40},'input_hash':'b'*64}
    write_new(upstream/'comparison-plan.json',identity)
    for fold,split in enumerate(folds(t,t+4*HOUR+300000)):
        for group in identity['groups']:
            run_study(upstream/'portfolios'/f'{fold}-{group}',
                {'comparison':identity,'fold':fold,'group':group},candidates()[:2],panel,
                lambda *a:{'net_pnl':0.,'drawdown':0.,'fills':0},split=split,
                fit=lambda s,x,y,z:np.full(len(z),.02 if s['id']==0 else -.03))
    return upstream,panel


def test_reuse_reads_outer_only_after_selection_and_never_fits(tmp_path,monkeypatch):
    from tools import ai_factory_sleeve_process as module
    upstream,panel=materials(tmp_path);output=tmp_path/'new';calls=[]
    original=module.verified
    def read(path):
        if path.name=='outer-predictions.json' and upstream in path.parents:
            assert (output/'portfolios'/path.parent.name/'selection.json').exists()
        return original(path)
    monkeypatch.setattr(module,'verified',read)
    def replay(t,p,fee,slip):
        calls.append(1)
        assert np.array_equal(np.asarray(p['predictions'])[:,0],[.02,-.03])
        return {'net_pnl':0.,'drawdown':0.,'fills':0}
    result=module.run_cached_comparison(output,upstream,panel,replay,{'attempt':1,'source':{'fixture':True}})
    assert len(result['rows'])==6 and len(calls)==72
    assert result['new_model_calls']==0 and result['refitted_models']==0
    assert all(r['status']=='COMPLETED' and r['portfolio']['inner_trials_attempted']==6 for r in result['rows'])
    assert result['outer_feedback_allowed'] is False and result['economic_eligible'] is False
    cache=verified(output/'portfolios/0-baseline/outer-predictions.json')
    assert cache['reuse']['source_prediction_hash']==verified(upstream/'portfolios/0-baseline/outer-predictions.json')['result_hash']
    assert module.run_cached_comparison(output,upstream,panel,replay,{'attempt':1,'source':{'fixture':True}})==result
    assert len(calls)==72
    # Resealing a hidden cached value cannot bypass the complete result binding.
    cache['predictions'][0][0]=99
    (output/'portfolios/0-baseline/outer-predictions.json').write_text(json.dumps(seal({k:v for k,v in cache.items() if k!='result_hash'})))
    with pytest.raises(ValueError,match='prediction|cache|binding'):
        module.verify_comparison(output)


def test_missing_outer_keeps_inner_selection_and_failed_group_without_retry(tmp_path):
    from tools import ai_factory_sleeve_process as module
    upstream,panel=materials(tmp_path);output=tmp_path/'new'
    (upstream/'portfolios/0-baseline/outer-predictions.json').unlink()
    result=module.run_cached_comparison(output,upstream,panel,
        lambda *a:{'net_pnl':0.,'drawdown':0.,'fills':0},{'attempt':1})
    row=result['rows'][0]
    assert row['status']=='FAILED' and row['stage']=='OUTER_COMPARISON'
    assert row['inner_trials_attempted']==6 and row['outer_cases_attempted']==0
    assert (output/'portfolios/0-baseline/selection.json').exists()
    assert len(result['rows'])==6 and result['failed_groups']==1
    assert module.run_cached_comparison(output,upstream,panel,lambda *a:pytest.fail('do not retry'),{'attempt':1})==result


def test_changed_partitions_and_missing_inner_stay_visible_before_any_replay(tmp_path):
    from tools import ai_factory_sleeve_process as module
    upstream,panel=materials(tmp_path);output=tmp_path/'new'
    path=upstream/'portfolios/0-baseline/plan.json';plan=verified(path)
    plan['partitions_hash']=digest('wrong fold')
    path.write_text(json.dumps(seal({k:v for k,v in plan.items() if k!='result_hash'})))
    (upstream/'portfolios/0-random/inner-predictions.json').unlink()
    result=module.run_cached_comparison(output,upstream,panel,
        lambda *a:{'net_pnl':0.,'drawdown':0.,'fills':0},{'attempt':1})
    assert result['failed_groups']==2
    assert all(r['inner_trials_attempted']==0 for r in result['rows'][:2])
    assert result['rows'][0]['stage']=='UPSTREAM_CONTRACT'
    assert result['rows'][1]['stage']=='INNER_FIT'


def test_incompatible_predictor_source_cannot_silently_refit(monkeypatch):
    from tools import ai_factory_sleeve_process as module
    from tools.perpetual_replay_research import source_identity
    source=source_identity();source['dependency_lock_sha256']='0'*64
    with pytest.raises(ValueError,match='predictor'):
        module.predictor_compatibility(source)


def test_real_checkpoint_roundtrip_verifies_sleeve_process_and_rejects_missing_row(tmp_path):
    from services.research.ai_factory_store import FactoryStore
    from tools.ai_quant_factory import execute
    from tools.ai_factory_checkpoint import create_checkpoint,restore_checkpoint
    from tools.ai_factory_sleeve_process import run_cached_comparison,verify_comparison
    upstream,panel=materials(tmp_path)
    root=tmp_path/'runtime';base=root/'data/cache/ai_factory';output=base/'1/process-sleeves-v1'
    with FactoryStore(base/'factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','objective':'BTC','budget':3})
        execute(store,1,panel,lambda *a:{'score':0.,'net_pnl':0.,'drawdown':0.,'fills':0},
            {'input_hash':digest('fixture')},base)
    result=run_cached_comparison(output,upstream,panel,
        lambda *a:{'net_pnl':0.,'drawdown':0.,'fills':0},{'attempt':1})
    archive=tmp_path/'archive.zip';create_checkpoint(root,archive)
    restored=tmp_path/'restored';restore_checkpoint(archive,restored)
    assert verify_comparison(restored/'1/process-sleeves-v1')==result
    (output/'rows/0-baseline.json').unlink()
    with pytest.raises(ValueError,match='terminal row missing'):
        create_checkpoint(root,tmp_path/'invalid.zip')
    assert not (tmp_path/'invalid.zip').exists()
