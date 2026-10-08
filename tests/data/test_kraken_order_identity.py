import json
import pytest

from services.data.kraken_order_identity import match
from tests.data.test_kraken_order_query import read, NOW
from tests.live.test_kraken_wire import order
from tests.tools.test_kraken_public_capture import capture


def inputs():
    draft=order();source=read();raw=json.loads(source['body']);row=raw['result']['ORDER-1']
    row.update(cl_ord_id=draft['client_order_id'],opentm='1800000000.01',vol=draft['volume'],vol_exec='0.1')
    row['descr'].update(price=draft['limit_price'])
    source['body']=json.dumps(raw)
    return source,[draft],[capture()[0]['instrument']]


def check(source,drafts,specs):
    return match(source,drafts,specs,account_ref='a'*64,not_before_ms=NOW,as_of_ms=NOW+30)


def test_observed_identity_matches_but_does_not_release_or_verify_units():
    source,drafts,specs=inputs()
    result=check(source,drafts,specs)
    assert result['status']=='OBSERVED_ORDER_IDENTITIES_MATCHED'
    assert result['orders'][0]['order_id']=='ORDER-1'
    assert result['orders'][0]['client_order_id']==drafts[0]['client_order_id']
    assert result['release_allowed'] is False and result['submission_enabled'] is False


@pytest.mark.parametrize('change',['missing_id','id','pair','side','volume','price','missing_time','old','late','duplicate'])
def test_unrelated_amended_or_reused_order_cannot_match(change):
    source,drafts,specs=inputs();raw=json.loads(source['body']);row=raw['result']['ORDER-1']
    if change=='missing_id':row.pop('cl_ord_id')
    if change=='id':row['cl_ord_id']='b'*32
    if change=='pair':row['descr']['pair']='NVDAxUSD'
    if change=='side':row['descr']['type']='sell'
    if change=='volume':row['vol']='999'
    if change=='price':row['descr']['price']='1'
    if change=='missing_time':row.pop('opentm')
    if change=='old':row['opentm']='1799999999'
    if change=='late':drafts[0]['deadline_ms']=NOW+5
    if change=='duplicate':
        raw['result']['ORDER-2']=dict(row);source['params']['txid']='ORDER-1,ORDER-2'
    source['body']=json.dumps(raw)
    assert check(source,drafts,specs)['status']=='UNRESOLVED'


def test_uuid_presentation_and_decimal_representation_do_not_change_identity():
    source,drafts,specs=inputs();raw=json.loads(source['body']);row=raw['result']['ORDER-1']
    from uuid import UUID
    row['cl_ord_id']=str(UUID(drafts[0]['client_order_id'])).upper()
    # Preserve the numeric value independently of a particular fixture format.
    raw['result']['ORDER-1']['descr']['price']=drafts[0]['limit_price']+'.0' if '.' not in drafts[0]['limit_price'] else drafts[0]['limit_price']+'0'
    source['body']=json.dumps(raw)
    assert check(source,drafts,specs)['status']=='OBSERVED_ORDER_IDENTITIES_MATCHED'


def test_other_account_is_rejected():
    source,drafts,specs=inputs();source['account_ref_sha256']='b'*64
    with pytest.raises(ValueError):check(source,drafts,specs)
