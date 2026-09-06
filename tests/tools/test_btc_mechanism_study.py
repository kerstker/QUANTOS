import numpy as np
import pytest
from tools.btc_mechanism_study import DAY, features, forecasts, hac_mean, imbalance


def fixture():
    days=np.arange(260,dtype=np.int64)*DAY
    rng=np.random.default_rng(10)
    x=rng.normal(size=(260,5)); y=x[:,0]*.01+rng.normal(size=260)*.002
    return days,x,y


def test_predictions_ignore_future_and_unmatured_label():
    days,x,y=fixture()
    p,f=forecasts(days,x,y)
    short,_=forecasts(days[:230],x[:230],y[:230])
    np.testing.assert_equal(p[:230],short)
    i=next(i for i in range(200,260) if days[i]+DAY==f['PRICE'][next(k for k,v in enumerate(f['PRICE']) if v['decision_ms']>=days[200]+DAY)]['decision_ms'])
    changed=y.copy();changed[i-1:]=1e6
    q,_=forecasts(days,x,changed)
    np.testing.assert_equal(p[:i+1],q[:i+1])
    for fits in f.values():
        assert all(z['latest_label_ms']<=z['decision_ms'] for z in fits)


def test_constant_columns_and_missing_history():
    days,x,y=fixture();x[:,4]=0;x[:100]=np.nan
    p,f=forecasts(days,x,y)
    assert np.isnan(p).all() and not any(f.values())
    p,_=forecasts(days,np.zeros_like(x),y)
    assert np.isfinite(p[-1]).all()


def test_imbalance_checks_and_units():
    assert imbalance(np.array([100.,300.]),np.array([100.,100.]))==0
    for total,buy in [([1.],[2.]),([1.],[-1.]),([0.],[0.]),([np.nan],[0.])]:
        with pytest.raises(ValueError):imbalance(np.array(total),np.array(buy))


def test_features_are_past_only_and_missing_propagates():
    close=np.arange(100,150,dtype=float);flow=np.zeros(50);fund=np.arange(50)*.0001
    x=features(close,flow,fund)
    assert x[35,4]==pytest.approx(fund[33:36].sum())
    changed=close.copy();changed[40:]=1e6
    np.testing.assert_equal(x[:40],features(changed,flow,fund)[:40])
    close[30]=np.nan
    assert np.isnan(features(close,flow,fund)[40,2])


def test_hac_matches_independent_bartlett_matrix():
    values=np.array([1.,3.,-2.,4.,2.,5.,-1.,2.,0.,3.]);n=len(values)
    centered=values-values.mean()
    matrix=np.array([[max(0,1-abs(i-j)/8) for j in range(n)] for i in range(n)])
    expected=float(np.sqrt(centered@matrix@centered/n**2))
    result=hac_mean(values)
    assert result['se']==pytest.approx(expected)
    assert result['mean']==pytest.approx(values.mean())
