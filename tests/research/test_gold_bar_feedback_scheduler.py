import pytest

from tests.tools.test_gold_bar_feedback_research import setup


@pytest.mark.parametrize('model_fails', [False, True])
def test_real_queue_consumes_feedback_once(tmp_path, monkeypatch, model_fails):
    from services.research_runner.gold_bar_feedback import feedback_tick
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.store import ResearchRunStore
    from services.research_runner.runner import JobQueue
    worker, value, _ = setup(tmp_path, monkeypatch)
    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        if model_fails:
            raise TimeoutError('fixture')
        return {'candidate_id': 1, 'model': 'fixture'}
    monkeypatch.setattr(worker, 'propose', model)
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue = JobQueue(registry)
    result = feedback_tick(tmp_path, queue, registry, now=3000)
    queue.join()
    row = registry.get(result['run_id'])
    assert row.status == ('error' if model_fails else 'done')
    if not model_fails:
        assert row.metrics['feedback_sha256'] == value['result_hash']
        assert row.verdict == 'TECHNICAL_DIAGNOSTIC_ONLY'
    assert feedback_tick(tmp_path, queue, registry, now=3001)['status'] == 'NO_BAR_FEEDBACK_READY'
    assert len(calls) == 1


@pytest.mark.parametrize('value', ['../x', 'A'*64, '', 1, True])
def test_runner_rejects_non_digest_feedback_parameters(value):
    from services.research_runner.run_kinds import validate_params
    with pytest.raises(ValueError):
        validate_params('gold_bar_feedback_research', {'feedback_sha256': value})


def test_resident_idle_scheduler_enqueues_feedback(tmp_path, monkeypatch):
    import asyncio
    import threading
    from services.research_runner import main, factory_campaign, gold_collection_program, gold_review, gold_bar_feedback
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.store import ResearchRunStore
    from services.research_runner.runner import JobQueue
    worker, value, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, 'propose', lambda *a, **k: {'candidate_id': 0, 'model': 'fixture'})
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue = JobQueue(registry)
    monkeypatch.setattr(main, '_STATE_ROOT', tmp_path)
    monkeypatch.setattr(main, '_registry', registry)
    monkeypatch.setattr(main, '_queue', queue)
    monkeypatch.setattr(main, '_reconcile_interrupted_runs_on_startup', lambda: None)
    monkeypatch.setenv('QUANTOS_FACTORY_CAMPAIGNS_ENABLED', 'true')
    monkeypatch.setenv('QUANTOS_BAR_FEEDBACK_RESEARCH_ENABLED', 'true')
    monkeypatch.setattr(factory_campaign, 'campaign_tick', lambda *a, **k: {'status': 'NO_CAMPAIGNS'})
    monkeypatch.setattr(gold_collection_program, 'program_tick', lambda *a, **k: {'status': 'NO_COLLECTION_READY'})
    monkeypatch.setattr(gold_review, 'review_tick', lambda *a, **k: {'status': 'NO_REVIEW_CHANGES'})
    called = threading.Event()
    original = gold_bar_feedback.feedback_tick
    def tick(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            called.set()
    monkeypatch.setattr(gold_bar_feedback, 'feedback_tick', tick)
    async def lifetime():
        async with main._owned_lifespan(main.app):
            assert await asyncio.to_thread(called.wait, 5)
    asyncio.run(lifetime())
    row = registry.get_by_invocation('gold-bar-feedback-'+value['result_hash'])
    assert row is not None and row.status == 'done'


@pytest.mark.parametrize('experiment_fails', [False, True])
def test_completed_hypothesis_releases_one_experiment(tmp_path, monkeypatch, experiment_fails):
    from services.research_runner.gold_bar_feedback import feedback_tick
    from services.research.run_registry.registry import RunRegistry
    from services.research.run_registry.store import ResearchRunStore
    from services.research_runner.runner import JobQueue
    from tools import gold_bar_feedback_experiment as experiment
    worker, value, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, 'propose', lambda *a, **k: {'candidate_id': 0, 'model': 'fixture'})
    calls = []
    def execute(params):
        calls.append(params)
        if experiment_fails:
            raise ValueError('fixture failure')
        return dict(schema='GOLD_BAR_FEEDBACK_EXPERIMENT_V1', status='RESEARCH_PROGRAM_COMPLETE',
            rounds=1, feedback_sha256=params['feedback_sha256'], proposal_hash='a'*64,
            program_summary_sha256='b'*64, submission_enabled=False, economic_eligible=False, authoritative=False)
    monkeypatch.setattr(experiment, 'run_research', execute)
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue = JobQueue(registry)
    first = feedback_tick(tmp_path, queue, registry, now=3000, experiments=True)
    queue.join()
    assert registry.get(first['run_id']).status == 'done'
    second = feedback_tick(tmp_path, queue, registry, now=3001, experiments=True)
    queue.join()
    assert second['status'] == 'SUBMITTED_BAR_FEEDBACK_EXPERIMENT'
    assert registry.get(second['run_id']).status == ('error' if experiment_fails else 'done')
    assert feedback_tick(tmp_path, queue, registry, now=3002, experiments=True)['status'] == 'NO_BAR_FEEDBACK_READY'
    assert calls == [{'feedback_sha256': value['result_hash']}]
