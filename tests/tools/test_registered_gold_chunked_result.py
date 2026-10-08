"""Original registered producer with explicit complete-report storage."""
import json
import time

import pytest

from tests.tools.test_registered_research_run import registered_case as registered_case, execute


def storage(**updates):
    return dict(schema='QUANTOS_GOLD_CHUNKED_REPORT_STORAGE_V1',
                maximum_bytes=16_000_000, maximum_index_bytes=256_000_000, chunk_bytes=4096, **updates)


def test_original_registered_gold_report_is_complete_and_cold_recovery_never_executes_again(
        registered_case, monkeypatch, tmp_path):
    from services.research.gold_chunked_result_reader import open_gold_result
    from quantos.streaming_identity import json_parts
    f = registered_case
    original = f['run'].gold._execute
    produced = []
    def actual(arguments):
        value = original(arguments)
        produced.append(value)
        return value
    monkeypatch.setattr(f['run'].gold, '_execute', actual)
    plan = f['prepare'](result_storage=storage())
    result = execute(f)
    assert len(produced) == 1
    root = f['directories']['one']
    reference = json.loads((root/'archive/report.json').read_bytes())
    assert reference['schema'] == 'QUANTOS_GOLD_CHUNKED_REPORT_REFERENCE_V1'
    with open_gold_result(root/'archive/report-parts', tmp_path/'index',
            expected_manifest_sha256=reference['manifest_sha256'],
            maximum_source_bytes=plan['result_storage']['maximum_bytes'],
            maximum_index_bytes=64_000_000) as report:
        assert json.loads(b''.join(json_parts(report))) == produced[0]
        assert report['result_hash'] == result['result_hash']
    before = (root/'archive/trials.db').read_bytes()
    assert execute(f) == result and len(produced) == 1
    assert (root/'archive/trials.db').read_bytes() == before
    assert all(f['registry'].trial_provenance(t['provenance_trial_id']) for t in plan['trials'])
    assert len(f['registry'].search_attempt_events('one')) == 2


def test_new_storage_budget_does_not_change_legacy_registration_contract(registered_case):
    f = registered_case
    plan = f['prepare']()
    assert 'result_storage' not in plan
    assert execute(f)['submission_enabled'] is False
    assert json.loads((f['directories']['one']/'archive/report.json').read_bytes())['schema'] == 'GOLD_BAR_PUBLIC_COMPARISON_V1'


@pytest.mark.parametrize('bad', [
    {'schema':'arbitrary'}, {'maximum_bytes':True}, {'maximum_bytes':32*1024**3+1},
    {'chunk_bytes':0}, {'chunk_bytes':16_000_001}, {'callback':'arbitrary'},
])
def test_storage_is_fixed_non_authorizing_and_bounded_before_registration(registered_case, bad):
    f = registered_case
    values = storage()
    values.update(bad)
    with pytest.raises(ValueError):
        f['prepare'](result_storage=values)
    assert not f['registry'].search_attempt_events('one')


