"""Liquidity role belongs to the fill mechanism, never the strategy's order type."""
from decimal import Decimal as D
from fractions import Fraction
import pytest

from services.execution.perpetual_simulation import OrderIntent, SimulationCostModel, SimulationLimits
from tests.execution.test_perpetual_simulation import T, frame, run
from tests.execution.test_perpetual_order_admission import profile, at_start
from tests.execution.test_passive_perpetual_simulation import run as passive


def fees(maker='0.0002', taker='0.001'):
    return SimulationCostModel(maker_fee_rate=D(maker), taker_fee_rate=D(taker))


@pytest.mark.parametrize('limit', [None, D('100')])
def test_aggressive_market_and_limit_both_pay_taker_once(limit):
    result=run((frame(0),frame(10,size='.04'),frame(20,size='.06')),
        at_start(OrderIntent('a','BTCUSDT',D('.1'),limit_price=limit)),costs=fees())
    trades=[e for e in result['ledger_events'] if e['event_type']=='TRADE']
    assert len(trades)==2
    assert {e['liquidity_role'] for e in trades}=={'TAKER'}
    assert {e['fee_rate'] for e in trades}=={'0.001'}
    expected=sum((abs(Fraction(e['filled_quantity']))*Fraction(e['execution_price'])*Fraction('0.001') for e in trades),Fraction(0))
    assert sum((Fraction(e['applied_fee']) for e in trades),Fraction(0))==expected
    assert Fraction(result['reconciliation']['fee'])==expected==Fraction('0.01')
    assert result['reconciliation']['ending_equity']=='999.99'
    assert result['reconciliation']['residual']=='0'


def test_post_only_queue_round_trip_uses_maker_and_keeps_funding_separate():
    actions=((T+10,OrderIntent('entry','BTCUSDT',D('.1'),limit_price=D('99'))),
             (T+45,OrderIntent('exit','BTCUSDT',D('-.1'),limit_price=D('101'),reduce_only=True)))
    result=passive(actions,costs=fees())
    trades=[e for e in result['ledger_events'] if e['event_type']=='TRADE']
    assert len(trades)==2 and {e['liquidity_role'] for e in trades}=={'MAKER'}
    assert [e['applied_fee'] for e in trades]==['0.00198','0.00202']
    assert result['reconciliation']['fee']=='0.004'
    assert result['reconciliation']['funding']=='-0.1'
    assert result['reconciliation']['ending_equity']=='1000.096'
    assert result['reconciliation']['residual']=='0'
    assert result['actual_venue_fees'] is None and result['venue_receipts_verified'] is False


def test_crossing_post_only_order_cannot_get_a_maker_fill():
    result=passive(((T+10,OrderIntent('cross','BTCUSDT',D('.1'),limit_price=D('101'))),),costs=fees())
    assert result['orders'][0]['reason']=='POST_ONLY_WOULD_CROSS'
    assert result['reconciliation']['fee']=='0'
    assert not [e for e in result['ledger_events'] if e['event_type']=='TRADE']


def test_zero_maker_is_only_an_explicit_cost_assumption():
    result=passive(((T+10,OrderIntent('entry','BTCUSDT',D('.1'),limit_price=D('99'))),),costs=fees(maker='0'))
    assert result['reconciliation']['fee']=='0'
    assert result['actual_venue_fees'] is None
    assert result['economic_eligible'] is False and result['authoritative'] is False


def test_pending_reservation_uses_the_larger_rate_without_spending_it():
    costs=fees(maker='.01',taker='.001')
    result=run((frame(0),frame(10,size='2')),at_start(OrderIntent('a','BTCUSDT',D('.5'))),
        costs=costs,initial_collateral=D('10'),order_rule_profiles=(profile(),),
        limits=SimulationLimits(max_gross_leverage=D('20'),max_single_weight=D('20')))
    assert result['order_events'][0]['reservation']['total_reserved']=='5.5'
    assert result['reconciliation']['fee']=='0.05'
    assert result['ending_reservation']['total_reserved']=='0'


def test_an_unused_rate_is_still_part_of_frozen_input_identity():
    args=((frame(0),frame(10)),at_start(OrderIntent('a','BTCUSDT',D('.1'))))
    first=run(*args,costs=fees());second=run(*args,costs=fees(maker='.0003'))
    assert first['reconciliation']==second['reconciliation']
    assert first['input_hash']!=second['input_hash']


def test_fixed_cost_compatibility_and_equal_role_rates_have_same_cashflows():
    args=((frame(0),frame(10)),at_start(OrderIntent('a','BTCUSDT',D('.1'))))
    old=run(*args,costs=SimulationCostModel(fee_rate=D('.001')))
    new=run(*args,costs=fees(maker='.001',taker='.001'))
    assert old['reconciliation']==new['reconciliation']
    assert old['input_hash']!=new['input_hash']


@pytest.mark.parametrize('values', [
    {'maker_fee_rate':D('.001')}, {'taker_fee_rate':D('.001')},
    {'maker_fee_rate':D('-0.001'),'taker_fee_rate':D('.001')},
    {'maker_fee_rate':D('0'),'taker_fee_rate':D('Infinity')},
    {'maker_fee_rate':D('NaN'),'taker_fee_rate':D('.001')},
    {'maker_fee_rate':.001,'taker_fee_rate':D('.001')},
    {'maker_fee_rate':D('.001'),'taker_fee_rate':D('1')},
    {'fee_rate':D('.001'),'maker_fee_rate':D('.001'),'taker_fee_rate':D('.002')},
])
def test_incomplete_ambiguous_or_invalid_fee_models_are_rejected(values):
    with pytest.raises(ValueError):SimulationCostModel(**values)
