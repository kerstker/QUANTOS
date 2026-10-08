from copy import deepcopy
from services.research.gold_trade_review import review
from tests.research.test_gold_trade_observation import document


def inputs():
    fills=document()
    order=dict(symbol='XAUUSDT',orderId=456,side='BUY',positionSide='BOTH',status='FILLED',
               type='LIMIT',origQty='0.01',executedQty='0.01',price='4000.1',time=1100,updateTime=1500)
    fee=dict(symbol='XAUUSDT',incomeType='COMMISSION',income='-0.0200005',asset='USDT',
             time=1500,tranId=1,tradeId='123')
    return fills,[order],[fee]


def test_order_and_native_cash_link_match_without_becoming_account_pnl():
    f,o,c=inputs();r=review(f,o,[],c)
    assert r['orders'][0]['quantity_check']=='MATCHING_OBSERVED_QUANTITY'
    assert r['trades'][0]['commission_check']=='MATCHING_REPORTED_CASH'
    assert r['net_pnl'] is None and r['order_lifecycle_complete'] is False
    assert r['trades'][0]['realized_pnl_check']=='NOT_OBSERVED'


def test_equal_aggregate_commission_does_not_hide_swapped_trade_ids():
    f,o,c=inputs()
    f['fills'].append(dict(f['fills'][0],trade_id='124',commission='0.04'))
    o[0].update(origQty='0.02',executedQty='0.02')
    c.append(dict(c[0],tranId=2,tradeId='124',income='-0.04'))
    c[0]['tradeId'],c[1]['tradeId']='124','123'
    r=review(f,o,[],c)
    assert all(t['commission_check']=='MISMATCH' for t in r['trades'])


def test_missing_order_and_foreign_cash_are_not_silently_counted():
    f,o,c=inputs();c[0]['symbol']='BTCUSDT'
    r=review(f,[],[],c)
    assert r['trades'][0]['order_check']=='NOT_OBSERVED'
    assert r['trades'][0]['commission_check']=='NOT_OBSERVED'
    assert r['other_market_income_count']==1


def test_duplicates_do_not_inflate_observed_fills_and_partial_cancel_is_visible():
    f,o,c=inputs();f['fills'].append(deepcopy(f['fills'][0]));c.append(deepcopy(c[0]))
    o[0].update(status='CANCELED',origQty='0.03')
    r=review(f,o,[],c)
    assert r['orders'][0]['observed_fill_quantity']=='0.01'
    assert r['orders'][0]['unfilled_quantity']=='0.02'
    assert r['orders'][0]['status']=='CANCELED'


def test_missing_child_of_triggered_algo_is_explicit():
    f,o,c=inputs()
    a=dict(symbol='XAUUSDT',algoId=99,side='BUY',positionSide='BOTH',algoStatus='TRIGGERED',
           orderType='STOP_MARKET',quantity='0.01',price='0',triggerPrice='4000',createTime=1100,
           updateTime=1500,actualOrderId='789')
    r=review(f,o,[a],c)
    assert r['conditional_orders'][0]['child_check']=='NOT_OBSERVED'
