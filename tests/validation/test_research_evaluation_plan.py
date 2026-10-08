"""Pre-execution evaluation declarations never certify unseen data or authority."""
import copy

import pytest


def core():
    return dict(
        schema='QUANTOS_RESEARCH_EVALUATION_CORE_V2', evidence_kind='BACKTEST',
        native_adapter='TECHNICAL_LINEAR_PERPETUAL_V2', registered_at_ms=2_100_000_000_000,
        input_sha256='1'*64, source_identity_sha256='2'*64, dataset_manifest_sha256='3'*64,
        cohort=[dict(candidate_id='original-alpha', config_hash='4'*64, native_trial_id='5'*64)],
        budget=dict(max_trials=1, max_invocations=1),
        sample=dict(start_ms=1_600_000_000_000, end_ms=1_600_000_120_000, frequency_ms=1000),
        splits=dict(train=dict(start_ms=1_600_000_000_000,end_ms=1_600_000_040_000),
                    validation=dict(start_ms=1_600_000_041_000,end_ms=1_600_000_080_000),
                    locked_oos=dict(start_ms=1_600_000_081_000,end_ms=1_600_000_120_000)),
        selection=dict(method='PREDECLARED_CANDIDATE',candidate_id='original-alpha',
                       validation_policy_sha256='6'*64),
        metrics=dict(schema='PERPETUAL_FIXED_FREQUENCY_EQUITY_V1',
                     calendar='ELAPSED_UTC_MILLISECONDS_365_DAY_YEAR'),
        benchmark=dict(id='declared-flat',definition_sha256='7'*64,input_sha256='8'*64),
        termination=dict(policy_sha256='9'*64,on_invalid_inputs='REJECT',
                         on_missing_evidence='INSUFFICIENT',retain_eliminated=True),
        oos_access=dict(boundary_id='original-locked-cut',history_sha256='a'*64,viewed_boundary_ids=[]),
        submission_enabled=False,economic_authority=False)


def module():
    from quantos.contracts import research_evaluation_plan
    return research_evaluation_plan


def test_historical_sample_is_not_backdated_registration():
    api=module();value=core();before=copy.deepcopy(value)
    assert value['sample']['end_ms'] < value['registered_at_ms']
    assert api.validate_core(value)==before
    assert value==before
    assert api.core_sha256(value)==api.core_status(value)['core_sha256']
    status=api.core_status(value)
    assert status['oos_status']=='UNKNOWN'
    assert 'OOS_ACCESS_NOT_INDEPENDENTLY_VERIFIED' in status['reasons']
    assert status['submission_enabled'] is status['economic_authority'] is False


@pytest.mark.parametrize(('adapter','kind'), [
    ('UNKNOWN_ADAPTER',None), ('UNKNOWN_ADAPTER','BACKTEST'),
    ('TECHNICAL_LINEAR_PERPETUAL_V2',None), (False,False),
])
def test_unknown_adapter_cannot_alias_missing_evidence_kind(adapter,kind):
    api=module();value=core();value.update(native_adapter=adapter,evidence_kind=kind)
    with pytest.raises(ValueError):api.validate_core(value)


def test_uses_original_fixed_frequency_statistic_identity():
    from quantos.contracts.research_statistics import fixed_frequency_equity
    value=core();api=module()
    result=fixed_frequency_equity([
        dict(timestamp=value['sample']['start_ms'],sequence=0,equity='100')],
        **value['sample'])
    assert value['metrics']==dict(schema=result['schema_version'],calendar=result['calendar'])
    assert api.core_status(value)['oos_status']!='PASS'


