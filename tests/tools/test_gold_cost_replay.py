"""Real packet-to-signal-to-ledger integration with explicitly synthetic rules."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
import json

import pytest

from tools.gold_cost_replay import replay_costs
from tests.tools.test_gold_signal_replay import archive
from tests.research.test_gold_v2_strategy import frame, F, T
from services.execution.perpetual_simulation import SimulationCostModel
from services.execution.perpetual_ledger_v2 import FundingEvent


def inputs():
    document = archive()
    shift = T - 3010
    document['start_ms'] += shift
    document['decision_times_ms'] = [t + shift for t in document['decision_times_ms']]
    for key in ('registered_at_ms', 'expires_at_ms'):
        document['zone'][key] += shift
    for packet in document['packets']:
        packet['received_at_ms'] += shift
        for key in ('E', 'T'):
            packet['payload'][key] += shift
    return dict(document=document, release_identity={'commit': 'f'*40},
        market_identity={'dataset': 'SYNTHETIC_GOLD_REGRESSION'},
        plan=dict(quantity=D('.1'), stop=D('95'), target=D('105'),
                  entry_timeout_ms=50, holding_ms=100, max_quote_age_ms=20),
        contract_rules=(replace(F['_rule'](), rule_id='synthetic-gold',
            spec=replace(F['_spec'](), instrument_id='XAUUSDT')),),
        funding_policies=(replace(F['_funding_policy'](), instrument_id='XAUUSDT'),),
        frames=(frame(0), frame(10), replace(frame(20, '106'),
            market_events=(FundingEvent(T+20, 'XAUUSDT', D('.01'), D('106')),
                           *frame(20, '106').market_events)), frame(30, '107')),
        initial_collateral=D('1000'), costs=SimulationCostModel(fee_rate=D('.001')))


def test_packets_drive_original_ledger_with_fees_funding_and_later_fills():
    args = inputs()
    before = deepcopy(args)
    result = replay_costs(**args)
    engine = result['simulation']
    assert engine['orders'][0]['filled_at'] == T+10
    assert engine['orders'][1]['filled_at'] == T+30
    assert engine['reconciliation']['ending_equity'] == '1000.5733'
    assert engine['reconciliation']['residual'] == '0'
    assert result['outcome'] == 'FLAT_TERMINAL'
    assert result['authoritative'] is False
    assert result['signal_replay']['net_pnl'] is None
    assert args == before


def test_open_position_is_retained_not_reported_as_closed_trade():
    args = inputs()
    args['frames'] = args['frames'][:2]
    result = replay_costs(**args)
    assert result['outcome'] == 'OPEN_AT_END'
    assert result['simulation']['ending_state']['positions']


def test_missing_signal_frame_is_rejected_not_silently_a_cash_baseline():
    args = inputs()
    args['frames'] = args['frames'][1:]
    with pytest.raises(ValueError, match='SIGNAL_FRAME_MISSING'):
        replay_costs(**args)


def test_missing_packet_coverage_is_not_a_zero_return_experiment():
    args = inputs()
    args['document']['packets'] = args['document']['packets'][:4]
    with pytest.raises(ValueError, match='SIGNAL_INPUT_INCOMPLETE'):
        replay_costs(**args)


def test_other_market_rules_are_rejected():
    args = inputs()
    args['contract_rules'] = (F['_rule'](),)
    with pytest.raises(ValueError, match='GOLD_MARKET_SCOPE'):
        replay_costs(**args)


def test_costs_and_signal_input_change_result_identity():
    args = inputs()
    base = replay_costs(**args)
    args['costs'] = SimulationCostModel(fee_rate=D('.002'))
    assert replay_costs(**args)['result_hash'] != base['result_hash']
    args = inputs()
    args['document']['rule']['sell_threshold'] = '11'
    assert replay_costs(**args)['result_hash'] != base['result_hash']


def test_no_signal_keeps_the_ledger_cash_baseline():
    args = inputs()
    args['document']['rule']['sell_threshold'] = '1000'
    result = replay_costs(**args)
    assert result['outcome'] == 'NO_SIGNAL'
    assert result['simulation']['orders'] == []
    assert result['simulation']['reconciliation']['ending_equity'] == '1000'


def test_future_quotes_do_not_change_the_original_entry_decision():
    args = inputs()
    original = replay_costs(**args)
    args['frames'] = (*args['frames'][:-1], frame(30, '110'))
    altered = replay_costs(**args)
    assert original['simulation']['decision_events'][0] == altered['simulation']['decision_events'][0]
    assert original['signal_replay'] == altered['signal_replay']
    assert original['result_hash'] != altered['result_hash']


def test_funding_event_missing_at_settlement_is_rejected_by_original_engine():
    args = inputs()
    args['frames'] = (frame(0), frame(10), frame(20, '106'), frame(30, '107'))
    with pytest.raises(ValueError):
        replay_costs(**args)


def test_repeated_run_has_same_identity_and_explicit_costs_are_required():
    args = inputs()
    assert replay_costs(**args)['result_hash'] == replay_costs(**args)['result_hash']
    args['costs'] = None
    with pytest.raises(ValueError, match='explicit cost model'):
        replay_costs(**args)


def test_report_can_be_stored_as_json_without_changing_its_identity():
    from services.execution.backtest_authority import exact_identity_hash
    result = replay_costs(**inputs())
    restored = json.loads(json.dumps(result, allow_nan=False))
    digest = restored.pop('result_hash')
    assert digest == exact_identity_hash(restored, name='gold_cost_replay')
