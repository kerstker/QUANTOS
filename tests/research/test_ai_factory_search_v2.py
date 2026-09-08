import numpy as np
import pytest
from services.research.ai_factory import candidates,fit_predict,validate_config


def test_composed_space_preserves_frozen_specs_and_predicts_without_future():
    old=candidates()
    expanded=candidates(2)
    assert expanded[:18]==old and len(expanded)>len(old)
    rng=np.random.default_rng(14);x=rng.normal(0,.01,(80,5));x[:,3]=abs(x[:,3])+.001
    y=rng.normal(0,.01,80)
    spec=next(s for s in expanded if s.get('transform')=='flow_interaction' and s['model']=='ridge')
    before=fit_predict(spec,x[:60],y[:60],x[60:65])
    altered=x.copy();altered[65:]=1000
    assert np.allclose(before,fit_predict(spec,altered[:60],y[:60],altered[60:65]))
    with pytest.raises(ValueError):fit_predict({**spec,'transform':'eval'},x[:60],y[:60],x[60:])


def test_search_plan_cannot_exclude_every_candidate_or_reference_unknown_source():
    config={'attempt':1,'mode':'ai','objective':'BTC','budget':3,'search_plan':{'version':2,'excluded_ids':[],'source_ids':['aqr','buildalpha']}}
    assert validate_config(config)['search_plan']['version']==2
    with pytest.raises(ValueError):validate_config({**config,'search_plan':{**config['search_plan'],'source_ids':['invented']}})
    with pytest.raises(ValueError):validate_config({**config,'search_plan':{**config['search_plan'],'excluded_ids':list(range(72))}})


def test_v2_counts_duplicate_predictions_without_replaying_them(tmp_path,monkeypatch):
    from services.research.ai_factory_store import FactoryStore
    from tools import ai_quant_factory as worker
    t=np.arange(600,dtype=np.int64)*3600000;x=np.zeros((600,5));y=np.zeros(600);calls=[]
    monkeypatch.setattr(worker,'fit_predict',lambda spec,tx,ty,z:np.zeros(len(z)))
    def evaluate(*args):calls.append(True);return {'score':0.,'equity':[]}
    with FactoryStore(tmp_path/'factory.db') as s:
        s.create({'attempt':1,'mode':'baseline','budget':3,'objective':'BTC','search_plan':{'version':2,'excluded_ids':[],'source_ids':['aqr']}})
        worker.execute(s,1,(t,x,y),evaluate,{},tmp_path)
        assert len(calls)==2
        assert len(s.trials(1))==3
        assert [r['status'] for r in s.trials(1)]==['PASSED','FAILED','FAILED']
