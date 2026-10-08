"""The real guarded queue must consume native preregistered originals."""
import json
import time

import pytest

from tests.tools.test_registered_research_run import technical_arguments as technical_arguments
from tests.tools.test_registered_research_evaluation import declaration
from tests.tools.test_registered_risk_replay import policy


def prepared(tmp_path, monkeypatch, arguments):
    from tools import registered_research_job as job
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    now = time.time_ns() // 1_000_000
    core = declaration({'clock': [now]}, arguments)
    plan = job.prepare_run(arguments, search_id='queued-original', dataset_version='native-fixture',
        registered_at_ms=now, invocation_id='9' * 64, evaluation_core=core,
        risk_review_policy=policy(core))
    project, database = job.workspace_paths()
    return job, plan, project / 'runs' / plan['invocation_id'], database


def queued(tmp_path):
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.registry import RunRegistry
    from services.research_runner.runner import JobQueue
    registry = RunRegistry(ResearchRunStore(str(tmp_path / 'queue.db')))
    return registry, JobQueue(registry)


def submit(queue, plan, *, recover=False):
    kind = 'registered_research_recover' if recover else 'registered_research'
    uid = queue.submit(kind, {'native_invocation_id': plan['invocation_id']},
        time.time_ns() // 1_000_000, invocation_id='recover' if recover else 'first')
    queue.join()
    return uid


def test_actual_queue_registers_before_native_execution_and_recovers_without_rerun(tmp_path, monkeypatch, technical_arguments):
    from tools import registered_technical_research_run as native
    from services.research.trial_ledger import TrialLedger
    from services.research.experiment_registry.store import SqliteExperimentStore
    job, plan, directory, database = prepared(tmp_path, monkeypatch, technical_arguments)
    registry, queue = queued(tmp_path)
    original = native.native.run_authoritative_linear_perpetual_v2
    calls = []

    def execute(**arguments):
        with TrialLedger(str(directory / 'archive/trials.db'), create=False, read_only=True) as ledger:
            assert [e.status for e in ledger.load(plan['trials'][0]['native_trial_id']).events] == ['PLANNED', 'RUNNING']
        store = SqliteExperimentStore(str(database), create=False, read_only=True)
        try:
            assert len(store.research_evidence_population()['provenance']) == 1
        finally:
            store.close()
        calls.append(1)
        return original(**arguments)

    monkeypatch.setattr(native.native, 'run_authoritative_linear_perpetual_v2', execute)
    uid = submit(queue, plan)
    run = registry.get(uid)
    assert run.status == 'done', run.error
    assert run.verdict == 'TECHNICAL_VALIDATION_ONLY'
    report = json.loads(run.metrics['registered_research_report_json'])
    assert report['native_trial_id'] == plan['trials'][0]['native_trial_id']
    assert report['account']['ending_equity'] == '1000.1299'
    assert report['account']['residual'] == '0'
    assert report['research_status'] == dict(oos='UNKNOWN', statistical_review='NOT_REVIEWED',
        risk_replay='REJECTED', independent_review='INDEPENDENT_REVIEW_REQUIRED', candidate_admission='NOT_APPROVED')
    assert report['submission_enabled'] is report['economic_authority'] is report['promotion_approved'] is False
    before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    restored = registry.get(submit(queue, plan, recover=True))
    assert restored.status == 'done', restored.error
    assert restored.metrics == run.metrics
    # The initial producer and its preregistered risk reproduction each run
    # once. Recovery adds neither a producer call nor a risk reproduction.
    assert calls == [1, 1]
    assert before == {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    assert queue.submit('registered_research', {'native_invocation_id': plan['invocation_id']},
        1, invocation_id='first') == uid


def test_published_native_result_survives_job_failure_without_new_computation(tmp_path, monkeypatch, technical_arguments):
    from tools import registered_technical_research_run as native
    job, plan, directory, _ = prepared(tmp_path, monkeypatch, technical_arguments)
    registry, queue = queued(tmp_path)
    original = native.common._put

    def interrupt(path, value, **kwargs):
        if path.name == 'outcome.json':
            raise RuntimeError('SYNTHETIC_RESULT_PUBLICATION_INTERRUPTION')
        return original(path, value, **kwargs)

    monkeypatch.setattr(native.common, '_put', interrupt)
    failed = submit(queue, plan)
    assert registry.get(failed).status == 'error'
    assert (directory / 'archive/report.json').is_file()
    monkeypatch.setattr(native.common, '_put', original)
    monkeypatch.setattr(native.native, 'run_authoritative_linear_perpetual_v2',
        lambda **_: pytest.fail('retained producer result must not be recomputed'))
    recovered = submit(queue, plan, recover=True)
    assert registry.get(recovered).status == 'done', registry.get(recovered).error
    assert registry.get(failed).status == 'error'


def test_durable_invocation_without_result_stays_unknown(tmp_path, monkeypatch, technical_arguments):
    from tools import registered_technical_research_run as native
    job, plan, directory, _ = prepared(tmp_path, monkeypatch, technical_arguments)
    registry, queue = queued(tmp_path)
    original = native.common._put

    def interrupt(path, value, **kwargs):
        result = original(path, value, **kwargs)
        if path.name == 'invocation.json':
            raise RuntimeError('SYNTHETIC_INVOCATION_INTERRUPTION')
        return result

    monkeypatch.setattr(native.common, '_put', interrupt)
    assert registry.get(submit(queue, plan)).status == 'error'
    assert (directory / 'invocation.json').is_file()
    monkeypatch.setattr(native.common, '_put', original)
    monkeypatch.setattr(native.native, 'run_authoritative_linear_perpetual_v2',
        lambda **_: pytest.fail('unknown invocation cannot safely be recomputed'))
    recovered = registry.get(submit(queue, plan, recover=True))
    assert recovered.status == 'error'
    assert 'UNKNOWN' in recovered.error
    assert not (directory / 'archive/report.json').exists()


@pytest.mark.parametrize('change', ['input', 'source', 'input_link', 'database_link', 'unevaluated'])
def test_changed_or_unsafe_originals_reject_before_population_mutation(tmp_path, monkeypatch, technical_arguments, change):
    from tools import registered_technical_research_run as native
    from quantos.contracts import registered_research_run as contract
    job, plan, directory, database = prepared(tmp_path, monkeypatch, technical_arguments)
    before = database.read_bytes()
    if change == 'input':
        value = json.loads((directory / 'input.json').read_bytes())
        value['manifest_inputs']['release_identity'] = ['value', 'changed']
        (directory / 'input.json').write_bytes(contract.canonical(value))
    elif change == 'source':
        monkeypatch.setattr(native, 'source_identity', lambda: {**technical_arguments['release_identity'], 'commit': 'e' * 40})
    elif change in ('input_link', 'database_link'):
        path = directory / 'input.json' if change == 'input_link' else database
        target = tmp_path / 'outside-original'
        path.rename(target)
        path.symlink_to(target)
    else:
        value = json.loads((directory / 'plan.json').read_bytes())
        value.pop('evaluation_core')
        value.pop('risk_review_policy')
        value['schema'] = contract.SCHEMA
        (directory / 'plan.json').write_bytes(contract.canonical(value))
    monkeypatch.setattr(native.native, 'run_authoritative_linear_perpetual_v2',
        lambda **_: pytest.fail('invalid retained input cannot execute'))
    registry, queue = queued(tmp_path)
    assert registry.get(submit(queue, plan)).status == 'error'
    assert database.read_bytes() == before
    assert not (directory / 'registration.json').exists()


@pytest.mark.parametrize('params', [{}, {'native_invocation_id': '../other'},
    {'native_invocation_id': 'A' * 64}, {'native_invocation_id': True},
    {'native_invocation_id': 'a' * 63}, {'native_invocation_id': 'a' * 64, 'path': '/srv/quantos/state'},
    {'native_invocation_id': 'a' * 64, 'trust': 'self'}, {'native_invocation_id': 'a' * 64, 'clock_ms': 0}])
def test_invalid_identifiers_and_paths_reject_before_queue(params):
    from services.research_runner.run_kinds import validate_params
    with pytest.raises(ValueError):
        validate_params('registered_research', params)


def test_recovery_requires_an_existing_native_invocation(tmp_path, monkeypatch, technical_arguments):
    job, plan, directory, database = prepared(tmp_path, monkeypatch, technical_arguments)
    before = database.read_bytes()
    registry, queue = queued(tmp_path)
    run = registry.get(submit(queue, plan, recover=True))
    assert run.status == 'error'
    assert 'RECOVERY_NOT_STARTED' in run.error
    assert database.read_bytes() == before
    assert not (directory / 'registration.json').exists()


def test_authenticated_original_http_entry_dispatches_actual_native_queue(tmp_path, monkeypatch, technical_arguments):
    from fastapi.testclient import TestClient
    _, plan, _, _ = prepared(tmp_path, monkeypatch, technical_arguments)
    # Import only after the isolated state root is set. Any runner token and
    # service stores created here are disposable test material, never host keys.
    from services.research_runner import main
    registry, queue = queued(tmp_path)
    monkeypatch.setattr(main, '_queue', queue)
    client = TestClient(main.app)
    request = dict(kind='registered_research', params={'native_invocation_id': plan['invocation_id']})
    assert client.post('/run', json=request).status_code == 401
    headers = {'X-Operator': 'ISOLATED_RESEARCH_TEST', 'X-Runner-Token': main._RUNNER_TOKEN,
        'Idempotency-Key': 'http-native'}
    assert client.post('/run', json={**request, 'params': {'native_invocation_id': '../outside'}},
        headers=headers).status_code == 400
    response = client.post('/run', json=request, headers=headers)
    assert response.status_code == 200, response.text
    queue.join()
    uid = response.json()['run_id']
    assert registry.get(uid).status == 'done', registry.get(uid).error
    assert client.post('/run', json=request, headers=headers).json()['run_id'] == uid
