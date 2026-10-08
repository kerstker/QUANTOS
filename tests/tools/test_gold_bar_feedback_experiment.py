import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.tools.test_gold_bar_feedback_research import setup


@pytest.mark.parametrize('failure', [False, True])
def test_new_window_uses_isolated_child_and_never_restarts(tmp_path, monkeypatch, failure):
    from tools import gold_bar_feedback_experiment as experiment
    research, value, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(research, 'propose', lambda *a, **k: {'candidate_id': 1, 'model': 'fixture'})
    proposal = research.run(value['result_hash'])
    monkeypatch.setattr(experiment, 'runtime_state_root', lambda: tmp_path)
    monkeypatch.setattr(experiment, 'source_identity', lambda: {'commit': 'c'*40})
    calls = []
    def child(argv, **kwargs):
        calls.append(argv)
        assert argv[argv.index('--rounds')+1] == '1'
        assert argv[argv.index('--initial-proposal')+1] == proposal['proposal_directory']
        assert '--register-original-trials' in argv
        assert kwargs['timeout'] == 7200 and kwargs['env']['ALLOW_ORDER_SUBMISSION'] == 'false'
        state = Path(kwargs['env']['QUANTOS_STATE_ROOT'])
        assert state != tmp_path and tmp_path in state.parents
        if failure:
            return SimpleNamespace(returncode=1)
        directory = state/'data/cache/gold_bar_research/programs/1'
        directory.mkdir(parents=True)
        (directory/'plan.json').write_text(json.dumps(dict(source={'commit': 'c'*40},
            rounds=1, first_attempt=1, initial_proposal={'proposal_hash': proposal['proposal_hash']})))
        (directory/'summary.json').write_text(json.dumps(dict(status='RESEARCH_PROGRAM_COMPLETE',
            rounds=[{'attempt': 1}], economic_eligible=False, authoritative=False, submission_enabled=False)))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(experiment.subprocess, 'run', child)
    params = {'feedback_sha256': value['result_hash']}
    if failure:
        for _ in range(2):
            with pytest.raises(ValueError):
                experiment.run_research(params)
    else:
        result = experiment.run_research(params)
        assert experiment.run_research(params) == result
        assert result['submission_enabled'] is False and result['economic_eligible'] is False
    assert len(calls) == 1
