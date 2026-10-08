"""Declared venue limits act on orders without replacing ledger risk checks."""
from dataclasses import replace
from decimal import Decimal as D
import pytest

from services.execution import perpetual_simulation as sim
from tests.execution.test_perpetual_order_admission import profile, execute, at_start, frame, T


def constrained(**changes):
    constraints = sim.VenueOrderConstraints(
        percent_price_up=D('1.1'), percent_price_down=D('.9'),
        max_open_orders=1, market_take_bound=D('.05'))
    return profile(venue_constraints=replace(constraints, **changes))


@pytest.mark.parametrize('qty,price,reason', [
    ('.1', '110.1', 'PERCENT_PRICE'), ('-.1', '89.9', 'PERCENT_PRICE'),
    ('.1', '110', None), ('-.1', '90', None),
    ('.1', '80', None), ('-.1', '120', None),
])
def test_percent_price_is_side_specific_and_inclusive_at_admission(qty, price, reason):
    r = execute((frame(0),), at_start(sim.OrderIntent('a', 'BTCUSDT', D(qty), limit_price=D(price))),
                order_profile=constrained())
    assert r['orders'][0]['reason'] == reason
    assert r['orders'][0]['status'] == ('REJECTED' if reason else 'OPEN')
    assert r['reconciliation']['fee'] == '0'


def test_pending_order_limit_counts_partial_orders_and_cancel_releases_slot():
    def strategy(ctx):
        if ctx.timestamp == T:
            return (sim.OrderIntent('first', 'BTCUSDT', D('.1')),
                    sim.OrderIntent('over_limit', 'BTCUSDT', D('.1')))
        if ctx.timestamp == T+10:
            return (sim.OrderIntent('partial_still_counts', 'BTCUSDT', D('.1')),
                    sim.CancelOrder('first'), sim.OrderIntent('replacement', 'BTCUSDT', D('.1')))
        return ()
    r = execute((frame(0), frame(10, size='.01'), frame(20)), strategy,
                order_profile=constrained())
    orders = {x['order_id']:x for x in r['orders']}
    assert orders['first']['status'] == 'CANCELLED'
    assert orders['first']['filled_quantity'] == '0.01'
    assert orders['over_limit']['reason'] == 'MAX_OPEN_ORDERS'
    assert orders['partial_still_counts']['reason'] == 'MAX_OPEN_ORDERS'
    assert orders['replacement']['status'] == 'FILLED'
    assert D(r['ending_state']['positions']['BTCUSDT']['quantity']) == D('.11')
    assert r['reconciliation']['residual'] == '0'


@pytest.mark.parametrize('qty,quote', [('.1', '106'), ('-.1', '94')])
def test_market_price_protection_uses_current_mark_before_any_fill(qty, quote):
    r = execute((frame(0), frame(10, bid=quote, ask=quote)),
                at_start(sim.OrderIntent('market', 'BTCUSDT', D(qty))), order_profile=constrained())
    assert r['orders'][0]['reason'] == 'MARKET_TAKE_BOUND'
    assert r['ending_state']['positions'] == {}
    assert r['ending_reservation']['total_reserved'] == '0'
    assert r['reconciliation']['fee'] == '0'


def test_market_protection_after_partial_fill_keeps_exact_committed_quantity_and_fee():
    r = execute((frame(0), frame(10, size='.04'), frame(20, bid='106', ask='106')),
                at_start(sim.OrderIntent('market', 'BTCUSDT', D('.1'))), order_profile=constrained(),
                costs=sim.SimulationCostModel(fee_rate=D('.001')))
    assert r['orders'][0]['reason'] == 'MARKET_TAKE_BOUND'
    assert r['orders'][0]['filled_quantity'] == '0.04'
    assert r['reconciliation']['fee'] == '0.004'
    assert r['reconciliation']['residual'] == '0'


def test_market_minimum_notional_uses_mark_not_favorable_executable_price():
    r = execute((frame(0, bid='200', ask='200'),),
                at_start(sim.OrderIntent('too_small', 'BTCUSDT', D('.04'))), order_profile=constrained())
    assert r['orders'][0]['reason'] == 'QUANTITY_RULE'
    assert r['ending_reservation']['total_reserved'] == '0'


def test_valid_mark_notional_is_not_mistaken_for_a_closing_only_exemption():
    r = execute((frame(0, bid='99', ask='99'), frame(10, bid='99', ask='99')),
                at_start(sim.OrderIntent('small_valid', 'BTCUSDT', D('.05'))), order_profile=constrained())
    assert r['orders'][0]['status'] == 'FILLED'
    assert r['orders'][0]['filled_quantity'] == '0.05'
    assert r['reconciliation']['residual'] == '0'


def test_previously_accepted_limit_is_not_cancelled_when_percent_band_moves():
    r = execute((frame(0), frame(10, '80', bid='85', ask='85')),
                at_start(sim.OrderIntent('rest', 'BTCUSDT', D('.1'), limit_price=D('95'))),
                order_profile=constrained())
    assert r['orders'][0]['status'] == 'FILLED'
    assert r['orders'][0]['filled_quantity'] == '0.1'


def test_constraint_changes_are_bound_even_when_no_order_is_submitted():
    a = execute((frame(0),), at_start(), order_profile=constrained())
    b = execute((frame(0),), at_start(), order_profile=constrained(max_open_orders=2))
    assert a['input_hash'] != b['input_hash']


def test_passive_rejection_does_not_enter_queue_and_cancel_releases_count():
    from tests.execution.test_passive_perpetual_simulation import run as passive
    r = passive(((T+10, sim.OrderIntent('first','BTCUSDT',D('.1'),limit_price=D('99'))),
                 (T+11, sim.OrderIntent('over_limit','BTCUSDT',D('.1'),limit_price=D('99'))),
                 (T+15, sim.CancelOrder('first')),
                 (T+16, sim.OrderIntent('replacement','BTCUSDT',D('.1'),limit_price=D('99')))),
                order_rule_profiles=(constrained(),))
    assert [o['status'] for o in r['orders']] == ['CANCELLED','REJECTED','FILLED']
    assert r['orders'][1]['reason'] == 'MAX_OPEN_ORDERS'
    assert [f['order_id'] for f in r['queue']['fills']] == ['replacement']
    assert r['reconciliation']['fee'] == '0.0099'
    assert r['reconciliation']['residual'] == '0'


@pytest.mark.parametrize('changes', [
    {'max_open_orders':True}, {'max_open_orders':0}, {'max_open_orders':1.0},
    {'percent_price_up':D('NaN')}, {'percent_price_down':D('0')},
    {'percent_price_up':D('.8')}, {'market_take_bound':D('-1')},
])
def test_malformed_constraints_fail_before_running(changes):
    with pytest.raises(ValueError):
        constrained(**changes)
