import numpy as np
import pytest
from services.research.ai_factory import HOUR,candidates
from tools.btc_factory_research import seal,verified
from tools.cache_io import atomic_write_json


def test_paired_portfolio_forecast_is_frozen_and_never_backfilled(tmp_path,monkeypatch):
    from tools import ai_factory_portfolio_forward as module
    base=tmp_path/'data/cache/ai_factory/1/portfolio';base.mkdir(parents=True)
    model=seal({'schema':'QUANTOS_FACTORY_PORTFOLIO_V1','components':candidates()[:2],
        'quarters':[2,2],'baseline_quarters':[4,0],'training_x':[[0]*5]*40,'training_y':[0]*40,
        'training_last_label_ms':HOUR,'authoritative':False,'live_eligible':False,'economic_eligible':False})
    atomic_write_json(str(base/'model.json'),model)
    atomic_write_json(str(base/'result.json'),seal({'model_hash':model['result_hash']}))
    monkeypatch.setattr(module,'verify_portfolio',lambda p:None)
    monkeypatch.setattr(module,'fit_predict',lambda s,x,y,z:np.array([.01 if s['id']==0 else -.01]))
    slot=100*HOUR
    def get(path):
        assert 'interval=1h' in path
        return {'received_at_ms':slot+1000,'payload':[[slot-(25-i)*HOUR,100,102,99,100+i,1,slot-(24-i)*HOUR-1,100,1,1,50,0] for i in range(25)]}
    module.run_observation({'attempt':1,'action':1},root=tmp_path,clock=lambda:slot-1000,get=get)
    result=module.run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+2000,get=get)
    assert result['predictions']==1 and result['account_pnl_computed'] is False
    path=base/'forward/forecasts'/f'{slot}.json';forecast=verified(path);before=path.read_bytes()
    assert forecast['prediction']==0 and forecast['baseline_prediction']>.005
    assert forecast['target_weight'] is None and forecast['decision_policy']=='DISCRETE_COST_RISK_V1'
    module.run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+3000,get=get)
    assert path.read_bytes()==before
    late=module.run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+HOUR+400000,get=get)
    assert late['predictions']==1 and not (base/'forward/forecasts'/f'{slot+HOUR}.json').exists()
    assert (tmp_path/'data/cache/profitability/1/portfolio-forward/plan.json').exists()
    assert not (tmp_path/'data/cache/ai_factory/1/forward').exists()
    assert not (tmp_path/'data/cache/profitability/1/p6').exists()
    control=base/'forward/freeze.json';changed=verified(control);changed['model']['quarters']=[4,0]
    atomic_write_json(str(control),seal({k:v for k,v in changed.items() if k!='result_hash'}))
    with pytest.raises(ValueError):
        module.run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+3000,get=get)
