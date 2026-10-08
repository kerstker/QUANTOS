"""Explicit 42/44 -> 44 feedback configuration transition, with no public CLI.

Trusted root caller supplies freshly captured consumer originals and the actual
local operator's approval. This module revalidates content, installed release,
locks, service inhibition and file custody; it does not establish provenance of
caller-supplied consumer originals or create human authorization. Old maintenance
journals are immutable inputs here. No issuer/account/research ledger is written.
"""
from contextlib import contextmanager
from copy import deepcopy
import base64
import hashlib
from itertools import islice
import json
from pathlib import Path
import re
import time

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_feedback_upgrade_files as files_tx
from tools import demo_feedback_upgrade_preflight as consumer
from tools import demo_maintenance_operator as operator
from tools import demo_maintenance_services as services
from tools import demo_maintenance_window as window
from tools import demo_supervisor as supervisor
from tools.demo_retention_transaction import directory, read, require, sync, write_new

SCHEMA = 'DEMO_FEEDBACK_CONFIGURATION_TRANSITION_V1'
APPROVAL = 'DEMO_FEEDBACK_CONFIGURATION_TRANSITION_APPROVAL_V1'
PURPOSE = 'UPGRADE_DEMO_FEEDBACK_CONFIGURATION'
AREA = 'feedback-upgrade'
TERMINAL = {'COMPLETED', 'ROLLED_BACK', 'CANCELED_UNCHANGED'}
PHASES = TERMINAL | {'PREPARED', 'INHIBITING', 'APPLYING', 'VERIFYING', 'RELEASING', 'RESTORING',
                     'ROLLBACK_PAUSING', 'ROLLING_BACK', 'ROLLBACK_RELEASING', 'ROLLBACK_RESTORING'}
MAXIMUM = 16 * 1024 * 1024
WITNESSES = 'consumer-witness'
WITNESS_HEAD = 'consumer-witness-head.json'
# At most 64 receipt additions, one previously absent directory creation and
# the original snapshot. Store deltas, not 66 copies of the full originals.
MAX_WITNESSES = 66
WITNESS_SCHEMA = 'DEMO_FEEDBACK_CONSUMER_WITNESS_V1'
WITNESS_HEAD_SCHEMA = 'DEMO_FEEDBACK_CONSUMER_WITNESS_HEAD_V1'


def _now():
    return int(time.time() * 1000)


def _canonical(value):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    require(len(raw) <= MAXIMUM, 'FEEDBACK_UPGRADE_SIZE')
    return raw


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _load(path):
    return decode(read(path, MAXIMUM))


def _put(path, value):
    write_new(path, _canonical(value))


def _phase(area, review, phase):
    services._save(area / 'state.json', {'schema': SCHEMA, 'review_sha256': _digest(review), 'phase': phase})


def _state(area, review):
    value = _load(area / 'state.json')
    require(type(value) is dict and set(value) == {'schema', 'review_sha256', 'phase'} and
            value['schema'] == SCHEMA and value['review_sha256'] == _digest(review) and
            value['phase'] in PHASES, 'FEEDBACK_UPGRADE_STATE')
    return value['phase']


def _approval(value, review):
    require(type(value) is dict and set(value) == {'schema', 'purpose', 'review_sha256',
            'operation_sha256', 'source_commit', 'operator_id', 'approved_at_ms',
            'service_activation_authorized', 'submission_enabled', 'economic_authority'} and
            value['schema'] == APPROVAL and value['purpose'] == PURPOSE and
            value['review_sha256'] == _digest(review) and
            value['operation_sha256'] == review['operation_sha256'] and
            value['source_commit'] == review['source_commit'] and
            type(value['operator_id']) is str and 0 < len(value['operator_id']) <= 128 and
            type(value['approved_at_ms']) is int and
            review['consumer_preflight']['observed_at_ms'] <= value['approved_at_ms'] <= _now() and
            value['service_activation_authorized'] is True and value['submission_enabled'] is False and
            value['economic_authority'] is False, 'FEEDBACK_UPGRADE_APPROVAL')
    return value


@contextmanager
def _locked(root):
    with operator.exclusive(), supervisor.controller_lease(maintenance=True), window._locked(root) as held:
        yield held


def _source(commit):
    require(type(commit) is str and re.fullmatch('[a-f0-9]{40}', commit), 'FEEDBACK_UPGRADE_SOURCE')
    operator._admit(commit)


