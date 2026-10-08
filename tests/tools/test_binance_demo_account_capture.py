"""Explicit Demo GET capture, no order or account configuration mutations."""
import json
import pytest

from tools import binance_demo_account_capture as tool
from tests.research.test_demo_account_baseline import NOW, payload
from tests.tools.test_binance_demo_private_check import credentials


def test_default_does_not_touch_credentials_or_network(tmp_path):
    def forbidden(*a,**k):pytest.fail('unexpected private request')
    result=tool.collect(credential_file=tmp_path/'missing',get=forbidden,clock_ms=lambda:NOW)
    assert result.status=='NOT_REQUESTED' and result.reads==[]


def transport(calls):
    kinds=iter(('config_before','account_before','symbols_before','positions','orders','symbols_after','account_after','config_after'))
    def get(path,*,params,headers):
        calls.append((path,params,headers))
        if path=='/fapi/v1/time':return {'serverTime':NOW},'0'*64
        return payload(next(kinds)),'a'*64
    return get


def test_complete_capture_binds_each_read_interval_and_fixed_account_identity_fields(tmp_path):
    calls=[]
    result=tool.collect(credential_file=credentials(tmp_path),operator_read=True,get=transport(calls),clock_ms=lambda:NOW)
    assert result.status=='CAPTURED' and len(result.reads)==8
    assert len(calls)==9 and calls[0][2]=={}
    assert all('signature=' in query and headers=={'X-MBX-APIKEY':'a'*64} for _,query,headers in calls[1:])
    assert all(x not in result.model_dump_json() for x in ('b'*64,'X-MBX-APIKEY','signature='))
    assert all(r.received_at_ms==NOW for r in result.reads)


def test_partial_failure_retains_prior_observations_and_stops(tmp_path):
    calls=[]; good=transport(calls)
    def get(path,**kwargs):
        if path=='/fapi/v3/positionRisk':raise RuntimeError('secret from private query')
        return good(path,**kwargs)
    result=tool.collect(credential_file=credentials(tmp_path),operator_read=True,get=get,clock_ms=lambda:NOW)
    assert result.status=='FAILED' and len(result.reads)==3
    assert result.reason=='REQUEST_FAILED' and 'secret' not in result.model_dump_json()


def test_transport_rejects_mutation_paths_and_mainnet():
    for path in ('/fapi/v1/order','https://fapi.binance.com/fapi/v3/account','/fapi/v1/listenKey'):
        with pytest.raises(tool.ProbeFailure):tool.get_response(path,params='',headers={})


def test_capture_storage_rejects_pointer_escape_and_preserves_history(tmp_path):
    from services.research.demo_account_baseline import save_capture,load_capture
    result=tool.collect(credential_file=tmp_path/'missing',clock_ms=lambda:NOW)
    directory=tmp_path/'observations';save_capture(directory,result)
    before={p.name:p.read_bytes() for p in directory.iterdir()}
    assert load_capture(directory)==result
    save_capture(directory,result)
    assert before=={p.name:p.read_bytes() for p in directory.iterdir()}
    (directory/'latest.json').write_text(json.dumps({'sha256':'../../key/demo'}))
    with pytest.raises(ValueError):load_capture(directory)
