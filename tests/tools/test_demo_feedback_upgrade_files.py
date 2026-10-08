"""Native temporary files; only host root identity/fixed destinations are synthetic."""
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.tools.test_demo_feedback_upgrade import materials


def configured(tmp_path, monkeypatch, *, existing=True):
    tx = importlib.import_module('tools.demo_feedback_upgrade_files')
    units = tmp_path / 'units'
    config = tmp_path / 'config'
    stage_parent = tmp_path / 'transactions'
    for path in (units, config, stage_parent):
        path.mkdir(mode=0o700)
    targets = {name: str((units if name.endswith(('.service', '.timer')) else config) / name)
               for name in tx.upgrade.TARGETS}
    monkeypatch.setattr(tx, 'TARGETS', targets)
    monkeypatch.setattr(tx, '_root', lambda: None)
    monkeypatch.setattr(tx, 'OWNER_UID', os.getuid())
    monkeypatch.setattr(tx, 'OWNER_GID', os.getgid())
    native_check = tx._protected
    host_ancestors = {(p.stat().st_dev, p.stat().st_ino) for p in tmp_path.parents}

    def synthetic_custody(info, *, directory, maximum=tx.MAX_INTENT):
        assert info.st_uid in {0, os.getuid()}
        view = SimpleNamespace(**{key: getattr(info, key) for key in
                               ('st_mode', 'st_nlink', 'st_size')}, st_uid=os.getuid())
        # The output parent is host mode 0775; synthesize only existing host
        # ancestors as protected. Every fixture directory retains real modes.
        if directory and (info.st_dev, info.st_ino) in host_ancestors:
            view.st_mode &= ~0o022
        native_check(view, directory=directory, maximum=maximum)
    monkeypatch.setattr(tx, '_protected', synthetic_custody)
    before = materials('b' * 40) if existing else dict.fromkeys(targets)
    if existing:
        for name, raw in before.items():
            Path(targets[name]).write_bytes(raw)
            Path(targets[name]).chmod(0o640 if name.endswith('.json') else 0o644)
    return SimpleNamespace(tx=tx, targets=targets, before=before, files=materials(),
                           stage=stage_parent / 'operation', operation='c' * 64)


def prepare(f):
    f.captured = f.tx.capture()
    f.identity = f.tx.prepare(f.stage, operation_sha256=f.operation,
                             source_commit='a' * 40, files=f.files, captured=f.captured)
    return f


def contents(f):
    return {name: Path(path).read_bytes() if Path(path).exists() else None
            for name, path in f.targets.items()}


@pytest.mark.parametrize('existing', [False, True])
def test_native_intent_apply_rollback_preserves_bytes_metadata_and_never_claims_authority(
        tmp_path, monkeypatch, existing):
    f = configured(tmp_path, monkeypatch, existing=existing)
    before_modes = {name: Path(p).stat().st_mode for name, p in f.targets.items()} if existing else {}
    prepare(f)
    json.dumps(f.captured)
    assert contents(f) == f.before
    assert f.tx.inspect(f.stage, f.identity)['phase'] == 'PREPARED'
    assert f.tx.prepare(f.stage, operation_sha256=f.operation, source_commit='a' * 40,
                        files=f.files, captured=f.captured) == f.identity
    calls = []
    result = f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: calls.append('guard'))
    assert result['phase'] == 'APPLIED' and not result['activation_authorized']
    assert contents(f) == f.files and len(calls) >= 5
    assert f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None) == result
    restored = f.tx.advance(f.stage, f.identity, action='rollback', guard=lambda: None)
    assert restored['phase'] == 'RESTORED' and contents(f) == f.before
    if existing:
        assert {name: Path(p).stat().st_mode for name, p in f.targets.items()} == before_modes
    assert f.tx.advance(f.stage, f.identity, action='rollback', guard=lambda: None) == restored
    with pytest.raises(ValueError, match='ROLLBACK'):
        f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)


@pytest.mark.parametrize('when', ['before_prepare', 'before_apply'])
@pytest.mark.parametrize('damage', ['same_bytes_inode', 'mode', 'parent', 'content', 'dropin'])
def test_metadata_cas_rejects_drift_before_any_target_write(tmp_path, monkeypatch, when, damage):
    f = configured(tmp_path, monkeypatch)
    f.captured = f.tx.capture()
    if when == 'before_apply':
        f.identity = f.tx.prepare(f.stage, operation_sha256=f.operation,
                                 source_commit='a' * 40, files=f.files, captured=f.captured)
    target = Path(next(iter(f.targets.values())))
    if damage == 'same_bytes_inode':
        replacement = target.with_name('replacement')
        replacement.write_bytes(target.read_bytes())
        replacement.chmod(target.stat().st_mode & 0o777)
        replacement.replace(target)
    elif damage == 'mode':
        target.chmod(0o600)
    elif damage == 'parent':
        old = target.parent.with_name('old-parent')
        target.parent.rename(old)
        target.parent.mkdir(mode=0o700)
        for p in old.iterdir():
            (target.parent / p.name).write_bytes(p.read_bytes())
    elif damage == 'content':
        target.write_bytes(b'concurrent-root-write')
    else:
        target.with_name(target.name + '.d').mkdir()
    changed = contents(f)
    with pytest.raises(ValueError):
        if when == 'before_prepare':
            f.tx.prepare(f.stage, operation_sha256=f.operation, source_commit='a' * 40,
                         files=f.files, captured=f.captured)
        else:
            f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)
    assert contents(f) == changed


