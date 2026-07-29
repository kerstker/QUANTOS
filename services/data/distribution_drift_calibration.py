"""Deterministic PIT calibration for trailing two-sample KS drift.

The current 60+20 observation assessment window is held out completely.
Earlier adjacent PIT blocks are pooled and deterministically permuted into
60+20 samples at a fixed stride, producing an empirical same-distribution
null.  This calibrates alert thresholds without giving the current observation
any influence over its own classification or treating historical trends as a
stable null.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import hashlib
import json
import math

from services.data.distribution_drift import (
    DistributionDriftPolicy,
    two_sample_ks_distance,
)


READY = "READY"
INSUFFICIENT = "INSUFFICIENT"
CALIBRATION = "EMPIRICALLY_CALIBRATED_PIT_V1"
PROVENANCE = "PIT_PRECURRENT_WINDOW_EMPIRICAL_KS_V1"


@dataclass(frozen=True)
class DriftCalibrationPolicy:
    baseline_window: int = 60
    recent_window: int = 20
    stride: int = 20
    permutations_per_window: int = 4
    min_calibration_windows: int = 100
    warn_quantile: float = 0.95
    block_quantile: float = 0.99
    minimum_threshold_separation: float = 0.01


@dataclass(frozen=True)
class DriftCalibrationResult:
    status: str
    calibration_id: str | None
    warn_ks: float | None
    block_ks: float | None
    window_count: int
    series_count: int
    held_out_observations_per_series: int
    source_digest: str | None
    reasons: tuple[str, ...]
    policy: DriftCalibrationPolicy
    calibration: str = CALIBRATION
    provenance: str = PROVENANCE
    load_bearing: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "reasons": list(self.reasons),
            "policy": asdict(self.policy),
        }

    def drift_policy(self) -> DistributionDriftPolicy:
        if (
            self.status != READY
            or self.warn_ks is None
            or self.block_ks is None
        ):
            raise ValueError("drift calibration is not ready")
        return DistributionDriftPolicy(
            baseline_window=self.policy.baseline_window,
            recent_window=self.policy.recent_window,
            min_observations=self.policy.recent_window,
            warn_ks=self.warn_ks,
            block_ks=self.block_ks,
        )


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _valid_policy(policy: DriftCalibrationPolicy) -> str | None:
    if not isinstance(policy, DriftCalibrationPolicy):
        return "invalid_policy_type"
    for name in (
        "baseline_window",
        "recent_window",
        "stride",
        "min_calibration_windows",
        "permutations_per_window",
    ):
        value = getattr(policy, name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 2:
            return f"invalid_{name}"
    for name in (
        "warn_quantile",
        "block_quantile",
        "minimum_threshold_separation",
    ):
        value = getattr(policy, name)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            return f"invalid_{name}"
    if not 0.0 < policy.warn_quantile < policy.block_quantile < 1.0:
        return "invalid_quantile_order"
    if not 0.0 <= policy.minimum_threshold_separation < 1.0:
        return "invalid_threshold_separation"
    return None


def _nearest_rank(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _insufficient(
    reason: str,
    *,
    policy: DriftCalibrationPolicy,
    window_count: int = 0,
    series_count: int = 0,
) -> DriftCalibrationResult:
    return DriftCalibrationResult(
        status=INSUFFICIENT,
        calibration_id=None,
        warn_ks=None,
        block_ks=None,
        window_count=window_count,
        series_count=series_count,
        held_out_observations_per_series=(
            policy.baseline_window + policy.recent_window
        ),
        source_digest=None,
        reasons=(reason,),
        policy=policy,
    )


def calibrate_distribution_drift(
    values_by_series: Mapping[str, Sequence[float]] | None,
    *,
    policy: DriftCalibrationPolicy | None = None,
) -> DriftCalibrationResult:
    """Calibrate pooled empirical thresholds from pre-current PIT windows."""

    effective = policy or DriftCalibrationPolicy()
    policy_error = _valid_policy(effective)
    if policy_error is not None:
        return _insufficient(policy_error, policy=effective)
    if (
        values_by_series is None
        or isinstance(values_by_series, (str, bytes))
        or not isinstance(values_by_series, Mapping)
        or not values_by_series
    ):
        return _insufficient("invalid_series", policy=effective)

    try:
        series_ids = tuple(sorted(values_by_series))
    except TypeError:
        return _insufficient("invalid_series_ids", policy=effective)
    if any(
        not isinstance(series_id, str) or not series_id.strip()
        for series_id in series_ids
    ):
        return _insufficient("invalid_series_ids", policy=effective)

    assessment_width = effective.baseline_window + effective.recent_window
    distances: list[float] = []
    normalized_source: dict[str, list[float]] = {}
    for series_id in series_ids:
        raw = values_by_series[series_id]
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            return _insufficient(
                "invalid_series_values",
                policy=effective,
                window_count=len(distances),
                series_count=len(series_ids),
            )
        try:
            values = [float(value) for value in raw]
        except (TypeError, ValueError):
            return _insufficient(
                "invalid_series_values",
                policy=effective,
                window_count=len(distances),
                series_count=len(series_ids),
            )
        if any(not math.isfinite(value) for value in values):
            return _insufficient(
                "invalid_series_values",
                policy=effective,
                window_count=len(distances),
                series_count=len(series_ids),
            )
        calibration_end = len(values) - assessment_width
        normalized_source[series_id] = values[: max(0, calibration_end)]
        for end_index in range(
            assessment_width,
            calibration_end + 1,
            effective.stride,
        ):
            pooled = values[end_index - assessment_width : end_index]
            for permutation in range(effective.permutations_per_window):
                order = sorted(
                    range(assessment_width),
                    key=lambda index: hashlib.sha256(
                        (
                            f"{series_id}|{end_index}|{permutation}|{index}"
                        ).encode("utf-8")
                    ).digest(),
                )
                baseline = [
                    pooled[index]
                    for index in order[: effective.baseline_window]
                ]
                recent = [
                    pooled[index]
                    for index in order[effective.baseline_window :]
                ]
                distances.append(
                    two_sample_ks_distance(baseline, recent)
                )

    if len(distances) < effective.min_calibration_windows:
        return _insufficient(
            "insufficient_calibration_windows",
            policy=effective,
            window_count=len(distances),
            series_count=len(series_ids),
        )

    warn_ks = _nearest_rank(distances, effective.warn_quantile)
    block_ks = _nearest_rank(distances, effective.block_quantile)
    if block_ks < warn_ks + effective.minimum_threshold_separation:
        block_ks = min(1.0, warn_ks + effective.minimum_threshold_separation)
    if not 0.0 <= warn_ks < block_ks <= 1.0:
        return _insufficient(
            "degenerate_calibrated_thresholds",
            policy=effective,
            window_count=len(distances),
            series_count=len(series_ids),
        )

    source_digest = _sha256(normalized_source)
    identity = {
        "schema_version": 1,
        "calibration": CALIBRATION,
        "provenance": PROVENANCE,
        "policy": asdict(effective),
        "source_digest": source_digest,
        "window_count": len(distances),
        "series_count": len(series_ids),
        "warn_ks": warn_ks,
        "block_ks": block_ks,
    }
    return DriftCalibrationResult(
        status=READY,
        calibration_id=_sha256(identity),
        warn_ks=warn_ks,
        block_ks=block_ks,
        window_count=len(distances),
        series_count=len(series_ids),
        held_out_observations_per_series=assessment_width,
        source_digest=source_digest,
        reasons=("empirical_precurrent_windows_calibrated",),
        policy=effective,
    )


__all__ = [
    "CALIBRATION",
    "INSUFFICIENT",
    "PROVENANCE",
    "READY",
    "DriftCalibrationPolicy",
    "DriftCalibrationResult",
    "calibrate_distribution_drift",
]
