"""Hand-computed queue and cancellation races; no PnL or private venue claims."""
from decimal import Decimal as D
import pytest

from services.execution.passive_queue import Depth, Trade, Action, replay_queue


def book(t=0,bids=(('99','3'),('98','4')),asks=(('101','2'),('102','5')),bootstrap=True):
    return Depth(t,tuple((D(p),D(q)) for p,q in bids),tuple((D(p),D(q)) for p,q in asks),bootstrap)


def order(name='a',t=10,quantity='1',price='99',side='BUY',expires=None):
    return Action(t,'ORDER',name,side,D(quantity),D(price),expires)


def trade(t=20,quantity='4',price='99',side='BUY',identity=1):
    return Trade(t,identity,D(price),D(quantity),side,t+7)


def run(events,actions,**kwargs):
    return replay_queue(tuple(events),tuple(actions),start_ms=0,end_ms=100,
                        bid_floor=D('98'),ask_ceiling=D('102'),**kwargs)


def test_shared_trade_volume_and_price_time_fifo_partial_fills():
    r=run([book(),trade(quantity='4.5')],[order(),order('b')])
    assert [(e['order_id'],e['quantity']) for e in r['fills']]==[('a','1'),('b','0.5')]
    assert r['orders'][1]['remaining_quantity']=='0.5'
    assert r['orders'][1]['censored'] is True
    assert r['orders'][0]['censored'] is False
    assert r['fills'][0]['venue_time_ms']==20 and r['fills'][0]['client_receive_ms']==45


def test_depth_change_cannot_fill_and_trade_is_not_subtracted_twice():
    r=run([book(),trade(quantity='2'),book(20,bids=(('99','1'),),asks=(),bootstrap=False),
           trade(30,quantity='1.5',identity=2)],[order()])
    assert len(r['fills'])==1 and r['fills'][0]['quantity']=='0.5'
    assert r['fills'][0]['venue_time_ms']==30


def test_cancellations_remove_external_tail_before_advancing_ahead_and_additions_join_tail():
    r=run([book(),book(15,bids=(('99','5'),),asks=(),bootstrap=False),
           book(20,bids=(('99','3'),),asks=(),bootstrap=False),trade(30,quantity='3.5')],[order()])
    assert r['fills'][0]['quantity']=='0.5'


def test_venue_cancel_arrival_not_client_send_controls_the_race():
    actions=[order(),Action(15,'CANCEL','a')]
    immediate=run([book(),trade()],actions,cancel_latency_ms=0)
    late=run([book(),trade()],actions,cancel_latency_ms=10)
    assert not immediate['fills'] and immediate['orders'][0]['status']=='CANCELLED'
    assert late['orders'][0]['status']=='FILLED'
    assert any(e['status']=='CANCEL_REJECTED' for e in late['events'])
    assert any(e['status']=='CANCEL_SENT' and e['client_time_ms']==15 for e in late['events'])


def test_same_time_trade_precedes_cancel_and_order_arrival_follows_market():
    r=run([book(),trade()],[order(),Action(20,'CANCEL','a')])
    assert r['orders'][0]['status']=='FILLED'
    late=run([book(),trade()],[order(t=20)])
    assert not late['fills']


def test_post_only_crossing_unknown_coverage_and_stale_book_are_explicit_rejections():
    r=run([book()],[order('cross',price='101'),order('unknown',price='97')])
    assert [o['reason'] for o in r['orders']]==['POST_ONLY_WOULD_CROSS','OUTSIDE_KNOWN_BOOK_RANGE']
    stale=run([book()],[order(t=50)],max_book_age_ms=20)
    assert stale['orders'][0]['reason']=='STALE_BOOK'


def test_trade_through_shares_quantity_across_better_own_prices():
    r=run([book(),trade(quantity='0.75',price='98')],[order('better',price='100',quantity='0.5'),order()])
    assert [(f['order_id'],f['quantity'],f['price']) for f in r['fills']]==[
        ('better','0.5','100'),('a','0.25','99')]


def test_sell_side_and_latency_and_expiry():
    r=run([book(),trade(quantity='3',price='101',side='SELL')],[order(price='101',side='SELL')])
    assert r['orders'][0]['status']=='FILLED'
    expired=run([book(),trade()],[order(expires=20)])
    assert not expired['fills'] and expired['orders'][0]['status']=='EXPIRED'
    delayed=run([book(),trade()],[order()],order_latency_ms=20)
    assert not delayed['fills']


def test_cancel_before_delayed_order_arrives_and_right_censored_send():
    r=run([book()],[order(),Action(12,'CANCEL','a')],order_latency_ms=20)
    assert r['orders'][0]['status']=='OPEN'
    assert any(e['status']=='CANCEL_REJECTED' for e in r['events'])
    pending=run([book()],[order(t=95)],order_latency_ms=20)
    assert pending['orders'][0]['status']=='SENT' and pending['orders'][0]['censored']


@pytest.mark.parametrize('value',[True,-1,1001])
def test_unbounded_latencies_reject(value):
    with pytest.raises(ValueError):run([book()],[order()],order_latency_ms=value)


def test_duplicate_identity_or_nonfinite_quantity_cannot_replay_twice():
    with pytest.raises(ValueError):run([book(),trade(),trade(30)],[order()])
    with pytest.raises(ValueError):order(quantity='NaN')
    with pytest.raises(ValueError):run([book()],[order(),order()])


def test_market_suffix_cannot_change_prior_order_acknowledgement():
    a=run([book(),trade(50)],[order()]);b=run([book(),trade(50,quantity='1')],[order()])
    assert [e for e in a['events'] if e['venue_time_ms']==10]==[e for e in b['events'] if e['venue_time_ms']==10]
    assert a['model']=='CANCEL_TAIL_SHARED_VOLUME_V1'
    assert a['economic_eligible'] is False and a['ledger_applied'] is False


def test_own_crossing_orders_require_a_self_trade_policy_and_are_not_silently_queued():
    r=run([book()],[order('buy',price='100'),order('sell',price='100',side='SELL')])
    assert r['orders'][1]['reason']=='SELF_CROSS_POLICY_UNMODELED'
    assert r['orders'][0]['status']=='OPEN'


def test_trades_share_budget_even_after_cancellations_and_multiple_price_levels():
    from random import Random
    random=Random(8246)
    for _ in range(25):
        quantities=[str(random.randint(1,30)/10) for _ in range(4)]
        market=[book(),*(trade(20+i*10,quantity=q,identity=i) for i,q in enumerate(quantities))]
        result=run(market,[order('a',quantity='3'),order('b',price='100',quantity='3'),Action(35,'CANCEL','a')])
        for i,q in enumerate(quantities):
            total=sum((D(f['quantity']) for f in result['fills'] if f['source_trade_id']==i),D(0))
            assert total<=D(q)
        for o in result['orders']:
            assert D(o['filled_quantity'])+D(o['remaining_quantity'])==D(o['quantity'])
