"""Capture fixed original functional-feedback records without writes or credentials.

The caller owns the original controller/maintenance locks; this helper never
acquires a nested lease or claims an atomic snapshot. Two complete observations
detect changes. The installation state machine separately owns human approval
and whether service restoration has reached the append-only receipt phase.
"""
import hashlib
from itertools import islice
import os
from pathlib import Path

from live.execution.demo_issuer_policy import require, validate_policy
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.validation.demo_submission import validate_registry, registry_sha256
from tools import demo_feedback_upgrade_capture as reader
from tools import demo_functional_cycle as functional
from tools import demo_operator_setup as setup
from tools import demo_supervisor as supervisor
from tools.demo_feedback_publish import hash_value
from tools.demo_issuer import verify_source


SCHEMA = 'DEMO_FEEDBACK_CONSUMER_CAPTURE_V1'
_FIELDS = {'schema', 'consumer_evidence', 'read_identity', 'read_identity_sha256'}


def _stable(info):
    """Directory membership is separate; unrelated parent mtime is not identity."""
    return dict(zip(('dev', 'ino', 'mode', 'uid', 'gid'), (info[0], info[1], *info[5:8])))


def _file(path, identities, *, optional=False, private=False):
    path = Path(path)
    body, info = reader._read_target(path)
    if body is None:
        require(optional, 'FEEDBACK_CONSUMER_CAPTURE_MISSING')
        identities['files'][str(path)] = {'parent': _stable(info), 'file': None, 'sha256': None}
        return None
    parent, leaf = info
    if private:
        require(not leaf[5] & 0o077, 'SUPERVISOR_REQUEST_CUSTODY')
    value = load_json_without_duplicate_keys(body)
    require(type(value) is dict, 'FEEDBACK_CONSUMER_CAPTURE_RECORD')
    identities['files'][str(path)] = {'parent': _stable(parent),
        'file': dict(zip(('dev', 'ino', 'size', 'mtime_ns', 'ctime_ns', 'mode', 'uid', 'gid', 'nlink'), leaf)),
        'sha256': hashlib.sha256(body).hexdigest()}
    return value


def _members(fd, maximum):
    if maximum is None:
        return None
    with os.scandir(fd) as entries:
        names = sorted(entry.name for entry in islice(entries, maximum + 1))
    require(len(names) <= maximum, 'FEEDBACK_CONSUMER_CAPTURE_HISTORY_BUDGET')
    return names


def _directory(path, identities, *, private=False, maximum=None, optional=False):
    path = Path(path)
    parent = opened = None
    try:
        parent = reader._parent(path)
        parent_identity = _stable(reader._identity(os.fstat(parent)))
        try:
            opened = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                             dir_fd=parent)
        except FileNotFoundError:
            require(optional, 'FEEDBACK_CONSUMER_CAPTURE_MISSING')
            try:
                os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                require(_stable(reader._identity(os.fstat(parent))) == parent_identity,
                        'FEEDBACK_CONSUMER_CAPTURE_CHANGED')
                value = {'parent': parent_identity, 'directory': None, 'members': None}
                identities['directories'][str(path)] = value
                return value
            raise ValueError('FEEDBACK_CONSUMER_CAPTURE_CHANGED')
        before = os.fstat(opened)
        reader._protected(before, directory=True)
        if private:
            require(not before.st_mode & 0o077, 'SUPERVISOR_PRIVATE_DIRECTORY')
        identity = _stable(reader._identity(before))
        members = _members(opened, maximum)
        named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        reader._protected(named, directory=True)
        require(identity == _stable(reader._identity(os.fstat(opened))) == _stable(reader._identity(named))
            and parent_identity == _stable(reader._identity(os.fstat(parent)))
            and members == _members(opened, maximum), 'FEEDBACK_CONSUMER_CAPTURE_CHANGED')
        value = {'parent': parent_identity, 'directory': identity, 'members': members}
        identities['directories'][str(path)] = value
        return value
    except OSError:
        raise ValueError('FEEDBACK_CONSUMER_CAPTURE_UNAVAILABLE') from None
    finally:
        if opened is not None: os.close(opened)
        if parent is not None: os.close(parent)


