"""Fixed public originals for a read-only Gold checkpoint.

Capture is a trusted root coordinator API, under the caller's actual writer
barrier. A callback or a cut document does not establish that barrier. Restore
validates archived bindings and content, never today's /etc or current send
permission. Opaque handles constrain callers; they do not defend against
trusted Python code forging objects. No source-migration contract is inferred.
"""
from base64 import b64decode, b64encode
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import stat

from live.execution.demo_authority import _read_root_owned_json, _require_protected
from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require, historical_policy_binding
from live.execution.demo_market_partition_runtime import validate_installed_records
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.validation.demo_submission import validate_registry
from tools import demo_supervisor as supervisor
from tools.demo_operating_bundle import read_private as _read_private
from tools.gold_pilot_retention_store import canonical

SCHEMA = 'GOLD_CHECKPOINT_FIXED_PUBLIC_V1'
PATHS = dict(executor_registry=Path('/etc/quantos/demo-submission/authority.json'),
    issuer_registry=Path('/etc/quantos/demo-issuer/authority.json'),
    policy=Path('/etc/quantos/demo-issuer/policy.json'),
    executor_partition=Path('/etc/quantos/demo-submission/market-partition.json'),
    issuer_partition=Path('/etc/quantos/demo-issuer/market-partition.json'),
    partition_intent=Path('/etc/quantos/demo-issuer/market-partition-intent.json'),
    activation=Path('/etc/quantos/demo-market-partition-activation.json'))
RELEASES = Path('/opt/quantos/releases')
PARTITION_HISTORY = Path('/var/lib/quantos-demo-controller/market-partition-installations')
MAXIMUM = 1024 * 1024
_STAT = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink',
         'st_size', 'st_mtime_ns', 'st_ctime_ns')
_PARTITION = ('executor_partition', 'issuer_partition', 'partition_intent', 'activation')
_TOKEN = object()


def _hash(value, length=64):
    require(type(value) is str and re.fullmatch('[a-f0-9]{' + str(length) + '}', value),
            'GOLD_FIXED_PUBLIC_HASH')
    return value


def _identity(info):
    return {name: getattr(info, name) for name in _STAT}


def _parents(path):
    """Open the entire parent chain; missing parent is never leaf absence."""
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'GOLD_FIXED_PUBLIC_PATH')
    current = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    result = []
    try:
        for part in ('', *path.parts[1:-1]):
            if part:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=current)
                os.close(current); current = child
            info = os.fstat(current); _require_protected(info, directory=True)
            result.append({key: value for key, value in _identity(info).items() if key in _STAT[:5]})
        return current, result
    except BaseException:
        os.close(current)
        raise


def _leaf(path, *, optional=False, private=False):
    descriptor, parents = _parents(path)
    try:
        try:
            before = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            require(optional, 'GOLD_FIXED_PUBLIC_REQUIRED')
            return dict(path=str(path), parents=parents, identity=None, body=None)
        identity = _identity(before)
        if private:
            raw = _read_private(path, maximum=3 * 65536)
        else:
            raw = _read_root_owned_json(path, file_check=lambda info: require(
                _identity(info) == identity, 'GOLD_FIXED_PUBLIC_CHANGED'))
        after = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        require(identity == _identity(after), 'GOLD_FIXED_PUBLIC_CHANGED')
        current, located_parents = _parents(path)
        try:
            require(located_parents == parents and identity == _identity(os.stat(
                path.name, dir_fd=current, follow_symlinks=False)), 'GOLD_FIXED_PUBLIC_CHANGED')
        finally: os.close(current)
        return dict(path=str(path), parents=parents, identity=identity, body=b64encode(raw).decode('ascii'))
    finally:
        os.close(descriptor)


