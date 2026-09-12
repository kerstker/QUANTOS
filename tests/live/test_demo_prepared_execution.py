"""Only exact independently verified Demo reservations reach one wire attempt."""
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from live.execution import demo_pre_submit as prep
from live.execution import demo_prepared_execution as executor
from live.exchange import binance_demo_exact as transport
from tests.live.test_demo_pre_submit import setup,signed,stores,NOW


def ready(tmp_path,monkeypatch):
    private,envelope,values=setup(tmp_path,monkeypatch)
    ds,rs,oms=stores(tmp_path)
    p=prep.proposal(**values,dispatch=ds,recovery=rs,oms=oms)
    batch=prep.prepare(signed(private,envelope,p),**values,dispatch=ds,recovery=rs,oms=oms,evidence_directory=tmp_path/'evidence')
    monkeypatch.setattr(executor,'now_ms',lambda:NOW+1)
    monkeypatch.setattr(executor,'_require_state_custody',lambda *a,**k:None)
    monkeypatch.setattr(transport,'_credentials',lambda account_ref:('isolated-fixture-key','isolated-fixture-secret'))
    args=dict(dispatch=ds,recovery=rs,oms=oms,risk_inputs=values['risk_inputs'],mandate=values['mandate'],attestations=values['attestations'])
    return batch,args


def test_exact_bytes_have_durable_claim_before_transport_and_ack_is_not_a_fill(tmp_path,monkeypatch):
    batch,args=ready(tmp_path,monkeypatch);calls=[]
    def http(body,headers):
        assert len(args['dispatch'].wire_snapshot())==1
        assert len(args['recovery'].pending())==1 and args['oms'].orders()[0]['status']=='PENDING_NEW'
        query=parse_qs(body.decode());assert query['quantity']==['0.002'] and query['price']==['60000']
        assert query['reduceOnly']==['false'] and query['newOrderRespType']==['ACK']
        assert 'signature' in query and headers['X-MBX-APIKEY']=='isolated-fixture-key'
        calls.append(query)
        return 200,json.dumps({'symbol':'BTCUSDC','clientOrderId':batch['orders'][0]['newClientOrderId'],'orderId':9007199254740993}).encode()
    monkeypatch.setattr(transport,'_http_once',http)
    try:
        report=executor.execute(batch,**args)
        assert report['status']=='WIRE_ATTEMPTS_RECORDED' and len(calls)==1
        assert report['orders'][0]['outcome']['order']['orderId']=='9007199254740993'
        assert args['oms'].fills()==[] and len(args['recovery'].pending())==1
        with pytest.raises(executor.DemoExecutionStopped):executor.execute(batch,**args)
        assert len(calls)==1
    finally:
        for name in ('dispatch','recovery','oms'):args[name].close()


@pytest.mark.parametrize('failure',['expired','revoked','kill','risk','proof','intent_missing','prepared_mismatch'])
def test_final_gate_changes_stop_before_secret_or_network(tmp_path,monkeypatch,failure):
    batch,args=ready(tmp_path,monkeypatch);calls=[]
    monkeypatch.setattr(transport,'_credentials',lambda ref: calls.append('credentials'))
    monkeypatch.setattr(transport,'_http_once',lambda *a:calls.append('http'))
    if failure=='expired':monkeypatch.setattr(executor,'now_ms',lambda:NOW+30000)
    elif failure=='revoked':
        monkeypatch.setattr(executor.demo_authority,'load_registry_for_executor',lambda:(_ for _ in ()).throw(ValueError('revoked fixture')))
    elif failure=='kill':args['risk_inputs']['kill_switch']._state=None
    elif failure=='risk':args['risk_inputs']['account']['day_pnl']='-21'
    elif failure=='proof':
        path=Path(batch['proposal_path']);raw=json.loads(path.read_text());raw['orders'][0]['quantity']='0.003';path.write_text(json.dumps(raw))
    elif failure=='prepared_mismatch':batch['orders'][0]['quantity']='0.003'
    else:args['oms']._conn.execute('DELETE FROM live_orders');args['oms']._conn.commit()
    try:
        with pytest.raises(executor.DemoExecutionStopped):executor.execute(batch,**args)
        assert calls==[] and args['dispatch'].wire_snapshot()==[]
    finally:
        for name in ('dispatch','recovery','oms'):args[name].close()


@pytest.mark.parametrize('mode',['timeout','503','lost_result','process_crash'])
def test_uncertainty_and_crash_never_trigger_second_post(tmp_path,monkeypatch,mode):
    batch,args=ready(tmp_path,monkeypatch);calls=[]
    def http(body,headers):
        calls.append(1)
        if mode=='timeout':raise TimeoutError('DO_NOT_LOG_PRIVATE_RESPONSE')
        if mode=='process_crash':raise SystemExit(99)
        return 503,b'{"msg":"untrusted private response"}'
    monkeypatch.setattr(transport,'_http_once',http)
    if mode=='lost_result':
        monkeypatch.setattr(args['dispatch'],'record_wire_result',lambda **k:(_ for _ in ()).throw(OSError('disk unavailable')))
    try:
        try:
            report=executor.execute(batch,**args)
            assert report['status']=='QUERY_RECOVERY_REQUIRED'
        except (executor.DemoExecutionStopped,SystemExit):pass
        assert len(calls)==1 and len(args['recovery'].pending())==1
        with pytest.raises(executor.DemoExecutionStopped):executor.execute(batch,**args)
        assert len(calls)==1
    finally:
        for name in ('dispatch','recovery','oms'):args[name].close()
