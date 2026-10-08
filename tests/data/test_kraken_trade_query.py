import json
import pytest
from services.data import kraken_trade_query as trades
from services.data.kraken_order_query import assess
from tests.data.test_kraken_order_query import read, NOW


def trade_body():
    return {'error':[],'result':{'TRADE-1':{'ordertxid':'ORDER-1','pair':'AAPLxUSD','type':'buy',
        'ordertype':'limit','time':'1799999999.123456','vol':'0.4','price':'100','cost':'40',
        'fee':'0.04','margin':'0'}}}


def observation(data=None):
    return {'endpoint':'QueryTrades','params':{'txid':'TRADE-1','trades':'false','rebase_multiplier':'base'},
        'body':json.dumps(data or trade_body()),'account_ref_sha256':'a'*64,
        'requested_at_ms':NOW+30,'received_at_ms':NOW+40}


def test_exact_individual_trades_match_order_totals_without_cash_claim():
    report=trades.assess([observation()],orders=assess(read(),as_of_ms=NOW+30),as_of_ms=NOW+50)
    assert report['status']=='TRADE_TOTALS_MATCHED'
    assert report['trades'][0]['time_seconds']=='1799999999.123456'
    assert report['cash_reconciled'] is False and report['fee_asset_status']=='UNKNOWN'


@pytest.mark.parametrize('case',['missing','wrong_order','wrong_side','cost','fee','quantity','margin','future','duplicate'])
def test_bad_trade_evidence_blocks_reconciliation(case):
    p=trade_body();row=p['result']['TRADE-1']
    if case=='missing':p['result']={}
    if case=='wrong_order':row['ordertxid']='ORDER-2'
    if case=='wrong_side':row['type']='sell'
    if case=='cost':row['cost']='41'
    if case=='fee':row['fee']='0.05'
    if case=='quantity':row['vol']='0.5'
    if case=='margin':row['margin']='1'
    if case=='future':row['time']='1800000001'
    reads=[observation(p)]
    if case=='duplicate':reads*=2
    with pytest.raises(ValueError):trades.assess(reads,orders=assess(read(),as_of_ms=NOW+30),as_of_ms=NOW+50)
