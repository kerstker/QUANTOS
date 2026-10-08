"""Complete retained inputs reach original registration, queue and cold recovery."""
import json
import time

import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case


@pytest.fixture
def short_source(registered_case, tmp_path, monkeypatch):
    from tests.tools.test_gold_stream_input import long_parts
    from tests.tools.test_registered_gold_chunked_result import storage
    from tools import gold_chunked_source as source, registered_research_job as workspace
    f = registered_case
    for path in f['root'].glob('registry.db*'):
        path.chmod(0o600)
    monkeypatch.setattr(source, 'source_identity', lambda: f['arguments']['release_identity'])
    monkeypatch.setattr(workspace, 'workspace_paths', lambda: (f['root']/'project', f['root']/'registry.db'))
    def parts():
        for index, part in enumerate(long_parts(tmp_path, monkeypatch, count=2)):
            if index == 0:
                part['release_identity'] = f['arguments']['release_identity']
            yield part
    root = tmp_path/'original-inputs'
    source.freeze(parts(), root, maximum_bytes=32_000_000)
    options = dict(search_id='original-source', dataset_version='synthetic',
        registered_at_ms=time.time_ns()//1_000_000, invocation_id='c'*64, result_storage=storage(),
        history_storage=dict(schema='QUANTOS_GOLD_HISTORY_STORAGE_V1', maximum_bytes=256_000_000),
        source_storage=dict(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V1', maximum_bytes=32_000_000,
                            maximum_preparation_bytes=64_000_000))
    return f, root, options


def test_interrupted_input_copy_is_unstarted_and_retry_preserves_every_original(short_source, monkeypatch):
    from tools import registered_gold_chunked_job as job
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    f, root, options = short_source
    put = f['run']._put
    copied = []
    def interrupt(path, *args, **kwargs):
        if path.parent.name == 'blobs':
            copied.append(path)
            if len(copied) == 2:
                raise RuntimeError('input copy interrupted')
        return put(path, *args, **kwargs)
    monkeypatch.setattr(f['run'], '_put', interrupt)
    with pytest.raises(RuntimeError, match='copy interrupted'):
        job.prepare_source_run(root, **options)
    directory = f['root']/'project/runs'/options['invocation_id']
    assert not (directory/'input-parts/manifest.json').exists()
    assert not (directory/'invocation.json').exists()
    first_bytes = copied[0].read_bytes()
    cut = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')()
    assert cut['runs'][options['invocation_id']]['input_material']['status'] == 'INCOMPLETE'
    assert cut['attempt_count'] == 3
    assert {row['status'] for row in cut['attempts']} == {'NOT_REGISTERED'}
    assert cut['runs'][options['invocation_id']]['ledger']['history'] == {}
    with pytest.raises(ValueError):
        job.run_research(dict(native_invocation_id=options['invocation_id']))
    assert not (directory/'archive/trials.db').exists()
    monkeypatch.setattr(f['run'], '_put', put)
    plan = job.prepare_source_run(root, **options)
    assert copied[0].read_bytes() == first_bytes
    originals = {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob('*') if p.is_file()}
    target = directory/'input-parts'
    assert {p.relative_to(target).as_posix():p.read_bytes() for p in target.rglob('*') if p.is_file()} == originals
    assert job.run_research(dict(native_invocation_id=plan['invocation_id']))['status'] == 'TECHNICAL_VALIDATION_ONLY'


def test_original_input_budget_fails_before_creating_a_plan(short_source):
    from tools import registered_gold_chunked_job as job
    from quantos.contracts.gold_chunked_source import RESERVED_BYTES
    f, root, options = short_source
    options['source_storage']['maximum_bytes'] = RESERVED_BYTES+1
    with pytest.raises(ValueError, match='SOURCE_BUDGET'):
        job.prepare_source_run(root, **options)
    assert not (f['root']/'project').exists()


