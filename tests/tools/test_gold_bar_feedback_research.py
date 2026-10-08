import json

import pytest

from tests.tools.test_gold_bar_feedback_publish import document


def setup(tmp_path, monkeypatch):
    from tools import gold_bar_feedback_research as research
    value = document()
    root = tmp_path/'data/cache/ai_factory/bar-execution-feedback'
    root.mkdir(parents=True)
    (root/(value['result_hash']+'.json')).write_text(json.dumps(value))
    monkeypatch.setattr(research, 'runtime_state_root', lambda: tmp_path)
    monkeypatch.setattr(research, 'source_identity', lambda: {'commit': 'c'*40})
    monkeypatch.setattr(research.time, 'time_ns', lambda: 3000*1000000)
    return research, value, root


def test_feedback_generates_once_and_remains_importable(tmp_path, monkeypatch):
    research, value, root = setup(tmp_path, monkeypatch)
    calls = []
    def model(objective, remaining, history, **kwargs):
        calls.append(objective)
        assert value['result_hash'] in objective
        assert remaining == [0, 1, 2] and history == []
        assert kwargs['candidate_catalog'] == 'GOLD_BAR_CANDIDATES_V1'
        return {'candidate_id': 2, 'model': 'fixture'}
    monkeypatch.setattr(research, 'propose', model)
    result = research.run(value['result_hash'])
    assert research.run(value['result_hash']) == result and len(calls) == 1
    assert result['submission_enabled'] is False and result['economic_eligible'] is False
    from tools.gold_initial_proposal import freeze_proposal
    proposal, _ = freeze_proposal(result['proposal_directory'], tmp_path/'imported')
    assert proposal['result_hash'] == result['proposal_hash']


@pytest.mark.parametrize('damage', ['future', 'tamper', 'interrupted'])
def test_invalid_or_failed_feedback_never_repeats_model(tmp_path, monkeypatch, damage):
    research, value, root = setup(tmp_path, monkeypatch)
    calls = []
    def model(*args, **kwargs):
        calls.append(1)
        raise TimeoutError('test timeout')
    monkeypatch.setattr(research, 'propose', model)
    if damage == 'future':
        monkeypatch.setattr(research.time, 'time_ns', lambda: 1000*1000000)
    elif damage == 'tamper':
        value['entry_decision_ms'] += 1
        (root/(value['result_hash']+'.json')).write_text(json.dumps(value))
    for _ in range(2):
        with pytest.raises(ValueError):
            research.run(value['result_hash'])
    assert len(calls) == (1 if damage == 'interrupted' else 0)


def test_feedback_changed_during_model_is_not_reported_complete(tmp_path, monkeypatch):
    research, value, root = setup(tmp_path, monkeypatch)
    def model(*args, **kwargs):
        (root/(value['result_hash']+'.json')).write_text('{}')
        return {'candidate_id': 0, 'model': 'fixture'}
    monkeypatch.setattr(research, 'propose', model)
    with pytest.raises(ValueError, match='RESEARCH_CHANGED'):
        research.run(value['result_hash'])
    target = tmp_path/'data/cache/ai_factory/bar-feedback-research'/value['result_hash']
    assert (target/'hypothesis/roles/0.json').is_file()
    assert not (target/'result.json').exists()
