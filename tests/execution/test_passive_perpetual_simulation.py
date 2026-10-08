"""Passive fills must share the existing canonical accounting and risk rules."""
from dataclasses import replace
from decimal import Decimal as D
import pytest

from tests.execution.test_perpetual_simulation import F,T
from tests.execution.test_perpetual_order_admission import profile
from services.data.binance_execution_tape import PublicTrade
from services.execution.passive_execution_input import BookUpdate,ExecutionTape
from services.execution.perpetual_simulation import (
    OrderIntent,CancelOrder,SimulationFrame,SimulationCostModel,SimulationLimits,
)
from services.execution.perpetual_ledger_v2 import MarkEvent,FundingEvent
from services.execution.passive_perpetual_simulation import run_passive_perpetual_simulation


def tape():
    levels=((D('99'),D('1')),(D('98'),D('3')))
    asks=((D('101'),D('2')),(D('102'),D('3')))
    books=tuple(BookUpdate(T+t,T+t+5,100+t,levels,asks,t==0,f'{t:064x}') for t in (0,30,70))
    trades=(PublicTrade('BTCUSDT',1,1,1,D('99'),D('1.1'),'SELL',T+20,T+21,T+27,'a'*64),
            PublicTrade('BTCUSDT',2,2,2,D('101'),D('2.1'),'BUY',T+60,T+61,T+67,'b'*64),
            PublicTrade('BTCUSDT',3,3,3,D('98'),D('.01'),'SELL',T+70,T+71,T+77,'c'*64))
    trades=tuple(replace(t,normal_quantity=t.quantity) for t in trades)
    return ExecutionTape('BTCUSDT','a'*64,T+5,T+70,books,trades,D('98'),D('102'))


def run(actions,*,market=None,capacity=None,**kwargs):
    kwargs.setdefault('costs',SimulationCostModel(fee_rate=D('.001')))
    kwargs.setdefault('initial_collateral',D('1000'))
    kwargs.setdefault('limits',SimulationLimits(max_single_weight=D('1')))
    kwargs.setdefault('order_rule_profiles',(profile(),))
    return run_passive_perpetual_simulation(
        release_identity={'commit':'f'*40},dataset_identity={'fixture':'synthetic-passive'},
        tape=market or tape(),actions=actions,
        contract_rules=(F['_rule'](),),
        funding_policies=(replace(F['_funding_policy'](),settlement_times=(T+40,)),),
        account_frames=(SimulationFrame(T,market_events=(MarkEvent(T,'BTCUSDT',D('100')),),capacity_snapshots=(capacity or F['_capacity'](),)),
            SimulationFrame(T+40,market_events=(FundingEvent(T+40,'BTCUSDT',D('.01'),D('100')),)),
            SimulationFrame(T+70,market_events=(MarkEvent(T+70,'BTCUSDT',D('100')),))),**kwargs)


def test_passive_round_trip_fees_and_funding_use_one_ledger_at_venue_time():
    result=run(((T+10,OrderIntent('entry','BTCUSDT',D('.1'),limit_price=D('99'))),
                (T+45,OrderIntent('exit','BTCUSDT',D('-.1'),limit_price=D('101'),reduce_only=True))),ack_latency_ms=25)
    assert result['reconciliation']['ending_equity']=='1000.08'
    assert result['reconciliation']['fee']=='0.02'
    assert result['reconciliation']['funding']=='-0.1'
    assert result['reconciliation']['residual']=='0'
    assert result['ending_state']['positions']=={}
    assert [f['venue_time_ms'] for f in result['queue']['fills']]==[T+20,T+60]
    assert result['queue']['fills'][0]['client_receive_ms']==T+45
    assert result['ledger_applied'] is True and result['authoritative'] is False
    assert result['economic_eligible'] is False


