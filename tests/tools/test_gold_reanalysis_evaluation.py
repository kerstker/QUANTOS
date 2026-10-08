"""Gold evaluation commitments precede computation, without asserting locked OOS."""
import copy
import hashlib
import json
import time

import pytest

from tests.tools.test_gold_source_reanalysis import older_source as older_source, physical
from tests.tools.test_gold_source_reanalysis import short_source as short_source
from tests.tools.test_gold_source_reanalysis import registered_case as registered_case
from tests.validation.test_research_evaluation_plan import core

GOLD_CORE = 'QUANTOS_GOLD_REANALYSIS_EVALUATION_CORE_V1'


def declaration(case):
    from tools import gold_chunked_source as carrier
    from quantos.contracts import registered_research_run as native
    f, root, options, collector, consumer = case
    with carrier.NativeSource(root, collection_source_identity=collector,
            replay_budget=options['source_storage'].get('replay_budget')) as inputs:
        document = copy.deepcopy(inputs.document)
    raw = f['run'].gold._bytes(document)
    input_hash = hashlib.sha256(raw).hexdigest()
    cohort = []
    for candidate in f['run'].gold.trial_candidates(document):
        config = f['run'].gold.gold_trial_definition_id(input_sha256=input_hash,
            candidate=candidate, source=consumer)
        cohort.append(dict(candidate_id=candidate, config_hash=config,
            native_trial_id=native.native_trial_id(options['invocation_id'], candidate, config)))
    value = core()
    start = document['forward_input']['closed_observations'][0][1]
    value.update(schema=GOLD_CORE, evidence_kind='BACKTEST', native_adapter=native.GOLD,
        registered_at_ms=options['registered_at_ms'], input_sha256=input_hash,
        source_identity_sha256=native.digest(consumer),
        dataset_manifest_sha256=document['source_binding']['manifest_sha256'], cohort=cohort,
        budget=dict(max_trials=len(cohort), max_invocations=1),
        sample=dict(start_ms=start, end_ms=start+6000, frequency_ms=1000),
        splits=dict(train=dict(start_ms=start, end_ms=start+1000),
            validation=dict(start_ms=start+2000, end_ms=start+3000),
            locked_oos=dict(start_ms=start+4000, end_ms=start+6000)))
    value['selection']['candidate_id'] = cohort[0]['candidate_id']
    return value


def prepare(case, evaluation):
    from tools import registered_gold_chunked_job as job
    _, root, options, collector, _ = case
    return job.prepare_source_reanalysis(root, collection_source_identity=collector,
        evaluation_core=evaluation, **options)