def _files(review):
    return {name: base64.b64decode(raw, validate=True) for name, raw in review['files_base64'].items()}


def _consumer_snapshot(snapshot, source_commit, *, initial=False, started=False):
    from tools import demo_feedback_upgrade_consumer_capture as capture
    if initial:
        current = capture.capture(source_commit=source_commit)
        require(current == snapshot, 'FEEDBACK_UPGRADE_CONSUMER_CHANGED')
        return current
    return (capture.recheck_started if started else capture.recheck)(snapshot, source_commit=source_commit)


def _witness_path(area, index):
    return area / WITNESSES / ('witness-' + str(index).zfill(3) + '.json')


def _witness_record(review, before, after, index, previous_sha256):
    old_records = before['consumer_evidence']['feedback_results']
    records = after['consumer_evidence']['feedback_results']
    old_files = before['read_identity']['files']
    identities = after['read_identity']
    return {'schema': WITNESS_SCHEMA, 'review_sha256': _digest(review), 'index': index,
            'previous_sha256': previous_sha256, 'snapshot_sha256': _digest(after),
            'feedback_records': {name: row for name, row in records.items() if name not in old_records},
            'file_identities': {name: row for name, row in identities['files'].items() if name not in old_files},
            'feedback_directory': identities['directories'][identities['feedback_directory']]}


def _head(record):
    return {'schema': WITNESS_HEAD_SCHEMA, 'review_sha256': record['review_sha256'],
            'index': record['index'], 'witness_sha256': _digest(record),
            'snapshot_sha256': record['snapshot_sha256']}


def _witness_state(area, review):
    """Replay the complete sequence; one complete unheaded next row is recoverable.

    Missing/truncated/intermediate/extra files are not guessed. The independent
    durable head detects tail deletion; a lagging head can only move forward to
    the unique fully verified next row under the original operation's locks.
    """
    location = area / WITNESSES
    directory(location)
    names = {p.name for p in islice(location.iterdir(), MAX_WITNESSES + 1)}
    count = len(names)
    require(1 <= count <= MAX_WITNESSES and names ==
            {_witness_path(area, index).name for index in range(count)}, 'CONSUMER_WITNESS_FILES')
    snapshot = deepcopy(review['consumer_snapshot'])
    initial = _witness_record(review, snapshot, snapshot, 0, None)
    previous = None
    heads = []
    total_bytes = 0
    for index in range(count):
        raw = read(_witness_path(area, index), MAXIMUM)
        total_bytes += len(raw)
        require(total_bytes <= MAXIMUM, 'CONSUMER_WITNESS_BUDGET')
        row = decode(raw)
        require(type(row) is dict and set(row) == set(initial) and row['schema'] == WITNESS_SCHEMA and
                row['review_sha256'] == _digest(review) and type(row['index']) is int and row['index'] == index
                and row['previous_sha256'] == previous, 'CONSUMER_WITNESS_CHAIN')
        if index == 0:
            require(row == initial, 'CONSUMER_WITNESS_ROOT')
        else:
            records, identities = row['feedback_records'], row['file_identities']
            current_records = snapshot['consumer_evidence']['feedback_results']
            current_identity = snapshot['read_identity']
            feedback_path = current_identity['feedback_directory']
            require(type(records) is dict and type(identities) is dict and
                    not set(records) & set(current_records) and
                    len(current_records) + len(records) <= 64 and
                    all(name == _digest(value) + '.json' for name, value in records.items()) and
                    set(identities) == {str(Path(feedback_path) / name) for name in records} and
                    not set(identities) & set(current_identity['files']), 'CONSUMER_WITNESS_DELTA')
            before_directory = current_identity['directories'][feedback_path]
            after_directory = row['feedback_directory']
            require(type(after_directory) is dict and set(after_directory) == set(before_directory) and
                    after_directory['parent'] == before_directory['parent'] and
                    (before_directory['directory'] is None or
                     after_directory['directory'] == before_directory['directory']) and
                    set(after_directory['members'] or ()) == set(current_records) | set(records),
                    'CONSUMER_WITNESS_DIRECTORY')
            previous_snapshot = _digest(snapshot)
            current_records.update(records)
            current_identity['files'].update(identities)
            current_identity['directories'][feedback_path] = after_directory
            snapshot['read_identity_sha256'] = _digest(current_identity)
            require(_digest(snapshot) != previous_snapshot, 'CONSUMER_WITNESS_EMPTY')
        require(_digest(snapshot) == row['snapshot_sha256'], 'CONSUMER_WITNESS_SNAPSHOT')
        previous = _digest(row)
        heads.append(_head(row))
    head = _load(area / WITNESS_HEAD)
    require(type(head) is dict and set(head) == set(heads[0]) and
            type(head['index']) is int and 0 <= head['index'] < count and
            head == heads[head['index']] and count - 1 - head['index'] in {0, 1},
            'CONSUMER_WITNESS_HEAD')
    return snapshot, heads[-1], head != heads[-1]


