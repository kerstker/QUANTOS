"""Gold causality is a prospective recipe, never retrospective OOS authority."""
from copy import deepcopy

import pytest

from tests.research.test_gold_v2_strategy import T


def design():
    from quantos.contracts.gold_forward_evaluation_design import declare
    from quantos.contracts.gold_causality_policy import declare as causality
    from tests.validation.test_gold_research_risk_policy import policy as risk
    from tests.validation.test_gold_market_states import policy as market
    from tests.validation.test_gold_capacity_inputs import policy as capacity
    sample = dict(start_ms=T+900_000,end_ms=T+1_810_000,frequency_ms=10_000)
    return declare(program_id=1,attempt=1,source_identity=dict(synthetic=True),
        configuration=dict(quantity='0.1',initial_collateral='1000',fee_rate='0.001'),
        first_close_ms=sample['start_ms'],window_bars=2,declared_at_ms=T-1,long_window=True,
        research_risk_policy=risk(start=sample['start_ms'],end=sample['end_ms'],
            frequency=sample['frequency_ms'],declared=T-1),
        market_state_policy=market(sample,declared=T-1),capacity_policy=capacity(sample,declared=T-1),
        causality_policy=causality(sample=sample,declared_at_ms=T-1))


def test_native_causality_recipe_is_bound_before_observation_in_new_v8_only():
    from quantos.contracts.gold_forward_evaluation_design import validate
    value=design()
    assert value['schema']=='QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V8'
    assert validate(value)==value
    assert value['oos_status']=='NOT_INDEPENDENTLY_LOCKED'
    assert value['economic_authority'] is value['submission_enabled'] is False


@pytest.mark.parametrize('damage',['late','axis','method','authority','budget','missing','legacy_schema'])
def test_recipe_time_scope_budget_and_version_cannot_be_rewritten_as_a_pass(damage):
    from quantos.contracts.gold_forward_evaluation_design import validate
    value=deepcopy(design())
    policy=value['risk_policy']['causality_policy']
    if damage=='late':
        policy['declared_at_ms']=value['window']['observation_start_ms']+1
    elif damage=='axis':
        policy['sample']['end_ms']+=1
    elif damage=='method':
        policy['method']['unassessable']='PASS'
    elif damage=='authority':
        policy['independent_verification']=True
    elif damage=='budget':
        policy['budget']['maximum_decisions']=True
    elif damage=='missing':
        del value['risk_policy']['causality_policy']
    else:
        value['schema']='QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V7'
    with pytest.raises(ValueError):
        validate(value)


def net_design():
    from quantos.contracts.gold_forward_evaluation_design import declare
    from quantos.contracts.gold_net_forward_policy import declare as forward
    old=design()
    candidate='channel';proposal='a'*64
    definition=next(r['strategy_definition_sha256'] for r in old['cohort'] if r['candidate_id']==candidate)
    risk=old['risk_policy']
    return declare(program_id=old['program_id'],attempt=old['attempt'],source_identity=dict(synthetic=True),
        configuration=old['configuration'],first_close_ms=old['metrics']['start_ms'],window_bars=2,
        declared_at_ms=old['declared_at_ms'],long_window=True,selected_candidate=candidate,proposal_sha256=proposal,
        research_risk_policy=risk['research_risk_policy'],market_state_policy=risk['market_state_policy'],
        capacity_policy=risk['capacity_policy'],causality_policy=risk['causality_policy'],
        net_forward_policy=forward(sample={k:old['metrics'][k] for k in ('start_ms','end_ms','frequency_ms')},
            declared_at_ms=old['declared_at_ms'],series=dict(start_ms=old['metrics']['start_ms'],
                declared_at_ms=old['declared_at_ms'],candidate_id=candidate,
                strategy_definition_sha256=definition,proposal_sha256=proposal)))


def test_cost_net_forward_series_is_selected_before_observation_in_v9_only():
    from quantos.contracts.gold_forward_evaluation_design import validate
    value=net_design()
    assert value['schema']=='QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V9'
    assert validate(value)==value
    assert design()['schema']=='QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V8'


@pytest.mark.parametrize('damage',['late','axis','candidate','proposal','definition','compare_all','legacy'])
def test_forward_series_cannot_replace_a_predeclared_candidate_after_seeing_results(damage):
    from quantos.contracts.gold_forward_evaluation_design import validate
    value=deepcopy(net_design());p=value['risk_policy']['net_forward_policy']
    if damage=='late':p['declared_at_ms']=value['declared_at_ms']+1
    elif damage=='axis':p['sample']['end_ms']+=10_000
    elif damage=='candidate':p['series']['candidate_id']='trend'
    elif damage=='proposal':p['series']['proposal_sha256']='b'*64
    elif damage=='definition':p['series']['strategy_definition_sha256']='b'*64
    elif damage=='compare_all':value['selection']=dict(method='COMPARE_ALL_WITHOUT_SELECTION',candidate_id=None,proposal_sha256=None)
    else:value['schema']='QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V8'
    with pytest.raises(ValueError):validate(value)
