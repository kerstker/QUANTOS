"""Durable observation-episode rotation under the original controller lease.

Never creates a pilot, changes its baseline, signs an intent, or sends an order.
"""
from itertools import islice
from pathlib import Path
import json
import os
import re

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from live.execution.demo_pre_submit import _retain
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_supervisor as supervisor
from tools import gold_bar_pilot_capacity as pilot_capacity

MAX_ROTATIONS = 31  # Existing first-close storage permits 32 episodes.


def _eligible(result, current, account):
    from tools.gold_strategy_controller import validate_result
    decision = validate_result(result, source_commit=current['source_commit'],
        bundle_sha256=_digest(current), account=account)
    pilot = decision.get('pilot_control')
    if (decision.get('position_flat') is not True or type(pilot) is not dict
            or pilot.get('status') != 'ACTIVE'):
        return False
    if decision['status'] == 'NO_ENTRY_SIGNAL':
        return decision.get('action') is None and decision.get('draft') is None
    if decision['status'] != 'STRATEGY_EPISODE_SETTLED':
        return False
    feedback, delivered = result.get('feedback'), result.get('research_feedback')
    return (type(feedback) is dict and type(delivered) is dict
        and feedback.get('status') == 'BAR_STRATEGY_FEEDBACK_RETAINED'
        and feedback.get('cash_reconciled') is True
        and delivered.get('status') == 'RESEARCH_FEEDBACK_DELIVERED'
        and delivered.get('cycle_sha256') == _digest(current)
        and type(feedback.get('feedback_sha256')) is str
        and re.fullmatch('[a-f0-9]{64}', feedback['feedback_sha256']) is not None
        and delivered.get('feedback_sha256') == feedback['feedback_sha256']
        and all(feedback.get(k) is False for k in ('submission_enabled', 'economic_authority'))
        and all(delivered.get(k) is False for k in ('source_verified', 'submission_enabled', 'economic_authority')))


def _next(base, current, result, account, ordinal):
    return dict(current, episode_id=_digest(dict(schema='GOLD_BAR_EPISODE_ROTATION_V1',
        base_sha256=_digest(base), previous_bundle_sha256=_digest(current),
        result_sha256=_digest(result), account_ref_sha256=account, ordinal=ordinal)))


def _load(root, base, account, *, read_only=False):
    require(type(read_only) is bool, 'BAR_ROTATION_READ_MODE')
    _fields(base, 'schema source_commit trial_manifest_sha256 archive_source_commit candidate seconds episode_id')
    require(base['schema'] == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'
        and type(account) is str and re.fullmatch('[a-f0-9]{64}', account) is not None,
        'BAR_ROTATION_SCOPE')
    root = Path(root).absolute()
    require(root.resolve() == root, 'BAR_ROTATION_ROOT')
    supervisor._root_private_directory(root)
    original = pilot_capacity.controller_context(base, account)
    capacity = None
    if original is None:
        require(not pilot_capacity.state_present(root), 'BAR_PILOT_REQUEST_MISSING')
    if original is not None:
        if original['configuration'] is None:
            # The original worker enrolls before its first capture. Controller
            # lookup must not create a provisional baseline/capacity identity.
            require(not pilot_capacity.state_present(root), 'BAR_PILOT_ENROLLMENT_REQUIRED')
            pilot_capacity.legacy_empty(root, root.lstat().st_uid)
            return root/pilot_capacity.PARENT, dict(base), [], {'pending': True}
        area, capacity = pilot_capacity.storage(root, 'rotations', original, base,
            uid=root.lstat().st_uid, read_only=read_only)
        if read_only and not area.exists():
            return area, dict(base), [], capacity
        return _records(area, base, account, capacity)
    parent = root/'strategy-bar-rotation'
    if read_only and not parent.exists():
        require(not parent.is_symlink(), 'BAR_ROTATION_ROOT')
        return parent/_digest(base), dict(base), [], capacity
    if not read_only:
        parent.mkdir(mode=0o700, exist_ok=True)
    supervisor._root_private_directory(parent)
    area = parent/_digest(base)
    if read_only and not area.exists():
        require(not area.is_symlink(), 'BAR_ROTATION_ROOT')
        return area, dict(base), [], capacity
    if not read_only:
        area.mkdir(mode=0o700, exist_ok=True)
    supervisor._root_private_directory(area)
    return _records(area, base, account, capacity)


def _records(area, base, account, capacity):
    if capacity is not None:
        pilot_capacity.rotation_history(area, capacity, area.lstat().st_uid)
    maximum = MAX_ROTATIONS if capacity is None else capacity['max_rotations']
    paths = list(islice(area.iterdir(), maximum+1))
    require(len(paths) <= maximum, 'BAR_ROTATION_STORAGE_BUDGET')
    records = []
    for path in paths:
        require(path.suffix == '.json' and path.stat().st_size <= 2_000_000, 'BAR_ROTATION_FILE')
        record = decode(supervisor._read_root_owned_json(path))
        _fields(record, 'schema base_sha256 account_ref_sha256 ordinal previous_bundle_sha256 result result_sha256 next_bundle submission_enabled')
        require(path.stem == _digest(record) and type(record['ordinal']) is int,
            'BAR_ROTATION_RECORD_CHANGED')
        records.append(record)
    current, records = verify_rotation_records(records, base=base, account=account, capacity=capacity)
    return area, current, records, capacity


