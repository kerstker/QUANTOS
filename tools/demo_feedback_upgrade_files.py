"""Fixed feedback files: intent-first custody/CAS and restartable rollback.

Privileged internal API, no CLI or service actions. The coordinator must hold its
maintenance exclusion and supply a live guard before every target mutation.
A review is not installation permission. Only these four configuration targets
are writable; issuer, account, claims and research records are never addressed.
Unknown partial journal/staging files fail closed. Configuration rollback restores
bytes/owner/mode, not historical inode numbers or running manager state.
"""
from contextlib import contextmanager
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_feedback_upgrade as upgrade
from tools import demo_retention_transaction as storage

TARGETS = dict(upgrade.TARGETS)
OWNER_UID = 0
OWNER_GID = 0
MAX_INTENT = 1024 * 1024
DIR_KEYS = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode')
FILE_KEYS = DIR_KEYS + ('st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
STABLE_KEYS = tuple(key for key in FILE_KEYS if key != 'st_ctime_ns')


def _require(ok, reason):
    if not ok:
        raise ValueError('FEEDBACK_FILES_' + reason)


def _root():
    _require(os.getresuid() == (0, 0, 0), 'ROOT_REQUIRED')


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    _require(len(raw) <= MAX_INTENT, 'SIZE')
    return raw


def _digest(value):
    return _hash(_canonical(value))


def _token(value, size=64):
    _require(type(value) is str and re.fullmatch('[a-f0-9]{' + str(size) + '}', value), 'IDENTITY')


def _protected(info, *, directory, maximum=MAX_INTENT):
    _require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
             and info.st_uid == OWNER_UID and not info.st_mode & 0o022, 'CUSTODY')
    if not directory:
        _require(info.st_nlink == 1 and 0 < info.st_size <= maximum, 'FILE')


def _identity(info, keys):
    return {key: getattr(info, key) for key in keys}


def _directory(path):
    path = Path(path)
    _require(path.is_absolute() and '..' not in path.parts, 'PATH')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    chain = []
    try:
        current = Path('/')
        _protected(os.fstat(fd), directory=True)
        chain.append({'path': str(current), 'identity': _identity(os.fstat(fd), DIR_KEYS)})
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd)
            try:
                info = os.fstat(child)
                _protected(info, directory=True)
                _require(_identity(info, DIR_KEYS) ==
                         _identity(os.stat(part, dir_fd=fd, follow_symlinks=False), DIR_KEYS), 'PARENT_CHANGED')
            except BaseException:
                os.close(child)
                raise
            os.close(fd)
            fd = child
            current /= part
            chain.append({'path': str(current), 'identity': _identity(info, DIR_KEYS)})
        return fd, chain
    except BaseException:
        os.close(fd)
        raise


def _read(fd, name, maximum):
    try:
        leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=fd)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(leaf)
        _protected(before, directory=False, maximum=maximum)
        chunks = []
        size = 0
        while size <= maximum:
            raw = os.read(leaf, min(8192, maximum + 1 - size))
            if not raw:
                break
            chunks.append(raw)
            size += len(raw)
        after = os.fstat(leaf)
        named = os.stat(name, dir_fd=fd, follow_symlinks=False)
        _protected(named, directory=False, maximum=maximum)
        _require(size == before.st_size and _identity(before, FILE_KEYS) ==
                 _identity(after, FILE_KEYS) == _identity(named, FILE_KEYS), 'FILE_CHANGED')
        return b''.join(chunks), _identity(after, FILE_KEYS)
    finally:
        os.close(leaf)


