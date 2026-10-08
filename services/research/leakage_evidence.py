"""断点1 (方案A): compute the `leakage_ok` evidence bit IN the search/driver layer — the only place that
has the raw bars + the factor's compute function — and feed it into `AlphaEvaluator.evaluate(leakage_ok=...)`.

The agent loop only sees pre-computed `PanelPeriod`s, so it cannot run the PIT-leakage detector itself; this
module adapts a trailing-window `SIGNAL_OP` into a real `detect_leakage` ComputeFn and returns a single
fail-closed verdict: True (every sampled decision time is PIT-safe), False (some future perturbation changed a
past value = LEAKAGE), or None (uncomputable -> the evaluator's fail-closed NOT-ASSESSED path stays in force).

Pure stdlib + deterministic (no RNG / no wall-clock). It NEVER weakens containment: a True only un-blocks the
leakage gate; promotion to APPROVED remains human-only (auto_approved=0) regardless of evidence.
"""
from __future__ import annotations

import math
from typing import Callable, Optional, Sequence

from quantos.contracts.data import Bar
from quantos.contracts.feature import FeatureValue
from services.features.leakage import detect_leakage
from services.research.signals import SIGNAL_OPS

ComputeFn = Callable[[Sequence[Bar]], list]


def compute_fn_from_signal(signal_fn, *, feature_id: str = "f") -> ComputeFn:
    """Build a leakage ComputeFn from ANY trailing-closes signal `signal_fn: Sequence[float] -> Optional[float]`.
    For every instrument it computes the signal at each bar's event_time from the closes AT-OR-BEFORE that time.
    A signal that reads no future bar (true for a SIGNAL_OP at fixed window, a GP expression tree, any trailing
    op) is reported leak-free by detect_leakage; one that peeks ahead is caught. Generalises signal_compute_fn
    so the GP/tree path reuses the SAME detector instead of asserting PIT-safety."""

    def compute(bars: Sequence[Bar]) -> list:
        by_inst: dict = {}
        for b in bars:
            by_inst.setdefault(b.instrument_id, []).append(b)
        out: list = []
        for inst, bs in by_inst.items():
            bs = sorted(bs, key=lambda b: b.event_time)
            closes: list = []
            for b in bs:
                closes.append(b.close)
                s = signal_fn(closes)
                if s is not None and math.isfinite(s):
                    out.append(FeatureValue(feature_id=feature_id, instrument_id=inst,
                                            event_time=b.event_time, knowledge_time=b.knowledge_time,
                                            value=float(s)))
        return out

    return compute


def signal_compute_fn(op: str, window: int, *, feature_id: str = "f") -> ComputeFn:
    """Adapt a trailing-window SIGNAL_OP into a leakage ComputeFn. `op` must be a key of SIGNAL_OPS."""
    fn = SIGNAL_OPS[op]
    return compute_fn_from_signal(lambda closes: fn(closes, window), feature_id=feature_id)


def leakage_ok_for_signal(signal_fn, bars: Sequence[Bar], target_times: Sequence[int], *,
                          feature_id: str = "f") -> Optional[bool]:
    """Convenience: assess leakage of an arbitrary trailing-closes signal over the sampled decision times."""
    return leakage_ok_over_times(compute_fn_from_signal(signal_fn, feature_id=feature_id), bars, target_times)


def leakage_ok_over_times(compute: ComputeFn, bars: Sequence[Bar], target_times: Sequence[int], *,
                          feature_id: str = "f") -> Optional[bool]:
    """Run the REAL detect_leakage at each target decision time. Returns True iff every assessable time is
    PIT-safe, False as soon as one leaks, None if nothing was assessable (-> evaluator stays fail-closed)."""
    assessed = False
    unassessed = False
    for t in target_times:
        if type(t) is not int:
            unassessed = True
            continue
        try:
            if not any(value.event_time == t for value in compute(bars)):
                unassessed = True
                continue
            res = detect_leakage(feature_id, compute, bars, target_event_time=t)
        except Exception:
            unassessed = True
            continue
        assessed = True
        if not res.passed:
            return False
    return True if assessed and not unassessed else None


