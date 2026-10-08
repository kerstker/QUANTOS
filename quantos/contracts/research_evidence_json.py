"""Unchanged bounded JSON rules shared by research readers and evidence contracts."""
import hashlib
import json
import re

from quantos.contracts.json_integrity import load_json_without_duplicate_keys, ascii_json_string_size

MAX_BYTES = 64_000_000
_JSON_ESCAPES = re.compile(r'[\x00-\x1f"\\]')
_SHORT_ESCAPES = '\b\t\n\f\r"\\'

def require(value, reason):
    if not value:
        raise ValueError('RESEARCH_ARTIFACT_' + reason)



def canonical(value):
    # Budget before serialization: repeated references cannot expand beyond the
    # new receipt's bound. Native payloads themselves retain their older limits.
    pending = [(value, 0)]
    budget = nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        require(depth <= 64 and nodes <= 1_000_000, 'BOUND')
        if type(item) is dict:
            require(all(type(k) is str for k in item), 'JSON_KEYS')
            budget += 2 + len(item)
            pending.extend((k, depth+1) for k in item)
            pending.extend((v, depth+1) for v in item.values())
        elif type(item) is list:
            budget += 2 + len(item)
            pending.extend((v, depth+1) for v in item)
        elif type(item) is str:
            require(len(item) <= MAX_BYTES, 'BOUND')
            ascii_size = ascii_json_string_size(item, maximum_bytes=MAX_BYTES-budget)
            if ascii_size is not None:
                budget += ascii_size
            else:
                budget += len(item.encode('utf-8')) + 2
                for escaped in _JSON_ESCAPES.finditer(item):
                    budget += 1 if escaped[0] in _SHORT_ESCAPES else 5
                    require(budget <= MAX_BYTES, 'BOUND')
        elif item is None or type(item) in (bool, int, float):
            budget += len(str(item)) + 1
        else:
            raise ValueError('RESEARCH_ARTIFACT_JSON_TYPE')
        require(budget <= MAX_BYTES, 'BOUND')
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                     allow_nan=False).encode()
    require(len(raw) <= MAX_BYTES, 'BOUND')
    return raw



def digest(value):
    return hashlib.sha256(value if type(value) is bytes else canonical(value)).hexdigest()



def decode(raw):
    require(type(raw) is bytes and len(raw) <= MAX_BYTES, 'BOUND')
    value = load_json_without_duplicate_keys(raw)
    require(canonical(value) == raw, 'CANONICAL')
    return value



def _require(value, reason):
    if not value:
        raise ValueError('RESEARCH_EVIDENCE_' + reason)



def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()



def _decode(raw):
    try:
        value = load_json_without_duplicate_keys(raw)
    except RecursionError as exc:
        raise ValueError('RESEARCH_EVIDENCE_JSON_DEPTH') from exc
    pending = [(value, 0)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        _require(depth <= 64 and nodes <= 1_000_000, 'JSON_BOUND')
        if type(item) is dict:
            pending.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
    _canonical(value)  # Reject nonfinite values, including deeply nested ones.
    return value
