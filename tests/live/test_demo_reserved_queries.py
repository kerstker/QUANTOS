"""Expired or uncertain reservations recover by the original client IDs only."""
import json
from urllib.parse import parse_qs

import pytest

from live.execution import demo_prepared_execution as executor
from live.exchange import binance_demo_exact as transport
from tests.live.test_demo_prepared_execution import ready,NOW


def snapshot(order, **changes):
    value={'symbol':order['symbol'],'clientOrderId':order['newClientOrderId'],'orderId':9007199254740993,
        'origQty':order['quantity'],'price':order['price'],'executedQty':'0.001','status':'PARTIALLY_FILLED',
        'side':order['side'],'positionSide':'BOTH','reduceOnly':False,'timeInForce':order['timeInForce'],'type':'LIMIT',
        'time':NOW+1,'updateTime':NOW+20}
    return value|changes


def test_query_after_grant_expiry_learns_id_and_partial_quantity_without_resending(tmp_path,monkeypatch):
    batch,args=ready(tmp_path,monkeypatch);calls=[]
    monkeypatch.setattr(executor,'now_ms',lambda:NOW+60000)
    def get(path,query,headers):
        calls.append(path)
        if path=='/fapi/v1/time':
            assert not headers and not query
            return 200,json.dumps({'serverTime':NOW+60000}).encode()
        params=parse_qs(query.decode())
        assert path=='/fapi/v1/order' and params['origClientOrderId']==[batch['orders'][0]['newClientOrderId']]
        assert 'orderId' not in params and 'newClientOrderId' not in params
        return 200,json.dumps(snapshot(batch['orders'][0])).encode()
    monkeypatch.setattr(transport,'_http_get_once',get)
    monkeypatch.setattr(transport,'_http_once',lambda *a:pytest.fail('query cannot POST'))
    try:
        result=executor.query_reserved(batch,dispatch=args['dispatch'],recovery=args['recovery'],oms=args['oms'])
        assert result['status']=='ORDER_SNAPSHOTS_OBSERVED' and len(calls)==2
        assert result['orders'][0]['outcome']['status']=='FOUND'
        assert result['orders'][0]['outcome']['order']['executedQty']=='0.001'
        rows=args['dispatch'].wire_snapshot()
        assert len(rows)==1 and rows[0]['kind']=='QUERY_RESULT'
        assert args['oms'].fills()==[] and len(args['recovery'].pending())==1
    finally:
        for name in ('dispatch','recovery','oms'):args[name].close()


@pytest.mark.parametrize('scenario',['not_found','timeout','wrong_identity','missing_quantity','bad_clock','future_order','prior_order'])
def test_query_failure_stays_unknown_and_never_confirms_absence(tmp_path,monkeypatch,scenario):
    batch,args=ready(tmp_path,monkeypatch);calls=[]
    def get(path,query,headers):
        calls.append(path)
        if path=='/fapi/v1/time':return 200,json.dumps({'serverTime':NOW+(10000 if scenario=='bad_clock' else 1)}).encode()
        if scenario=='not_found':return 400,b'{"code":-2013,"msg":"Order does not exist"}'
        if scenario=='timeout':raise TimeoutError('private exception')
        raw=snapshot(batch['orders'][0])
        if scenario=='wrong_identity':raw['clientOrderId']='QDforeign0001'
        if scenario=='missing_quantity':del raw['executedQty']
        if scenario=='future_order':raw['updateTime']=NOW+10000
        if scenario=='prior_order':raw.update(time=NOW-1000,updateTime=NOW-500)
        return 200,json.dumps(raw).encode()
    monkeypatch.setattr(transport,'_http_get_once',get)
    try:
        result=executor.query_reserved(batch,dispatch=args['dispatch'],recovery=args['recovery'],oms=args['oms'])
        assert result['status']=='QUERY_RECOVERY_REQUIRED'
        assert all(r['outcome']['status']=='UNKNOWN' for r in result['orders'])
        assert len(args['recovery'].pending())==1 and args['oms'].fills()==[]
        if scenario=='bad_clock':assert calls==['/fapi/v1/time']
    finally:
        for name in ('dispatch','recovery','oms'):args[name].close()