def signal_leakage_ok(op: str, window: int, bars: Sequence[Bar], target_times: Sequence[int]) -> Optional[bool]:
    """Convenience: build the SIGNAL_OP ComputeFn and assess leakage over the sampled decision times."""
    return leakage_ok_over_times(signal_compute_fn(op, window), bars, target_times)


def volume_compute_fn(op: str, window: int, *, feature_id: str = "f") -> ComputeFn:
    """Like signal_compute_fn but for a VOLUME_OP, which reads the trailing (closes, volumes) history. Same
    PIT property: a correct trailing volume op reads no future bar, so perturbing future price OR volume
    cannot change a past value -> detected leak-free; one that peeks at future volume is caught. `op` must be
    a key of VOLUME_OPS."""
    from services.research.volume_signals import VOLUME_OPS
    fn = VOLUME_OPS[op]

    def compute(bars: Sequence[Bar]) -> list:
        by_inst: dict = {}
        for b in bars:
            by_inst.setdefault(b.instrument_id, []).append(b)
        out: list = []
        for inst, bs in by_inst.items():
            bs = sorted(bs, key=lambda b: b.event_time)
            closes: list = []
            vols: list = []
            for b in bs:
                closes.append(b.close)
                vols.append(b.volume)
                s = fn(closes, vols, window)
                if s is not None and math.isfinite(s):
                    out.append(FeatureValue(feature_id=feature_id, instrument_id=inst,
                                            event_time=b.event_time, knowledge_time=b.knowledge_time,
                                            value=float(s)))
        return out

    return compute


def volume_leakage_ok(op: str, window: int, bars: Sequence[Bar], target_times: Sequence[int]) -> Optional[bool]:
    """Convenience: build the VOLUME_OP ComputeFn and assess leakage over the sampled decision times."""
    return leakage_ok_over_times(volume_compute_fn(op, window), bars, target_times)


def panel_expression_leakage_ok(
    expr,
    panel,
    target_rows: Sequence[int],
    *,
    evaluate_fn=None,
    tolerance: float = 1e-12,
) -> Optional[bool]:
    """Check a FieldPanel expression by full-history versus prefix invariance.

    Funding/OI/premium expressions cannot be represented as OHLCV ``Bar``
    ComputeFns.  Their exact analogue is to evaluate the expression on the full
    PIT panel and on a panel truncated at each decision row.  Any future read
    changes the decision-row value and returns False.  Unassessable input stays
    None so the evaluator remains fail closed.
    """
    if not math.isfinite(float(tolerance)) or tolerance < 0.0:
        raise ValueError("tolerance must be finite and non-negative")
    from services.research import alpha_expression as ax

    evaluator = ax.evaluate if evaluate_fn is None else evaluate_fn
    try:
        full = evaluator(expr, panel)
    except Exception:
        return None
    assessed = False
    unassessed = False
    for raw_row in target_rows:
        if isinstance(raw_row, bool) or not isinstance(raw_row, int):
            unassessed = True
            continue
        if raw_row < 0 or raw_row >= len(panel.event_times):
            unassessed = True
            continue
        prefix = ax.FieldPanel(
            panel.instruments,
            panel.event_times[: raw_row + 1],
            {
                name: matrix[: raw_row + 1]
                for name, matrix in panel.matrices.items()
            },
        )
        try:
            prefix_row = evaluator(expr, prefix)[-1]
            full_row = full[raw_row]
        except Exception:
            unassessed = True
            continue
        if len(prefix_row) != len(full_row):
            return False
        assessed = True
        for prefix_value, full_value in zip(prefix_row, full_row):
            if prefix_value is None or full_value is None:
                if prefix_value is not full_value:
                    return False
                continue
            try:
                left, right = float(prefix_value), float(full_value)
            except (TypeError, ValueError, OverflowError):
                return False
            if not math.isfinite(left) or not math.isfinite(right):
                return False
            if abs(left - right) > tolerance:
                return False
    return True if assessed and not unassessed else None
