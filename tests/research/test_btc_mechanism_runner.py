"""Predictive research reaches the queue and console without economic claims."""
from dataclasses import replace
import json
import numpy as np
import pytest
from services.research.run_registry.store import ResearchRunStore
from services.research.run_registry.registry import RunRegistry
from services.research_runner.runner import JobQueue
from services.research_runner.run_kinds import validate_params
from services.research.trial_ledger import TrialLedger
from tools import btc_mechanism_study as tool


def test_missing_inputs_register_all_failed_trials(tmp_path):
    with pytest.raises((ValueError, FileNotFoundError)):
        tool.run(tmp_path / 'missing', tmp_path / 'results', 1)
    failure = json.loads((tmp_path / 'results/1/failure.json').read_text())
    assert len(failure['trial_ids']) == 4
    with TrialLedger(str(tmp_path / 'results/trials.db'), create=False) as ledger:
        assert ledger.verify()
        assert all(ledger.load(t).status == 'FAILED' for t in failure['trial_ids'])


@pytest.mark.parametrize('params', [{'attempt': True}, {'attempt': 0}, {'attempt':1001}, {'path':'/tmp'}])
def test_bounded_parameters(params):
    with pytest.raises(ValueError): validate_params('btc_mechanism', params)


def test_queue_prediction_projection_and_immutable_attempt(tmp_path, monkeypatch):
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(tmp_path))
    directory = tmp_path / 'data/cache/perpetual_alpha/input'
    directory.mkdir(parents=True)
    (directory / 'funding-capture.json').write_text('{}')
    days = np.arange(tool.timestamp('2024-01-01'), tool.timestamp('2026-08-01'), tool.DAY)
    rng = np.random.default_rng(5)
    x = rng.normal(size=(len(days), 5)); y = rng.normal(size=len(days)) * .01
    monkeypatch.setattr(tool, 'load_panel', lambda _: (days, x, y, {'historical_receive_times':'UNKNOWN'}))
    registry = RunRegistry(ResearchRunStore(str(tmp_path/'runs.db')))
    queue = JobQueue(registry)
    run_id = queue.submit('btc_mechanism', {'attempt':1}, 1, invocation_id='first')
    queue.join(); record = registry.get(run_id)
    assert record.status == 'done', record.error
    assert record.metrics['trial_count'] == 4
    assert record.metrics['account_pnl_computed'] == 'false'
    from services.console.api import research_run_views as views
    monkeypatch.setattr(views, '_runs_registry', lambda: registry)
    detail = views.technical_replay(run_id)['technical_replay']
    assert detail['metrics']['evaluation'] == 'EXPLORATORY_PREDICTION_ONLY'
    assert len(detail['series']['all_rmse_bps']) == 4
    assert views._run_summary(record)['verdict'] == 'EXPLORATORY_PREDICTION_ONLY'
    assert 'invalidation_code' not in views._run_detail(record)
    assert views._technical_replay_detail(replace(record, metrics={})) is None
    path = tmp_path/'data/cache/btc_mechanism/results/1/result.json'
    original = path.read_bytes()
    second = queue.submit('btc_mechanism', {'attempt':1}, 2, invocation_id='second')
    queue.join()
    assert registry.get(second).status == 'error'
    assert path.read_bytes() == original
