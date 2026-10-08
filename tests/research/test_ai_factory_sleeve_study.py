"""Independent decisions use the same bounded inner-selection machinery."""
import numpy as np
import pytest
from tests.research.test_ai_factory_portfolio_workflow import inputs
from services.research.ai_factory import HOUR
from tools.ai_factory_portfolio import run_study
from tools.ai_factory_sleeve_replay import CONSTRUCTION
from tools.btc_factory_research import verified


def test_raw_component_predictions_and_weights_reach_each_registered_replay(tmp_path):
    panel,components=inputs();calls=[]
    def replay(t,p,fee,slip):
        assert set(p)=={'components','predictions','quarters','construction'}
        assert p['construction']==CONSTRUCTION
        assert np.asarray(p['predictions']).shape==(2,len(t))
        if t[0]>400*HOUR:assert (tmp_path/'selection.json').exists()
        calls.append((int(t[0]),p['quarters']))
        return {'net_pnl':0.,'drawdown':0.,'fills':0,'fees':'0','funding':'0','equity':[]}
    result=run_study(tmp_path,{'id':1},components,panel,replay,
        fit=lambda s,x,y,test:np.full(len(test),.02 if s['id']==0 else -.004),construction=CONSTRUCTION)
    assert len(calls)==12 and result['inner_trials_attempted']==6
    assert result['selected']['quarters']==[0,0]
    model=verified(tmp_path/'model.json')
    assert model['decision_policy']=='INDEPENDENT_SLEEVE_COST_RISK_V1'
    assert model['construction']==CONSTRUCTION
    assert result['schema']=='QUANTOS_FACTORY_SLEEVE_PORTFOLIO_V1'
    assert 'ONE_WAY_NET_ACCOUNT_NOT_HEDGE_MODE' in result['limitations']
    with pytest.raises(ValueError,match='identity'):
        run_study(tmp_path,{'id':1},components,panel,replay,
            fit=lambda s,x,y,test:np.zeros(len(test)))


def test_future_labels_do_not_change_sleeve_budgets(tmp_path):
    panel,components=inputs()
    def replay(t,p,fee,slip):
        return {'net_pnl':-float(np.abs(p['predictions']).sum()),'drawdown':0.,'fills':0}
    def fit(s,x,y,test):return np.full(len(test),float(np.mean(y))+.001*(s['id']+1))
    first=run_study(tmp_path/'first',{'id':1},components,panel,replay,fit=fit,construction=CONSTRUCTION)
    y=panel[2].copy();y[500:]=1e6
    second=run_study(tmp_path/'second',{'id':2},components,(panel[0],panel[1],y),replay,fit=fit,construction=CONSTRUCTION)
    assert first['selected']==second['selected']


def test_full_sleeve_trace_is_preserved_outside_the_bounded_result_and_verified(tmp_path):
    import json
    from tools.ai_factory_portfolio_verify import verify_portfolio
    from tools.btc_factory_research import seal
    from services.research.ai_factory_sleeves import SleeveBook
    panel,components=inputs();book=SleeveBook([0,1],[2,2]);summary=book.report()
    trace={'construction':CONSTRUCTION,'ending':summary,'decisions':[
        {'time':i,**summary,'target':0.,'choices':[]} for i in range(100)],
        'execution':[],'authoritative':False,'economic_eligible':False,'limitations':[]}
    result=run_study(tmp_path,{'id':1},components,panel,
        lambda *a:{'net_pnl':0.,'drawdown':0.,'fills':0,'sleeve_execution':trace},
        fit=lambda s,x,y,test:np.zeros(len(test)),construction=CONSTRUCTION)
    projection=result['outer_cases'][0]['metrics']['sleeve_execution']
    assert projection['decision_count']==100 and len(projection['decisions'])==96
    assert len(list((tmp_path/'sleeve-traces').glob('*.json')))==12
    path=tmp_path/'sleeve-traces'/f"{result['outer_cases'][0]['trial_id']}.json"
    full=verified(path);assert len(full['trace']['decisions'])==100
    # Even a modification before the projected tail must break checkpoint binding.
    full['trace']['decisions'][0]['time']=-1
    path.write_text(json.dumps(seal({k:v for k,v in full.items() if k!='result_hash'})))
    with pytest.raises(ValueError,match='sleeve'):verify_portfolio(tmp_path)
