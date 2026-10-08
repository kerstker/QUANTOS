from copy import deepcopy
import pytest
from services.research.gold_account_history import normalize_income, reconcile_inventory


def income(**changes):
    return dict(symbol='XAUUSDT',incomeType='COMMISSION',income='-0.02',asset='USDT',
                time=1500,tranId=1,tradeId='7',**changes)


def account(wallet='100',qty='0'):
    return {'assets':[{'asset':'USDT','walletBalance':wallet}],
            'positions':[{'symbol':'XAUUSDT','positionSide':'BOTH','positionAmt':qty}]}


def test_income_native_identity_includes_kind_and_retains_other_markets():
    a=income();b={**a,'incomeType':'FUNDING_FEE','symbol':'BTCUSDT'}
    rows=normalize_income([a,a,b],start_ms=1000,end_ms=2000)
    assert len(rows)==2 and rows[1]['symbol']=='BTCUSDT'
    with pytest.raises(ValueError):normalize_income([a,{**a,'income':'-0.03'}],start_ms=1000,end_ms=2000)


def test_wallet_uses_income_once_and_position_uses_fills():
    from tests.research.test_gold_trade_observation import document
    f=document();f['fills'][0].update(time_ms=1500,quantity='0.01',commission='0.02')
    r=reconcile_inventory(account(),account('99.98','0.01'),f,
        normalize_income([income()],start_ms=1000,end_ms=2000))
    assert r['wallet_status']=='MATCHING_OBSERVED_TOTALS'
    assert r['position_status']=='MATCHING_OBSERVED_TOTALS'
    assert r['history_complete'] is False and r['net_pnl'] is None
    r=reconcile_inventory(account(),account('99.97','0.02'),f,
        normalize_income([income()],start_ms=1000,end_ms=2000))
    assert r['wallet_status']=='MISMATCH' and r['position_status']=='MISMATCH'


def test_missing_other_market_fills_prevents_account_position_claim():
    from tests.research.test_gold_trade_observation import document
    a=account();a['positions'].append({'symbol':'BTCUSDT','positionSide':'BOTH','positionAmt':'1'})
    r=reconcile_inventory(a,a,document(),[])
    assert r['position_status']=='OTHER_MARKETS_UNCHECKED'


def test_order_history_accepts_terminal_states_and_links_algo_to_order():
    from services.research.gold_account_history import normalize_orders
    o={'symbol':'XAUUSDT','orderId':42,'side':'SELL','positionSide':'BOTH','status':'CANCELED',
       'type':'LIMIT','origQty':'1','executedQty':'0','price':'4000','time':1000,'updateTime':1500}
    assert normalize_orders([o],algo=False)[0]['status']=='CANCELED'
    a={'symbol':'XAUUSDT','algoId':12,'side':'SELL','positionSide':'BOTH','algoStatus':'TRIGGERED',
       'orderType':'STOP_MARKET','quantity':'1','price':'0','triggerPrice':'3990','createTime':1000,
       'updateTime':1500,'actualOrderId':'42'}
    assert normalize_orders([a],algo=True)[0]['actualOrderId']=='42'
    with pytest.raises(ValueError):normalize_orders([o,{**o,'executedQty':'1'}],algo=False)
