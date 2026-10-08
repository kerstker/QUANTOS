"""Crash recovery uses persistent original bytes, never a live target tree."""
from pathlib import Path

import pytest

from tools import demo_feedback_upgrade_rehearsal as rehearsal
from tests.tools.test_demo_feedback_upgrade import materials


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('cut', [0, 1, 2, 3])
def test_interrupted_apply_can_resume_or_restore_exact_originals(tmp_path, monkeypatch, existing, cut):
    files = materials()
    before = materials('b' * 40) if existing else dict.fromkeys(files)
    for action in ('apply', 'restore'):
        root = tmp_path / action
        identity = rehearsal.prepare(root, files=files, preimages=before, release_commit='a' * 40)
        snapshots = {p.relative_to(root): p.read_bytes() for area in ('before', 'after')
                     for p in (root / area).iterdir()}
        original = rehearsal._switch
        calls = 0
        def crash(*args):
            nonlocal calls
            if calls == cut:
                raise OSError('injected process interruption')
            calls += 1
            original(*args)
        with monkeypatch.context() as patch:
            patch.setattr(rehearsal, '_switch', crash)
            with pytest.raises(OSError):
                rehearsal.advance(root, identity, action='apply')
        result = rehearsal.advance(root, identity, action=action)
        assert result['status'] == ('APPLIED_ISOLATED' if action == 'apply' else 'RESTORED_ISOLATED')
        assert not result['production_changes'] and not result['activation_authorized']
        expected = files if action == 'apply' else before
        for name, body in expected.items():
            path = root / 'targets' / name
            assert path.read_bytes() == body if body is not None else not path.exists()
        assert all((root / name).read_bytes() == body for name, body in snapshots.items())
        assert rehearsal.advance(root, identity, action=action) == result


def test_changed_target_aborts_before_any_switch(tmp_path, monkeypatch):
    files = materials()
    root = tmp_path / 'run'
    identity = rehearsal.prepare(root, files=files, preimages=dict.fromkeys(files), release_commit='a' * 40)
    (root / 'targets' / 'research-feedback.json').write_bytes(b'unrelated edit')
    (root / 'targets' / 'research-feedback.json').chmod(0o600)
    monkeypatch.setattr(rehearsal, '_switch', lambda *args: pytest.fail('partial mutation'))
    with pytest.raises(ValueError, match='UPGRADE_REHEARSAL_TARGET_CHANGED'):
        rehearsal.advance(root, identity, action='apply')


def test_restore_is_sticky_and_old_materials_cannot_be_modified(tmp_path):
    files = materials()
    root = tmp_path / 'run'
    identity = rehearsal.prepare(root, files=files, preimages=materials('b' * 40), release_commit='a' * 40)
    rehearsal.advance(root, identity, action='apply')
    rehearsal.advance(root, identity, action='restore')
    with pytest.raises(ValueError, match='UPGRADE_REHEARSAL_RESTORE_REQUESTED'):
        rehearsal.advance(root, identity, action='apply')
    (root / 'before' / 'research-feedback.json').write_bytes(b'modified original')
    with pytest.raises(ValueError):
        rehearsal.advance(root, identity, action='restore')


def test_no_live_target_or_existing_directory_is_adopted(tmp_path):
    files = materials()
    with pytest.raises(ValueError):
        rehearsal.prepare(Path('/etc/quantos'), files=files, preimages=dict.fromkeys(files), release_commit='a' * 40)
    with pytest.raises(FileExistsError):
        rehearsal.prepare(tmp_path, files=files, preimages=dict.fromkeys(files), release_commit='a' * 40)


def test_actual_process_exit_leaves_recoverable_durable_preimages(tmp_path):
    import json
    import os
    import subprocess
    import sys
    files = materials()
    before = materials('b' * 40)
    root = tmp_path / 'run'
    identity = rehearsal.prepare(root, files=files, preimages=before, release_commit='a' * 40)
    script = '''import os,sys
from tools import demo_feedback_upgrade_rehearsal as r
original=r._switch
count=0
def crash(*args):
    global count
    if count==2: os._exit(87)
    original(*args)
    count+=1
r._switch=crash
r.advance(sys.argv[1],sys.argv[2],action='apply')
'''
    environment = {'PATH': os.defpath, 'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    process = subprocess.run([sys.executable, '-c', script, str(root), identity],
                             env=environment, capture_output=True, timeout=30)
    assert process.returncode == 87, process.stderr
    recovery = subprocess.run([sys.executable, '-c',
        "import json,sys; from tools import demo_feedback_upgrade_rehearsal as r; "
        "print(json.dumps(r.advance(sys.argv[1],sys.argv[2],action='restore')))", str(root), identity],
        env=environment, capture_output=True, timeout=30)
    assert recovery.returncode == 0, recovery.stderr
    assert json.loads(recovery.stdout)['status'] == 'RESTORED_ISOLATED'
    assert all((root / 'targets' / name).read_bytes() == body for name, body in before.items())


@pytest.mark.parametrize('cut', [0, 1, 2, 3])
def test_restore_interruption_cannot_resume_forward_switch(tmp_path, monkeypatch, cut):
    files = materials()
    before = dict.fromkeys(files)
    root = tmp_path / 'run'
    identity = rehearsal.prepare(root, files=files, preimages=before, release_commit='a' * 40)
    rehearsal.advance(root, identity, action='apply')
    original = rehearsal._switch
    count = 0
    def crash(*args):
        nonlocal count
        if count == cut:
            raise OSError('interrupted rollback')
        original(*args)
        count += 1
    with monkeypatch.context() as patch:
        patch.setattr(rehearsal, '_switch', crash)
        with pytest.raises(OSError):
            rehearsal.advance(root, identity, action='restore')
    with pytest.raises(ValueError, match='UPGRADE_REHEARSAL_RESTORE_REQUESTED'):
        rehearsal.advance(root, identity, action='apply')
    rehearsal.advance(root, identity, action='restore')
    assert not any((root / 'targets' / name).exists() for name in files)


def test_pending_write_from_crash_is_reused_but_never_overwritten(tmp_path, monkeypatch):
    files = materials()
    root = tmp_path / 'run'
    identity = rehearsal.prepare(root, files=files, preimages=dict.fromkeys(files), release_commit='a' * 40)
    import os
    with monkeypatch.context() as patch:
        patch.setattr(rehearsal.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('before rename')))
        with pytest.raises(OSError):
            rehearsal.advance(root, identity, action='apply')
    pending = list((root / 'targets').glob('.next-apply-*'))
    assert len(pending) == 1
    original_bytes = pending[0].read_bytes()
    pending[0].write_bytes(b'foreign pending bytes')
    with pytest.raises(ValueError, match='UPGRADE_REHEARSAL_STAGING_CHANGED'):
        rehearsal.advance(root, identity, action='apply')
    pending[0].write_bytes(original_bytes)
    rehearsal.advance(root, identity, action='apply')
    assert not os.path.exists(pending[0])
