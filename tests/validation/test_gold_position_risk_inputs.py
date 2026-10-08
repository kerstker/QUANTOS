"""Actual account exposure survives partial fills, receipt delays and replay."""

import copy
import json
from dataclasses import replace
from decimal import Decimal as D

import pytest

from services.execution.perpetual_simulation import LinearPerpetualSimulationSession, OrderIntent
from quantos.contracts.decimal_arithmetic import ledger_decimal_context
from quantos.streaming_identity import json_parts
from tests.execution.test_perpetual_simulation_session import configuration
from tests.research.test_gold_atomic_phase_loss import recovered_settlement
from tests.research.test_gold_loss_control import run_control
from tests.research.test_gold_v2_strategy import T, frame
from tests.validation.test_gold_account_risk_inputs import case, inputs


def trade(ctx):
    if ctx.timestamp == T:
        return (OrderIntent('entry', 'BTCUSDT', D('.1')),)
    if ctx.timestamp == T + 30:
        return (OrderIntent('exit', 'BTCUSDT', D('-.1'), reduce_only=True),)
    return ()


def test_original_history_records_actual_partial_quantity_and_contract_multiplier():
    from tests.execution.test_perpetual_simulation import frame as btc_frame
    config = configuration(trade)
    rule, = config['contract_rules']
    config['contract_rules'] = (replace(rule, spec=replace(rule.spec, contract_multiplier=D('2'))),)
    session = LinearPerpetualSimulationSession(**config)
    session.advance((btc_frame(0), btc_frame(10, size='.04'), btc_frame(20),
                     btc_frame(30, '110'), btc_frame(40, '110')))
    result = session.report()
    assert result['position_history_policy'] == 'ACTUAL_LEDGER_POSITION_MARKS_V1'
    assert result['equity_history'][0]['positions'] == {}
    filled = [p for p in result['equity_history'] if p['phase'] == 'FILL']
    partial = filled[0]['positions']['BTCUSDT']
    assert partial['quantity'] == '0.04'  # Original target was .1, not filled inventory.
    assert partial['contract_multiplier'] == '2'
    assert partial['mark_price'] == '100'
    assert partial['notional'] == '8'
    assert partial['margin_asset'] == 'USDT'
    assert partial['contract_rule_id'] == rule.rule_id
    assert filled[1]['positions']['BTCUSDT']['quantity'] == '0.1'
    assert filled[-1]['positions'] == result['ending_state']['positions'] == {}
    assert result['reconciliation']['residual'] == '0'


@ledger_decimal_context()
def test_gold_closed_account_preserves_intragrid_exposure_and_actual_weights():
    simulation = case()
    before = copy.deepcopy(simulation)
    value = inputs(simulation, frequency=40)
    projected = value.projection['position_inputs']
    assert value.projection['schema'] == 'QUANTOS_GOLD_ACCOUNT_RISK_INPUTS_V2'
    assert projected['status'] == 'AVAILABLE'
    assert projected['latest']['positions'] == {}
    assert projected['latest']['signed_weights'] == {}
    assert D(projected['peak_gross_notional']['value']) == D('10')
    assert D(projected['peak_gross_weight']['value']) == D('10') / D('95')
    observed = list(value.position_history.items())
    entry = next(p for p in observed if p['phase'] == 'FILL' and p['positions'])
    assert entry['signed_weights']['XAUUSDT'] == str(D('10') / D('95'))
    assert entry['positions']['XAUUSDT']['quantity'] == '0.1'
    assert value.projection['source_groups']['account_positions']['status'] == 'AVAILABLE'
    # Instrument inventory cannot invent the independent alpha allocation required by the gate.
    assert value.projection['source_groups']['weights']['status'] == 'MISSING'
    assert value.projection['source_groups']['alpha_returns']['status'] == 'MISSING'
    assert value.projection['full_research_risk_ready'] is False
    assert simulation == before