def _history():
    parent, parents = _parents(PARTITION_HISTORY)
    child = None
    try:
        try:
            child = os.open(PARTITION_HISTORY.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=parent)
        except FileNotFoundError:
            return dict(path=str(PARTITION_HISTORY), parents=parents, identity=None, members=[])
        before = os.fstat(child); _require_protected(before, directory=True)
        members = []
        with os.scandir(child) as rows:
            for row in rows:
                require(len(members) < 128, 'GOLD_FIXED_PUBLIC_HISTORY_BOUND')
                require(re.fullmatch('[a-f0-9]{64}', row.name), 'GOLD_FIXED_PUBLIC_HISTORY_MEMBER')
                info = row.stat(follow_symlinks=False); _require_protected(info, directory=True)
                members.append(dict(name=row.name, identity={k: v for k, v in _identity(info).items() if k in _STAT[:5]}))
        require(_identity(before) == _identity(os.fstat(child)), 'GOLD_FIXED_PUBLIC_HISTORY_CHANGED')
        return dict(path=str(PARTITION_HISTORY), parents=parents,
            identity={k: v for k, v in _identity(before).items() if k in _STAT[:5]},
            members=sorted(members, key=lambda row: row['name']))
    finally:
        if child is not None: os.close(child)
        os.close(parent)


def _raw(item, name):
    _fields(item, 'path parents identity body')
    _parent_metadata(item['parents'])
    if item['identity'] is None:
        require(item['body'] is None and name in _PARTITION, 'GOLD_FIXED_PUBLIC_REQUIRED')
        return None
    identity = item['identity']; _fields(identity, ' '.join(_STAT))
    require(all(type(identity[key]) is int for key in _STAT)
        and identity['st_dev'] >= 0 and identity['st_ino'] > 0 and identity['st_uid'] == 0
        and identity['st_gid'] >= 0 and stat.S_ISREG(identity['st_mode'])
        and not identity['st_mode'] & 0o022 and identity['st_nlink'] == 1,
        'GOLD_FIXED_PUBLIC_CUSTODY')
    maximum = 3 * 65536 if name == 'partition_intent' else 65536
    require(type(item['body']) is str and len(item['body']) <= (maximum + 2) // 3 * 4,
            'GOLD_FIXED_PUBLIC_SIZE')
    try: raw = b64decode(item['body'], validate=True)
    except Exception: raise ValueError('GOLD_FIXED_PUBLIC_ENCODING') from None
    require(0 < len(raw) == identity['st_size'] <= maximum
        and b64encode(raw).decode('ascii') == item['body'], 'GOLD_FIXED_PUBLIC_SIZE')
    return raw


def _parent_metadata(parents):
    require(type(parents) is list and 1 <= len(parents) <= 64, 'GOLD_FIXED_PUBLIC_PARENTS')
    for row in parents:
        _fields(row, ' '.join(_STAT[:5]))
        require(all(type(value) is int and value >= 0 for value in row.values())
            and row['st_ino'] > 0 and row['st_uid'] == 0 and stat.S_ISDIR(row['st_mode'])
            and not row['st_mode'] & 0o022, 'GOLD_FIXED_PUBLIC_PARENT_CUSTODY')


def _policy(policy, registry, source, account):
    from live.execution.demo_gold_budget_scope import is_scope, historical_scope_binding
    from live.execution.demo_gold_future_template import SCHEMA as FUTURE, historical_template_binding
    if is_scope(policy): historical_scope_binding(policy, registry)
    elif policy.get('schema') == FUTURE: historical_template_binding(policy, registry)
    else: historical_policy_binding(policy, registry)
    require(policy['executor_release_commit'] == source and policy['account_ref_sha256'] == account,
            'GOLD_FIXED_PUBLIC_POLICY_BINDING')


