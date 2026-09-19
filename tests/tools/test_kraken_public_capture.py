import json
from copy import deepcopy

import pytest

from tools import kraken_public_capture as tool
from services.data.persistent_store import SqlitePITStore

NOW=1800000000000


def responses():
    return {'AssetPairs': {'error': [], 'result': {'AAPLxUSD': {'altname':'AAPLxUSD','wsname':'AAPLx/USD',
        'base':'AAPLx','quote':'ZUSD','aclass_base':'tokenized_asset','aclass_quote':'currency',
        'pair_decimals':2,'lot_decimals':8,'lot_multiplier':1,'ordermin':'0.00000001',
        'costmin':'0.5','tick_size':'0.01','status':'online'}}},
        'OHLC': {'error': [], 'result': {'AAPLxUSD': [[NOW//1000-120,'100','102','99','101','100','2',4],
             [NOW//1000-60,'101','103','100','102','101','3',5],
             [NOW//1000,'102','105','101','104','103','1',2]], 'last':NOW//1000-60}}}


def capture(data=None, now=NOW):
    source=data or responses();ticks=iter(range(now,now+100));calls=[]
    def get(endpoint, params):
        calls.append((endpoint,params));return json.dumps(source[endpoint]).encode()
    result=tool.collect(pair='AAPLx/USD',asset_class='tokenized_asset',interval=1,get=get,clock_ms=lambda:next(ticks))
    return result,calls


def test_public_capture_persists_replay_and_preserves_earliest_knowledge(tmp_path):
    raw,calls=capture(); assert [c[0] for c in calls]==['AssetPairs','OHLC']
    assert calls[1][1]['asset_class']=='tokenized_asset'
    assert tool.persist(tmp_path,raw)['bars_inserted']==2
    assert tool.persist(tmp_path,raw)['bars_inserted']==0
    second,_=capture(now=NOW+1000);assert tool.persist(tmp_path,second)['bars_inserted']==0
    db=SqlitePITStore(str(tmp_path/'market.db'));key=raw['instrument']['instrument_id']
    assert db.as_of(key,NOW-1)==[] and len(db.as_of(key,NOW+100))==2
    changed=responses();changed['OHLC']['result']['AAPLxUSD'][0][4]='100.5'
    third,_=capture(changed,now=NOW+2000);assert tool.persist(tmp_path,third)['bars_inserted']==1
    assert db.as_of(key,NOW+100)[0].close==101


def test_tampering_failure_and_nonpublic_endpoints_do_not_write_success(tmp_path):
    raw,_=capture();bad=deepcopy(raw);bad['rows'][0]['close']=200
    with pytest.raises(ValueError):tool.persist(tmp_path,bad)
    assert not (tmp_path/'market.db').exists()
    broken=responses();broken['OHLC']['error']=['EGeneral:Temporary lockout']
    failed,_=capture(broken,now=NOW+1000);assert failed['status']=='FAILED'
    tool.persist(tmp_path,raw);old=(tmp_path/'latest-success.json').read_bytes()
    tool.persist(tmp_path,failed);assert (tmp_path/'latest-success.json').read_bytes()==old
    assert json.loads((tmp_path/'latest-attempt.json').read_text())['status']=='FAILED'
    with pytest.raises(ValueError): tool.public_get('AddOrder',{})


def test_state_symlink_is_rejected(tmp_path):
    target=tmp_path/'real';target.mkdir();link=tmp_path/'link';link.symlink_to(target,target_is_directory=True)
    raw,_=capture()
    with pytest.raises(ValueError):tool.persist(link,raw)


def test_replaying_old_capture_never_rolls_back_status_and_interval_is_bound(tmp_path):
    old,_=capture();new,_=capture(now=NOW+2000)
    tool.persist(tmp_path,new);before=(tmp_path/'latest-success.json').read_bytes()
    tool.persist(tmp_path,old)
    assert (tmp_path/'latest-success.json').read_bytes()==before
    wrong=deepcopy(new);wrong['interval']=5
    wrong['result_hash']=tool.digest({k:v for k,v in wrong.items() if k!='result_hash'})
    with pytest.raises(ValueError):tool.persist(tmp_path,wrong)
