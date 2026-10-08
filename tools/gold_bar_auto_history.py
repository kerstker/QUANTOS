"""Capture one immutable public warmup per installed episode; no recapture."""
import hashlib
import os
from pathlib import Path

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.execution.perpetual_public_replay import decode_public_gzip
from services.research.gold_bar_dataset import load_bars
from tools.demo_resident_recovery import _check_path
from tools.gold_bar_capture import capture
from tools.gold_bar_worker_inputs import protected_tree
from tools.gold_cost_review import _read
from tools import gold_bar_pilot_capacity as pilot_capacity


def load(root, bundle, *, uid, read_only=False):
    require(type(read_only) is bool, 'BAR_AUTO_HISTORY_READ_MODE')
    root = Path(root).absolute()
    require(root.resolve() == root, 'BAR_AUTO_HISTORY_ROOT')
    _check_path(root, uid, directory=True)
    original = pilot_capacity.context(root, bundle, uid=uid)
    capacity = None
    if original is not None:
        area, capacity = pilot_capacity.storage(root, 'history', original, bundle, uid=uid, read_only=read_only)
    else:
        area = root/'strategy-bar-history-work'
        if not read_only:
            area.mkdir(mode=0o700, exist_ok=True)
    _check_path(area, uid, directory=True)
    if capacity is not None:
        entries = pilot_capacity.usage(area, capacity, 'history', uid)
    else:
        entries = list(area.iterdir())
        require(len(entries) <= 32, 'BAR_AUTO_HISTORY_BUDGET')
        size = sum(row[2] for path in entries for row in protected_tree(path, uid))
        require(size <= 256*1024*1024, 'BAR_AUTO_HISTORY_BUDGET')
    path = area/_digest(bundle)
    if not path.exists() and not path.is_symlink():
        require(not read_only, 'BAR_AUTO_HISTORY_NOT_RETAINED')
        if capacity is not None:
            pilot_capacity.allocate_capture(area, capacity, 'history', uid, bundle)
        else:
            require(len(entries) < 32, 'BAR_AUTO_HISTORY_BUDGET')
        capture(path, protected_network=True)
    if capacity is not None:
        pilot_capacity.usage(area, capacity, 'history', uid)
        require(pilot_capacity.context(root, bundle, uid=uid) == original, 'BAR_PILOT_ORIGINAL_CHANGED')
    protected_tree(path, uid)
    names = ('receipt.json', 'summary.json', 'response.json', 'response.wire')
    raw = {name:_read(path, name) for name in names}
    receipt, summary = decode(raw['receipt.json']), decode(raw['summary.json'])
    require(receipt.get('network_mode') == 'PROTECTED_DEMO_NETWORK_V1'
        and receipt.get('content_encoding') in ('identity', 'gzip')
        and summary.get('schema') == 'GOLD_BAR_HISTORY_CAPTURE_V1'
        and summary.get('status') == 'CAPTURED_FOR_RESEARCH'
        and summary.get('submission_enabled') is False, 'BAR_AUTO_HISTORY_INCOMPLETE')
    digest = hashlib.sha256(raw['response.json']).hexdigest()
    require(digest == receipt['response_sha256'] == summary['source_sha256']
        and hashlib.sha256(raw['response.wire']).hexdigest() == receipt['wire_sha256'], 'BAR_AUTO_HISTORY_BYTES')
    decoded = decode_public_gzip(raw['response.wire']) if receipt['content_encoding'] == 'gzip' else raw['response.wire']
    require(decoded == raw['response.json'], 'BAR_AUTO_HISTORY_ENCODING')
    bars = load_bars(raw['response.json'], requested_at_ms=receipt['requested_at_ms'], received_at_ms=receipt['received_at_ms'])
    require(len(bars) == summary['count'], 'BAR_AUTO_HISTORY_COUNT')
    require(all(_read(path, name) == value for name, value in raw.items()), 'BAR_AUTO_HISTORY_CHANGED')
    if read_only:
        return path
    for file in path.iterdir():
        _check_path(file, uid, directory=False)
        with file.open('rb') as handle:
            os.fsync(handle.fileno())
    for directory in (path, area):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return path
