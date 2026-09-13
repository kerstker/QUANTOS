"""Persistent, separate capture and sealing cycles for Demo namespace backups.

Capture runs as the source owner without a backup secret. A separate sealer
checks custody, exact captured bytes and complete isolated restoration. This
tool retains failures and interrupted work; it never activates restored state.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import uuid

from services.governance.json_integrity import load_json_without_duplicate_keys
from tools import demo_namespace_checkpoint as checkpoint

MAX_CYCLES = 512
MAX_SEAL_ATTEMPTS = 8
MAX_METADATA_BYTES = 65536
CYCLE_ID = re.compile(r'[0-9]{13}-[a-f0-9]{12}')
SCHEMA = 'DEMO_CHECKPOINT_CYCLE_V1'
SAFETY = dict(source_verified=False, cross_namespace_atomic=False,
              submission_enabled=False, economic_authority=False,
              production_restore_performed=False)


def _now():
    return time.time_ns() // 1_000_000


def _require(value, reason):
    checkpoint.require(value, reason)


def _private(path, uid, *, directory):
    info = Path(path).lstat()
    _require(info.st_uid == uid and not info.st_mode & 0o077 and
             (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
             'CYCLE_CUSTODY')
    return info


def _root(path, uid):
    path = Path(path).absolute()
    _require(not any(p.is_symlink() for p in path.parents), 'CYCLE_PARENT')
    _private(path, uid, directory=True)
    return path


def _read(path, uid):
    info = _private(path, uid, directory=False)
    _require(0 < info.st_size <= MAX_METADATA_BYTES, 'CYCLE_METADATA_SIZE')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        _require(all(getattr(info, k) == getattr(before, k) for k in fields), 'CYCLE_METADATA_CHANGED')
        body = source.read(MAX_METADATA_BYTES + 1)
        after = os.fstat(source.fileno())
        _require(len(body) == info.st_size and all(getattr(before, k) == getattr(after, k) for k in fields),
                 'CYCLE_METADATA_CHANGED')
    value = load_json_without_duplicate_keys(body)
    _require(type(value) is dict and value.get('schema') == SCHEMA, 'CYCLE_METADATA_SCHEMA')
    _require(all(value.get(k) is v for k, v in SAFETY.items()), 'CYCLE_AUTHORITY')
    return value


def _write_json(path, value):
    body = checkpoint.canonical(value)
    _require(len(body) <= MAX_METADATA_BYTES, 'CYCLE_METADATA_SIZE')
    # The final name appears only after the full file has been fsynced.
    fd, name = tempfile.mkstemp(prefix='.cycle-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as target:
            target.write(body); target.flush(); os.fsync(target.fileno())
        os.link(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(name)


@contextmanager
def _lock(root, uid):
    root = _root(root, uid)
    path = root / 'cycle.lock'
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    try:
        _private(path, uid, directory=False)
        _require(os.fstat(fd).st_size == 0, 'CYCLE_LOCK_CONTENT')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise checkpoint.CheckpointError('CYCLE_ALREADY_RUNNING') from error
        yield
    finally:
        os.close(fd)


def _cycles(root, uid):
    root = _root(root, uid)
    result = []
    for path in root.iterdir():
        if path.name == 'cycle.lock':
            _private(path, uid, directory=False)
            continue
        _require(CYCLE_ID.fullmatch(path.name), 'CYCLE_DIRECTORY_NAME')
        _private(path, uid, directory=True)
        result.append(path)
        _require(len(result) <= MAX_CYCLES, 'CYCLE_CAPACITY')
    return sorted(result)


def _identity(record, path, namespace):
    _require(record.get('cycle_id') == path.name and record.get('namespace') == namespace and
             type(record.get('source_commit')) is str and re.fullmatch('[a-f0-9]{40}', record['source_commit']),
             'CYCLE_SCOPE')
    _require(type(record.get('started_at_ms')) is int and record['started_at_ms'] >= 0, 'CYCLE_TIME')
    return record


def _reason(error):
    return str(error) if type(error) is checkpoint.CheckpointError else 'CHECKPOINT_UNCLASSIFIED'


def capture_cycle(source, spool, *, namespace, source_commit, observation_prefix=False):
    checkpoint.scope(namespace)
    _require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}', source_commit), 'CYCLE_SOURCE')
    _require(type(observation_prefix) is bool and (not observation_prefix or namespace == 'research'), 'CYCLE_PREFIX_SCOPE')
    spool = _root(spool, os.getuid())
    source = Path(source).absolute()
    _require(spool != source and source not in spool.parents and spool not in source.parents, 'CYCLE_SEPARATE_ROOTS')
    with _lock(spool, os.getuid()):
        _require(len(_cycles(spool, os.getuid())) < MAX_CYCLES, 'CYCLE_CAPACITY')
        at = _now(); identity = f'{at:013d}-' + uuid.uuid4().hex[:12]
        directory = spool / identity
        directory.mkdir(mode=0o700)
        start = dict(schema=SCHEMA, cycle_id=identity, namespace=namespace, source_commit=source_commit,
                     started_at_ms=at, status='STARTED', observation_prefix=observation_prefix, **SAFETY)
        _write_json(directory / 'capture-start.json', start)
        try:
            # Conservative worst-case space for source copies, ZIP and verification.
            _require(shutil.disk_usage(spool).free >= 3 * checkpoint.MAX_BYTES + 1024**3, 'CYCLE_DISK_RESERVE')
            captured = checkpoint.create_checkpoint(source, directory / 'capture.zip', namespace=namespace,
                source_commit=source_commit, passphrase=None, capture_only=True, observation_prefix=observation_prefix)
            result = dict(start, status='CAPTURED', completed_at_ms=_now(), capture=captured)
        except Exception as error:
            result = dict(start, status='FAILED', completed_at_ms=_now(), reason_code=_reason(error))
        _write_json(directory / 'capture-result.json', result)
        return result


def _capture(directory, uid, namespace):
    directory = _root(directory, uid)
    _require(CYCLE_ID.fullmatch(directory.name), 'CYCLE_DIRECTORY_NAME')
    start = _identity(_read(directory / 'capture-start.json', uid), directory, namespace)
    result = _identity(_read(directory / 'capture-result.json', uid), directory, namespace)
    _require(all(result.get(k) == v for k, v in start.items() if k != 'status'), 'CYCLE_START_BINDING')
    _require(result.get('status') == 'CAPTURED' and type(result.get('capture')) is dict, 'CYCLE_CAPTURE_REQUIRED')
    capture = result['capture']
    _require(capture.get('status') == 'CAPTURED_PRIVATE_NAMESPACE_ARCHIVE' and capture.get('namespace') == namespace and
             capture.get('source_commit') == result['source_commit'] and capture.get('encrypted') is False and
             type(capture.get('sha256')) is str and re.fullmatch('[a-f0-9]{64}', capture['sha256']), 'CYCLE_CAPTURE_BINDING')
    _private(directory / 'capture.zip', uid, directory=False)
    _require(checkpoint.sha(directory / 'capture.zip') == capture['sha256'], 'CYCLE_CAPTURE_DIGEST')
    return result


def seal_cycle(directory, backup, *, expected_uid, namespace, passphrase):
    checkpoint.scope(namespace)
    directory = Path(directory).absolute()
    _require(type(expected_uid) is int and expected_uid >= 0, 'CYCLE_SOURCE_UID')
    _require(CYCLE_ID.fullmatch(directory.name), 'CYCLE_DIRECTORY_NAME')
    _root(directory, expected_uid)
    backup = _root(backup, os.getuid())
    _require(backup != directory and directory not in backup.parents and backup not in directory.parents, 'CYCLE_SEPARATE_ROOTS')
    with _lock(backup, os.getuid()):
        output = backup / directory.name
        if not output.exists():
            _require(len(_cycles(backup, os.getuid())) < MAX_CYCLES, 'CYCLE_CAPACITY')
            output.mkdir(mode=0o700)
        _private(output, os.getuid(), directory=True)
        destination = output / 'checkpoint.enc'
        final = output / 'seal-result.json'
        if final.exists():
            record = _capture(directory, expected_uid, namespace)
            old = _identity(_read(final, os.getuid()), output, namespace)
            _private(destination, os.getuid(), directory=False)
            _require(old.get('status') == 'VERIFIED' and old.get('capture_sha256') == record['capture']['sha256'] and
                     old.get('source_commit') == record['source_commit'] and old.get('sha256') == checkpoint.sha(destination),
                     'CYCLE_SEALED_BINDING')
            return old
        failures = list(output.glob('seal-failure-*.json'))
        _require(len(failures) < MAX_SEAL_ATTEMPTS, 'CYCLE_SEAL_ATTEMPT_BUDGET')
        started = _now()
        record = None
        try:
            record = _capture(directory, expected_uid, namespace)
            _require(shutil.disk_usage(backup).free >= 4 * checkpoint.MAX_BYTES + 1024**3, 'CYCLE_DISK_RESERVE')
            recovered = destination.exists()
            if recovered:
                _private(destination, os.getuid(), directory=False)
                digest = checkpoint.sha(destination)
                with tempfile.TemporaryDirectory(prefix='.cycle-restore-', dir=output) as temp:
                    plain = Path(temp) / 'capture.zip'
                    checkpoint.decrypt_file(str(destination), str(plain), passphrase)
                    _require(checkpoint.sha(plain) == record['capture']['sha256'], 'CYCLE_CAPTURE_DIGEST')
                    checked = checkpoint.restore_checkpoint(destination, Path(temp) / 'restored',
                        passphrase=passphrase, expected_sha256=digest)
                checked = dict(checked, sha256=digest, encrypted=True)
            else:
                checked = checkpoint.seal_checkpoint(directory / 'capture.zip', destination,
                    passphrase=passphrase, expected_sha256=record['capture']['sha256'])
            _require(checked['namespace'] == namespace and checked['source_commit'] == record['source_commit'] and
                     checked['source_uid'] == expected_uid and checked['state'] == record['capture']['state'], 'CYCLE_RESTORE_BINDING')
            result = dict(checked, schema=SCHEMA, cycle_id=directory.name, status='VERIFIED',
                capture_sha256=record['capture']['sha256'], started_at_ms=record['started_at_ms'],
                seal_started_at_ms=started, completed_at_ms=_now(), recovered_after_interruption=recovered, **SAFETY)
        except Exception as error:
            failure = dict(schema=SCHEMA, cycle_id=directory.name, namespace=namespace,
                source_commit=None if record is None else record['source_commit'],
                status='FAILED', started_at_ms=started, completed_at_ms=_now(), reason_code=_reason(error), **SAFETY)
            _write_json(output / ('seal-failure-' + uuid.uuid4().hex + '.json'), failure)
            raise
        _write_json(final, result)
        return result


def summarize(spool, backup, *, expected_uid, namespace, now_ms, rpo_seconds):
    checkpoint.scope(namespace)
    _require(type(now_ms) is int and now_ms >= 0 and type(rpo_seconds) is int and 60 <= rpo_seconds <= 7 * 86400, 'CYCLE_STATUS_TIME')
    captures = _cycles(spool, expected_uid)
    archives = {path.name: path for path in _cycles(backup, os.getuid())}
    counts = dict(verified_cycles=0, pending_cycles=0, failed_cycles=0, interrupted_cycles=0, seal_failures=0)
    latest = point = None
    for path in captures:
        if not (path / 'capture-start.json').exists():
            counts['interrupted_cycles'] += 1
            continue
        start = _identity(_read(path / 'capture-start.json', expected_uid), path, namespace)
        if not (path / 'capture-result.json').exists():
            counts['interrupted_cycles'] += 1
            continue
        record = _identity(_read(path / 'capture-result.json', expected_uid), path, namespace)
        _require(all(record.get(k) == v for k, v in start.items() if k != 'status'), 'CYCLE_START_BINDING')
        if record.get('status') == 'FAILED':
            counts['failed_cycles'] += 1
            continue
        _require(record.get('status') == 'CAPTURED', 'CYCLE_CAPTURE_STATUS')
        archive = archives.get(path.name)
        if archive:
            counts['seal_failures'] += len(list(archive.glob('seal-failure-*.json')))
        if not archive or not (archive / 'seal-result.json').exists():
            counts['pending_cycles'] += 1
            continue
        sealed = _identity(_read(archive / 'seal-result.json', os.getuid()), archive, namespace)
        _private(archive / 'checkpoint.enc', os.getuid(), directory=False)
        _require(sealed.get('status') == 'VERIFIED' and sealed.get('capture_sha256') == record['capture']['sha256'] and
                 sealed.get('source_commit') == record['source_commit'], 'CYCLE_SEALED_BINDING')
        _require(type(sealed.get('completed_at_ms')) is int and 0 <= start['started_at_ms'] <= sealed['completed_at_ms'] <= now_ms,
                 'CYCLE_STATUS_CLOCK')
        counts['verified_cycles'] += 1
        if point is None or start['started_at_ms'] > point:
            latest, point = sealed['completed_at_ms'], start['started_at_ms']
    return dict(schema='DEMO_CHECKPOINT_CYCLE_STATUS_V1', namespace=namespace, observed_at_ms=now_ms,
        status='NO_VERIFIED_BACKUP' if point is None else 'STALE' if now_ms - point > rpo_seconds * 1000 else 'CURRENT',
        recovery_point_at_ms=point, last_verified_at_ms=latest, rpo_seconds=rpo_seconds,
        source_capture_age_ms=None if point is None else now_ms - point,
        capture_slots_remaining=MAX_CYCLES-len(captures), archive_bytes_reverified=False,
        automatic_history_deletion=False, **counts, **SAFETY)


def seal_pending(spool, backup, *, expected_uid, namespace, passphrase):
    """At most four pending captures per invocation; keep exhausted failures visible."""
    checkpoint.scope(namespace)
    backup = _root(backup, os.getuid())
    _cycles(backup, os.getuid())
    pending = []
    exhausted = 0
    for path in _cycles(spool, expected_uid):
        if not (path / 'capture-result.json').exists():
            continue
        record = _identity(_read(path / 'capture-result.json', expected_uid), path, namespace)
        if record.get('status') == 'FAILED':
            continue
        _require(record.get('status') == 'CAPTURED', 'CYCLE_CAPTURE_STATUS')
        output = Path(backup) / path.name
        if (output / 'seal-result.json').exists():
            _private(output, os.getuid(), directory=True)
            old = _identity(_read(output / 'seal-result.json', os.getuid()), output, namespace)
            _require(old.get('status') == 'VERIFIED' and old.get('source_commit') == record['source_commit'] and
                     old.get('capture_sha256') == record['capture']['sha256'], 'CYCLE_SEALED_BINDING')
            _private(output / 'checkpoint.enc', os.getuid(), directory=False)
            continue
        if output.exists():
            _private(output, os.getuid(), directory=True)
            if len(list(output.glob('seal-failure-*.json'))) >= MAX_SEAL_ATTEMPTS:
                exhausted += 1
                continue
        pending.append(path)
    records = []
    for path in pending[:4]:
        try:
            result = seal_cycle(path, backup, expected_uid=expected_uid, namespace=namespace, passphrase=passphrase)
            records.append(dict(cycle_id=path.name, status=result['status'], sha256=result['sha256']))
        except Exception as error:
            records.append(dict(cycle_id=path.name, status='FAILED', reason_code=_reason(error)))
    failed = sum(value['status'] == 'FAILED' for value in records)
    return dict(schema=SCHEMA, namespace=namespace, status='PARTIAL' if failed or exhausted else 'PROCESSED',
                observed_at_ms=_now(), attempted_cycles=len(records), failed_cycles=failed,
                exhausted_cycles=exhausted, pending_cycles=len(pending)-len(records)+failed,
                records=records, **SAFETY)


def verify_release(source_commit):
    _require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}', source_commit), 'CYCLE_RELEASE')
    root = Path(__file__).resolve().parents[1]
    _require(root == Path('/opt/quantos/releases') / source_commit, 'CYCLE_RELEASE_LOCATION')
    for parent in (root, *root.parents):
        info = parent.lstat()
        _require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'CYCLE_RELEASE_CUSTODY')
    from tools.l2_schedule_entrypoint import _verify_release_identity, _verify_release_evidence
    _verify_release_identity(root, source_commit)
    _verify_release_evidence(root, source_commit)
    return dict(schema=SCHEMA, status='RELEASE_VERIFIED', source_commit=source_commit, **SAFETY)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    verify = commands.add_parser('verify-release')
    verify.add_argument('--source-commit', required=True)
    capture = commands.add_parser('capture')
    capture.add_argument('--source-dir', type=Path, required=True)
    capture.add_argument('--source-commit', required=True)
    capture.add_argument('--observation-prefix', action='store_true')
    seal = commands.add_parser('seal')
    selection = seal.add_mutually_exclusive_group(required=True)
    selection.add_argument('--cycle-id')
    selection.add_argument('--pending', action='store_true')
    status = commands.add_parser('status')
    status.add_argument('--rpo-seconds', type=int, default=86400)
    for command in (capture, seal, status):
        command.add_argument('--namespace', choices=('research', 'executor', 'issuer', 'controller'), required=True)
        command.add_argument('--spool', type=Path, required=True)
    for command in (seal, status):
        command.add_argument('--backup', type=Path, required=True)
        command.add_argument('--source-uid', type=int, required=True)
    args = parser.parse_args(argv); previous = os.umask(0o077)
    try:
        if args.command == 'verify-release':
            result = verify_release(args.source_commit)
        elif args.command == 'capture':
            result = capture_cycle(args.source_dir, args.spool, namespace=args.namespace,
                source_commit=args.source_commit, observation_prefix=args.observation_prefix)
        elif args.command == 'seal':
            _require(args.pending or CYCLE_ID.fullmatch(args.cycle_id), 'CYCLE_DIRECTORY_NAME')
            secret = checkpoint.resolve_backup_passphrase(environment_passphrase=None, passphrase_file=checkpoint._default_passphrase_file())
            if args.pending:
                result = seal_pending(args.spool, args.backup, expected_uid=args.source_uid,
                    namespace=args.namespace, passphrase=secret)
            else:
                result = seal_cycle(args.spool / args.cycle_id, args.backup, expected_uid=args.source_uid,
                    namespace=args.namespace, passphrase=secret)
            del secret
        else:
            result = summarize(args.spool, args.backup, expected_uid=args.source_uid,
                namespace=args.namespace, now_ms=_now(), rpo_seconds=args.rpo_seconds)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] in ('CAPTURED', 'VERIFIED', 'CURRENT', 'PROCESSED', 'RELEASE_VERIFIED') else 3
    except Exception as error:
        print(json.dumps(dict(schema=SCHEMA, status='NOT_COMPLETED', command=args.command, reason_code=_reason(error), **SAFETY)))
        return 3
    finally:
        os.umask(previous)


if __name__ == '__main__':
    raise SystemExit(main())
