import hashlib
import asyncio

import pytest

from tests.tools.test_gold_forward_review import captured
from tools.gold_strategy_publish import publish, verify


def test_package_reuses_original_review_and_never_copies_unlisted_files(tmp_path, monkeypatch):
    observation, trial, _ = captured(tmp_path, monkeypatch)
    (trial/'unrelated.txt').write_text('not part of the strategy archive')
    original = {p:hashlib.sha256(p.read_bytes()).hexdigest() for root in (observation,trial)
                for p in root.iterdir() if p.is_file()}
    output = tmp_path/'package'
    result = publish(observation, strategy_trial=trial, output_directory=output, source_commit='c'*40)
    assert len(result['files']) == 11 and result['installed'] is False
    assert result['source_commit_binding_verified'] is False
    assert not list(output.rglob('unrelated.txt'))
    assert verify(output) == result
    assert publish(observation, strategy_trial=trial, output_directory=output, source_commit='c'*40) == result
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == value for p,value in original.items())
    with pytest.raises(ValueError, match='EXISTING_PACKAGE_CONFLICT'):
        publish(observation, strategy_trial=trial, output_directory=output, source_commit='d'*40)
    leaf = next(output.glob('strategy-observations/*/result.json'))
    leaf.write_text('{}')
    with pytest.raises(ValueError): verify(output)


def test_active_trial_cannot_be_published_as_a_frozen_copy(tmp_path, monkeypatch):
    observation, trial, _ = captured(tmp_path, monkeypatch)
    (trial/'trials.db-wal').write_bytes(b'synthetic unfinished journal')
    output = tmp_path/'package'
    with pytest.raises(ValueError, match='TRIAL_NOT_FROZEN'):
        publish(observation, strategy_trial=trial, output_directory=output, source_commit='c'*40)
    assert not output.exists()


@pytest.mark.parametrize('late', [False, True])
def test_forward_observation_automatically_publishes_only_completed_input(tmp_path, monkeypatch, late):
    from tests.tools.test_gold_forward_observe import strategy_forward_case, mock_socket
    from tools.gold_strategy_publish import observe_and_publish
    trial, _, document, plan, initial = strategy_forward_case(tmp_path, monkeypatch)
    if late:
        document['packets'][0]['received_at_ms'] = plan['decision_times_ms'][0]+plan['max_lateness_ms']+1
    observation = tmp_path/'observation'
    mock_socket(monkeypatch, observation, document=document, initial_ms=initial)
    result = asyncio.run(observe_and_publish(plan, observation_directory=observation,
        strategy_trial=trial, output_directory=tmp_path/'package', source_commit='c'*40))
    if late:
        assert result['status'] == 'OBSERVATION_INCOMPLETE' and result['package_sha256'] is None
        assert not (tmp_path/'package').exists()
    else:
        assert result['status'] == 'INPUT_PACKAGE_PREPARED'
        assert verify(tmp_path/'package')['result_hash'] == result['package_sha256']