def test_actual_guarded_queue_consumes_original_chunked_gold_and_recovers(registered_case, monkeypatch):
    from tools import registered_gold_chunked_job as job
    from tools import registered_research_job as workspace
    from tests.research.test_registered_research_job import queued
    from services.research.trial_ledger import TrialLedger
    from services.research.experiment_registry.store import SqliteExperimentStore
    f = registered_case
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(f['root']))
    plan = job.prepare_run(f['arguments'], search_id='queued-gold-chunks', dataset_version='native-gold',
        registered_at_ms=time.time_ns()//1_000_000, invocation_id='c'*64, result_storage=storage())
    project, database = workspace.workspace_paths()
    directory = project/'runs'/plan['invocation_id']
    original = f['run'].gold._execute
    calls = []
    def produce(arguments):
        with TrialLedger(str(directory/'archive/trials.db'), create=False, read_only=True) as ledger:
            assert all(ledger.load(t['native_trial_id']).status == 'RUNNING' for t in plan['trials'])
        store = SqliteExperimentStore(str(database), create=False, read_only=True)
        try:
            assert len(store.research_evidence_population()['provenance']) == 3
        finally:
            store.close()
        calls.append(1)
        return original(arguments)
    monkeypatch.setattr(f['run'].gold, '_execute', produce)
    registry, queue = queued(f['root'])
    uid = queue.submit('registered_gold_chunked', {'native_invocation_id':plan['invocation_id']},
        time.time_ns()//1_000_000, invocation_id='first-gold-chunks')
    queue.join()
    run = registry.get(uid)
    assert run.status == 'done', run.error
    report = json.loads(run.metrics['registered_gold_chunked_report_json'])
    assert set(report['accounts']) == {'channel','trend','reversion'}
    assert all(a['residual']=='0' for a in report['accounts'].values())
    assert report['economic_authority'] is report['promotion_approved'] is report['submission_enabled'] is False
    before = {p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}
    uid = queue.submit('registered_gold_chunked_recover', {'native_invocation_id':plan['invocation_id']},
        time.time_ns()//1_000_000, invocation_id='recover-gold-chunks')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    assert registry.get(uid).metrics == run.metrics
    assert calls == [1] and before == {p:p.read_bytes() for p in directory.rglob('*') if p.is_file()}


@pytest.mark.parametrize('stage', ['report.json','outcome.json'])
def test_durable_report_can_finish_same_invocation_after_publication_interruption(
        registered_case, monkeypatch, stage):
    f = registered_case
    plan = f['prepare'](result_storage=storage())
    put = f['run']._put
    def interrupt(path, *args, **kwargs):
        if path.name == 'outcome.json' and stage == 'outcome.json':
            raise RuntimeError('synthetic publication interruption')
        result = put(path, *args, **kwargs)
        if path.name == 'report.json' and stage == 'report.json':
            raise RuntimeError('synthetic durable report interruption')
        return result
    monkeypatch.setattr(f['run'], '_put', interrupt)
    with pytest.raises(RuntimeError):
        execute(f)
    root = f['directories']['one']
    before = {p:p.read_bytes() for p in (root/'archive/report-parts').rglob('*') if p.is_file()}
    monkeypatch.setattr(f['run'], '_put', put)
    monkeypatch.setattr(f['run'].gold, '_execute', lambda _:pytest.fail('durable original cannot rerun'))
    assert execute(f)['trial_ids'] == [t['native_trial_id'] for t in plan['trials']]
    assert before == {p:p.read_bytes() for p in (root/'archive/report-parts').rglob('*') if p.is_file()}


def test_incomplete_report_stays_unknown_without_reexecution_or_deleted_trials(registered_case, monkeypatch):
    from services.research.trial_ledger import TrialLedger
    f = registered_case
    values = storage()
    values['maximum_bytes'] = 1
    plan = f['prepare'](result_storage=values)
    with pytest.raises(ValueError):
        execute(f)
    root = f['directories']['one']
    assert (root/'archive/report-parts/failure.json').exists()
    assert not (root/'archive/report.json').exists()
    monkeypatch.setattr(f['run'].gold, '_execute', lambda _:pytest.fail('unknown computation cannot rerun'))
    with pytest.raises(ValueError, match='UNKNOWN_INVOCATION_OUTCOME'):
        execute(f)
    with TrialLedger(str(root/'archive/trials.db'), create=False, read_only=True) as ledger:
        assert all(ledger.load(t['native_trial_id']).status == 'RUNNING' for t in plan['trials'])
    assert len(f['registry'].search_candidates('one')) == 3


def test_corrupted_complete_report_recovery_rejects_without_repair_or_rerun(registered_case, monkeypatch):
    f = registered_case
    f['prepare'](result_storage=storage())
    execute(f)
    root = f['directories']['one']
    blob = next((root/'archive/report-parts/blobs').iterdir())
    blob.write_bytes(blob.read_bytes()+b' ')
    before = (root/'archive/trials.db').read_bytes()
    monkeypatch.setattr(f['run'].gold, '_execute', lambda _:pytest.fail('damaged originals cannot rerun'))
    with pytest.raises(ValueError):
        execute(f)
    assert before == (root/'archive/trials.db').read_bytes()


def test_report_above_original_single_file_budget_uses_explicit_new_protocol(registered_case, monkeypatch):
    from quantos.contracts.backtest_identity import exact_identity_hash
    f = registered_case
    actual = f['run'].gold._execute
    original_accounts = []
    def large(arguments):
        report = actual(arguments)
        original_accounts.append([t['simulation']['reconciliation'] for t in report['trials']])
        # Explicit synthetic transport load. Cash and all original account
        # fields remain unchanged; this is not long-market-coverage evidence.
        report['synthetic_transport_load'] = ['diagnostic transport fixture '*160]*4096
        report['result_hash'] = exact_identity_hash({k:v for k,v in report.items() if k!='result_hash'})
        return report
    monkeypatch.setattr(f['run'].gold, '_execute', large)
    values = storage()
    values.update(maximum_bytes=24_000_000, maximum_index_bytes=128_000_000, chunk_bytes=16_000_000)
    plan = f['prepare'](result_storage=values)
    result = execute(f)
    root = f['directories']['one']
    reference = json.loads((root/'archive/report.json').read_bytes())
    assert 16_000_000 < reference['bytes'] < values['maximum_bytes']
    assert result['result_hash'] == reference['result_hash']
    with f['run']._chunked_gold_report(root, plan) as report:
        assert [dict(t['simulation']['reconciliation']) for t in report['trials']] == original_accounts[0]
        assert len(report['synthetic_transport_load']) == 4096


def test_original_failed_producer_keeps_registered_failed_trials_for_new_format(registered_case, monkeypatch):
    from services.research.trial_ledger import TrialLedger
    f = registered_case
    plan = f['prepare'](result_storage=storage())
    def fail(_):
        raise RuntimeError('synthetic original producer failure')
    monkeypatch.setattr(f['run'].gold, '_execute', fail)
    with pytest.raises(RuntimeError):
        execute(f)
    root = f['directories']['one']
    with TrialLedger(str(root/'archive/trials.db'), create=False, read_only=True) as ledger:
        assert all(ledger.load(t['native_trial_id']).status=='FAILED' for t in plan['trials'])
    assert len(f['registry'].search_candidates('one')) == 3
    with pytest.raises(ValueError, match='TERMINAL_FAILURE'):
        execute(f)


@pytest.mark.parametrize('portfolio', [False, True])
def test_declared_segmented_gold_with_funding_keeps_original_policy_and_cohort(
        registered_case, monkeypatch, portfolio):
    from tests.research.test_gold_segmented_registration import request
    from tools.gold_bar_research import read_arguments
    from services.research.trial_ledger import TrialLedger
    f = registered_case
    args = {'release_identity':f['arguments']['release_identity']}
    root = request(f['root'], monkeypatch, args, portfolio=portfolio, declared=True, funding=True)
    arguments = read_arguments(root, original_source=args['release_identity'])
    definition = json.loads((root/'plan.json').read_bytes())['forward_design']
    f['arguments'] = arguments
    f['clock'][0] = time.time_ns()//1_000_000
    plan = f['run'].prepare_gold(arguments, project_root=f['root']/'project',
        search_id='one', producer_id='isolated-native', actor_id='AUTOMATED_TEST',
        dataset_version='native-xau', registered_at_ms=f['clock'][0],
        result_storage=storage(), forward_design=definition)
    f['directories']['one'] = f['run'].run_directory(f['root']/'project', plan)
    result = execute(f)
    assert len(result['trial_ids']) == (4 if portfolio else 3)
    with TrialLedger(str(f['directories']['one']/'archive/trials.db'), create=False, read_only=True) as ledger:
        assert all(ledger.load(t['native_trial_id']).validation_policy_digest ==
            f['run'].plan_digest(definition) for t in plan['trials'])
    with f['run']._chunked_gold_report(f['directories']['one'], plan) as report:
        assert report['trials'][0]['simulation']['reconciliation']['funding'] == '-0.3'
        assert report['trials'][0]['simulation']['reconciliation']['residual'] == '0'
