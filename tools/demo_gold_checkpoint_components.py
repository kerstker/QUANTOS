"""Read-only BAR checkpoint components using their original native validators.

The checkpoint caller holds the writer barrier and inventories the entire root.
Execution context and installed partition evidence come from independent native
book replay; this module does not turn caller JSON into execution authority.
"""
from copy import deepcopy
import hashlib
import os
from itertools import islice
from pathlib import Path
import re
from types import SimpleNamespace

from live.execution.demo_batch_risk import _digest, _fields
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.demo_resident_recovery import _check_path
from tools.gold_pilot_install import _read_target
from tools import gold_bar_pilot_capacity as capacity

SCHEMA = 'GOLD_CHECKPOINT_BAR_COMPONENTS_V1'
MISSING = object()
HASH = re.compile('[a-f0-9]{64}')
ORDINARY = ('strategy-cash-work', 'strategy-cash', 'strategy-bar-reported-captures', 'strategy-bar-feedback')


def require(ok, reason):
    if not ok:
        raise ValueError('GOLD_CHECKPOINT_BAR_'+reason)


def present(path):
    return path.exists() or path.is_symlink()


def active(root):
    return capacity.state_present(root) or any(present(Path(root)/name) for name in
        (*ORDINARY, 'strategy-bar-tails', 'strategy-bar-history-work', 'strategy-bar-observation-work',
            'strategy-bar-rotation', 'strategy-bar-history', 'strategy-bar-observations'))


def bounds(*, public, original, namespace):
    """Original finite episode limits; cash shares the ordinary outer bucket."""
    from tools.gold_bar_episode_tail import MAX_RECORDS
    require(namespace in ('executor', 'controller'), 'ROLE_ROOT')
    cap, _, _ = rotation(public, original)
    automatic = public['base_runtime']['schema'] == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'
    count = (32 if automatic else 1) if cap is None else cap['max_episodes']
    def bound(files, directories, size):
        return dict(max_files=files, max_directories=directories, max_bytes=size)
    result = {}
    if cap is not None:
        allocations = cap['allocation_max_records']
        result['bar_capacity'] = bound(3+2*allocations+cap['max_rotations'], 12,
            2*32768+2*allocations*cap['allocation_record_bytes']+cap['rotation_total_bytes'])
    elif automatic:
        result['bar_capacity'] = bound(31, 2, 31*capacity.ROTATION_BYTES)
    if namespace == 'executor':
        if automatic:
            result['bar_history'] = bound(count*10000, count*10000+1,
                256*1024*1024 if cap is None else cap['history_total_bytes'])
            result['bar_observations'] = bound(count*10000, count*10000+1,
                256*1024*1024 if cap is None else cap['observation_total_bytes'])
        # The native tail admits 128 events and one snapshot per immutable
        # prefix (including empty), plus its one binding. No caller quota.
        result['bar_tails'] = bound(count*(2*MAX_RECORDS+2), 1+4*count,
            count*(2*MAX_RECORDS+2)*200000)
    return result


def component(relative, namespace, *, directory):
    """Classify only fixed Gold families; unknown names stay rejected."""
    parts = Path(relative).parts; first = parts[0]
    if namespace == 'executor' and re.fullmatch(r'bar-exit-trigger-[a-f0-9]{64}\.json', relative):
        require(not directory, 'TRIGGER_PATH')
        return 'ordinary'
    if namespace == 'executor' and first in ('strategy-bar-history', 'strategy-bar-observations',
            'strategy-bar-history-work', 'strategy-bar-observation-work'):
        from tools.gold_bar_worker_inputs import OBSERVATION_FILES, JOURNAL_FILES
        history = first in ('strategy-bar-history', 'strategy-bar-history-work')
        history_files = ('receipt.json', 'response.json', 'response.wire', 'request.json', 'summary.json',
            'split-plan.json', 'attempt-1-request.json', 'attempt-1-error.json', 'attempt-2-request.json', 'attempt-2-error.json')
        require(directory and (len(parts) == 1 or len(parts) == 2 and HASH.fullmatch(parts[1])
            or not history and len(parts) == 3 and HASH.fullmatch(parts[1]) and parts[2] == 'journal')
            or not directory and len(parts) >= 3 and HASH.fullmatch(parts[1]) and
                (len(parts) == 3 and parts[2] in (history_files if history else OBSERVATION_FILES)
                 or not history and len(parts) == 4 and parts[2] == 'journal' and parts[3] in JOURNAL_FILES),
            'FIXED_OBSERVATION_PATH')
        # These legacy fixed inputs stay inside the original ordinary transport
        # bucket; their own native readers still enforce original input limits.
        return 'ordinary'
    if namespace == 'controller' and first == 'strategy-bar-rotation':
        require(directory and (len(parts) == 1 or len(parts) == 2 and HASH.fullmatch(parts[1]))
            or not directory and len(parts) == 3 and HASH.fullmatch(parts[1])
                and HASH.fullmatch(Path(relative).stem) and Path(relative).suffix == '.json', 'ROTATION_PATH')
        return 'bar_capacity'
    if namespace == 'executor' and first in ORDINARY:
        require((directory and (len(parts) == 1 or len(parts) >= 2 and HASH.fullmatch(parts[1])))
            or (not directory and len(parts) >= 3 and HASH.fullmatch(parts[1]) and Path(relative).suffix == '.json'),
            'CASH_PATH')
        return 'ordinary'
    if namespace == 'executor' and first == 'strategy-bar-tails':
        require((directory and (len(parts) == 1 or len(parts) == 2 and HASH.fullmatch(parts[1])
            or len(parts) == 3 and HASH.fullmatch(parts[1]) and parts[2] in ('binding', 'events', 'snapshots')))
            or (not directory and len(parts) == 4 and HASH.fullmatch(parts[1])
                and parts[2] in ('binding', 'events', 'snapshots')
                and Path(relative).suffix == '.json' and HASH.fullmatch(Path(relative).stem)), 'TAIL_PATH')
        return 'bar_tails'
    if first in (capacity.MARKER, capacity.AUDIT, capacity.PARENT) and namespace in ('executor', 'controller'):
        if first == capacity.MARKER:
            require(len(parts) == 1 and not directory, 'CAPACITY_PATH')
        if first == capacity.PARENT and len(parts) >= 3 and parts[2] in ('history', 'observations'):
            require(namespace == 'executor' or directory and len(parts) == 3, 'CAPACITY_PATH')
            if namespace == 'executor':
                return 'bar_history' if parts[2] == 'history' else 'bar_observations'
        return 'bar_capacity'
    return None