@pytest.mark.parametrize('damage', ['symlink', 'hardlink', 'fifo', 'writable', 'oversize'])
def test_capture_rejects_unsafe_native_leaf(tmp_path, monkeypatch, damage):
    f = configured(tmp_path, monkeypatch)
    target = Path(next(iter(f.targets.values())))
    if damage in {'symlink', 'fifo'}:
        target.unlink()
        if damage == 'symlink':
            target.symlink_to('absent')
        else:
            os.mkfifo(target)
    elif damage == 'hardlink':
        os.link(target, target.with_name('alias'))
    elif damage == 'writable':
        target.chmod(0o666)
    else:
        target.write_bytes(b'x' * 65537)
    with pytest.raises(ValueError):
        f.tx.capture()


def test_guard_is_required_and_failure_does_not_write_targets(tmp_path, monkeypatch):
    f = prepare(configured(tmp_path, monkeypatch))
    with pytest.raises((ValueError, TypeError)):
        f.tx.advance(f.stage, f.identity, action='apply', guard=None)
    with pytest.raises(RuntimeError, match='not paused'):
        f.tx.advance(f.stage, f.identity, action='apply',
                     guard=lambda: (_ for _ in ()).throw(RuntimeError('not paused')))
    assert contents(f) == f.before


def test_unknown_stage_wrong_identity_and_modified_intent_refuse(tmp_path, monkeypatch):
    f = prepare(configured(tmp_path, monkeypatch))
    with pytest.raises(ValueError):
        f.tx.inspect(f.stage, 'f' * 64)
    (f.stage / 'foreign').write_bytes(b'unknown')
    with pytest.raises(ValueError):
        f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)
    assert contents(f) == f.before
    (f.stage / 'foreign').unlink()
    raw = (f.stage / 'intent.json').read_bytes()
    (f.stage / 'intent.json').write_bytes(raw.replace(b'"source_commit":"a', b'"source_commit":"b', 1))
    with pytest.raises(ValueError):
        f.tx.inspect(f.stage, f.identity)


def test_prepare_refuses_unpublished_or_conflicting_stage(tmp_path, monkeypatch):
    f = configured(tmp_path, monkeypatch)
    f.captured = f.tx.capture()
    f.stage.mkdir(mode=0o700)
    (f.stage / 'incomplete').write_bytes(b'partial')
    with pytest.raises(ValueError):
        f.tx.prepare(f.stage, operation_sha256=f.operation, source_commit='a' * 40,
                     files=f.files, captured=f.captured)
    assert contents(f) == f.before


def crash_child(f, monkeypatch, *, action, point, occurrence=1):
    pid = os.fork()
    if pid == 0:
        native = getattr(f.tx, point)
        count = 0

        def stopped(*args, **kwargs):
            nonlocal count
            result = native(*args, **kwargs)
            count += 1
            if count == occurrence:
                os._exit(73)
            return result
        setattr(f.tx, point, stopped)
        try:
            f.tx.advance(f.stage, f.identity, action=action, guard=lambda: None)
        except BaseException:
            os._exit(74)
        os._exit(75)
    waited, status = os.waitpid(pid, 0)
    assert waited == pid and os.waitstatus_to_exitcode(status) == 73


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('point', ['_ready', '_publish_target', '_complete'])
def test_separate_process_crash_resumes_original_apply(tmp_path, monkeypatch, existing, point):
    f = prepare(configured(tmp_path, monkeypatch, existing=existing))
    crash_child(f, monkeypatch, action='apply', point=point)
    assert f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)['phase'] == 'APPLIED'
    assert contents(f) == f.files


@pytest.mark.parametrize('point', ['_rollback_marker', '_ready', '_publish_target', '_complete'])
def test_separate_process_rollback_is_sticky_and_resumable(tmp_path, monkeypatch, point):
    f = prepare(configured(tmp_path, monkeypatch))
    f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)
    crash_child(f, monkeypatch, action='rollback', point=point)
    with pytest.raises(ValueError, match='ROLLBACK'):
        f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)
    assert f.tx.advance(f.stage, f.identity, action='rollback', guard=lambda: None)['phase'] == 'RESTORED'
    assert contents(f) == f.before


def test_staged_inode_is_bound_before_rename_and_cannot_be_substituted(tmp_path, monkeypatch):
    f = prepare(configured(tmp_path, monkeypatch))
    crash_child(f, monkeypatch, action='apply', point='_ready')
    staged = list(Path(next(iter(f.targets.values()))).parent.glob('.quantos-feedback-*'))
    assert len(staged) == 1
    raw = staged[0].read_bytes()
    mode = staged[0].stat().st_mode & 0o777
    staged[0].unlink()
    staged[0].write_bytes(raw)
    staged[0].chmod(mode)
    with pytest.raises(ValueError):
        f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)
    assert contents(f) == f.before


