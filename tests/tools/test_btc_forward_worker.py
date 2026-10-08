import pytest
from tools import btc_forward_observation as worker


def test_status_without_freeze_is_honest_and_does_not_create_state(tmp_path,monkeypatch):
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    report=worker.run_forward({'attempt':1})
    assert report['status']=='NO_FREEZE' and report['models']==[]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('params',[{'attempt':True},{'attempt':0},{'attempt':1,'action':True},{'attempt':1,'action':4},{'attempt':1,'path':'/tmp'}])
def test_worker_parameters_bounded(params):
    with pytest.raises(ValueError):worker.run_forward(params)


def test_private_endpoint_never_reachable():
    with pytest.raises(ValueError):worker._get('/fapi/v2/account')


def test_public_worker_freeze_predict_settle_and_repeat(tmp_path,monkeypatch):
    import json
    from tools.btc_factory_research import seal
    slot=1788825600000;clock=[slot-worker.DAY]
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    monkeypatch.setattr(worker,'_now',lambda:clock[0])
    fits={name:[{'mean':[0.]*n,'scale':[1.]*n,'beta':[.01]+[0.]*n,
                  'train_n':200,'latest_label_ms':slot-2*worker.DAY}]
          for name,n in [('PRICE',3),('PRICE_FLOW',4),('PRICE_FUNDING',4),('PRICE_FLOW_FUNDING',5)]}
    source=tmp_path/'data/cache/btc_factory/results/1';source.mkdir(parents=True)
    (source/'result.json').write_text(json.dumps(seal({'schema':'QUANTOS_BTC_FACTORY_REPLAY_V1',
         'trial_ledger_verified':True,'cases':[{'status':'PASSED'}]*48,'fits':fits})))
    frozen=worker.run_forward({'attempt':1,'action':1})
    assert len(frozen['models'])==4
    calls=[]
    def get(path):
        calls.append(path)
        if 'interval=1d' in path:
            rows=[[slot-(29-i)*worker.DAY,100,100,100,100+i,100,slot-(28-i)*worker.DAY-1,100,0,0,50] for i in range(29)]
        elif 'fundingRate' in path:
            rows=[{'symbol':'BTCUSDC','fundingTime':at,'fundingRate':'0'} for at in range(slot-3*worker.DAY,slot,8*60*worker.MINUTE)]
        else:
            from urllib.parse import parse_qs,urlsplit
            at=int(parse_qs(urlsplit(path).query)['startTime'][0]);price=100 if at==slot+5*worker.MINUTE else 101
            rows=[[at,price,price,price,price,1,at+worker.MINUTE-1]]
        return {'payload':rows,'received_at_ms':clock[0],'sha256':'a'*64}
    monkeypatch.setattr(worker,'_get',get)
    clock[0]=slot+2*worker.MINUTE
    first=worker.run_forward({'attempt':1,'action':2})
    assert all(r['pending']==1 for r in first['models'])
    clock[0]=slot+worker.DAY+7*worker.MINUTE
    second=worker.run_forward({'attempt':1,'action':2})
    assert all(r['completed']==1 and r['pending']==0 for r in second['models'])
    before=len(calls)
    third=worker.run_forward({'attempt':1,'action':2})
    assert third['status']=='WAITING_WINDOW' and len(calls)==before


def test_partial_public_capture_is_retained_when_second_request_fails(tmp_path,monkeypatch):
    from tools.btc_factory_research import seal
    import json
    slot=1788825600000
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    monkeypatch.setattr(worker,'_now',lambda:slot+worker.MINUTE)
    directory=tmp_path/'data/cache/btc_forward/1';directory.mkdir(parents=True)
    fits={name:{'mean':[0.]*n,'scale':[1.]*n,'beta':[0.]*(n+1),
                'train_n':180,'latest_label_ms':slot-worker.DAY}
          for name,n in [('PRICE',3),('PRICE_FLOW',4),('PRICE_FUNDING',4),('PRICE_FLOW_FUNDING',5)]}
    (directory/'freeze.json').write_text(json.dumps(seal({'source_hash':'a'*64,'frozen_at_ms':slot-worker.DAY,'fits':fits})))
    def get(path):
        if 'fundingRate' in path:raise ValueError('funding unavailable')
        return {'payload':[],'received_at_ms':slot,'sha256':'b'*64}
    monkeypatch.setattr(worker,'_get',get)
    with pytest.raises(ValueError,match='funding unavailable'):
        worker.run_forward({'attempt':1,'action':2})
    assert len(list((directory/'captures').glob('*.json')))==1
    assert len(list((directory/'errors').glob('*.json')))==1
    assert all(row['decisions']==0 for row in worker.run_forward({'attempt':1})['models'])
    enabled=worker.run_forward({'attempt':1,'action':3})
    assert enabled['online_factory']['status']=='WAITING_WINDOW'
    assert enabled['online_factory']['training_available']==0
    assert worker.run_forward({'attempt':1,'action':3})['online_factory']==enabled['online_factory']


def test_interrupted_prediction_group_reuses_first_received_daily_features(tmp_path,monkeypatch):
    import json
    from tools.btc_factory_research import seal
    slot=1788825600000
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    monkeypatch.setattr(worker,'_now',lambda:slot+worker.MINUTE)
    directory=tmp_path/'data/cache/btc_forward/1';directory.mkdir(parents=True)
    fits={name:{'mean':[0.]*n,'scale':[1.]*n,'beta':[0.]*(n+1),
                'train_n':180,'latest_label_ms':slot-worker.DAY}
          for name,n in [('PRICE',3),('PRICE_FLOW',4),('PRICE_FUNDING',4),('PRICE_FLOW_FUNDING',5)]}
    (directory/'freeze.json').write_text(json.dumps(seal({'source_hash':'a'*64,'frozen_at_ms':slot-worker.DAY,'fits':fits})))
    calls=[]
    def features(slot,records):
        calls.append(slot);records.append({'received_at_ms':slot+worker.MINUTE,'payload':'original'})
        return [1,2,3,4,5],slot+worker.MINUTE
    monkeypatch.setattr(worker,'_features',features)
    original=worker.record_forecast;count=[0]
    def fail_once(*args,**kwargs):
        count[0]+=1
        if count[0]==2:raise RuntimeError('interrupted group')
        return original(*args,**kwargs)
    monkeypatch.setattr(worker,'record_forecast',fail_once)
    with pytest.raises(RuntimeError,match='interrupted group'):
        worker.run_forward({'attempt':1,'action':2})
    report=worker.run_forward({'attempt':1,'action':2})
    assert len(calls)==1 and all(row['decisions']==1 for row in report['models'])
    with worker.RealtimeAlphaShadowStore(str(directory/'observations.db')) as store:
        decisions=[store.decisions(row['candidate_id'])[0]['payload'] for row in report['models']]
        assert len({row['source_hash'] for row in decisions})==1
        assert all(row['features']==[1,2,3,4,5] for row in decisions)
