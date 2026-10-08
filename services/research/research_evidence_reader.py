"""Read original evidence under a trusted, fixed application configuration.

Construct this reader in the application composition root, never from request
paths or keys supplied with an assessment. The public registry digest, minimum
generation, source commit and producer are operator/release pins. This module
does not configure them or grant authority. Signature and native result checks
are performed by the validation consumer on each returned snapshot.

Only private copies of the original TrialLedger DB/WAL are opened by SQLite.
Every trial, including failed, cancelled and planned trials, is compared with
the retained export. Original files are never initialized or repaired.
"""
from __future__ import annotations

from quantos.contracts.research_evidence_json import _require, _canonical, _decode

import hashlib
import os
from pathlib import Path
import platform
import re
import stat
import tempfile
from contextlib import contextmanager
from itertools import islice

from services.research.trial_ledger import TrialLedger

ROLES = ('source_manifest', 'source_identity', 'candidate_definition', 'manifest',
         'input', 'record', 'dataset_manifest', 'trial_history', 'evaluation_plan', 'risk_replay')
LEAF_BYTES = 16_000_000
TOTAL_BYTES = 64_000_000
SOURCE_BYTES = 256 * 1024**2
SOURCE_FILES = 20_000
DATABASE_BYTES = 128 * 1024**2
MAX_TRIALS = 100_000


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _path(value):
    path = Path(value)
    _require(path.is_absolute() and '..' not in path.parts and path != Path('/'), 'PATH')
    return path


def _open(path, flags):
    """Resolve every component without following a symlink, including parents."""
    path = _path(path)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd)
            os.close(fd)
            fd = child
        return os.open(path.name, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                       dir_fd=fd)
    except OSError as exc:
        raise ValueError('RESEARCH_EVIDENCE_PATH_CUSTODY') from exc
    finally:
        os.close(fd)


class _ReadSet:
    def __init__(self, *, maximum_directory_entries=None):
        self.files = {}
        self.directories = {}
        _require(maximum_directory_entries is None or
            (type(maximum_directory_entries) is int and maximum_directory_entries > 0), 'DIRECTORY_BOUND')
        self.maximum_directory_entries = maximum_directory_entries

    def directory(self, path):
        fd = _open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            info = os.fstat(fd)
            _require(info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022,
                     'DIRECTORY_CUSTODY')
            if self.maximum_directory_entries is None:
                names = os.listdir(fd)
            else:
                with os.scandir(fd) as iterator:
                    names = [entry.name for entry in islice(iterator, self.maximum_directory_entries+1)]
                _require(len(names) <= self.maximum_directory_entries, 'DIRECTORY_BOUND')
            value = (_identity(info), tuple(sorted(names)))
            _require(_identity(os.fstat(fd)) == value[0], 'DIRECTORY_CHANGED')
        finally:
            os.close(fd)
        previous = self.directories.setdefault(path, value)
        _require(previous == value, 'DIRECTORY_CHANGED')
        return value[1]

    def read(self, path, limit):
        fd = _open(path, os.O_RDONLY)
        try:
            info = os.fstat(fd)
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                     and info.st_uid in (0, os.geteuid()) and not info.st_mode & 0o022,
                     'FILE_CUSTODY')
            _require(info.st_size <= limit, 'FILE_BOUND')
            with os.fdopen(fd, 'rb', closefd=False) as handle:
                raw = handle.read(limit + 1)
            _require(len(raw) == info.st_size and _identity(os.fstat(fd)) == _identity(info),
                     'FILE_CHANGED')
        finally:
            os.close(fd)
        value = (_identity(info), _sha(raw), limit)
        previous = self.files.setdefault(path, value)
        _require(previous == value, 'FILE_CHANGED')
        return raw

    def recheck(self):
        for path in tuple(self.directories):
            self.directory(path)
        for path, (_, _, limit) in tuple(self.files.items()):
            self.read(path, limit)
        return _sha(_canonical({
            'files': {str(p): list(v[:2]) for p, v in sorted(self.files.items())},
            'directories': {str(p): v for p, v in sorted(self.directories.items())},
        }))