def _contents(snapshot, at):
    require(type(snapshot) is dict and len(canonical(snapshot)) <= MAXIMUM, 'GOLD_FIXED_PUBLIC_SIZE')
    _fields(snapshot, 'schema source_commit operation_sha256 account_ref_sha256 captured_at_ms '
        'partition_status files partition_history submission_enabled economic_authority')
    require(snapshot['schema'] == SCHEMA and type(at) is int and at > 0
        and type(snapshot['captured_at_ms']) is int and snapshot['captured_at_ms'] > 0
        and snapshot['submission_enabled'] is snapshot['economic_authority'] is False,
        'GOLD_FIXED_PUBLIC_SCHEMA')
    source = _hash(snapshot['source_commit'], 40); _hash(snapshot['operation_sha256'])
    account = _hash(snapshot['account_ref_sha256']); files = snapshot['files']
    require(type(files) is dict and set(files) == set(PATHS) | {'release_manifest'}, 'GOLD_FIXED_PUBLIC_FILES')
    paths = {**PATHS, 'release_manifest': RELEASES / source / 'evidence/l2_release_evidence_manifest.json'}
    documents = {}
    for name, item in files.items():
        raw = _raw(item, name)
        require(item['path'] == str(paths[name]), 'GOLD_FIXED_PUBLIC_PATH')
        documents[name] = None if raw is None else decode(raw)
    registry = validate_registry(documents['executor_registry'])
    require(canonical(registry) == canonical(documents['issuer_registry']), 'GOLD_FIXED_PUBLIC_REGISTRY_MIRROR')
    _policy(documents['policy'], registry, source, account)
    for name, item in files.items():
        if item['identity'] is None: continue
        gid = registry['executor_uid'] if name.startswith('executor') else (
            0 if name in ('activation', 'partition_intent', 'release_manifest') else registry['issuer_uid'])
        mode = 0o600 if name == 'partition_intent' else (0o644 if name in ('activation', 'release_manifest') else 0o640)
        require(item['identity']['st_gid'] == gid and stat.S_IMODE(item['identity']['st_mode']) == mode,
                'GOLD_FIXED_PUBLIC_ROLE_CUSTODY')
    manifest = documents['release_manifest']
    require(type(manifest) is dict and type(manifest.get('schema_version')) is int
        and manifest['schema_version'] == 1 and manifest.get('readiness') == 'L2_DRY_RUN_ONLY',
        'GOLD_FIXED_PUBLIC_RELEASE_MANIFEST')
    history = snapshot['partition_history']; _fields(history, 'path parents identity members')
    require(history['path'] == str(PARTITION_HISTORY) and type(history['members']) is list
        and len(history['members']) <= 128, 'GOLD_FIXED_PUBLIC_HISTORY')
    _parent_metadata(history['parents'])
    if history['identity'] is None:
        require(not history['members'], 'GOLD_FIXED_PUBLIC_HISTORY')
    else: _parent_metadata([history['identity']])
    names = []
    for row in history['members']:
        _fields(row, 'name identity'); names.append(_hash(row['name'])); _parent_metadata([row['identity']])
    require(names == sorted(set(names)), 'GOLD_FIXED_PUBLIC_HISTORY')
    present = [documents[name] is not None for name in _PARTITION]
    if not any(present):
        require(snapshot['partition_status'] == 'VERIFIED_ABSENT' and not names,
                'GOLD_FIXED_PUBLIC_ABSENCE_UNPROVEN')
        return dict(status='VERIFIED_ABSENT')
    require(all(present) and snapshot['partition_status'] == 'INSTALLED', 'GOLD_FIXED_PUBLIC_PARTIAL_INSTALL')
    record = documents['executor_partition']; activation = documents['activation']; intent = documents['partition_intent']
    require(canonical(record) == canonical(documents['issuer_partition']), 'GOLD_FIXED_PUBLIC_PARTITION_MIRROR')
    validate_installed_records(record, activation, registry=registry, source_commit=source, as_of_ms=at)
    require(record['candidate']['binding']['account_ref_sha256'] == account
        and record['candidate_sha256'] in names, 'GOLD_FIXED_PUBLIC_PARTITION_ACCOUNT_HISTORY')
    _fields(intent, 'schema source_commit candidate_sha256 operator_approval_sha256 record revalidation ready_result')
    require(intent['schema'] == 'DEMO_MARKET_PARTITION_INSTALL_INTENT_V2'
        and intent['source_commit'] == source and canonical(intent['record']) == canonical(record)
        and intent['candidate_sha256'] == record['candidate_sha256']
        and intent['operator_approval_sha256'] == _digest(record['operator_approval'])
        and _digest(intent['revalidation']) == record['revalidation_sha256']
        and _digest(intent['ready_result']) == activation['ready_result_sha256'], 'GOLD_FIXED_PUBLIC_INTENT')
    return dict(status='INSTALLED', record=deepcopy(record), activation=deepcopy(activation), registry=deepcopy(registry))


