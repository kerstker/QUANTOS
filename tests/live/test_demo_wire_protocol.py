import json
from copy import deepcopy

import pytest

from live.execution.demo_wire_protocol import outcome_for_response,validate_outcome
from tests.live.test_demo_submission_verification import material


def receipt():
    order=material()[2][0]
    return order,{'symbol':order['symbol'],'clientOrderId':order['newClientOrderId'],'orderId':9007199254740993,
        'origQty':order['quantity'],'price':order['price'],'executedQty':'0','status':'NEW',
        'side':order['side'],'positionSide':'BOTH','reduceOnly':False,'timeInForce':order['timeInForce'],'type':'LIMIT'}


def test_ack_preserves_exact_order_id_and_does_not_retain_untrusted_error_text():
    order,raw=receipt();raw['msg']='untrusted echoed credential';outcome=outcome_for_response(order,200,json.dumps(raw).encode())
    assert outcome['status']=='ACKNOWLEDGED' and outcome['order']['orderId']=='9007199254740993'
    assert 'msg' not in outcome['order'] and validate_outcome(outcome,order)==outcome


@pytest.mark.parametrize('field,value',[('clientOrderId','QDdifferent0001'),('symbol','BTCUSDT'),('origQty','0.003'),
    ('price',60000),('reduceOnly',0),('executedQty','0.003'),('orderId',1.5),('status','FILLED'),('time',False)])
def test_inconsistent_identity_quantity_state_and_types_remain_unknown(field,value):
    order,raw=receipt();raw[field]=value
    result=outcome_for_response(order,200,json.dumps(raw).encode())
    assert result['status']=='UNKNOWN' and result['order'] is None


def test_redirect_and_errors_are_unknown_and_oversize_has_no_full_response_hash():
    order,raw=receipt()
    for status in (302,400,408,429,503):
        result=outcome_for_response(order,status,b'{"code":-2013,"msg":"private detail"}')
        assert result['status']=='UNKNOWN' and result['code']==-2013
        assert 'private detail' not in json.dumps(result)
    result=outcome_for_response(order,200,b'x'*65537)
    assert result['reason']=='RESPONSE_TOO_LARGE' and result['response_sha256'] is None


def test_duplicate_json_keys_and_numeric_id_boolean_do_not_create_receipts():
    order,raw=receipt()
    assert outcome_for_response(order,200,b'{"orderId":1,"orderId":2}')['status']=='UNKNOWN'
    raw['orderId']=True
    assert outcome_for_response(order,200,json.dumps(raw).encode())['status']=='UNKNOWN'