def _read(path, uid, maximum=16_000_000):
    return decode(_read_target(path, uid=uid, maximum=maximum))


def _members(area, uid, maximum, *, expected=None):
    _check_path(area, uid, directory=True)
    paths = list(islice(area.iterdir(), maximum+1))
    require(len(paths) <= maximum, 'MEMBER_BUDGET')
    if expected is not None:
        require({p.name for p in paths} == set(expected), 'MEMBER_SET')
    return paths


def _hash_records(area, uid, maximum, *, byte_limit=16_000_000):
    result = {}
    for path in _members(area, uid, maximum):
        require(path.suffix == '.json' and HASH.fullmatch(path.stem), 'RECORD_NAME')
        value = _read(path, uid, byte_limit)
        require(_digest(value) == path.stem, 'RECORD_HASH')
        result[path.stem] = value
    return result


def _component_members(root, runtimes, uid):
    identities = {_digest(value) for value in runtimes}
    for family in (*ORDINARY, 'strategy-bar-tails'):
        parent = root/family
        if not present(parent):
            continue
        children = _members(parent, uid, len(identities))
        require({p.name for p in children} <= identities, 'EPISODE_OUTSIDE_ROTATION')
        for path in children:
            _check_path(path, uid, directory=True)
            if family == 'strategy-cash-work':
                actual = {p.name for p in _members(path, uid, 4)}
                require({'binding.json', 'opening'} <= actual
                    <= {'binding.json', 'opening', 'account-opening', 'failures'}, 'MEMBER_SET')
            elif family == 'strategy-bar-reported-captures':
                actual = {p.name for p in _members(path, uid, 2)}
                require({'binding'} <= actual <= {'binding', 'inputs'}, 'MEMBER_SET')
            elif family == 'strategy-bar-feedback':
                _members(path, uid, 1, expected=['research-feedback'])


def _fixed_inputs(root, base, runtimes, uid, pilot_capacity):
    from tools.gold_bar_worker_inputs import OBSERVATION_FILES, JOURNAL_FILES
    expected = {name:set() for name in ('strategy-bar-history', 'strategy-bar-observations',
        'strategy-bar-history-work', 'strategy-bar-observation-work')}
    if base['schema'] == 'GOLD_BAR_RETAINED_EPISODE_INPUT_V1':
        expected['strategy-bar-observations'].add(base['observation_sha256'])
    elif base['schema'] == 'GOLD_BAR_FRESH_EPISODE_INPUT_V1':
        expected['strategy-bar-history'].add(base['history_sha256'])
        expected['strategy-bar-observation-work'].add(_digest(base))
    elif pilot_capacity is None:
        for value in runtimes:
            expected['strategy-bar-history-work'].add(_digest(value))
            expected['strategy-bar-observation-work'].add(_digest(value))
    for family, identities in expected.items():
        parent = root/family
        if not present(parent):
            continue
        require(identities, 'UNBOUND_FIXED_INPUTS')
        children = _members(parent, uid, len(identities))
        require({p.name for p in children} <= identities, 'UNBOUND_FIXED_INPUTS')
        for child in children:
            _check_path(child, uid, directory=True)
            if family in ('strategy-bar-observations', 'strategy-bar-observation-work'):
                _members(child, uid, len(OBSERVATION_FILES)+1, expected=(*OBSERVATION_FILES, 'journal'))
                _members(child/'journal', uid, len(JOURNAL_FILES), expected=JOURNAL_FILES)