def _initial_witness(area, review):
    location = area / WITNESSES
    initial = _witness_record(review, review['consumer_snapshot'], review['consumer_snapshot'], 0, None)
    if not location.exists() and not location.is_symlink():
        location.mkdir(mode=0o700)
        sync(area)
    directory(location)
    # Before a start marker exists there can only be this original root.
    # A completely published root with its head write interrupted is known.
    names = {p.name for p in islice(location.iterdir(), MAX_WITNESSES + 1)}
    head_path = area / WITNESS_HEAD
    if not names:
        # mkdir + fsync may have completed before the first root-row write.
        # The caller has rechecked the unchanged original consumer snapshot,
        # approved restore boundary and absence of any start marker.
        require(not head_path.exists() and not head_path.is_symlink(), 'CONSUMER_WITNESS_ROOT')
        _put(_witness_path(area, 0), initial)
        names = {_witness_path(area, 0).name}
    if names == {_witness_path(area, 0).name} and not head_path.exists() and not head_path.is_symlink():
        require(_load(_witness_path(area, 0)) == initial, 'CONSUMER_WITNESS_ROOT')
        services._save(head_path, _head(initial))
    snapshot, head, pending = _witness_state(area, review)
    require(snapshot == review['consumer_snapshot'] and head == _head(initial) and not pending,
            'CONSUMER_WITNESS_ROOT')
    info = directory(location)
    return {'root_sha256': _digest(initial), 'device': info.st_dev, 'inode': info.st_ino}


def _started_snapshot(area, review, *, record):
    snapshot, head, pending = _witness_state(area, review)
    fresh = _consumer_snapshot(snapshot, review['source_commit'], started=True)
    if not record:
        require(not pending, 'CONSUMER_WITNESS_RECOVERY_REQUIRED')
        return fresh
    if pending:
        # The complete next row is already the comparison anchor. Never compare
        # against the older head or fabricate a replacement for the retained row.
        services._save(area / WITNESS_HEAD, head)
    if fresh != snapshot:
        require(head['index'] + 1 < MAX_WITNESSES, 'CONSUMER_WITNESS_BUDGET')
        row = _witness_record(review, snapshot, fresh, head['index'] + 1, head['witness_sha256'])
        # Publish the independently checked delta, then verify full replay.
        _put(_witness_path(area, row['index']), row)
        # Keep atomic-replacement temporaries outside the immutable row set.
        # A fsynced temp is neither an authoritative head nor a witness; after
        # a crash the existing head and unique complete next row remain enough.
        services._save(area / WITNESS_HEAD, _head(row))
        rebuilt, latest, pending = _witness_state(area, review)
        require(rebuilt == fresh and latest == _head(row) and not pending, 'CONSUMER_WITNESS_READBACK')
    return fresh


def _started(area, review):
    path = area / 'start-allowed.json'
    if not path.exists() and not path.is_symlink():
        require(_state(area, review) not in {'COMPLETED', 'ROLLED_BACK'}, 'FEEDBACK_UPGRADE_START_BOUNDARY')
        return False
    value = _load(path)
    require(type(value) is dict and set(value) == {'schema', 'review_sha256', 'approval_sha256', 'consumer_witness'} and
            value['schema'] == 'DEMO_FEEDBACK_CONFIGURATION_START_BOUNDARY_V1' and
            value['review_sha256'] == _digest(review) and
            value['approval_sha256'] == _digest(_approval(_load(area / 'approval.json'), review)) and
            _state(area, review) in {'RESTORING', 'COMPLETED', 'ROLLBACK_PAUSING', 'ROLLING_BACK',
                'ROLLBACK_RELEASING', 'ROLLBACK_RESTORING', 'ROLLED_BACK'},
            'FEEDBACK_UPGRADE_START_BOUNDARY')
    witness = value['consumer_witness']
    info = directory(area / WITNESSES)
    require(type(witness) is dict and set(witness) == {'root_sha256', 'device', 'inode'} and
            witness == {'root_sha256': _digest(_load(_witness_path(area, 0))),
                        'device': info.st_dev, 'inode': info.st_ino}, 'CONSUMER_WITNESS_IDENTITY')
    return True


