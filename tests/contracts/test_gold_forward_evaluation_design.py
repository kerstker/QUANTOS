import copy

import pytest

SOURCE = dict(commit='f'*40, python_source_sha256='1'*64,
    dependency_lock_sha256='2'*64, python_version='3.12.3', architecture='aarch64')
CONFIG = dict(quantity='0.02', initial_collateral='1000', fee_rate='0.0005')
START = 1_728_000_000_000
INTERVAL = 900_000


def declaration(**extra):
    from quantos.contracts import gold_forward_evaluation_design as design
    return design.declare(program_id=1, attempt=1, source_identity=SOURCE,
        configuration=copy.deepcopy(CONFIG), first_close_ms=START, window_bars=1,
        declared_at_ms=START-INTERVAL-180_000, selected_candidate='channel',
        proposal_sha256='a'*64, **extra)


def test_forward_design_precedes_whole_bar_and_does_not_know_future_input():
    from quantos.contracts import gold_forward_evaluation_design as design
    value = declaration()
    assert design.validate(value) == value
    assert value['declared_at_ms'] < value['window']['observation_start_ms']
    assert value['window']['closed_bars'] == [START]
    assert 'input_sha256' not in value
    assert value['statistical_policy']['dsr_policy']['minimum_probability'] == '0.95'
    assert value['statistical_policy']['pbo_policy']['maximum_probability'] == '0.25'
    assert value['budget'] == {'max_invocations':1, 'max_trials':3}
    assert value['economic_authority'] is value['submission_enabled'] is False


def test_legacy_declaration_retains_original_unimplemented_adapter():
    from quantos.contracts import gold_forward_evaluation_design as design
    value = declaration(legacy=True)
    assert value['schema'] == design.SCHEMA
    assert value['statistical_policy']['status'] == 'ADAPTER_NOT_IMPLEMENTED'
    assert design.validate(value) == value


def test_original_v2_complete_project_policy_cannot_be_relabelled_as_scoped():
    from quantos.contracts import gold_forward_evaluation_design as design
    value = declaration(comparison_scope='COMPLETE_PROJECT')
    assert value['schema'] == design.REVIEW_SCHEMA
    assert value['statistical_policy'] == design.full_statistics(1)
    assert 'candidate_matrix' not in value['statistical_policy']['comparison_policy']
    assert design.validate(value) == value
    value['schema'] = design.SCOPED_REVIEW_SCHEMA
    with pytest.raises(ValueError):
        design.validate(value)


@pytest.mark.parametrize('damage', ['late','source','cost','budget','selection','authority','future_hash'])
def test_declared_design_rejects_timing_policy_and_self_authority(damage):
    from quantos.contracts import gold_forward_evaluation_design as design
    value = declaration()
    if damage == 'late':
        value['declared_at_ms'] = value['window']['observation_start_ms']
    elif damage == 'source':
        value['source_identity_sha256'] = 'not-a-hash'
    elif damage == 'cost':
        value['configuration']['fee_rate'] = '-1'
    elif damage == 'budget':
        value['budget']['max_trials'] = 1
    elif damage == 'selection':
        value['selection']['candidate_id'] = 'another-family'
    elif damage == 'authority':
        value['economic_authority'] = True
    else:
        value['input_sha256'] = 'b'*64
    with pytest.raises(ValueError):
        design.validate(value)


@pytest.mark.parametrize('damage', ['boolean_budget', 'integer_retention', 'off_grid'])
def test_policy_types_and_bar_grid_cannot_be_changed(damage):
    from quantos.contracts import gold_forward_evaluation_design as design
    value = declaration()
    if damage == 'boolean_budget':
        value['budget']['max_invocations'] = True
    elif damage == 'integer_retention':
        value['termination']['retain_failed_and_eliminated'] = 1
    else:
        value['window']['closed_bars'][0] += 1
        value['window']['observation_start_ms'] += 1
        value['metrics']['start_ms'] += 1
        value['metrics']['end_ms'] += 1
    with pytest.raises(ValueError):
        design.validate(value)


