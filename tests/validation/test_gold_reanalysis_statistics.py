"""Original Gold BACKTEST study and retained policy; no independent OOS claim."""
import json
import time

import pytest

from tests.tools.test_gold_source_reanalysis import older_source as older_source, physical
from tests.tools.test_gold_source_reanalysis import short_source as short_source
from tests.tools.test_gold_source_reanalysis import registered_case as registered_case
from tests.tools.test_gold_reanalysis_evaluation import declaration
from tests.contracts.test_research_statistical_review import policy as technical_policy
from quantos.contracts.registered_research_run import digest

SCHEMA = 'QUANTOS_GOLD_REANALYSIS_STATISTICAL_POLICY_V1'
METHOD = 'NATIVE_GOLD_RECEIPT_AVAILABLE_EQUITY_GRID_V1'


def declared(case, *, oos_material=False):
    core = declaration(case, oos_material=oos_material)
    baseline = dict(schema='QUANTOS_GOLD_EMBEDDED_CASH_BENCHMARK_V1',
        id='GOLD_NO_ORDER_ORIGINAL_ACCOUNT_V1', native_adapter='GOLD_BAR_FORWARD_V1',
        source_identity_sha256=core['source_identity_sha256'],
        dataset_manifest_sha256=core['dataset_manifest_sha256'], input_sha256=core['input_sha256'])
    benchmark = dict(id=baseline['id'], definition_sha256=digest(baseline),
        input_sha256=core['input_sha256'], comparison='SAME_NATIVE_SOURCE_DATASET_GRID')
    value = technical_policy(source=core['source_identity_sha256'],
        dataset=core['dataset_manifest_sha256'], benchmark=benchmark)
    value.update(schema=SCHEMA, sample_method=METHOD, market_scope=['XAUUSDT'],
        sample=dict(start_ms=core['splits']['train']['start_ms'],
            end_ms=core['splits']['validation']['end_ms'], frequency_ms=1000))
    value['study_scope']['registered_at_ms'] = core['registered_at_ms']-1
    value['selection_policy'].update(candidate_id=core['selection']['candidate_id'],
        definition_sha256=core['cohort'][0]['config_hash'])
    value['split_policy'].update(maximum_information_horizon_ms=86_400_000, embargo_ms=86_400_000)
    core['benchmark'] = {k:benchmark[k] for k in ('id', 'definition_sha256', 'input_sha256')}
    core['selection']['validation_policy_sha256'] = digest(value)
    return core, value


def prepare(case, core, policy):
    from tools import registered_gold_chunked_job as job
    _, root, options, collector, _ = case
    return job.prepare_source_reanalysis(root, collection_source_identity=collector,
        evaluation_core=core, statistical_policy=policy, **options)


