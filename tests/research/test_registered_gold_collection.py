"""AI/collector requests must register every original Gold trial before execution."""
import copy
import json
import shutil

import pytest

from tests.tools.test_registered_research_run import technical_arguments as technical_arguments
from tests.tools.test_gold_bar_observe import recorded
from tests.research.test_gold_bar_dataset import raw_rows, STEP
from tests.execution.test_perpetual_public_replay import START, gold_capture_fixture


def request(tmp_path, monkeypatch, technical_arguments, *, portfolio=False):
    from tools import gold_bar_experiment, gold_bar_research
    rows = raw_rows()
    shift = START // STEP * STEP - 1001 * STEP
    for row in rows:
        row[0] += shift
        row[6] += shift
    _, journal = recorded(tmp_path, monkeypatch, values=rows, received=START+1500)
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    source = copy.deepcopy(technical_arguments['release_identity'])
    for module in (gold_bar_experiment, gold_bar_research):
        monkeypatch.setattr(module, 'source_identity', lambda: source)
    root = tmp_path/'data/cache/gold_bar_research'
    dest = root/'requests/1'
    dest.mkdir(parents=True)
    shutil.copytree(journal, dest/'journal')
    (dest/'quotes.json').write_text(json.dumps(gold_capture_fixture()))
    plan = dict(quantity='.1', initial_collateral='1000', fee_rate='.001',
        release_identity=source, register_original_trials=True)
    if portfolio:
        plan['portfolio_plan'] = dict(quantities={n:'.1' for n in ('channel','trend','reversion')}, max_notional='100')
    (dest/'plan.json').write_text(json.dumps(plan))
    return root


@pytest.mark.parametrize('portfolio', [False, True])
def test_collected_request_uses_original_registry_and_one_native_invocation(tmp_path, monkeypatch, technical_arguments, portfolio):
    from tools import registered_gold_collection as bridge
    from tools import registered_research_job as job
    from tools import registered_research_run as native
    from services.research.experiment_registry.store import SqliteExperimentStore
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from tests.research.test_registered_research_job import queued
    root = request(tmp_path, monkeypatch, technical_arguments, portfolio=portfolio)
    expected = 4 if portfolio else 3
    original = native._native_run
    calls = []

    def observed(**kwargs):
        execute = kwargs['execute']
        def compute():
            _, database = job.workspace_paths()
            store = SqliteExperimentStore(str(database), create=False, read_only=True)
            try:
                assert len(store.research_evidence_population()['provenance']) == expected
            finally:
                store.close()
            calls.append(1)
            return execute()
        return original(**{**kwargs, 'execute':compute})

    monkeypatch.setattr(native, '_native_run', observed)
    registry, queue = queued(tmp_path)
    uid = queue.submit('gold_bar_research', {'attempt':1}, 1, invocation_id='registered-gold')
    queue.join()
    run = registry.get(uid)
    assert run.status == 'done', run.error
    assert calls == [1]
    project, database = job.workspace_paths()
    cut = ProjectResearchEvidenceReader(project, registry_database=database)()
    assert cut['attempt_count'] == expected and cut['population_coverage'] == 'COMPLETE'
    assert {row['status'] for row in cut['attempts']} == {'PASSED'}
    native_id = bridge.invocation_id(1)
    original_dir = project/'runs'/native_id/'archive'
    mirror = root/'results/1'
    for name in ('input.json','report.json','manifest.json','trials.db'):
        assert (original_dir/name).read_bytes() == (mirror/name).read_bytes()
    report = json.loads(run.metrics['gold_bar_report_json'])
    assert report['native_registration']['native_invocation_id'] == native_id
    assert report['native_registration']['attempt_count'] == expected
    assert report['economic_eligible'] is report['submission_enabled'] is False
    assert report['candidates'][0]['account']['ending_equity'] == '999.97999'
    before = database.read_bytes()
    uid = queue.submit('gold_bar_recover', {'attempt':1}, 2, invocation_id='registered-gold-recover')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    assert calls == [1] and database.read_bytes() == before


@pytest.mark.parametrize(('portfolio', 'publication_failure', 'native_interruption'),
    [(False, False, False), (True, False, False), (False, True, False), (False, False, True)])
