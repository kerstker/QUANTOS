"""Deterministic, reproducible hashing for contract objects (CT-3).

A single canonical hash used across the platform so identical inputs always
produce an identical `reproducibility_hash`. Floats are rounded to a fixed
precision so platform-specific float repr (e.g. 0.1 + 0.2) cannot change the
hash.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

FLOAT_PRECISION = 10


def _canonicalize(obj: Any) -> Any:
    if isinstance(obj, float):
        return round(obj, FLOAT_PRECISION)
    if isinstance(obj, dict):
        return {str(k): _canonicalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canonicalize(v) for v in obj]
    return obj


def stable_hash(obj: Any) -> str:
    """Return a stable 64-hex-char sha256 of `obj` (order- and float-stable)."""
    payload = json.dumps(_canonicalize(obj), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def short_hash(obj: Any, length: int = 16) -> str:
    return stable_hash(obj)[:length]