def _preflight(review, snapshot, *, started=False):
    value = consumer.preflight(files=_files(review), preimages=files_tx.preimages(review['captured']),
                               release_commit=review['source_commit'],
                               consumer_evidence=snapshot['consumer_evidence'], now_ms=_now())
    original = review['consumer_preflight']
    require(value['observed_at_ms'] >= original['observed_at_ms'] and
            value.get('timer_compatible') is True and
            (started or {k: v for k, v in value.items() if k != 'observed_at_ms'} ==
             {k: v for k, v in original.items() if k != 'observed_at_ms'}),
            'FEEDBACK_UPGRADE_CONSUMER_CHANGED')


def _drained(root, document, evidence):
    window.preparation.restore_guard(root, document)
    previous = window._worker_record(root, document)
    if previous is not None:
        result = previous['result']
        require(window.handoff.drained(previous['unit']) and
                ((type(result) is dict and result.get('status') == 'WORKER_COMPLETED' and
                  result.get('worker_drained') is True) or window._prepare_aborted(root, document, previous)),
                'WINDOW_WORKER_RECOVERY_REQUIRED')
    registry = evidence['executor_registry']
    observed = window.processes.observe(executor_uid=registry['executor_uid'], issuer_uid=registry['issuer_uid'])
    require(observed['status'] == 'NO_SCOPED_PROCESS_OBSERVED', 'FEEDBACK_UPGRADE_PROCESSES_UNKNOWN')


def _journal(root, document, review):
    require(document == review['window'] and document['phase'] == 'PAUSED' and
            services.inspect_journal(root / 'services.json') == review['original_journal'] and
            hashlib.sha256(read(root / 'services.json', 128 * 1024)).hexdigest() == review['journal_sha256'],
            'FEEDBACK_UPGRADE_WINDOW_CHANGED')


def _loaded(expected, token=None, *, inhibited=True, idle=True, allowed_active=()):
    backend = window.backend(token, require_inhibition=inhibited)
    for name, fingerprint in expected.items():
        value = services._settled_observation(backend, name)
        require(value['fingerprint'] == fingerprint, 'CONFIGURATION_CHANGED')
        if idle or name not in allowed_active:
            require(value['active'] in {'inactive', 'failed'} and backend.idle(name), 'UNIT_PROCESSES_REMAIN')
    return backend


def _restored(expected, active):
    """Check the current terminal configuration and activity without starting."""
    require(set(services.configured_units()) == set(expected), 'UNIT_SCOPE_CHANGED')
    backend = _loaded(expected, idle=False, allowed_active=active)
    for name in active:
        value = services._settled_observation(backend, name)
        require(value['fingerprint'] == expected[name] and (value['active'] == 'active' or
                name.endswith('.service') and name != 'quantos-demo-executor-stream.service' and
                value['active'] == 'inactive'), 'RESTORE_INCOMPLETE')


def _fingerprint(path, digest):
    return hashlib.sha256(json.dumps([(path, digest)], sort_keys=True).encode()).hexdigest()


def _pair_before(captured, token):
    backend = window.backend(token, require_inhibition=False)
    result = {}
    for name in services.FEEDBACK_UNITS:
        row = captured['targets'][name]
        current = backend.configuration(name, allow_absent=True)
        raw = files_tx.preimages(captured)[name]
        require(current['exists'] == (raw is not None), 'FEEDBACK_UPGRADE_PAIR_EXISTENCE')
        if raw is not None:
            expected = [(row['path'], hashlib.sha256(raw).hexdigest())]
            require(current['configuration'] == expected, 'FEEDBACK_UPGRADE_PAIR_CONFIGURATION')
        result[name] = current
    return result


def _disk_inhibition(token):
    # Exact files and no competing operation; publication guard runs every time.
    with window.inhibit._locked():
        paths = window.inhibit._paths(window.inhibit._payload(token), services.ALLOWED_UNITS)
        require(all(window.inhibit._existing(path, window.inhibit._payload(token)) for path in paths),
                'INHIBITOR_MISSING')