def _read(source, operation, account, at):
    paths = {**PATHS, 'release_manifest': RELEASES / source / 'evidence/l2_release_evidence_manifest.json'}
    files = {name: _leaf(path, optional=name in _PARTITION, private=name == 'partition_intent')
             for name, path in paths.items()}
    present = [files[name]['identity'] is not None for name in _PARTITION]
    require(all(present) or not any(present), 'GOLD_FIXED_PUBLIC_PARTIAL_INSTALL')
    value = dict(schema=SCHEMA, source_commit=source, operation_sha256=operation, account_ref_sha256=account,
        captured_at_ms=at, partition_status='INSTALLED' if all(present) else 'VERIFIED_ABSENT',
        files=files, partition_history=_history(), submission_enabled=False, economic_authority=False)
    _contents(value, at)
    return value


def capture(*, source_commit, operation_sha256, account_ref_sha256, guard):
    require(os.geteuid() == 0 and callable(guard), 'GOLD_FIXED_PUBLIC_ROOT_GUARD_REQUIRED')
    _hash(source_commit, 40); _hash(operation_sha256); _hash(account_ref_sha256)
    from tools.gold_strategy_controller import observation_configuration
    guard(); registry, policy = observation_configuration(source_commit)
    at = supervisor.now_ms()
    first = _read(source_commit, operation_sha256, account_ref_sha256, at)
    require(canonical(registry) == canonical(decode(_raw(first['files']['executor_registry'], 'executor_registry')))
        and canonical(policy) == canonical(decode(_raw(first['files']['policy'], 'policy'))), 'GOLD_FIXED_PUBLIC_CHANGED')
    guard(); observation_configuration(source_commit)
    second = _read(source_commit, operation_sha256, account_ref_sha256, at)
    require(canonical(first) == canonical(second) and supervisor.now_ms() >= at, 'GOLD_FIXED_PUBLIC_CHANGED')
    guard()
    return first


def recheck(snapshot, *, guard):
    _contents(snapshot, snapshot['captured_at_ms'])
    fresh = capture(source_commit=snapshot['source_commit'], operation_sha256=snapshot['operation_sha256'],
        account_ref_sha256=snapshot['account_ref_sha256'], guard=guard)
    require(fresh['captured_at_ms'] >= snapshot['captured_at_ms'], 'GOLD_FIXED_PUBLIC_CLOCK_REVERSED')
    fresh['captured_at_ms'] = snapshot['captured_at_ms']
    require(canonical(fresh) == canonical(snapshot), 'GOLD_FIXED_PUBLIC_CHANGED')
    return deepcopy(snapshot)


def _binding(snapshot, public, cut):
    _contents(snapshot, snapshot['captured_at_ms'])
    _fields(public, 'schema source_commit registry account_ref_sha256 base_runtime current_runtime records')
    _fields(cut, 'schema operation_sha256 source_commit registry_sha256 public_bundle_sha256 roles '
        'writer_barrier_sha256 opening_at_ms submission_enabled economic_authority')
    require(public['schema'] == 'GOLD_CHECKPOINT_PUBLIC_CONTEXT_V1' and type(public['records']) is dict
        and 'fixed_public' in public['records'] and canonical(public['records']['fixed_public']) == canonical(snapshot)
        and public['source_commit'] == snapshot['source_commit'] == cut['source_commit']
        and public['account_ref_sha256'] == snapshot['account_ref_sha256']
        and canonical(public['registry']) == canonical(decode(_raw(snapshot['files']['executor_registry'], 'executor_registry'))),
        'GOLD_FIXED_PUBLIC_CONTEXT')
    validate_registry(public['registry'])
    require(cut['schema'] == 'GOLD_CHECKPOINT_CUT_REQUEST_V1' and cut['operation_sha256'] == snapshot['operation_sha256']
        and cut['registry_sha256'] == _digest(public['registry']) and cut['public_bundle_sha256'] == _digest(public)
        and type(cut['roles']) is list and cut['roles'] and all(type(role) is str for role in cut['roles'])
        and len(cut['roles']) == len(set(cut['roles'])) and set(cut['roles']) <= {'executor', 'issuer', 'controller', 'research'}
        and type(cut['opening_at_ms']) is int and 0 < cut['opening_at_ms'] <= snapshot['captured_at_ms']
        and cut['submission_enabled'] is cut['economic_authority'] is False, 'GOLD_FIXED_PUBLIC_CUT')
    _hash(cut['writer_barrier_sha256'])


