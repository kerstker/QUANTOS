"""Independent order-rule and collateral competition regressions (synthetic venue)."""
from dataclasses import replace
from decimal import Decimal as D
from pathlib import Path
import runpy

import pytest

from services.execution import perpetual_simulation as sim
from services.execution.perpetual_ledger_v2 import MarginMode

F = runpy.run_path(str(Path(__file__).with_name('test_perpetual_simulation.py')))
T, frame, run = F['T'], F['frame'], F['run']


def profile(**changes):
    return replace(sim.OrderRuleProfile(
        rule_id='synthetic-order-rules-v1', contract_rule_id=F['F']['_rule']().rule_id,
        instrument_id='BTCUSDT', venue_id='BINANCE_USDM',
        effective_from=T-100, effective_until=T+1000,
        observed_time=T-30, available_time=T-20, ingestion_time=T-10,
        source_identity='synthetic-fixture:not-venue-authority',
        price_tick=D('.1'), min_price=D('.1'), max_price=D('100000'),
        market_max_quantity=D('10'), limit_max_quantity=D('20'),
        max_notional=D('100000'), minimum_policy='ORIGINAL_ORDER',
    ), **changes)


def execute(frames, strategy, *, order_profile=None, **kwargs):
    return run(frames, strategy, order_rule_profiles=(order_profile or profile(),), **kwargs)


def at_start(*orders):
    return lambda ctx: orders if ctx.timestamp == T else ()


@pytest.mark.parametrize('order,reason', [
    (sim.OrderIntent('bad','BTCUSDT',D('.1'),limit_price=D('99.95')), 'PRICE_TICK'),
    (sim.OrderIntent('bad','BTCUSDT',D('11')), 'MAX_ORDER_QUANTITY'),
    (sim.OrderIntent('bad','BTCUSDT',D('21'),limit_price=D('100')), 'MAX_ORDER_QUANTITY'),
    (sim.OrderIntent('bad','BTCUSDT',D('.1'),limit_price=D('.01')), 'PRICE_RANGE'),
])
def test_original_order_rules_reject_before_open(order, reason):
    r=execute((frame(0),frame(10)),at_start(order))
    assert r['order_events'][0]['status']=='REJECTED'
    assert r['orders'][0]['reason']==reason
    assert r['ending_state']['positions']=={}


def test_order_type_and_notional_caps_are_explicit():
    order=sim.OrderIntent('entry','BTCUSDT',D('.1'))
    r=execute((frame(0),),at_start(order),order_profile=profile(supported_order_types=('LIMIT',)))
    assert r['orders'][0]['reason']=='UNSUPPORTED_ORDER_TYPE'
    r=execute((frame(0),),at_start(order),order_profile=profile(max_notional=D('9')))
    assert r['orders'][0]['reason']=='MAX_ORDER_NOTIONAL'


def test_reversal_minimum_is_the_original_order_not_new_inventory():
    def strategy(ctx):
        if ctx.timestamp==T: return (sim.OrderIntent('open','BTCUSDT',D('.1')),)
        if ctx.timestamp==T+10: return (sim.OrderIntent('reverse','BTCUSDT',D('-.11')),)
        return ()
    r=execute((frame(0),frame(10),frame(20)),strategy)
    assert r['orders'][1]['status']=='FILLED'
    assert D(r['ending_state']['positions']['BTCUSDT']['quantity'])==D('-.01')
    assert r['reconciliation']['residual']=='0'
    conservative=execute((frame(0),frame(10),frame(20)),strategy,
                         order_profile=profile(minimum_policy='OPENED_POSITION'))
    assert conservative['orders'][1]['reason']=='QUANTITY_RULE'
    assert conservative['input_hash']!=r['input_hash']


def test_pending_orders_cannot_double_spend_margin_and_fees():
    r=execute((frame(0),frame(10,size='2')),at_start(
        sim.OrderIntent('first','BTCUSDT',D('.5')),
        sim.OrderIntent('second','BTCUSDT',D('-.5'))),
        initial_collateral=D('10'),costs=sim.SimulationCostModel(fee_rate=D('.001')),
        limits=sim.SimulationLimits(max_gross_leverage=D('20'),max_single_weight=D('20')))
    assert r['orders'][0]['status']=='FILLED'
    assert r['orders'][1]['reason']=='INSUFFICIENT_RESERVED_COLLATERAL'
    assert D(r['order_events'][0]['reservation']['total_reserved'])==D('5.05')
    assert r['ending_reservation']['total_reserved']=='0'