@pytest.mark.parametrize('damage', ['missing', 'extra', 'same-bytes'])
def test_native_cold_recovery_requires_all_original_inputs(short_source, monkeypatch, damage):
    from tools import registered_gold_chunked_job as job
    f, root, options = short_source
    plan = job.prepare_source_run(root, **options)
    params = dict(native_invocation_id=plan['invocation_id'])
    result = job.run_research(params)
    monkeypatch.setattr(f['run'].gold, '_execute', lambda *a, **kw: pytest.fail('cold recovery must not execute'))
    target = f['root']/'project/runs'/plan['invocation_id']/'input-parts'
    if damage == 'extra':
        extra = target/'blobs'/('f'*64)
        extra.write_bytes(b'unknown')
        extra.chmod(0o600)
        with pytest.raises(ValueError):
            job.recover_research(params)
    elif damage == 'missing':
        next((target/'blobs').iterdir()).unlink()
        with pytest.raises(ValueError):
            job.recover_research(params)
    else:
        # A rewrite before a new read lease preserves the content identity.
        # Rewriting during a lease must fail even when bytes are unchanged.
        assert job.recover_research(params) == result
        from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
        with pytest.raises(ValueError, match='CHANGED'):
            with ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db').evidence():
                blob = next((target/'blobs').iterdir())
                blob.write_bytes(blob.read_bytes())


def test_original_queue_retains_and_cold_verifies_all_65_input_parts(registered_case, tmp_path, monkeypatch):
    from tools import gold_chunked_source as source, registered_gold_chunked_job as job
    from tools import registered_research_job as workspace
    from tests.tools.test_gold_stream_input import long_parts
    from tests.tools.test_registered_gold_chunked_result import storage
    from tests.research.test_registered_research_job import queued
    from services.research.trial_ledger import TrialLedger
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    f = registered_case
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(f['root']))
    monkeypatch.setattr(source, 'source_identity', lambda: f['arguments']['release_identity'])
    def parts():
        for index, part in enumerate(long_parts(tmp_path, monkeypatch)):
            if index == 0:
                part['release_identity'] = f['arguments']['release_identity']
            yield part
    original_inputs = tmp_path/'source-input'
    source.freeze(parts(), original_inputs, maximum_bytes=32_000_000)
    plan = job.prepare_source_run(original_inputs, search_id='long-gold-native', dataset_version='frozen-gold',
        registered_at_ms=time.time_ns()//1_000_000, invocation_id='e'*64, result_storage=storage(),
        history_storage=dict(schema='QUANTOS_GOLD_HISTORY_STORAGE_V1', maximum_bytes=256_000_000),
        source_storage=dict(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V1', maximum_bytes=32_000_000,
                            maximum_preparation_bytes=64_000_000))
    project, database = workspace.workspace_paths()
    directory = project/'runs'/plan['invocation_id']
    calls = []
    producer = f['run'].gold._execute
    def produce(arguments, **kwargs):
        with TrialLedger(str(directory/'archive/trials.db'), create=False, read_only=True) as ledger:
            assert all(ledger.load(t['native_trial_id']).status == 'RUNNING' for t in plan['trials'])
        calls.append(1)
        return producer(arguments, **kwargs)
    monkeypatch.setattr(f['run'].gold, '_execute', produce)
    params = dict(native_invocation_id=plan['invocation_id'])
    registry, queue = queued(f['root'])
    uid = queue.submit('registered_gold_chunked', params, time.time_ns()//1_000_000, invocation_id='first-source')
    queue.join()
    first = registry.get(uid)
    assert first.status == 'done', first.error
    result = json.loads(first.metrics['registered_gold_chunked_report_json'])
    assert len(calls) == 1 and result['status'] == 'TECHNICAL_VALIDATION_ONLY'
    assert len(result['accounts']) == 3
    before = (directory/'archive/trials.db').read_bytes()
    uid = queue.submit('registered_gold_chunked_recover', params, time.time_ns()//1_000_000, invocation_id='recover-source')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    assert registry.get(uid).metrics == first.metrics
    assert len(calls) == 1 and (directory/'archive/trials.db').read_bytes() == before
    cut = ProjectResearchEvidenceReader(str(project), registry_database=str(database))()
    run = cut['runs'][plan['invocation_id']]
    assert cut['schema'] == 'QUANTOS_PROJECT_RESEARCH_CUT_V3'
    assert run['input_material']['status'] == 'COMPLETE'
    assert run['input_material']['part_count'] == 65
    manifest = json.loads((directory/'input-parts/manifest.json').read_bytes())
    path = directory/'input-parts/blobs'/manifest['parts'][0]['sha256']
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError):
        job.recover_research(params)
    with pytest.raises(ValueError):
        ProjectResearchEvidenceReader(str(project), registry_database=str(database))()
    assert len(calls) == 1
