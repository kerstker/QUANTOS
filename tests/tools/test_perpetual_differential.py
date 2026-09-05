import json
from pathlib import Path
from tools.perpetual_differential import compare
from tools.perpetual_reference_worker import reference

FIXTURE=Path(__file__).parents[1]/'fixtures/perpetual_reference_cases.json'


def test_reference_has_independent_hand_computed_round_trip_values():
    cases=json.loads(FIXTURE.read_text())
    values={c['name']:reference(c,'1000','.001','fraction') for c in cases['cases']}
    from fractions import Fraction
    assert Fraction(values['long_round_trip'][-1]['equity'])==Fraction('1000.979')
    assert Fraction(values['short_round_trip'][-1]['equity'])==Fraction('1000.981')
    assert all(Fraction(v[-1]['position'])==0 for v in values.values())


def test_differential_checks_every_step_and_detects_a_changed_reference():
    fixture=json.loads(FIXTURE.read_text())
    expected={'engine':'fraction','dependencies':{},'cases':{
        c['name']:reference(c,fixture['initial_collateral'],fixture['fee_rate'],'fraction') for c in fixture['cases']}}
    assert compare(fixture,expected,{'source':'test'})['verdict']=='PASS'
    expected['cases']['long_round_trip'][1]['fees']='0'
    result=compare(fixture,expected,{'source':'test'})
    assert result['verdict']=='FAIL'
    assert any(d['field']=='fees' for c in result['cases'] for d in c['differences'])