def _base_guard(root, document, review, *, record_consumer=True):
    _source(review['source_commit'])
    _journal(root, document, review)
    if (root / AREA / 'approval.json').exists():
        _approval(_load(root / AREA / 'approval.json'), review)
    started = _started(root / AREA, review)
    snapshot = (_started_snapshot(root / AREA, review, record=record_consumer) if started else
                _consumer_snapshot(review['consumer_snapshot'], review['source_commit']))
    _preflight(review, snapshot, started=started)



def _write_guard(root, document, review, *, rollback=False):
    _base_guard(root, document, review)
    _drained(root, document, review['consumer_snapshot']['consumer_evidence'])
    token = document['inhibitor_sha256']
    _disk_inhibition(token)
    if rollback:
        # First-install rollback can remove a fragment still cached as loaded.
        # Reload only under the complete same-operation inhibitor, before the
        # next mutation; no absent fragment is treated as an inspect success.
        window.reload_manager()
    _loaded({name: review['old_fingerprints'][name] for name in services.UNITS}, token,
            inhibited=False)
    backend = window.backend(token, require_inhibition=False)
    for name in services.FEEDBACK_UNITS:
        value = backend.configuration(name, allow_absent=True)
        require(value['active'] in {'inactive', 'failed'} and
                (not value['exists'] or backend.idle(name)), 'UNIT_PROCESSES_REMAIN')
        if value['exists']:
            allowed = {review['new_fingerprints'][name], review['old_fingerprints'].get(name)}
            require(value['fingerprint'] in allowed, 'CONFIGURATION_CHANGED')


def _read_review(root, document, expected_digest, *, record_consumer=True,
                 approval=None, check_approval=False):
    area = root / AREA
    directory(area)
    review = _load(area / 'review.json')
    require(_digest(review) == expected_digest and review.get('schema') == SCHEMA,
            'FEEDBACK_UPGRADE_REVIEW_CHANGED')
    if check_approval:
        _approval(approval, review)
        approved = area / 'approval.json'
        if approved.exists() or approved.is_symlink():
            require(_load(approved) == approval, 'FEEDBACK_UPGRADE_APPROVAL_CHANGED')
    _base_guard(root, document, review, record_consumer=record_consumer)
    files_tx.inspect(area / 'files', review['files_intent_sha256'])
    return area, review, _state(area, review)


def _receipt(area, review, phase):
    stored = files_tx.inspect(area / 'files', review['files_intent_sha256'])
    return {'schema': SCHEMA, 'status': phase, 'review_sha256': _digest(review),
            'operation_sha256': review['operation_sha256'], 'source_commit': review['source_commit'],
            'files': {key: value for key, value in stored.items() if key != 'captured'},
            'submission_enabled': False, 'economic_authority': False}