def _cash_capture(path, uid):
    """The original immutable capture plus its exact latest pointer."""
    from services.research import demo_cash_window as native
    pointer = _read(path/'latest.json', uid, 1024)
    _fields(pointer, 'sha256')
    require(type(pointer['sha256']) is str and HASH.fullmatch(pointer['sha256']) is not None,
        'CASH_POINTER')
    _members(path, uid, 2, expected=('latest.json', pointer['sha256']+'.json'))
    _read_target(path/(pointer['sha256']+'.json'), uid=uid, maximum=native.MAX_BYTES)
    return native.load_capture(path)


def rotation(public, original):
    """Validate the full bound public chain; filesystem equality is separate."""
    from tools.gold_bar_rotation import verify_rotation_records
    from tools.gold_strategy_budget_enrollment import runtime_check
    base, current = public['base_runtime'], public['current_runtime']
    runtime_check(base, public['source_commit']); runtime_check(current, public['source_commit'])
    raw = public['records'].get('rotation')
    require(type(raw) is dict, 'MISSING_ROTATION_HISTORY')
    _fields(raw, 'base current records capacity')
    expected = capacity._capacity(original, base) if original is not None and base['schema'] == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1' else None
    require(raw['base'] == base and raw['current'] == current and raw['capacity'] == expected,
        'ROTATION_BINDING')
    if base['schema'] != 'GOLD_BAR_AUTO_EPISODE_INPUT_V1':
        require(base == current and raw['records'] == [] and raw['capacity'] is None, 'FIXED_RUNTIME_ROTATION')
        return None, [], [base]
    active, records = verify_rotation_records(raw['records'], base=base,
        account=public['account_ref_sha256'], capacity=expected)
    require(active == current, 'ROTATION_CURRENT')
    runtimes = [base, *(row['next_bundle'] for row in records)]
    return expected, records, runtimes


def _storage(root, public, original, uid, namespace):
    expected, records, runtimes = rotation(public, original)
    if expected is None:
        require(not capacity.state_present(root), 'UNBOUND_CAPACITY')
        parent = root/'strategy-bar-rotation'
        if namespace == 'controller' and public['base_runtime']['schema'] == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1':
            actual = {}
            if present(parent):
                _members(parent, uid, 1, expected=[_digest(public['base_runtime'])])
                actual = _hash_records(parent/_digest(public['base_runtime']), uid, 31, byte_limit=capacity.ROTATION_BYTES)
            require(actual == {_digest(row):row for row in records}, 'ROTATION_INVENTORY_CHANGED')
        else:
            require(not present(parent), 'UNBOUND_ROTATION')
        return expected, records, runtimes, dict(history=root/'strategy-bar-history-work',
            observations=root/'strategy-bar-observation-work')
    areas = {}
    for kind in capacity.KINDS:
        area, saved = capacity.storage(root, kind, original, public['base_runtime'], uid=uid, read_only=True)
        require(saved == expected, 'CAPACITY_BINDING')
        areas[kind] = area
    if not present(root/capacity.MARKER):
        require(not records if namespace == 'controller' else True, 'ROTATION_HISTORY_MISSING')
        return expected, records, runtimes, areas
    parent = root/capacity.PARENT; scope = parent/expected['request_sha256']; audit = root/capacity.AUDIT
    _members(parent, uid, 1, expected=[expected['request_sha256']])
    _members(scope, uid, 7, expected=(*capacity.DIRECTORIES, 'allocation.lock'))
    lock = scope/'allocation.lock'
    _check_path(lock, uid, directory=False)
    fd = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(before.st_size == 0 and os.read(fd, 1) == b''
            and (before.st_dev, before.st_ino) == (lock.stat().st_dev, lock.stat().st_ino), 'ALLOCATION_LOCK')
    finally:
        os.close(fd)
    _members(audit, uid, 4, expected=('capacity.json', *(k+'-allocations' for k in capacity.KINDS)))
    identities = {_digest(bundle) for bundle in runtimes}
    for kind in ('history', 'observations'):
        paths = capacity.usage(areas[kind], expected, kind, uid)
        require({p.name for p in paths} <= identities, 'EPISODE_OUTSIDE_ROTATION')
        if namespace == 'controller':
            require(not paths, 'CONTROLLER_OBSERVATION_PAYLOAD')
    capacity.rotation_history(areas['rotations'], expected, uid)
    rows = _hash_records(areas['rotations'], uid, expected['max_rotations'],
        byte_limit=expected['rotation_record_bytes'])
    if namespace == 'controller':
        require(rows == {_digest(row): row for row in records}, 'ROTATION_INVENTORY_CHANGED')
    else:
        require(not rows, 'EXECUTOR_ROTATION_PAYLOAD')
    return expected, records, runtimes, areas