def _source(reads, root, originals, commit):
    identity = _decode(originals['source_identity'])
    manifest = _decode(originals['source_manifest'])
    _require(type(identity) is dict and set(identity) == {
        'commit', 'python_source_sha256', 'dependency_lock_sha256', 'python_version', 'architecture'},
        'SOURCE_IDENTITY')
    _require(type(manifest) is dict and set(manifest) == {'schema', 'source_identity_sha256', 'files'}
        and manifest['schema'] == 'QUANTOS_RESEARCH_SOURCE_MANIFEST_V1'
        and manifest['source_identity_sha256'] == _sha(originals['source_identity']), 'SOURCE_MANIFEST')
    reads.directory(root)
    paths = []
    pending = [root / folder for folder in ('services', 'quantos', 'tools')]
    visited = 0
    while pending:
        directory = pending.pop()
        names = reads.directory(directory)
        visited += 1
        _require(visited <= SOURCE_FILES, 'SOURCE_BOUND')
        for name in names:
            path = directory / name
            info = path.lstat()
            _require(not stat.S_ISLNK(info.st_mode), 'SOURCE_PATH')
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif name.endswith('.py'):
                paths.append(path)
                _require(len(paths) <= SOURCE_FILES, 'SOURCE_BOUND')
    digest = hashlib.sha256()
    files = {}
    size = 0
    for path in sorted(paths):
        raw = reads.read(path, min(LEAF_BYTES, SOURCE_BYTES - size))
        size += len(raw)
        _require(size <= SOURCE_BYTES, 'SOURCE_BOUND')
        name = path.relative_to(root).as_posix()
        digest.update(name.encode() + b'\0' + raw + b'\0')
        files[name] = _sha(raw)
    lock = reads.read(root / 'requirements-ci-linux-aarch64.lock', LEAF_BYTES)
    observed = dict(commit=commit, python_source_sha256=digest.hexdigest(),
        dependency_lock_sha256=_sha(lock), python_version=platform.python_version(),
        architecture=platform.machine())
    _require(_canonical(identity) == _canonical(observed), 'SOURCE_IDENTITY')
    _require(_canonical(manifest['files']) == _canonical(files), 'SOURCE_MEMBERS')


@contextmanager
def _private_database(reads, database):
    """Retain DB/WAL/SHM in one read set; open only a disposable DB/WAL copy."""
    names = reads.directory(database.parent)
    _require(database.name + '-journal' not in names, 'TRIAL_ROLLBACK_JOURNAL')
    members = [database]
    if database.name + '-wal' in names:
        members.append(Path(str(database) + '-wal'))
    # SHM is coordination state, not a source of committed rows; retain its
    # identity/bytes in the stable read cut without opening the original DB.
    if database.name + '-shm' in names:
        reads.read(Path(str(database) + '-shm'), DATABASE_BYTES)
    with tempfile.TemporaryDirectory(prefix='quantos-research-evidence-') as temporary:
        private = Path(temporary)
        for path in members:
            raw = reads.read(path, DATABASE_BYTES)
            target = private / path.name
            with target.open('xb') as handle:
                target.chmod(0o600)
                handle.write(raw)
        yield private / database.name


def _trial_snapshot(reads, database, *, maximum_receipts=None):
    with _private_database(reads, database) as private_database:
        with TrialLedger(str(private_database), create=False, read_only=True) as ledger:
            count = ledger._c.execute('SELECT count(*) FROM trial_events').fetchone()[0]
            _require(count <= MAX_TRIALS * 3, 'TRIAL_HISTORY_BOUND')
            if maximum_receipts is not None:
                count = ledger._c.execute('SELECT count(*) FROM validation_receipt_events').fetchone()[0]
                _require(count <= maximum_receipts, 'RECEIPT_HISTORY_BOUND')
            ledger.verify()
            cursor = ledger._c.execute('SELECT * FROM trial_events ORDER BY global_seq')
            fields = [column[0] for column in cursor.description]
            history = {}
            for row in cursor:
                event = dict(zip(fields, row))
                history.setdefault(event['trial_id'], []).append(event)
            _require(len(history) <= MAX_TRIALS, 'TRIAL_HISTORY_BOUND')
            receipts, head = [], None
            if maximum_receipts is not None:
                cursor = ledger._c.execute('SELECT * FROM validation_receipt_events ORDER BY global_seq')
                fields = [column[0] for column in cursor.description]
                receipts = [dict(zip(fields, row)) for row in cursor]
                row = ledger._c.execute('SELECT event_count,head_hash FROM validation_receipt_heads WHERE id=1').fetchone()
                head = dict(zip(('event_count', 'head_hash'), row))
    return dict(history=history, validation_receipts=receipts, validation_receipt_head=head)