def prepare(window_root, *, source_commit, operation_sha256, files, captured, approved_active,
            consumer_snapshot):
    """Capture immutable review while the original service window is paused."""
    with _locked(window_root) as (root, document):
        _source(source_commit)
        require(document['phase'] == 'PAUSED' and document['operation_sha256'] == operation_sha256,
                'FEEDBACK_UPGRADE_WINDOW')
        original = services.inspect_journal(root / 'services.json')
        require(original['phase'] == 'PAUSED', 'WINDOW_NOT_PAUSED')
        require(type(approved_active) in (list, tuple) and len(set(approved_active)) == len(approved_active)
                and set(approved_active) <= set(services.ALLOWED_UNITS), 'FEEDBACK_UPGRADE_ACTIVE_SCOPE')
        old = {name: value['fingerprint'] for name, value in original['units'].items()}
        require(set(services.configured_units()) == set(old), 'UNIT_SCOPE_CHANGED')
        current_snapshot = _consumer_snapshot(consumer_snapshot, source_commit, initial=True)
        consumer_evidence = current_snapshot['consumer_evidence']
        _drained(root, document, consumer_evidence)
        _loaded(old, document['inhibitor_sha256'])
        pair = _pair_before(captured, document['inhibitor_sha256'])
        preflight = consumer.preflight(files=files, preimages=files_tx.preimages(captured),
                release_commit=source_commit, consumer_evidence=consumer_evidence, now_ms=_now())
        require(preflight.get('timer_compatible') is True, 'FEEDBACK_UPGRADE_CONSUMER_INCOMPATIBLE')
        new = {name: old[name] for name in services.UNITS}
        new.update({name: _fingerprint(captured['targets'][name]['path'], hashlib.sha256(files[name]).hexdigest())
                    for name in services.FEEDBACK_UNITS})
        area = root / AREA
        if not area.exists():
            area.mkdir(mode=0o700)
            sync(root)
        directory(area)
        if (area / 'review.json').exists():
            previous = _load(area / 'review.json')['consumer_preflight']
            require(preflight['observed_at_ms'] >= previous['observed_at_ms'] and
                    {k: v for k, v in preflight.items() if k != 'observed_at_ms'} ==
                    {k: v for k, v in previous.items() if k != 'observed_at_ms'},
                    'FEEDBACK_UPGRADE_CONSUMER_CHANGED')
            preflight = previous
        identity = files_tx.prepare(area / 'files', operation_sha256=operation_sha256,
                                   source_commit=source_commit, files=files, captured=captured)
        review = {'schema': SCHEMA, 'purpose': PURPOSE, 'source_commit': source_commit,
                  'operation_sha256': operation_sha256, 'window': document, 'original_journal': original,
                  'journal_sha256': hashlib.sha256(read(root / 'services.json', 128 * 1024)).hexdigest(),
                  'old_fingerprints': old, 'new_fingerprints': new, 'pair_before': pair,
                  'approved_active': sorted(approved_active), 'captured': captured,
                  'recovery_actions': ['continue', 'rollback_to_original'],
                  'rollback_active': sorted(n for n, v in original['units'].items() if v['active'] == 'active'),
                  'files_base64': {name: base64.b64encode(raw).decode() for name, raw in files.items()},
                  'files_intent_sha256': identity, 'consumer_snapshot': current_snapshot,
                  'consumer_preflight': preflight, 'submission_enabled': False, 'economic_authority': False}
        path = area / 'review.json'
        if path.exists():
            require(_load(path) == review, 'FEEDBACK_UPGRADE_REVIEW_CHANGED')
            if not (area / 'state.json').exists():
                require(set(p.name for p in area.iterdir()) == {'files', 'review.json'},
                        'FEEDBACK_UPGRADE_PARTIAL_PREPARE')
                _phase(area, review, 'PREPARED')
            require(_state(area, review) == 'PREPARED' and not (area / 'approval.json').exists(),
                    'FEEDBACK_UPGRADE_ALREADY_APPROVED')
        else:
            require(set(p.name for p in area.iterdir()) == {'files'}, 'FEEDBACK_UPGRADE_PARTIAL_PREPARE')
            _put(path, review)
            _phase(area, review, 'PREPARED')
        return {'schema': SCHEMA, 'review_sha256': _digest(review), 'operation_sha256': operation_sha256,
                'source_commit': source_commit, 'old_scope': sorted(old), 'new_scope': sorted(new),
                'original_active': sorted(n for n, v in original['units'].items() if v['active'] == 'active'),
                'approved_active': review['approved_active'], 'files_intent_sha256': identity,
                'recovery_actions': review['recovery_actions'], 'rollback_active': review['rollback_active'],
                'targets': files_tx.inspect(area / 'files', identity)['targets'],
                'consumer_preflight': preflight, 'activation_authorized': False}


def _restore_active(root, document, area, review, expected, active):
    if not _started(area, review):
        _base_guard(root, document, review)
        _drained(root, document, review['consumer_snapshot']['consumer_evidence'])
        require(set(services.configured_units()) == set(expected), 'UNIT_SCOPE_CHANGED')
        _loaded(expected)
        result = files_tx.inspect(area / 'files', review['files_intent_sha256'])
        require(result['phase'] in {'APPLIED', 'RESTORED'}, 'FEEDBACK_UPGRADE_FILES_INCOMPLETE')
        _put(area / 'start-allowed.json', {'schema': 'DEMO_FEEDBACK_CONFIGURATION_START_BOUNDARY_V1',
                'review_sha256': _digest(review), 'approval_sha256': _digest(_load(area / 'approval.json')),
                'consumer_witness': _initial_witness(area, review)})
    for name in sorted(active, key=lambda n: (n.endswith('.timer'), n)):
        _base_guard(root, document, review)
        files_tx.inspect(area / 'files', review['files_intent_sha256'])
        require(set(services.configured_units()) == set(expected), 'UNIT_SCOPE_CHANGED')
        backend = _loaded(expected, idle=False, allowed_active=active)
        value = backend.inspect(name)
        if value['active'] != 'active':
            backend.change('start', name)
        after = services._settled_observation(backend, name)
        require(after['fingerprint'] == expected[name] and (after['active'] == 'active' or
                name.endswith('.service') and name != 'quantos-demo-executor-stream.service' and
                after['active'] == 'inactive'), 'RESTORE_INCOMPLETE')
    _restored(expected, active)