def test_position_mark_history_is_delayed_until_the_actual_funding_receipt():
    _, simulation = run_control((frame(0), frame(10), recovered_settlement(delayed=True),
                                 frame(1010, price='115'), frame(1020, price='115')))
    settlements = [dict(settlement_at_ms=T + 1000, requested_at_ms=T + 1000,
        available_at_ms=T + 1010, instrument_id='XAUUSDT', funding_rate='0.1', mark_price='100')]
    early = inputs(simulation, end=1000, frequency=1000, settlements=settlements)
    assert D(early.projection['position_inputs']['peak_gross_notional']['value']) == D('10')
    assert early.projection['position_inputs']['latest']['timestamp'] < T + 1000
    assert all(p['available_at_ms'] <= T + 1000 for p in early.position_history.items())
    known = inputs(simulation, end=1010, frequency=1010, settlements=settlements)
    assert D(known.projection['position_inputs']['peak_gross_notional']['value']) == D('11.5')
    delayed = next(p for p in known.position_history.items() if p['phase'] == 'MARK' and p['timestamp'] == T + 1000)
    assert delayed['available_at_ms'] == T + 1010


def test_unrecorded_legacy_positions_are_unknown_even_when_the_account_ends_flat():
    simulation = case()
    del simulation['position_history_policy']
    for row in simulation['equity_history']:
        del row['positions']
    value = inputs(simulation)
    assert value.returns is not None
    assert value.position_history is None
    assert value.projection['position_inputs']['status'] == 'UNAVAILABLE_NOT_RECORDED'
    assert value.projection['source_groups']['account_positions']['status'] == 'UNAVAILABLE_NOT_RECORDED'


@pytest.mark.parametrize('damage', ['missing', 'quantity', 'notional', 'multiplier', 'unit', 'terminal'])
def test_declared_position_history_rejects_missing_or_corrupted_original_facts(damage):
    simulation = case()
    point = next(p for p in simulation['equity_history'] if p['phase'] == 'FILL' and p['positions'])
    position = point['positions']['XAUUSDT']
    if damage == 'missing': del point['positions']
    elif damage == 'quantity': position['quantity'] = '0'
    elif damage == 'notional': position['notional'] = '0'
    elif damage == 'multiplier': position['contract_multiplier'] = 'NaN'
    elif damage == 'unit': position['margin_asset'] = 'CNY'
    else: simulation['equity_history'][-1]['positions'] = copy.deepcopy(point['positions'])
    with pytest.raises(ValueError, match='GOLD_ACCOUNT_RISK_POSITION'):
        inputs(simulation)


def test_future_marks_do_not_change_the_actual_exposure_available_at_the_cut():
    _, first = run_control((frame(0), frame(10), frame(20, price='101'), frame(30, price='102')))
    _, second = run_control((frame(0), frame(10), frame(20, price='101'), frame(30, price='900')))
    a, b = inputs(first, end=20), inputs(second, end=20)
    assert a.projection['position_inputs'] == b.projection['position_inputs']


def test_actual_short_position_keeps_signed_weight_and_positive_gross():
    from tests.research.test_gold_v2_strategy import execute
    strategy = lambda ctx: (OrderIntent('short', 'XAUUSDT', D('-.1')), ) if ctx.timestamp == T else ()
    result = execute((frame(0), frame(10), frame(20)), strategy, initial_collateral=D('100'))
    value = inputs(result, end=20)
    latest = value.projection['position_inputs']['latest']
    assert D(latest['signed_weights']['XAUUSDT']) < 0
    assert D(latest['gross_notional']) == D('10')