def _dropins(fd, name):
    if name.endswith(('.service', '.timer')):
        try:
            os.stat(name + '.d', dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise ValueError('FEEDBACK_FILES_UNKNOWN_DROPIN')


def _observe(name):
    path = Path(TARGETS[name])
    fd, chain = _directory(path.parent)
    try:
        _dropins(fd, path.name)
        observed = _read(fd, path.name, upgrade.MAXIMUM)
        check, current = _directory(path.parent)
        os.close(check)
        _require(chain == current, 'PARENT_CHANGED')
        return {'path': str(path), 'parents': chain,
                'metadata': None if observed is None else observed[1],
                'body_base64': None if observed is None else base64.b64encode(observed[0]).decode()}
    finally:
        os.close(fd)


def capture():
    """Capture full JSON-safe preimages; no atomicity or activation claim."""
    _root()
    try:
        first = {name: _observe(name) for name in TARGETS}
        second = {name: _observe(name) for name in TARGETS}
        _require(first == second, 'CAPTURE_CHANGED')
        return {'schema': 'DEMO_FEEDBACK_FILES_CAPTURE_V1', 'targets': first}
    except OSError:
        raise ValueError('FEEDBACK_FILES_UNAVAILABLE') from None


def _body(value):
    if value is None:
        return None
    _require(type(value) is str, 'BODY')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise ValueError('FEEDBACK_FILES_BODY') from None
    _require(0 < len(raw) <= upgrade.MAXIMUM and base64.b64encode(raw).decode() == value, 'BODY')
    return raw


def _snapshot(value):
    _require(type(value) is dict and set(value) == {'schema', 'targets'}
             and value['schema'] == 'DEMO_FEEDBACK_FILES_CAPTURE_V1'
             and type(value['targets']) is dict and set(value['targets']) == set(TARGETS), 'CAPTURE')
    before = {}
    for name, row in value['targets'].items():
        _require(type(row) is dict and set(row) == {'path', 'parents', 'metadata', 'body_base64'}
                 and row['path'] == TARGETS[name], 'CAPTURE')
        _require(type(row['parents']) is list and row['parents'], 'CAPTURE')
        for parent in row['parents']:
            _require(type(parent) is dict and set(parent) == {'path', 'identity'}
                     and type(parent['path']) is str and type(parent['identity']) is dict
                     and set(parent['identity']) == set(DIR_KEYS)
                     and all(type(n) is int and n >= 0 for n in parent['identity'].values()), 'CAPTURE')
        raw = _body(row['body_base64'])
        meta = row['metadata']
        _require((raw is None and meta is None) or
                 (raw is not None and type(meta) is dict and set(meta) == set(FILE_KEYS)
                  and all(type(n) is int and n >= 0 for n in meta.values())
                  and meta['st_size'] == len(raw)), 'CAPTURE')
        before[name] = raw
    return before


def preimages(captured):
    """Validate and decode an untrusted JSON-safe captured snapshot."""
    return _snapshot(captured)


def _write_new(fd, name, raw):
    _require(_read(fd, name, MAX_INTENT) is None, 'JOURNAL_EXISTS')
    temporary = '.pending-' + name
    leaf = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                   0o600, dir_fd=fd)
    try:
        with os.fdopen(leaf, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(leaf)
    finally:
        os.close(leaf)
    os.rename(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)


def _json(fd, name):
    raw = _read(fd, name, MAX_INTENT)
    return None if raw is None else decode(raw[0])


@contextmanager
def _locked(stage, *, shared=False):
    _root()
    fd, chain = _directory(stage)
    try:
        _require(not os.fstat(fd).st_mode & 0o077, 'STAGE_PRIVATE')
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('FEEDBACK_FILES_BUSY') from None
        yield fd, chain
        current, observed = _directory(stage)
        os.close(current)
        _require(chain == observed, 'STAGE_CHANGED')
    finally:
        os.close(fd)


def _ready_name(action, name):
    return 'ready-' + action + '-' + name + '.json'


def _done_name(action, name):
    return 'done-' + action + '-' + name + '.json'


def _temp(identity, action, name):
    return '.quantos-feedback-' + identity + '-' + action + '-' + name


def _load(fd, chain, identity):
    _token(identity)
    raw = _read(fd, 'intent.json', MAX_INTENT)
    _require(raw is not None and _hash(raw[0]) == identity, 'INTENT')
    intent = decode(raw[0])
    _require(type(intent) is dict and set(intent) ==
             {'schema', 'operation_sha256', 'source_commit', 'captured', 'files', 'review', 'stage_parents'}
             and intent['schema'] == 'DEMO_FEEDBACK_FILES_INTENT_V1'
             and intent['stage_parents'] == chain, 'INTENT')
    _token(intent['operation_sha256'])
    _token(intent['source_commit'], 40)
    before = _snapshot(intent['captured'])
    _require(type(intent['files']) is dict and set(intent['files']) == set(TARGETS), 'INTENT')
    after = {name: _body(raw) for name, raw in intent['files'].items()}
    _require(intent['review'] == upgrade.review(files=after, preimages=before,
                                              release_commit=intent['source_commit']), 'INTENT')
    allowed = {'intent.json', 'rollback.json', 'applied.json', 'restored.json'}
    for name in TARGETS:
        for action in ('apply', 'rollback'):
            allowed.update((_ready_name(action, name), _done_name(action, name)))
    _require(set(os.listdir(fd)) <= allowed, 'UNKNOWN_JOURNAL')
    for marker in ('rollback.json', 'applied.json', 'restored.json'):
        value = _json(fd, marker)
        _require(value is None or value == {'intent_sha256': identity}, 'MARKER')
    _require(_json(fd, 'restored.json') is None or _json(fd, 'rollback.json') is not None, 'MARKER')
    return intent, before, after


def _same_file(row, raw, metadata, *, stable=False):
    return (_body(row['body_base64']) == raw and row['metadata'] is not None
            and all(row['metadata'][key] == metadata[key]
                    for key in (STABLE_KEYS if stable else FILE_KEYS)))


def _records(fd, identity, name, action, desired):
    ready = _json(fd, _ready_name(action, name))
    done = _json(fd, _done_name(action, name))
    if ready is not None:
        _require(type(ready) is dict and set(ready) == {'intent_sha256', 'temporary', 'metadata'}
                 and ready['intent_sha256'] == identity
                 and ready['temporary'] == _temp(identity, action, name)
                 and type(ready['metadata']) is dict and set(ready['metadata']) == set(FILE_KEYS)
                 and all(type(n) is int and n >= 0 for n in ready['metadata'].values())
                 and desired is not None and ready['metadata']['st_size'] == len(desired), 'READY')
    if done is not None:
        _require(type(done) is dict and set(done) == {'intent_sha256', 'metadata'}
                 and done['intent_sha256'] == identity
                 and ((desired is None and done['metadata'] is None) or
                      (ready is not None and type(done['metadata']) is dict
                       and set(done['metadata']) == set(FILE_KEYS)
                       and all(done['metadata'][key] == ready['metadata'][key] for key in STABLE_KEYS))), 'DONE')
    return ready, done


def _states(fd, identity, intent, before, after):
    states = {}
    rollback = _json(fd, 'rollback.json') is not None
    for name in TARGETS:
        row = _observe(name)
        original = intent['captured']['targets'][name]
        _require(row['parents'] == original['parents'], 'PARENT_CHANGED')
        state = 'BEFORE' if row == original else None
        for action, desired, label in (('apply', after[name], 'AFTER'),
                                       ('rollback', before[name], 'RESTORED')):
            ready, done = _records(fd, identity, name, action, desired)
            _require(action == 'apply' or rollback or ready is None and done is None, 'ROLLBACK')
            parent, chain = _directory(Path(TARGETS[name]).parent)
            try:
                _require(chain == original['parents'], 'PARENT_CHANGED')
                temporary = _read(parent, _temp(identity, action, name), upgrade.MAXIMUM)
            finally:
                os.close(parent)
            if ready is None:
                _require(temporary is None, 'UNBOUND_STAGING')
            elif temporary is not None:
                _require(temporary[0] == desired and temporary[1] == ready['metadata'], 'STAGING_CHANGED')
            if ready is not None and _same_file(row, desired,
                                               done['metadata'] if done else ready['metadata'],
                                               stable=done is None):
                _require(temporary is None, 'STAGING_DUPLICATE')
                state = label
            elif action == 'rollback' and rollback and desired is None and row['metadata'] is None:
                state = 'RESTORED'
            if action == 'apply' and done is not None and state == 'BEFORE' and not rollback:
                raise ValueError('FEEDBACK_FILES_TARGET_CHANGED')
            if ready is not None and temporary is None:
                # Apply artifacts may have been consumed and subsequently restored.
                _require(state == label or action == 'apply' and rollback, 'STAGING_MISSING')
        _require(state is not None, 'TARGET_CHANGED')
        states[name] = state
    if _json(fd, 'applied.json') is not None and not rollback:
        _require(set(states.values()) == {'AFTER'}, 'TARGET_CHANGED')
    if _json(fd, 'restored.json') is not None:
        _require(set(states.values()) <= {'BEFORE', 'RESTORED'}, 'TARGET_CHANGED')
    return states


def _receipt(fd, identity, intent, before, after):
    states = _states(fd, identity, intent, before, after)
    rollback = _json(fd, 'rollback.json') is not None
    phase = ('RESTORED' if _json(fd, 'restored.json') is not None else 'ROLLING_BACK') if rollback else (
        'APPLIED' if _json(fd, 'applied.json') is not None else
        'APPLYING' if any(name.startswith('ready-') for name in os.listdir(fd)) else 'PREPARED')
    targets = {}
    for name, row in intent['captured']['targets'].items():
        meta = row['metadata']
        targets[name] = {'path': row['path'], 'before_sha256': None if before[name] is None else _hash(before[name]),
                         'after_sha256': _hash(after[name]), 'before_metadata': meta,
                         'after_metadata': _desired_metadata(meta), 'parent_identity': row['parents']}
    return {'schema': 'DEMO_FEEDBACK_FILES_RESULT_V1', 'phase': phase,
            'intent_sha256': identity, 'operation_sha256': intent['operation_sha256'],
            'source_commit': intent['source_commit'], 'captured': intent['captured'],
            'targets': targets, 'current_states': states, 'activation_authorized': False}


def prepare(stage, *, operation_sha256, source_commit, files, captured):
    """Publish complete intent before target writes. Exact PREPARED retry only."""
    _root()
    _token(operation_sha256)
    _token(source_commit, 40)
    before = _snapshot(captured)
    review = upgrade.review(files=files, preimages=before, release_commit=source_commit)
    _require(capture() == captured, 'PREIMAGE_CHANGED')
    stage = Path(stage)
    parent, _ = _directory(stage.parent)
    try:
        try:
            os.mkdir(stage.name, mode=0o700, dir_fd=parent)
            os.fsync(parent)
        except FileExistsError:
            with _locked(stage) as (fd, chain):
                raw = _read(fd, 'intent.json', MAX_INTENT)
                _require(raw is not None, 'INCOMPLETE_PREPARATION')
                identity = _hash(raw[0])
                intent, old, new = _load(fd, chain, identity)
                _require(intent['operation_sha256'] == operation_sha256 and
                         intent['source_commit'] == source_commit and intent['captured'] == captured
                         and old == before and new == files, 'PREPARE_CONFLICT')
                result = _receipt(fd, identity, intent, old, new)
                _require(result['phase'] == 'PREPARED' and set(result['current_states'].values()) == {'BEFORE'},
                         'PREPARE_PHASE')
                return identity
        with _locked(stage) as (fd, chain):
            intent = {'schema': 'DEMO_FEEDBACK_FILES_INTENT_V1', 'operation_sha256': operation_sha256,
                      'source_commit': source_commit, 'captured': captured,
                      'files': {name: base64.b64encode(body).decode() for name, body in files.items()},
                      'review': review, 'stage_parents': chain}
            _require(capture() == captured, 'PREIMAGE_CHANGED')
            raw = _canonical(intent)
            _write_new(fd, 'intent.json', raw)
            return _hash(raw)
    finally:
        os.close(parent)


def inspect(stage, identity):
    with _locked(stage, shared=True) as (fd, chain):
        intent, before, after = _load(fd, chain, identity)
        return _receipt(fd, identity, intent, before, after)


def _desired_metadata(before):
    return {'st_uid': OWNER_UID if before is None else before['st_uid'],
            'st_gid': OWNER_GID if before is None else before['st_gid'],
            'st_mode': stat.S_IFREG | (0o644 if before is None else stat.S_IMODE(before['st_mode']))}


def _ready(fd, identity, intent, name, action, raw, guard):
    existing = _json(fd, _ready_name(action, name))
    if existing is not None:
        return existing
    original = intent['captured']['targets'][name]
    meta = _desired_metadata(original['metadata'])
    path = Path(TARGETS[name])
    parent, chain = _directory(path.parent)
    try:
        _require(chain == original['parents'], 'PARENT_CHANGED')
        guard()
        leaf = os.open(_temp(identity, action, name),
                       os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                       0o600, dir_fd=parent)
        try:
            with os.fdopen(leaf, 'wb', closefd=False) as stream:
                stream.write(raw)
                stream.flush()
                os.fchown(leaf, meta['st_uid'], meta['st_gid'])
                os.fchmod(leaf, stat.S_IMODE(meta['st_mode']))
                os.fsync(leaf)
        finally:
            os.close(leaf)
        os.fsync(parent)
        observed = _read(parent, _temp(identity, action, name), upgrade.MAXIMUM)
        _require(observed is not None and observed[0] == raw, 'STAGING_CHANGED')
        ready = {'intent_sha256': identity, 'temporary': _temp(identity, action, name),
                 'metadata': observed[1]}
        _write_new(fd, _ready_name(action, name), _canonical(ready))
        return ready
    finally:
        os.close(parent)


def _publish_target(fd, identity, intent, name, action, raw, guard, expected):
    path = Path(TARGETS[name])
    parent, chain = _directory(path.parent)
    try:
        guard()
        _require(chain == intent['captured']['targets'][name]['parents'] and _observe(name) == expected,
                 'CAS_CHANGED')
        if raw is None:
            os.unlink(path.name, dir_fd=parent)
        else:
            ready = _json(fd, _ready_name(action, name))
            staged = _read(parent, ready['temporary'], upgrade.MAXIMUM)
            _require(staged is not None and staged[0] == raw and staged[1] == ready['metadata'], 'STAGING_CHANGED')
            if expected['metadata'] is None:
                # Reuse the original renameat2(RENAME_NOREPLACE) primitive:
                # absence remains a no-replace condition in the atomic syscall.
                storage.move(path.with_name(ready['temporary']), path)
            else:
                # The outer root maintenance lock excludes cooperating writers;
                # protected parent ownership excludes unprivileged name races.
                os.replace(ready['temporary'], path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
        row = _observe(name)
        _require(_body(row['body_base64']) == raw, 'READBACK')
        if raw is not None:
            _require(_same_file(row, raw, ready['metadata'], stable=True), 'READBACK')
        done = {'intent_sha256': identity, 'metadata': row['metadata']}
        _write_new(fd, _done_name(action, name), _canonical(done))
    finally:
        os.close(parent)


def _rollback_marker(fd, identity):
    if _json(fd, 'rollback.json') is None:
        _write_new(fd, 'rollback.json', _canonical({'intent_sha256': identity}))


def _complete(fd, identity, action):
    name = 'applied.json' if action == 'apply' else 'restored.json'
    if _json(fd, name) is None:
        _write_new(fd, name, _canonical({'intent_sha256': identity}))


def advance(stage, identity, *, action, guard):
    """Caller guard must independently hold authorization/inhibition/exclusion."""
    _require(action in ('apply', 'rollback') and callable(guard), 'ACTION_GUARD')
    with _locked(stage) as (fd, chain):
        intent, before, after = _load(fd, chain, identity)
        _require(action == 'rollback' or _json(fd, 'rollback.json') is None, 'ROLLBACK_SELECTED')
        guard()
        states = _states(fd, identity, intent, before, after)
        if action == 'rollback':
            _rollback_marker(fd, identity)
        for name in TARGETS:
            desired = after[name] if action == 'apply' else before[name]
            states = _states(fd, identity, intent, before, after)
            if states[name] == ('AFTER' if action == 'apply' else 'RESTORED') or (
                    action == 'rollback' and states[name] == 'BEFORE'):
                # A crash can follow rename and precede the done record. Seal
                # its exact post-rename metadata before declaring completion.
                if states[name] != 'BEFORE' and _json(fd, _done_name(action, name)) is None:
                    row = _observe(name)
                    _write_new(fd, _done_name(action, name),
                               _canonical({'intent_sha256': identity, 'metadata': row['metadata']}))
                continue
            expected = _observe(name)
            if desired is not None:
                _ready(fd, identity, intent, name, action, desired, guard)
            _states(fd, identity, intent, before, after)
            _publish_target(fd, identity, intent, name, action, desired, guard, expected)
        states = _states(fd, identity, intent, before, after)
        _require(set(states.values()) == {'AFTER'} if action == 'apply' else
                 set(states.values()) <= {'BEFORE', 'RESTORED'}, 'INCOMPLETE')
        guard()
        _complete(fd, identity, action)
        return _receipt(fd, identity, intent, before, after)