def _tail(root, bundle, episode, uid):
    from tools import gold_bar_episode_tail as native
    area = root/'strategy-bar-tails'/_digest(bundle)
    if not present(area):
        return dict(status='NOT_STARTED', records=0, snapshots=[])
    _members(area, uid, 3, expected=('binding', 'events', 'snapshots'))
    binding = native._binding(episode.engine, _digest(bundle), episode.entry_decision_ms)
    require(_hash_records(area/'binding', uid, 1, byte_limit=200000) == {_digest(binding): binding}, 'TAIL_BINDING')
    records = _hash_records(area/'events', uid, native.MAX_RECORDS, byte_limit=200000)
    require(all(type(row.get('ordinal')) is int for row in records.values()), 'TAIL_ORDINAL')
    ordered = sorted(records.items(), key=lambda pair: pair[1]['ordinal'])
    rebuilt = deepcopy(episode.engine); prefix = []
    for ordinal, (digest, row) in enumerate(ordered, 1):
        native._replay_row(rebuilt, row, binding=binding, ordinal=ordinal, previous=prefix[-1] if prefix else None)
        prefix.append(digest)
    snapshots = _hash_records(area/'snapshots', uid, native.MAX_RECORDS+1, byte_limit=200000)
    for digest, snapshot in snapshots.items():
        require(type(snapshot) is dict and set(snapshot) == {'binding', 'prefix'}
            and snapshot['binding'] == binding and type(snapshot['prefix']) is list
            and snapshot['prefix'] == prefix[:len(snapshot['prefix'])], 'TAIL_SNAPSHOT_PREFIX')
        native.restore_snapshot(root, bundle_sha256=_digest(bundle), engine=episode.engine,
            entry_decision_ms=episode.entry_decision_ms, snapshot_sha256=digest)
    return dict(status='COMPLETE', records=len(ordered), snapshots=sorted(snapshots), blocked=rebuilt._cursor.blocked)


def _opening(root, bundle, public, original, uid, market_partition):
    from tools import gold_bar_cash as native
    area = root/'strategy-cash-work'/_digest(bundle)
    if not present(area) or not present(area/'account-opening'):
        return None
    require(market_partition is not MISSING, 'PARTITION_EVIDENCE_REQUIRED')
    binding = _read(area/'binding.json', uid, 4096)
    intent, record = native._metadata(area/'account-opening', uid)
    raw = _read_target(native._origin_path(root, record['origin_file_sha256']), uid=uid, maximum=8_000_000)
    return native.validate_account_opening_records(binding=binding, intent=intent, record=record, origin_bytes=raw,
        bundle=bundle, account=public['account_ref_sha256'], source_commit=public['source_commit'],
        original_pilot=original, market_partition=market_partition)


def _cash(root, bundle, public, uid, *, cash_as_of_ms=None):
    from services.research import demo_cash_window as native
    from tools.gold_bar_cash import opening
    from tools.gold_strategy_cash import collector
    area = root/'strategy-cash-work'/_digest(bundle)
    if not present(area):
        return dict(status='NOT_STARTED', opening_sha256=None, completed_sha256=None, failures=[])
    require(_read(area/'binding.json', uid, 4096) == dict(schema='GOLD_STRATEGY_CASH_BINDING_V1',
        source_commit=public['source_commit'], bundle_sha256=_digest(bundle),
        account_ref_sha256=public['account_ref_sha256'], collector_sha256=collector.source_hash()), 'CASH_BINDING')
    _cash_capture(area/'opening', uid)
    prefix = opening(area, public['account_ref_sha256'])
    if cash_as_of_ms is not None:
        native.assess(prefix, as_of_ms=cash_as_of_ms)
    completed = None; target = root/'strategy-cash'/_digest(bundle)
    if present(target):
        completed = _cash_capture(target, uid)
        require(completed['schema'] == native.LONG_SCHEMA and completed['status'] == 'CAPTURED'
            and completed['account_ref_sha256'] == public['account_ref_sha256']
            and completed['source_sha256'] == collector.source_hash()
            and completed['reads'][:2] == prefix['reads'] and completed['attempts'][:2] == prefix['attempts'],
            'CASH_COMPLETED_BINDING')
        if cash_as_of_ms is not None:
            native.assess(completed, as_of_ms=cash_as_of_ms)
    failures = {}
    if present(area/'failures'):
        # No invented cash quota: these files share the original ordinary
        # checkpoint count and byte bucket, checked before semantic replay.
        from tools.demo_namespace_checkpoint import MAX_FILES
        failures = _hash_records(area/'failures', uid, MAX_FILES)
        for report in failures.values():
            native.validate(report)
            require(report['schema'] == native.LONG_SCHEMA and report['status'] != 'CAPTURED'
                and report['account_ref_sha256'] == public['account_ref_sha256']
                and report['source_sha256'] == collector.source_hash()
                and report['reads'][:2] == prefix['reads'] and report['attempts'][:2] == prefix['attempts'],
                'CASH_FAILURE_BINDING')
            if cash_as_of_ms is not None:
                native.assess(report, as_of_ms=cash_as_of_ms)
    return dict(status='OPENING_RETAINED' if completed is None else 'COMPLETE', opening_sha256=_digest(prefix),
        completed_sha256=None if completed is None else _digest(completed), failures=sorted(failures))


