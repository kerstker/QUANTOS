"""Original validation receipt hashes, without a ledger or authority reader."""
import hashlib
import json


def receipt_payload_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode()).hexdigest()


def receipt_row_hash(*, trial_id, stage, event_index, state, payload_hash,
                     global_seq, prev_hash, owner, lease_until, attempt):
    value = dict(trial_id=trial_id, stage=stage, event_index=event_index,
                 state=state, payload_hash=payload_hash, global_seq=global_seq,
                 prev_hash=prev_hash, owner=owner, lease_until=lease_until, attempt=attempt)
    return receipt_payload_hash(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                          ensure_ascii=True, allow_nan=False))