def test_rejected_order_never_occupies_queue_or_steals_another_orders_trade():
    result=run(((T+10,OrderIntent('oversize','BTCUSDT',D('50'),limit_price=D('99'))),
                (T+10,OrderIntent('valid','BTCUSDT',D('.1'),limit_price=D('99')))))
    assert result['orders'][0]['status']=='REJECTED'
    assert result['orders'][0]['reason']=='MAX_ORDER_QUANTITY'
    assert result['orders'][1]['filled_quantity']=='0.1'
    assert [f['order_id'] for f in result['queue']['fills']]==['valid']


def test_price_grid_failure_is_preserved_and_passive_never_uses_adverse_price_slippage():
    result=run(((T+10,OrderIntent('offtick','BTCUSDT',D('.1'),limit_price=D('99.05'))),))
    assert result['orders'][0]['reason']=='PRICE_TICK'
    with pytest.raises(ValueError,match='slippage'):
        run((),costs=SimulationCostModel(fee_rate=D('.001'),adverse_slippage_rate=D('.01')))


def test_cancel_latency_changes_fills_before_funding_without_double_debit():
    actions=((T+10,OrderIntent('a','BTCUSDT',D('.1'),limit_price=D('99'))),(T+15,CancelOrder('a')))
    fast=run(actions,cancel_latency_ms=0);slow=run(actions,cancel_latency_ms=10)
    assert not fast['queue']['fills'] and fast['reconciliation']['fee']=='0'
    assert slow['orders'][0]['filled_quantity']=='0.1'
    assert slow['reconciliation']['funding']=='-0.1'
    assert fast['input_hash']!=slow['input_hash']


def test_missing_order_rules_do_not_silently_remove_reservation_checks():
    with pytest.raises(ValueError,match='order rule'):
        run((),order_rule_profiles=None)


def test_post_cost_refusal_returns_trade_budget_without_leaking_rejected_ledger_entries():
    from services.execution.perpetual_simulation import RiskVerdict
    def risk(ctx,intent):
        return RiskVerdict(False,'FIXTURE_FILL_REJECTION') if ctx.timestamp==T+20 and intent.order_id=='first' else RiskVerdict(True)
    result=run(((T+10,OrderIntent('first','BTCUSDT',D('.1'),limit_price=D('99'))),
                (T+10,OrderIntent('second','BTCUSDT',D('.1'),limit_price=D('99')))),
        risk_policy=risk,risk_policy_identity={'fixture':'reject-first-at-fill'})
    assert [o['status'] for o in result['orders']]==['REJECTED','FILLED']
    assert result['reconciliation']['fee']=='0.0099'
    assert [f['order_id'] for f in result['queue']['fills']]==['second']
    assert not any(e.get('order_id')=='first' for e in result['ledger_events'])


def test_two_reduce_only_exits_cannot_open_a_reverse_position_or_charge_an_extra_fee():
    market=tape()
    trades=list(market.trades);trades[1]=replace(trades[1],quantity=D('2.2'),normal_quantity=D('2.2'))
    market=replace(market,trades=tuple(trades))
    result=run(((T+10,OrderIntent('entry','BTCUSDT',D('.1'),limit_price=D('99'))),
        (T+45,OrderIntent('exit-a','BTCUSDT',D('-.1'),limit_price=D('101'),reduce_only=True)),
        (T+45,OrderIntent('exit-b','BTCUSDT',D('-.1'),limit_price=D('101'),reduce_only=True))),market=market)
    assert result['ending_state']['positions']=={}
    assert result['orders'][-1]['reason']=='REDUCE_ONLY_NO_POSITION'
    assert result['reconciliation']['fee']=='0.02'


