from copy import deepcopy
import json
from decimal import localcontext

import pytest

from services.data import kraken_account as account

NOW=1800000000000


def observations():
    balances={'error':[],'result':{'ZUSD':{'balance':'1000.00000001','hold_trade':'250'},
        'AAPLx.T':{'balance':'2.123456789012345678','hold_trade':'0.1'}}}
    orders={'error':[],'result':{'open':{'ORDER-1':{'status':'open','vol':'1.25','vol_exec':'0.25',
        'descr':{'pair':'AAPLxUSD','type':'buy','ordertype':'limit','price':'250','price2':'0','leverage':'none'}}}}}
    return [{'endpoint':kind,'params':{'rebase_multiplier':'base'},'requested_at_ms':NOW+i*100,
             'received_at_ms':NOW+i*100+50,'body':json.dumps(value),'account_ref_sha256':'a'*64}
            for i,(kind,value) in enumerate([('BalanceEx',balances),('OpenOrders',orders),('BalanceEx',balances),('OpenOrders',orders)])]


def test_existing_holdings_locked_amounts_and_remaining_orders_are_exact():
    with localcontext() as ctx:
        ctx.prec=6
        r=account.assess(observations(),as_of_ms=NOW+500)
    assert r['status']=='OBSERVED_STABLE'
    assert r['balances']['AAPLx.T']['balance']=='2.123456789012345678'
    assert r['balances']['ZUSD']['available']=='750.00000001'
    assert r['open_orders'][0]['remaining']=='1'
    assert r['open_orders'][0]['canonical_state']=='PARTIALLY_FILLED'
    assert r['cost_basis_status']=='UNKNOWN' and not r['submission_enabled']
    assert r['atomic_snapshot'] is False and r['quantity_basis']=='base'


def test_changing_account_is_reported_unresolved_not_empty():
    reads=observations();p=json.loads(reads[2]['body']);p['result']['ZUSD']['balance']='900';reads[2]['body']=json.dumps(p)
    r=account.assess(reads,as_of_ms=NOW+500)
    assert r['status']=='UNRESOLVED' and 'BALANCES_CHANGED' in r['issues']
    assert r['balances']['ZUSD']['balance']=='900'


@pytest.mark.parametrize('bad',['identity','basis','future','missing','bad_volume','error','duplicate'])
def test_rejects_incomplete_or_ambiguous_account_evidence(bad):
    reads=observations()
    if bad=='identity':reads[2]['account_ref_sha256']='b'*64
    if bad=='basis':reads[2]['params']['rebase_multiplier']='rebased'
    if bad=='future':reads[-1]['received_at_ms']=NOW+501
    if bad=='missing':reads.pop()
    if bad=='bad_volume':reads[-1]['body']=reads[-1]['body'].replace('"0.25"','"9"')
    if bad=='error':reads[-1]['body']='{"error":["denied"],"result":{}}'
    if bad=='duplicate':reads[0]['body']='{"error":[],"error":["denied"],"result":{}}'
    with pytest.raises(ValueError):account.assess(reads,as_of_ms=NOW+500)


def test_stale_observation_and_borrowing_cannot_be_treated_as_ready():
    reads=observations()
    assert account.assess(reads,as_of_ms=NOW+200000)['status']=='STALE'
    for i in (0,2):
        p=json.loads(reads[i]['body']);p['result']['ZUSD'].update(credit='500',credit_used='100');reads[i]['body']=json.dumps(p)
    r=account.assess(reads,as_of_ms=NOW+500)
    assert r['balances']['ZUSD']['available']=='1150.00000001'
    assert r['status']=='UNRESOLVED' and 'CREDIT_EXPOSURE_PRESENT' in r['issues']