def test_explicit_long_declaration_keeps_monthly_ten_second_grid_and_original_thresholds():
    from quantos.contracts import gold_forward_evaluation_design as design
    from quantos.contracts import research_statistical_review as statistics

    value = design.declare(program_id=1, attempt=1, source_identity=SOURCE,
        configuration=copy.deepcopy(CONFIG), first_close_ms=START, window_bars=2881,
        declared_at_ms=START-INTERVAL-180_000, selected_candidate='channel',
        proposal_sha256='a'*64, long_window=True)
    assert design.validate(value) == value
    assert value['schema'] == 'QUANTOS_GOLD_FORWARD_EVALUATION_DESIGN_V4'
    assert value['window']['closed_bars'] == [START + i * INTERVAL for i in range(2881)]
    assert value['metrics']['frequency_ms'] == 10_000
    assert value['metrics']['end_ms'] - value['metrics']['start_ms'] == 30 * 86_400_000 + 10_000
    policy = value['statistical_policy']
    budget = policy['computation_budget']
    assert statistics.sample({k: value['metrics'][k] for k in ('start_ms', 'end_ms', 'frequency_ms')},
                             budget=budget) == 259202
    assert policy['economic_checks'] == design.FULL_STATISTICS['economic_checks']
    assert policy['dsr_policy'] == design.FULL_STATISTICS['dsr_policy']
    assert policy['cpcv_policy'] == design.FULL_STATISTICS['cpcv_policy']
    assert policy['pbo_policy'] == design.FULL_STATISTICS['pbo_policy']
    assert policy['split_policy']['maximum_information_horizon_ms'] == 2881 * INTERVAL + 40_000
    assert value['economic_authority'] is value['submission_enabled'] is False
    assert len(statistics.canonical(value)) <= 65_536


def test_long_grid_and_four_candidate_matrix_require_the_exact_predeclared_budget():
    from quantos.contracts import gold_long_statistics_budget as budget
    from quantos.contracts import research_statistical_review as statistics
    from services.validation.project_research_statistics import _work_budget

    sample = dict(start_ms=START, end_ms=START + 32 * 86_400_000 + 10_000, frequency_ms=10_000)
    count = statistics.sample(sample, budget=budget.declaration())
    assert count == 276482
    groups = dict(n_groups=6, k_test=2)
    assert _work_budget(4, count, groups, 8, budget=budget.declaration()) == 15
    with pytest.raises(ValueError, match='GRID'):
        statistics.sample(sample)
    with pytest.raises(ValueError, match='WORK_BUDGET'):
        _work_budget(4, count, groups, 8)
    with pytest.raises(ValueError):
        statistics.sample(dict(sample, end_ms=sample['end_ms'] + 10_000), budget=budget.declaration())
    for key in ('maximum_points', 'maximum_matrix_cells', 'maximum_work'):
        changed = budget.declaration()
        changed[key] += 1
        with pytest.raises(ValueError):
            statistics.sample(sample, budget=changed)


def test_maximum_long_declaration_fits_original_plan_and_metadata_budgets_without_registration():
    from quantos.contracts import gold_forward_evaluation_design as design
    from quantos.contracts import registered_research_run as native
    from quantos.contracts import registered_gold_collection as metadata
    from quantos.contracts.gold_trial_identity import gold_trial_definition_id
    from tools.registered_research_run import _plan
    from tools.registered_gold_collection import _metadata

    configuration = dict(CONFIG, portfolio_plan=dict(
        quantities={name: '0.02' for name in design.NAMES}, max_notional='100'))
    value = design.declare(program_id=1, attempt=1, source_identity=SOURCE,
        configuration=configuration, first_close_ms=START, window_bars=3073,
        declared_at_ms=START-INTERVAL-180_000, long_window=True)
    raw = b'{}'  # Identity/size proof only: this is never registered or bound as research input.
    candidates = []
    for member in value['cohort']:
        name = member['candidate_id']
        preimage = dict(input_sha256=native.digest(raw), candidate=name, source=SOURCE)
        candidates.append((name, gold_trial_definition_id(**preimage), preimage))
    plan = _plan(adapter=native.GOLD, input_raw=raw, source=SOURCE, candidates=candidates,
        search_id='capacity-contract', producer_id='REGISTERED_GOLD_COLLECTOR_V1',
        actor_id='AUTOMATED_TEST', dataset_version='synthetic-capacity-contract',
        registered_at_ms=value['window']['closed_bars'][-1]+40_000,
        invocation_id='9'*64, forward_design=value)
    assert len(native.canonical(plan)) <= native.MAX_PLAN_BYTES == 65_536
    record = _metadata(plan)
    assert record['schema'] == metadata.LONG_REVIEW_SCHEMA
    assert len(record['forward_declaration']['closed_bars']) == 3073
    assert len(native.canonical(record)) <= 65_536
    for old_schema in (metadata.DECLARED_SCHEMA, metadata.REVIEW_SCHEMA):
        changed = copy.deepcopy(record)
        changed['schema'] = old_schema
        with pytest.raises(ValueError):
            metadata.validate(changed)
    with pytest.raises(ValueError, match='WINDOW'):
        design.declare(program_id=1, attempt=1, source_identity=SOURCE,
            configuration=configuration, first_close_ms=START, window_bars=3074,
            declared_at_ms=START-INTERVAL-180_000, long_window=True)