def verify_dispatch_cut(snapshot, cut, *, expected_sha256):
    """Prove a finite candidate against an already verified native dual chain.

    Counts select original prefixes only. The original captured digest, never a
    caller's claimed authority or reconstructed ledger, proves their identity.
    """
    _fields(snapshot, 'reservations wire')
    _fields(cut, 'reservations wire')
    require(all(type(snapshot[name]) is list and type(cut[name]) is int
        and 0 <= cut[name] <= len(snapshot[name]) for name in ('reservations', 'wire')), 'DISPATCH_PREFIX')
    prefix = {name:snapshot[name][:cut[name]] for name in ('reservations', 'wire')}
    require(_digest(prefix) == expected_sha256, 'DISPATCH_PREFIX')
    return prefix


def _reported(root, bundle, episode, opening, public, uid, stores, execution, *, cash_as_of_ms=None):
    from tools.gold_bar_feedback import validate_retained_episode_records
    from tools.gold_bar_reported_feedback import build_from_records
    from live.execution.demo_pre_submit import _state
    area = root/'strategy-bar-reported-captures'/_digest(bundle)
    target = root/'strategy-bar-feedback'/_digest(bundle)
    if not present(area):
        require(not present(target), 'FEEDBACK_WITHOUT_CAPTURE')
        return dict(status='NOT_STARTED'), None
    require(stores is not None and execution is not None, 'EXECUTION_CONTEXT_REQUIRED')
    require(all(getattr(stores.get(k), 'read_only', False) is True for k in ('dispatch', 'recovery', 'oms', 'stream')),
        'READONLY_STORES_REQUIRED')
    require(execution['state'] == _state(stores['dispatch'], stores['recovery'], stores['oms']), 'EXECUTION_CHANGED')
    require(type(execution) is dict and set(execution) in (
        {'state', 'context', 'reservation_sources', 'records'},
        {'state', 'context', 'reservation_sources', 'records', 'dispatch_cuts'}), 'EXECUTION_FIELDS')
    batches = validate_retained_episode_records(**{k:v for k,v in execution.items() if k != 'dispatch_cuts'}, engine=episode.engine,
        entry_decision_ms=episode.entry_decision_ms, account=public['account_ref_sha256'])
    prepared = []; proofs = []
    for batch in batches:
        proposal = batch['prepared']['proposal_sha256']
        path = root/'evidence'/(proposal+'.json')
        proof = _read(path, uid)
        require(_digest(proof) == proposal, 'PROPOSAL_CHANGED')
        proofs.append(proof)
        prepared.append(dict(batch['prepared'], proposal_path=str(path)))
    before = stores['dispatch'].evidence_snapshot()
    bindings = _hash_records(area/'binding', uid, 1)
    require(len(bindings) == 1, 'REPORT_CAPTURE_BINDING')
    original_binding, = bindings.values()
    binding = dict(bundle_sha256=_digest(bundle), account_ref_sha256=public['account_ref_sha256'],
        source_commit=public['source_commit'], proof_sha256=[item['proposal_sha256'] for item in prepared],
        dispatch_sha256=original_binding.get('dispatch_sha256'), submission_enabled=False,
        capture_window_policy='BOUNDED_SEVEN_DAY_STATEMENTS_V1')
    if opening is not None:
        binding['opening_record_sha256'] = _digest(opening)
    binding['order_windows'] = [{k: b[k] for k in ('start_ms', 'end_ms')} for b in batches]
    require(original_binding == binding, 'REPORT_CAPTURE_BINDING')
    cuts = execution.get('dispatch_cuts', {})
    require(type(cuts) is dict and all(type(k) is str
        and re.fullmatch('[a-f0-9]{64}', k) is not None for k in cuts), 'DISPATCH_PREFIX')
    if binding['dispatch_sha256'] in cuts:
        prefix = verify_dispatch_cut(before, cuts[binding['dispatch_sha256']],
            expected_sha256=binding['dispatch_sha256'])
    elif binding['dispatch_sha256'] == _digest(before):
        prefix = before
    else:
        captured, feedback = _unreplayed_reported(root, bundle, public, uid)
        captured['status'] = 'DISPATCH_PREFIX_REQUIRED'
        return captured, feedback
    if not present(area/'inputs'):
        require(not present(target), 'FEEDBACK_WITHOUT_INPUTS')
        return dict(status='CAPTURE_RESERVED', binding_sha256=_digest(binding)), None
    packets = _hash_records(area/'inputs', uid, 1)
    require(len(packets) == 1, 'REPORT_CAPTURE_INPUTS')
    packet, = packets.values()
    require(type(packet) is dict and set(packet) == {'binding', 'inputs'} and packet['binding'] == binding,
        'REPORT_CAPTURE_BINDING')
    inputs = packet['inputs']
    as_of = max(inputs['closing']['completed_at_ms'], *(s['completed_at_ms'] for s in inputs['statements']),
        *(w['end_ms'] for w in inputs['windows']))
    if cash_as_of_ms is not None:
        as_of = cash_as_of_ms
    artifact = build_from_records(engine=episode.engine, entry_decision_ms=episode.entry_decision_ms, prepared=prepared,
        proofs=proofs, dispatch_snapshot=prefix, inputs=inputs, source_commit=public['source_commit'], as_of_ms=as_of,
        **(dict(opening_record=opening, bundle_sha256=_digest(bundle)) if opening is not None else {}))
    feedback = None
    if present(target):
        _members(target, uid, 1, expected=['research-feedback'])
        retained = _hash_records(target/'research-feedback', uid, 1)
        require(retained == {_digest(artifact): artifact}, 'FEEDBACK_REPLAY_CHANGED')
        feedback = dict(status='COMPLETE', artifact_sha256=_digest(artifact),
            account_report_sha256=artifact['account_report_sha256'], result_hash=artifact['result_hash'])
    require(stores['dispatch'].evidence_snapshot() == before, 'EXECUTION_CHANGED')
    return dict(status='COMPLETE', binding_sha256=_digest(binding), inputs_sha256=_digest(inputs)), feedback


