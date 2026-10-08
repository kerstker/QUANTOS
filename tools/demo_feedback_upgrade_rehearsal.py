"""Persistent, isolated rehearsal of four-file feedback switching and rollback.

Always creates a new private fixture tree. Never adopts live destinations,
operates systemd, authorizes deployment, or replaces trading state.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_feedback_upgrade as upgrade
from tools import demo_retention_transaction as storage


def _hash(body):
    return hashlib.sha256(body).hexdigest()


def _root(path):
    path = storage.isolated(path)
    storage.require(not any(path == Path(p) or Path(p) in path.parents
                            for p in ('/etc', '/usr', '/var', '/srv', '/opt', '/proc', '/sys', '/dev')),
                    'UPGRADE_REHEARSAL_ISOLATED_ONLY')
    return path


def _optional(path):
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    return storage.read(path, upgrade.MAXIMUM)


def prepare(path, *, files, preimages, release_commit):
    path = _root(path)
    review = upgrade.review(files=files, preimages=preimages, release_commit=release_commit)
    path.mkdir(mode=0o700, exist_ok=False)
    for name in ('before', 'after', 'targets'):
        (path / name).mkdir(mode=0o700)
    for name, body in files.items():
        storage.write_new(path / 'after' / name, body)
        if preimages[name] is not None:
            storage.write_new(path / 'before' / name, preimages[name])
            storage.write_new(path / 'targets' / name, preimages[name])
    body = json.dumps(review, sort_keys=True, separators=(',', ':')).encode()
    storage.write_new(path / 'lock', b'')
    # Published last. An incomplete preparation cannot be used for switching.
    storage.write_new(path / 'review.json', body)
    return _hash(body)


def _load(path, identity):
    for directory in (path, path / 'before', path / 'after', path / 'targets'):
        storage.directory(directory)
    body = storage.read(path / 'review.json', 65536)
    storage.require(_hash(body) == identity, 'UPGRADE_REHEARSAL_IDENTITY')
    review = decode(body)
    before = {name: _optional(path / 'before' / name) for name in upgrade.TARGETS}
    after = {name: storage.read(path / 'after' / name, upgrade.MAXIMUM) for name in upgrade.TARGETS}
    upgrade.recheck(review, files=after, preimages=before)
    return before, after


def _switch(path, desired, direction):
    if _optional(path) == desired:
        return
    if desired is None:
        path.unlink()
        storage.sync(path.parent)
        return
    temporary = path.parent / ('.next-' + direction + '-' + path.name)
    if temporary.exists() or temporary.is_symlink():
        storage.require(storage.read(temporary, upgrade.MAXIMUM) == desired,
                        'UPGRADE_REHEARSAL_STAGING_CHANGED')
    else:
        storage.write_new(temporary, desired)
    os.replace(temporary, path)
    storage.sync(path.parent)


def advance(path, identity, *, action):
    path = _root(path)
    storage.directory(path)
    storage.require(action in ('apply', 'restore'), 'UPGRADE_REHEARSAL_ACTION')
    storage.require(storage.read(path / 'lock', 1) == b'', 'UPGRADE_REHEARSAL_LOCK')
    with os.fdopen(os.open(path / 'lock', os.O_RDONLY | os.O_NOFOLLOW), 'rb') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before, after = _load(path, identity)
        marker = path / 'restore-request.json'
        restoring = _optional(marker)
        if restoring is not None:
            storage.require(restoring == identity.encode(), 'UPGRADE_REHEARSAL_RESTORE_BINDING')
            storage.require(action == 'restore', 'UPGRADE_REHEARSAL_RESTORE_REQUESTED')
        # Check all targets before changing any one of them.
        observed = {name: _optional(path / 'targets' / name) for name in upgrade.TARGETS}
        storage.require(all(observed[name] in (before[name], after[name]) for name in observed),
                        'UPGRADE_REHEARSAL_TARGET_CHANGED')
        if action == 'restore' and restoring is None:
            storage.write_new(marker, identity.encode())
        desired = after if action == 'apply' else before
        for name in upgrade.TARGETS:
            target = path / 'targets' / name
            storage.require(_optional(target) == observed[name], 'UPGRADE_REHEARSAL_TARGET_CHANGED')
            _switch(target, desired[name], action)
        storage.require(all(_optional(path / 'targets' / name) == desired[name] for name in desired),
                        'UPGRADE_REHEARSAL_INCOMPLETE')
        return {'status': 'APPLIED_ISOLATED' if action == 'apply' else 'RESTORED_ISOLATED',
                'review_sha256': identity, 'production_changes': False, 'activation_authorized': False}
