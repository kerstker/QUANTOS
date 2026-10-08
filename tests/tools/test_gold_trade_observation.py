import hashlib
import json
import pytest

from tests.research.test_gold_trade_observation import document
from tools.gold_trade_observation import main


def test_archive_keeps_native_bytes_normalized_facts_and_private_permissions(tmp_path):
    from tests.research.test_gold_trade_observation import native_document
    from tools.gold_trade_observation import archive_observation, verify_archive
    raw = json.dumps(native_document()).encode()
    root = tmp_path/'archive'
    report = archive_observation(raw, root, input_format='binance-user-trades')
    assert (root/'input.json').read_bytes() == raw
    assert json.loads((root/'fills.json').read_bytes()) == document()
    assert json.loads((root/'report.json').read_bytes()) == report
    assert root.stat().st_mode & 0o777 == 0o700
    for path in root.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    assert verify_archive(root)['status'] == 'VERIFIED_LOCAL_FILL_ARCHIVE'
    assert verify_archive(root)['history_complete'] is False


def test_archive_refuses_overwrite_and_rejects_invalid_input_before_persistence(tmp_path):
    from tools.gold_trade_observation import archive_observation
    root = tmp_path/'archive'
    raw = json.dumps(document()).encode()
    archive_observation(raw, root)
    saved = {p.name:p.read_bytes() for p in root.iterdir()}
    with pytest.raises(FileExistsError):
        archive_observation(raw, root)
    assert saved == {p.name:p.read_bytes() for p in root.iterdir()}
    with pytest.raises(ValueError):
        archive_observation(b'{"secret":"do-not-persist"}', tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


@pytest.mark.parametrize('name', ['input.json', 'fills.json', 'report.json', 'manifest.json'])
def test_archive_rejects_corruption(tmp_path, name):
    from tools.gold_trade_observation import archive_observation, verify_archive
    root = tmp_path/'archive'
    archive_observation(json.dumps(document()).encode(), root)
    (root/name).write_bytes(b'{}')
    with pytest.raises(ValueError):
        verify_archive(root)


def test_archive_recomputes_report_even_if_hash_is_replaced(tmp_path):
    from tools.gold_trade_observation import archive_observation, verify_archive
    root = tmp_path/'archive'
    archive_observation(json.dumps(document()).encode(), root)
    report = json.loads((root/'report.json').read_bytes())
    report['history_complete'] = True
    raw = json.dumps(report).encode()
    (root/'report.json').write_bytes(raw)
    manifest = json.loads((root/'manifest.json').read_bytes())
    manifest['files']['report.json'] = hashlib.sha256(raw).hexdigest()
    (root/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        verify_archive(root)


def test_interruption_cannot_leave_a_verifiable_archive(tmp_path, monkeypatch):
    from tools import gold_trade_observation as module
    root = tmp_path/'archive'
    original = module._write_private
    def interrupted(path, raw):
        if path.name == 'report.json':
            raise KeyboardInterrupt
        original(path, raw)
    monkeypatch.setattr(module, '_write_private', interrupted)
    with pytest.raises(KeyboardInterrupt):
        module.archive_observation(json.dumps(document()).encode(), root)
    assert (root/'input.json').exists()
    assert not (root/'manifest.json').exists()
    with pytest.raises((ValueError, OSError)):
        module.verify_archive(root)


def test_archive_verification_rejects_links_and_source_drift(tmp_path, monkeypatch):
    from tools import gold_trade_observation as module
    root = tmp_path/'archive'
    module.archive_observation(json.dumps(document()).encode(), root)
    link = tmp_path/'linked'
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError):
        module.verify_archive(link)
    original = module._source_hashes()
    monkeypatch.setattr(module, '_source_hashes', lambda: {**original, 'changed': '0'*64})
    with pytest.raises(ValueError):
        module.verify_archive(root)


def test_source_change_while_archiving_leaves_no_manifest(tmp_path, monkeypatch):
    from tools import gold_trade_observation as module
    original = module._source_hashes()
    calls = []
    def changing():
        calls.append(1)
        return original if len(calls) == 1 else {**original, 'changed': '0'*64}
    monkeypatch.setattr(module, '_source_hashes', changing)
    root = tmp_path/'archive'
    with pytest.raises(ValueError, match='SOURCE_CHANGED_DURING_ARCHIVE'):
        module.archive_observation(json.dumps(document()).encode(), root)
    assert not (root/'manifest.json').exists()


def test_archive_rejects_symlinked_file_and_truncated_manifest(tmp_path):
    from tools.gold_trade_observation import archive_observation, verify_archive
    root = tmp_path/'archive'
    archive_observation(json.dumps(document()).encode(), root)
    raw = (root/'input.json').read_bytes()
    external = tmp_path/'external.json'
    external.write_bytes(raw)
    (root/'input.json').unlink()
    (root/'input.json').symlink_to(external)
    with pytest.raises(ValueError, match='INVALID_ARCHIVE_FILE'):
        verify_archive(root)
    (root/'input.json').unlink()
    (root/'input.json').write_bytes(raw)
    (root/'manifest.json').write_bytes(b'{')
    with pytest.raises(ValueError):
        verify_archive(root)


def test_cli_archive_and_verification_preserve_input(tmp_path, capsys):
    path = tmp_path/'input.json'
    raw = json.dumps(document()).encode()
    path.write_bytes(raw)
    root = tmp_path/'archive'
    assert main(['--input', str(path), '--output-dir', str(root)]) == 0
    assert json.loads(capsys.readouterr().out)['unique_fills'] == 1
    assert main(['--verify-dir', str(root)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'VERIFIED_LOCAL_FILL_ARCHIVE'
    assert path.read_bytes() == raw


def test_cli_native_binance_format_binds_original_bytes(tmp_path, capsys):
    from tests.research.test_gold_trade_observation import native_document
    path = tmp_path/'native.json'
    raw = json.dumps(native_document()).encode()
    path.write_bytes(raw)
    assert main(['--input', str(path), '--format', 'binance-user-trades']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['unique_fills'] == 1
    assert report['history_complete'] is False
    assert report['input_sha256'] == hashlib.sha256(raw).hexdigest()
    assert path.read_bytes() == raw


def test_cli_reads_without_modifying_and_binds_exact_input(tmp_path, capsys):
    path = tmp_path/'fills.json'
    raw = json.dumps(document()).encode()
    path.write_bytes(raw)
    assert main(['--input', str(path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['input_sha256'] == hashlib.sha256(raw).hexdigest()
    assert report['unique_fills'] == 1
    assert path.read_bytes() == raw
    assert list(tmp_path.iterdir()) == [path]


def test_cli_missing_or_invalid_input_has_no_private_error_details(tmp_path, capsys):
    path = tmp_path/'private-account-name'
    assert main(['--input', str(path)]) == 2
    assert 'private-account-name' not in capsys.readouterr().out
    path.write_text('{"secret":"private-value"}')
    assert main(['--input', str(path)]) == 2
    output = capsys.readouterr().out
    assert 'private-value' not in output
    assert json.loads(output)['status'] == 'REJECTED'
