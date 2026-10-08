"""Frozen public metadata must reach actual simulation order admission."""
import json
from decimal import Decimal as D
import pytest

from tests.execution.test_passive_account_environment import fixture, receipt, prepare, BASE


def environment(*, missing=None, bad=None):
    raw, env = fixture()
    symbol = json.loads(env['before']['exchange']['raw_utf8'])['symbols'][0]
    symbol['marketTakeBound'] = '.05'
    symbol['filters'] += [
        {'filterType':'PERCENT_PRICE','multiplierUp':'1.1','multiplierDown':'.9','multiplierDecimal':4},
        {'filterType':'MAX_NUM_ORDERS','limit':3},
    ]
    if missing == 'marketTakeBound': del symbol[missing]
    elif missing: symbol['filters'] = [f for f in symbol['filters'] if f['filterType'] != missing]
    if bad == 'number': symbol['marketTakeBound'] = .05
    elif bad == 'bool': symbol['filters'][-1]['limit'] = True
    elif bad == 'nan': symbol['filters'][-2]['multiplierUp'] = 'NaN'
    elif bad == 'duplicate': symbol['filters'].append(dict(symbol['filters'][-1]))
    env['before']['exchange'] = receipt('exchange', {'symbols':[symbol]})
    env['exchange_after'] = receipt('exchange', {'symbols':[symbol]}, BASE+1300)
    return raw, env


def test_public_parameters_are_bound_to_profile_and_explain_modeled_scope():
    raw, env = environment()
    result = prepare(raw, env)
    constraints = result['order_rule_profiles'][0].venue_constraints
    assert constraints.percent_price_up == D('1.1')
    assert constraints.max_open_orders == 3
    assert constraints.market_fill_reason(D('.1'), D('106'), D('100')) == 'MARKET_TAKE_BOUND'
    assert result['manifest']['venue_order_constraints']['scope'] == 'ISOLATED_DECLARED_ORDERS_ONLY'
    assert result['manifest']['venue_margin_tiers_verified'] is False


@pytest.mark.parametrize('missing', ['PERCENT_PRICE','MAX_NUM_ORDERS','marketTakeBound'])
def test_incomplete_public_constraint_group_cannot_silently_disable_check(missing):
    with pytest.raises(ValueError): prepare(*environment(missing=missing))


@pytest.mark.parametrize('bad', ['number','bool','nan','duplicate'])
def test_noncanonical_or_duplicate_values_are_rejected(bad):
    with pytest.raises(ValueError): prepare(*environment(bad=bad))


def test_legacy_missing_group_remains_explicitly_unknown():
    result = prepare(*fixture())
    assert result['order_rule_profiles'][0].venue_constraints is None
    assert result['manifest']['venue_order_constraints'] is None
    assert 'DYNAMIC_VENUE_FILTERS_NOT_FULLY_MODELED' in result['manifest']['limitations']


def test_rest_frozen_rules_reach_active_order_rejection():
    from tests.execution.test_perpetual_public_replay import capture_fixture, sealed, prepare as rest_prepare
    from services.execution.perpetual_simulation import OrderIntent, SimulationCostModel, run_linear_perpetual_simulation
    document = capture_fixture()
    for record in (document['bootstrap']['exchange'], document['exchange_after']):
        record['payload'] = json.loads(json.dumps(record['payload']))
        symbol = record['payload']['symbols'][0]
        symbol['marketTakeBound'] = '.05'
        symbol['filters'] += [
            {'filterType':'PERCENT_PRICE','multiplierUp':'1.1','multiplierDown':'.9'},
            {'filterType':'MAX_NUM_ORDERS','limit':3},
        ]
    prepared = rest_prepare(sealed(document))
    first = prepared['frames'][0].timestamp
    result = run_linear_perpetual_simulation(
        release_identity={'fixture':'synthetic'}, dataset_identity={'fixture':'synthetic-public'},
        strategy_identity={'fixture':'reject-limit'}, initial_collateral=D('1000'),
        strategy=lambda ctx:(OrderIntent('bad','BTCUSDT',D('.1'),limit_price=D('110.1')),)
            if ctx.timestamp == first else (), costs=SimulationCostModel(fee_rate=D('0')),
        **{k:v for k,v in prepared.items() if k != 'manifest'})
    assert result['orders'][0]['reason'] == 'PERCENT_PRICE'
    assert result['reconciliation']['fee'] == '0'
    assert result['ending_state']['positions'] == {}
