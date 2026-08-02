"""Offline/online parity comparison (727 §6.3 / Factor Garden F4, 2026-07-27).

Pure functions comparing two computed representations of the SAME factor under the SAME
row-selection policy (offline full-panel evaluation vs the online/live decision path).
The three production row-selection policies (live bounded lookback + coverage filter /
forward-paper cadence rows / dry-run full-history weekly grid) are NOT unified by this
module — parity is judged within one policy; unifying the policies is a behavior change
needing its own review.

Fail-closed: an empty key intersection NEVER reports OK (PARITY_INSUFFICIENT_OVERLAP) —
no evidence of parity is not evidence of parity.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

PARITY_OK = "PARITY_OK"
PARITY_MISMATCH = "PARITY_MISMATCH"
PARITY_INSUFFICIENT_OVERLAP = "PARITY_INSUFFICIENT_OVERLAP"

_MAX_DETAIL = 10        # deterministic cap on per-mismatch detail rows


@dataclass(frozen=True)
class ParityReport:
    factor_id: str
    compared_cells: int
    mismatches: int
    max_abs_diff: Optional[float]      # None when nothing finite was compared
    tolerance: float
    verdict: str                       # PARITY_OK | PARITY_MISMATCH | PARITY_INSUFFICIENT_OVERLAP
    detail: tuple = field(default_factory=tuple)   # first _MAX_DETAIL mismatch rows, deterministic order


def _validate(factor_id: str, tolerance: float) -> float:
    if not isinstance(factor_id, str) or not factor_id.strip():
        raise ValueError("factor_id must be a non-empty string")
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) \
            or not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be a non-negative finite number")
    return float(tolerance)


def _cell_diff(a, b) -> Optional[float]:
    """|a-b| with honest None/NaN/Inf semantics; None = "not comparable" (a mismatch)."""
    if a is None or b is None:
        return None if a is b else math.inf
    fa, fb = float(a), float(b)
    if fa == fb:                       # covers inf == inf and exact equality
        return 0.0
    if not (math.isfinite(fa) and math.isfinite(fb)):
        return math.inf                # NaN vs anything, inf vs finite — never "within tolerance"
    return abs(fa - fb)


def compare_series(
    offline: Mapping,
    online: Mapping,
    *,
    factor_id: str,
    tolerance: float = 0.0,
) -> ParityReport:
    """Compare two {key: value} factor series on their key intersection (deterministic
    order: keys sorted by repr). Empty intersection -> PARITY_INSUFFICIENT_OVERLAP."""
    tolerance = _validate(factor_id, tolerance)
    keys = sorted(set(offline) & set(online), key=repr)
    if not keys:
        return ParityReport(factor_id.strip(), 0, 0, None, tolerance,
                            PARITY_INSUFFICIENT_OVERLAP,
                            (("no_common_keys", len(offline), len(online)),))
    mismatches = 0
    max_abs: Optional[float] = None
    detail = []
    for key in keys:
        diff = _cell_diff(offline[key], online[key])
        if diff is None:                              # both None — equal, not compared
            continue
        max_abs = diff if max_abs is None else max(max_abs, diff)
        if diff > tolerance:
            mismatches += 1
            if len(detail) < _MAX_DETAIL:
                detail.append((repr(key), offline[key], online[key], diff))
    verdict = PARITY_OK if mismatches == 0 else PARITY_MISMATCH
    compared = len(keys)
    return ParityReport(factor_id.strip(), compared, mismatches, max_abs, tolerance,
                        verdict, tuple(detail))


def compare_panels(
    offline_matrix: Sequence,
    online_matrix: Sequence,
    *,
    factor_id: str,
    tolerance: float = 0.0,
) -> ParityReport:
    """Matrix version over (T x N) Optional[float] cells. Shape mismatch -> PARITY_MISMATCH
    with a shape detail row (never raises)."""
    tolerance = _validate(factor_id, tolerance)
    off = [list(row) for row in offline_matrix]
    onl = [list(row) for row in online_matrix]
    shape_off = (len(off), max((len(r) for r in off), default=0))
    shape_onl = (len(onl), max((len(r) for r in onl), default=0))
    if shape_off != shape_onl or any(len(r) != shape_off[1] for r in off) \
            or any(len(r) != shape_onl[1] for r in onl):
        return ParityReport(factor_id.strip(), 0, 1, None, tolerance, PARITY_MISMATCH,
                            (("shape", shape_off, shape_onl),))
    if shape_off[0] == 0 or shape_off[1] == 0:
        return ParityReport(factor_id.strip(), 0, 0, None, tolerance,
                            PARITY_INSUFFICIENT_OVERLAP, (("empty_panel", shape_off),))
    offline = {}
    online = {}
    for ti in range(shape_off[0]):
        for ii in range(shape_off[1]):
            offline[(ti, ii)] = off[ti][ii]
            online[(ti, ii)] = onl[ti][ii]
    return compare_series(offline, online, factor_id=factor_id, tolerance=tolerance)
