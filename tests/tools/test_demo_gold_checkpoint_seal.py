"""Gold sealing consumes a finished capture, without original role access."""
import os
from pathlib import Path
import shutil

import pytest

from tools import demo_namespace_checkpoint as checkpoint
from tools.live_backup import BackupPassphraseTooWeak
from tests.tools import test_demo_gold_namespace_checkpoint as native

gold_namespace = native.gold_namespace
private_files = native.private_files
source_attachment = native.source_attachment
SECRET = 'isolated-gold-seal-process-test-passphrase'


def captured(f, monkeypatch, tmp_path):
    with native.capture(f, monkeypatch) as inventory:
        return checkpoint.create_gold_namespace_checkpoint(inventory, tmp_path / 'capture',
            passphrase=None, capture_only=True)


@pytest.mark.parametrize('multipart', [False, True])
def test_seal_and_restore_need_only_exact_completed_capture(gold_namespace, monkeypatch, tmp_path, multipart):
    if multipart:
        from tools import demo_gold_checkpoint_transport as transport
        monkeypatch.setattr(transport, 'MAX_PART_MEMBERS', 2)
    f = gold_namespace; saved = captured(f, monkeypatch, tmp_path)
    before = native.hashes(tmp_path / 'capture')
    state = native.hashes(f.root)
    shutil.rmtree(f.root)
    shutil.rmtree(f.attachment['source_root']); Path(f.attachment['bundle_path']).unlink()
    sealed = checkpoint.seal_gold_namespace_checkpoint(tmp_path / 'capture', tmp_path / 'sealed',
        passphrase=SECRET, expected_sha256=saved['sha256'], expected_uid=os.geteuid())
    assert native.hashes(tmp_path / 'capture') == before
    assert sealed['capture_sha256'] == saved['sha256'] and sealed['encrypted'] is True
    assert sealed['production_resumable'] is sealed['submission_enabled'] is False
    assert sealed['source_uid'] == os.geteuid()
    shutil.rmtree(tmp_path / 'capture')
    restored = checkpoint.restore_gold_namespace_checkpoint(tmp_path / 'sealed', tmp_path / 'restored',
        passphrase=SECRET, expected_sha256=sealed['sha256'])
    assert restored['state'] == saved['state']
    assert native.hashes(tmp_path / 'restored/state') == state


@pytest.mark.parametrize('damage', ['wrong_uid', 'changed_part', 'unlisted_file', 'weak_secret'])
def test_seal_rejects_invalid_capture_without_publishing(gold_namespace, monkeypatch, tmp_path, damage):
    f = gold_namespace; saved = captured(f, monkeypatch, tmp_path)
    uid = os.geteuid(); secret = SECRET
    if damage == 'wrong_uid':
        uid += 1000
    elif damage == 'changed_part':
        part = next((tmp_path / 'capture').glob('part-*.zip'))
        raw = bytearray(part.read_bytes()); raw[0] ^= 1; part.write_bytes(raw)
    elif damage == 'unlisted_file':
        (tmp_path / 'capture/extra').write_bytes(b'unlisted')
    else:
        secret = 'weak'
    before = native.hashes(tmp_path / 'capture')
    with pytest.raises(BackupPassphraseTooWeak if damage == 'weak_secret' else ValueError):
        checkpoint.seal_gold_namespace_checkpoint(tmp_path / 'capture', tmp_path / 'sealed',
            passphrase=secret, expected_sha256=saved['sha256'], expected_uid=uid)
    assert native.hashes(tmp_path / 'capture') == before
    assert not (tmp_path / 'sealed').exists()


def test_source_capture_change_during_encryption_cannot_publish(gold_namespace, monkeypatch, tmp_path):
    saved = captured(gold_namespace, monkeypatch, tmp_path)
    original = checkpoint.encrypt_file; changed = []
    def encrypt(*args, **kwargs):
        result = original(*args, **kwargs)
        if not changed:
            part = next((tmp_path / 'capture').glob('part-*.zip'))
            raw = part.read_bytes(); part.unlink(); part.write_bytes(raw)
            changed.append(True)
        return result
    monkeypatch.setattr(checkpoint, 'encrypt_file', encrypt)
    with pytest.raises(ValueError):
        checkpoint.seal_gold_namespace_checkpoint(tmp_path / 'capture', tmp_path / 'sealed',
            passphrase=SECRET, expected_sha256=saved['sha256'], expected_uid=os.geteuid())
    assert changed and not (tmp_path / 'sealed').exists()


def test_interrupted_encryption_preserves_capture_and_cleans_private_staging(gold_namespace, monkeypatch, tmp_path):
    saved = captured(gold_namespace, monkeypatch, tmp_path)
    before = native.hashes(tmp_path / 'capture')
    def interrupted(source, destination, passphrase):
        Path(destination).write_bytes(b'incomplete encrypted output')
        raise OSError('isolated encryption interruption')
    monkeypatch.setattr(checkpoint, 'encrypt_file', interrupted)
    with pytest.raises(OSError, match='isolated encryption interruption'):
        checkpoint.seal_gold_namespace_checkpoint(tmp_path / 'capture', tmp_path / 'sealed',
            passphrase=SECRET, expected_sha256=saved['sha256'], expected_uid=os.geteuid())
    assert native.hashes(tmp_path / 'capture') == before
    assert not (tmp_path / 'sealed').exists()
    assert not list(tmp_path.glob('.gold-seal-*'))


def test_last_output_sync_cannot_hide_capture_inode_replacement(gold_namespace, monkeypatch, tmp_path):
    from tools import demo_gold_checkpoint_transport as transport
    saved = captured(gold_namespace, monkeypatch, tmp_path)
    original = transport.sync; changed = []
    def sync(path):
        result = original(path)
        if Path(path).name == 'sealed' and not changed:
            part = next((tmp_path / 'capture').glob('part-*.zip'))
            inode = part.stat().st_ino
            replacement = part.with_name('replacement.tmp')
            replacement.write_bytes(part.read_bytes()); os.replace(replacement, part)
            assert part.stat().st_ino != inode
            changed.append(True)
        return result
    monkeypatch.setattr(transport, 'sync', sync)
    with pytest.raises(ValueError, match='GOLD_TRANSPORT_CAPTURE_CHANGED'):
        checkpoint.seal_gold_namespace_checkpoint(tmp_path / 'capture', tmp_path / 'sealed',
            passphrase=SECRET, expected_sha256=saved['sha256'], expected_uid=os.geteuid())
    assert changed and not (tmp_path / 'sealed').exists()