@pytest.mark.parametrize('terminal',['CANCELLED','EXPIRED'])
def test_partial_fill_releases_only_filled_reserve_then_terminal_releases_rest(terminal):
    seen=[]
    def strategy(ctx):
        seen.append(ctx)
        if ctx.timestamp==T:
            return (sim.OrderIntent('first','BTCUSDT',D('1'),
                    expires_at=T+20 if terminal=='EXPIRED' else None),)
        if ctx.timestamp==T+10 and terminal=='CANCELLED': return (sim.CancelOrder('first'),)
        return ()
    r=execute((frame(0),frame(10,size='.4'),frame(20,size='0')),strategy)
    partial=next(e for e in r['order_events'] if e['status']=='PARTIALLY_FILLED')
    assert D(partial['reservation']['total_reserved'])==D('6')
    assert r['orders'][0]['status']==terminal
    assert r['ending_reservation']['total_reserved']=='0'
    assert D(r['ending_state']['positions']['BTCUSDT']['quantity'])==D('.4')
    assert seen[1].reserved_collateral['first']==D('6')
    with pytest.raises(TypeError): seen[1].reserved_collateral['first']=D('0')


def test_cancel_releases_budget_for_next_action_in_same_callback():
    def strategy(ctx):
        if ctx.timestamp==T: return (sim.OrderIntent('rest','BTCUSDT',D('1'),limit_price=D('90')),)
        return (sim.CancelOrder('rest'),sim.OrderIntent('replacement','BTCUSDT',D('1')))
    r=execute((frame(0),frame(10)),strategy,initial_collateral=D('15'),
              limits=sim.SimulationLimits(max_gross_leverage=D('20'),max_single_weight=D('20')))
    assert [o['status'] for o in r['orders']]==['CANCELLED','OPEN']
    assert D(r['ending_reservation']['total_reserved'])==D('10')


def test_profile_epoch_and_venue_are_fail_closed():
    for changed in [profile(ingestion_time=T+1),profile(effective_until=T+10),
                    profile(venue_id='WRONG'),profile(contract_rule_id='WRONG')]:
        with pytest.raises(ValueError,match='order rule'):
            execute((frame(0),frame(10)),at_start(),order_profile=changed)


def test_profile_mode_rejects_unsupported_isolated_admission_explicitly():
    order=sim.OrderIntent('isolated','BTCUSDT',D('.1'),margin_mode=MarginMode.ISOLATED,isolated_margin=D('2'))
    r=execute((frame(0),frame(10)),at_start(order))
    assert r['orders'][0]['reason']=='UNSUPPORTED_ORDER_MARGIN_MODE'


def test_tier_crossing_reserves_incremental_canonical_margin():
    from services.execution.perpetual_ledger_v2 import MarginBracket
    rule=F['F']['_rule']()
    rule=replace(rule,spec=replace(rule.spec,margin_brackets=(
        MarginBracket(D('0'),D('100'),D('.1'),D('.05'),D('0')),
        MarginBracket(D('100'),None,D('.2'),D('.1'),D('5')))))
    r=execute((frame(0),),at_start(sim.OrderIntent('a','BTCUSDT',D('.8')),
        sim.OrderIntent('b','BTCUSDT',D('.3'))),contract_rules=(rule,))
    assert r['ending_reservation']['by_order']=={'a':'8','b':'14'}
    assert r['ending_reservation']['total_reserved']=='22'


def test_fill_cannot_consume_collateral_committed_to_another_pending_order():
    def strategy(ctx):
        if ctx.timestamp==T:
            return (sim.OrderIntent('gap','BTCUSDT',D('.5')),
                    sim.OrderIntent('rest','BTCUSDT',D('.5'),limit_price=D('90')))
        return ()
    # Initially 10 total reserved. At 120 the gap fill alone needs 6, but the
    # resting order also needs 6: rejecting it must undo its entire ledger fill.
    r=execute((frame(0),frame(10,'120')) ,strategy,initial_collateral=D('11'),
        limits=sim.SimulationLimits(max_gross_leverage=D('30'),max_single_weight=D('30')))
    assert [o['status'] for o in r['orders']]==['REJECTED','OPEN']
    assert r['orders'][0]['reason']=='INSUFFICIENT_RESERVED_COLLATERAL'
    assert r['ending_state']['positions']=={}
    assert r['ending_reservation']['by_order']=={'rest':'6'}
    assert r['reconciliation']['ending_equity']=='11'
    assert r['reconciliation']['residual']=='0'


def test_rejected_fill_releases_depth_capacity_and_reservation_for_retry():
    def policy(ctx,intent):
        if ctx.timestamp==T+10 and intent.order_id=='deny': return sim.RiskVerdict(False,'DENIED')
        return sim.RiskVerdict(True)
    r=execute((frame(0),frame(10,size='.5')),at_start(
        sim.OrderIntent('deny','BTCUSDT',D('.5')),
        sim.OrderIntent('retry','BTCUSDT',D('.5'))),risk_policy=policy,
        risk_policy_identity={'version':'denied-fixture-v1'})
    assert [o['status'] for o in r['orders']]==['REJECTED','FILLED']
    assert r['ending_reservation']['total_reserved']=='0'
    assert D(r['ending_state']['positions']['BTCUSDT']['quantity'])==D('.5')
    assert r['reconciliation']['residual']=='0'


