"""Retain the original long-window wallet before a bar episode can observe."""
import time

from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _retain
from services.research.demo_cash_window import LONG_SCHEMA, load_capture, save_capture
from tools import gold_strategy_cash as original
from tools.demo_resident_recovery import _check_path


def require(condition, reason):
    if not condition:
        raise ValueError('GOLD_BAR_CASH_'+reason)


def opening(area, account):
    prefix = original._prefix(area, account)
    require(prefix['schema'] == LONG_SCHEMA, 'LONG_PREFIX_REQUIRED')
    return prefix


def open_window(root, *, bundle, account, source_commit, observation_exists):
    area = original._area(root, bundle, account, source_commit)
    if (area/'opening').exists() or (area/'opening').is_symlink():
        prefix = opening(area, account)
    else:
        require(not observation_exists, 'PRE_SIGNAL_OPENING_MISSING')
        prefix = original._collect(account, opening_only=True, long_window=True)
        require(prefix['schema'] == LONG_SCHEMA and prefix['status'] == 'OPENING_CAPTURED'
            and prefix['account_ref_sha256'] == account, 'OPENING_UNAVAILABLE')
        save_capture(area/'opening', prefix)
    # Held/settled episodes only validate their immutable opening. They must
    # remain observable beyond cash coverage, without authorizing a new entry.
    if not observation_exists:
        require(0 <= time.time_ns()//1_000_000-prefix['completed_at_ms'] <= 120000, 'OPENING_EXPIRED')
    target = root/'strategy-cash'/_digest(bundle)
    if observation_exists and (target.exists() or target.is_symlink()):
        close_window(root, bundle=bundle, account=account, source_commit=source_commit)
    return prefix


def close_window(root, *, bundle, account, source_commit):
    area = original._area(root, bundle, account, source_commit)
    prefix = opening(area, account)
    target = root/'strategy-cash'/_digest(bundle)
    if target.exists() or target.is_symlink():
        for path in (target.parent, target):
            _check_path(path, root.stat().st_uid, directory=True)
        report = load_capture(target)
    else:
        report = original._collect(account, opening_prefix=prefix, long_window=True)
        if report['status'] != 'CAPTURED':
            failures = area/'failures'
            failures.mkdir(mode=0o700, exist_ok=True)
            _check_path(failures, root.stat().st_uid, directory=True)
            _retain(failures, report)
            raise ValueError('GOLD_BAR_CASH_CLOSING_INCOMPLETE')
    require(report['schema'] == LONG_SCHEMA and report['status'] == 'CAPTURED'
        and report['account_ref_sha256'] == account and report['source_sha256'] == original.collector.source_hash()
        and report['reads'][:2] == prefix['reads'] and report['attempts'][:2] == prefix['attempts'], 'COMPLETED_BINDING')
    if not target.exists():
        target.parent.mkdir(mode=0o700, exist_ok=True)
        _check_path(target.parent, root.stat().st_uid, directory=True)
        save_capture(target, report)
    return report


# Full account openings are references to the original bounded executor capture,
# not a second cash ledger or a new allocation/time/authorization budget.
ACCOUNT_OPENING = 'GOLD_BAR_PRE_SIGNAL_ACCOUNT_OPENING_V1'


def _opening_stores(stack, root):
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    stores = {}
    for name, factory in (('dispatch', DemoDispatchStore), ('recovery', SqliteOrderRecoveryStore), ('oms', LiveLedger)):
        store = factory(str(root/(name+'.sqlite3')), read_only=True)
        stack.callback(store.close)
        stores[name] = store
    return stores


def _once(path, value):
    import json
    import os
    from tools.gold_bar_report_capture import _sync
    raw = (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()
    require(len(raw) <= 16384, 'ACCOUNT_OPENING_METADATA_BUDGET')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _sync(path.parent)


def _metadata(area, uid):
    from tools.gold_pilot_install import _read_target
    from services.governance.json_integrity import load_json_without_duplicate_keys as decode
    from live.execution.demo_batch_risk import _fields
    _check_path(area, uid, directory=True)
    from itertools import islice
    require({p.name for p in islice(area.iterdir(), 3)} == {'intent.json', 'record.json'}, 'ACCOUNT_OPENING_INCOMPLETE')
    intent = decode(_read_target(area/'intent.json', uid=uid, maximum=16384))
    record = decode(_read_target(area/'record.json', uid=uid, maximum=16384))
    return _metadata_records(intent, record)


def _metadata_records(intent, record):
    from live.execution.demo_batch_risk import _fields
    _fields(intent, 'schema bundle_sha256 source_commit account_ref_sha256 state_sha256 '
        'market_partition pilot_request_sha256 pilot_configuration_sha256 submission_enabled economic_authority')
    require(intent['schema'] == ACCOUNT_OPENING and intent['submission_enabled'] is False
        and intent['economic_authority'] is False, 'ACCOUNT_OPENING_FIELDS')
    _fields(record, 'intent_sha256 origin_sha256 origin_file_sha256')
    require(record['intent_sha256'] == _digest(intent), 'ACCOUNT_OPENING_INTENT_CHANGED')
    return intent, record


def _origin_path(root, digest):
    import re
    require(type(digest) is str and re.fullmatch('[a-f0-9]{64}', digest) is not None,
        'ACCOUNT_OPENING_ORIGIN_ID')
    return root/'execution-origins'/'XAUUSDT'/(digest+'.json')


def load_account_opening(root, *, bundle, account, source_commit):
    """Read only: missing or interrupted openings never acquire a new origin."""
    from tools.gold_pilot_install import _read_target
    from services.governance.json_integrity import load_json_without_duplicate_keys as decode
    from tools import demo_executor_origin as capture
    uid = root.stat().st_uid
    area = root/'strategy-cash-work'/_digest(bundle)
    for path in (root, area.parent, area):
        _check_path(path, uid, directory=True)
    # Keep the original cash binding byte contract, without creating an area.
    binding = decode(_read_target(area/'binding.json', uid=uid, maximum=4096))
    intent, record = _metadata(area/'account-opening', uid)
    from tools import gold_bar_pilot_capacity as pilot
    from live.execution import demo_market_partition_runtime as partition
    original_pilot = pilot.original_context(root, bundle, uid=uid, account=account)
    installed = partition._load(role='executor', as_of_ms=time.time_ns()//1_000_000)
    path = _origin_path(root, record['origin_file_sha256'])
    for parent in (path.parent.parent, path.parent):
        _check_path(parent, uid, directory=True)
    raw = _read_target(path, uid=uid, maximum=capture.original.MAX_BYTES)
    return validate_account_opening_records(binding=binding, intent=intent, record=record, origin_bytes=raw,
        bundle=bundle, account=account, source_commit=source_commit, original_pilot=original_pilot,
        market_partition=None if installed is None else partition._descriptor(installed[0]))


def validate_account_opening_records(*,binding,intent,record,origin_bytes,bundle,account,source_commit,
                                    original_pilot,market_partition):
    """Pure historical content check, never proof of current installed custody.

    The production reader independently loads its real partition at its actual
    current clock; an isolated verifier supplies the captured original descriptor.
    """
    from services.governance.json_integrity import load_json_without_duplicate_keys as decode
    from tools import demo_executor_origin as capture
    import hashlib
    _metadata_records(intent,record)
    require(binding == dict(schema='GOLD_STRATEGY_CASH_BINDING_V1', source_commit=source_commit,
        bundle_sha256=_digest(bundle), account_ref_sha256=account,
        collector_sha256=original.collector.source_hash()), 'ACCOUNT_OPENING_BINDING')
    require(intent['bundle_sha256'] == _digest(bundle) and intent['source_commit'] == source_commit
        and intent['account_ref_sha256'] == account, 'ACCOUNT_OPENING_BINDING')
    require(intent['pilot_request_sha256'] == (None if original_pilot is None else _digest(original_pilot['request']))
        and intent['pilot_configuration_sha256'] == (None if original_pilot is None else _digest(original_pilot['configuration'])),
        'ACCOUNT_OPENING_PILOT_CHANGED')
    require(intent['market_partition'] == market_partition, 'ACCOUNT_OPENING_PARTITION_CHANGED')
    require(type(origin_bytes) is bytes and hashlib.sha256(origin_bytes).hexdigest() == record['origin_file_sha256'],
        'ACCOUNT_OPENING_FILE_CHANGED')
    origin = capture.original.validate(decode(origin_bytes))
    require(_digest(origin) == record['origin_sha256'] and origin['status'] == 'CAPTURED'
        and origin['symbol'] == 'XAUUSDT' and origin['account_ref_sha256'] == account
        and origin['source_sha256'] == capture.source_hash(), 'ACCOUNT_OPENING_ORIGINAL')
    return dict(intent, origin_sha256=record['origin_sha256'], origin_file_sha256=record['origin_file_sha256'], origin=origin)


def account_opening_usage(root, *, observation_area, capacity, bundle, account, pending=False):
    """Charge retained origin bytes to the existing observation count/byte budget."""
    from itertools import islice
    from tools.gold_bar_worker_inputs import protected_tree
    from tools import gold_bar_pilot_capacity as pilot
    uid = root.stat().st_uid
    original_pilot = pilot.original_context(root, bundle, uid=uid, account=account)
    pilot_hash = None if original_pilot is None else _digest(original_pilot['request'])
    if bundle.get('schema') == 'GOLD_BAR_AUTO_EPISODE_INPUT_V1' and original_pilot is not None:
        expected_area, expected_capacity = pilot.storage(root, 'observations', original_pilot, bundle,
            uid=uid, read_only=True)
        require(observation_area == expected_area and capacity == expected_capacity, 'ACCOUNT_OPENING_CAPACITY_CHANGED')
    else:
        require(capacity is None and observation_area == root/'strategy-bar-observation-work',
            'ACCOUNT_OPENING_CAPACITY_CHANGED')
    maximum = 32 if capacity is None else capacity['max_episodes']
    total_limit = 256*1024*1024 if capacity is None else capacity['observation_total_bytes']
    per_limit = 64*1024*1024 if capacity is None else capacity['observation_episode_bytes']
    if capacity is not None:
        entries = pilot.usage(observation_area, capacity, 'observations', uid)
    else:
        entries = list(islice(observation_area.iterdir(), maximum+1))
        require(len(entries) <= maximum, 'ACCOUNT_OPENING_EPISODE_BUDGET')
    sizes = {p.name: sum(r[2] for r in protected_tree(p, uid)) for p in entries}
    parent = root/'strategy-cash-work'
    if parent.exists() or parent.is_symlink():
        _check_path(parent, uid, directory=True)
        areas = list(islice(parent.iterdir(), 1025))
        require(len(areas) <= 1024, 'ACCOUNT_OPENING_SCAN_BUDGET')
        for area in areas:
            _check_path(area, uid, directory=True)
            opening_area = area/'account-opening'
            if pending and area.name == _digest(bundle):
                continue
            if not opening_area.exists() and not opening_area.is_symlink():
                continue
            intent, record = _metadata(opening_area, uid)
            if (intent['source_commit'], intent['account_ref_sha256'], intent['pilot_request_sha256']) != (
                    bundle['source_commit'], account, pilot_hash):
                continue
            require(area.name == intent['bundle_sha256'], 'ACCOUNT_OPENING_AREA')
            path = _origin_path(root, record['origin_file_sha256'])
            for p in (path.parent.parent, path.parent):
                _check_path(p, uid, directory=True)
            _check_path(path, uid, directory=False)
            require(path.stat().st_size <= 8_000_000, 'ACCOUNT_OPENING_ORIGIN_BYTES')
            size = path.stat().st_size + sum(r[2] for r in protected_tree(opening_area, uid))
            sizes[area.name] = sizes.get(area.name, 0)+size
    if pending:
        sizes[_digest(bundle)] = sizes.get(_digest(bundle), 0)+8_000_000+32768
    require(len(sizes) <= maximum and all(size <= per_limit for size in sizes.values())
        and sum(sizes.values()) <= total_limit, 'ACCOUNT_OPENING_STORAGE_BUDGET')


def open_account_origin(root, *, bundle, account, source_commit, observation_exists,
                        observation_area, capacity):
    """Capture once before public observation; caller is outside preparation lease."""
    from contextlib import ExitStack
    from live.execution.demo_pre_submit import _state, _preparation_lease
    from live.execution import demo_market_partition_runtime as partition
    from tools import demo_executor_origin as capture, gold_bar_pilot_capacity as pilot
    from tools.demo_resident_recovery import require_custody
    from tools.demo_executor_stream import fixed_account
    from tools.gold_bar_report_capture import _sync
    from tools.demo_reported_recovery import _read
    require(root == require_custody() and (root, account) == fixed_account(), 'ACCOUNT_OPENING_CUSTODY')
    observed_path = observation_area/_digest(bundle)
    require(type(observation_exists) is bool and observation_exists == (observed_path.exists() or observed_path.is_symlink()),
        'ACCOUNT_OPENING_OBSERVATION_STATE')
    area = original._area(root, bundle, account, source_commit)/'account-opening'
    if area.exists() or area.is_symlink():
        value = load_account_opening(root, bundle=bundle, account=account, source_commit=source_commit)
    else:
        require(not observation_exists, 'PRE_SIGNAL_ACCOUNT_OPENING_MISSING')
        account_opening_usage(root, observation_area=observation_area, capacity=capacity,
            bundle=bundle, account=account, pending=True)
        with ExitStack() as stack:
            stores = _opening_stores(stack, root)
            stack.enter_context(_preparation_lease(stores['dispatch']))
            # Recheck under the same account lease immediately before reserving
            # the attempt; a concurrent episode may have consumed the budget.
            account_opening_usage(root, observation_area=observation_area, capacity=capacity,
                bundle=bundle, account=account, pending=True)
            state = _state(**stores)
            original_pilot = pilot.original_context(root, bundle, uid=root.stat().st_uid, account=account)
            area.mkdir(mode=0o700)
            _sync(area.parent)
            # The consumed intent precedes GET; interrupted/failed attempts cannot renew it.
            intent = dict(schema=ACCOUNT_OPENING, bundle_sha256=_digest(bundle), source_commit=source_commit,
                account_ref_sha256=account, state_sha256=_digest(state), market_partition=None,
                pilot_request_sha256=None if original_pilot is None else _digest(original_pilot['request']),
                pilot_configuration_sha256=None if original_pilot is None else _digest(original_pilot['configuration']),
                submission_enabled=False, economic_authority=False)
            # Independently load the installed cut before starting the observation.
            installed = partition._load(role='executor', as_of_ms=time.time_ns()//1_000_000)
            intent['market_partition'] = None if installed is None else partition._descriptor(installed[0])
            _once(area/'intent.json', intent)
            captured = capture.run(symbol='XAUUSDT', source_commit=source_commit)
            require(captured['status'] == 'CAPTURED', 'ACCOUNT_OPENING_CAPTURE_FAILED')
            from pathlib import Path
            path = Path(captured['path'])
            origin = _read(path, root, capture.original.MAX_BYTES, raw_digest=True)
            require(path == _origin_path(root, path.stem), 'ACCOUNT_OPENING_CAPTURE_PATH')
            context, descriptor = partition.resolve_context(state=state, origin=origin,
                as_of_ms=origin['completed_at_ms'])
            if original_pilot is not None:
                config = original_pilot['configuration']
                require(config['started_at_ms'] <= origin['observed_at_ms'] <= origin['completed_at_ms'] < config['ends_at_ms'],
                    'ACCOUNT_OPENING_PILOT_WINDOW')
            report = capture.original.assess(origin, as_of_ms=origin['completed_at_ms'])
            require(report['status'] == 'OBSERVED_FLAT_QUOTE' and (context is None or context['quantity'] == '0'),
                'ACCOUNT_OPENING_NOT_FLAT')
            require(descriptor == intent['market_partition'] and _state(**stores) == state
                and pilot.original_context(root, bundle, uid=root.stat().st_uid, account=account) == original_pilot,
                'ACCOUNT_OPENING_HISTORY_CHANGED')
            _once(area/'record.json', dict(intent_sha256=_digest(intent), origin_sha256=_digest(origin), origin_file_sha256=path.stem))
        value = load_account_opening(root, bundle=bundle, account=account, source_commit=source_commit)
    account_opening_usage(root, observation_area=observation_area, capacity=capacity,bundle=bundle,account=account)
    if not observation_exists:
        # This wait is before the public close, not inside a five-second grant.
        now = time.time_ns()//1_000_000
        require(0 <= now-value['origin']['completed_at_ms'] <= 120000, 'ACCOUNT_OPENING_EXPIRED')
        remaining = value['origin']['completed_at_ms']+5001-now
        if remaining > 0:
            time.sleep(remaining/1000)
        require(time.time_ns()//1_000_000 >= max(now, value['origin']['completed_at_ms']+5001),
            'ACCOUNT_OPENING_CLOCK')
    return value


def check_account_opening(value, *, proof, bundle_sha256, source_commit, entry_decision_ms):
    """Bind the earlier origin to the original independently checked first proof."""
    from live.execution.demo_batch_risk import _fields
    from tools import demo_executor_origin as capture
    _fields(value, 'schema bundle_sha256 source_commit account_ref_sha256 state_sha256 market_partition '
        'pilot_request_sha256 pilot_configuration_sha256 submission_enabled economic_authority origin_sha256 origin_file_sha256 origin')
    import re
    for key in ('bundle_sha256', 'state_sha256', 'origin_sha256', 'origin_file_sha256', 'account_ref_sha256'):
        require(type(value[key]) is str and re.fullmatch('[a-f0-9]{64}', value[key]) is not None,
            'ACCOUNT_OPENING_DIGEST')
    require(value['schema'] == ACCOUNT_OPENING and value['submission_enabled'] is False
        and value['economic_authority'] is False and value['bundle_sha256'] == bundle_sha256
        and value['source_commit'] == source_commit and value['account_ref_sha256'] == proof['context']['account_ref_sha256'],
        'ACCOUNT_OPENING_BINDING')
    origin = capture.original.validate(value['origin'])
    require(value['origin_sha256'] == _digest(origin) and origin['status'] == 'CAPTURED'
        and origin['account_ref_sha256'] == value['account_ref_sha256'] and origin['symbol'] == 'XAUUSDT'
        and origin['source_sha256'] == capture.source_hash()
        and origin['completed_at_ms']+5000 < entry_decision_ms <= proof['origin']['completed_at_ms'],
        'ACCOUNT_OPENING_TIME_OR_ORIGIN')
    state = proof['state']
    from live.execution.demo_market_partition_runtime import _core
    require(_digest(_core(state)) == value['state_sha256']
        and state.get('market_partition') == value['market_partition'], 'ACCOUNT_OPENING_HISTORY')
    pilot = state.get('pilot_control')
    require((pilot is None and value['pilot_configuration_sha256'] is None)
        or (type(pilot) is dict and pilot.get('configuration_sha256') == value['pilot_configuration_sha256']),
        'ACCOUNT_OPENING_PILOT')
    # The original position/partition replay independently binds observed wallet
    # and existing owned history; the retained digest alone is never sufficient.
    from live.execution.demo_position_context import validate_origin
    context = state.get('position_context')
    if context is not None:
        validate_origin(origin, context, as_of_ms=origin['completed_at_ms'])
    else:
        # With no owned Gold history, both observations must contain the same
        # real opening wallet; no account-wide history is reset or discarded.
        def wallet(observed):
            reads = {row['kind']: row['data'] for row in observed['reads']}
            from decimal import Decimal
            return [Decimal(next(a for a in reads[k]['assets'] if a['asset'] == 'USDT')['walletBalance'])
                for k in ('account_before', 'account_after')]
        require(wallet(origin) == wallet(proof['origin']), 'ACCOUNT_OPENING_WALLET_CHANGED')
    require(capture.original.assess(origin, as_of_ms=origin['completed_at_ms'])['status'] == 'OBSERVED_FLAT_QUOTE'
        and (context is None or context['quantity'] == '0'), 'ACCOUNT_OPENING_NOT_FLAT')
    return origin