def test_native_disk_history_and_cold_restore_preserve_every_actual_position(tmp_path):
    from tools.perpetual_stream_history import SimulationHistory
    from tests.execution.test_perpetual_simulation import frame as btc_frame
    prefix = (btc_frame(0), btc_frame(10, size='.04'))
    tail = (btc_frame(20), btc_frame(30, '110'), btc_frame(40, '110'))
    with SimulationHistory(tmp_path / 'before', maximum_bytes=256_000_000) as store:
        original = LinearPerpetualSimulationSession(history_store=store, **configuration(trade))
        original.advance(prefix)
        checkpoint = original.checkpoint()
        with SimulationHistory(tmp_path / 'restored', maximum_bytes=256_000_000) as recovered_store:
            recovered = LinearPerpetualSimulationSession.restore_batches(checkpoint,
                batches=(prefix,), history_store=recovered_store, **configuration(trade))
            original.advance(tail)
            recovered.advance(tail)
            actual = json.loads(b''.join(json_parts(recovered.report_stream())))
            expected = json.loads(b''.join(json_parts(original.report_stream())))
            assert actual == expected
            assert any(p['positions'] for p in actual['equity_history'])
            assert actual['equity_history'][-1]['positions'] == {}


def test_rule_epoch_changes_identity_without_overwriting_past_inventory():
    from tests.execution.test_perpetual_environment_epochs import continued, tape
    original, prefix = continued()
    before = next(p for p in prefix['equity_history'] if p['phase'] == 'FILL')['positions']['BTCUSDT']
    middle = original.report()
    after = middle['equity_history'][-1]['positions']['BTCUSDT']
    assert before['contract_rule_id'] != after['contract_rule_id']
    assert before['quantity'] == after['quantity'] == '0.04'
    assert before['notional'] == after['notional'] == '4'
    original.advance(tape()[6:])
    assert original.report()['equity_history'][:len(prefix['equity_history'])] == prefix['equity_history']


def test_previous_equity_only_checkpoint_cannot_be_upgraded_to_recorded_positions():
    from tests.execution.test_perpetual_simulation import frame as btc_frame
    # Actual checkpoint captured from frozen parent 9d502eea with this configuration.
    checkpoint = dict(schema='PERPETUAL_SIMULATION_REPLAY_CHECKPOINT_V1',
        input_hash='ce4c49b3ed8aa2ed62d6cfcda68460b970bc073a1d24a2580b44e93af059c842',
        result_hash='0033354007b14eb9629db3237fad5765a76e21fb551b749e98d5930dcd6bd5b3',
        frame_count=2, last_timestamp=T + 10, authoritative=False, submission_enabled=False)
    with pytest.raises(ValueError, match='CHECKPOINT_REPLAY_MISMATCH'):
        LinearPerpetualSimulationSession.restore(checkpoint,
            frames=(btc_frame(0), btc_frame(10, '120')), **configuration())


def test_intragrid_nonpositive_equity_keeps_positions_without_claiming_valid_risk_weights():
    result = case()
    next(p for p in result['equity_history'] if p['phase'] == 'FILL' and p['positions'])['equity'] = '0'
    value = inputs(result, frequency=40)
    observation = next(p for p in value.position_history.items() if p['phase'] == 'FILL' and p['positions'])
    assert observation['signed_weights'] is observation['gross_weight'] is None
    assert value.projection['position_inputs']['weights_status'] == 'UNAVAILABLE_NONPOSITIVE_EQUITY'
    assert value.returns is None


def test_uncovered_period_does_not_extend_known_positions_to_claim_current_risk_inputs():
    value = inputs(case(), end=50)
    assert value.projection['position_inputs']['status'] == 'UNAVAILABLE_SAMPLE_COVERAGE'
    assert value.projection['position_inputs']['latest']['available_at_ms'] == T + 40
    assert 'account_positions' in value.projection['missing_groups']


@pytest.mark.parametrize('precision', [6, 28, 80])
def test_lazy_position_history_keeps_ledger_precision_and_signed_identity(precision):
    from decimal import localcontext
    from quantos.streaming_identity import exact_stream_identity_hash
    value = inputs(case())
    expected = value.projection['position_inputs']['history_sha256']
    with localcontext() as context:
        context.prec = precision
        assert exact_stream_identity_hash(value.position_history) == expected
        assert exact_stream_identity_hash(value.position_history) == expected
