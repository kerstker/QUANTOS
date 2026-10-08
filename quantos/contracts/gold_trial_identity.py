"""Exact historical identity of a native Gold archive trial definition.

Keep ASCII escaping and the original JSON separators: this is not the general
stable_hash or the typed perpetual run-identity format. Callers validate native
source and candidate semantics before using this identity as evidence.
"""
import hashlib
import json


def gold_trial_definition_id(*, input_sha256, candidate, source):
    preimage = dict(input_sha256=input_sha256, candidate=candidate, source=source)
    raw = json.dumps(preimage, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()
