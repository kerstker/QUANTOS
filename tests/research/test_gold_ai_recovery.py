import json

import pytest

from tests.research.test_gold_ai_research import setup


def test_resume_after_proposal_runs_trial_once_without_new_model_call(tmp_path, monkeypatch):
    tool, calls, dest = setup(tmp_path, monkeypatch)
    archive = tool.archive_experiment
    def stop(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(tool, 'archive_experiment', stop)
    with pytest.raises(KeyboardInterrupt):
        tool.run_research({'attempt': 1})
    assert len(calls) == 1 and not (dest/'trial').exists()
    monkeypatch.setattr(tool, 'archive_experiment', archive)
    result = tool.resume_research({'attempt': 1})
    assert result['status'] == 'COMPLETED' and len(calls) == 1
    before = (dest/'trial/trials.db').read_bytes()
    assert tool.resume_research({'attempt': 1})['trial_id'] == result['trial_id']
    assert (dest/'trial/trials.db').read_bytes() == before and len(calls) == 1


def test_resume_after_trial_only_reconstructs_missing_summary(tmp_path, monkeypatch):
    tool, calls, dest = setup(tmp_path, monkeypatch)
    write = tool._write
    def interrupt(path, value):
        if path == dest/'result.json':
            raise KeyboardInterrupt()
        return write(path, value)
    monkeypatch.setattr(tool, '_write', interrupt)
    with pytest.raises(KeyboardInterrupt):
        tool.run_research({'attempt': 1})
    original = (dest/'trial/trials.db').read_bytes()
    monkeypatch.setattr(tool, '_write', write)
    def forbidden(*args, **kwargs):
        pytest.fail('completed trial must not be rerun')
    monkeypatch.setattr(tool, 'archive_experiment', forbidden)
    result = tool.resume_research({'attempt': 1})
    assert result['status'] == 'COMPLETED' and len(calls) == 1
    assert (dest/'trial/trials.db').read_bytes() == original


def test_unknown_trial_terminal_blocks_recovery_without_replay(tmp_path, monkeypatch):
    from tools import gold_cost_research
    tool, calls, dest = setup(tmp_path, monkeypatch)
    def stop(**kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(gold_cost_research, 'replay_costs', stop)
    with pytest.raises(KeyboardInterrupt):
        tool.run_research({'attempt': 1})
    original = (dest/'trial/trials.db').read_bytes()
    with pytest.raises(ValueError, match='TRIAL_TERMINAL_UNKNOWN'):
        tool.resume_research({'attempt': 1})
    assert len(calls) == 1 and (dest/'trial/trials.db').read_bytes() == original


def test_unresolved_model_reservation_never_calls_model_again(tmp_path, monkeypatch):
    tool, calls, dest = setup(tmp_path, monkeypatch)
    def stop(*args, **kwargs):
        calls.append(1)
        raise KeyboardInterrupt()
    monkeypatch.setattr(tool, 'propose', stop)
    with pytest.raises(KeyboardInterrupt):
        tool.run_research({'attempt': 1})
    with pytest.raises(ValueError, match='PROPOSAL_UNRESOLVED'):
        tool.resume_research({'attempt': 1})
    assert len(calls) == 1 and not (dest/'trial').exists()


def test_concurrent_recovery_is_refused_while_original_worker_holds_lock(tmp_path, monkeypatch):
    tool, _, _ = setup(tmp_path, monkeypatch)
    original = tool.propose
    def nested(*args, **kwargs):
        with pytest.raises(RuntimeError, match='GOLD_AI_BUSY'):
            tool.resume_research({'attempt': 1})
        return original(*args, **kwargs)
    monkeypatch.setattr(tool, 'propose', nested)
    assert tool.run_research({'attempt': 1})['status'] == 'COMPLETED'


def test_complete_summary_must_match_retained_trial(tmp_path, monkeypatch):
    tool, _, dest = setup(tmp_path, monkeypatch)
    tool.run_research({'attempt': 1})
    path = dest/'result.json'
    document = json.loads(path.read_bytes())
    document['trial_id'] = 'a'*64
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match='SUMMARY_BINDING'):
        tool.resume_research({'attempt': 1})


def test_changed_source_cannot_resume_unstarted_trial(tmp_path, monkeypatch):
    tool, calls, dest = setup(tmp_path, monkeypatch)
    def stop(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(tool, 'archive_experiment', stop)
    with pytest.raises(KeyboardInterrupt):
        tool.run_research({'attempt': 1})
    monkeypatch.setattr(tool, 'source_identity', lambda: {'commit': 'a'*40})
    with pytest.raises(ValueError, match='SOURCE_VERSION_MISMATCH'):
        tool.resume_research({'attempt': 1})
    assert len(calls) == 1 and not (dest/'trial').exists()


def test_recovery_queue_returns_verified_original_trial(tmp_path, monkeypatch):
    from services.research.run_registry.store import ResearchRunStore
    from services.research.run_registry.registry import RunRegistry
    from services.research_runner.runner import JobQueue
    tool, calls, dest = setup(tmp_path, monkeypatch)
    original = tool.run_research({'attempt': 1})
    raw = (dest/'result.json').read_bytes()
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'recovery-runs.db')))
    queue = JobQueue(registry)
    uid = queue.submit('gold_ai_recover', {'attempt': 1}, 1, invocation_id='recover-one')
    queue.join()
    run = registry.get(uid)
    assert run.status == 'done', run.error
    assert run.metrics['trial_id'] == original['trial_id']
    assert len(calls) == 1 and (dest/'result.json').read_bytes() == raw