@pytest.mark.parametrize('mutation',[
    'schema','unknown','authority','submission','bool_time','backward_sample',
    'bool_frequency','oversized_grid','overlap','outside','off_grid','empty_cohort',
    'duplicate_trial','contradictory_candidate','trial_budget','bool_budget','zero_invocations',
    'unknown_selection','unknown_candidate','train_selection_chosen','train_missing',
    'metric_calendar','metric_schema','metric_override','benchmark','termination',
    'forgotten_elimination','oos_duplicate','oos_typed','posthoc_plan','posthoc_result',
    'source_hash','oversized_text',
])
def test_strict_precommit_rejects_ambiguous_or_mutable_contract(mutation):
    api=module();v=core()
    if mutation=='schema':v['schema']='QUANTOS_RESEARCH_EVALUATION_PLAN_V1'
    if mutation=='unknown':v['custom']=True
    if mutation=='authority':v['economic_authority']=0
    if mutation=='submission':v['submission_enabled']=True
    if mutation=='bool_time':v['registered_at_ms']=True
    if mutation=='backward_sample':v['sample']['end_ms']=v['sample']['start_ms']-1
    if mutation=='bool_frequency':v['sample']['frequency_ms']=True
    if mutation=='oversized_grid':v['sample']['end_ms']=v['sample']['start_ms']+100_001_000
    if mutation=='overlap':v['splits']['validation']['start_ms']=v['splits']['train']['end_ms']
    if mutation=='outside':v['splits']['locked_oos']['end_ms']=v['sample']['end_ms']+1000
    if mutation=='off_grid':v['splits']['train']['end_ms']+=1
    if mutation=='empty_cohort':v['cohort']=[]
    if mutation=='duplicate_trial':v['cohort']*=2;v['budget']['max_trials']=2
    if mutation=='contradictory_candidate':
        v['cohort'].append(dict(v['cohort'][0],config_hash='b'*64,native_trial_id='c'*64));v['budget']['max_trials']=2
    if mutation=='trial_budget':v['budget']['max_trials']=0
    if mutation=='bool_budget':v['budget']['max_trials']=True
    if mutation=='zero_invocations':v['budget']['max_invocations']=0
    if mutation=='unknown_selection':v['selection']['method']='BEST_OOS_RETURN'
    if mutation=='unknown_candidate':v['selection']['candidate_id']='not-in-cohort'
    if mutation=='train_selection_chosen':v['selection']['method']='TRAIN_ONLY'
    if mutation=='train_missing':v['selection'].update(method='TRAIN_ONLY',candidate_id=None);v['splits']['train']=None
    if mutation=='metric_calendar':v['metrics']['calendar']='TRADE_EVENT_COUNT'
    if mutation=='metric_schema':v['metrics']['schema']='NEW_UNREVIEWED_SHARPE'
    if mutation=='metric_override':v['metrics']['annualized_sharpe']=10
    if mutation=='benchmark':v['benchmark']['definition_sha256']=''
    if mutation=='termination':v['termination']['on_missing_evidence']='PASS'
    if mutation=='forgotten_elimination':v['termination']['retain_eliminated']=1
    if mutation=='oos_duplicate':v['oos_access']['viewed_boundary_ids']=['seen','seen']
    if mutation=='oos_typed':v['oos_access']['history_sha256']=False
    if mutation=='posthoc_plan':v['registered_run_plan_sha256']='e'*64
    if mutation=='posthoc_result':v['result_sha256']='e'*64
    if mutation=='source_hash':v['source_identity_sha256']='f'*40
    if mutation=='oversized_text':v['benchmark']['id']='x'*65537
    with pytest.raises(ValueError):api.validate_core(v)


@pytest.mark.parametrize('missing', ['locked_oos','history','boundary'])
def test_missing_original_oos_evidence_is_explicitly_insufficient(missing):
    api=module();v=core()
    if missing=='locked_oos':v['splits']['locked_oos']=None
    if missing=='history':v['oos_access']['history_sha256']=None
    if missing=='boundary':v['oos_access']['boundary_id']=None
    assert api.core_status(v)['oos_status']=='INSUFFICIENT'


def test_previously_viewed_oos_cannot_be_declared_unseen():
    api=module();v=core()
    v['oos_access']['viewed_boundary_ids']=[v['oos_access']['boundary_id']]
    assert api.core_status(v)['oos_status']=='INSUFFICIENT'
    assert 'LOCKED_OOS_ALREADY_VIEWED' in api.core_status(v)['reasons']


def test_repeated_attempts_are_not_deduplicated_and_change_commitment():
    api=module();v=core();before=api.core_sha256(v)
    v['cohort'].append(dict(v['cohort'][0],native_trial_id='b'*64))
    v['budget'].update(max_trials=2,max_invocations=2)
    assert len(api.validate_core(v)['cohort'])==2
    assert api.core_sha256(v)!=before


def test_train_only_selection_does_not_predeclare_posthoc_winner():
    api=module();v=core();v['selection'].update(method='TRAIN_ONLY',candidate_id=None)
    assert api.core_status(v)['oos_status']=='UNKNOWN'


def test_forward_retains_actual_post_registration_window():
    api=module();v=core();v.update(evidence_kind='FORWARD',native_adapter='GOLD_BAR_FORWARD_V1')
    with pytest.raises(ValueError):api.validate_core(v)
    shift=v['registered_at_ms']-v['sample']['start_ms']
    for window in [v['sample'],*v['splits'].values()]:
        window['start_ms']+=shift;window['end_ms']+=shift
    assert api.core_status(v)['oos_status']=='UNKNOWN'


def test_all_declared_policy_inputs_are_bound_and_no_approval_is_returned():
    api=module();v=core();before=api.core_sha256(v)
    v['termination']['policy_sha256']='e'*64
    assert api.core_sha256(v)!=before
    status=api.core_status(v)
    assert set(status)=={'schema','core_sha256','oos_status','reasons','submission_enabled','economic_authority'}