def _trial_history(reads, database):
    return _trial_snapshot(reads, database)['history']


def _trials(reads, database, originals):
    history = _trial_history(reads, database)
    _require(_canonical(_decode(originals['trial_history'])) == _canonical(history), 'TRIAL_HISTORY')
    plan = _decode(originals['evaluation_plan'])
    ids = plan.get('trial_ids') if type(plan) is dict else None
    _require(type(ids) is list and all(type(v) is str for v in ids)
             and len(ids) == len(set(ids)) and set(ids) == set(history), 'TRIAL_HISTORY_PLAN')


class ResearchEvidenceSnapshotReader:
    """Fixed original locations and independent governance pins, supplied by bootstrap.

    This read boundary does not establish whether a research result is eligible.
    Its return value must go through consume_evidence, including its second read.
    A source commit pin comes from the existing verified release bootstrap; this
    reader checks its complete Python/lock content, not a caller-written .git/HEAD.
    """
    def __init__(self, material_directory, *, source_directory, trial_database,
                 trust_registry, expected_registry_sha256, minimum_generation,
                 source_commit, producer_id):
        self.material = _path(material_directory)
        self.source = _path(source_directory)
        self.trials = _path(trial_database)
        self.trust = _path(trust_registry)
        _require(not self.trust.is_relative_to(self.material)
                 and not self.material.is_relative_to(self.trust.parent), 'TRUST_LOCATION')
        _require(type(expected_registry_sha256) is str
                 and re.fullmatch('[0-9a-f]{64}', expected_registry_sha256), 'TRUST_PIN')
        _require(type(minimum_generation) is int and minimum_generation >= 1, 'TRUST_GENERATION')
        _require(type(source_commit) is str and re.fullmatch('[0-9a-f]{40}', source_commit), 'SOURCE_COMMIT')
        _require(type(producer_id) is str and 0 < len(producer_id) <= 255, 'PRODUCER')
        self.expected_registry_sha256 = expected_registry_sha256
        self.minimum_generation = minimum_generation
        self.source_commit = source_commit
        self.producer_id = producer_id

    def __call__(self):
        reads = _ReadSet()
        names = set(reads.directory(self.material))
        required = {role + '.json' for role in ROLES if role != 'risk_replay'}
        required |= {'subject.json', 'source_envelope.json', 'result_envelope.json'}
        _require(names in (required, required | {'risk_replay.json'}), 'MATERIAL_MEMBERS')
        raw = {}
        used_bytes = 0
        for name in sorted(names):
            limit = (16384 if name == 'subject.json' else
                     65536 if name in ('source_envelope.json', 'result_envelope.json') else LEAF_BYTES)
            raw[name] = reads.read(self.material / name, min(limit, TOTAL_BYTES - used_bytes))
            used_bytes += len(raw[name])
            _decode(raw[name])
        _require(sum(map(len, raw.values())) <= TOTAL_BYTES, 'MATERIAL_BOUND')
        subject = _decode(raw['subject.json'])
        _require(type(subject) is dict and subject.get('producer_id') == self.producer_id, 'PRODUCER')
        originals = {role: raw.get(role + '.json') for role in ROLES}
        _source(reads, self.source, originals, self.source_commit)
        _trials(reads, self.trials, originals)
        reads.directory(self.trust.parent)
        registry_raw = reads.read(self.trust, 65536)
        _require(_sha(registry_raw) == self.expected_registry_sha256, 'TRUST_PIN')
        registry = _decode(registry_raw)
        _require(type(registry) is dict
                 and set(registry) == {'schema', 'generation', 'producer_ids', 'keys'}
                 and registry['schema'] == 'QUANTOS_RESEARCH_EVIDENCE_TRUST_V1'
                 and type(registry['generation']) is int
                 and registry['generation'] >= self.minimum_generation, 'TRUST_GENERATION')
        # The signature layer pins canonical registry content; the operator pin
        # above additionally binds the exact bytes in the governed file.
        return dict(schema='QUANTOS_RESEARCH_EVIDENCE_SNAPSHOT_V1', subject=subject,
            originals=originals, source_envelope=_decode(raw['source_envelope.json']),
            result_envelope=_decode(raw['result_envelope.json']), trust_registry=registry,
            expected_registry_sha256=_sha(_canonical(registry)), read_identity_sha256=reads.recheck())
