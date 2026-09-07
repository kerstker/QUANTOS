import numpy as np
import pytest
from services.research.btc_factory_replay import select_schedule, prediction_target, frozen_price_predictions
from tools.btc_mechanism_study import DAY, MINUTE, forecasts


def panel():
    days=np.arange(1704067200000,1704067200000+400*DAY,DAY,dtype=np.int64)
    y=np.full(400,.01)
    p=np.full((400,4),.02);p[:,1]=.01
    return days,p,y


def test_selector_uses_mature_labels_and_future_changes_cannot_rewrite_choices():
    days,p,y=panel()
    choices,updates=select_schedule(days,p,y)
    assert choices[-1]=='PRICE_FLOW'
    assert all(row['latest_label_ms']<=row['decision_ms'] for row in updates if row['n'])
    changed=y.copy();changed[198:]=1e3
    alternate,other=select_schedule(days,p,changed)
    assert choices[:200]==alternate[:200]
    short,_=select_schedule(days[:200],p[:200],y[:200])
    assert choices[:200]==short


def test_cash_fallback_and_ties_are_deterministic():
    days,p,y=panel();p[:]=.03
    choices,_=select_schedule(days,p,y)
    assert set(choices)=={'CASH'}
    p[:]=.01
    choices,updates=select_schedule(days,p,y)
    assert choices[-1]=='PRICE'
    assert choices[0]=='CASH'
    assert all(row['n']<=90 for row in updates)
    assert len(updates)==14


def test_threshold_and_invalid_forecast():
    assert prediction_target(.01)==.25
    assert prediction_target(-.01)==-.25
    assert prediction_target(.001)==0
    with pytest.raises(ValueError): prediction_target(float('nan'))


def test_frozen_model_never_refits_on_future_labels():
    rng=np.random.default_rng(9)
    days=np.arange(1704067200000,1704067200000+400*DAY,DAY,dtype=np.int64)
    x=rng.normal(size=(400,5));y=x[:,0]*.01
    _,fits=forecasts(days,x,y)
    start=int(days[250]+DAY)
    a,fit=frozen_price_predictions(days,x,fits,start)
    assert fit['latest_label_ms']<=start
    changed=y.copy();changed[250:]=1000
    _,other=forecasts(days,x,changed)
    b,_=frozen_price_predictions(days,x,other,start)
    np.testing.assert_equal(a,b)
    with pytest.raises(ValueError): frozen_price_predictions(days,x,fits,int(days[1]+DAY))
