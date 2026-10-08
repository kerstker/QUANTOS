from copy import deepcopy
from decimal import Decimal as D

import pytest

from tests.execution.test_perpetual_public_replay import gold_capture_fixture, capture_fixture, START, sealed
from tests.tools.test_gold_signal_replay import archive
from services.execution.perpetual_simulation import SimulationCostModel
from tools.gold_public_cost_inputs import assemble_inputs, verify_assembly
from tools.gold_cost_replay import replay_costs


def request():
    doc = archive()
    shift = START - 508
    doc['start_ms'] += shift
    doc['decision_times_ms'] = [t+shift for t in doc['decision_times_ms']]
    for key in ('registered_at_ms', 'expires_at_ms'):
        doc['zone'][key] += shift
    for row in doc['packets']:
        row['received_at_ms'] += shift
        for key in ('E', 'T'):
            row['payload'][key] += shift
    return dict(capture=gold_capture_fixture(), document=doc,
        release_identity={'commit': 'f'*40}, initial_collateral=D('1000'),
        plan=dict(quantity=D('.1'), stop=D('95'), target=D('105'),
                  entry_timeout_ms=2000, holding_ms=10000, max_quote_age_ms=2000),
        costs=SimulationCostModel(fee_rate=D('.001')))


def test_signal_clock_uses_only_prior_quotes_and_fills_on_next_real_observation():
    args = assemble_inputs(**request())
    verify_assembly(args)
    clock = next(f for f in args['frames'] if f.timestamp == START+2502)
    assert clock.quotes == clock.market_events == clock.capacity_snapshots == ()
    result = replay_costs(**args)
    order = result['simulation']['orders'][0]
    assert order['created_at'] == START+2502
    assert order['filled_at'] == START+3002
    assert result['outcome'] == 'OPEN_AT_END'


def test_future_quote_cannot_rewrite_the_signal_decision_quote():
    req = request()
    original = replay_costs(**assemble_inputs(**req))
    last = req['capture']['samples'][-1]['depth']['payload']
    last.update(bids=[['101', '1']], asks=[['101.1', '1']])
    req['capture'] = sealed(req['capture'])
    changed = replay_costs(**assemble_inputs(**req))
    before = next(r for r in original['simulation']['decision_events'] if r['timestamp'] == START+2502)
    after = next(r for r in changed['simulation']['decision_events'] if r['timestamp'] == START+2502)
    assert before == after


def test_outside_quote_coverage_and_btc_capture_are_rejected():
    req = request()
    req['document']['decision_times_ms'][-1] = START+4000
    with pytest.raises(ValueError, match='DECISION_OUTSIDE'):
        assemble_inputs(**req)
    req = request()
    req['capture'] = capture_fixture()
    with pytest.raises(ValueError, match='GOLD_CAPTURE_REQUIRED'):
        assemble_inputs(**req)


def test_changed_derived_frames_cannot_keep_original_capture_binding():
    from dataclasses import replace
    args = assemble_inputs(**request())
    args['frames'] = (replace(args['frames'][0], quotes=()), *args['frames'][1:])
    with pytest.raises(ValueError, match='ASSEMBLY_BINDING'):
        verify_assembly(args)


def test_assembly_owns_inputs_without_mutating_source_documents():
    req = request()
    before = deepcopy(req)
    args = assemble_inputs(**req)
    assert req == before
    req['document']['rule']['sell_threshold'] = '999'
    assert args['document']['rule']['sell_threshold'] == '10'


def test_empty_decision_frame_does_not_refresh_a_stale_quote():
    req = request()
    req['plan']['max_quote_age_ms'] = 100
    result = replay_costs(**assemble_inputs(**req))
    assert result['simulation']['orders'] == []
    assert result['strategy_events'][-1]['reason'] == 'ENTRY_CONTEXT_REJECTED'