class VerifiedPublic:
    __slots__ = ('_snapshot', '_public', '_cut', '_guard', '_archive')

    def __init__(self, token, snapshot, public, cut, *, guard=None, archive=None):
        require(token is _TOKEN, 'GOLD_FIXED_PUBLIC_VERIFIED_HANDLE_REQUIRED')
        self._snapshot = deepcopy(snapshot); self._public = deepcopy(public); self._cut = deepcopy(cut)
        self._guard = guard; self._archive = deepcopy(archive)

    @property
    def source_commit(self): return self._snapshot['source_commit']
    @property
    def account_ref_sha256(self): return self._snapshot['account_ref_sha256']
    @property
    def snapshot_sha256(self): return _digest(self._snapshot)
    @property
    def public_bundle_sha256(self): return _digest(self._public)
    @property
    def cut_request_sha256(self): return _digest(self._cut)

    def recheck(self):
        _binding(self._snapshot, self._public, self._cut)
        if self._archive is None: recheck(self._snapshot, guard=self._guard)
        else: _archive_binding(self._snapshot, self._public, self._cut, **self._archive)
        return self

    def partition_at(self, as_of_ms):
        self.recheck()
        return _contents(self._snapshot, as_of_ms)


def require_verified(value, *, source_commit, account_ref_sha256):
    require(type(value) is VerifiedPublic and value.source_commit == source_commit
        and value.account_ref_sha256 == account_ref_sha256, 'GOLD_FIXED_PUBLIC_VERIFIED_HANDLE_REQUIRED')
    return value.recheck()


def bind_captured(snapshot, *, public_bundle, cut_request, guard):
    _binding(snapshot, public_bundle, cut_request); recheck(snapshot, guard=guard)
    return VerifiedPublic(_TOKEN, snapshot, public_bundle, cut_request, guard=guard)


