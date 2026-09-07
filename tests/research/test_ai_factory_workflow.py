import numpy as np
import pytest
from services.research.ai_factory_store import FactoryStore
from tools.ai_quant_factory import execute, validate_config

def panel():
    t=np.arange(600,dtype=np.int64)*3600000
    x=np.random.default_rng(3).normal(size=(600,5));return t,x,x[:,0]*.001

def test_baseline_runs_all_trials_and_outer_only_once(tmp_path):
    calls=[]
    def evaluate(spec,t,p):
        calls.append((spec['id'],t.copy()))
        return {'score':float(spec['id']), 'net_pnl':float(spec['id']), 'equity':[]}
    with FactoryStore(tmp_path/'factory.db') as s:
        s.create(validate_config({'attempt':1,'mode':'baseline','objective':'BTC','budget':3}))
        result=execute(s,1,panel(),evaluate,{'input':'fixed'},tmp_path)
        assert result['state']=='COMPLETED'
        assert len(s.trials(1))==3 and len(calls)==4
        assert result['result']['selected']['id']==2
        assert calls[-1][1][0]>max(c[1][-1] for c in calls[:-1])
        assert result['result']['authoritative'] is False
        execute(s,1,panel(),evaluate,{'input':'fixed'},tmp_path)
        assert len(calls)==4

def test_pause_and_resume_preserves_trials(tmp_path):
    def evaluate(spec,t,p):
        if spec['id']==0:s.control(1,'pause')
        return {'score':0.,'equity':[]}
    with FactoryStore(tmp_path/'factory.db') as s:
        s.create(validate_config({'attempt':1,'mode':'baseline','objective':'BTC','budget':3}))
        assert execute(s,1,panel(),evaluate,{},tmp_path)['state']=='PAUSED'
        assert len(s.trials(1))==1
        s.control(1,'resume')
        assert execute(s,1,panel(),evaluate,{},tmp_path)['state']=='COMPLETED'
        assert len(s.trials(1))==3

def test_missing_completed_trial_is_not_rewritten_as_interrupted(tmp_path):
    def evaluate(spec,t,p):
        if spec['id']==0:s.control(1,'pause')
        return {'score':0.,'equity':[]}
    with FactoryStore(tmp_path/'factory.db') as s:
        s.create(validate_config({'attempt':1,'mode':'baseline','objective':'BTC','budget':3}))
        assert execute(s,1,panel(),evaluate,{},tmp_path)['state']=='PAUSED'
        with s.c:s.c.execute('DELETE FROM trials WHERE attempt=1')
        s.control(1,'resume')
        with pytest.raises(ValueError,match='terminal receipt missing'):
            execute(s,1,panel(),evaluate,{},tmp_path)
        assert s.trials(1)==[]

def test_identity_drift_refused(tmp_path):
    with FactoryStore(tmp_path/'factory.db') as s:
        s.create(validate_config({'attempt':1,'mode':'baseline','objective':'BTC','budget':3}))
        s.control(1,'pause')
        execute(s,1,panel(),lambda *a:None,{'input':'a'},tmp_path)
        with pytest.raises(ValueError,match='identity'):execute(s,1,panel(),lambda *a:None,{'input':'b'},tmp_path)

@pytest.mark.parametrize('change',[{'budget':100},{'attempt':True},{'mode':'live'},{'objective':''}])
def test_bounded_config(change):
    with pytest.raises(ValueError):validate_config({'attempt':1,'mode':'baseline','objective':'BTC','budget':3,**change})


def test_hourly_factory_uses_canonical_whole_day_accounting_window():
    from decimal import Decimal as D
    from tools.ai_quant_factory import evaluate_market
    from services.research.ai_factory import candidates,HOUR
    minute=60000;day=24*HOUR
    times=np.arange(0,6*day+minute,minute,dtype=np.int64)
    market={'times':times,'prices':np.full(len(times),100.),'marks':np.full(len(times),100.),
            'funding':[],'daily_volume':{i:D('10000000') for i in range(7)},'start':300000,'end':6*day}
    decisions=np.arange(2*HOUR,5*day,HOUR)
    result=evaluate_market(market,candidates()[1],decisions,np.full(len(decisions),.01))
    assert result['net_pnl']<0 and float(result['fees'])>0
    assert result['equity'][0]['time']==day+300000


def test_crash_after_final_artifact_recovers_without_repeating_outer(tmp_path,monkeypatch):
    calls=[]
    def evaluate(spec,t,p):calls.append(1);return {'score':0.,'equity':[]}
    with FactoryStore(tmp_path/'factory.db') as s:
        s.create(validate_config({'attempt':1,'mode':'baseline','objective':'BTC','budget':3}))
        original=s.update
        def interrupted(attempt,state,result=None):
            if state=='COMPLETED':raise KeyboardInterrupt()
            return original(attempt,state,result)
        monkeypatch.setattr(s,'update',interrupted)
        with pytest.raises(KeyboardInterrupt):execute(s,1,panel(),evaluate,{},tmp_path)
        monkeypatch.setattr(s,'update',original)
        assert execute(s,1,panel(),evaluate,{},tmp_path)['state']=='COMPLETED'
        assert len(calls)==4
