import hashlib
import json

import pytest

from services.research.trial_ledger import TrialLedger
from tests.tools.test_gold_signal_replay import archive
from tools.gold_signal_replay import archive_replay, main, verify_archive


def test_archive_retains_exact_input_decisions_and_trial_history(tmp_path):
    raw = json.dumps(archive()).encode()
    root = tmp_path / 'run'
    result = archive_replay(raw, root)
    assert (root / 'input.json').read_bytes() == raw
    manifest = json.loads((root / 'manifest.json').read_bytes())
    assert manifest['input_sha256'] == hashlib.sha256(raw).hexdigest()
    assert manifest['report_sha256'] == hashlib.sha256((root / 'report.json').read_bytes()).hexdigest()
    assert manifest['mode'] == 'RETROSPECTIVE_REPLAY'
    assert manifest['preregistered'] is False and manifest['authoritative'] is False
    assert result['status'] == 'RESEARCH_SIGNALS_ONLY'
    assert len(result['records']) == 2
    with TrialLedger(str(root / 'trials.db'), create=False, read_only=True) as ledger:
        trial = ledger.load(manifest['trial_id'])
        assert [e.status for e in trial.events] == ['PLANNED', 'RUNNING', 'PASSED']
    for name, digest in manifest['source_sha256'].items():
        assert hashlib.sha256((root / 'source' / name).read_bytes()).hexdigest() == digest
    assert verify_archive(root)['status'] == 'VERIFIED_LOCAL_REPLAY'


def test_incomplete_replay_retains_prefix_and_failed_terminal(tmp_path):
    d = archive()
    d['packets'] = d['packets'][:4]
    root = tmp_path / 'run'
    result = archive_replay(json.dumps(d).encode(), root)
    assert result['status'] == 'INPUT_INCOMPLETE' and len(result['records']) == 1
    manifest = json.loads((root / 'manifest.json').read_bytes())
    with TrialLedger(str(root / 'trials.db'), create=False, read_only=True) as ledger:
        assert ledger.load(manifest['trial_id']).status == 'FAILED'


def test_bad_json_is_recorded_without_echoing_input(tmp_path):
    root = tmp_path / 'run'
    result = archive_replay(b'{"secret":', root)
    assert result == {'status': 'REJECTED', 'reason': 'INVALID_OR_UNREADABLE_REPLAY_INPUT'}
    assert (root / 'input.json').read_bytes() == b'{"secret":'
    assert 'secret' not in (root / 'report.json').read_text()
    assert json.loads((root / 'manifest.json').read_bytes())['terminal'] == 'FAILED'


def test_existing_output_cannot_be_overwritten(tmp_path):
    root = tmp_path / 'run'
    root.mkdir()
    (root / 'report.json').write_text('original')
    with pytest.raises(FileExistsError):
        archive_replay(json.dumps(archive()).encode(), root)
    assert (root / 'report.json').read_text() == 'original'


def test_source_change_during_replay_prevents_success(tmp_path, monkeypatch):
    from tools import gold_signal_replay as module
    original = module._source_files
    calls = 0
    def changing():
        nonlocal calls
        calls += 1
        data = original()
        if calls > 1:
            data['services/research/gold_signal.py'] += b'\n# changed'
        return data
    monkeypatch.setattr(module, '_source_files', changing)
    result = archive_replay(json.dumps(archive()).encode(), tmp_path / 'run')
    assert result['status'] == 'REJECTED'
    assert result['reason'] == 'SOURCE_CHANGED_DURING_REPLAY'


def test_cli_can_write_archive_and_keeps_stdout_contract(tmp_path, capsys):
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(archive()))
    assert main(['--input', str(path), '--output-dir', str(tmp_path / 'run')]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'RESEARCH_SIGNALS_ONLY'
    assert (tmp_path / 'run' / 'manifest.json').is_file()
    assert main(['--verify-dir', str(tmp_path / 'run')]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'VERIFIED_LOCAL_REPLAY'


@pytest.mark.parametrize('name', ['input.json', 'report.json', 'manifest.json',
    'source/services/research/gold_signal.py'])
def test_verifier_rejects_changed_archive(tmp_path, name):
    root = tmp_path / 'run'
    archive_replay(json.dumps(archive()).encode(), root)
    (root / name).write_bytes(b'{}')
    with pytest.raises(ValueError):
        verify_archive(root)


def test_verifier_recomputes_instead_of_trusting_rehashed_report(tmp_path):
    root = tmp_path / 'run'
    archive_replay(json.dumps(archive()).encode(), root)
    report = json.loads((root / 'report.json').read_bytes())
    report['records'] = []
    encoded = json.dumps(report).encode()
    (root / 'report.json').write_bytes(encoded)
    manifest = json.loads((root / 'manifest.json').read_bytes())
    manifest['report_sha256'] = hashlib.sha256(encoded).hexdigest()
    (root / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify_archive(root)


def test_interruption_leaves_an_incomplete_attempt_not_verified_success(tmp_path, monkeypatch):
    from tools import gold_signal_replay as module
    def interrupt(_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(module, 'replay', interrupt)
    root = tmp_path / 'run'
    with pytest.raises(KeyboardInterrupt):
        archive_replay(json.dumps(archive()).encode(), root)
    assert (root / 'input.json').exists() and (root / 'trials.db').exists()
    assert not (root / 'manifest.json').exists()
    with pytest.raises((ValueError, OSError)):
        verify_archive(root)
