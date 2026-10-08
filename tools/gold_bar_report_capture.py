"""One protected, retained reported-account capture for a bar episode."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from itertools import islice
import os
import re

from live.execution.demo_batch_risk import _digest
from live.execution.demo_prepared_execution import _load_proof
from live.execution.demo_pre_submit import _retain
from tools.gold_bar_reported_feedback import require


def _sync(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def capture_once(root, *, bundle_sha256, prepared, dispatch, stream, account, source_commit, order_windows=None, opening_record=None):
    """Caller owns the original preparation lease; partial captures stay consumed."""
    from tools.demo_resident_recovery import require_custody, _check_path
    from tools.demo_executor_stream import fixed_account
    from tools.demo_reported_recovery import capture_inputs, _read
    root = Path(root).absolute()
    require(root == require_custody() and (root, account) == fixed_account(), 'CAPTURE_CUSTODY')
    require(type(bundle_sha256) is str and re.fullmatch('[a-f0-9]{64}', bundle_sha256) is not None
        and type(source_commit) is str and re.fullmatch('[a-f0-9]{40}', source_commit) is not None,
        'CAPTURE_IDENTITY')
    require(type(prepared) in (tuple, list) and 2 <= len(prepared) <= 32, 'CAPTURE_BATCHES')
    if order_windows is not None:
        require(type(order_windows) is list and len(order_windows) == len(prepared)
            and all(type(w) is dict and set(w) == {'start_ms', 'end_ms'}
                and all(type(w[k]) is int for k in w) and 0 < w['end_ms']-w['start_ms'] <= 150000
                for w in order_windows), 'CAPTURE_ORDER_WINDOWS')
    original_windows = deepcopy(order_windows)
    proofs = [_load_proof(p) for p in prepared]
    orders = [o for p in proofs for o in p['orders']]
    opening = proofs[0]['origin']
    if opening_record is not None:
        from tools.gold_bar_cash import check_account_opening
        opening = check_account_opening(opening_record, proof=proofs[0], bundle_sha256=bundle_sha256,
            source_commit=source_commit, entry_decision_ms=proofs[0]['execution_constraints']['not_before_ms'])
    require(2 <= len(orders) <= 32 and all(p['context']['account_ref_sha256'] == account for p in proofs)
        and all(o['symbol'] == 'XAUUSDT' for o in orders), 'CAPTURE_ACCOUNT_SCOPE')
    before = dispatch.evidence_snapshot()
    binding = dict(bundle_sha256=bundle_sha256, account_ref_sha256=account, source_commit=source_commit,
        proof_sha256=[_digest(p) for p in proofs], dispatch_sha256=_digest(before), submission_enabled=False,
        capture_window_policy='BOUNDED_SEVEN_DAY_STATEMENTS_V1')
    if opening_record is not None:
        binding['opening_record_sha256'] = _digest(opening_record)
    if order_windows is not None:
        binding['order_windows'] = original_windows
    parent = root/'strategy-bar-reported-captures'
    parent.mkdir(mode=0o700, exist_ok=True)
    _check_path(parent, root.stat().st_uid, directory=True)
    area = parent/bundle_sha256
    fresh = False
    try:
        area.mkdir(mode=0o700)
        fresh = True
    except FileExistsError:
        _check_path(area, root.stat().st_uid, directory=True)
    if fresh:
        # This fsynced binding reserves the only collection attempt before any GET.
        _retain(area/'binding', binding)
        for directory in (area, parent, root):
            _sync(directory)
        if opening_record is not None:
            import time
            require(original_windows is not None, 'CAPTURE_ORIGINAL_WINDOWS_REQUIRED')
            end = max(w['end_ms'] for w in original_windows)
            now = time.time_ns()//1_000_000
            require(end <= now, 'CAPTURE_FUTURE_WINDOW')
            # A flat query may arrive immediately after the final fill. Wait
            # outside all signal/grant deadlines, without moving any receipt.
            remaining = end+5001-now
            if remaining > 0:
                time.sleep(remaining/1000)
            require(time.time_ns()//1_000_000 >= max(now, end+5001), 'CAPTURE_CLOCK_ROLLBACK')
        risk = proofs[0]['risk']['input_snapshot']
        inputs = capture_inputs(root, account, source_commit, opening=opening,
            system_orders=orders, day_replay=True,
            adv_notional=str(Decimal(str(risk['market']['daily_volume_base']))*
                             Decimal(str(risk['market']['reference_price']))),
            participation_rate=str(risk['native_limits']['max_participation_rate']), stream=stream,
            **({'order_windows': deepcopy(original_windows)} if order_windows is not None else {}))
        require(dispatch.evidence_snapshot() == before
            and all(_load_proof(p) == original for p, original in zip(prepared, proofs, strict=True)),
            'CAPTURE_ORIGINALS_CHANGED')
        _retain(area/'inputs', dict(binding=binding, inputs=inputs))
        _sync(area)
    else:
        bound_path = area/'binding'/(_digest(binding)+'.json')
        require(bound_path.exists(), 'CAPTURE_BINDING_CHANGED_OR_INCOMPLETE')
        original = _read(bound_path, root, 16000000)
        require(original == binding, 'CAPTURE_BINDING_CHANGED')
    directory = area/'inputs'
    require(directory.exists() and not directory.is_symlink(), 'CAPTURE_INCOMPLETE')
    _check_path(directory, root.stat().st_uid, directory=True)
    paths = list(islice(directory.iterdir(), 2))
    require(len(paths) == 1, 'CAPTURE_INPUT_COUNT')
    value = _read(paths[0], root, 16000000)
    require(value['binding'] == binding and value['inputs']['opening'] == opening
        and value['inputs']['system_orders'] == orders and dispatch.evidence_snapshot() == before
        and all(_load_proof(p) == original for p, original in zip(prepared, proofs, strict=True)),
        'CAPTURE_RETAINED_BINDING')
    require(order_windows == original_windows, 'CAPTURE_ORDER_WINDOWS_CHANGED')
    return deepcopy(value['inputs'])