def test_other_process_lock_is_busy_and_no_targets_change(tmp_path, monkeypatch):
    import fcntl
    f = prepare(configured(tmp_path, monkeypatch))
    fd = os.open(f.stage, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    pid = os.fork()
    if pid == 0:
        os.close(fd)
        try:
            f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)
        except ValueError as exc:
            os._exit(0 if 'BUSY' in str(exc) else 1)
        os._exit(2)
    _, status = os.waitpid(pid, 0)
    os.close(fd)
    assert os.waitstatus_to_exitcode(status) == 0
    assert contents(f) == f.before


@pytest.mark.parametrize('occurrence', [1, 2, 3, 4])
@pytest.mark.parametrize('action', ['apply', 'rollback'])
def test_each_target_publish_survives_real_process_exit(tmp_path, monkeypatch, occurrence, action):
    f = prepare(configured(tmp_path, monkeypatch))
    if action == 'rollback':
        f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)
    crash_child(f, monkeypatch, action=action, point='_publish_target', occurrence=occurrence)
    f.tx.advance(f.stage, f.identity, action=action, guard=lambda: None)
    assert contents(f) == (f.files if action == 'apply' else f.before)


def test_rename_before_completion_record_is_recovered_without_second_inode(tmp_path, monkeypatch):
    f = prepare(configured(tmp_path, monkeypatch))
    pid = os.fork()
    if pid == 0:
        native = f.tx.os.replace

        def exit_after_rename(*args, **kwargs):
            native(*args, **kwargs)
            os._exit(73)
        f.tx.os.replace = exit_after_rename
        f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)
        os._exit(74)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 73
    first = Path(next(iter(f.targets.values())))
    inode = first.stat().st_ino
    assert first.read_bytes() == f.files[first.name]
    assert f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)['phase'] == 'APPLIED'
    assert first.stat().st_ino == inode


def test_guard_change_between_staging_and_target_rename_is_rejected(tmp_path, monkeypatch):
    f = prepare(configured(tmp_path, monkeypatch))
    target = Path(next(iter(f.targets.values())))
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls == 3:
            target.chmod(0o600)
    with pytest.raises(ValueError, match='CAS_CHANGED'):
        f.tx.advance(f.stage, f.identity, action='apply', guard=guard)
    assert contents(f) == f.before


def test_partial_staging_is_unknown_and_never_overwritten(tmp_path, monkeypatch):
    f = prepare(configured(tmp_path, monkeypatch))
    first = next(iter(f.targets))
    unexpected = Path(f.targets[first]).with_name(f.tx._temp(f.identity, 'apply', first))
    unexpected.write_bytes(b'partial crash')
    unexpected.chmod(0o600)
    with pytest.raises(ValueError, match='UNBOUND_STAGING'):
        f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)
    assert contents(f) == f.before and unexpected.read_bytes() == b'partial crash'


def test_root_guard_precedes_any_filesystem_access(monkeypatch):
    tx = importlib.import_module('tools.demo_feedback_upgrade_files')
    monkeypatch.setattr(tx.os, 'getresuid', lambda: (1000, 1000, 1000))
    monkeypatch.setattr(tx, '_directory', lambda path: pytest.fail('read before root guard'))
    with pytest.raises(ValueError, match='ROOT_REQUIRED'):
        tx.capture()


def test_malformed_or_widened_capture_cannot_choose_another_target(tmp_path, monkeypatch):
    from copy import deepcopy
    f = configured(tmp_path, monkeypatch)
    capture = f.tx.capture()
    for damage in ('path', 'body', 'extra'):
        bad = deepcopy(capture)
        name = next(iter(f.targets))
        if damage == 'path':
            bad['targets'][name]['path'] = str(tmp_path / 'issuer.sqlite')
        elif damage == 'body':
            bad['targets'][name]['body_base64'] = 'not-base64'
        else:
            bad['targets']['issuer.sqlite'] = bad['targets'][name]
        with pytest.raises(ValueError):
            f.tx.prepare(f.stage, operation_sha256=f.operation, source_commit='a' * 40,
                         files=f.files, captured=bad)
    assert contents(f) == f.before and not f.stage.exists()


def test_first_publication_crash_never_leaves_two_link_target(tmp_path, monkeypatch):
    from tools import demo_retention_transaction as storage
    f = prepare(configured(tmp_path, monkeypatch, existing=False))
    pid = os.fork()
    if pid == 0:
        native_link = f.tx.os.link
        native_move = storage.move

        def exit_after_link(*args, **kwargs):
            native_link(*args, **kwargs)
            os._exit(73)

        def exit_after_move(*args, **kwargs):
            native_move(*args, **kwargs)
            os._exit(73)
        f.tx.os.link = exit_after_link
        storage.move = exit_after_move
        f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)
        os._exit(74)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 73
    assert f.tx.advance(f.stage, f.identity, action='apply', guard=lambda: None)['phase'] == 'APPLIED'
    assert all(Path(path).stat().st_nlink == 1 for path in f.targets.values())