@pytest.mark.parametrize('budgeted', [False, True])
def test_original_queue_commits_gold_splits_in_registry_and_ledger_before_execution(
        older_source, monkeypatch, budgeted):
    from services.validation import gold_native_statistics as statistics
    from tests.research.test_registered_research_job import queued
    from services.research.experiment_registry.store import SqliteExperimentStore
    from services.research.experiment_registry.registry import ExperimentRegistry
    from services.research.trial_ledger import TrialLedger
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.validation.project_research_evidence_authority import project_subject, project_facts
    from services.validation.project_research_statistics import project_native_series, _identity
    from quantos.contracts.research_statistical_review import METHODS
    from quantos.contracts.research_evaluation_plan import core_sha256
    from quantos.contracts import registered_research_run as native
    f, root, options, _, _ = older_source
    if budgeted:
        options['source_storage'].update(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V2',
            replay_budget=dict(schema='LINEAR_PERPETUAL_REPLAY_BUDGET_V1',
                maximum_environment_segments=2, maximum_elapsed_ms=30*86_400_000))
    before = physical(root)
    evaluation = declaration(older_source)
    committed = copy.deepcopy(evaluation)
    plan = prepare(older_source, evaluation)
    assert plan['schema'] == ('QUANTOS_REGISTERED_GOLD_REANALYSIS_RUN_V4' if budgeted
                             else 'QUANTOS_REGISTERED_GOLD_REANALYSIS_RUN_V3')
    assert plan['schema'] in native.EVALUATED_SCHEMAS
    assert plan['evaluation_core'] == committed and plan['search']['search_kind'] == 'BACKTEST'
    directory = f['root']/'project/runs'/plan['invocation_id']
    expected = core_sha256(committed)
    execute = f['run'].gold._execute
    calls = []

    def checked(*args, **kwargs):
        saved = json.loads((directory/'plan.json').read_bytes())
        assert saved['evaluation_core'] == committed
        store = SqliteExperimentStore(str(f['root']/'registry.db'), create=False, read_only=True)
        original_registry = ExperimentRegistry(store)
        with TrialLedger(str(directory/'archive/trials.db'), create=False, read_only=True) as ledger:
            for trial in plan['trials']:
                events = ledger.load(trial['native_trial_id']).events
                assert [e.status for e in events] == ['PLANNED', 'RUNNING']
                assert all(e.validation_policy_digest == expected for e in events)
                record = original_registry.trial_provenance(trial['provenance_trial_id'])['record']
                assert record['artifact_hashes'][0] == f['run'].plan_digest(saved)
        store.close()
        calls.append(1)
        return execute(*args, **kwargs)

    monkeypatch.setattr(f['run'].gold, '_execute', checked)
    evaluation['splits']['locked_oos']['start_ms'] += 1000
    assert plan['evaluation_core'] == committed
    registry, queue = queued(f['root'])
    params = dict(native_invocation_id=plan['invocation_id'])
    uid = queue.submit('registered_gold_chunked', params, time.time_ns()//1_000_000,
        invocation_id='gold-evaluation')
    queue.join()
    result = registry.get(uid)
    assert result.status == 'done', result.error
    assert calls == [1]
    samples = []
    native_series = statistics._series

    def scoped_series(simulation, sample, settlements, **kwargs):
        samples.append((copy.deepcopy(sample), kwargs.get('evidence_kind')))
        return native_series(simulation, sample, settlements, **kwargs)

    monkeypatch.setattr(statistics, '_series', scoped_series)
    with ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db').evidence() as (cut, reports):
        subject = project_subject(cut, invocation_id=plan['invocation_id'],
            native_trial_id=plan['trials'][0]['native_trial_id'])
        facts = project_facts(cut, subject, reports=reports)
        assert subject['evidence_kind'] == 'BACKTEST'
        assert facts['evaluation_core']['core_sha256'] == expected
        assert facts['evaluation_core']['oos_status'] == 'UNKNOWN'
        assert 'OOS_ACCESS_NOT_INDEPENDENTLY_VERIFIED' in facts['limitations']
        assert 'EVALUATION_PLAN_NOT_PREREGISTERED' not in facts['limitations']
        assert 'FORWARD_NOT_HISTORICAL_OOS' not in facts['limitations']
        assert facts['metrics']['reconciliation_residual'] == '0'
        assert samples == [(dict(start_ms=committed['splits']['train']['start_ms'],
            end_ms=committed['splits']['validation']['end_ms'], frequency_ms=1000), 'BACKTEST')]
        assert facts['promotion_approved'] is facts['submission_enabled'] is False
        attempt = next(a for a in cut['attempts'] if a['native_trial_id'] == plan['trials'][0]['native_trial_id'])
        series = project_native_series(project_cut=cut, attempt=_identity(attempt),
            sample=samples[0][0], sample_method=next(iter(METHODS)))
        assert series['status'] == 'INSUFFICIENT'
        assert series['reasons'] == ['GOLD_BACKTEST_STATISTICAL_ADAPTER_UNAVAILABLE']
        assert not series['points'] and series['native_result_sha256'] is None
    native_before = physical(directory)
    monkeypatch.setattr(f['run'].gold, '_execute', lambda *a, **kw: pytest.fail('cold read cannot execute'))
    uid = queue.submit('registered_gold_chunked_recover', params, time.time_ns()//1_000_000,
        invocation_id='gold-evaluation-cold')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    assert registry.get(uid).metrics == result.metrics and physical(directory) == native_before
    assert physical(root) == before


