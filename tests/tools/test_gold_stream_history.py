"""The original complete Gold accounts can spill history without changing cash."""
import json

import pytest

from quantos.streaming_identity import json_parts
from tests.tools.test_registered_research_run import registered_case as registered_case


@pytest.fixture
def original_arguments(registered_case, monkeypatch):
    from tests.research.test_gold_segmented_registration import request
    from tools.gold_bar_research import read_arguments
    f = registered_case
    root = request(f['root'], monkeypatch, {'release_identity':f['arguments']['release_identity']},
        portfolio=True, declared=True, funding=True)
    return read_arguments(root, original_source=f['arguments']['release_identity'])


def test_original_complete_streamed_comparison_matches_memory_accounts_and_funding(
        original_arguments, tmp_path):
    from tools.gold_bar_experiment import _execute, _bytes
    from tools.gold_stream_history import GoldResearchHistory
    expected = _execute(original_arguments)
    with GoldResearchHistory(tmp_path/'histories', maximum_history_bytes=256_000_000) as histories:
        actual = _execute(original_arguments, history=histories)
        raw = b''.join(json_parts(actual))
        assert json.loads(raw) == json.loads(_bytes(expected))
        assert actual['result_hash'] == expected['result_hash']
        assert len(histories.accounts) == 5  # Three original candidates, cash and shared portfolio.
        assert all(account._history_store is not None for account in histories.accounts)
        assert all(account._history_store.path.is_file() for account in histories.accounts)
        for account in histories.accounts:
            store = account._history_store
            assert store.db.execute("SELECT count(*) FROM arrays WHERE namespace='frames'").fetchone()[0] == len(store.frames)
            assert len(store.frames) > 0
        simulation = actual['portfolio']['simulation']
        assert simulation['reconciliation']['residual'] == '0'
        assert actual['trials'][0]['simulation']['reconciliation']['funding'] == '-0.3'
        kept = actual['trials'][0]['signal_observations']
        assert not isinstance(kept, (list, tuple))
        assert len(list(kept)) > 0
    with pytest.raises(ValueError, match='CLOSED'):
        list(kept)


def test_whole_history_budget_rejects_without_erasing_the_original_scratch(
        original_arguments, tmp_path):
    from tools.gold_bar_experiment import _execute
    from tools.gold_stream_history import GoldResearchHistory
    root = tmp_path/'histories'
    with pytest.raises(ValueError, match='HISTORY_BYTES'):
        with GoldResearchHistory(root, maximum_history_bytes=1) as histories:
            _execute(original_arguments, history=histories)
    assert root.is_dir()
    assert list(root.rglob('history.sqlite3'))


def test_history_budget_failure_closes_the_created_database(tmp_path, monkeypatch):
    import sqlite3
    from tools import perpetual_stream_history as storage
    closed = []
    class Connection(sqlite3.Connection):
        def close(self):
            closed.append(self)
            super().close()
    connect = sqlite3.connect
    monkeypatch.setattr(storage.sqlite3, 'connect',
        lambda path: connect(path, factory=Connection))
    with pytest.raises(ValueError, match='HISTORY_BYTES'):
        storage.SimulationHistory(tmp_path/'history', maximum_bytes=1)
    assert len(closed) == 1
    assert (tmp_path/'history/history.sqlite3').is_file()


@pytest.mark.parametrize('damage', ['rewrite', 'replace', 'extra'])
def test_sealed_original_histories_reject_changes_before_consumption(
        original_arguments, tmp_path, damage):
    from tools.gold_bar_experiment import _execute
    from tools.gold_stream_history import GoldResearchHistory
    with pytest.raises(ValueError, match='HISTORY_(CHANGED|MEMBERS)'):
        with GoldResearchHistory(tmp_path/'histories', maximum_history_bytes=256_000_000) as histories:
            actual = _execute(original_arguments, history=histories)
            path = histories.accounts[0]._history_store.path
            if damage == 'rewrite':
                path.write_bytes(path.read_bytes())
                list(actual['cash_baseline']['equity_history'])
            elif damage == 'replace':
                raw = path.read_bytes()
                path.unlink()
                path.write_bytes(raw)
                path.chmod(0o600)
                list(actual['cash_baseline']['equity_history'])
            else:
                (path.parent/'foreign').write_bytes(b'extra')
                histories.verify()