def _unreplayed_reported(root, bundle, public, uid):
    """Preserve exact artifacts without claiming native book/cash replay."""
    area = root/'strategy-bar-reported-captures'/_digest(bundle)
    target = root/'strategy-bar-feedback'/_digest(bundle)
    if not present(area):
        require(not present(target), 'FEEDBACK_WITHOUT_CAPTURE')
        return dict(status='NOT_STARTED'), None
    bindings = _hash_records(area/'binding', uid, 1)
    require(len(bindings) == 1, 'REPORT_CAPTURE_BINDING')
    binding, = bindings.values()
    extra = (' opening_record_sha256' if 'opening_record_sha256' in binding else '')
    extra += ' order_windows' if 'order_windows' in binding else ''
    _fields(binding, 'bundle_sha256 account_ref_sha256 source_commit proof_sha256 dispatch_sha256 '
        'submission_enabled capture_window_policy'+extra)
    require(binding['bundle_sha256'] == _digest(bundle) and binding['source_commit'] == public['source_commit']
        and binding['account_ref_sha256'] == public['account_ref_sha256'] and binding['submission_enabled'] is False
        and binding['capture_window_policy'] == 'BOUNDED_SEVEN_DAY_STATEMENTS_V1'
        and type(binding['proof_sha256']) is list and 2 <= len(binding['proof_sha256']) <= 32,
        'REPORT_CAPTURE_BINDING')
    if not present(area/'inputs'):
        require(not present(target), 'FEEDBACK_WITHOUT_INPUTS')
        return dict(status='CAPTURE_RESERVED', binding_sha256=_digest(binding)), None
    packets = _hash_records(area/'inputs', uid, 1)
    require(len(packets) == 1, 'REPORT_CAPTURE_INPUTS')
    packet, = packets.values()
    require(type(packet) is dict and set(packet) == {'binding', 'inputs'} and packet['binding'] == binding,
        'REPORT_CAPTURE_BINDING')
    feedback = None
    if present(target):
        artifacts = _hash_records(target/'research-feedback', uid, 1)
        require(len(artifacts) == 1, 'FEEDBACK_COUNT')
        artifact, = artifacts.values()
        require(artifact.get('schema') in ('GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V1', 'GOLD_BAR_REPORTED_ACCOUNT_FEEDBACK_V2')
            and artifact.get('result_hash') == _digest({k:v for k,v in artifact.items() if k != 'result_hash'})
            and artifact.get('source_commit') == public['source_commit']
            and artifact.get('inputs_sha256') == _digest(packet['inputs'])
            and artifact.get('dispatch_evidence_sha256') == binding['dispatch_sha256'], 'FEEDBACK_CAPTURE_BINDING')
        feedback = dict(status='REPLAY_DEPENDENCY_REQUIRED', artifact_sha256=_digest(artifact),
            account_report_sha256=artifact['account_report_sha256'], result_hash=artifact['result_hash'])
    return dict(status='REPLAY_DEPENDENCY_REQUIRED', binding_sha256=_digest(binding),
        inputs_sha256=_digest(packet['inputs'])), feedback


