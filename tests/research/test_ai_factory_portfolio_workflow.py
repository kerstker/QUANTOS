import numpy as np
import pytest

from services.research.ai_factory import HOUR
from tools.btc_factory_research import verified


def inputs():
    t=np.arange(600,dtype=np.int64)*HOUR
    x=np.ones((600,5));y=np.zeros(600)
    components=[{'id':0,'model':'fixture','threshold_bps':20},
                {'id':1,'model':'fixture','threshold_bps':20}]
    return (t,x,y),components


def test_weights_freeze_before_outer_and_predictions_fit_once_per_component(tmp_path):
    from tools.ai_factory_portfolio import run_study
    panel,components=inputs();fits=[];calls=[]
    def fit(spec,x,y,test):
        fits.append((spec['id'],len(test)))
        return np.full(len(test),.003 if spec['id']==0 else -.003)
    def replay(t,p,fee,slip):
        outer=int(t[0])>400*HOUR
        if outer:assert (tmp_path/'selection.json').exists()
        calls.append((outer,fee,slip))
        net=-float(np.abs(p).sum())
        return {'net_pnl':net,'drawdown':0.,'fills':int(np.count_nonzero(p)),
                'fees':'0','funding':'0','equity':[{'time':int(t[0]),'equity':'1500'}]}
    result=run_study(tmp_path,{'input_hash':'a'*64},components,panel,replay,fit=fit)
    assert result['selected']['quarters']==[0,0]
    assert result['decision']=='STOP_NO_ACTIVE_PORTFOLIO_EVIDENCE'
    assert result['inner_trials_attempted']==6 and len(result['outer_cases'])==6
    assert len(fits)==4
    before=(tmp_path/'selection.json').read_bytes()
    assert run_study(tmp_path,{'input_hash':'a'*64},components,panel,replay,fit=fit)==result
    assert len(fits)==4 and (tmp_path/'selection.json').read_bytes()==before
    with pytest.raises(ValueError,match='identity'):
        run_study(tmp_path,{'input_hash':'b'*64},components,panel,replay,fit=fit)


def test_outer_labels_cannot_change_inner_weights(tmp_path):
    from tools.ai_factory_portfolio import run_study
    panel,components=inputs()
    def fit(spec,x,y,test):return np.full(len(test),.003 if spec['id']==0 else -.003)
    def replay(t,p,fee,slip):return {'net_pnl':-float(np.abs(p).sum()),'drawdown':0.,'fills':int(np.count_nonzero(p)),'fees':'0','funding':'0','equity':[]}
    original=run_study(tmp_path/'one',{'id':1},components,panel,replay,fit=fit)
    changed=panel[2].copy();changed[500:]=1e6
    other=run_study(tmp_path/'two',{'id':2},components,(panel[0],panel[1],changed),replay,fit=fit)
    assert original['selected']==other['selected']


def test_interrupted_trial_is_retained_and_not_replayed(tmp_path):
    from tools.ai_factory_portfolio import run_study
    panel,components=inputs();calls=[]
    def fit(spec,x,y,test):return np.full(len(test),.003)
    def interrupted(t,p,fee,slip):calls.append(1);raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):run_study(tmp_path,{'id':1},components,panel,interrupted,fit=fit)
    def replay(t,p,fee,slip):return {'net_pnl':0.,'drawdown':0.,'fills':0,'fees':'0','funding':'0','equity':[]}
    result=run_study(tmp_path,{'id':1},components,panel,replay,fit=fit)
    assert result['inner_trials'][0]['status']=='FAILED'
    assert result['inner_trials'][0]['reason']=='INTERRUPTED'
    assert result['inner_trials_attempted']==6
    assert len(calls)==1
    assert verified(tmp_path/'result.json')['authoritative'] is False


def test_checkpoint_rejects_resealed_portfolio_model_and_trial_conflicts(tmp_path):
    from services.research.ai_factory_store import FactoryStore
    from tools.ai_factory_checkpoint import create_checkpoint,restore_checkpoint
    from tools.ai_factory_portfolio import run_study
    from tools.btc_factory_research import seal
    import json
    root=tmp_path/'state';base=root/'data/cache/ai_factory';out=base/'1/portfolio'
    with FactoryStore(base/'factory.db') as store:
        store.create({'attempt':1,'mode':'baseline','objective':'BTC','budget':3})
    panel,components=inputs()
    result=run_study(out,{'id':1},components,panel,
        lambda *a:{'net_pnl':0.,'drawdown':0.,'fills':0},
        fit=lambda s,x,y,test:np.zeros(len(test)))
    create_checkpoint(root,tmp_path/'good.zip')
    restore_checkpoint(tmp_path/'good.zip',tmp_path/'restored')
    model=verified(out/'model.json');changed=dict(model);changed.pop('result_hash');changed['quarters']=[4,0]
    (out/'model.json').write_text(json.dumps(seal(changed)))
    with pytest.raises(ValueError,match='portfolio'):
        create_checkpoint(root,tmp_path/'bad-model.zip')
    (out/'model.json').write_text(json.dumps(model))
    case=out/'cases'/f"{result['inner_trials'][0]['trial_id']}.json"
    value=verified(case);value.pop('result_hash');value['status']='FAILED'
    case.write_text(json.dumps(seal(value)))
    with pytest.raises(ValueError,match='portfolio'):
        create_checkpoint(root,tmp_path/'bad-trial.zip')


def test_resealed_prediction_cache_cannot_hide_invalid_shape_on_resume(tmp_path):
    from tools.ai_factory_portfolio import run_study
    from tools.btc_factory_research import seal
    import json
    panel,components=inputs()
    def interrupted(*a):raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run_study(tmp_path,{'id':1},components,panel,interrupted,fit=lambda s,x,y,z:np.zeros(len(z)))
    path=tmp_path/'inner-predictions.json';cached=verified(path);cached.pop('result_hash')
    cached['predictions']=[[0],[0]];path.write_text(json.dumps(seal(cached)))
    with pytest.raises(ValueError,match='predictions'):
        run_study(tmp_path,{'id':1},components,panel,lambda *a:None)
