"""Leakage detection (Phase 3) — static and dynamic.

Dynamic detection is the strong guarantee: perturb every bar AFTER the target
time and recompute. If the feature value at the target time changes, the feature
read future data → it leaks. A correct (past-only) feature is invariant to
future perturbation.
"""
from __future__ import annotations

from typing import Callable, Sequence

from quantos.contracts.data import Bar
from quantos.contracts.feature import FeatureValue, LeakageCheckResult

ComputeFn = Callable[[Sequence[Bar]], list[FeatureValue]]


def _perturb_future(bars: Sequence[Bar], target_event_time: int) -> list[Bar]:
    out: list[Bar] = []
    for b in bars:
        if b.event_time > target_event_time:
            v = b.close * 10.0 + 1.0   # large, consistent change; keep OHLC valid (flat bar)
            out.append(Bar(instrument_id=b.instrument_id, event_time=b.event_time,
                           knowledge_time=b.knowledge_time, open=v, high=v, low=v, close=v,
                           volume=b.volume))
        else:
            out.append(b)
    return out


def detect_leakage(feature_id: str, compute: ComputeFn, bars: Sequence[Bar],
                   target_event_time: int) -> LeakageCheckResult:
    def value_at(rows):
        for fv in compute(rows):
            if fv.event_time == target_event_time:
                return fv.value
        return None

    base = value_at(bars)
    perturbed = value_at(_perturb_future(bars, target_event_time))
    leaked = base != perturbed
    return LeakageCheckResult(
        feature_id=feature_id,
        passed=not leaked,
        detail=("future perturbation changed the value (LEAKAGE)" if leaked
                else "invariant to future data"),
    )


def static_lookback_ok(lookback_days: int) -> bool:
    """Static guard: a feature must not declare a negative (future) lookback."""
    return lookback_days >= 0
