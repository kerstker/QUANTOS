from decimal import Decimal as D
import numpy as np
from services.research.ai_factory import HOUR
from services.research.ai_factory_store import FactoryStore,digest
from tools.ai_quant_factory import execute
from tools import ai_factory_assessment as module


def test_assessment_replays_stress_and_shared_equity(tmp_path,monkeypatch):
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    root=tmp_path/'data/cache';out=root/'ai_factory';(root/'perpetual_alpha/input').mkdir(parents=True)
    t=np.arange(600,dtype=np.int64)*HOUR;x=np.random.default_rng(3).normal(size=(600,5));x[:,3]=.005;y=x[:,0]*.001
    with FactoryStore(out/'factory.db') as s:
        s.create({'attempt':1,'mode':'baseline','objective':'BTC','budget':3})
        execute(s,1,(t,x,y),lambda spec,t,p:{'score':float(spec['id']),'equity':[{'time':int(v),'equity':'1500'} for v in t[::24]]},
                {'input_hash':digest({})},out)
    times=np.arange(0,611*HOUR,60000,dtype=np.int64)
    market={'times':times,'prices':np.full(len(times),100.),'marks':np.full(len(times),100.),'start':0,'end':610*HOUR,
            'funding':[{'time':i*8*HOUR,'rate':D('.0001'),'mark':D(100)} for i in range(76)],
            'daily_volume':{i:D('10000000') for i in range(26)}}
    monkeypatch.setattr(module,'load_verified_minutes',lambda d:(None,None))
    monkeypatch.setattr(module,'hourly_panel',lambda a:(t,x,y))
    monkeypatch.setattr(module,'load_data',lambda **k:(market,None))
    # The production panel is already clipped before the identity is frozen.
    result=module.run_assessment({'attempt':1})
    assert len(result['cases'])==6 and result['portfolio']['status']=='POST_SELECTION_DIAGNOSTIC'
    assert result['portfolio']['add']['equity'][0]['equity']=='1500'
    assert result['authoritative'] is False
    assert module.run_assessment({'attempt':1})==result
