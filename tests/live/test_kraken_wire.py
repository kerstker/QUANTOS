from urllib.parse import parse_qs
import pytest
from live.exchange import kraken_wire as wire
from tests.tools.test_kraken_public_capture import capture

NOW=1800000000000


def order():
    return {'instrument_id':'KRAKEN:tokenized_asset:AAPLxUSD','side':'buy','volume':'0.12345678',
        'limit_price':'100.01','client_order_id':'0123456789abcdef0123456789abcdef','deadline_ms':NOW+10000}


def test_exact_wire_values_and_fixed_spot_limits():
    result=wire.prepare(order(),spec=capture()[0]['instrument'],now_ms=NOW,nonce=12345)
    fields=parse_qs(result['body'].decode())
    assert fields['volume']==['0.12345678'] and fields['price']==['100.01']
    assert fields['asset_class']==['tokenized_asset']
    assert fields['timeinforce']==['IOC'] and fields['oflags']==['fciq']
    assert fields['validate']==['true'] and 'rebase_multiplier' not in fields
    assert result['execution_eligible'] is False


def test_wire_bytes_preserve_form_encoding_and_decimal_lexemes():
    import hashlib
    now=1700000000000;o=order();o['deadline_ms']=now+10000
    result=wire.prepare(o,spec=capture()[0]['instrument'],now_ms=now,nonce=12345)
    expected=(b'nonce=12345&ordertype=limit&type=buy&volume=0.12345678&pair=AAPLxUSD'
        b'&price=100.01&cl_ord_id=0123456789abcdef0123456789abcdef&timeinforce=IOC'
        b'&oflags=fciq&deadline=2023-11-14T22%3A13%3A30.000Z&validate=true&asset_class=tokenized_asset')
    assert result['body']==expected and result['body_sha256']==hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize('case',['float','precision','min_cost','tick','identity','deadline','extra','client_id'])
def test_invalid_or_unbounded_order_is_rejected(case):
    o=order()
    if case=='float':o['volume']=0.12345678
    if case=='precision':o['volume']='0.123456789'
    if case=='min_cost':o['volume']='0.00000001'
    if case=='tick':o['limit_price']='100.001'
    if case=='identity':o['instrument_id']='KRAKEN:currency:AAPLxUSD'
    if case=='deadline':o['deadline_ms']=NOW+61000
    if case=='extra':o['leverage']='5'
    if case=='client_id':o['client_order_id']='duplicate optional id'
    with pytest.raises(ValueError):wire.prepare(o,spec=capture()[0]['instrument'],now_ms=NOW,nonce=12345)
