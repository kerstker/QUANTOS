import pytest

from tests.tools.test_gold_forward_review import captured
from tools import gold_execution_identity as identity
from tools.gold_forward_review import review


def test_prepared_strategy_reuses_verification_but_checks_all_archive_bytes(tmp_path, monkeypatch):
    observation, trial, _ = captured(tmp_path, monkeypatch)
    expected = review(observation, strategy_trial=trial)
    prepared = identity.PreparedStrategy(trial)
    def forbidden(*a, **kw):
        raise AssertionError('warm decision repeated full strategy verification')
    monkeypatch.setattr(identity, 'retain_strategy_definition', forbidden)
    assert review(observation, strategy_trial=trial, prepared_strategy=prepared) == expected
    definition = prepared.definition(trial)
    definition['exit_and_size_plan']['quantity'] = '999'
    assert prepared.definition(trial)['exit_and_size_plan']['quantity'] != '999'
    report = trial/'report.json'
    report.write_bytes(report.read_bytes()+b' ')
    with pytest.raises(ValueError, match='PREPARED_STRATEGY_CHANGED'):
        review(observation, strategy_trial=trial, prepared_strategy=prepared)