def test_ai_collector_prepares_registered_trials_automatically(tmp_path, monkeypatch,
        technical_arguments, portfolio, publication_failure, native_interruption):
    from tests.tools.test_gold_bar_program import test_collection_enters_original_queue_and_keeps_candidate_results
    from tools import registered_research_job as job, gold_bar_research
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    if native_interruption:
        from tools import registered_research_run as native
        original = native._put
        remaining = [True]
        def interrupted(path, *args, **kwargs):
            if path.name == 'outcome.json' and remaining[0]:
                remaining[0] = False
                raise RuntimeError('ORIGINAL_OUTCOME_PUBLICATION_INTERRUPTED')
            return original(path, *args, **kwargs)
        monkeypatch.setattr(native, '_put', interrupted)
    test_collection_enters_original_queue_and_keeps_candidate_results(tmp_path, monkeypatch,
        ai_plan=True, review_failure=False, publication_failure=publication_failure,
        portfolio=portfolio, registered_source=copy.deepcopy(technical_arguments['release_identity']))
    project, database = job.workspace_paths()
    cut = ProjectResearchEvidenceReader(project, registry_database=database)()
    assert cut['attempt_count'] == (4 if portfolio else 3)
    assert cut['population_coverage'] == 'COMPLETE'
    assert {row['status'] for row in cut['attempts']} == {'PASSED'}
    root = tmp_path/'state/data/cache/gold_bar_research'
    assert json.loads((root/'programs/1/plan.json').read_text())['register_original_trials'] is True
    assert json.loads((root/'requests/1/plan.json').read_text())['register_original_trials'] is True
    row = json.loads((root/'programs/1/round-1.json').read_text())
    retained = gold_bar_research.retained_publication(root/'results/1', 1)
    assert retained['report'] == row['report']
    assert retained['report']['native_registration']['evaluation_plan'] == 'WINDOW_DECLARED_UNVERIFIED'
    if native_interruption:
        assert row['recovered_from'] != row['run_id']


