"""Finite account limits govern new exposure without losing risk valuation."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.execution.perpetual_ledger_v2 import MarginBracket, MarginConfigurationError
from services.execution.perpetual_simulation import OrderIntent
from tests.execution.test_perpetual_order_admission import F, T, frame, execute, at_start


def finite_rule(cap='120'):
    rule = F['F']['_rule']()
    return replace(rule, spec=replace(rule.spec,
        margin_brackets=(MarginBracket(D('0'), D('100'), D('.1'), D('.01')),
                         MarginBracket(D('100'), D('200'), D('.2'), D('.02'), D('1'))),
        new_risk_notional_cap=D(cap), margin_overflow_policy='LAST_BRACKET_RISK_ESTIMATE_V1'))


def test_finite_tail_keeps_original_cap_and_valuates_market_excursion():
    spec = finite_rule().spec
    assert spec.margin_requirement(D('100')).maintenance_margin == D('1')
    value = spec.margin_requirement(D('250'))
    assert value.initial_margin == D('50') and value.maintenance_margin == D('4')
    assert value.outside_observed_brackets is True
    assert spec.margin_brackets[-1].notional_cap == D('200')
    assert spec.as_canonical_dict()['new_risk_notional_cap'] == '120'


@pytest.mark.parametrize('changes', [
    {'new_risk_notional_cap': None}, {'new_risk_notional_cap': D('201')},
    {'margin_overflow_policy': 'NONE'}, {'margin_overflow_policy': 'invented'},
])
def test_finite_tables_require_bounded_admission_and_explicit_overflow_policy(changes):
    with pytest.raises(MarginConfigurationError):
        replace(finite_rule().spec, **changes)


def test_pending_orders_cannot_separately_spend_the_same_notional_limit():
    r = execute((frame(0),), at_start(OrderIntent('a', 'BTCUSDT', D('.7')),
        OrderIntent('b', 'BTCUSDT', D('.5'))), contract_rules=(finite_rule(),))
    assert [o['status'] for o in r['orders']] == ['OPEN', 'REJECTED']
    assert r['orders'][1]['reason'] == 'ACCOUNT_NOTIONAL_LIMIT'
    assert r['ending_reservation']['by_order'] == {'a': '7'}


def test_fill_price_excursion_rejects_and_releases_reservation_without_ledger_cost():
    r = execute((frame(0), frame(10, '121')), at_start(OrderIntent('a', 'BTCUSDT', D('1'))),
        contract_rules=(finite_rule(),))
    assert r['orders'][0]['reason'] == 'ACCOUNT_NOTIONAL_LIMIT'
    assert r['ending_state']['positions'] == {}
    assert r['reconciliation']['fee'] == '0'
    assert r['ending_reservation']['total_reserved'] == '0'


def test_market_move_above_final_bracket_preserves_existing_position_and_exit():
    def strategy(ctx):
        if ctx.timestamp == T: return (OrderIntent('entry', 'BTCUSDT', D('1')),)
        if ctx.timestamp == T+20:
            return (OrderIntent('add', 'BTCUSDT', D('.1')),
                    OrderIntent('exit', 'BTCUSDT', D('-1'), reduce_only=True))
        return ()
    r = execute((frame(0), frame(10), frame(20, '250'), frame(30, '250')), strategy,
        contract_rules=(finite_rule(),))
    assert [o['status'] for o in r['orders']] == ['FILLED', 'REJECTED', 'FILLED']
    assert r['orders'][1]['reason'] == 'ACCOUNT_NOTIONAL_LIMIT'
    assert r['ending_state']['positions'] == {}
    assert r['reconciliation']['ending_equity'] == '1150'
    assert r['reconciliation']['residual'] == '0'


def test_direct_ledger_cannot_bypass_cap_and_failed_fill_rolls_back():
    from services.execution.perpetual_ledger_v2 import LinearPerpetualLedger, AccountNotionalLimit, MarkEvent
    from tests.execution.test_perpetual_ledger_v2 import _trade
    ledger = LinearPerpetualLedger((finite_rule().spec,), initial_collateral=D('1000'))
    ledger.apply_event(MarkEvent(0, 'BTCUSDT', D('100')))
    before = ledger.state_as_canonical_dict()
    with pytest.raises(AccountNotionalLimit):
        ledger.apply_event(_trade(1, '1.2', '100', fee='1'))
    assert ledger.state_as_canonical_dict() == before
    ledger.apply_event(_trade(1, '1', '100'))
    assert ledger.position('BTCUSDT').quantity == D('1')


def test_account_cap_identity_does_not_change_legacy_unbounded_contract():
    old = F['F']['_rule']().spec
    assert 'new_risk_notional_cap' not in old.as_canonical_dict()
    assert old.margin_requirement(D('250')).outside_observed_brackets is False
    assert finite_rule('120').spec.as_canonical_dict() != finite_rule('121').spec.as_canonical_dict()


def test_opposite_pending_orders_do_not_net_into_extra_account_capacity():
    orders = (OrderIntent('buy', 'BTCUSDT', D('1.1')),
              OrderIntent('sell', 'BTCUSDT', D('-1.1')),
              OrderIntent('more-buy', 'BTCUSDT', D('.1')))
    r = execute((frame(0),), at_start(*orders), contract_rules=(finite_rule(),))
    assert [o['status'] for o in r['orders']] == ['OPEN', 'OPEN', 'REJECTED']
    assert r['orders'][2]['reason'] == 'ACCOUNT_NOTIONAL_LIMIT'


def test_actual_capacity_slice_is_checked_instead_of_unfilled_requested_quantity():
    from services.execution.perpetual_ledger_v2 import CapacityAction
    cap = F['F']['_capacity']()
    cap = replace(cap, policy=replace(cap.policy, adv_notional=D('5500'),
        max_participation_rate=D('.01'), action=CapacityAction.PARTIAL))
    frames = tuple(replace(f, capacity_snapshots=(cap,)) for f in (frame(0), frame(10, '200')))
    r = execute(frames, at_start(OrderIntent('entry', 'BTCUSDT', D('1'))), contract_rules=(finite_rule(),))
    assert r['orders'][0]['status'] == 'PARTIALLY_FILLED'
    assert r['orders'][0]['filled_quantity'] == '0.275'
    assert r['reconciliation']['residual'] == '0'


def test_pending_limit_prices_are_included_in_account_cap_stress():
    r = execute((frame(0),), at_start(
        OrderIntent('high-limit', 'BTCUSDT', D('.1'), limit_price=D('600')),
        OrderIntent('second', 'BTCUSDT', D('.1'), limit_price=D('100'))),
        contract_rules=(finite_rule(),))
    assert [o['status'] for o in r['orders']] == ['OPEN', 'REJECTED']
    assert r['orders'][1]['reason'] == 'ACCOUNT_NOTIONAL_LIMIT'