def advance(window_root, *, expected_digest, action, approval):
    require(action in {'apply', 'rollback'}, 'FEEDBACK_UPGRADE_ACTION')
    with _locked(window_root) as (root, document):
        area, review, phase = _read_review(root, document, expected_digest,
                                         approval=approval, check_approval=True)
        _approval(approval, review)
        require(phase != 'CANCELED_UNCHANGED', 'FEEDBACK_UPGRADE_CANCELED')
        path = area / 'approval.json'
        if path.exists():
            require(_load(path) == approval, 'FEEDBACK_UPGRADE_APPROVAL_CHANGED')
        else:
            require(phase == 'PREPARED', 'FEEDBACK_UPGRADE_APPROVAL_MISSING')
            _put(path, approval)
        if phase == 'ROLLED_BACK':
            require(action == 'rollback', 'FEEDBACK_UPGRADE_TERMINAL')
            _restored(review['old_fingerprints'], review['rollback_active'])
            return _receipt(area, review, phase)
        if phase == 'COMPLETED' and action == 'apply':
            _restored(review['new_fingerprints'], review['approved_active'])
            return _receipt(area, review, phase)
        if action == 'apply':
            require(not phase.startswith('ROLL'), 'FEEDBACK_UPGRADE_ROLLBACK_STARTED')
        elif not phase.startswith('ROLL'):
            _phase(area, review, 'ROLLBACK_PAUSING')
            phase = 'ROLLBACK_PAUSING'
        token = document['inhibitor_sha256']
        guard = lambda: _write_guard(root, document, review, rollback=action == 'rollback')
        if action == 'apply':
            if phase in {'PREPARED', 'INHIBITING'}:
                _loaded(review['old_fingerprints'], token, inhibited=False)
                _phase(area, review, 'INHIBITING')
                window.inhibit.install(token, units=services.ALLOWED_UNITS)
                window.reload_manager()
                _loaded(review['old_fingerprints'], token)
                _phase(area, review, 'APPLYING')
                phase = 'APPLYING'
            if phase == 'APPLYING':
                files_tx.advance(area / 'files', review['files_intent_sha256'], action='apply', guard=guard)
                _phase(area, review, 'VERIFYING')
                phase = 'VERIFYING'
            if phase == 'VERIFYING':
                require(files_tx.inspect(area / 'files', review['files_intent_sha256'])['phase'] == 'APPLIED',
                        'FEEDBACK_UPGRADE_FILES_INCOMPLETE')
                guard()
                window.reload_manager()
                require(set(services.configured_units()) == set(services.ALLOWED_UNITS), 'UNIT_SCOPE_CHANGED')
                _loaded(review['new_fingerprints'], token)
                _phase(area, review, 'RELEASING')
                phase = 'RELEASING'
            if phase == 'RELEASING':
                require(files_tx.inspect(area / 'files', review['files_intent_sha256'])['phase'] == 'APPLIED',
                        'FEEDBACK_UPGRADE_FILES_INCOMPLETE')
                _drained(root, document, review['consumer_snapshot']['consumer_evidence'])
                _loaded(review['new_fingerprints'], token, inhibited=False)
                window.inhibit.remove(token, units=services.ALLOWED_UNITS)
                window.reload_manager()
                _loaded(review['new_fingerprints'])
                _phase(area, review, 'RESTORING')
                phase = 'RESTORING'
            if phase == 'RESTORING':
                _restore_active(root, document, area, review, review['new_fingerprints'], review['approved_active'])
                _phase(area, review, 'COMPLETED')
                phase = 'COMPLETED'
        else:
            if phase == 'ROLLBACK_PAUSING':
                window.inhibit.install(token, units=services.ALLOWED_UNITS)
                window.reload_manager()
                backend = window.backend(token)
                _loaded({n: review['old_fingerprints'][n] for n in services.UNITS}, token,
                        idle=False, allowed_active=services.UNITS)
                for name in sorted(services.ALLOWED_UNITS, key=lambda n: (not n.endswith('.timer'), n)):
                    value = backend.configuration(name, allow_absent=True)
                    if value['exists'] and value['active'] == 'active':
                        backend.change('stop', name)
                guard()
                _phase(area, review, 'ROLLING_BACK')
                phase = 'ROLLING_BACK'
            if phase == 'ROLLING_BACK':
                files_tx.advance(area / 'files', review['files_intent_sha256'], action='rollback', guard=guard)
                window.reload_manager()
                _loaded(review['old_fingerprints'], token)
                _phase(area, review, 'ROLLBACK_RELEASING')
                phase = 'ROLLBACK_RELEASING'
            if phase == 'ROLLBACK_RELEASING':
                require(files_tx.inspect(area / 'files', review['files_intent_sha256'])['phase'] == 'RESTORED',
                        'FEEDBACK_UPGRADE_FILES_INCOMPLETE')
                _loaded(review['old_fingerprints'], token, inhibited=False)
                window.inhibit.remove(token, units=services.ALLOWED_UNITS)
                window.reload_manager()
                _loaded(review['old_fingerprints'])
                _phase(area, review, 'ROLLBACK_RESTORING')
                phase = 'ROLLBACK_RESTORING'
            if phase == 'ROLLBACK_RESTORING':
                active = [n for n, v in review['original_journal']['units'].items() if v['active'] == 'active']
                _restore_active(root, document, area, review, review['old_fingerprints'], active)
                _phase(area, review, 'ROLLED_BACK')
                phase = 'ROLLED_BACK'
        return _receipt(area, review, phase)


