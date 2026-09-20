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


def stable_json(obj: Any, *, allow_non_finite: bool = True) -> str:
    """Return the exact canonical JSON bytestring consumed by :func:`stable_hash`.

    This is useful at language boundaries where JSON parsing erases the
    distinction between an integer and an integer-shaped float such as ``1.0``.

    ``allow_non_finite`` (2026-09-20 审计 P0-1)
    ------------------------------------------
    ``json.dumps`` defaults to ``allow_nan=True``, which emits the bare literals
    ``NaN`` / ``Infinity`` / ``-Infinity``.  Those are **not** RFC 8259 JSON, and
    their effect here is worse than a parse error: a non-finite limit hashes to a
    perfectly ordinary-looking 64-hex digest, so identity-critical consumers
    (limits hashes, dataset versions, evidence bindings) would bind a
    semantically meaningless configuration as if it were a real one.

    The default stays permissive on purpose.  Flipping it globally breaks real
    in-repo producers of non-finite floats (``services/alpha/walk_forward.py``,
    ``services/alpha/decay.py``, ``services/intelligence/search_strategy.py``,
    ``services/portfolio/signal_adapter.py``, …) and
    ``tests/research/test_single_asset_rule_backtest.py`` asserts
    ``summary['profit_factor'] == math.inf``.  Callers that must never bind a
    non-finite value pass ``allow_non_finite=False`` and get a ``ValueError``
    instead of a plausible-looking digest.
    """
    return json.dumps(
        _canonicalize(obj),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=allow_non_finite,
    )


def stable_hash(obj: Any, *, allow_non_finite: bool = True) -> str:
    """Return a stable 64-hex-char sha256 of `obj` (order- and float-stable).

    Pass ``allow_non_finite=False`` where the payload is an identity that must
    never encode a non-finite number (see :func:`stable_json`).
    """
    return hashlib.sha256(
        stable_json(obj, allow_non_finite=allow_non_finite).encode("utf-8")
    ).hexdigest()


def short_hash(obj: Any, length: int = 16, *, allow_non_finite: bool = True) -> str:
    return stable_hash(obj, allow_non_finite=allow_non_finite)[:length]


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
