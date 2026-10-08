"""Synthetic IOC expiry through original issuer, wire, receipts and OMS stores."""
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from live.execution import demo_issuer as issuer, demo_pre_submit as prep, demo_prepared_execution as sender
from live.execution.demo_batch_risk import _digest
from live.exchange import binance_demo_exact as transport
from services.research.demo_user_stream import normalize_message
from services.validation.demo_submission import GOLD_BATCH_SCHEMA
from tests.live.test_demo_pre_submit import NOW
from tests.research.test_binance_order_receipts import message


def expire(*,tmp_path,monkeypatch,args,held,order,risk,handoff,policy,registry,private,log,stream,events):
    from live.execution.demo_wire_protocol import outcome_for_query
    origin=deepcopy(held)
    origin['observed_at_ms']-=500;origin['completed_at_ms']-=500
    for read in origin['reads']:
        read['requested_at_ms']-=500;read['received_at_ms']-=500
        if read['kind'].startswith('clock'):read['data']['serverTime']-=500
        elif read['kind'] in ('book','mark'):read['data']['time']-=500
    # The first SELL has a higher limit and expires; the later independent
    # proposal uses the current lower limit. Same-price rapid duplicates remain
    # prohibited by the original risk gate.
    order=dict(order,newClientOrderId=order['newClientOrderId'][:-1]+'0',
        price=str(Decimal(order['price'])+Decimal('0.1')))
    risk=deepcopy(risk);risk['now_ms']=NOW+1500;risk['market']['observed_at_ms']=NOW+1500
    values=dict(args,origin=origin,risk_inputs=risk,orders=[order],now_ms=NOW+1500)
    proposal=prep.proposal(**values)
    grant=issuer.issue_submission(dict(handoff,proposal=proposal,proposal_sha256=_digest(proposal)),
        policy=policy,registry=registry,key=private,store=log,now_ms=NOW+1501)
    prepared=prep.prepare(json.dumps(grant['envelope']).encode(),verification_now_ms=NOW+1501,
        evidence_directory=tmp_path/'executor/evidence',**values)
    monkeypatch.setattr(sender,'now_ms',lambda:NOW+1502)
    sent=[]
    def http(body,headers):
        sent.append(body)
        return 200,json.dumps({'symbol':'XAUUSDT','clientOrderId':order['newClientOrderId'],'orderId':125}).encode()
    monkeypatch.setattr(transport,'_http_once',http)
    execution={k:values[k] for k in ('dispatch','recovery','oms','risk_inputs','mandate','attestations')}
    assert sender.execute(prepared,**execution)['status']=='WIRE_ATTEMPTS_RECORDED'
    with pytest.raises(sender.DemoExecutionStopped):sender.execute(prepared,**execution)
    assert len(sent)==1
    event=message(NOW+1510,'EXPIRED')
    event['o'].update(s='XAUUSDT',c=order['newClientOrderId'],i=125,q=order['quantity'],p=order['price'],
        ap='0',S='SELL',f='IOC',m=False,R=True,er='2',rp='0',x='EXPIRED')
    stream.record('MESSAGE',at_ms=NOW+1512,message=normalize_message(json.dumps(event)))
    query={'symbol':'XAUUSDT','clientOrderId':order['newClientOrderId'],'orderId':125,'status':'EXPIRED',
        'side':'SELL','type':'LIMIT','timeInForce':'IOC','origQty':order['quantity'],'executedQty':'0',
        'price':order['price'],'avgPrice':'0','reduceOnly':True,'positionSide':'BOTH',
        'time':NOW+1502,'updateTime':NOW+1510}
    args['dispatch'].record_query_result(reservation_sha256=prepared['reservation_sha256'],
        client_order_id=order['newClientOrderId'],requested_at_ms=NOW+1530,now_ms=NOW+1531,
        outcome=outcome_for_query(order,200,json.dumps(query).encode(),schema=GOLD_BATCH_SCHEMA))
    monkeypatch.setattr(sender,'now_ms',lambda:NOW+1900)
    result=sender.reconcile_reserved(prepared,**{k:args[k] for k in ('dispatch','recovery','oms')},
        event_store=events,stream=stream,start_ms=NOW+1501,end_ms=NOW+1800,source_commit='a'*40)
    assert result['status']=='OMS_TERMINAL_PROJECTED',result
    assert len(args['oms'].fills())==1 and not args['recovery'].pending()
    assert args['recovery'].latest(order['newClientOrderId']).observed_status=='EXPIRED'
    return prepared
