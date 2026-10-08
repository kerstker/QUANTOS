from tools.btc_factory_research import seal
from tools.cache_io import atomic_write_json
from services.console.api.forward_dashboard import forward_payload


def test_reads_sealed_forward_state_and_rejects_foreign_freeze(tmp_path):
    folder=tmp_path/'data/cache/btc_forward/1';folder.mkdir(parents=True)
    freeze=seal({'source_hash':'a'*64,'frozen_at_ms':1,'fits':{}})
    atomic_write_json(str(folder/'freeze.json'),freeze)
    status=seal({'schema':'QUANTOS_BTC_FORWARD_OBSERVATION_V1','attempt':1,'freeze_hash':freeze['result_hash'],
                 'status':'WAITING_WINDOW','observed_at_ms':100,'models':[],'authoritative':False,'account_pnl_computed':False})
    atomic_write_json(str(folder/'status.json'),status)
    assert len(forward_payload(tmp_path,now_ms=200)['observations'])==1
    status['freeze_hash']='b'*64
    atomic_write_json(str(folder/'status.json'),seal({k:v for k,v in status.items() if k!='result_hash'}))
    result=forward_payload(tmp_path,now_ms=200)
    assert result['observations']==[] and result['errors']