def test_total_budget_is_shared_by_the_five_original_accounts(original_arguments, tmp_path):
    from tools.gold_bar_experiment import _execute
    from tools.gold_stream_history import GoldResearchHistory
    with GoldResearchHistory(tmp_path/'reference', maximum_history_bytes=256_000_000) as reference:
        _execute(original_arguments, history=reference)
        first_bytes = reference.accounts[0]._history_store.path.stat().st_size
    with pytest.raises(ValueError, match='HISTORY_BYTES'):
        with GoldResearchHistory(tmp_path/'limited', maximum_history_bytes=first_bytes+1) as histories:
            _execute(original_arguments, history=histories)
    assert len(histories.accounts) == 1
    assert (tmp_path/'limited/0/history.sqlite3').is_file()
    assert (tmp_path/'limited/1/history.sqlite3').is_file()


def history_storage(**updates):
    return dict(schema='QUANTOS_GOLD_HISTORY_STORAGE_V1', maximum_bytes=256_000_000, **updates)


def test_registered_original_producer_uses_frozen_history_budget_and_cold_result(
        original_arguments, registered_case, tmp_path, monkeypatch):
    from tests.tools.test_registered_gold_chunked_result import storage
    from tests.tools.test_registered_research_run import execute
    f = registered_case
    f['arguments'] = original_arguments
    run = f['run']
    expected = json.loads(run.gold._bytes(run.gold._execute(original_arguments)))
    plan = run.prepare_gold(original_arguments, project_root=f['root']/'project',
        search_id='one', producer_id='original-history-test', actor_id='AUTOMATED_TEST',
        dataset_version='native-xau', registered_at_ms=f['clock'][0],
        result_storage=storage(), history_storage=history_storage())
    f['directories']['one'] = run.run_directory(f['root']/'project', plan)
    assert plan['schema'] == 'QUANTOS_REGISTERED_GOLD_CHUNKED_RUN_V2'
    producer = run.gold._execute
    calls, accounts, thunks = [], [], []
    original_native = run._native_run
    def native_call(**configuration):
        original_execute = configuration['execute']
        def execute():
            thunks.append(1)
            return original_execute()
        return original_native(**{**configuration, 'execute': execute})
    monkeypatch.setattr(run, '_native_run', native_call)
    def produce(arguments, *, history):
        from services.research.trial_ledger import TrialLedger
        root = f['directories']['one']
        with TrialLedger(str(root/'archive/trials.db'), create=False, read_only=True) as ledger:
            assert all(ledger.load(t['native_trial_id']).status == 'RUNNING' for t in plan['trials'])
        report = producer(arguments, history=history)
        calls.append(json.loads(b''.join(json_parts(report))))
        accounts.extend(history.accounts)
        assert history.maximum_bytes == plan['history_storage']['maximum_bytes']
        return report
    monkeypatch.setattr(run.gold, '_execute', produce)
    result = execute(f)
    assert calls == [expected] and len(accounts) == 5 and thunks == [1]
    with run._chunked_gold_report(f['directories']['one'], plan) as report:
        assert json.loads(b''.join(json_parts(report))) == expected
    for account in accounts:
        import sqlite3
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            account._history_store.db.execute('SELECT 1')
        assert account._history_store.path.is_file()
    before = {p:p.read_bytes() for p in f['directories']['one'].rglob('*') if p.is_file()}
    assert execute(f) == result and calls == [expected] and thunks == [1]
    assert before == {p:p.read_bytes() for p in f['directories']['one'].rglob('*') if p.is_file()}
    assert result['submission_enabled'] is result['economic_eligible'] is False


@pytest.mark.parametrize('bad', [{'maximum_bytes':True}, {'maximum_bytes':0},
    {'maximum_bytes':32*1024**3+1}, {'schema':'arbitrary'}, {'callback':'arbitrary'}])
