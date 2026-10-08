"""Complete native population and original bytes, never a selected-trial export."""
import copy
import json
import time

import pytest

from quantos.contracts.research_evidence_json import digest
from tests.tools.test_registered_research_run import registered_case as registered_case, execute
from tests.tools.test_registered_gold_chunked_result import storage


def reader(f):
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    return ProjectResearchEvidenceReader(f['root']/'project', registry_database=f['root']/'registry.db')


def original_bytes(f):
    return {str(p.relative_to(f['root'])):p.read_bytes() for p in f['root'].rglob('*') if p.is_file()}


def test_complete_cut_keeps_old_failed_and_unstarted_attempts_with_all_chunk_files(registered_case, monkeypatch):
    from services.validation.project_research_population import validate_project_cut
    f = registered_case
    first = f['prepare']('one', result_storage=storage())
    execute(f)
    failed = f['prepare']('failed')
    actual = f['run'].gold._execute
    def fail(_):
        raise RuntimeError('original failed producer')
    monkeypatch.setattr(f['run'].gold, '_execute', fail)
    with pytest.raises(RuntimeError):
        execute(f, 'failed')
    monkeypatch.setattr(f['run'].gold, '_execute', actual)
    unstarted = f['prepare']('unstarted')
    before = original_bytes(f)
    cut = reader(f)()
    assert validate_project_cut(cut) == cut
    assert cut['schema'] == 'QUANTOS_PROJECT_RESEARCH_CUT_V2'
    assert set(cut['runs']) == {p['invocation_id'] for p in (first, failed, unstarted)}
    assert cut['attempt_count'] == 9 and cut['registered_attempt_count'] == 6
    assert sorted(a['status'] for a in cut['attempts']) == ['FAILED']*3+['NOT_REGISTERED']*3+['PASSED']*3
    material = cut['runs'][first['invocation_id']]['result_material']
    directory = f['directories']['one']/'archive/report-parts'
    assert set(material['files']) == {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    for name, binding in material['files'].items():
        raw = (directory/name).read_bytes()
        assert binding == {'sha256':digest(raw), 'bytes':len(raw)}
    assert not cut['all_runs_finalized']
    assert cut['submission_enabled'] is cut['economic_authority'] is False
    assert original_bytes(f) == before


def test_full_original_report_is_available_only_inside_pinned_context(registered_case):
    from quantos.streaming_identity import json_parts
    f = registered_case
    plan = f['prepare'](result_storage=storage())
    execute(f)
    before = original_bytes(f)
    with reader(f).evidence() as (cut, reports):
        report = reports[plan['invocation_id']]
        raw = b''.join(json_parts(report))
        reference = json.loads((f['directories']['one']/'archive/report.json').read_bytes())
        assert digest(raw) == reference['sha256']
        assert len(report['trials']) == len(plan['trials'])
        assert cut['population_coverage'] == 'COMPLETE'
    with pytest.raises(ValueError, match='CLOSED'):
        report['trials']
    assert original_bytes(f) == before


def test_same_byte_rewrite_during_consumer_is_rejected(registered_case):
    f = registered_case
    f['prepare'](result_storage=storage())
    execute(f)
    with pytest.raises(ValueError, match='CHANGED'):
        with reader(f).evidence():
            blob = next((f['directories']['one']/'archive/report-parts/blobs').iterdir())
            blob.write_bytes(blob.read_bytes())


@pytest.mark.parametrize('damage', ['missing', 'extra', 'symlink', 'alias', 'bytes'])
def test_damaged_original_report_never_disappears_from_population(registered_case, damage):
    f = registered_case
    f['prepare'](result_storage=storage())
    execute(f)
    blobs = f['directories']['one']/'archive/report-parts/blobs'
    blob = next(blobs.iterdir())
    if damage == 'missing':
        blob.unlink()
    elif damage == 'extra':
        path = blobs/('a'*64)
        path.write_bytes(b'extra')
        path.chmod(0o600)
    elif damage == 'symlink':
        target = f['root']/'original-blob'
        blob.rename(target)
        blob.symlink_to(target)
    elif damage == 'alias':
        import os
        os.link(blob, f['root']/'aliased-blob')
    else:
        blob.write_bytes(b'x'+blob.read_bytes()[1:])
    with pytest.raises(ValueError):
        reader(f)()


@pytest.mark.parametrize('damage', ['omit_blob', 'replace_hash', 'claim_authority', 'old_cut'])
def test_retained_metadata_cannot_rebind_or_drop_original_parts(registered_case, damage):
    from services.validation.project_research_population import validate_project_cut
    f = registered_case
    plan = f['prepare'](result_storage=storage())
    execute(f)
    cut = copy.deepcopy(reader(f)())
    material = cut['runs'][plan['invocation_id']]['result_material']
    blob = next(n for n in material['files'] if n.startswith('blobs/'))
    if damage == 'omit_blob':
        del material['files'][blob]
    elif damage == 'replace_hash':
        material['files'][blob]['sha256'] = '0'*64
    elif damage == 'claim_authority':
        cut['economic_authority'] = True
    else:
        cut['schema'] = 'QUANTOS_PROJECT_RESEARCH_CUT_V1'
    cut['cut_sha256'] = digest({k:v for k,v in cut.items() if k not in ('cut_sha256','read_identity_sha256')})
    with pytest.raises(ValueError):
        validate_project_cut(cut)


@pytest.mark.parametrize('portfolio', [False, True])
def test_chunked_statistics_recompute_original_cash_grid_and_funding_receipt_time(
        registered_case, monkeypatch, portfolio):
    from tests.research.test_gold_segmented_registration import request
    from tools.gold_bar_research import read_arguments
    from services.validation.gold_native_statistics import project_gold_statistics
    f = registered_case
    root = request(f['root'], monkeypatch, {'release_identity':f['arguments']['release_identity']},
        portfolio=portfolio, declared=True, funding=True)
    f['arguments'] = read_arguments(root, original_source=f['arguments']['release_identity'])
    f['clock'][0] = time.time_ns()//1_000_000
    design = json.loads((root/'plan.json').read_bytes())['forward_design']
    def prepare(name, **extra):
        plan = f['run'].prepare_gold(f['arguments'], project_root=f['root']/'project',
            search_id=name, producer_id='isolated-native', actor_id='AUTOMATED_TEST',
            dataset_version='native-xau', registered_at_ms=f['clock'][0], forward_design=design,
            invocation_id=('e' if name == 'old' else '1')*64, **extra)
        f['directories'][name] = f['run'].run_directory(f['root']/'project', plan)
        return plan
    old = prepare('old')
    execute(f, 'old')
    old_cut = reader(f)()
    expected = project_gold_statistics(project_cut=old_cut, invocation_id=old['invocation_id'])
    new = prepare('one', result_storage=storage())
    execute(f)
    with reader(f).evidence() as (cut, reports):
        actual = project_gold_statistics(project_cut=cut, invocation_id=new['invocation_id'], reports=reports)
        for left, right in zip(expected['attempts'], actual['attempts'], strict=True):
            assert {k:v for k,v in left.items() if k not in ('native_trial_id','config_hash')} == {
                k:v for k,v in right.items() if k not in ('native_trial_id','config_hash')}
        assert actual['benchmark'] == expected['benchmark']
        assert actual['population']['attempt_count'] == (8 if portfolio else 6)
        assert actual['attempts'][0]['metrics']['funding'] == '-0.3'
        assert actual['economic_authority'] is actual['promotion_approved'] is actual['submission_enabled'] is False
        with pytest.raises(ValueError, match='MATERIAL_REQUIRED'):
            project_gold_statistics(project_cut=cut, invocation_id=new['invocation_id'])
        from quantos.streaming_identity import json_parts
        report = json.loads(b''.join(json_parts(reports[new['invocation_id']])))
        report['trials'][0]['simulation']['reconciliation']['funding'] = '999'
        with pytest.raises(ValueError, match='REPORT_BYTES'):
            project_gold_statistics(project_cut=cut, invocation_id=new['invocation_id'],
                reports={new['invocation_id']:report})


def test_partial_publication_keeps_all_original_bytes_and_running_trials(registered_case, monkeypatch):
    from services.validation.project_research_population import validate_project_cut
    f = registered_case
    plan = f['prepare'](result_storage=storage())
    actual = f['run']._put
    def stop(path, value=None, **kwargs):
        if path.name == 'report.json':
            raise RuntimeError('original reference publication interrupted')
        return actual(path, value, **kwargs)
    monkeypatch.setattr(f['run'], '_put', stop)
    with pytest.raises(RuntimeError):
        execute(f)
    before = original_bytes(f)
    cut = reader(f)()
    assert validate_project_cut(cut) == cut
    run = cut['runs'][plan['invocation_id']]
    assert run['result_material']['status'] == 'INCOMPLETE'
    assert 'manifest.json' in run['result_material']['files']
    assert {a['status'] for a in cut['attempts']} == {'RUNNING'}
    assert 'OUTCOME_UNKNOWN' in run['partial']
    assert not cut['all_runs_finalized']
    assert original_bytes(f) == before


def test_large_report_stays_in_original_chunks_not_embedded_in_cut(registered_case, monkeypatch):
    from quantos.contracts.backtest_identity import exact_identity_hash
    from quantos.contracts.research_evidence_json import canonical
    f = registered_case
    actual = f['run'].gold._execute
    def produce(arguments):
        report = actual(arguments)
        report['synthetic_transport_fixture'] = ['transport '*1024]*1800
        report['result_hash'] = exact_identity_hash({k:v for k,v in report.items() if k != 'result_hash'})
        return report
    monkeypatch.setattr(f['run'].gold, '_execute', produce)
    limits = storage()
    limits.update(maximum_bytes=24_000_000, maximum_index_bytes=128_000_000, chunk_bytes=16_000_000)
    plan = f['prepare'](result_storage=limits)
    execute(f)
    before = original_bytes(f)
    with reader(f).evidence() as (cut, reports):
        run = cut['runs'][plan['invocation_id']]
        assert run['result_material']['bytes'] > 16_000_000
        assert len(canonical(cut)) < 1_000_000
        assert len(reports[plan['invocation_id']]['synthetic_transport_fixture']) == 1800
    assert original_bytes(f) == before


def test_signed_consumer_requires_complete_original_material_even_with_valid_test_signatures(registered_case):
    from tests.validation.test_project_research_evidence_authority import signed_project
    from services.validation.project_research_evidence_authority import verify_project_evidence
    f = registered_case
    plan = f['prepare'](result_storage=storage())
    execute(f)
    with reader(f).evidence() as (cut, reports):
        snapshot, _ = signed_project(cut, plan['invocation_id'], now=f['clock'][0], reports=reports)
        result = verify_project_evidence(snapshot, now_ms=f['clock'][0], reports=reports)
        assert result['authentication'] == 'VERIFIED_SOURCE_AND_RESULT'
        assert result['qualification'] == 'INSUFFICIENT'
        assert result['promotion_approved'] is result['submission_enabled'] is False
        with pytest.raises(ValueError, match='MATERIAL_REQUIRED'):
            verify_project_evidence(snapshot, now_ms=f['clock'][0])


def test_statistics_material_population_cannot_select_only_one_complete_run(registered_case):
    from quantos.contracts.project_chunked_results import full_report_population
    f = registered_case
    plans = [f['prepare'](name, result_storage=storage()) for name in ('one', 'two')]
    for name in ('one', 'two'):
        execute(f, name)
    with reader(f).evidence() as (cut, reports):
        assert cut['attempt_count'] == 6 and cut['all_runs_finalized']
        full_report_population(cut, reports)
        invocation = plans[0]['invocation_id']
        with pytest.raises(ValueError, match='MATERIAL_REQUIRED'):
            full_report_population(cut, {invocation:reports[invocation]})


def test_report_budget_failure_retains_failure_original_and_unknown_invocation(registered_case):
    from services.validation.project_research_population import validate_project_cut
    f = registered_case
    limits = storage()
    limits['maximum_bytes'] = 512
    plan = f['prepare'](result_storage=limits)
    with pytest.raises(ValueError, match='STREAM_BYTES'):
        execute(f)
    before = original_bytes(f)
    cut = reader(f)()
    assert validate_project_cut(cut) == cut
    material = cut['runs'][plan['invocation_id']]['result_material']
    assert set(material['files']) == {'failure.json'}
    assert {a['status'] for a in cut['attempts']} == {'RUNNING'}
    assert not cut['all_runs_finalized']
    assert original_bytes(f) == before
