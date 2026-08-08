"""Deterministic, reproducible hashing for contract objects (CT-3).

A single canonical hash used across the platform so identical inputs always
produce an identical `reproducibility_hash`. Floats are rounded to a fixed
precision so platform-specific float repr (e.g. 0.1 + 0.2) cannot change the
hash.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
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


def stable_json(obj: Any) -> str:
    """Return the exact canonical JSON bytestring consumed by :func:`stable_hash`.

    This is useful at language boundaries where JSON parsing erases the
    distinction between an integer and an integer-shaped float such as ``1.0``.
    """
    return json.dumps(
        _canonicalize(obj), sort_keys=True, separators=(",", ":"), default=str
    )


def stable_hash(obj: Any) -> str:
    """Return a stable 64-hex-char sha256 of `obj` (order- and float-stable)."""
    return hashlib.sha256(stable_json(obj).encode("utf-8")).hexdigest()


def short_hash(obj: Any, length: int = 16) -> str:
    return stable_hash(obj)[:length]


def source_file_sha256(path: str | Path) -> str:
    """Hash source text independently of checkout newline policy.

    Git stores governed text as LF, while an editor on Windows can temporarily
    materialize the same tracked source with CRLF without producing a Git diff.
    Evidence must bind the logical source content, not that workstation detail.
    This helper is intentionally for UTF-8 source/config text only; binary and
    dataset evidence must continue to hash exact bytes.
    """

    content = Path(path).read_bytes()
    canonical = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(canonical).hexdigest()
