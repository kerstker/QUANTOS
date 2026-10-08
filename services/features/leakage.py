"""Leakage detection (Phase 3) — static and dynamic.

Dynamic detection perturbs EVERY field of every strictly-future bar
(event_time > target) — price, volume, AND knowledge_time — across several
schemes, then recomputes. If the feature value(s) at the target change, the
feature read future data → it leaks. A correct (past-only) feature is invariant.

Restatement leakage (a not-yet-knowable correction of a past/current event) is
closed by requiring PIT-deduplicated input: exactly one knowable bar per
(instrument_id, event_time). Callers obtain this by querying the PITStore
`as_of(...)` view before computing features, so an un-knowable restatement can
never be smuggled in.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

from quantos.contracts.data import Bar
from quantos.contracts.feature import FeatureValue, LeakageCheckResult

ComputeFn = Callable[[Sequence[Bar]], list[FeatureValue]]

# Bar prices must be > 0, so all schemes map to positive values. Using several
# reduces the chance a non-linear feature (sign/rank/bucket) is invariant to one.
_SCHEMES = (
    lambda c: c * 10.0 + 1.0,
    lambda c: c * 0.5 + 0.01,
    lambda c: c + 1000.0,
)
_FAR_FUTURE = 10 ** 15


def _assert_pit_deduplicated(bars: Sequence[Bar]) -> None:
    seen = set()
    for b in bars:
        key = (b.instrument_id, b.event_time)
        if key in seen:
            raise ValueError(
                "detect_leakage requires PIT-deduplicated bars (one knowable bar "
                "per (instrument_id, event_time)); query PITStore.as_of(...) to "
                "obtain a deduplicated view before computing features"
            )
        seen.add(key)


def _perturb_future(bars: Sequence[Bar], target_event_time: int, scheme) -> list[Bar]:
    out: list[Bar] = []
    for b in bars:
        if b.event_time > target_event_time:
            v = scheme(b.close)
            out.append(Bar(
                instrument_id=b.instrument_id, event_time=b.event_time,
                knowledge_time=b.knowledge_time + _FAR_FUTURE,   # perturb knowledge_time too
                open=v, high=v, low=v, close=v,
                volume=b.volume * 10.0 + 1.0,                    # perturb volume too
            ))
        else:
            out.append(b)
    return out


def detect_leakage(feature_id: str, compute: ComputeFn, bars: Sequence[Bar],
                   target_event_time: int, instrument_id: Optional[str] = None) -> LeakageCheckResult:
    _assert_pit_deduplicated(bars)

    def values_at(rows: Sequence[Bar]):
        vals = [
            (fv.instrument_id, fv.value)
            for fv in compute(rows)
            if fv.event_time == target_event_time and (instrument_id is None or fv.instrument_id == instrument_id)
        ]
        return tuple(sorted(vals, key=lambda x: x[0]))

    base = values_at(bars)
    for scheme in _SCHEMES:
        if values_at(_perturb_future(bars, target_event_time, scheme)) != base:
            return LeakageCheckResult(
                feature_id=feature_id, passed=False,
                detail="future perturbation (price/volume/knowledge_time) changed the value (LEAKAGE)",
            )
    return LeakageCheckResult(
        feature_id=feature_id, passed=True,
        detail="invariant to future-data perturbation across all fields",
    )


def static_lookback_ok(lookback_days: int) -> bool:
    """Static guard: a feature must not declare a negative (future) lookback."""
    return lookback_days >= 0