@pytest.mark.parametrize('field', ['input_sha256', 'source_identity_sha256',
    'dataset_manifest_sha256', 'registered_at_ms', 'cohort', 'forward', 'core-version'])
def test_gold_evaluation_mismatch_is_rejected_before_any_plan(older_source, field):
    f, root, _, _, _ = older_source
    before = physical(root)
    value = declaration(older_source)
    if field == 'registered_at_ms':
        value[field] -= 1
    elif field == 'cohort':
        value[field][0]['native_trial_id'] = '8'*64
    elif field == 'forward':
        value['evidence_kind'] = 'FORWARD'
    elif field == 'core-version':
        value['schema'] = 'QUANTOS_RESEARCH_EVALUATION_CORE_V2'
    else:
        value[field] = '8'*64
    with pytest.raises(ValueError):
        prepare(older_source, value)
    assert not (f['root']/'project').exists() and physical(root) == before


def test_completed_plain_reanalysis_cannot_acquire_an_evaluation_core(older_source):
    from tools import registered_gold_chunked_job as job
    f, root, options, collector, _ = older_source
    value = declaration(older_source)
    plan = job.prepare_source_reanalysis(root, collection_source_identity=collector, **options)
    job.run_research(dict(native_invocation_id=plan['invocation_id']))
    directory = f['root']/'project/runs'/plan['invocation_id']
    before = physical(directory)
    with pytest.raises(ValueError):
        prepare(older_source, value)
    assert physical(directory) == before


@pytest.mark.parametrize('mode', ['unverified', 'viewed', 'missing'])
def test_gold_core_never_authenticates_its_oos_declaration(older_source, mode):
    from quantos.contracts.research_evaluation_plan import core_status, validate_core
    value = declaration(older_source)
    if mode == 'viewed':
        value['oos_access']['viewed_boundary_ids'] = [value['oos_access']['boundary_id']]
    elif mode == 'missing':
        value['oos_access']['history_sha256'] = None
    before = copy.deepcopy(value)
    assert validate_core(value) == before and value == before
    status = core_status(value)
    assert status['oos_status'] == ('UNKNOWN' if mode == 'unverified' else 'INSUFFICIENT')
    assert status['submission_enabled'] is status['economic_authority'] is False


@pytest.mark.parametrize('damage', ['gap', 'missing-windows', 'overlap', 'off-grid', 'authority'])
def test_gold_statistical_window_cannot_cross_gaps_or_locked_oos(older_source, damage):
    f, root, _, _, _ = older_source
    before = physical(root)
    value = declaration(older_source)
    if damage == 'gap':
        value['splits']['validation']['start_ms'] += 1000
    elif damage == 'missing-windows':
        value['splits'].update(train=None, validation=None)
    elif damage == 'overlap':
        value['splits']['locked_oos']['start_ms'] = value['splits']['validation']['end_ms']
    elif damage == 'off-grid':
        value['splits']['validation']['end_ms'] += 1
    else:
        value['economic_authority'] = True
    with pytest.raises(ValueError):
        prepare(older_source, value)
    assert not (f['root']/'project').exists() and physical(root) == before


def test_original_forward_core_version_cannot_be_relabelled_as_gold_backtest():
    from quantos.contracts.research_evaluation_plan import validate_core
    value = core()
    value.update(native_adapter='GOLD_BAR_FORWARD_V1')
    with pytest.raises(ValueError):
        validate_core(value)


def test_missing_oos_declaration_remains_insufficient_and_keeps_the_declared_sample(older_source):
    from quantos.contracts.research_evaluation_plan import core_status, statistical_sample
    value = declaration(older_source)
    value['splits']['locked_oos'] = None
    value['oos_access'].update(boundary_id=None, history_sha256=None)
    assert statistical_sample(value) == value['sample']
    assert core_status(value)['oos_status'] == 'INSUFFICIENT'