@pytest.mark.parametrize('unknown', [False, True])
def test_failed_and_unknown_native_attempts_never_recompute(tmp_path, monkeypatch,
        technical_arguments, unknown):
    from tools import gold_bar_experiment as producer, registered_research_run as native
    from tools import registered_gold_collection as bridge, registered_research_job as job
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from tests.research.test_registered_research_job import queued
    request(tmp_path, monkeypatch, technical_arguments)
    calls = []
    original_execute = producer._execute
    original_put = native._put
    def compute(arguments):
        calls.append(1)
        if not unknown:
            raise RuntimeError('ORIGINAL_COMPUTATION_FAILED')
        return original_execute(arguments)
    def interrupted_put(path, *args, **kwargs):
        if path.name == 'report.json':
            raise RuntimeError('NATIVE_REPORT_NOT_DURABLE')
        return original_put(path, *args, **kwargs)
    monkeypatch.setattr(producer, '_execute', compute)
    if unknown:
        monkeypatch.setattr(native, '_put', interrupted_put)
    registry, queue = queued(tmp_path)
    uid = queue.submit('gold_bar_research', {'attempt':1}, 1, invocation_id='native-failure')
    queue.join()
    assert registry.get(uid).status == 'error'
    assert calls == [1]
    monkeypatch.setattr(native, '_put', original_put)
    project, database = job.workspace_paths()
    directory = project/'runs'/bridge.invocation_id(1)
    before = {str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    for kind in ('gold_bar_recover', 'gold_bar_research'):
        uid = queue.submit(kind, {'attempt':1}, 2, invocation_id=kind+'-failed-original')
        queue.join()
        assert registry.get(uid).status == 'error'
        if unknown:
            assert 'UNKNOWN_INVOCATION_OUTCOME' in registry.get(uid).error
    assert calls == [1]
    assert before == {str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    cut = ProjectResearchEvidenceReader(project, registry_database=database)()
    assert cut['attempt_count'] == 3 and cut['population_coverage'] == 'COMPLETE'
    assert (bool(cut['unfinished_attempts'])) == unknown
    if not unknown:
        assert {row['status'] for row in cut['attempts']} == {'FAILED'}


@pytest.mark.parametrize('change', ['quantity', 'remove_mode'])
def test_changed_request_cannot_allocate_another_native_invocation(tmp_path, monkeypatch,
        technical_arguments, change):
    from tools import registered_gold_collection as bridge, registered_research_job as job
    from tests.research.test_registered_research_job import queued
    root = request(tmp_path, monkeypatch, technical_arguments)
    registry, queue = queued(tmp_path)
    uid = queue.submit('gold_bar_research', {'attempt':1}, 1, invocation_id='before-change')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    project, database = job.workspace_paths()
    directory = project/'runs'/bridge.invocation_id(1)
    before = database.read_bytes()
    original = {str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    path = root/'requests/1/plan.json'
    plan = json.loads(path.read_text())
    if change == 'quantity':
        plan['quantity'] = '.2'
    else:
        del plan['register_original_trials']
    path.write_text(json.dumps(plan))
    uid = queue.submit('gold_bar_research', {'attempt':1}, 2, invocation_id='after-change')
    queue.join()
    assert registry.get(uid).status == 'error'
    assert database.read_bytes() == before
    assert original == {str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert len([p for p in (project/'runs').iterdir() if p.is_dir()]) == 1


def test_standalone_history_cannot_be_registered_retrospectively(tmp_path, monkeypatch, technical_arguments):
    from tools import gold_bar_research, registered_research_job as job
    root = request(tmp_path, monkeypatch, technical_arguments)
    mirror = root/'results/1'
    mirror.mkdir(parents=True, mode=0o700)
    (mirror/'history.json').write_bytes(b'original diagnostic history')
    with pytest.raises(ValueError, match='GOLD_STANDALONE_HISTORY_PRESENT'):
        gold_bar_research.run_research({'attempt':1})
    assert (mirror/'history.json').read_bytes() == b'original diagnostic history'
    assert not job.workspace_paths()[1].exists()


@pytest.mark.parametrize('extended', [False, True])
def test_registered_program_cold_read_keeps_original_relations_and_bytes(tmp_path, monkeypatch, technical_arguments, extended):
    from tests.tools.test_demo_gold_research_checkpoint import native_program, read_jobs, originals
    from tools import gold_bar_program
    f = native_program(tmp_path, monkeypatch,
        source=copy.deepcopy(technical_arguments['release_identity']), register_original_trials=True,
        extended=extended)
    root = f['root']/'data/cache/gold_bar_research'
    before = originals(f['root'])
    value = gold_bar_program.verify_retained_program(root/'programs/1',
        requests_root=root/'requests', results_root=root/'results',
        jobs=read_jobs(f['root']/'data/cache/research_runs.db'), original_source=f['source'])
    assert value['summary'] == f['result'] and value['partial'] == []
    if extended:
        arguments = json.loads((root/'results/1/input.json').read_bytes())
        assert arguments['settlement_window_ms'] == 1000
        assert len(arguments['observed_funding_records']) == 1
        assert value['summary']['rounds'][0]['report']['native_registration']['attempt_count'] == 4
        from quantos.contracts.registered_research_run import canonical
        with pytest.raises(ValueError, match='REGISTERED_RESEARCH_COMPLEXITY'):
            canonical(json.loads((root/'results/1/report.json').read_bytes()))
    assert originals(f['root']) == before


def test_declared_funding_without_receipt_keeps_original_failed_trials(tmp_path, monkeypatch, technical_arguments):
    from tests.tools.test_demo_gold_research_checkpoint import native_program, read_jobs
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from tools import registered_research_job as job
    with pytest.raises(ValueError, match='REGISTERED_RESEARCH_FAILED'):
        native_program(tmp_path, monkeypatch, source=copy.deepcopy(technical_arguments['release_identity']),
            extended=True, register_original_trials=True, funding_receipt=False)
    cut = ProjectResearchEvidenceReader(job.workspace_paths()[0], registry_database=job.workspace_paths()[1])()
    assert cut['attempt_count'] == 4 and cut['population_coverage'] == 'COMPLETE'
    assert all(row['status'] == 'FAILED' for row in cut['attempts'])
    runs = read_jobs(tmp_path/'state/data/cache/research_runs.db')
    assert len(runs) == 1 and next(iter(runs.values()))['error'] == 'FUNDING_RECORDS_REQUIRED'


@pytest.mark.parametrize('damage', ['removed', 'proposal'])
def test_publication_recovery_cannot_reuse_original_report_with_changed_declaration(tmp_path,
        monkeypatch, technical_arguments, damage):
    from tests.tools.test_demo_gold_research_checkpoint import native_program, originals
    from tools import registered_gold_collection as bridge, registered_research_job as job
    f = native_program(tmp_path, monkeypatch,
        source=copy.deepcopy(technical_arguments['release_identity']), register_original_trials=True)
    root = f['root']/'data/cache/gold_bar_research'
    path = root/'requests/1/plan.json'
    request_plan = json.loads(path.read_bytes())
    if damage == 'removed':
        del request_plan['forward_design']
    else:
        request_plan['forward_design']['selection']['proposal_sha256'] = 'b'*64
    path.write_text(json.dumps(request_plan))
    before = originals(job.workspace_paths()[0])
    with pytest.raises(ValueError, match='GOLD_RECOVERY_DECLARATION_BINDING'):
        bridge.publication_recoverable({'attempt':1})
    assert originals(job.workspace_paths()[0]) == before


def test_ai_program_freezes_design_before_capture_and_binds_original_native_plan(tmp_path,
        monkeypatch, technical_arguments):
    from tests.tools.test_gold_bar_program import test_collection_enters_original_queue_and_keeps_candidate_results
    from tools import gold_bar_program as program, registered_gold_collection as bridge, registered_research_job as job
    original = program.collect_joint
    observed = []
    async def collect(*args, **kwargs):
        path = tmp_path/'state/data/cache/gold_bar_research/programs/1/evaluation-design-1.json'
        value = json.loads(path.read_text())
        assert value['declared_at_ms'] < value['window']['observation_start_ms']
        assert not (tmp_path/'state/data/cache/gold_bar_research/requests/1/plan.json').exists()
        observed.append(value)
        return await original(*args, **kwargs)
    monkeypatch.setattr(program, 'collect_joint', collect)
    test_collection_enters_original_queue_and_keeps_candidate_results(tmp_path, monkeypatch,
        ai_plan=True, review_failure=False, publication_failure=False,
        registered_source=copy.deepcopy(technical_arguments['release_identity']))
    assert len(observed) == 1
    project, _ = job.workspace_paths()
    plan = json.loads((project/'runs'/bridge.invocation_id(1)/'plan.json').read_text())
    assert plan['forward_design'] == observed[0]
    assert plan['schema'] == 'QUANTOS_REGISTERED_GOLD_FORWARD_RUN_V1'
    assert observed[0]['declared_at_ms'] < plan['search']['recorded_at_ms']
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    cut = ProjectResearchEvidenceReader(project, registry_database=job.workspace_paths()[1])()
    assert cut['attempt_count'] == 3 and cut['population_coverage'] == 'COMPLETE'
    from services.validation.project_research_population import validate_project_cut
    from services.validation.project_research_evidence_authority import project_facts, project_subject
    assert validate_project_cut(cut) == cut
    subject = project_subject(cut, invocation_id=plan['invocation_id'],
        native_trial_id=plan['trials'][0]['native_trial_id'])
    facts = project_facts(cut, subject)
    assert 'FORWARD_WINDOW_DECLARED_NOT_INDEPENDENTLY_VERIFIED' in facts['limitations']
    assert 'EVALUATION_PLAN_NOT_PREREGISTERED' not in facts['limitations']
    assert facts['evaluation_core'] is None and facts['metrics'] is not None
    assert facts['metrics']['statistics_status'] == 'INSUFFICIENT_SAMPLE_COVERAGE'
    assert 'GOLD_EVALUATION_CORE_ADAPTER_UNAVAILABLE' in facts['limitations']
    assert facts['promotion_approved'] is facts['submission_enabled'] is False


def test_slow_model_cannot_declare_after_bar_opens_or_start_collection(tmp_path, monkeypatch,
        technical_arguments):
    from tools import gold_bar_program as program, registered_research_job as job
    from tests.tools.test_gold_bar_program import test_collection_enters_original_queue_and_keeps_candidate_results
    original = program.bounded_proposal
    def slow_model(*args, **kwargs):
        result = original(*args, **kwargs)
        clock = program.time.time_ns
        monkeypatch.setattr(program.time, 'time_ns', lambda: clock()+180000*1000000)
        return result
    monkeypatch.setattr(program, 'bounded_proposal', slow_model)
    with pytest.raises(ValueError, match='GOLD_FORWARD_DESIGN_LATE_DECLARATION'):
        test_collection_enters_original_queue_and_keeps_candidate_results(tmp_path, monkeypatch,
            ai_plan=True, review_failure=False, publication_failure=False,
            registered_source=copy.deepcopy(technical_arguments['release_identity']))
    root = tmp_path/'state/data/cache/gold_bar_research'
    assert (root/'programs/1/hypothesis-1/result.json').exists()
    assert not (root/'programs/1/evaluation-design-1.json').exists()
    assert not (root/'requests').exists()
    project, database = job.workspace_paths()
    assert not project.exists() and not database.exists()


@pytest.mark.parametrize('damage', ['window', 'quantity', 'source', 'undeclared_funding'])
def test_native_prepare_rejects_input_not_matching_declaration_before_writing(tmp_path,
        monkeypatch, technical_arguments, damage):
    from quantos.contracts import gold_forward_evaluation_design as design
    from tools import gold_bar_research as gold, registered_research_run as native
    root = request(tmp_path, monkeypatch, technical_arguments)
    arguments, _ = gold._arguments({'attempt':1})
    close = START//STEP*STEP
    source = copy.deepcopy(technical_arguments['release_identity'])
    config = dict(quantity='0.1', initial_collateral='1000', fee_rate='0.001')
    if damage == 'quantity':
        config['quantity'] = '0.2'
    elif damage == 'source':
        source['commit'] = 'e'*40
    elif damage == 'undeclared_funding':
        arguments['settlement_window_ms'] = 500
    declared = design.declare(program_id=1, attempt=1, source_identity=source, configuration=config,
        first_close_ms=close+STEP if damage == 'window' else close, window_bars=1,
        declared_at_ms=close-STEP-180000)
    project = tmp_path/'prospective-project'
    with pytest.raises(ValueError, match='GOLD_FORWARD_DESIGN_'):
        native.prepare_gold(arguments, project_root=project, search_id='prospective',
            producer_id='REGISTERED_GOLD_COLLECTOR_V1', actor_id='AUTOMATED_RESEARCH',
            dataset_version='recorded-public', registered_at_ms=START+100000, forward_design=declared)
    assert not project.exists()
    assert not (root/'results').exists()


@pytest.mark.parametrize('change', ['marker', 'archive'])
def test_readonly_registration_rejects_changed_mirror_without_repair(tmp_path, monkeypatch,
        technical_arguments, change):
    from tools import registered_gold_collection as bridge, registered_research_job as job
    from tests.research.test_registered_research_job import queued
    root = request(tmp_path, monkeypatch, technical_arguments)
    registry, queue = queued(tmp_path)
    uid = queue.submit('gold_bar_research', {'attempt':1}, 1, invocation_id='mirror-original')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    project, database = job.workspace_paths()
    directory = project/'runs'/bridge.invocation_id(1)
    before = database.read_bytes()
    original = {str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    mirror = root/'results/1'
    path = mirror/(bridge.MARKER if change == 'marker' else 'report.json')
    value = json.loads(path.read_text())
    value['submission_enabled'] = True
    path.write_text(json.dumps(value))
    changed = path.read_bytes()
    with pytest.raises(ValueError, match='GOLD_MIRROR'):
        bridge.retained_registration(mirror, 1)
    assert path.read_bytes() == changed and database.read_bytes() == before
    assert original == {str(p.relative_to(directory)):p.read_bytes() for p in directory.rglob('*') if p.is_file()}
