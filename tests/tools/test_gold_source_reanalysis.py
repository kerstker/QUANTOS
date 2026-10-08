"""New-code replay retains old collector originals and never inherits forward authority."""
import copy
import hashlib
import json
import time

import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case
from tests.tools.test_registered_gold_source import short_source as short_source


def physical(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_ino,
        p.stat().st_mtime_ns, p.stat().st_mode) for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def older_source(short_source, monkeypatch):
    from tools import gold_chunked_source as carrier
    f, root, options = short_source
    collector = copy.deepcopy(f['arguments']['release_identity'])
    consumer = dict(collector, commit='e'*40, python_source_sha256='3'*64)
    monkeypatch.setattr(carrier, 'source_identity', lambda: consumer)
    monkeypatch.setattr(f['run'].gold, 'source_identity', lambda: consumer)
    return f, root, options, collector, consumer


def test_original_forward_route_still_rejects_another_code_version(older_source):
    from tools import registered_gold_chunked_job as job
    f, root, options, _, _ = older_source
    before = physical(root)
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        job.prepare_source_run(root, **options)
    assert physical(root) == before
    assert not (f['root']/'project').exists()


@pytest.mark.parametrize('budgeted', [False, True])
def test_old_originals_reach_fresh_registered_queue_accounts_and_cold_recovery(
        older_source, monkeypatch, budgeted):
    from tools import registered_gold_chunked_job as job
    from tests.research.test_registered_research_job import queued
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from services.research.trial_ledger import TrialLedger
    from quantos.contracts import registered_research_run as shape

    f, root, options, collector, consumer = older_source
    before = physical(root)
    if budgeted:
        options['source_storage'].update(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V2',
            replay_budget=dict(schema='LINEAR_PERPETUAL_REPLAY_BUDGET_V1',
                maximum_environment_segments=2, maximum_elapsed_ms=30*86_400_000))
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(f['root']))
    plan = job.prepare_source_reanalysis(root, collection_source_identity=collector, **options)
    assert plan['source_identity'] == consumer
    assert plan['search']['search_kind'] == 'BACKTEST'
    assert plan['search']['source'] == 'REGISTERED_GOLD_REANALYSIS_RUNNER_V1'
    assert 'forward_design' not in plan and 'evaluation_core' not in plan
    assert plan['economic_authority'] is plan['submission_enabled'] is False
    changed = copy.deepcopy(plan)
    changed['search']['search_kind'] = 'FORWARD'
    with pytest.raises(ValueError):
        shape.validate_plan(changed)
    directory = f['root']/'project/runs'/plan['invocation_id']
    document = json.loads((directory/'input.json').read_bytes())
    binding = document['source_binding']
    assert binding['collection_source_identity'] == collector
    assert document['release_identity'] == consumer
    assert binding['manifest_sha256'] == hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest()
    assert {k:v[0] for k,v in physical(directory/'input-parts').items()} == {k:v[0] for k,v in before.items()}
    calls = []
    execute = f['run'].gold._execute
    def observed(*args, **kwargs):
        with TrialLedger(str(directory/'archive/trials.db'), create=False, read_only=True) as ledger:
            assert all(ledger.load(t['native_trial_id']).status == 'RUNNING' for t in plan['trials'])
        calls.append(1)
        return execute(*args, **kwargs)
    monkeypatch.setattr(f['run'].gold, '_execute', observed)
    registry, queue = queued(f['root'])
    params = dict(native_invocation_id=plan['invocation_id'])
    uid = queue.submit('registered_gold_chunked', params, time.time_ns()//1_000_000,
        invocation_id='new-code-source')
    queue.join()
    result = registry.get(uid)
    assert result.status == 'done', result.error
    mirror = json.loads(result.metrics['registered_gold_chunked_report_json'])
    assert len(mirror['accounts']) == 3 and all(a['residual'] == '0' for a in mirror['accounts'].values())
    assert mirror['economic_authority'] is mirror['submission_enabled'] is mirror['promotion_approved'] is False
    with f['run']._chunked_gold_report(directory, plan) as report:
        assert dict(report['release_identity']) == consumer
        assert dict(report['dataset_identity']['source_binding']['collection_source_identity']) == collector
        assert report['dataset_identity']['input_segment_count'] == 2
    ledger_before = (directory/'archive/trials.db').read_bytes()
    monkeypatch.setattr(f['run'].gold, '_execute', lambda *a, **kw: pytest.fail('cold replay must not execute'))
    uid = queue.submit('registered_gold_chunked_recover', params, time.time_ns()//1_000_000,
        invocation_id='new-code-source-recovery')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    assert registry.get(uid).metrics == result.metrics
    assert calls == [1] and (directory/'archive/trials.db').read_bytes() == ledger_before
    with ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db').evidence() as (cut, reports):
        assert cut['attempt_count'] == 3 and {a['status'] for a in cut['attempts']} == {'PASSED'}
        assert cut['runs'][plan['invocation_id']]['input_material']['status'] == 'COMPLETE'
        assert len(reports.source_inputs[plan['invocation_id']]) == 2
    assert physical(root) == before
    # A nonselected original is mandatory even after a successful execution.
    manifest = json.loads((directory/'input-parts/manifest.json').read_bytes())
    (directory/'input-parts/blobs'/manifest['parts'][-1]['sha256']).unlink()
    with pytest.raises(ValueError):
        job.recover_research(params)
    assert (directory/'archive/trials.db').read_bytes() == ledger_before