@pytest.mark.parametrize('quantity,expected',[('.1','100.1'),('-.1','99.9')])
def test_adverse_slippage_rounds_to_executable_tick(quantity,expected):
    r=execute((frame(0),frame(10)),at_start(sim.OrderIntent('tick','BTCUSDT',D(quantity))),
        costs=sim.SimulationCostModel(adverse_slippage_rate=D('.0001')))
    assert r['order_events'][-1]['fill_price']==expected
    assert r['reconciliation']['residual']=='0'


def test_execution_max_notional_is_checked_on_parent_not_small_fill_slice():
    r=execute((frame(0),frame(10,'200',size='.01')),at_start(
        sim.OrderIntent('gap','BTCUSDT',D('.1'))),order_profile=profile(max_notional=D('15')))
    assert r['orders'][0]['reason']=='MAX_ORDER_NOTIONAL'
    assert r['ending_reservation']['total_reserved']=='0'
    assert r['ending_state']['positions']=={}


def test_profile_partial_fills_do_not_reapply_original_minimum():
    frames=(frame(0),)+tuple(frame(t,size='.01') for t in range(10,110,10))
    r=execute(frames,at_start(sim.OrderIntent('entry','BTCUSDT',D('.1'))))
    fills=[e for e in r['order_events'] if e['status'] in ('PARTIALLY_FILLED','FILLED')]
    assert len(fills)==10
    assert all(D(e['filled_quantity'])==D('.01') for e in fills)
    assert r['orders'][0]['status']=='FILLED'
    assert r['ending_reservation']['total_reserved']=='0'


def test_cross_instrument_pending_orders_compete_for_same_wallet():
    eth=replace(F['F']['_rule'](),rule_id='eth',spec=replace(F['F']['_spec'](),instrument_id='ETHUSDT'))
    def market(t):
        return sim.SimulationFrame(T+t,quotes=(F['quote'](t),F['quote'](t,name='ETHUSDT')),
            market_events=(F['MarkEvent'](T+t,'BTCUSDT',D('100')),F['MarkEvent'](T+t,'ETHUSDT',D('100'))),
            capacity_snapshots=(F['F']['_capacity'](),replace(F['F']['_capacity'](),instrument_id='ETHUSDT')))
    r=run((market(0),market(10)),at_start(sim.OrderIntent('btc','BTCUSDT',D('.6')),
        sim.OrderIntent('eth','ETHUSDT',D('.6'))),contract_rules=(F['F']['_rule'](),eth),
        order_rule_profiles=(profile(),profile(instrument_id='ETHUSDT',contract_rule_id='eth')),
        initial_collateral=D('10'),limits=sim.SimulationLimits(max_gross_leverage=D('20'),max_single_weight=D('20')))
    assert r['orders'][0]['status']=='FILLED'
    assert r['orders'][1]['reason']=='INSUFFICIENT_RESERVED_COLLATERAL'


def test_future_quote_changes_do_not_change_admission_or_reservations():
    strategy=at_start(sim.OrderIntent('entry','BTCUSDT',D('.1')))
    a=execute((frame(0),frame(10)),strategy)
    b=execute((frame(0),frame(10,'200')),strategy)
    assert a['decision_events'][0]==b['decision_events'][0]
    assert a['order_events'][0]==b['order_events'][0]
    assert a==execute((frame(0),frame(10)),strategy)


def test_reduce_only_exit_is_not_blocked_by_pending_reservation_deficit():
    def strategy(ctx):
        if ctx.timestamp==T: return (sim.OrderIntent('open','BTCUSDT',D('.5')),)
        if ctx.timestamp==T+10: return (sim.OrderIntent('rest','BTCUSDT',D('.5'),limit_price=D('90')),)
        if ctx.timestamp==T+20: return (sim.OrderIntent('exit','BTCUSDT',D('-.5'),reduce_only=True),)
        return ()
    r=execute((frame(0),frame(10),frame(20,bid='95',ask='105',size='0'),
        frame(30,bid='95',ask='105')),strategy,initial_collateral=D('11'),
        limits=sim.SimulationLimits(max_gross_leverage=D('30'),max_single_weight=D('30')))
    assert r['orders'][2]['status']=='FILLED'
    assert r['ending_state']['positions']=={}
    assert r['reconciliation']['ending_equity']=='8.5'


def test_missing_profile_remains_explicitly_incomplete_legacy_diagnostic():
    r=run((frame(0),))
    assert 'ORDER_RULE_PROFILE_MISSING' in r['authority_blockers']
    assert r['ending_reservation']['policy']=='UNRESERVED_LEGACY_DIAGNOSTIC'
    with pytest.raises(ValueError,match='exactly cover'):
        run((frame(0),),order_rule_profiles=())
