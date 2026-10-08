import json
import numpy as np
import pytest
from tools.ai_factory_forward import run_observation
from tools.btc_factory_research import seal,verified
from tools.cache_io import atomic_write_json
from services.research.ai_factory import HOUR,candidates
from services.research.ai_factory_store import FactoryStore


def setup(tmp_path):
    dest=tmp_path/'data/cache/ai_factory/1';dest.mkdir(parents=True)
    x=np.random.default_rng(2).normal(size=(40,5))
    model=seal({'schema':'QUANTOS_AI_FACTORY_V1','spec':candidates()[1],'training_x':x.tolist(),'training_y':(x[:,0]*.001).tolist(),'training_last_label_ms':HOUR,'authoritative':False,'live_eligible':False})
    atomic_write_json(str(dest/'model.json'),model)
    atomic_write_json(str(dest/'result.json'),seal({'model_hash':model['result_hash']}))
    with FactoryStore(dest.parent/'factory.db') as s:
        s.create({'attempt':1,'mode':'baseline','objective':'BTC','budget':3});s.update(1,'COMPLETED',{'model_hash':model['result_hash']})
    return dest


def fetch(slot):
    def get(path):
        if 'interval=1h' in path:
            payload=[[slot-(25-i)*HOUR,100,102,99,100+i,1,slot-(24-i)*HOUR-1,100,1,1,50,0] for i in range(25)]
        else:
            from urllib.parse import parse_qs,urlsplit
            at=int(parse_qs(urlsplit(path).query)['startTime'][0]);payload=[[at,100,101,99,100,1,at+59999,100,1,1,50,0]]
        return {'payload':payload,'received_at_ms':slot+1000,'requested_at_ms':slot,'sha256':'a'*64}
    return get


def test_activation_then_natural_issue_is_idempotent(tmp_path):
    dest=setup(tmp_path);slot=100*HOUR
    run_observation({'attempt':1,'action':1},root=tmp_path,clock=lambda:slot-1000,get=fetch(slot))
    r=run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+2000,get=fetch(slot))
    assert r['predictions']==1 and r['labels']==0
    r=run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+3000,get=fetch(slot))
    assert r['predictions']==1
    f=list((dest/'forward/forecasts').glob('*.json'));assert len(f)==1
    assert verified(f[0])['issued_at_ms']==slot+2000


def test_no_history_backfill_or_prediction_after_cutoff(tmp_path):
    dest=setup(tmp_path);slot=100*HOUR
    run_observation({'attempt':1,'action':1},root=tmp_path,clock=lambda:slot-1000,get=fetch(slot))
    r=run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+400000,get=fetch(slot))
    assert r['status']=='WAITING_WINDOW' and r['predictions']==0
    run_observation({'attempt':1,'action':2},root=tmp_path,clock=lambda:slot+500000,get=fetch(slot))
    r=run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+HOUR+1000,get=fetch(slot+HOUR))
    assert r['status']=='DISABLED'


def test_changed_frozen_model_rejected(tmp_path):
    dest=setup(tmp_path);slot=100*HOUR
    run_observation({'attempt':1,'action':1},root=tmp_path,clock=lambda:slot-1000,get=fetch(slot))
    path=dest/'forward/freeze.json';d=json.loads(path.read_text());d['model']['spec']['id']=2;path.write_text(json.dumps(d))
    with pytest.raises(ValueError):run_observation({'attempt':1,'action':0},root=tmp_path,clock=lambda:slot+1000,get=fetch(slot))
