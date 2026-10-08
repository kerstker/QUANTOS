"""Feature validation (Phase 3): missing/infinite values, timestamp alignment,
and leakage -> FeatureValidationResult."""
from __future__ import annotations

import math
from typing import Optional, Sequence

from quantos.contracts.feature import FeatureValidationResult, FeatureValue, LeakageCheckResult


def count_missing(values: Sequence[FeatureValue], include_warmup: bool = False) -> int:
    """None-valued features. Warmup Nones (leading) are excluded by default."""
    vals = list(values)
    if not include_warmup:
        # drop the leading run of None (legitimate warmup)
        i = 0
        while i < len(vals) and vals[i].value is None:
            i += 1
        vals = vals[i:]
    return sum(1 for fv in vals if fv.value is None)


def count_non_finite(values: Sequence[FeatureValue]) -> int:
    return sum(1 for fv in values if fv.value is not None and not math.isfinite(fv.value))


def is_time_aligned(values: Sequence[FeatureValue]) -> bool:
    """event_time strictly increasing and knowledge_time >= event_time (the
    latter is already guaranteed by the FeatureValue contract)."""
    ets = [fv.event_time for fv in values]
    if any(b <= a for a, b in zip(ets, ets[1:])):
        return False
    return all(fv.knowledge_time >= fv.event_time for fv in values)


def validate_feature(
    feature_id: str,
    values: Sequence[FeatureValue],
    leakage: Optional[LeakageCheckResult] = None,
) -> FeatureValidationResult:
    non_finite = count_non_finite(values)
    missing = count_missing(values)
    aligned = is_time_aligned(values)
    passed = (
        non_finite == 0
        and missing == 0
        and aligned
        and (leakage is None or leakage.passed)
    )
    notes = f"non_finite={non_finite} missing={missing} aligned={aligned}"
    return FeatureValidationResult(feature_id=feature_id, passed=passed, leakage=leakage, notes=notes)
