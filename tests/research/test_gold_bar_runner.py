import json

from tests.tools.test_gold_bar_observe import recorded
from tests.research.test_gold_bar_dataset import raw_rows, STEP
from tests.execution.test_perpetual_public_replay import START, gold_capture_fixture


def queued_experiment(tmp_path, monkeypatch, *, publication_failure=False, computation_interruption=False, portfolio=False):
    from tools import gold_bar_experiment, gold_bar_research
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.registry import RunRegistry
    from services.research_runner.runner import JobQueue
    rows = raw_rows()
    shift = START // STEP * STEP - 1001 * STEP
    for row in rows:
        row[0] += shift
        row[6] += shift
    _, journal = recorded(tmp_path, monkeypatch, values=rows, received=START + 1500)
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    source = {'fixture': True}
    monkeypatch.setattr(gold_bar_experiment, 'source_identity', lambda: source)
    monkeypatch.setattr(gold_bar_research, 'source_identity', lambda: source)
    root = tmp_path / 'data/cache/gold_bar_research'
    request = root / 'requests/1'
    request.mkdir(parents=True)
    import shutil
    shutil.copytree(journal, request / 'journal')
    (request / 'quotes.json').write_text(json.dumps(gold_capture_fixture()))
    (request / 'plan.json').write_text(json.dumps(dict(quantity='.1', initial_collateral='1000',
        fee_rate='.001', release_identity=source)))
    if portfolio:
        value = json.loads((request / 'plan.json').read_text())
        value['portfolio_plan'] = dict(quantities={n:'.1' for n in ('channel','trend','reversion')}, max_notional='100')
        (request / 'plan.json').write_text(json.dumps(value))
    registry = RunRegistry(ResearchRunStore(str(tmp_path / 'runs.db')))
    queue = JobQueue(registry)
    original_review = gold_bar_research.review_request
    original_write = gold_bar_experiment._write
    if computation_interruption:
        def interrupted_write(path, value):
            result = original_write(path, value)
            if path.name == 'running.json':
                raise RuntimeError('SYNTHETIC_WORKER_INTERRUPTION')
            return result
        monkeypatch.setattr(gold_bar_experiment, '_write', interrupted_write)
    if publication_failure:
        def fail_review(path):
            raise RuntimeError('SYNTHETIC_PUBLICATION_INTERRUPTION')
        monkeypatch.setattr(gold_bar_research, 'review_request', fail_review)
    uid = queue.submit('gold_bar_research', {'attempt': 1}, 1, invocation_id='gold-bars-one')
    queue.join()
    run = registry.get(uid)
    if computation_interruption:
        monkeypatch.setattr(gold_bar_experiment, '_write', original_write)
        assert run.status == 'error'
        assert not (root / 'results/1/manifest.json').exists()
        return registry, uid
    if publication_failure:
        monkeypatch.setattr(gold_bar_research, 'review_request', original_review)
        assert run.status == 'error'
        assert json.loads((root / 'results/1/manifest.json').read_text())['terminal'] == 'PASSED'
        return registry, uid
    assert run.status == 'done', run.error
    assert run.verdict == 'TECHNICAL_VALIDATION_ONLY'
    assert run.metrics['candidate_count'] == 3
    assert run.metrics['independent_review_status'] == 'INDEPENDENT_REVIEW_REQUIRED'
    assert (root / 'results/1/independent-request.json').is_file()
    report = json.loads(run.metrics['gold_bar_report_json'])
    assert report['economic_eligible'] is False
    assert len(report['trial_ids']) == (4 if portfolio else 3)
    assert len(report['candidates']) == 3
    assert all(row['order_count'] >= row['filled_order_count'] for row in report['candidates'])
    account = report['candidates'][0]['account']
    assert account['initial_equity'] == '1000'
    assert account['ending_equity'] == '999.97999'
    assert account['net_equity_change'] == '-0.02001'
    assert account['fees'] == '0.01001'
    assert account['reconciliation_residual'] == '0'
    assert account['valuation'] == 'MARK_TO_MARKET_INCLUDES_OPEN_POSITIONS'
    assert report['candidates'][0]['open_position_count'] == 1
    assert (root / 'results/1/trials.db').is_file()
    assert queue.submit('gold_bar_research', {'attempt': 1}, 2, invocation_id='gold-bars-one') == uid

    return registry, uid


def test_original_queue_runs_closed_bar_experiment_with_three_ledger_trials(tmp_path, monkeypatch):
    queued_experiment(tmp_path, monkeypatch)


def test_recovery_publishes_original_archived_trials_and_preserves_failed_job(tmp_path, monkeypatch):
    from services.research_runner.runner import JobQueue
    from tools import gold_bar_research
    registry, failed = queued_experiment(tmp_path, monkeypatch, publication_failure=True)
    root = tmp_path / 'data/cache/gold_bar_research/results/1'
    names = ('input.json', 'report.json', 'manifest.json', 'trials.db')
    original = {n: (root / n).read_bytes() for n in names}
    def no_archive(*a, **kw):
        raise AssertionError('recovery must not create another experiment')
    monkeypatch.setattr(gold_bar_research, 'archive', no_archive)
    queue = JobQueue(registry)
    uid = queue.submit('gold_bar_recover', {'attempt': 1}, 2, invocation_id='recover-one')
    queue.join()
    recovered = registry.get(uid)
    assert recovered.status == 'done', recovered.error
    assert registry.get(failed).status == 'error'
    assert all((root / n).read_bytes() == body for n, body in original.items())
    report = json.loads(recovered.metrics['gold_bar_report_json'])
    assert report['trial_ids'] == json.loads(original['manifest.json'])['trial_ids']
    assert (root / 'independent-request.json').exists()
    assert report['economic_eligible'] is False


def test_recovery_rejects_changed_request_without_replacing_archive(tmp_path, monkeypatch):
    import pytest
    from tools.gold_bar_research import resume_research
    queued_experiment(tmp_path, monkeypatch, publication_failure=True)
    root = tmp_path / 'data/cache/gold_bar_research'
    original = (root / 'results/1/input.json').read_bytes()
    plan = root / 'requests/1/plan.json'
    value = json.loads(plan.read_text())
    value['quantity'] = '.2'
    plan.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='REQUEST_CHANGED'):
        resume_research({'attempt': 1})
    assert (root / 'results/1/input.json').read_bytes() == original
    assert not (root / 'results/1/independent-request.json').exists()


def test_original_recovery_queue_finishes_interrupted_computation(tmp_path, monkeypatch):
    from services.research_runner.runner import JobQueue
    registry, failed = queued_experiment(tmp_path, monkeypatch, computation_interruption=True)
    root = tmp_path / 'data/cache/gold_bar_research/results/1'
    registration = json.loads((root / 'running.json').read_text())
    original_input = (root / 'input.json').read_bytes()
    queue = JobQueue(registry)
    uid = queue.submit('gold_bar_recover', {'attempt': 1}, 2, invocation_id='recover-interrupted')
    queue.join()
    recovered = registry.get(uid)
    assert recovered.status == 'done', recovered.error
    assert registry.get(failed).status == 'error'
    assert (root / 'input.json').read_bytes() == original_input
    report = json.loads(recovered.metrics['gold_bar_report_json'])
    assert report['trial_ids'] == registration['manifest']['trial_ids']
    assert len(report['candidates']) == 3
    assert report['candidates'][0]['open_position_count'] == 1
    assert report['economic_eligible'] is False