def test_registered_history_budget_is_validated_before_any_attempt(registered_case, bad):
    from tests.tools.test_registered_gold_chunked_result import storage
    f = registered_case
    values = history_storage()
    values.update(bad)
    with pytest.raises(ValueError):
        f['prepare'](result_storage=storage(), history_storage=values)
    assert not f['registry'].search_attempt_events('one')


def test_original_history_cut_is_checked_before_native_success(registered_case, monkeypatch):
    from tests.tools.test_registered_gold_chunked_result import storage
    from tests.tools.test_registered_research_run import execute
    from services.research.trial_ledger import TrialLedger
    f = registered_case
    plan = f['prepare'](result_storage=storage(), history_storage=history_storage())
    producer, publish = f['run'].gold._execute, f['run']._put_gold_report
    history = []
    def produce(arguments, **context):
        history.append(context['history'])
        return producer(arguments, **context)
    def rewrite_after_publication(*args):
        publish(*args)
        path = history[0].accounts[0]._history_store.path
        path.write_bytes(path.read_bytes())
    monkeypatch.setattr(f['run'].gold, '_execute', produce)
    monkeypatch.setattr(f['run'], '_put_gold_report', rewrite_after_publication)
    with pytest.raises(ValueError, match='HISTORY_CHANGED'):
        execute(f)
    root = f['directories']['one']
    assert not (root/'outcome.json').exists()
    with TrialLedger(str(root/'archive/trials.db'), create=False, read_only=True) as ledger:
        assert all(ledger.load(t['native_trial_id']).status=='RUNNING' for t in plan['trials'])
    # The already durable complete original report may finish the same
    # invocation, with full cold validation and no second computation.
    monkeypatch.setattr(f['run'].gold, '_execute', lambda *a, **k:pytest.fail('cannot rerun'))
    result = execute(f)
    assert result['submission_enabled'] is False and len(history) == 1


def test_guarded_queue_uses_registered_original_history_and_recovers(registered_case, monkeypatch):
    import time
    from tools import registered_gold_chunked_job as job
    from tools import registered_research_job as workspace
    from tests.research.test_registered_research_job import queued
    from tests.tools.test_registered_gold_chunked_result import storage
    f = registered_case
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(f['root']))
    plan = job.prepare_run(f['arguments'], search_id='original-history-queue',
        dataset_version='native-xau', registered_at_ms=time.time_ns()//1_000_000,
        invocation_id='c'*64, result_storage=storage(), history_storage=history_storage())
    project, _ = workspace.workspace_paths()
    producer = f['run'].gold._execute
    calls = []
    def produce(arguments, **context):
        assert context['history'].maximum_bytes == plan['history_storage']['maximum_bytes']
        calls.append(1)
        return producer(arguments, **context)
    monkeypatch.setattr(f['run'].gold, '_execute', produce)
    registry, queue = queued(f['root'])
    uid = queue.submit('registered_gold_chunked', {'native_invocation_id':plan['invocation_id']},
        time.time_ns()//1_000_000, invocation_id='streamed-gold')
    queue.join()
    run = registry.get(uid)
    assert run.status == 'done', run.error
    report = json.loads(run.metrics['registered_gold_chunked_report_json'])
    assert report['plan']['schema'] == 'QUANTOS_REGISTERED_GOLD_CHUNKED_RUN_V2'
    assert all(a['residual']=='0' for a in report['accounts'].values())
    before = {p:p.read_bytes() for p in (project/'runs').rglob('*') if p.is_file()}
    uid = queue.submit('registered_gold_chunked_recover', {'native_invocation_id':plan['invocation_id']},
        time.time_ns()//1_000_000, invocation_id='streamed-gold-recover')
    queue.join()
    assert registry.get(uid).status == 'done', registry.get(uid).error
    assert registry.get(uid).metrics == run.metrics and calls == [1]
    assert before == {p:p.read_bytes() for p in (project/'runs').rglob('*') if p.is_file()}