@pytest.mark.parametrize('invalid', ['collector', 'forward', 'budget'])
def test_reanalysis_rejects_bad_pin_forward_relabel_and_insufficient_budget_before_plan(
        older_source, invalid):
    from tools import registered_gold_chunked_job as job
    f, root, options, collector, _ = older_source
    before = physical(root)
    if invalid == 'collector':
        collector = dict(collector, python_source_sha256='9'*64)
    elif invalid == 'forward':
        options['forward_design'] = {'schema':'UNACCEPTABLE_BACKDATED_DECLARATION'}
    else:
        options['source_storage'].update(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V2',
            replay_budget=dict(schema='LINEAR_PERPETUAL_REPLAY_BUDGET_V1',
                maximum_environment_segments=1, maximum_elapsed_ms=30*86_400_000))
    with pytest.raises(ValueError):
        job.prepare_source_reanalysis(root, collection_source_identity=collector, **options)
    assert not (f['root']/'project').exists() and physical(root) == before


def test_reanalysis_physical_cut_rejects_source_change_and_same_byte_rewrite(older_source, monkeypatch):
    from tools import gold_chunked_source as carrier
    f, root, _, collector, consumer = older_source
    with pytest.raises(ValueError, match='SOURCE_CHANGED'):
        with carrier.NativeSource(root, collection_source_identity=collector):
            monkeypatch.setattr(carrier, 'source_identity', lambda: dict(consumer, commit='b'*40))
    monkeypatch.setattr(carrier, 'source_identity', lambda: consumer)
    with pytest.raises(ValueError, match='FILE_CHANGED'):
        with carrier.NativeSource(root, collection_source_identity=collector):
            blob = next((root/'blobs').iterdir())
            blob.write_bytes(blob.read_bytes())


def test_reanalysis_queue_cannot_take_a_caller_source_pin(older_source):
    from tools import registered_gold_chunked_job as job
    _, _, options, collector, _ = older_source
    with pytest.raises(ValueError):
        job.run_research(dict(native_invocation_id=options['invocation_id'],
            collection_source_identity=collector))


def test_actual_original_funding_and_cashflow_keep_the_collector_pin(registered_case, tmp_path, monkeypatch):
    from decimal import Decimal
    from tools import gold_chunked_source as carrier, registered_gold_chunked_job as job
    from tools import registered_research_job as workspace
    from tests.tools.test_gold_public_segment_producer import native_session
    from tests.tools.test_registered_gold_chunked_result import storage
    from quantos.contracts import gold_native_source as source_shape, registered_research_run as plan_shape
    f = registered_case
    collector = copy.deepcopy(f['arguments']['release_identity'])
    bars, _, summary = native_session(tmp_path, monkeypatch, collector, funding_after_ms=500_000)
    monkeypatch.setattr(carrier, 'source_identity', lambda: collector)
    root = tmp_path/'old-funded-originals'
    carrier.from_session(tmp_path/'session', bars, root, segment_count=summary['segment_count'],
        release_identity=collector, quantity=Decimal('0.1'), initial_collateral=Decimal('1000'),
        fee_rate=Decimal('0.001'), settlement_window_ms=1000, maximum_bytes=32_000_000)
    before = physical(root)
    consumer = dict(collector, commit='e'*40, python_source_sha256='3'*64)
    monkeypatch.setattr(carrier, 'source_identity', lambda: consumer)
    monkeypatch.setattr(f['run'].gold, 'source_identity', lambda: consumer)
    monkeypatch.setattr(workspace, 'workspace_paths', lambda: (f['root']/'project', f['root']/'registry.db'))
    for path in f['root'].glob('registry.db*'):
        path.chmod(0o600)
    plan = job.prepare_source_reanalysis(root, collection_source_identity=collector,
        search_id='funded-new-code', dataset_version='synthetic', registered_at_ms=time.time_ns()//1_000_000,
        invocation_id='d'*64, result_storage=storage(),
        history_storage=dict(schema='QUANTOS_GOLD_HISTORY_STORAGE_V1', maximum_bytes=256_000_000),
        source_storage=dict(schema='QUANTOS_GOLD_NATIVE_SOURCE_STORAGE_V2', maximum_bytes=32_000_000,
            maximum_preparation_bytes=64_000_000, replay_budget=dict(schema='LINEAR_PERPETUAL_REPLAY_BUDGET_V1',
                maximum_environment_segments=summary['segment_count'], maximum_elapsed_ms=30*86_400_000)))
    directory = f['root']/'project/runs'/plan['invocation_id']
    document = json.loads((directory/'input.json').read_bytes())
    assert document['settlement_window_ms'] == 1000
    wrong = copy.deepcopy(plan)
    wrong['schema'] = plan_shape.BUDGETED_SOURCE_GOLD_SCHEMA
    wrong['search']['search_kind'] = 'FORWARD'
    plan_shape.validate_plan(wrong)
    with pytest.raises(ValueError, match='REANALYSIS_PLAN_VERSION'):
        source_shape.bind_plan(wrong, document)
    job.run_research(dict(native_invocation_id=plan['invocation_id']))
    with f['run']._chunked_gold_report(directory, plan) as report:
        cash = report['trials'][0]['simulation']['ending_state']['cashflow_identity']
        assert cash['funding'] == '-0.01' and cash['ending_equity'] == '999.95' and cash['residual'] == '0'
        assert dict(report['release_identity']) == consumer
        assert dict(report['dataset_identity']['source_binding']['collection_source_identity']) == collector
    assert physical(root) == before
