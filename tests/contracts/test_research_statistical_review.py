"""Strict statistical declarations, without authority or a new cash model."""
import copy
import importlib

import pytest

DAY = 86_400_000


def policy(*, source='1'*64, dataset='2'*64, start=2_000_073_600_000, benchmark=None):
    return dict(schema='QUANTOS_RESEARCH_STATISTICAL_POLICY_V1',
        profile='ROBUST_RESEARCH_SIGNIFICANCE_V1',
        sample_method='CANONICAL_FLAT_TERMINAL_CASH_EXACT_GRID_V1',
        source_identity_sha256=source, market_scope=['BTCUSDT'], dataset_manifest_sha256=dataset,
        study_scope=dict(study_sha256='3'*64, registered_at_ms=2_099_999_999_999),
        trial_budget=dict(max_attempts=1000),
        sample=dict(start_ms=start, end_ms=start+30*DAY, frequency_ms=DAY),
        split_policy=dict(scope='TRAIN_VALIDATION_ONLY', maximum_information_horizon_ms=DAY, embargo_ms=DAY),
        selection_policy=dict(method='PREDECLARED_CANDIDATE', candidate_id='candidate-a', definition_sha256='6'*64, tie_break='ATTEMPT_ID_ASCENDING'),
        dsr_policy=dict(method='ORIGINAL_DSR_V1', trial_count='ALL_CANDIDATE_ATTEMPTS',
            moments='EMPIRICAL_POPULATION_CENTRAL_MOMENTS_V1', variance='ORIGINAL_POPULATION_VARIANCE_FLOOR_V1', minimum_probability='0.95'),
        cpcv_policy=dict(method='ORIGINAL_CPCV_V1', n_groups=4, k_test=2,
            minimum_positive_fraction='0.5', minimum_p05_sharpe='0'),
        pbo_policy=dict(method='ORIGINAL_CSCV_V1', block_count=4, maximum_probability='0.25'),
        benchmark_policy=benchmark or dict(id='fixed-native-baseline',definition_sha256='4'*64,
            input_sha256='5'*64,comparison='SAME_NATIVE_SOURCE_DATASET_GRID'),
        oos_policy=dict(status='NOT_EVALUATED_BY_THIS_STATISTICAL_REPORT'),
        economic_checks=dict(minimum_net_return='0',minimum_benchmark_excess='0',maximum_drawdown='1',
            minimum_observations=30,minimum_duration_ms=30*DAY),
        elimination_policy='RETAIN_ALL_ATTEMPTS_NO_FABRICATED_RETURNS',
        termination_policy='MISSING_INSUFFICIENT_THRESHOLD_FAIL',submission_enabled=False,economic_authority=False)


def api():
    return importlib.import_module('quantos.contracts.research_statistical_review')


def test_policy_is_exact_bounded_and_non_authorizing():
    value=policy();before=copy.deepcopy(value)
    assert api().validate_statistical_policy(value)==before and value==before
    assert value['economic_authority'] is value['submission_enabled'] is False


@pytest.mark.parametrize('damage',['unknown','authority_alias','bool_time','nonfinite','weaker_dsr','weaker_pbo',
    'posthoc_selection','odd_blocks','split_blowup','horizon','offgrid','too_many_points','zero_frequency','oos_claim'])
def test_policy_refuses_ambiguous_or_weakened_parameters(damage):
    value=policy()
    if damage=='unknown':value['approved']=True
    elif damage=='authority_alias':value['economic_authority']=0
    elif damage=='bool_time':value['study_scope']['registered_at_ms']=True
    elif damage=='nonfinite':value['dsr_policy']['minimum_probability']='NaN'
    elif damage=='weaker_dsr':value['dsr_policy']['minimum_probability']='0.1'
    elif damage=='weaker_pbo':value['pbo_policy']['maximum_probability']='0.9'
    elif damage=='posthoc_selection':value['selection_policy']['method']='BEST_FULL_SAMPLE'
    elif damage=='odd_blocks':value['pbo_policy']['block_count']=3
    elif damage=='split_blowup':value['cpcv_policy'].update(n_groups=16,k_test=8)
    elif damage=='horizon':value['split_policy']['maximum_information_horizon_ms']=2*DAY
    elif damage=='offgrid':value['sample']['end_ms']+=1
    elif damage=='too_many_points':value['sample']['end_ms']=value['sample']['start_ms']+100001*DAY
    elif damage=='zero_frequency':value['sample']['frequency_ms']=0
    else:value['oos_policy']['status']='INDEPENDENTLY_LOCKED'
    with pytest.raises(ValueError):api().validate_statistical_policy(value)


def test_shared_large_input_is_rejected_before_unbounded_encoding():
    value=policy();value['market_scope']=['x'*1_000_000]*1000
    with pytest.raises(ValueError):api().validate_statistical_policy(value)


@pytest.mark.parametrize('profile,dsr,pbo,valid',[
    ('RESEARCH_PRESCREEN_V1','0.6','0.5',True),
    ('ROBUST_RESEARCH_SIGNIFICANCE_V1','0.95','0.25',True),
    ('ROBUST_RESEARCH_SIGNIFICANCE_V1','0.6','0.25',False),
    ('ROBUST_RESEARCH_SIGNIFICANCE_V1','0.95','0.5',False),
    ('RESEARCH_PRESCREEN_V1','0.59','0.5',False),
    ('CUSTOM','0.95','0.25',False)])
def test_profile_has_a_fixed_minimum_threshold(profile,dsr,pbo,valid):
    value=policy();value['profile']=profile;value['dsr_policy']['minimum_probability']=dsr
    value['pbo_policy']['maximum_probability']=pbo
    if valid:assert api().validate_statistical_policy(value)==value
    else:
        with pytest.raises(ValueError):api().validate_statistical_policy(value)


def test_declared_zero_cannot_remove_actual_return_interval_embargo():
    value=policy();value['split_policy'].update(maximum_information_horizon_ms=0,embargo_ms=0)
    with pytest.raises(ValueError,match='INFORMATION_HORIZON'):api().validate_statistical_policy(value)


def test_selected_candidate_cannot_relabel_its_own_retry_as_benchmark():
    value=policy();value['benchmark_policy']['id']=value['selection_policy']['candidate_id']
    with pytest.raises(ValueError,match='BENCHMARK_ROLE'):api().validate_statistical_policy(value)


def test_selection_requires_a_precomputed_native_definition_identity():
    value=policy();value['selection_policy'].pop('definition_sha256',None)
    with pytest.raises(ValueError):api().validate_statistical_policy(value)


@pytest.mark.parametrize('identity',[None,False,0,'same-name','G'*64,'a'*63])
def test_selection_definition_is_an_exact_native_digest(identity):
    value=policy();value['selection_policy']['definition_sha256']=identity
    with pytest.raises(ValueError):api().validate_statistical_policy(value)
