import json
import pytest
from services.data import kraken_order_query as query

NOW=1800000000000


def body(status='open',filled='0.4'):
    return json.dumps({'error':[],'result':{'ORDER-1':{'status':status,'vol':'1','vol_exec':filled,
        'cost':'40','fee':'0.04','descr':{'pair':'AAPLxUSD','type':'buy','ordertype':'limit','leverage':'none'},
        'trades':['TRADE-1']}}})


def read(payload=None):
    return {'endpoint':'QueryOrders','params':{'txid':'ORDER-1','trades':'true','rebase_multiplier':'base'},
        'body':payload or body(),'account_ref_sha256':'a'*64,'requested_at_ms':NOW,'received_at_ms':NOW+20}


def test_partial_and_cancelled_orders_preserve_exact_totals_without_inventing_fills():
    report=query.assess(read(body('canceled')),as_of_ms=NOW+30)
    row=report['orders'][0]
    assert row['canonical_state']=='CANCELLED' and row['filled']=='0.4'
    assert row['cumulative_cost']=='40' and row['cumulative_fee']=='0.04'
    assert row['fee_asset_status']=='UNKNOWN'
    assert report['cash_reconciled'] is False and report['submission_enabled'] is False


@pytest.mark.parametrize('case',['missing','basis','future','duplicates'])
def test_incomplete_or_mismatched_query_cannot_close_an_order(case):
    r=read()
    if case=='missing':r['body']='{"error":[],"result":{}}'
    if case=='basis':r['params']['rebase_multiplier']='rebased'
    if case=='future':r['received_at_ms']=NOW+100
    if case=='duplicates':r['params']['txid']='ORDER-1,ORDER-1'
    with pytest.raises(ValueError):query.assess(r,as_of_ms=NOW+30)


def test_restart_progress_does_not_accept_fewer_fills_or_amended_identity():
    from copy import deepcopy
    previous=query.assess(read(),as_of_ms=NOW+30)
    newer=query.assess(read(body('canceled')),as_of_ms=NOW+30)
    query.check_progress(previous,newer)
    for field,value in [('filled','0.1'),('pair','NVDAxUSD'),('cumulative_cost','1'),('trade_ids',[])]:
        changed=deepcopy(newer);changed['orders'][0][field]=value
        with pytest.raises(ValueError):query.check_progress(previous,changed)
