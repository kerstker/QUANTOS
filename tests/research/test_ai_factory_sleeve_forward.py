"""Preissued raw component forecasts drive the same frozen net-account policy."""
import numpy as np
import pytest
from decimal import Decimal as D
from services.research.ai_factory import HOUR,candidates
from services.research.ai_factory_store import digest
from services.research.profitability_forward_account import make_plan,validate_window,replay_window
from tools.btc_factory_research import seal

DAY=24*HOUR
POLICY='INDEPENDENT_SLEEVES_AND_INNER_BASELINE'


def contract():return {'components':candidates()[:2],'quarters':[2,2],'baseline_quarters':[4,0]}


def data():
    plan=make_plan(now_ms=DAY+1,model_hash='a'*64,policy=POLICY,sleeves=contract())
    rows=[]
    for slot in range(plan['start_ms']-300000,plan['start_ms']+DAY-300000,HOUR):
        rows.append(seal({'slot_ms':slot,'available_at_ms':slot+1,'issued_at_ms':slot+2,
            'entry_ms':slot+300000,'exit_ms':slot+300000+4*HOUR,'model_hash':'a'*64,'input_hash':'b'*64,
            'prediction':.008,'baseline_prediction':.02,'component_predictions':[.02,-.004],
            'sleeve_contract_hash':digest(contract()),'features':[0,0,0,.001,0],
            'target_weight':None,'decision_policy':'INDEPENDENT_SLEEVE_COST_RISK_V1'}))
    times=np.arange(plan['start_ms'],plan['start_ms']+DAY+60000,60000)
    market={'times':times,'prices':np.full(len(times),100000.),'marks':np.full(len(times),100000.),
        'funding':[{'time':h*HOUR,'rate':D(0),'mark':D(100000)} for h in range(0,16*24,8)],
        'start':int(times[0]),'end':int(times[-1]),'daily_volume':{i:D('1000000000') for i in range(16)}}
    return plan,rows,market


def test_forward_sleeves_match_historical_policy_without_reconstructing_component_predictions():
    from services.research.ai_factory_sleeve_replay import replay_sleeves
    plan,rows,market=data();now=plan['start_ms']+DAY+4*HOUR
    result=replay_window(plan,rows,market,as_of_ms=now)
    assert len(result['cases'])==9
    selected=[c for c in result['cases'] if c['model']=='FROZEN_MODEL']
    baseline=[c for c in result['cases'] if c['model']=='SINGLE_BASELINE']
    assert all(c['attribution']['net_pnl']=='0' for c in selected)
    assert all(D(c['attribution']['net_pnl'])<0 for c in baseline)
    historical=replay_sleeves(market,contract()['components'],[2,2],
        np.asarray([r['slot_ms'] for r in rows]),np.asarray([r['component_predictions'] for r in rows]).T,
        variance_by_slot={r['slot_ms']:.001**2*4 for r in rows},window=(plan['start_ms'],plan['start_ms']+DAY))
    assert historical==selected[0]['receipt']
    assert all(c['receipt']['reconciliation']['residual']=='0' for c in result['cases'])


@pytest.mark.parametrize('change',['missing_components','wrong_contract','late','direction_target','wrong_length'])
def test_forward_refuses_to_infer_missing_vectors_or_mix_policy_versions(change):
    plan,rows,_=data();row={k:v for k,v in rows[0].items() if k!='result_hash'}
    if change=='missing_components':row.pop('component_predictions')
    elif change=='wrong_contract':row['sleeve_contract_hash']='c'*64
    elif change=='late':row['issued_at_ms']=row['entry_ms']+1
    elif change=='direction_target':row['target_weight']=.25
    else:row['component_predictions']=[.02]
    with pytest.raises(ValueError):validate_window(plan,[seal(row),*rows[1:]],as_of_ms=plan['start_ms']+DAY+4*HOUR)


def test_scalar_forecast_document_keeps_target_unassigned_for_independent_policy():
    from services.research.ai_factory_forward import forecast_document
    row=forecast_document(slot=2*DAY,available=2*DAY+1,issued=2*DAY+2,activated=DAY,
        model_hash='a'*64,input_hash='b'*64,features=[0,0,0,.001,0],prediction=.008,threshold_bps=0,
        decision_policy='INDEPENDENT_SLEEVE_COST_RISK_V1')
    assert row['target_weight'] is None


def test_observer_freezes_component_vectors_and_opens_a_separate_cost_window(tmp_path,monkeypatch):
    from tools import ai_factory_sleeve_forward as module
    from tools import ai_factory_portfolio_forward as shared
    from tools.cache_io import atomic_write_json
    from tools.btc_factory_research import verified
    base=tmp_path/'data/cache/ai_factory/1/portfolio-sleeves-v1';base.mkdir(parents=True)
    model=seal({'schema':'QUANTOS_FACTORY_SLEEVE_PORTFOLIO_V1',**contract(),
        'construction':'INDEPENDENT_DECISIONS_ONE_WAY_NET_ACCOUNT_V1','training_x':[[0]*5]*40,
        'training_y':[0]*40,'training_last_label_ms':HOUR,'authoritative':False,'live_eligible':False,'economic_eligible':False})
    atomic_write_json(str(base/'model.json'),model)
    atomic_write_json(str(base/'result.json'),seal({'model_hash':model['result_hash']}))
    monkeypatch.setattr(shared,'verify_portfolio',lambda path:None)
    monkeypatch.setattr(shared,'fit_predict',lambda s,x,y,z:np.array([.02 if s['id']==0 else -.004]))
    slot=100*HOUR
    def get(path):
        assert 'interval=1h' in path
        return {'received_at_ms':slot+1000,'payload':[[slot-(25-i)*HOUR,100,102,99,100+i,1,slot-(24-i)*HOUR-1,100,1,1,50,0] for i in range(25)]}
    module.run_observation({'attempt':1,'action':1},root=tmp_path,clock=lambda:slot-1000,get=get)
    result=module.run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+2000,get=get)
    assert result['predictions']==1
    path=base/'forward/forecasts'/f'{slot}.json';row=verified(path);before=path.read_bytes()
    assert row['component_predictions']==[.02,-.004]
    assert row['sleeve_contract_hash']==digest(contract())
    assert row['target_weight'] is None and row['decision_policy']=='INDEPENDENT_SLEEVE_COST_RISK_V1'
    module.run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+3000,get=get)
    assert path.read_bytes()==before
    module.run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+HOUR+400000,get=get)
    assert not (base/'forward/forecasts'/f'{slot+HOUR}.json').exists()
    cost=tmp_path/'data/cache/profitability/1/portfolio-sleeves-forward/plan.json'
    assert verified(cost)['policy']==POLICY
    assert not (tmp_path/'data/cache/profitability/1/portfolio-forward').exists()
    assert not (tmp_path/'data/cache/ai_factory/1/portfolio').exists()
    freeze_path=base/'forward/freeze.json';frozen=verified(freeze_path)
    frozen['model']['quarters']=[4,0]
    frozen['model']=seal({k:v for k,v in frozen['model'].items() if k!='result_hash'})
    atomic_write_json(str(freeze_path),seal({k:v for k,v in frozen.items() if k!='result_hash'}))
    with pytest.raises(ValueError,match='activated portfolio model changed'):
        module.run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+3000,get=get)
    assert path.read_bytes()==before
