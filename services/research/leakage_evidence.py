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


def signal_compute_fn(op: str, window: int, *, feature_id: str = "f") -> ComputeFn:
    """Adapt a trailing-window SIGNAL_OP into a leakage ComputeFn. For every instrument it computes the signal
    at each bar's event_time from the closes AT-OR-BEFORE that time (PIT). Because a correct trailing op reads
    no future bar, perturbing future bars cannot change a past value -> detect_leakage reports it leak-free;
    an op that peeks ahead is caught. `op` must be a key of SIGNAL_OPS."""
    fn = SIGNAL_OPS[op]

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
                s = fn(closes, window)
                if s is not None and math.isfinite(s):
                    out.append(FeatureValue(feature_id=feature_id, instrument_id=inst,
                                            event_time=b.event_time, knowledge_time=b.knowledge_time,
                                            value=float(s)))
        return out

    return compute


def leakage_ok_over_times(compute: ComputeFn, bars: Sequence[Bar], target_times: Sequence[int], *,
                          feature_id: str = "f") -> Optional[bool]:
    """Run the REAL detect_leakage at each target decision time. Returns True iff every assessable time is
    PIT-safe, False as soon as one leaks, None if nothing was assessable (-> evaluator stays fail-closed)."""
    assessed = False
    for t in target_times:
        try:
            res = detect_leakage(feature_id, compute, bars, target_event_time=int(t))
        except Exception:
            continue
        assessed = True
        if not res.passed:
            return False
    return True if assessed else None


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
