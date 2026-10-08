"""Causal and durable contracts for the automated research factory."""
import numpy as np
import pytest
from services.research.ai_factory import candidates, fit_predict, partition, targets
from services.research.ai_factory_store import FactoryStore


def test_training_cannot_see_validation_labels():
    rng=np.random.default_rng(9); x=rng.normal(size=(500,5)); y=x[:,0]*.001
    a=fit_predict(candidates()[1],x[:300],y[:300],x[300:])
    y[300:]=1000
    b=fit_predict(candidates()[1],x[:300],y[:300],x[300:])
    np.testing.assert_array_equal(a,b)


def test_split_purges_labels_crossing_boundary():
    times=np.arange(600)*3600000
    train,valid,test=partition(times,times+4*3600000)
    assert (times[train]+4*3600000<times[valid[0]]).all()
    assert (times[valid]+4*3600000<times[test[0]]).all()


def test_targets_include_no_trade_and_cost_hurdle():
    assert targets([0,.001,.01,-.01],20)==[0,0,.25,-.25]
    with pytest.raises(ValueError):targets([float('nan')],20)


def test_session_identity_controls_and_history(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as s:
        config={'attempt':1,'objective':'BTC flow','mode':'baseline','budget':3}
        a=s.create(config)
        assert s.create(config)==a
        with pytest.raises(ValueError):s.create({**config,'budget':4})
        s.control(1,'pause'); assert s.get(1)['command']=='pause'
        s.control(1,'resume'); assert s.get(1)['command']=='run'
        s.control(1,'cancel')
        with pytest.raises(ValueError):s.control(1,'resume')
        assert len(s.events(1))==4


def test_trial_result_immutable(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as s:
        s.create({'attempt':1,'objective':'BTC','mode':'baseline','budget':3})
        s.record_trial(1,0,{'status':'FAILED','reason':'test'})
        s.record_trial(1,0,{'status':'FAILED','reason':'test'})
        with pytest.raises(ValueError):s.record_trial(1,0,{'status':'PASSED'})


def test_read_only_missing_does_not_create_database(tmp_path):
    assert FactoryStore.snapshot(tmp_path/'missing.db')['sessions']==[]
    assert not (tmp_path/'missing.db').exists()


def test_cancel_paused_session_is_immediately_terminal(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as s:
        s.create({'attempt':1,'objective':'BTC','mode':'baseline','budget':3})
        s.update(1,'PAUSED');s.control(1,'cancel')
        assert s.get(1)['state']=='CANCELLED'


def test_hourly_features_accept_zero_volume_minutes_without_future_leakage():
    from services.research.ai_factory import hourly_panel,HOUR
    n=40*60;t=np.arange(n)*60000;price=100+np.arange(n)*.001
    rows=np.column_stack((t,price,price,price,price,np.ones(n),t+59999,np.ones(n)*100,np.ones(n)*50))
    rows[10,7:]=0
    times,x,y=hourly_panel(rows)
    assert len(times)>0 and np.isfinite(x).all()
    changed=rows.copy();changed[changed[:,0]>=times[0],1:5]*=2
    times2,x2,y2=hourly_panel(changed)
    np.testing.assert_array_equal(x[0],x2[0])