def _exit_triggers(root, public, uid, retention, runtimes, historical=None):
    from live.execution import demo_bar_exit_trigger as native
    from tools.gold_pilot_retention import validate_retained_result
    from tools.demo_namespace_checkpoint import MAX_FILES
    paths = list(islice(root.glob('bar-exit-trigger-*.json'), MAX_FILES+1))
    require(len(paths) <= MAX_FILES, 'EXIT_TRIGGER_BUDGET')
    values = []; partial = []
    identities = {_digest(runtime) for runtime in runtimes}
    for path in sorted(paths):
        value = _read(path, uid, 32768)
        historical_position = None
        binding = value.get('binding')
        _fields(binding, 'account_ref_sha256 definition_hash entry_client_order_id reservation_sha256 opened_venue_at_ms')
        require(path.name == 'bar-exit-trigger-'+_digest(binding)+'.json'
            and binding['account_ref_sha256'] == public['account_ref_sha256'], 'EXIT_TRIGGER_BINDING')
        native.validate(value, binding, value['observed_at_ms'])
        if retention is None:
            status = 'RETENTION_EVIDENCE_REQUIRED'
        else:
            claim = retention.reference('origin', value['origin_sha256'])
            decision_bytes = retention.read_claim(claim, 'decision')
            result_bytes = retention.read_claim(claim, 'result')
            require(claim['bundle_sha256'] in identities and decision_bytes is not None
                and result_bytes is not None, 'EXIT_TRIGGER_FRAME')
            result = decode(result_bytes)
            decision = validate_retained_result(result, claim, retention.binding)
            require(decision == decode(decision_bytes), 'EXIT_TRIGGER_FRAME')
            draft = decision.get('draft')
            require(type(draft) is dict and draft.get('status') == 'BAR_EXIT_DRAFT'
                and decision['observed_at_ms'] == value['observed_at_ms']
                and decision['origin_sha256'] == draft['origin_sha256'] == value['origin_sha256']
                and draft['position_context_sha256'] == value['position_context_sha256']
                and draft['exit_trigger_sha256'] == value['record_hash'], 'EXIT_TRIGGER_FRAME')
            episode = draft['position_episode']
            require(binding == dict(account_ref_sha256=public['account_ref_sha256'],
                definition_hash=draft['definition_hash'], entry_client_order_id=episode['client_order_id'],
                reservation_sha256=episode['reservation_sha256'], opened_venue_at_ms=episode['opened_venue_at_ms']),
                'EXIT_TRIGGER_BINDING')
            # This proves the original fixed trigger/claim/draft relationship.
            # The archived historical position context still needs independent
            # book replay in the set verifier; a current flat context is not it.
            status = 'HISTORICAL_POSITION_CONTEXT_REQUIRED'
            if historical is not None:
                from tools.demo_gold_historical_position import HistoricalContextUnproven
                try:
                    replayed = historical.replay_trigger(trigger_sha256=value['record_hash'],
                        runtime_sha256=claim['bundle_sha256'])
                    historical_position = replayed['summary']
                    status = historical_position['status']
                except HistoricalContextUnproven as exc:
                    status = str(exc)
        row = dict(record_hash=value['record_hash'], binding_sha256=_digest(binding), status=status)
        if historical_position is not None:
            row['historical_position'] = historical_position
        values.append(row)
        if status != 'HISTORICAL_CONTEXT_VERIFIED':
            partial.append(dict(component='exit_trigger', record_hash=value['record_hash'], status=status))
    return values, partial


