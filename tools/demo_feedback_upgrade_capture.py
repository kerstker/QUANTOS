"""Read four fixed feedback deployment preimages under protected custody.

No credentials, installer, service action or CLI. Two observations detect
changes; they do not establish an atomic snapshot or a maintenance window.
"""
import os
from pathlib import Path

from live.execution.demo_authority import _require_protected as _protected
from tools.demo_feedback_upgrade import MAXIMUM, TARGETS, _require, recheck


def _identity(info):
    return tuple(getattr(info, key) for key in (
        'st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns',
        'st_mode', 'st_uid', 'st_gid', 'st_nlink',
    ))


def _parent(path):
    path = Path(path)
    _require(path.is_absolute() and '..' not in path.parts, 'FEEDBACK_CAPTURE_PATH')
    current = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        _protected(os.fstat(current), directory=True)
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=current)
            os.close(current)
            current = child
            _protected(os.fstat(current), directory=True)
        return current
    except BaseException:
        os.close(current)
        raise


def _read_target(path):
    path = Path(path)
    parent = leaf = None
    try:
        parent = _parent(path)
        parent_before = _identity(os.fstat(parent))
        try:
            leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                           dir_fd=parent)
        except FileNotFoundError:
            # Missing ancestors/permission errors are never interpreted as absence.
            try:
                os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                _require(_identity(os.fstat(parent)) == parent_before, 'FEEDBACK_CAPTURE_CHANGED')
                return None, parent_before
            raise ValueError('FEEDBACK_CAPTURE_CHANGED')
        before = os.fstat(leaf)
        _protected(before, directory=False)
        chunks = []
        size = 0
        while size <= MAXIMUM:
            body = os.read(leaf, min(8192, MAXIMUM + 1 - size))
            if not body:
                break
            chunks.append(body)
            size += len(body)
        after = os.fstat(leaf)
        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        _protected(after, directory=False)
        _protected(named, directory=False)
        _require(size == before.st_size and _identity(before) == _identity(after) == _identity(named)
                 and _identity(os.fstat(parent)) == parent_before, 'FEEDBACK_CAPTURE_CHANGED')
        return b''.join(chunks), (parent_before, _identity(after))
    except OSError:
        raise ValueError('FEEDBACK_CAPTURE_UNAVAILABLE') from None
    finally:
        if leaf is not None:
            os.close(leaf)
        if parent is not None:
            os.close(parent)


def capture():
    _require(os.geteuid() == 0, 'FEEDBACK_CAPTURE_ROOT_REQUIRED')
    first = {name: _read_target(path) for name, path in TARGETS.items()}
    second = {name: _read_target(path) for name, path in TARGETS.items()}
    _require(first == second, 'FEEDBACK_CAPTURE_CHANGED')
    return {'preimages': {name: row[0] for name, row in first.items()},
            'atomic_snapshot': False, 'activation_authorized': False}


def check_review(value, *, files):
    observed = capture()
    verified = recheck(value, files=files, preimages=observed['preimages'])
    return {'schema': 'DEMO_FEEDBACK_UPGRADE_CAPTURE_V1',
            'release_commit': verified['release_commit'], 'targets': verified['targets'],
            'preimages_verified': True, 'atomic_snapshot': False,
            'activation_authorized': False, 'installed': False}
