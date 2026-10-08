"""Frozen long budget travels through the original native transaction and readers."""
import json

import pytest

from tests.tools.test_registered_gold_source import short_source as short_source
from tests.tools.test_registered_research_run import registered_case as registered_case


def upgrade(options, *, epochs=2):
    options['source_storage'].update(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V2',
        replay_budget=dict(schema='LINEAR_PERPETUAL_REPLAY_BUDGET_V1',
            maximum_environment_segments=epochs, maximum_elapsed_ms=30 * 86_400_000))
    return options


def test_original_native_plan_execution_cold_recovery_and_project_reader_bind_budget(short_source, monkeypatch):
    from tools import registered_gold_chunked_job as job
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from quantos.contracts.project_chunked_results import report_for
    f, root, options = short_source
    plan = job.prepare_source_run(root, **upgrade(options))
    assert plan['schema'] == 'QUANTOS_REGISTERED_GOLD_CHUNKED_RUN_V4'
    directory = f['root']/'project/runs'/plan['invocation_id']
    document = json.loads((directory/'input.json').read_bytes())
    assert document['input_schema'] == 'QUANTOS_GOLD_NATIVE_SOURCE_INPUT_V2'
    assert document['replay_budget'] == plan['source_storage']['replay_budget']
    params = dict(native_invocation_id=plan['invocation_id'])
    result = job.run_research(params)
    assert result['plan'] == plan
    assert result['economic_authority'] is result['promotion_approved'] is result['submission_enabled'] is False
    reader = ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')
    with reader.evidence() as (cut, reports):
        report = report_for(cut['runs'][plan['invocation_id']], reports)
        assert report['dataset_identity']['schema'] == 'GOLD_CHUNKED_BAR_QUOTE_BINDING_V3'
        for engine in [report['cash_baseline'], *(t['simulation'] for t in report['trials'])]:
            assert dict(engine['replay_budget']) == document['replay_budget']
            assert engine['schema_version'] == 'LINEAR_PERPETUAL_FORWARD_SIMULATION_V4'
    before = {p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    monkeypatch.setattr(f['run'].gold, '_execute', lambda *a, **kw: pytest.fail('cold recovery cannot execute'))
    assert job.recover_research(params) == result
    assert {p.relative_to(directory).as_posix():p.read_bytes() for p in directory.rglob('*') if p.is_file()} == before


def test_insufficient_environment_budget_rejected_before_plan_or_trial_creation(short_source):
    from tools import registered_gold_chunked_job as job
    f, root, options = short_source
    with pytest.raises(ValueError, match='REPLAY.*BUDGET'):
        job.prepare_source_run(root, **upgrade(options, epochs=1))
    assert not (f['root']/'project').exists()


def test_old_plan_cannot_be_relabelled_with_new_storage_budget(short_source):
    from tools import registered_gold_chunked_job as job
    from quantos.contracts.registered_research_run import validate_plan
    _, root, options = short_source
    plan = job.prepare_source_run(root, **options)
    plan['source_storage'] = upgrade(options)['source_storage']
    with pytest.raises(ValueError, match='BUDGET|VERSION'):
        validate_plan(plan)


@pytest.mark.parametrize('new_code', [False, True], ids=['same-code', 'new-code'])
def test_original_queue_executes_all_1001_parts_beyond_legacy_budget(registered_case, tmp_path, monkeypatch, new_code):
    from tests.execution.test_perpetual_public_replay import START, sealed
    from tests.tools.test_gold_stream_input import long_parts
    from tests.tools.test_registered_gold_chunked_result import storage
    from tests.research.test_registered_research_job import queued
    from tools import gold_chunked_source as source, registered_gold_chunked_job as job
    from tools import registered_research_job as workspace
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from quantos.contracts.project_chunked_results import report_for
    import time
    f = registered_case
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(f['root']))
    monkeypatch.setattr(source, 'source_identity', lambda:f['arguments']['release_identity'])
    def parts():
        first_journal = None
        for index, part in enumerate(long_parts(tmp_path, monkeypatch, count=1001)):
            if index == 0:
                part['release_identity'] = f['arguments']['release_identity']
                first_journal = part['journal_files']
            # Explicitly synthetic 15-second segments, within one received
            # funding forecast. Never fabricate a historical settlement.
            if index < 60:
                part['journal_files'] = first_journal
            capture = part['capture']
            records = [*capture['bootstrap'].values(), capture['exchange_after'],
                       *(r for sample in capture['samples'] for r in sample.values())]
            for record in records:
                for key in ('requested_at', 'received_at'):
                    record[key] = START + (record[key]-START)//2
            for sample in capture['samples']:
                sample['mark']['payload']['time'] = START + (sample['mark']['payload']['time']-START)//2
                for key in ('T', 'E'):
                    sample['depth']['payload'][key] = START + (sample['depth']['payload'][key]-START)//2
            part['capture'] = sealed(capture)
            yield part
    original = tmp_path/'complete-originals'
    source.freeze(parts(), original, maximum_bytes=64_000_000)
    result_storage = storage()
    result_storage.update(maximum_bytes=256_000_000, maximum_index_bytes=512_000_000, chunk_bytes=65_536)
    options = dict(search_id='original-1001-epoch-budget', dataset_version='synthetic-1001',
        registered_at_ms=time.time_ns()//1_000_000, invocation_id='b'*64, result_storage=result_storage,
        history_storage=dict(schema='QUANTOS_GOLD_HISTORY_STORAGE_V1', maximum_bytes=512_000_000),
        source_storage=dict(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V1', maximum_bytes=64_000_000,
                            maximum_preparation_bytes=512_000_000))
    collector = dict(f['arguments']['release_identity'])
    if new_code:
        consumer = dict(collector, commit='e'*40, python_source_sha256='3'*64)
        monkeypatch.setattr(source, 'source_identity', lambda: consumer)
        monkeypatch.setattr(f['run'].gold, 'source_identity', lambda: consumer)
        plan = job.prepare_source_reanalysis(original, collection_source_identity=collector,
            **upgrade(options, epochs=1001))
        assert plan['search']['search_kind'] == 'BACKTEST' and 'forward_design' not in plan
    else:
        plan = job.prepare_source_run(original, **upgrade(options, epochs=1001))
    params = dict(native_invocation_id=plan['invocation_id'])
    registry, queue = queued(f['root'])
    uid = queue.submit('registered_gold_chunked', params, time.time_ns()//1_000_000, invocation_id='execute-all-1001')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    project, database = workspace.workspace_paths()
    with ProjectResearchEvidenceReader(project, registry_database=database).evidence() as (cut, reports):
        report = report_for(cut['runs'][plan['invocation_id']], reports)
        if new_code:
            assert dict(report['release_identity']) == consumer
            assert dict(report['dataset_identity']['source_binding']['collection_source_identity']) == collector
        assert report['dataset_identity']['input_segment_count'] == 1001
        for engine in [report['cash_baseline'], *(t['simulation'] for t in report['trials'])]:
            assert len(engine['environment_segments']) == 1001
            assert engine['reconciliation']['initial_equity'] == '1000'
            assert engine['reconciliation']['residual'] == '0'
    uid = queue.submit('registered_gold_chunked_recover', params, time.time_ns()//1_000_000,
                       invocation_id='recover-all-1001')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
