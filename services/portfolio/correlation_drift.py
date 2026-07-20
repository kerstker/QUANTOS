"""Point-in-time correlation-regime drift for research strategy books.

The kernel is deterministic and pure.  It compares disjoint trailing windows
ending at an exclusive caller-supplied index and returns a gross-risk scaling
recommendation.  It has no execution authority and performs no IO.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
import math
from typing import Mapping, Sequence


PASS = "PASS"
WARN = "WARN"
BLOCK = "BLOCK"
PROVENANCE = "PIT_TRAILING_CORRELATION_DRIFT_V1"
CALIBRATION = "UNCALIBRATED_PRINCIPLED"
_EPS = 1e-18


@dataclass(frozen=True)
class CorrelationDriftPolicy:
    baseline_window: int = 52
    recent_window: int = 8
    warn_correlation: float = 0.50
    block_correlation: float = 0.75
    warn_increase: float = 0.25
    block_increase: float = 0.40
    warn_gross_multiplier: float = 0.75
    block_gross_multiplier: float = 0.50


@dataclass(frozen=True)
class PairCorrelationDrift:
    book_a: str
    book_b: str
    baseline_correlation: float
    recent_correlation: float
    correlation_increase: float
    status: str


@dataclass(frozen=True)
class CorrelationDriftAssessment:
    status: str
    recommended_gross_multiplier: float
    pairs: tuple[PairCorrelationDrift, ...]
    reasons: tuple[str, ...]
    end_index: int | None
    baseline_start: int | None
    baseline_end: int | None
    recent_start: int | None
    recent_end: int | None
    provenance: str = PROVENANCE
    calibration: str = CALIBRATION


def _finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def validate_correlation_drift_policy(policy: CorrelationDriftPolicy) -> str | None:
    if not isinstance(policy, CorrelationDriftPolicy):
        return "invalid_policy_type"
    if not isinstance(policy.baseline_window, int) or isinstance(policy.baseline_window, bool):
        return "invalid_baseline_window"
    if policy.baseline_window < 4:
        return "invalid_baseline_window"
    if not isinstance(policy.recent_window, int) or isinstance(policy.recent_window, bool):
        return "invalid_recent_window"
    if policy.recent_window < 4:
        return "invalid_recent_window"

    for name in ("warn_correlation", "block_correlation"):
        value = getattr(policy, name)
        if not _finite_number(value) or not (0.0 <= float(value) <= 1.0):
            return f"invalid_{name}"
    if float(policy.warn_correlation) > float(policy.block_correlation):
        return "invalid_correlation_threshold_order"

    for name in ("warn_increase", "block_increase"):
        value = getattr(policy, name)
        if not _finite_number(value) or not (0.0 <= float(value) <= 2.0):
            return f"invalid_{name}"
    if float(policy.warn_increase) > float(policy.block_increase):
        return "invalid_increase_threshold_order"

    for name in ("warn_gross_multiplier", "block_gross_multiplier"):
        value = getattr(policy, name)
        if not _finite_number(value) or not (0.0 < float(value) <= 1.0):
            return f"invalid_{name}"
    if float(policy.block_gross_multiplier) > float(policy.warn_gross_multiplier):
        return "invalid_gross_multiplier_order"
    return None


def _failure_multiplier(policy: CorrelationDriftPolicy) -> float:
    value = getattr(policy, "block_gross_multiplier", 0.0)
    return float(value) if _finite_number(value) and 0.0 < float(value) <= 1.0 else 0.0


def _blocked(
    policy: CorrelationDriftPolicy,
    reason: str,
    *,
    end_index: int | None = None,
    baseline_start: int | None = None,
    baseline_end: int | None = None,
    recent_start: int | None = None,
    recent_end: int | None = None,
) -> CorrelationDriftAssessment:
    return CorrelationDriftAssessment(
        status=BLOCK,
        recommended_gross_multiplier=_failure_multiplier(policy),
        pairs=(),
        reasons=(reason,),
        end_index=end_index,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        recent_start=recent_start,
        recent_end=recent_end,
    )


def _centered_energy(values: Sequence[float]) -> float:
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values)


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    left_mean = sum(left) / len(left)
    right_mean = sum(right) / len(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    numerator = sum(a * b for a, b in zip(left_centered, right_centered))
    denominator = math.sqrt(
        sum(value * value for value in left_centered)
        * sum(value * value for value in right_centered)
    )
    correlation = numerator / denominator
    return min(max(correlation, -1.0), 1.0)


def _pair_status(
    baseline: float,
    recent: float,
    policy: CorrelationDriftPolicy,
) -> str:
    increase = recent - baseline
    if (
        recent >= float(policy.block_correlation)
        and increase >= float(policy.block_increase)
    ):
        return BLOCK
    if (
        recent >= float(policy.warn_correlation)
        or increase >= float(policy.warn_increase)
    ):
        return WARN
    return PASS


def evaluate_correlation_drift(
    returns_by_book: Mapping[str, Sequence[float]],
    *,
    end_index: int | None = None,
    policy: CorrelationDriftPolicy | None = None,
) -> CorrelationDriftAssessment:
    policy = policy or CorrelationDriftPolicy()
    policy_reason = validate_correlation_drift_policy(policy)
    if policy_reason is not None:
        return _blocked(policy, policy_reason, end_index=end_index)
    if not isinstance(returns_by_book, Mapping) or len(returns_by_book) < 2:
        return _blocked(policy, "insufficient_books", end_index=end_index)

    raw_book_ids = list(returns_by_book)
    if any(not isinstance(book_id, str) or not book_id.strip() for book_id in raw_book_ids):
        return _blocked(policy, "invalid_book_id", end_index=end_index)
    book_ids = sorted(raw_book_ids)
    lengths = []
    for book_id in book_ids:
        values = returns_by_book[book_id]
        try:
            lengths.append(len(values))
            values[0:0]
        except (AttributeError, TypeError):
            return _blocked(
                policy,
                f"invalid_return_series:{book_id}",
                end_index=end_index,
            )
    if len(set(lengths)) != 1:
        return _blocked(policy, "misaligned_returns", end_index=end_index)
    available = lengths[0]
    if end_index is None:
        end = available
    elif isinstance(end_index, bool) or not isinstance(end_index, int):
        return _blocked(policy, "invalid_end_index", end_index=end_index)
    else:
        end = end_index
    if end < 0 or end > available:
        return _blocked(policy, "invalid_end_index", end_index=end)

    recent_start = end - policy.recent_window
    baseline_end = recent_start
    baseline_start = baseline_end - policy.baseline_window
    positions = {
        "end_index": end,
        "baseline_start": baseline_start,
        "baseline_end": baseline_end,
        "recent_start": recent_start,
        "recent_end": end,
    }
    if baseline_start < 0:
        return _blocked(policy, "insufficient_history", **positions)

    baseline: dict[str, tuple[float, ...]] = {}
    recent: dict[str, tuple[float, ...]] = {}
    for book_id in book_ids:
        baseline_values = returns_by_book[book_id][baseline_start:baseline_end]
        recent_values = returns_by_book[book_id][recent_start:end]
        for value in tuple(baseline_values) + tuple(recent_values):
            if not _finite_number(value):
                return _blocked(policy, f"invalid_return:{book_id}", **positions)
        baseline[book_id] = tuple(float(value) for value in baseline_values)
        recent[book_id] = tuple(float(value) for value in recent_values)
        if _centered_energy(baseline[book_id]) <= _EPS:
            return _blocked(policy, f"zero_variance:{book_id}:baseline", **positions)
        if _centered_energy(recent[book_id]) <= _EPS:
            return _blocked(policy, f"zero_variance:{book_id}:recent", **positions)

    pairs: list[PairCorrelationDrift] = []
    for book_a, book_b in combinations(book_ids, 2):
        baseline_correlation = _pearson(baseline[book_a], baseline[book_b])
        recent_correlation = _pearson(recent[book_a], recent[book_b])
        status = _pair_status(baseline_correlation, recent_correlation, policy)
        pairs.append(
            PairCorrelationDrift(
                book_a=book_a,
                book_b=book_b,
                baseline_correlation=baseline_correlation,
                recent_correlation=recent_correlation,
                correlation_increase=recent_correlation - baseline_correlation,
                status=status,
            )
        )

    if any(pair.status == BLOCK for pair in pairs):
        status = BLOCK
        recommended_gross_multiplier = float(policy.block_gross_multiplier)
        reasons = tuple(
            f"correlation_block:{pair.book_a}:{pair.book_b}"
            for pair in pairs
            if pair.status == BLOCK
        )
    elif any(pair.status == WARN for pair in pairs):
        status = WARN
        recommended_gross_multiplier = float(policy.warn_gross_multiplier)
        reasons = tuple(
            f"correlation_warn:{pair.book_a}:{pair.book_b}"
            for pair in pairs
            if pair.status == WARN
        )
    else:
        status = PASS
        recommended_gross_multiplier = 1.0
        reasons = ("correlation_stable",)

    return CorrelationDriftAssessment(
        status=status,
        recommended_gross_multiplier=recommended_gross_multiplier,
        pairs=tuple(pairs),
        reasons=reasons,
        **positions,
    )


def correlation_drift_diagnostics(
    assessment: CorrelationDriftAssessment,
) -> dict:
    return {
        "status": assessment.status,
        "recommended_gross_multiplier": assessment.recommended_gross_multiplier,
        "reasons": list(assessment.reasons),
        "end_index": assessment.end_index,
        "baseline_start": assessment.baseline_start,
        "baseline_end": assessment.baseline_end,
        "recent_start": assessment.recent_start,
        "recent_end": assessment.recent_end,
        "pairs": [asdict(pair) for pair in assessment.pairs],
        "provenance": assessment.provenance,
        "calibration": assessment.calibration,
        "load_bearing": False,
    }


__all__ = [
    "BLOCK",
    "CALIBRATION",
    "PASS",
    "PROVENANCE",
    "WARN",
    "CorrelationDriftAssessment",
    "CorrelationDriftPolicy",
    "PairCorrelationDrift",
    "correlation_drift_diagnostics",
    "evaluate_correlation_drift",
    "validate_correlation_drift_policy",
]
