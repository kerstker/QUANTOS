"""Venue order lattices must not be confused with the accounting quantity atom."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
import json

import pytest

from services.execution import perpetual_simulation as sim
from tests.execution.test_perpetual_order_admission import F, T, frame, profile, execute, at_start
from tests.execution.test_passive_account_environment import fixture, receipt, prepare, BASE


def filters(*, price_min='.1', price_max='100000', tick='.1'):
    from services.execution.venue_order_filters import PriceFilter, QuantityFilter, VenueOrderFilters
    return VenueOrderFilters(PriceFilter(D(price_min), D(price_max), D(tick)),
        QuantityFilter(D('.003'), D('.010'), D('.002')),
        QuantityFilter(D('.002'), D('.008'), D('.003')))


def bound_profile(**price):
    f = filters(**price)
    return profile(price_tick=f.price.step, min_price=f.price.minimum, max_price=f.price.maximum,
        market_max_quantity=f.market.maximum, limit_max_quantity=f.limit.maximum, venue_filters=f)


def rule():
    r = F['F']['_rule']()
    return replace(r, spec=replace(r.spec, quantity_step=D('.001'), min_quantity=D('.002'), min_notional=D('.01')))


@pytest.mark.parametrize('kind,quantity,reason', [
    ('LIMIT', '.003', None), ('LIMIT', '.005', None), ('LIMIT', '.009', None),
    ('LIMIT', '.004', 'QUANTITY_STEP'), ('LIMIT', '.010', 'QUANTITY_STEP'),
    ('LIMIT', '.011', 'MAX_ORDER_QUANTITY'), ('LIMIT', '.001', 'MIN_ORDER_QUANTITY'),
    ('MARKET', '.002', 'MIN_ORDER_QUANTITY'), ('MARKET', '.005', None), ('MARKET', '.008', 'QUANTITY_STEP'),
    ('MARKET', '.003', 'QUANTITY_STEP'), ('MARKET', '.011', 'MAX_ORDER_QUANTITY'),
])
def test_original_orders_use_their_own_origin_minimum_step_and_bound(kind, quantity, reason):
    order = sim.OrderIntent('order', 'BTCUSDT', D(quantity), limit_price=D('100') if kind=='LIMIT' else None)
    result = execute((frame(0), frame(10)), at_start(order), order_profile=bound_profile(), contract_rules=(rule(),))
    assert result['orders'][0]['reason'] == reason
    assert result['orders'][0]['status'] == ('FILLED' if reason is None else 'REJECTED')
    assert result['reconciliation']['residual'] == '0'
    if reason:
        assert result['ending_state']['positions'] == {}
        assert result['ending_reservation']['total_reserved'] == '0'


def test_partial_fills_use_accounting_atom_without_reapplying_parent_grid():
    frames = (frame(0),) + tuple(frame(t, size='.001') for t in range(10, 60, 10))
    result = execute(frames, at_start(sim.OrderIntent('partial', 'BTCUSDT', D('.005'))),
        order_profile=bound_profile(), contract_rules=(rule(),))
    assert result['orders'][0]['status'] == 'FILLED'
    fills = [e for e in result['order_events'] if e['status'] in ('PARTIALLY_FILLED', 'FILLED')]
    assert len(fills) == 5
    assert all(D(e['filled_quantity']) == D('.001') for e in fills)
    assert result['ending_state']['positions']['BTCUSDT']['quantity'] == '0.005'
    assert result['reconciliation']['residual'] == '0'


@pytest.mark.parametrize('quantity,expected', [('.005', '100.15'), ('-.005', '99.95')])
def test_adverse_slippage_uses_offset_price_lattice(quantity, expected):
    result = execute((frame(0, '100.05'), frame(10, '100.05')),
        at_start(sim.OrderIntent('offset', 'BTCUSDT', D(quantity))),
        order_profile=bound_profile(price_min='.05', price_max='100000.02'), contract_rules=(rule(),),
        costs=sim.SimulationCostModel(fee_rate=D('0'), adverse_slippage_rate=D('.0001')))
    assert result['orders'][0]['status'] == 'FILLED'
    assert result['order_events'][-1]['fill_price'] == expected
    assert result['reconciliation']['residual'] == '0'


@pytest.mark.parametrize('minimum,maximum,step,price,reason', [
    ('0', '0', '0', '100.037', None), ('0', '100', '0', '100.001', 'PRICE_RANGE'),
    ('.05', '0', '0', '.049', 'PRICE_RANGE'), ('.05', '0', '.1', '100.05', None),
    ('.05', '100.02', '.1', '100.05', 'PRICE_RANGE'),
    ('.05', '0', '.1', '100.1', 'PRICE_TICK'), ('0', '0', '.1', '100.1', None),
])
def test_price_constraints_disable_independently_and_keep_origin(minimum, maximum, step, price, reason):
    p = bound_profile(price_min=minimum, price_max=maximum, tick=step)
    assert p.price_reason(D(price)) == reason


def changed_environment():
    raw, env = fixture()
    symbol = json.loads(env['before']['exchange']['raw_utf8'])['symbols'][0]
    by_type = {f['filterType']: f for f in symbol['filters']}
    by_type['LOT_SIZE'].update(minQty='.003', stepSize='.002', maxQty='.010')
    by_type['MARKET_LOT_SIZE'].update(minQty='.002', stepSize='.003', maxQty='.008')
    env['before']['exchange'] = receipt('exchange', {'symbols':[symbol]})
    env['exchange_after'] = receipt('exchange', {'symbols':[symbol]}, BASE+1300)
    return raw, env


def test_passive_public_adapter_binds_distinct_grids_and_minimal_exact_account_atom():
    raw, env = changed_environment(); before = deepcopy((raw, env))
    result = prepare(raw, env)
    assert (raw, env) == before
    assert result['contract_rules'][0].spec.quantity_step == D('.001')
    p = result['order_rule_profiles'][0]
    assert p.venue_filters.limit.minimum == D('.003')
    assert p.venue_filters.market.minimum == D('.002')
    assert result['manifest']['venue_order_filters']['quantity_atom'] == '0.001'
    assert result['manifest']['venue_order_filters']['venue_behavior_verified'] is False


def test_rest_public_adapter_binds_same_contract_without_rounding_original_input():
    from tests.execution.test_perpetual_public_replay import capture_fixture, sealed, prepare as rest_prepare
    data = capture_fixture()
    for record in (data['bootstrap']['exchange'], data['exchange_after']):
        record['payload'] = deepcopy(record['payload'])
        f = {row['filterType']:row for row in record['payload']['symbols'][0]['filters']}
        f['LOT_SIZE'].update(minQty='.003', stepSize='.002', maxQty='.010')
        f['MARKET_LOT_SIZE'].update(minQty='.002', stepSize='.003', maxQty='.008')
    frozen = sealed(data); before = deepcopy(frozen)
    result = rest_prepare(frozen)
    assert frozen == before
    assert result['contract_rules'][0].spec.quantity_step == D('.001')
    assert result['order_rule_profiles'][0].venue_filters.market.step == D('.003')
    assert result['manifest']['venue_order_filters']['quantity_atom'] == '0.001'


def test_public_market_notional_uses_mark_even_without_dynamic_constraint_group():
    r = rule(); r = replace(r, spec=replace(r.spec, min_notional=D('.52')))
    result = execute((frame(0, bid='110', ask='110'), frame(10, bid='110', ask='110')),
        at_start(sim.OrderIntent('market', 'BTCUSDT', D('.005'))), order_profile=bound_profile(),
        contract_rules=(r,))
    assert result['orders'][0]['reason'] == 'QUANTITY_RULE'
    assert result['ending_state']['positions'] == {}
    assert result['reconciliation']['fee'] == '0'


def test_disabled_price_filter_does_not_invent_tick_or_divide_by_zero():
    result = execute((frame(0, '100.037'), frame(10, '100.037')),
        at_start(sim.OrderIntent('free-price', 'BTCUSDT', D('.005'))),
        order_profile=bound_profile(price_min='0', price_max='0', tick='0'), contract_rules=(rule(),))
    assert result['orders'][0]['status'] == 'FILLED'
    assert result['order_events'][-1]['fill_price'] == '100.037'


def test_wrong_accounting_atom_cannot_silently_truncate_legal_orders():
    r = rule(); r = replace(r, spec=replace(r.spec, quantity_step=D('.002')))
    with pytest.raises(ValueError, match='accounting quantity atom'):
        execute((frame(0), frame(10)), at_start(), order_profile=bound_profile(), contract_rules=(r,))


def test_rejected_market_grid_does_not_consume_budget_or_next_orders_depth():
    result = execute((frame(0, size='.005'), frame(10, size='.005')),
        at_start(sim.OrderIntent('bad', 'BTCUSDT', D('.003')), sim.OrderIntent('valid', 'BTCUSDT', D('.005'))),
        order_profile=bound_profile(), contract_rules=(rule(),))
    assert [o['status'] for o in result['orders']] == ['REJECTED', 'FILLED']
    assert result['ending_state']['positions']['BTCUSDT']['quantity'] == '0.005'
    assert result['ending_reservation']['total_reserved'] == '0'
    assert result['reconciliation']['residual'] == '0'


def test_adverse_price_beyond_maximum_is_rejected_without_favorable_clamping():
    result = execute((frame(0, '100.05'), frame(10, '100.05')),
        at_start(sim.OrderIntent('outside', 'BTCUSDT', D('.005'))),
        order_profile=bound_profile(price_min='.05', price_max='100.12'), contract_rules=(rule(),),
        costs=sim.SimulationCostModel(fee_rate=D('.001'), adverse_slippage_rate=D('.0001')))
    assert result['orders'][0]['reason'] == 'PRICE_RANGE'
    assert result['ending_state']['positions'] == {}
    assert result['reconciliation']['fee'] == '0'


def test_new_filters_remain_in_identity_and_do_not_use_future_quote():
    p=bound_profile(price_min='.05', price_max='100000.02')
    strategy=at_start(sim.OrderIntent('order', 'BTCUSDT', D('.005')))
    first=execute((frame(0, '100.05'), frame(10, '100.05')), strategy, order_profile=p, contract_rules=(rule(),))
    later=execute((frame(0, '100.05'), frame(10, '200.05')), strategy, order_profile=p, contract_rules=(rule(),))
    assert first['decision_events'][0] == later['decision_events'][0]
    assert first['order_events'][0] == later['order_events'][0]
    other_filters=replace(p.venue_filters, market=replace(p.venue_filters.market, maximum=D('.009')))
    other=execute((frame(0, '100.05'), frame(10, '100.05')), strategy,
        order_profile=replace(p, market_max_quantity=D('.009'), venue_filters=other_filters), contract_rules=(rule(),))
    assert first['input_hash'] != other['input_hash']


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', '1e-999999', '1e999999', 0.001, True])
def test_public_filter_numbers_are_exact_and_bounded_before_lattice_arithmetic(value):
    raw, env = changed_environment()
    for key, at in (('before', BASE+900), ('after', BASE+1300)):
        old = env['before']['exchange'] if key=='before' else env['exchange_after']
        payload=json.loads(old['raw_utf8']); payload['symbols'][0]['filters'][1]['stepSize']=value
        if key=='before': env['before']['exchange']=receipt('exchange', payload, at)
        else: env['exchange_after']=receipt('exchange', payload, at)
    with pytest.raises(ValueError):
        prepare(raw, env)
