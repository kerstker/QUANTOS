"""Real technical replay must use mutable state beside immutable installed code."""
import json
from tests.execution.test_perpetual_public_replay import capture_fixture


def test_explicit_state_root_reads_input_and_retains_real_ledger_outside_code(tmp_path, monkeypatch):
    from tools import perpetual_replay_research as replay

    state = tmp_path / 'state'
    code_working_directory = tmp_path / 'immutable-code'
    code_working_directory.mkdir(mode=0o555)
    capture = state / replay.CAPTURE
    capture.parent.mkdir(parents=True)
    capture.write_text(json.dumps(capture_fixture()))
    monkeypatch.chdir(code_working_directory)
    monkeypatch.setenv('QUANTOS_STATE_ROOT', str(state))

    result = replay.run_replay({'strategy_id': 1, 'frequency_ms': 1000})

    assert result['evidence_status'] == 'TECHNICAL_VALIDATION_ONLY'
    assert result['authoritative'] is False
    assert result['reconciliation']['residual'] == '0'
    assert len(result['orders']) == 1
    artifact = state / replay.OUTPUT / (result['result_hash'] + '.json')
    assert json.loads(artifact.read_text()) == result
    assert list(code_working_directory.iterdir()) == []


def test_default_state_keeps_existing_working_directory_behavior(tmp_path, monkeypatch):
    from tools import perpetual_replay_research as replay

    capture = tmp_path / replay.CAPTURE
    capture.parent.mkdir(parents=True)
    capture.write_text(json.dumps(capture_fixture()))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('QUANTOS_STATE_ROOT', raising=False)

    result = replay.run_replay({'strategy_id': 1, 'frequency_ms': 1000})

    artifact = tmp_path / replay.OUTPUT / (result['result_hash'] + '.json')
    assert json.loads(artifact.read_text()) == result
    assert result['authoritative'] is False