def _history(path, identities, maximum):
    before = _directory(path, identities, private=True, maximum=maximum, optional=True)
    records = {}
    for name in before['members'] or ():
        require(name.endswith('.json'), 'FEEDBACK_CONSUMER_CAPTURE_HISTORY_DIGEST')
        hash_value(name[:-5])
        value = _file(path/name, identities)
        require(name == setup.digest(value)+'.json', 'FEEDBACK_CONSUMER_CAPTURE_HISTORY_DIGEST')
        records[name] = value
    after = _directory(path, identities, private=True, maximum=maximum, optional=True)
    require(before == after, 'FEEDBACK_CONSUMER_CAPTURE_CHANGED')
    return records


def _observe(source_commit):
    identities = {'files': {}, 'directories': {}}
    registry = validate_registry(_file(supervisor.EXECUTOR_REGISTRY, identities))
    issuer_registry = _file(supervisor.ISSUER_REGISTRY, identities)
    policy = validate_policy(_file(supervisor.ISSUER_POLICY, identities))
    require(issuer_registry == registry, 'FUNCTIONAL_FEEDBACK_REGISTRY_MIRROR')
    require(policy['registry_sha256'] == registry_sha256(registry)
        and policy['deployment_sha256'] == registry['deployment_sha256']
        and policy['executor_release_commit'] == source_commit, 'FUNCTIONAL_FEEDBACK_REGISTRY')
    root = functional.SETUP
    _directory(root, identities)
    pointer = _file(root/'active.json', identities, optional=True)
    stage = root
    if pointer is not None:
        require(set(pointer) == {'schema', 'enrollment_sha256', 'result_sha256'}
            and pointer['schema'] == 'DEMO_ACTIVE_ENROLLMENT_V1', 'SUCCESSOR_ACTIVE_POINTER')
        for name in ('enrollment_sha256', 'result_sha256'): hash_value(pointer[name])
        stage = root/'cycles'/pointer['enrollment_sha256']
    _directory(stage, identities)
    installed = _file(stage/'result.json', identities)
    if pointer is not None:
        require(setup.digest(installed) == pointer['result_sha256']
            and installed.get('enrollment_sha256') == pointer['enrollment_sha256'], 'SUCCESSOR_ACTIVE_BINDING')
    spec = setup.spec_check(_file(stage/'spec.json', identities))
    require(installed.get('status') == 'INSTALLED_PENDING_CYCLE'
        and installed.get('source_commit') == spec['source_commit'] == source_commit
        and installed.get('spec_sha256') == setup.digest(spec)
        and policy['account_ref_sha256'] == spec['account_ref_sha256'], 'FUNCTIONAL_FEEDBACK_INSTALLATION')
    _directory(supervisor.STATE, identities, private=True)
    _directory(supervisor.STATE/'tasks', identities, private=True)
    task_hash = installed.get('task_sha256'); hash_value(task_hash)
    task_path = supervisor.STATE/'tasks'/(task_hash+'.json')
    task = _file(task_path, identities, private=True)
    # Preserve read_document's exact raw-byte digest and private-file boundary.
    require(identities['files'][str(task_path)]['sha256'] == task_hash
        and setup.digest(task) == task_hash, 'SUPERVISOR_REQUEST_DIGEST')
    cycle = setup.digest(installed)
    area = supervisor.STATE/'functional-cycle'
    if stage != root: area = area/'cycles'/cycle
    _directory(area, identities, private=True)
    claim = _file(area/'claim.json', identities)
    require(claim.get('cycle_sha256') == cycle and claim.get('source_commit') == source_commit,
            'FUNCTIONAL_FEEDBACK_CYCLE_BINDING')
    result = _file(area/'result.json', identities, optional=True)
    recoveries = _history(area/'recovery-results', identities, 32)
    receipts = _history(area/'feedback-results', identities, 64)
    identities['feedback_directory'] = str(area/'feedback-results')
    evidence = dict(schema='DEMO_FEEDBACK_CONSUMER_EVIDENCE_V1', consumer='functional',
        executor_registry=registry, issuer_registry=issuer_registry, policy=policy,
        spec=spec, installation=installed, task=task, claim=claim, initial_result=result,
        recovery_results=recoveries, feedback_results=receipts)
    return dict(schema=SCHEMA, consumer_evidence=evidence, read_identity=identities,
                read_identity_sha256=setup.digest(identities))


