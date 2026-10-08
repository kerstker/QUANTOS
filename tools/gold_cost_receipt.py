"""Verify retained external gold review receipts using the existing public registry.

No signer, key enrollment, economic admission or execution capability is added.
Every check is retained separately so expiry/revocation cannot rewrite history.
"""
import hashlib
import time
from pathlib import Path

from quantos.runtime_paths import runtime_state_root
from services.governance.artifact_receipt_auth import (
    ReceiptAuthorityKey, SignedArtifactVerificationReceipt,
)
from services.governance.ai_factory_verification import verify_research_receipt
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.gold_cost_review import review_request, _read, _encode

REGISTRY = Path(__file__).resolve().parents[1]/'config/artifact_receipt_authority_registry.json'


def run_check(params):
    """Recheck an existing trial; never replay or alter its terminal record."""
    if (type(params) is not dict or set(params) != {'attempt'}
            or type(params['attempt']) is not int or not 1 <= params['attempt'] <= 1000):
        raise ValueError('INVALID_GOLD_RECEIPT_ATTEMPT')
    root = runtime_state_root()/'data/cache/gold_cost_replay/results'/str(params['attempt'])
    return check_receipt(root, now_ms=time.time_ns()//1000000)


def check_receipt(directory, *, now_ms):
    if type(now_ms) is not int or not 0 <= now_ms <= 2**63-1:
        raise ValueError('GOLD_RECEIPT_CHECK_TIME')
    root = Path(directory).absolute()
    request = review_request(root)
    artifact_hash = hashlib.sha256(_encode(request)).hexdigest()
    verification, registry_hash, envelope_hash = verify_registered_receipt(
        root, artifact_hash=artifact_hash, now_ms=now_ms, registry_path=REGISTRY)
    result = {'schema': 'GOLD_RESEARCH_RECEIPT_CHECK_V1', 'checked_at_ms': now_ms,
              'artifact_sha256': artifact_hash, 'registry_sha256': registry_hash,
              'envelope_sha256': envelope_hash, 'trial_id': request['trial_id'],
              'trial_terminal': request['terminal'], 'verification': verification,
              'authoritative': False, 'economic_eligible': False}
    return retain_receipt_check(root, result)


def verify_registered_receipt(root, *, artifact_hash, now_ms, registry_path):
    """Shared existing signature checks; no key enrollment or economic admission."""
    registry_hash = envelope_hash = None
    try:
        registry_raw = _read(registry_path.parent, registry_path.name)
        registry_hash = hashlib.sha256(registry_raw).hexdigest()
        registry = load_json_without_duplicate_keys(registry_raw)
        if (not isinstance(registry, dict) or registry.get('schema_version') != 2
                or not isinstance(registry.get('keys'), dict) or len(registry['keys']) > 128):
            raise ValueError('GOLD_RECEIPT_REGISTRY_SCHEMA')
        keys = [ReceiptAuthorityKey.model_validate(row) for row in registry['keys'].values()]
        if len({(key.authority_id, key.key_id) for key in keys}) != len(keys):
            raise ValueError('GOLD_RECEIPT_AMBIGUOUS_AUTHORITY')
        envelope = None
        key = None
        path = root/'independent-envelope.json'
        if path.exists() or path.is_symlink():
            raw = _read(root, path.name)
            if len(raw) > 65536:
                raise ValueError('GOLD_RECEIPT_ENVELOPE_SIZE')
            envelope_hash = hashlib.sha256(raw).hexdigest()
            envelope = SignedArtifactVerificationReceipt.model_validate(load_json_without_duplicate_keys(raw))
            key = next((k for k in keys if (k.authority_id, k.key_id)
                        == (envelope.authority_id, envelope.key_id)), None)
        verification = verify_research_receipt(artifact_hash, envelope, key, now=now_ms)
    except (ValueError, OSError, TypeError) as exc:
        verification = {'status': 'REJECTED', 'error_type': type(exc).__name__,
                        'economic_eligible': False, 'authoritative': False}
    return verification, registry_hash, envelope_hash


def retain_receipt_check(root, result):
    raw = _encode(result)
    area = root/'receipt-checks'
    if area.resolve() != area.absolute():
        raise ValueError('GOLD_RECEIPT_UNSAFE_OUTPUT')
    area.mkdir(exist_ok=True)
    name = hashlib.sha256(raw).hexdigest()+'.json'
    path = area/name
    if path.exists() or path.is_symlink():
        if _read(area, name) != raw:
            raise ValueError('GOLD_RECEIPT_CHECK_CHANGED')
    else:
        with path.open('xb') as stream:
            stream.write(raw)
    return result
