"""Pure, point-in-time distribution drift diagnostics.

The detector deliberately answers one narrow question: did the recent window
come from a measurably different one-dimensional distribution than the earlier
window?  It has no IO or decision authority and is safe to use from data,
research, or read-only monitoring callers.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import math
import statistics
from typing import Sequence


PASS = "PASS"
WARN = "WARN"
BLOCK = "BLOCK"
PROVENANCE = "PIT_TRAILING_KS_DRIFT_V1"
FEATURE_PROVENANCE = "PIT_FEATURE_TRAILING_KS_DRIFT_V1"
CALIBRATION = "UNCALIBRATED_PRINCIPLED"


@dataclass(frozen=True)
class DistributionDriftPolicy:
    baseline_window: int = 60
    recent_window: int = 20
    min_observations: int = 20
    warn_ks: float = 0.20
    block_ks: float = 0.35


@dataclass(frozen=True)
class DistributionDriftAssessment:
    status: str
    ks_distance: float | None
    baseline_mean: float | None
    recent_mean: float | None
    baseline_std: float | None
    recent_std: float | None
    sample_counts: tuple[int, int]
    reasons: tuple[str, ...]
    end_index: int | None
    baseline_start: int | None
    baseline_end: int | None
    recent_start: int | None
    recent_end: int | None
    provenance: str = PROVENANCE
    calibration: str = CALIBRATION


@dataclass(frozen=True)
class FeatureDistributionDriftAssessment:
    """Aggregate PIT drift evidence for a declared feature universe.

    ``values_by_feature`` is a caller-supplied, PIT-trimmed snapshot.  The
    detector never reads a feature store implicitly, so a missing or
    unexpected feature is a hard failure instead of an incomplete green
    result.  Individual one-dimensional KS assessments remain available for
    operators and tests without turning this diagnostic into a trading gate.
    """

    status: str
    feature_ids: tuple[str, ...]
    assessments: tuple[tuple[str, DistributionDriftAssessment], ...]
    reasons: tuple[str, ...]
    missing_features: tuple[str, ...] = ()
    unexpected_features: tuple[str, ...] = ()
    end_index: int | None = None
    provenance: str = FEATURE_PROVENANCE
    calibration: str = CALIBRATION

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "feature_ids": list(self.feature_ids),
            "assessments": {
                feature_id: distribution_drift_diagnostics(assessment)
                for feature_id, assessment in self.assessments
            },
            "reasons": list(self.reasons),
            "missing_features": list(self.missing_features),
            "unexpected_features": list(self.unexpected_features),
            "end_index": self.end_index,
            "provenance": self.provenance,
            "calibration": self.calibration,
            "load_bearing": False,
        }


def _finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def validate_distribution_drift_policy(policy: DistributionDriftPolicy) -> str | None:
    if not isinstance(policy, DistributionDriftPolicy):
        return "invalid_policy_type"
    for name in ("baseline_window", "recent_window", "min_observations"):
        value = getattr(policy, name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 2:
            return f"invalid_{name}"
    if policy.baseline_window < policy.min_observations:
        return "invalid_baseline_window"
    if policy.recent_window < policy.min_observations:
        return "invalid_recent_window"
    for name in ("warn_ks", "block_ks"):
        value = getattr(policy, name)
        if not _finite_number(value) or not 0.0 <= float(value) <= 1.0:
            return f"invalid_{name}"
    if float(policy.warn_ks) > float(policy.block_ks):
        return "invalid_ks_threshold_order"
    return None


def _failed(
    reason: str,
    *,
    end_index: int | None = None,
    baseline_start: int | None = None,
    baseline_end: int | None = None,
    recent_start: int | None = None,
    recent_end: int | None = None,
    sample_counts: tuple[int, int] = (0, 0),
) -> DistributionDriftAssessment:
    return DistributionDriftAssessment(
        status=BLOCK,
        ks_distance=None,
        baseline_mean=None,
        recent_mean=None,
        baseline_std=None,
        recent_std=None,
        sample_counts=sample_counts,
        reasons=(reason,),
        end_index=end_index,
        baseline_start=baseline_start,
        baseline_end=baseline_end,
        recent_start=recent_start,
        recent_end=recent_end,
    )


def _ks_distance(left: Sequence[float], right: Sequence[float]) -> float:
    ordered_left = sorted(left)
    ordered_right = sorted(right)
    i = j = 0
    distance = 0.0
    while i < len(ordered_left) or j < len(ordered_right):
        if j >= len(ordered_right) or (
            i < len(ordered_left) and ordered_left[i] <= ordered_right[j]
        ):
            value = ordered_left[i]
        else:
            value = ordered_right[j]
        while i < len(ordered_left) and ordered_left[i] <= value:
            i += 1
        while j < len(ordered_right) and ordered_right[j] <= value:
            j += 1
        distance = max(
            distance,
            abs(i / len(ordered_left) - j / len(ordered_right)),
        )
    return min(1.0, max(0.0, distance))


def evaluate_distribution_drift(
    values: Sequence[float] | None,
    *,
    end_index: int | None = None,
    policy: DistributionDriftPolicy | None = None,
) -> DistributionDriftAssessment:
    policy = policy or DistributionDriftPolicy()
    policy_reason = validate_distribution_drift_policy(policy)
    if policy_reason is not None:
        return _failed(policy_reason, end_index=end_index)
    if values is None or isinstance(values, (str, bytes)):
        return _failed("invalid_return_series", end_index=end_index)
    try:
        available = len(values)
    except (TypeError, AttributeError):
        return _failed("invalid_return_series", end_index=end_index)

    if end_index is None:
        end = available
    elif isinstance(end_index, bool) or not isinstance(end_index, int):
        return _failed("invalid_end_index", end_index=end_index)
    else:
        end = end_index
    if end < 0 or end > available:
        return _failed("invalid_end_index", end_index=end)

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
        return _failed("insufficient_history", **positions)

    baseline = tuple(values[baseline_start:baseline_end])
    recent = tuple(values[recent_start:end])
    counts = (len(baseline), len(recent))
    if len(baseline) < policy.min_observations or len(recent) < policy.min_observations:
        return _failed("insufficient_observations", sample_counts=counts, **positions)
    if any(not _finite_number(value) for value in baseline + recent):
        return _failed("invalid_return", sample_counts=counts, **positions)

    baseline_float = tuple(float(value) for value in baseline)
    recent_float = tuple(float(value) for value in recent)
    distance = _ks_distance(baseline_float, recent_float)
    if distance >= float(policy.block_ks):
        status = BLOCK
        reasons = ("distribution_ks_block",)
    elif distance >= float(policy.warn_ks):
        status = WARN
        reasons = ("distribution_ks_warn",)
    else:
        status = PASS
        reasons = ("distribution_stable",)

    return DistributionDriftAssessment(
        status=status,
        ks_distance=distance,
        baseline_mean=statistics.fmean(baseline_float),
        recent_mean=statistics.fmean(recent_float),
        baseline_std=statistics.pstdev(baseline_float),
        recent_std=statistics.pstdev(recent_float),
        sample_counts=counts,
        reasons=reasons,
        **positions,
    )


def distribution_drift_diagnostics(assessment: DistributionDriftAssessment) -> dict:
    return {
        **asdict(assessment),
        "sample_counts": list(assessment.sample_counts),
        "reasons": list(assessment.reasons),
        "load_bearing": False,
    }


def evaluate_feature_distribution_drift(
    values_by_feature: Mapping[str, Sequence[float]] | None,
    *,
    expected_features: Sequence[str] | None = None,
    end_index: int | None = None,
    policy: DistributionDriftPolicy | None = None,
) -> FeatureDistributionDriftAssessment:
    """Evaluate KS drift for a declared PIT feature universe.

    The caller must provide values that were already materialized/read with a
    single point-in-time ``as_of`` boundary.  This function only composes the
    deterministic one-dimensional kernel; it does not infer feature values,
    fill missing series, or access a database.  Structural scope errors are
    ``BLOCK`` so a partial cache cannot masquerade as a clean feature panel.
    """

    def blocked(reason: str, *, feature_ids: tuple[str, ...] = ()) -> FeatureDistributionDriftAssessment:
        return FeatureDistributionDriftAssessment(
            status=BLOCK,
            feature_ids=feature_ids,
            assessments=(),
            reasons=(reason,),
            end_index=end_index,
        )

    if values_by_feature is None or isinstance(values_by_feature, (str, bytes)):
        return blocked("invalid_feature_series")
    if not isinstance(values_by_feature, Mapping):
        return blocked("invalid_feature_series")

    try:
        observed = tuple(sorted(values_by_feature))
    except TypeError:
        return blocked("invalid_feature_ids")
    if any(
        not isinstance(feature_id, str) or not feature_id.strip()
        for feature_id in observed
    ) or len(set(observed)) != len(observed):
        return blocked("invalid_feature_ids")

    if expected_features is None:
        declared = observed
    elif isinstance(expected_features, (str, bytes)) or not isinstance(expected_features, Sequence):
        return blocked("invalid_expected_features")
    else:
        try:
            declared = tuple(sorted(expected_features))
        except TypeError:
            return blocked("invalid_expected_features")
        if (
            any(not isinstance(feature_id, str) or not feature_id.strip() for feature_id in declared)
            or len(set(declared)) != len(declared)
            or not declared
        ):
            return blocked("invalid_expected_features")

    if not declared:
        return blocked("empty_feature_universe")

    missing = tuple(feature_id for feature_id in declared if feature_id not in values_by_feature)
    unexpected = tuple(feature_id for feature_id in observed if feature_id not in declared)
    assessments: list[tuple[str, DistributionDriftAssessment]] = []
    reasons: list[str] = []
    for feature_id in observed:
        assessment = evaluate_distribution_drift(
            values_by_feature[feature_id], end_index=end_index, policy=policy
        )
        assessments.append((feature_id, assessment))
        reasons.extend(f"{feature_id}:{reason}" for reason in assessment.reasons)
    reasons.extend(f"missing_feature:{feature_id}" for feature_id in missing)
    reasons.extend(f"unexpected_feature:{feature_id}" for feature_id in unexpected)

    status = PASS
    if missing or unexpected:
        status = BLOCK
    else:
        status = max(
            (assessment.status for _, assessment in assessments),
            key={PASS: 0, WARN: 1, BLOCK: 2}.get,
        )
    return FeatureDistributionDriftAssessment(
        status=status,
        feature_ids=declared,
        assessments=tuple(assessments),
        reasons=tuple(reasons),
        missing_features=missing,
        unexpected_features=unexpected,
        end_index=end_index,
    )


__all__ = [
    "BLOCK",
    "CALIBRATION",
    "PASS",
    "PROVENANCE",
    "WARN",
    "DistributionDriftAssessment",
    "DistributionDriftPolicy",
    "FEATURE_PROVENANCE",
    "FeatureDistributionDriftAssessment",
    "distribution_drift_diagnostics",
    "evaluate_feature_distribution_drift",
    "evaluate_distribution_drift",
    "validate_distribution_drift_policy",
]
