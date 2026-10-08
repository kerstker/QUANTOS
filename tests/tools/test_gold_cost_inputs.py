"""Frozen JSON inputs must reproduce the real cost replay without Python objects."""
import json
from decimal import Decimal

import pytest

from tests.tools.test_gold_cost_replay import inputs
from tools.gold_cost_inputs import dump_inputs, load_inputs
from tools.gold_cost_replay import replay_costs


def test_json_roundtrip_preserves_exact_types_and_cash_result():
    original = inputs()
    frozen = dump_inputs(original)
    restored = load_inputs(frozen)
    assert restored == original
    assert type(restored['plan']['quantity']) is Decimal
    assert replay_costs(**restored)['result_hash'] == replay_costs(**original)['result_hash']
    assert dump_inputs(restored) == frozen


def test_input_cannot_select_a_python_callable_or_file():
    document = json.loads(dump_inputs(inputs()))
    document['arguments'] = ['object', 'subprocess.Popen', {}]
    with pytest.raises(ValueError):
        load_inputs(json.dumps(document).encode())


@pytest.mark.parametrize('replacement', [float('nan'), .001, object()])
def test_nonexact_or_unknown_python_values_cannot_enter_archive(replacement):
    args = inputs()
    args['plan']['quantity'] = replacement
    with pytest.raises(ValueError):
        dump_inputs(args)


def test_unknown_arguments_do_not_become_simulator_options():
    args = inputs()
    args['strategy'] = 'caller-selected-callback'
    with pytest.raises(ValueError):
        dump_inputs(args)


def test_missing_costs_cannot_acquire_defaults():
    args = inputs()
    del args['costs']
    with pytest.raises(ValueError):
        dump_inputs(args)


def test_duplicate_json_fields_and_excessive_nesting_are_rejected():
    raw = dump_inputs(inputs())
    with pytest.raises(ValueError):
        load_inputs(b'{"schema":"wrong",' + raw[1:])
    value = ['none']
    for _ in range(40):
        value = ['list', [value]]
    with pytest.raises(ValueError):
        load_inputs(json.dumps({'schema': 'GOLD_COST_INPUT_V1', 'arguments': value}).encode())


def test_tagged_nonfinite_decimal_is_rejected():
    document = json.loads(dump_inputs(inputs()))
    document['arguments'] = ['decimal', 'NaN']
    with pytest.raises(ValueError):
        load_inputs(json.dumps(document).encode())


def test_order_profile_and_limits_roundtrip_without_dropping_risk_fields():
    from dataclasses import replace
    from tests.execution.test_perpetual_order_admission import profile
    from services.execution.perpetual_simulation import SimulationLimits
    args = inputs()
    args['order_rule_profiles'] = (replace(profile(), instrument_id='XAUUSDT',
                                           contract_rule_id=args['contract_rules'][0].rule_id),)
    args['limits'] = SimulationLimits(max_gross_leverage=Decimal('.5'))
    assert load_inputs(dump_inputs(args)) == args