def verify_rotation_records(records, *, base, account, capacity):
    """Replay already custody-checked original rows; no I/O or new rotation."""
    _fields(base, 'schema source_commit trial_manifest_sha256 archive_source_commit candidate seconds episode_id')
    require(base['schema'] == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1'
        and type(account) is str and re.fullmatch('[a-f0-9]{64}', account) is not None, 'BAR_ROTATION_SCOPE')
    maximum = MAX_ROTATIONS if capacity is None else capacity['max_rotations']
    require(type(records) is list and len(records) <= maximum, 'BAR_ROTATION_STORAGE_BUDGET')
    for record in records:
        _fields(record, 'schema base_sha256 account_ref_sha256 ordinal previous_bundle_sha256 result result_sha256 next_bundle submission_enabled')
        require(type(record['ordinal']) is int, 'BAR_ROTATION_RECORD_CHANGED')
    records = sorted(records, key=lambda r: r['ordinal'])
    current = dict(base)
    for ordinal, record in enumerate(records, 1):
        require(record['schema'] == 'GOLD_BAR_EPISODE_ROTATION_V1'
            and record['base_sha256'] == _digest(base) and record['account_ref_sha256'] == account
            and record['ordinal'] == ordinal and record['previous_bundle_sha256'] == _digest(current)
            and record['result_sha256'] == _digest(record['result']) and record['submission_enabled'] is False
            and _eligible(record['result'], current, account), 'BAR_ROTATION_CHAIN')
        if capacity is not None:
            pilot_capacity.result_binding(record['result'], capacity)
        expected = _next(base, current, record['result'], account, ordinal)
        require(record['next_bundle'] == expected, 'BAR_ROTATION_NEXT_CHANGED')
        current = expected
    return current, records


def load(*, root, base, account, read_only=False):
    return _load(root, base, account, read_only=read_only)[1]


def feedback_history(*, root, base, account, current):
    """Original complete episode identities, never inferred from claim order."""
    _, active, records, _ = _load(root, base, account, read_only=True)
    require(current == active, 'BAR_FEEDBACK_SCOPE_CURRENT')
    return (_digest(base), *(_digest(row['next_bundle']) for row in records))


def feedback_terminal(*, root, base, account, current):
    """Last delivered settlement in the fully verified original rotations.

    Later no-signal episodes do not erase the terminal cash obligation. This
    read does not publish feedback, allocate an episode or modify history.
    """
    _, active, records, _ = _load(root, base, account, read_only=True)
    require(current == active, 'BAR_FEEDBACK_SCOPE_CURRENT')
    for row in reversed(records):
        if row['result']['decision']['status'] == 'STRATEGY_EPISODE_SETTLED':
            return dict(result=row['result'], rotation_sha256=_digest(row))
    return None


def feedback_scope(*, root, base, account, current):
    """Read the original active episode and its exact previous completion.

    Full existing rotation verification precedes this bounded view. This does
    not create a directory, advance a rotation or authorize a new observation.
    """
    _area, active, records, _capacity = _load(root, base, account, read_only=True)
    require(current == active, 'BAR_FEEDBACK_SCOPE_CURRENT')
    previous = None if not records else (dict(base) if len(records) == 1 else records[-2]['next_bundle'])
    return dict(current=active, previous=previous,
        rotation_result=records[-1]['result'] if records else None,
        rotation_sha256=_digest(records[-1]) if records else None)


def advance(*, root, base, account, current, result, before_commit=None):
    require(before_commit is None or callable(before_commit), 'BAR_ROTATION_RECHECK')
    area, active, records, capacity = _load(root, base, account)
    require(capacity is None or capacity.get('pending') is not True, 'BAR_PILOT_ENROLLMENT_REQUIRED')
    if current != active:
        require(records and records[-1]['previous_bundle_sha256'] == _digest(current)
            and records[-1]['result'] == result, 'BAR_ROTATION_STALE_RESULT')
        return active
    if not _eligible(result, current, account):
        return active
    if capacity is not None:
        pilot_capacity.result_binding(result, capacity)
    require(len(records) < (MAX_ROTATIONS if capacity is None else capacity['max_rotations']),
        'BAR_ROTATION_STORAGE_BUDGET')
    ordinal = len(records)+1
    following = _next(base, current, result, account, ordinal)
    record = dict(schema='GOLD_BAR_EPISODE_ROTATION_V1', base_sha256=_digest(base),
        account_ref_sha256=account, ordinal=ordinal, previous_bundle_sha256=_digest(current),
        result=result, result_sha256=_digest(result), next_bundle=following, submission_enabled=False)
    require(len(json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())+1 <= 2_000_000,
        'BAR_ROTATION_RECORD_BUDGET')
    if before_commit is not None:
        allowed = before_commit()
        require(type(allowed) is bool, 'BAR_ROTATION_RECHECK')
        if not allowed:
            return active
    if capacity is not None:
        pilot_capacity.allocate_rotation(area, capacity, area.lstat().st_uid, record)
        # Allocation can perform durable I/O. A deadline/stop reached during
        # that work consumes its original reservation, never a next episode.
        if before_commit is not None:
            require(before_commit() is True, 'BAR_ROTATION_WINDOW_CHANGED')
    _retain(area, record)
    for directory in (area.parent, area.parent.parent):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return load(root=root, base=base, account=account)