def _archive_binding(snapshot, public, cut, *, inventory, role_receipt, index_entries):
    from tools import demo_gold_namespace_checkpoint as native
    from tools import demo_gold_research_inventory as research
    _binding(snapshot, public, cut)
    research_role = type(inventory) is dict and inventory.get('schema') == research.SCHEMA
    _fields(inventory, 'schema operation_sha256 cut_request_sha256 namespace source_commit registry_sha256 '
        'account_ref_sha256 pilot_identity source_root index semantic_view component_bounds files directories '
        'payload_bytes source_attachment_sha256 source_dependencies submission_enabled economic_authority'
        + (' research_layout' if research_role else ''))
    _fields(role_receipt, 'schema operation_sha256 cut_request_sha256 namespace source_commit registry_sha256 '
        'inventory_sha256 semantic_view_sha256 source_attachment_sha256 before_sha256 after_sha256 '
        'started_at_ms completed_at_ms stable worker_identity submission_enabled economic_authority')
    require(inventory['schema'] == (research.SCHEMA if research_role else native.SCHEMA)
        and (not research_role or inventory['namespace'] == 'research') and inventory['namespace'] in cut['roles']
        and inventory['cut_request_sha256'] == _digest(cut)
        and inventory['operation_sha256'] == snapshot['operation_sha256']
        and inventory['source_commit'] == snapshot['source_commit']
        and inventory['account_ref_sha256'] == snapshot['account_ref_sha256']
        and inventory['registry_sha256'] == _digest(public['registry'])
        and inventory['submission_enabled'] is inventory['economic_authority'] is False,
        'GOLD_FIXED_PUBLIC_INVENTORY')
    if research_role:
        layout = research._scope(inventory['research_layout'], public, cut)
        uid = research.original_custody(inventory, layout=layout)
        research.check_index_entries(inventory, index_entries)
    else:
        uid = native._original_custody(inventory, public)
    worker = role_receipt['worker_identity']
    _fields(worker, 'uid gid groups pid start_ticks')
    require(all(type(worker[key]) is int and worker[key] >= 0 for key in ('uid', 'gid', 'pid', 'start_ticks'))
        and worker['uid'] == worker['gid'] == uid and worker['pid'] > 0 and worker['start_ticks'] > 0
        and type(worker['groups']) is list and all(type(g) is int for g in worker['groups'])
        and worker['groups'] == sorted(set(worker['groups'])) and set(worker['groups']) <= {uid},
        'GOLD_FIXED_PUBLIC_RECEIPT')
    require(role_receipt['schema'] == native.RECEIPT_SCHEMA and role_receipt['stable'] is True
        and role_receipt['inventory_sha256'] == _digest(inventory)
        and role_receipt['semantic_view_sha256'] == _digest(inventory['semantic_view'])
        and all(role_receipt[key] == inventory[key] for key in ('namespace', 'operation_sha256',
            'cut_request_sha256', 'source_commit', 'registry_sha256', 'source_attachment_sha256'))
        and type(role_receipt['started_at_ms']) is int and type(role_receipt['completed_at_ms']) is int
        and 0 < role_receipt['started_at_ms'] <= role_receipt['completed_at_ms']
        and role_receipt['submission_enabled'] is role_receipt['economic_authority'] is False,
        'GOLD_FIXED_PUBLIC_RECEIPT')
    require(type(index_entries) is list and type(inventory['index']) is list
        and all(type(inventory[key]) is int and inventory[key] >= 0 for key in ('files', 'directories', 'payload_bytes'))
        and len(index_entries) == inventory['files'] + inventory['directories']
        and native._source_digest(index_entries) == _hash(role_receipt['before_sha256']) == role_receipt['after_sha256'],
        'GOLD_FIXED_PUBLIC_INDEX')
    offset = 0; previous = None
    for number, descriptor in enumerate(inventory['index'], 1):
        _fields(descriptor, 'ordinal name sha256 bytes entries first_key last_key')
        require(type(descriptor['ordinal']) is int and descriptor['ordinal'] == number
            and descriptor['name'] == f'index/{number:06d}.jsonl'
            and type(descriptor['entries']) is int and 0 < descriptor['entries'] <= native.MAX_INDEX_ENTRIES
            and type(descriptor['bytes']) is int and 0 < descriptor['bytes'] <= native.MAX_INDEX_BYTES,
            'GOLD_FIXED_PUBLIC_INDEX')
        rows = index_entries[offset:offset + descriptor['entries']]; offset += len(rows)
        raw = b''.join(canonical(row) for row in rows)
        require(len(rows) == descriptor['entries'] and len(raw) == descriptor['bytes']
            and hashlib.sha256(raw).hexdigest() == _hash(descriptor['sha256'])
            and rows[0]['relative'] == descriptor['first_key'] and rows[-1]['relative'] == descriptor['last_key'],
            'GOLD_FIXED_PUBLIC_INDEX')
        for row in rows:
            require(type(row.get('relative')) is str and (previous is None or previous < row['relative']),
                    'GOLD_FIXED_PUBLIC_INDEX')
            previous = row['relative']
    require(offset == len(index_entries), 'GOLD_FIXED_PUBLIC_INDEX')


def validate_archived(snapshot, *, public_bundle, cut_request, inventory, role_receipt, index_entries, as_of_ms):
    archive = dict(inventory=inventory, role_receipt=role_receipt, index_entries=index_entries)
    _archive_binding(snapshot, public_bundle, cut_request, **archive)
    _contents(snapshot, as_of_ms)
    return VerifiedPublic(_TOKEN, snapshot, public_bundle, cut_request, archive=archive)