def verify(root, *, namespace, public, original, uid, readonly_stores=None,
           execution=None, market_partition=MISSING, retention=None, historical=None, cash_as_of_ms=None):
    """Verify existing components only. Never allocate, capture or export."""
    from tools.gold_bar_fresh_inputs import FreshEpisode
    from tools.gold_bar_auto_history import load as load_history
    from tools.gold_bar_cash import account_opening_usage
    root = Path(root).absolute()
    require(cash_as_of_ms is None or type(cash_as_of_ms) is int and cash_as_of_ms > 0, 'CASH_CUT_TIME')
    require(root.resolve() == root and namespace in ('executor', 'controller'), 'ROLE_ROOT')
    _check_path(root, uid, directory=True)
    cap, records, runtimes, areas = _storage(root, public, original, uid, namespace)
    if execution is not None and 'dispatch_cuts' in execution:
        require(type(execution['dispatch_cuts']) is dict and len(execution['dispatch_cuts']) <= len(runtimes),
            'DISPATCH_PREFIX_BUDGET')
    episodes = []; partial = []; triggers = []
    if namespace == 'executor':
        _component_members(root, runtimes, uid)
        _fixed_inputs(root, public['base_runtime'], runtimes, uid, cap)
        for bundle in runtimes:
            identity = _digest(bundle)
            if bundle['schema'] == 'GOLD_BAR_RETAINED_EPISODE_INPUT_V1':
                from tools.gold_bar_worker_inputs import load
                engine, checked = load(root, bundle, source_commit=public['source_commit'], uid=uid)
                def recheck(runtime=bundle):
                    return load(root, runtime, source_commit=public['source_commit'], uid=uid)[1]
                episode = SimpleNamespace(engine=engine, entry_decision_ms=bundle['entry_decision_ms'], recheck=recheck)
                history_view = dict(status='EMBEDDED_WARMUP')
            else:
                automatic = bundle['schema'] == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'
                history = (areas.get('history', root/'strategy-bar-history-work')/identity if automatic
                    else root/'strategy-bar-history'/bundle['history_sha256'])
                observed = areas.get('observations', root/'strategy-bar-observation-work')/identity
                if not present(history):
                    require(automatic and not present(observed)
                        and not any(present(root/family/identity) for family in (*ORDINARY, 'strategy-bar-tails')),
                        'OBSERVATION_WITHOUT_HISTORY')
                    episodes.append(dict(runtime=bundle, bundle_sha256=identity, history=dict(status='NOT_STARTED'),
                        observation=dict(status='NOT_STARTED'), tail=None, opening=None, cash=None,
                        reported_capture=None, feedback=None))
                    continue
                if automatic:
                    load_history(root, bundle, uid=uid, read_only=True)
                receipt_bytes = _read_target(history/'receipt.json', uid=uid, maximum=16_000_000)
                receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
                if not automatic:
                    from services.research.gold_bar_dataset import load_bars
                    from services.research.gold_bar_stream import GoldBarStream
                    require(receipt_sha256 == bundle['history_sha256'], 'FRESH_HISTORY_BINDING')
                    receipt = decode(receipt_bytes)
                    response = _read_target(history/'response.json', uid=uid, maximum=2_000_000)
                    require(hashlib.sha256(response).hexdigest() == receipt['response_sha256'], 'FRESH_HISTORY_BYTES')
                    # Replay the same warmup constructor as the original observer
                    # even before that observer has created its first directory.
                    GoldBarStream(load_bars(response, requested_at_ms=receipt['requested_at_ms'],
                        received_at_ms=receipt['received_at_ms']))
                history_view = dict(status='COMPLETE', receipt_sha256=receipt_sha256)
                if not present(observed):
                    episodes.append(dict(runtime=bundle, bundle_sha256=identity, history=history_view,
                        observation=dict(status='NOT_STARTED'), tail=None, opening=None, cash=None,
                        reported_capture=None, feedback=None))
                    continue
                episode = FreshEpisode(root, bundle, source_commit=public['source_commit'], uid=uid, read_only=True)
            opened = None
            if market_partition is MISSING and present(root/'strategy-cash-work'/identity):
                partial.append(dict(component='account_opening', bundle_sha256=identity,
                    status='PARTITION_EVIDENCE_REQUIRED'))
            else:
                opened = _opening(root, bundle, public, original, uid, market_partition)
            if opened is not None:
                require(opened['origin']['completed_at_ms']+5000 < episode.entry_decision_ms, 'OPENING_BEFORE_SIGNAL')
                account_opening_usage(root, observation_area=episode.observation_area,
                    capacity=episode.pilot_capacity, bundle=bundle, account=public['account_ref_sha256'])
            if execution is None or market_partition is MISSING:
                captured, feedback = _unreplayed_reported(root, bundle, public, uid)
                if captured['status'] != 'NOT_STARTED':
                    partial.append(dict(component='reported_cash', bundle_sha256=identity,
                        status='EXECUTION_CONTEXT_REQUIRED'))
            else:
                captured, feedback = _reported(root, bundle, episode, opened, public, uid, readonly_stores, execution,
                    cash_as_of_ms=cash_as_of_ms)
            if captured['status'] == 'CAPTURE_RESERVED':
                partial.append(dict(component='reported_capture', bundle_sha256=identity, status='CAPTURE_RESERVED'))
            if captured['status'] == 'DISPATCH_PREFIX_REQUIRED':
                partial.append(dict(component='reported_cash', bundle_sha256=identity, status='DISPATCH_PREFIX_REQUIRED'))
            episodes.append(dict(runtime=bundle, bundle_sha256=identity, history=history_view,
                observation=dict(status='COMPLETE', **episode.recheck()), tail=_tail(root, bundle, episode, uid),
                opening=None if opened is None else {k:v for k,v in opened.items() if k != 'origin'},
                cash=_cash(root, bundle, public, uid, cash_as_of_ms=cash_as_of_ms),
                reported_capture=captured, feedback=feedback))
        if retention is not None:
            require(retention.read_only and retention.verify_history and retention.role == 'executor'
                and retention.binding['source_commit'] == public['source_commit']
                and retention.binding['account_ref_sha256'] == public['account_ref_sha256'], 'RETENTION_BINDING')
            by_bundle = {row['bundle_sha256']: row for row in episodes}
            before = retention.metadata()
            for ordinal, raw_claim in retention.conn.execute('SELECT ordinal,claim FROM frames ORDER BY ordinal'):
                claim = decode(raw_claim)
                require(claim['bundle_sha256'] in by_bundle, 'RETENTION_RUNTIME')
                raw = retention.read_claim(claim, 'result')
                if raw is None:
                    continue  # The outer native closure/partial validator owns this proof.
                result = decode(raw); retained = result.get('feedback')
                if retained is not None:
                    actual = by_bundle[claim['bundle_sha256']]['feedback']
                    require(actual is not None and retained['artifact_sha256'] == actual['artifact_sha256']
                        and retained['feedback_sha256'] == actual['result_hash'], 'RETAINED_FEEDBACK_MISSING')
            require(retention.metadata() == before, 'RETENTION_CHANGED')
        triggers, pending = _exit_triggers(root, public, uid, retention, runtimes, historical)
        partial.extend(pending)
    return dict(schema=SCHEMA, capacity=cap, rotation=dict(records=len(records),
        records_sha256=_digest(records), current=public['current_runtime']), episodes=episodes,
        exit_triggers=triggers, partial=partial, submission_enabled=False, economic_authority=False)
