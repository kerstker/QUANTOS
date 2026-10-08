"""The original TrialLedger event identity shared with offline result readers."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

TRIAL_RECORD_FIELDS = ('trial_id', 'alpha_id', 'family_id', 'exact_definition_id',
                       'validation_policy_digest', 'status', 'event_index', 'at')


def trial_record_bytes(record: Mapping, global_seq: int) -> bytes:
    if not isinstance(record, Mapping) or set(record) != set(TRIAL_RECORD_FIELDS):
        raise ValueError('trial identity fields')
    for field in TRIAL_RECORD_FIELDS[:5]:
        if not isinstance(record[field], str) or not re.fullmatch('[0-9a-f]{64}', record[field]):
            raise ValueError('trial identity digest')
    if not isinstance(record['status'], str) or record['status'] not in {
        'PLANNED', 'RUNNING', 'PASSED', 'FAILED', 'CANCELLED'
    }:
        raise ValueError('trial identity status')
    for value in (record['event_index'], record['at'], global_seq):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError('trial identity integer')
    if record['event_index'] < 0 or global_seq < 1:
        raise ValueError('trial identity sequence')
    return json.dumps(dict(record, global_seq=global_seq), sort_keys=True,
                      separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def trial_event_hash(record: Mapping, *, global_seq: int, previous_hash: str,
                     global_previous_hash: str) -> str:
    """Preserve SHA256((trial_prev + global_prev).encode() + original JSON)."""
    for value in (previous_hash, global_previous_hash):
        if not isinstance(value, str) or (value and not re.fullmatch('[0-9a-f]{64}', value)):
            raise ValueError('trial identity previous hash')
    return hashlib.sha256((previous_hash + global_previous_hash).encode()
                          + trial_record_bytes(record, global_seq)).hexdigest()
