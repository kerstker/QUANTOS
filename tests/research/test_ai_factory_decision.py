import pytest
from services.research.ai_factory_decision import decide

def test_cost_hurdle_and_hold_avoid_pointless_turnover():
    assert decide(prediction=.0001,variance=.001,current=0,fee_bps=4,slip_bps=5,funding=0)['target']==0
    assert decide(prediction=.01,variance=.0001,current=0,fee_bps=4,slip_bps=5,funding=0)['target']==.25
    held=decide(prediction=.01,variance=.0001,current=.249,fee_bps=4,slip_bps=5,funding=0)
    assert held['target'] is None and held['reason']=='HOLD_COST_BAND'

def test_unknown_cost_is_not_zero_and_current_risk_is_reduced():
    with pytest.raises(ValueError):decide(prediction=.1,variance=.01,current=0,fee_bps=None,slip_bps=5,funding=0)
    r=decide(prediction=0,variance=.001,current=.4,fee_bps=4,slip_bps=5,funding=0)
    assert r['target'] in (0,.25,-.25) and r['target']!=.4

def test_higher_cost_can_remove_trade():
    args=dict(prediction=.001,variance=.00001,current=0,funding=0)
    assert decide(**args,fee_bps=1,slip_bps=2)['target']==.25
    assert decide(**args,fee_bps=20,slip_bps=10)['target']==0


def test_factory_threshold_changes_cost_aware_candidate_behavior():
    from decimal import Decimal as D
    import numpy as np
    from tests.research.test_perpetual_alpha import market
    from tools.ai_quant_factory import evaluate_market
    from services.research.ai_factory import candidates,HOUR
    data=market()
    data['funding']=[{'time':data['start']-i*8*HOUR,'rate':D(0),'mark':D(100)} for i in (3,2,1)]
    t=np.arange(data['start']-300000,data['end']-300000,HOUR,dtype=np.int64)
    p=np.full(len(t),.002);variance={int(at):.00001 for at in t}
    low=evaluate_market(data,candidates()[1],t,p,cost_aware=True,variance_by_slot=variance)
    high=evaluate_market(data,candidates()[10],t,p,cost_aware=True,variance_by_slot=variance)
    assert low['fills']>0 and high['fills']==0
