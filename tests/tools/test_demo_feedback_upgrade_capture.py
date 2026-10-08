"""Protected upgrade capture rejects unknown custody and changing preimages."""
import os
from types import SimpleNamespace

import pytest

from live.execution import demo_authority as custody
from tools import demo_feedback_upgrade_capture as capture
from tools import demo_feedback_upgrade as upgrade
from tests.tools.test_demo_feedback_upgrade import materials


def isolated_reader(tmp_path, monkeypatch):
    # Only parent traversal and owner UID are simulated. Real descriptors,
    # no-follow leaf opens, file types, modes, links and race checks remain.
    monkeypatch.setattr(capture, '_parent', lambda path: os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY))
    original = custody._require_protected
    def check(info, *, directory):
        view = SimpleNamespace(**{key: getattr(info, key) for key in
            ('st_mode', 'st_nlink', 'st_size')}, st_uid=0)
        original(view, directory=directory)
    monkeypatch.setattr(capture, '_protected', check)


def test_absent_leaf_is_distinct_from_unknown_parent(tmp_path, monkeypatch):
    isolated_reader(tmp_path, monkeypatch)
    assert capture._read_target(tmp_path / 'absent')[0] is None
    monkeypatch.setattr(capture, '_parent', lambda path: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(ValueError, match='FEEDBACK_CAPTURE_UNAVAILABLE'):
        capture._read_target(tmp_path / 'absent')


@pytest.mark.parametrize('damage', ['symlink', 'fifo', 'writable', 'hardlink', 'oversize'])
def test_untrusted_leaf_cannot_be_a_review_preimage(tmp_path, monkeypatch, damage):
    isolated_reader(tmp_path, monkeypatch)
    path = tmp_path / 'target'
    if damage == 'symlink':
        path.symlink_to(tmp_path / 'absent')
    elif damage == 'fifo':
        os.mkfifo(path)
    else:
        path.write_bytes(b'x' * (65537 if damage == 'oversize' else 10))
        path.chmod(0o666 if damage == 'writable' else 0o600)
        if damage == 'hardlink':
            os.link(path, tmp_path / 'alias')
    with pytest.raises(ValueError):
        capture._read_target(path)


def test_replaced_leaf_is_rejected_even_when_open_descriptor_is_unchanged(tmp_path, monkeypatch):
    isolated_reader(tmp_path, monkeypatch)
    path = tmp_path / 'target'
    path.write_bytes(b'original')
    replacement = tmp_path / 'replacement'
    replacement.write_bytes(b'original')
    original = os.read
    replaced = False
    def read(fd, size):
        nonlocal replaced
        body = original(fd, size)
        if not replaced:
            replaced = True
            os.replace(replacement, path)
        return body
    monkeypatch.setattr(capture.os, 'read', read)
    with pytest.raises(ValueError):
        capture._read_target(path)


def test_capture_root_guard_runs_before_opening_any_target(monkeypatch):
    monkeypatch.setattr(capture.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(capture, '_read_target', lambda path: pytest.fail('read before root guard'))
    with pytest.raises(ValueError, match='FEEDBACK_CAPTURE_ROOT_REQUIRED'):
        capture.capture()


def test_double_capture_rejects_changed_metadata_and_is_not_atomic(monkeypatch):
    monkeypatch.setattr(capture.os, 'geteuid', lambda: 0)
    counts = {}
    def changed(path):
        counts[path] = counts.get(path, 0) + 1
        return b'bytes', ('inode', counts[path])
    monkeypatch.setattr(capture, '_read_target', changed)
    with pytest.raises(ValueError, match='FEEDBACK_CAPTURE_CHANGED'):
        capture.capture()
    monkeypatch.setattr(capture, '_read_target', lambda path: (None, ('parent', 1)))
    result = capture.capture()
    assert result['atomic_snapshot'] is False and result['activation_authorized'] is False
    assert result['preimages'] == dict.fromkeys(upgrade.TARGETS)


def test_fresh_capture_must_match_review_and_cannot_upgrade_authority(monkeypatch):
    files = materials()
    before = dict.fromkeys(files)
    review = upgrade.review(files=files, preimages=before, release_commit='a' * 40)
    monkeypatch.setattr(capture.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(capture, '_read_target', lambda path: (None, ('parent', 1)))
    result = capture.check_review(review, files=files)
    assert result['preimages_verified'] and not result['activation_authorized']
    assert not result['installed'] and not result['atomic_snapshot']
    monkeypatch.setattr(capture, '_read_target', lambda path: (b'changed', ('inode', 1)))
    with pytest.raises(ValueError, match='FEEDBACK_UPGRADE_PREIMAGE_CHANGED'):
        capture.check_review(review, files=files)
