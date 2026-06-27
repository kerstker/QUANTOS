"""Expression-level leakage detection for the cross-sectional alpha engine (P0 safety).

`alpha_expression` is "PIT-safe by construction" — its time-series operators only read DOWN a
column (an instrument's own past) and its cross-sectional operators only read ACROSS a row (the
same period). This module turns that claim into a MACHINE PROOF, per alpha: perturb every
strictly-future panel row, re-evaluate the expression, and require the as-of cross-section at the
target row to be BIT-INVARIANT. A correct (past-only) expression cannot change; any operator that
reads the future does. It is the cross-sectional analog of services/features/leakage.py (which
perturbs future Bars), and it lets the cs path COMPUTE `leakage_ok` instead of trusting it.

Pure-Python stdlib, deterministic (no RNG, no wall-clock), fail-closed (a panel too short to have
both history and a future row to perturb is NOT assessable -> None, never a silent pass).
"""
from __future__ import annotations

from typing import Optional, Sequence

from .alpha_expression import FieldPanel, evaluate

# Perturbation schemes applied to every strictly-future cell. `c + 1000.0` has NO fixed point, so it
# is GUARANTEED to change any value (the detector can never miss a leak for lack of a real change);
# the multiplicative schemes add robustness for non-linear operators (sign/rank/bucket) that could be
# coincidentally invariant to a single additive shift.
_SCHEMES = (
    lambda c: c + 1000.0,
    lambda c: c * 10.0 + 1.0,
    lambda c: c * -3.0 - 7.0,
)


def _perturb_future(panel: FieldPanel, target_row: int, scheme) -> FieldPanel:
    """A copy of `panel` with every cell in rows strictly after `target_row` mapped through `scheme`
    (None stays None); rows up to and including `target_row` are untouched."""
    t = len(panel.event_times)
    mats = {}
    for f, m in panel.matrices.items():
        mats[f] = [
            ([(scheme(v) if v is not None else None) for v in m[r]] if r > target_row else list(m[r]))
            for r in range(t)
        ]
    return FieldPanel(panel.instruments, panel.event_times, mats)


def _row_at(expr, panel: FieldPanel, target_row: int):
    return tuple(evaluate(expr, panel)[target_row])


def detect_expression_leakage(expr, panel: FieldPanel, target_row: int, *, schemes=_SCHEMES) -> bool:
    """True if the as-of cross-section at `target_row` is INVARIANT to perturbing all strictly-future
    rows (PIT-safe); False if any scheme changes it (the expression read future data = look-ahead).
    Exact equality is correct: identical past inputs through deterministic operators yield identical
    floats, so a real (non-leaking) operator reproduces the row bit-for-bit."""
    base = _row_at(expr, panel, target_row)
    for scheme in schemes:
        if _row_at(expr, _perturb_future(panel, target_row, scheme), target_row) != base:
            return False
    return True


def compute_leakage_ok(expr, panel: FieldPanel, *, rows: Optional[Sequence[int]] = None,
                       schemes=_SCHEMES) -> Optional[bool]:
    """Machine-proof of PIT-safety for one cross-sectional alpha expression. Returns:
      True  — invariant at every assessed target row (no look-ahead),
      False — a future perturbation changed an as-of value (LEAKAGE),
      None  — not assessable (panel too short to have both history and a strictly-future row).

    Assesses several target rows spread across the panel by default so a leak that only bites near
    the end (or only with enough history) is still caught. The result is the `leakage_ok` a caller
    feeds to AlphaEvaluator/ValidationGate; None keeps the evaluator's fail-closed human-review path.
    """
    t = len(panel.event_times)
    if t < 2:
        return None
    if rows is None:
        last = t - 2                                   # last row that still has a future row to perturb
        rows = sorted({last, last // 2, max(0, last - 1), max(0, t // 3)})
    assessable = [r for r in rows if 0 <= r <= t - 2]
    if not assessable:
        return None
    for r in assessable:
        if not detect_expression_leakage(expr, panel, r, schemes=schemes):
            return False
    return True