def capture(*, source_commit):
    """Read only fixed records under the caller's original maintenance locks."""
    require(os.geteuid() == 0, 'FEEDBACK_CONSUMER_CAPTURE_ROOT_REQUIRED')
    verify_source(source_commit)
    first = _observe(source_commit)
    second = _observe(source_commit)
    require(first == second, 'FEEDBACK_CONSUMER_CAPTURE_CHANGED')
    return first


def _snapshot(value):
    require(type(value) is dict and set(value) == _FIELDS and value['schema'] == SCHEMA
        and type(value['consumer_evidence']) is dict
        and value['consumer_evidence'].get('schema') == 'DEMO_FEEDBACK_CONSUMER_EVIDENCE_V1'
        and value['consumer_evidence'].get('consumer') == 'functional'
        and type(value['read_identity']) is dict
        and set(value['read_identity']) == {'files', 'directories', 'feedback_directory'}
        and type(value['read_identity']['files']) is dict and type(value['read_identity']['directories']) is dict
        and setup.digest(value['read_identity']) == value['read_identity_sha256'],
        'FEEDBACK_CONSUMER_CAPTURE_SNAPSHOT')


def recheck(expected_snapshot, *, source_commit):
    """Re-read every original byte, metadata identity and bounded history set."""
    _snapshot(expected_snapshot)
    fresh = capture(source_commit=source_commit)
    require(fresh == expected_snapshot, 'FEEDBACK_CONSUMER_CAPTURE_CHANGED')
    return fresh


def recheck_started(expected_snapshot, *, source_commit):
    """Allow only receipts appended after the caller's persisted service-start gate.

    This does not establish that gate, approval, locking or phase. The original
    strict recheck remains mandatory before the first configuration/service change.
    """
    _snapshot(expected_snapshot)
    fresh = capture(source_commit=source_commit)
    old_evidence = expected_snapshot['consumer_evidence']; evidence = fresh['consumer_evidence']
    require({k: v for k, v in old_evidence.items() if k != 'feedback_results'} ==
        {k: v for k, v in evidence.items() if k != 'feedback_results'}, 'FEEDBACK_CONSUMER_CAPTURE_CHANGED')
    old_identity = expected_snapshot['read_identity']; identity = fresh['read_identity']
    directory = identity['feedback_directory']
    require(old_identity['feedback_directory'] == directory
        and set(old_identity['directories']) == set(identity['directories']), 'FEEDBACK_CONSUMER_CAPTURE_CHANGED')
    for path, before in old_identity['directories'].items():
        after = identity['directories'][path]
        if path != directory:
            require(before == after, 'FEEDBACK_CONSUMER_CAPTURE_CHANGED')
        else:
            require(before['parent'] == after['parent']
                and (before['directory'] is None or before['directory'] == after['directory'])
                and set(before['members'] or ()) <= set(after['members'] or ()), 'FEEDBACK_CONSUMER_CAPTURE_CHANGED')
    for path, before in old_identity['files'].items():
        require(identity['files'].get(path) == before, 'FEEDBACK_CONSUMER_CAPTURE_CHANGED')
    new_files = set(identity['files']) - set(old_identity['files'])
    added = set(evidence['feedback_results']) - set(old_evidence['feedback_results'])
    require(new_files == {str(Path(directory)/name) for name in added}
        and all(evidence['feedback_results'].get(name) == value
                for name, value in old_evidence['feedback_results'].items()), 'FEEDBACK_CONSUMER_CAPTURE_CHANGED')
    now_ms = supervisor.now_ms()
    cycle = setup.digest(evidence['installation'])
    completed = functional.feedback_completion_records(evidence['initial_result'], evidence['recovery_results'],
        cycle=cycle, source_commit=source_commit, now_ms=now_ms)
    completion = functional.completion_digest(completed)
    for name in added:
        value = evidence['feedback_results'][name]
        require(value.get('completion_sha256') == completion
            and value.get('status') in ('RESEARCH_FEEDBACK_DELIVERED', 'RESEARCH_FEEDBACK_DELIVERY_FAILED')
            and value.get('submission_enabled') is False, 'FUNCTIONAL_FEEDBACK_RECEIPT_SCOPE')
        functional.delivered_feedback_records({name: value}, completion=completion,
            cycle=cycle, source_commit=source_commit, now_ms=now_ms)
    functional.delivered_feedback_records(evidence['feedback_results'], completion=completion,
        cycle=cycle, source_commit=source_commit, now_ms=now_ms)
    return fresh
