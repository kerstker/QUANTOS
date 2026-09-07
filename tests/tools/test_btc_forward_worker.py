import pytest
from tools import btc_forward_observation as worker


def test_status_without_freeze_is_honest_and_does_not_create_state(tmp_path,monkeypatch):
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    report=worker.run_forward({'attempt':1})
    assert report['status']=='NO_FREEZE' and report['models']==[]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('params',[{'attempt':True},{'attempt':0},{'attempt':1,'action':True},{'attempt':1,'action':3},{'attempt':1,'path':'/tmp'}])
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