@pytest.mark.parametrize('damage', ['normal', 'cohort', 'policy', 'report', 'other-history'])
def test_original_queue_and_full_statistical_evidence_are_bound_to_predeclared_gold_backtest(
        older_source, monkeypatch, damage):
    from tools import registered_gold_chunked_job as job
    from tests.research.test_registered_research_job import queued
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation import project_research_statistics as kernels
    from services.validation import project_research_statistical_evidence as evidence
    from services.validation import gold_project_statistics as study
    f, original, _, _, _ = older_source
    before = physical(original)
    if damage == 'other-history':
        _, _, options, collector, _ = older_source
        previous = job.prepare_source_reanalysis(original, collection_source_identity=collector, **options)
        job.run_research(dict(native_invocation_id=previous['invocation_id']))
        options.update(invocation_id='d'*64, search_id='new-study')
    core, policy = declared(older_source)
    plan = prepare(older_source, core, policy)
    retained = f['root']/'statistical_policies'/(digest(policy)+'.json')
    assert json.loads(retained.read_bytes()) == policy
    execute = f['run'].gold._execute
    def checked(*args, **kwargs):
        assert json.loads(retained.read_bytes()) == policy
        return execute(*args, **kwargs)
    monkeypatch.setattr(f['run'].gold, '_execute', checked)
    registry, queue = queued(f['root'])
    uid = queue.submit('registered_gold_chunked', dict(native_invocation_id=plan['invocation_id']),
        time.time_ns()//1_000_000, invocation_id='gold-backtest-study')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    calls = []
    original_methods = kernels.review_native_methods
    def methods(vectors, selected, rules, count):
        calls.append((len(vectors), rules['sample'], count))
        return original_methods(vectors, selected, rules, count)
    monkeypatch.setattr(kernels, 'review_native_methods', methods)
    directory = f['root']/'project/runs'/plan['invocation_id']
    native_before = physical(directory)
    with ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db').evidence() as (cut, reports):
        cohort = study.project_cohort(project_cut=cut, invocation_id=plan['invocation_id'],
            policy=policy, reports=reports)
        total = 6 if damage == 'other-history' else 3
        assert len(cohort['attempts']) == total
        assert cohort['benchmark_attempt']['id'] == 'GOLD_NO_ORDER_ORIGINAL_ACCOUNT_V1'
        assert cohort['selected_attempt']['config_hash'] == policy['selection_policy']['definition_sha256']
        value = evidence.prepare(project_cut=cut, policy=policy, cohort=cohort,
            as_of_ms=cohort['frozen_at_ms'], reports=reports)
        report = value['report']
        assert report['schema'] == 'QUANTOS_GOLD_BACKTEST_STATISTICAL_REPORT_V1'
        assert report['population']['selection_attempt_count'] == report['metrics']['n_trials'] == total
        assert sum(a['policy_bound'] and a['series_status'] == 'READY' for a in report['attempts']) == 3
        assert calls == [(3, policy['sample'], total)]
        if damage == 'other-history':
            assert sum(a['series_status'] == 'OUTSIDE_COMPARISON_SCOPE' for a in report['attempts']) == 3
            assert 'GLOBAL_DSR_VARIANCE_NOT_COMPARABLE' in report['reasons']
            assert report['checks']['dsr'] == 'INSUFFICIENT' and report['metrics']['dsr'] is None
        assert report['metrics']['selected_cash']['full_run']['reconciliation_residual'] == '0'
        assert 'SAMPLE_TOO_SHORT' in report['reasons'] and report['statistical_decision'] in ('FAIL', 'INSUFFICIENT')
        assert 'FORWARD_NOT_HISTORICAL_OOS' not in report['limitations']
        assert all(report[k] is False for k in ('independent_oos_verified', 'promotion_approved',
            'submission_enabled', 'economic_authority'))
        if damage == 'cohort':
            value['cohort']['attempts'].pop()
        elif damage == 'policy':
            value['policy']['sample']['end_ms'] += 1000
        elif damage == 'report':
            value['report']['metrics']['n_trials'] = 2
        if damage in ('normal', 'other-history'):
            assert evidence.validate(value, project_cut=cut, reports=reports) == value
        else:
            with pytest.raises(ValueError):
                evidence.validate(value, project_cut=cut, reports=reports)
    assert physical(directory) == native_before and physical(original) == before
    monkeypatch.setattr(f['run'].gold, '_execute', lambda *a, **kw: pytest.fail('cold cannot execute'))
    assert job.recover_research(dict(native_invocation_id=plan['invocation_id']))['status'] == 'TECHNICAL_VALIDATION_ONLY'


@pytest.mark.parametrize('damage', ['source', 'dataset', 'input', 'sample', 'selection',
    'benchmark', 'policy-hash', 'oos-sample', 'horizon', 'authority', 'method', 'profile'])
def test_gold_statistical_policy_mismatch_is_rejected_before_the_plan(older_source, damage):
    f, original, _, _, _ = older_source
    before = physical(original)
    core, policy = declared(older_source)
    if damage in ('source', 'dataset'):
        policy[damage+'_identity_sha256' if damage == 'source' else 'dataset_manifest_sha256'] = '8'*64
    elif damage == 'input':
        policy['benchmark_policy']['input_sha256'] = '8'*64
    elif damage == 'sample':
        policy['sample']['start_ms'] += 1000
    elif damage == 'selection':
        policy['selection_policy']['candidate_id'] = 'trend'
    elif damage == 'benchmark':
        policy['benchmark_policy']['definition_sha256'] = '8'*64
    elif damage == 'policy-hash':
        core['selection']['validation_policy_sha256'] = '8'*64
    elif damage == 'oos-sample':
        policy['sample']['end_ms'] = core['splits']['locked_oos']['end_ms']
    elif damage == 'horizon':
        policy['split_policy'].update(maximum_information_horizon_ms=1000, embargo_ms=1000)
    elif damage == 'authority':
        policy['economic_authority'] = True
    elif damage == 'profile':
        policy['profile'] = 'RESEARCH_PRESCREEN_V1'
        policy['dsr_policy']['minimum_probability'] = '0.6'
        policy['pbo_policy']['maximum_probability'] = '0.5'
    else:
        policy['sample_method'] = 'CANONICAL_FLAT_TERMINAL_CASH_EXACT_GRID_V1'
    if damage != 'policy-hash':
        core['selection']['validation_policy_sha256'] = digest(policy)
    with pytest.raises(ValueError):
        prepare(older_source, core, policy)
    assert not (f['root']/'project').exists() and physical(original) == before


def test_gold_policy_cannot_be_added_after_an_existing_core_plan(older_source):
    from tools import registered_gold_chunked_job as job
    f, original, options, collector, _ = older_source
    core, policy = declared(older_source)
    plan = job.prepare_source_reanalysis(original, collection_source_identity=collector,
        evaluation_core=core, **options)
    directory = f['root']/'project/runs'/plan['invocation_id']
    before = physical(directory)
    with pytest.raises(ValueError, match='STATISTICAL_POLICY_NOT_RETAINED_BEFORE_PLAN'):
        prepare(older_source, core, policy)
    assert physical(directory) == before


def export_backtest(case, monkeypatch, *, oos_material=False):
    from tools import gold_chunked_source as carrier
    from tools import registered_gold_chunked_job as job
    from tools import registered_research_review_job as review
    from tools.perpetual_replay_research import source_identity
    f, original, options, collector, _ = case
    if oos_material:
        from tests.tools.test_gold_reanalysis_evaluation import historical_oos_clock
        historical_oos_clock(case, monkeypatch)
    actual_source = source_identity()
    monkeypatch.setattr(carrier, 'source_identity', lambda: actual_source)
    monkeypatch.setattr(f['run'].gold, 'source_identity', lambda: actual_source)
    case = f, original, options, collector, actual_source
    core, policy = declared(case, oos_material=oos_material)
    plan = prepare(case, core, policy)
    job.run_research(dict(native_invocation_id=plan['invocation_id']))
    original_native = physical(f['root']/'project')
    population = f['store'].research_evidence_population()
    f['store'].upgrade_research_evidence_schema(expected_version=f['store']._schema_version(),
        expected_population_sha256=population['sha256'])
    assert plan['search']['source'] == 'REGISTERED_GOLD_REANALYSIS_RUNNER_V1'
    assert plan['source_identity'] == review.source_identity()
    exported = review.prepare_review_request(dict(native_invocation_id=plan['invocation_id']))
    request = exported['review_request_id']
    packet = review.read_request(request)
    return f, plan, policy, original_native, request, packet


@pytest.mark.parametrize('oos_material', [False, True, 'review'])
def test_actual_gold_backtest_unsigned_export_signed_consumer_and_cold_registry(older_source, monkeypatch, oos_material):
    from tools import registered_research_review_job as review
    from tests.research.test_registered_research_review_job import independent_response
    from services.research.experiment_registry.store import SqliteExperimentStore
    from services.research.project_result_material_reader import ProjectResultMaterialReader
    from quantos.contracts.research_evidence_json import canonical
    f, plan, policy, original_native, request, packet = export_backtest(older_source, monkeypatch, oos_material=oos_material)
    assert packet['request']['schema'] == 'QUANTOS_INDEPENDENT_PROJECT_REVIEW_REQUEST_V3'
    assert packet['subject']['evidence_kind'] == 'BACKTEST'
    assert packet['statistical_evidence']['policy'] == policy
    assert 'STATISTICAL_REVIEW_NOT_IMPLEMENTED' not in packet['projection']['limitations']
    if oos_material:
        actual = packet['projection']['oos_material']
        assert actual['invocation_id'] == plan['invocation_id']
        assert actual['material_status'] == 'AVAILABLE' and actual['oos_status'] == 'UNKNOWN'
        assert actual['candidate']['window']['sample_count'] == 2
        assert actual['statistical_decision'] == 'NOT_EVALUATED' and actual['independent_oos_verified'] is False
        if oos_material == 'review':
            assert actual['economic_review']['economic_decision'] == 'FAIL'
            assert actual['economic_review']['policy_sha256'] == digest(plan['evaluation_core']['oos_evaluation_policy'])
    trust = f['root']/'synthetic-review/trust.json'
    independent_response(packet, review.response_directory(request), trust)
    registry = json.loads(trust.read_bytes())
    registry['producer_ids'] = ['REGISTERED_GOLD_REANALYSIS_RUNNER_V1']
    trust.write_bytes(canonical(registry))
    trust.chmod(0o444)
    monkeypatch.setenv('QUANTOS_PROJECT_REVIEW_TRUST_FILE', str(trust))
    monkeypatch.setenv('QUANTOS_PROJECT_REVIEW_TRUST_SHA256', digest(trust.read_bytes()))
    monkeypatch.setenv('QUANTOS_PROJECT_REVIEW_MIN_GENERATION', '1')
    if oos_material:
        later = time.time_ns() + 1_000_000
        monkeypatch.setattr(time, 'time_ns', lambda: later)
    result = review.consume_review(dict(review_request_id=request))
    assert result['authentication'] == 'VERIFIED_SOURCE_AND_RESULT'
    assert result['qualification'] in (('REJECTED',) if oos_material == 'review' else ('FAIL', 'INSUFFICIENT'))
    assert result['economic_authority'] is result['promotion_approved'] is result['submission_enabled'] is False
    monkeypatch.setattr(f['run'].gold, '_execute', lambda *a, **kw: pytest.fail('review cold cannot execute'))
    assert review.recover_review(dict(review_request_id=request))['review_id'] == result['review_id']
    cold = SqliteExperimentStore(str(f['root']/'registry.db'), create=False, read_only=True,
        project_result_reader=ProjectResultMaterialReader(f['root']/'project'))
    try:
        assert cold.get_project_research_evidence(result['review_id'])['verdict'] in (
            ('REJECTED',) if oos_material == 'review' else ('FAIL', 'INSUFFICIENT'))
        assert cold._conn.execute('SELECT count(*) FROM evaluations').fetchone()[0] == 1
        assert cold._conn.execute('SELECT count(*) FROM evaluation_audit_outbox').fetchone()[0] == 1
        assert cold._conn.execute('SELECT count(*) FROM search_evaluations').fetchone()[0] == 0
    finally:
        cold.close()
    assert physical(f['root']/'project') == original_native


@pytest.mark.parametrize('floor', ['minimum_net_return', 'minimum_benchmark_excess',
    'minimum_observations', 'minimum_duration_ms', 'maximum_drawdown'])
def test_oos_policy_cannot_relax_bound_training_economics_before_execution(older_source, monkeypatch, floor):
    from tests.tools.test_gold_reanalysis_evaluation import historical_oos_clock
    historical_oos_clock(older_source, monkeypatch)
    core, policy = declared(older_source, oos_material='review')
    tightened = {'minimum_net_return': '0.1', 'minimum_benchmark_excess': '0.1',
        'minimum_observations': 31, 'minimum_duration_ms': 31*86_400_000, 'maximum_drawdown': '0.04'}
    policy['economic_checks'][floor] = tightened[floor]
    core['selection']['validation_policy_sha256'] = digest(policy)
    before = physical(older_source[0]['root'])
    with pytest.raises(ValueError, match='GOLD_OOS_RELAXED'):
        prepare(older_source, core, policy)
    assert physical(older_source[0]['root']) == before


def test_core_geometry_cannot_shorten_the_actual_native_information_episode(older_source):
    from tools import registered_gold_chunked_job as job
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation import gold_project_statistics as study
    from services.validation import project_research_statistical_evidence as evidence
    f, _, _, _, _ = older_source
    core, policy = declared(older_source)
    horizon = core['sample']['end_ms']-core['sample']['start_ms']+core['sample']['frequency_ms']
    policy['split_policy'].update(maximum_information_horizon_ms=horizon, embargo_ms=horizon)
    core['selection']['validation_policy_sha256'] = digest(policy)
    plan = prepare(older_source, core, policy)
    job.run_research(dict(native_invocation_id=plan['invocation_id']))
    with ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db').evidence() as (cut, reports):
        cohort = study.project_cohort(project_cut=cut, invocation_id=plan['invocation_id'], policy=policy, reports=reports)
        with pytest.raises(ValueError, match='GOLD_BACKTEST_EPISODE_HORIZON'):
            evidence.prepare(project_cut=cut, policy=policy, cohort=cohort,
                as_of_ms=cohort['frozen_at_ms'], reports=reports)
