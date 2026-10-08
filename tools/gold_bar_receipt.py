"""Read external three-candidate review receipts; never sign or admit a strategy."""
import hashlib
import time
from pathlib import Path

from quantos.runtime_paths import runtime_state_root
from tools.gold_bar_review import retained_request
from tools.gold_cost_receipt import REGISTRY, verify_registered_receipt, retain_receipt_check
from tools.gold_cost_review import _read, _encode


def run_check(params):
    if (type(params) is not dict or set(params) != {'attempt'}
            or type(params['attempt']) is not int or not 1 <= params['attempt'] <= 1000):
        raise ValueError('INVALID_GOLD_BAR_RECEIPT_ATTEMPT')
    root = runtime_state_root() / 'data/cache/gold_bar_research/results' / str(params['attempt'])
    return check_receipt(root, now_ms=time.time_ns() // 1000000)


def check_receipt(directory, *, now_ms):
    if type(now_ms) is not int or not 0 <= now_ms <= 2**63 - 1:
        raise ValueError('GOLD_BAR_RECEIPT_CHECK_TIME')
    root = Path(directory).absolute()
    request = retained_request(root)
    raw = _read(root, 'independent-request.json')
    if raw != _encode(request):
        raise ValueError('GOLD_BAR_RECEIPT_REQUEST_CHANGED')
    artifact_hash = hashlib.sha256(raw).hexdigest()
    verification, registry_hash, envelope_hash = verify_registered_receipt(
        root, artifact_hash=artifact_hash, now_ms=now_ms, registry_path=REGISTRY)
    if retained_request(root) != request:
        raise ValueError('GOLD_BAR_RECEIPT_REQUEST_CHANGED')
    result = dict(schema='GOLD_BAR_RESEARCH_RECEIPT_CHECK_V1', checked_at_ms=now_ms,
        artifact_sha256=artifact_hash, registry_sha256=registry_hash, envelope_sha256=envelope_hash,
        trial_ids=request['trial_ids'], result_hash=request['result_hash'], verification=verification,
        local_check='RETAINED_BINDINGS_ONLY_NOT_FRESH_REPRODUCTION',
        authoritative=False, economic_eligible=False, submission_enabled=False)
    return retain_receipt_check(root, result)