def query(window_root, *, expected_digest):
    """Read and check the retained prefix; newly seen receipts are not recorded.

    A complete row awaiting its head requires an approved mutating operation to
    finish. This read never repairs it or implies durable receipt registration.
    """
    with _locked(window_root) as (root, document):
        area, review, phase = _read_review(root, document, expected_digest, record_consumer=False)
        return _receipt(area, review, phase) | {'consumer_observation_recorded': False}


def cancel_prepared(window_root, *, expected_digest):
    with _locked(window_root) as (root, document):
        area, review, phase = _read_review(root, document, expected_digest)
        require(phase in {'PREPARED', 'CANCELED_UNCHANGED'} and not (area / 'approval.json').exists(),
                'FEEDBACK_UPGRADE_CANCEL_FORBIDDEN')
        current = files_tx.inspect(area / 'files', review['files_intent_sha256'])
        require(current['phase'] == 'PREPARED' and set(current['current_states'].values()) == {'BEFORE'},
                'FEEDBACK_UPGRADE_CANCEL_CHANGED')
        require(set(services.configured_units()) == set(review['old_fingerprints']), 'UNIT_SCOPE_CHANGED')
        _loaded(review['old_fingerprints'], document['inhibitor_sha256'])
        if phase != 'CANCELED_UNCHANGED':
            _phase(area, review, 'CANCELED_UNCHANGED')
        return _receipt(area, review, 'CANCELED_UNCHANGED')


def restore_guard(window_root):
    """Called inside the old window lock; never silently reinterpret its journal."""
    area = Path(window_root) / AREA
    if not area.exists() and not area.is_symlink():
        return
    try:
        directory(area)
        review = _load(area / 'review.json')
        phase = _state(area, review)
        require(phase in {'CANCELED_UNCHANGED', 'ROLLED_BACK'}, 'FEEDBACK_UPGRADE_RECOVERY_REQUIRED')
        current = files_tx.inspect(area / 'files', review['files_intent_sha256'])
        require((phase == 'CANCELED_UNCHANGED' and current['phase'] == 'PREPARED' and
                 set(current['current_states'].values()) == {'BEFORE'} and not (area / 'approval.json').exists()) or
                (phase == 'ROLLED_BACK' and current['phase'] == 'RESTORED'), 'FEEDBACK_UPGRADE_RECOVERY_REQUIRED')
        if phase == 'ROLLED_BACK':
            _source(review['source_commit'])
            require(_started(area, review), 'FEEDBACK_UPGRADE_RECOVERY_REQUIRED')
            snapshot, _head_value, pending = _witness_state(area, review)
            require(not pending, 'FEEDBACK_UPGRADE_RECOVERY_REQUIRED')
            fresh = _consumer_snapshot(snapshot, review['source_commit'], started=True)
            # The legacy guard has no authority to advance this witness. New
            # valid appends must first go through approved advance(rollback).
            require(fresh == snapshot, 'FEEDBACK_UPGRADE_RECOVERY_REQUIRED')
            _preflight(review, fresh, started=True)
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError('FEEDBACK_UPGRADE_RECOVERY_REQUIRED') from None