def test_fixed_actions_cannot_see_public_book_before_receipt_and_identity_binds_clocks():
    result=run(((T+10,OrderIntent('a','BTCUSDT',D('.1'),limit_price=D('99'))),))
    q=result['decision_events'][0]['quotes']['BTCUSDT']
    assert q['information_scope']=='EXCHANGE_INTERNAL_FIXED_ACTIONS_ONLY'
    assert q['public_available_time']==T+5
    assert 'available_time' not in q
    late=replace(tape(),books=tuple(replace(b,available_ms=b.available_ms+1) for b in tape().books),start_ms=T+6)
    other=run(((T+10,OrderIntent('a','BTCUSDT',D('.1'),limit_price=D('99'))),),market=late)
    assert result['input_hash']!=other['input_hash']
    with pytest.raises(ValueError):run(((T+1,OrderIntent('early','BTCUSDT',D('.1'),limit_price=D('99'))),))


def test_partial_fill_expiry_releases_reservation_and_keeps_only_executed_fees():
    result=run(((T+10,OrderIntent('partial','BTCUSDT',D('.2'),limit_price=D('99'),expires_at=T+25)),))
    assert result['orders'][0]['status']=='EXPIRED'
    assert result['orders'][0]['remaining_quantity']=='0.1'
    assert result['queue']['orders'][0]['censored'] is False
    assert result['ending_reservation']['total_reserved']=='0'
    assert result['reconciliation']['fee']=='0.0099'
    assert result['reconciliation']['funding']=='-0.1'


def test_expired_before_venue_arrival_is_a_recorded_rejection_not_a_phantom_fill():
    result=run(((T+10,OrderIntent('late','BTCUSDT',D('.1'),limit_price=D('99'),expires_at=T+15)),),order_latency_ms=20)
    assert result['orders'][0]['reason']=='EXPIRED_BEFORE_ARRIVAL'
    assert not result['queue']['fills']
    assert result['reconciliation']['attributed_change']=='0'


def test_partial_reduce_only_remainder_cancels_and_cannot_consume_another_exit_budget():
    market=tape();trades=list(market.trades)
    trades[0]=replace(trades[0],quantity=D('1.2'),normal_quantity=D('1.2'))
    trades[1]=replace(trades[1],quantity=D('2.2'),normal_quantity=D('2.2'))
    result=run(((T+10,OrderIntent('entry','BTCUSDT',D('.2'),limit_price=D('99'))),
        (T+45,OrderIntent('exit-a','BTCUSDT',D('-.1'),limit_price=D('101'),reduce_only=True)),
        (T+45,OrderIntent('exit-b','BTCUSDT',D('-.2'),limit_price=D('101'),reduce_only=True))),
        market=replace(market,trades=tuple(trades)))
    assert result['orders'][-1]['reason']=='REDUCE_ONLY_REMAINDER'
    assert result['queue']['orders'][-1]['status']=='CANCELLED'
    assert result['orders'][-1]['remaining_quantity']=='-0.1'
    assert result['reconciliation']['fee']=='0.04'
    assert result['ending_state']['positions']=={}


def test_capacity_partial_slice_keeps_front_order_priority_and_charges_only_committed_quantity():
    from services.execution.perpetual_ledger_v2 import CapacityAction
    capacity=F['_capacity'](adv_notional='99')
    capacity=replace(capacity,policy=replace(capacity.policy,action=CapacityAction.PARTIAL))
    market=tape();trades=list(market.trades)
    trades[0]=replace(trades[0],quantity=D('1.3'),normal_quantity=D('1.3'))
    result=run(((T+10,OrderIntent('front','BTCUSDT',D('.2'),limit_price=D('99'))),
        (T+10,OrderIntent('back','BTCUSDT',D('.1'),limit_price=D('99')))),
        market=replace(market,trades=tuple(trades)),capacity=capacity)
    assert [(f['order_id'],f['quantity']) for f in result['queue']['fills']]==[('front','0.1')]
    at_trade=[e for e in result['queue']['events'] if e['venue_time_ms']==T+20]
    assert [(e['order_id'],e['status']) for e in at_trade]==[('front','PARTIALLY_FILLED')]
    assert result['reconciliation']['fee']=='0.0099'
    assert list(result['ending_state']['capacity_consumed'].values())==['9.9']
