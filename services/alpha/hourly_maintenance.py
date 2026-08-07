"""Pure hourly-alpha health evaluation and one-way automated de-risking.

The automated application surface contains only HOLD, PAUSE, and RETIRE.  It
cannot approve, recover a paused deployment, reset a kill-switch, or touch live
credentials and order submission.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum

from quantos.contracts.alpha import Actor
from quantos.contracts.hashing import stable_hash
from quantos.contracts.research import AlphaState
from services.lifecycle import DeploymentState, HealthReason, HealthState, HealthVerdict


class MaintenanceAction(str, Enum):
    HOLD = "HOLD"
    PAUSE = "PAUSE"
    RETIRE = "RETIRE"


@dataclass(frozen=True, slots=True)
class HourlyMaintenancePolicy:
    version: str
    min_forward_realizations: int
    max_feature_drift_score: float
    max_abs_return_drift_z: float
    max_cost_ratio: float
    max_reality_gap: float
    max_consecutive_coverage_failures: int
    retire_consecutive_failed_periods: int

    def __post_init__(self) -> None:
        if type(self.version) is not str or not self.version:
            raise ValueError("maintenance policy version is required")
        for name in (
            "min_forward_realizations",
            "max_consecutive_coverage_failures",
            "retire_consecutive_failed_periods",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive int")
        for name in (
            "max_feature_drift_score",
            "max_abs_return_drift_z",
            "max_cost_ratio",
            "max_reality_gap",
        ):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
        if self.max_cost_ratio < 1:
            raise ValueError("max_cost_ratio must be at least one")

    @property
    def digest(self) -> str:
        return stable_hash({"schema": "quantos.hourly-maintenance-policy.v1", **asdict(self)})


DEFAULT_HOURLY_MAINTENANCE_POLICY = HourlyMaintenancePolicy(
    version="hourly-maintenance.v1",
    min_forward_realizations=24,
    max_feature_drift_score=0.20,
    max_abs_return_drift_z=3.0,
    max_cost_ratio=1.50,
    max_reality_gap=0.25,
    max_consecutive_coverage_failures=3,
    retire_consecutive_failed_periods=24,
)


@dataclass(frozen=True, slots=True)
class HourlyMaintenanceEvidence:
    alpha_id: str
    observed_at: int
    forward_chain_valid: bool | None
    forward_realizations: int | None
    feature_drift_score: float | None
    return_drift_z: float | None
    expected_cost_bps: float | None
    realized_cost_bps: float | None
    reality_gap: float | None
    consecutive_coverage_failures: int | None
    consecutive_failed_periods: int | None

    def __post_init__(self) -> None:
        if type(self.alpha_id) is not str or not self.alpha_id:
            raise ValueError("alpha_id is required")
        if type(self.observed_at) is not int or self.observed_at < 0:
            raise ValueError("observed_at must be a non-negative int")
        if self.forward_chain_valid is not None and type(self.forward_chain_valid) is not bool:
            raise ValueError("forward_chain_valid must be bool or None")
        for name in (
            "forward_realizations",
            "consecutive_coverage_failures",
            "consecutive_failed_periods",
        ):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative int or None")
        for name in (
            "feature_drift_score",
            "return_drift_z",
            "expected_cost_bps",
            "realized_cost_bps",
            "reality_gap",
        ):
            value = getattr(self, name)
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value)
            ):
                raise ValueError(f"{name} must be finite or None")
        if self.feature_drift_score is not None and self.feature_drift_score < 0:
            raise ValueError("feature_drift_score must be non-negative")
        if self.expected_cost_bps is not None and self.expected_cost_bps < 0:
            raise ValueError("expected_cost_bps must be non-negative")
        if self.realized_cost_bps is not None and self.realized_cost_bps < 0:
            raise ValueError("realized_cost_bps must be non-negative")
        if self.reality_gap is not None and self.reality_gap < 0:
            raise ValueError("reality_gap must be non-negative")

    @property
    def digest(self) -> str:
        return stable_hash({"schema": "quantos.hourly-maintenance-evidence.v1", **asdict(self)})


@dataclass(frozen=True, slots=True)
class MaintenanceRecommendation:
    alpha_id: str
    observed_at: int
    action: MaintenanceAction
    reason_codes: tuple[str, ...]
    evidence_digest: str
    policy_digest: str

    def __post_init__(self) -> None:
        if type(self.alpha_id) is not str or not self.alpha_id:
            raise ValueError("alpha_id is required")
        if type(self.observed_at) is not int or self.observed_at < 0:
            raise ValueError("observed_at must be a non-negative int")
        if not isinstance(self.action, MaintenanceAction):
            raise ValueError("action must be MaintenanceAction")
        reasons = tuple(self.reason_codes)
        if any(type(reason) is not str or not reason for reason in reasons):
            raise ValueError("reason codes must be non-empty strings")
        if self.action is MaintenanceAction.HOLD and reasons:
            raise ValueError("HOLD cannot carry failure reasons")
        if self.action is not MaintenanceAction.HOLD and not reasons:
            raise ValueError("de-risking requires failure reasons")
        for digest in (self.evidence_digest, self.policy_digest):
            if type(digest) is not str or len(digest) != 64:
                raise ValueError("recommendation digests must be sha256")
        object.__setattr__(self, "reason_codes", reasons)


_EVIDENCE_FIELDS = (
    "forward_chain_valid",
    "forward_realizations",
    "feature_drift_score",
    "return_drift_z",
    "expected_cost_bps",
    "realized_cost_bps",
    "reality_gap",
    "consecutive_coverage_failures",
    "consecutive_failed_periods",
)


def _recommendation(
    evidence: HourlyMaintenanceEvidence,
    policy: HourlyMaintenancePolicy,
    action: MaintenanceAction,
    reasons: tuple[str, ...],
) -> MaintenanceRecommendation:
    return MaintenanceRecommendation(
        alpha_id=evidence.alpha_id,
        observed_at=evidence.observed_at,
        action=action,
        reason_codes=reasons,
        evidence_digest=evidence.digest,
        policy_digest=policy.digest,
    )


def evaluate_hourly_maintenance(
    evidence: HourlyMaintenanceEvidence,
    policy: HourlyMaintenancePolicy = DEFAULT_HOURLY_MAINTENANCE_POLICY,
) -> MaintenanceRecommendation:
    """Evaluate one immutable evidence snapshot without I/O, clocks, or mutation."""
    if not isinstance(evidence, HourlyMaintenanceEvidence):
        raise TypeError("evidence must be HourlyMaintenanceEvidence")
    if not isinstance(policy, HourlyMaintenancePolicy):
        raise TypeError("policy must be HourlyMaintenancePolicy")
    if (
        evidence.consecutive_failed_periods is not None
        and evidence.consecutive_failed_periods >= policy.retire_consecutive_failed_periods
    ):
        return _recommendation(
            evidence, policy, MaintenanceAction.RETIRE, ("PERSISTENT_FAILURE",)
        )
    if any(getattr(evidence, field) is None for field in _EVIDENCE_FIELDS):
        return _recommendation(
            evidence, policy, MaintenanceAction.PAUSE, ("MISSING_EVIDENCE",)
        )
    reasons: list[str] = []
    if not evidence.forward_chain_valid:
        reasons.append("FORWARD_CHAIN_INVALID")
    if evidence.forward_realizations < policy.min_forward_realizations:
        reasons.append("INSUFFICIENT_FORWARD_EVIDENCE")
    if evidence.feature_drift_score > policy.max_feature_drift_score:
        reasons.append("FEATURE_DRIFT_BLOCK")
    if abs(evidence.return_drift_z) > policy.max_abs_return_drift_z:
        reasons.append("RETURN_DRIFT_BLOCK")
    cost_ratio = (
        math.inf
        if evidence.expected_cost_bps == 0 and evidence.realized_cost_bps > 0
        else (
            1.0
            if evidence.expected_cost_bps == 0
            else evidence.realized_cost_bps / evidence.expected_cost_bps
        )
    )
    if cost_ratio > policy.max_cost_ratio:
        reasons.append("COST_INFLATION")
    if evidence.reality_gap > policy.max_reality_gap:
        reasons.append("REALITY_GAP")
    if (
        evidence.consecutive_coverage_failures
        >= policy.max_consecutive_coverage_failures
    ):
        reasons.append("COVERAGE_FAILURES")
    if reasons:
        return _recommendation(
            evidence, policy, MaintenanceAction.PAUSE, tuple(reasons)
        )
    return _recommendation(evidence, policy, MaintenanceAction.HOLD, ())


_HEALTH_REASON = {
    "MISSING_EVIDENCE": HealthReason.DATA_STALE,
    "FORWARD_CHAIN_INVALID": HealthReason.DATA_LINEAGE_BROKEN,
    "INSUFFICIENT_FORWARD_EVIDENCE": HealthReason.DATA_STALE,
    "FEATURE_DRIFT_BLOCK": HealthReason.SIGNAL_DECAY,
    "RETURN_DRIFT_BLOCK": HealthReason.SIGNAL_DECAY,
    "COST_INFLATION": HealthReason.COST_INFLATION,
    "REALITY_GAP": HealthReason.COST_INFLATION,
    "COVERAGE_FAILURES": HealthReason.DATA_STALE,
    "EVALUATION_ERROR": HealthReason.MODEL_INVALIDATED,
    "PERSISTENT_FAILURE": HealthReason.SIGNAL_DECAY,
}


def _evaluation_error(alpha_id: str, timestamp: int, policy: object) -> MaintenanceRecommendation:
    safe_policy = (
        policy
        if isinstance(policy, HourlyMaintenancePolicy)
        else DEFAULT_HOURLY_MAINTENANCE_POLICY
    )
    return MaintenanceRecommendation(
        alpha_id=alpha_id,
        observed_at=timestamp,
        action=MaintenanceAction.PAUSE,
        reason_codes=("EVALUATION_ERROR",),
        evidence_digest=stable_hash({"schema": "quantos.hourly-maintenance-error.v1"}),
        policy_digest=safe_policy.digest,
    )


def apply_hourly_maintenance(
    manager,
    alpha_id: str,
    actor: Actor,
    *,
    evidence: HourlyMaintenanceEvidence,
    policy: HourlyMaintenancePolicy = DEFAULT_HOURLY_MAINTENANCE_POLICY,
    timestamp: int,
) -> MaintenanceRecommendation:
    """Apply only audited PAUSE/RETIRE demotions; HOLD never performs recovery."""
    if not isinstance(actor, Actor) or actor.is_human:
        raise ValueError("hourly maintenance requires a non-human actor")
    if type(alpha_id) is not str or not alpha_id or type(timestamp) is not int or timestamp < 0:
        raise ValueError("alpha_id and deterministic timestamp are required")
    try:
        recommendation = evaluate_hourly_maintenance(evidence, policy)
        if recommendation.alpha_id != alpha_id or recommendation.observed_at > timestamp:
            raise ValueError("maintenance evidence identity or time mismatch")
    except Exception:
        recommendation = _evaluation_error(alpha_id, timestamp, policy)
    if recommendation.action is MaintenanceAction.HOLD:
        return recommendation
    if recommendation.action is MaintenanceAction.RETIRE:
        if manager.catalog.state(alpha_id) is AlphaState.RETIRED:
            return recommendation
        if manager.catalog.deployment_state(alpha_id) is DeploymentState.PAUSED:
            manager.advance(alpha_id, AlphaState.RETIRED, actor, timestamp=timestamp)
            return recommendation
        recommendation = MaintenanceRecommendation(
            alpha_id=recommendation.alpha_id,
            observed_at=recommendation.observed_at,
            action=MaintenanceAction.PAUSE,
            reason_codes=("PERSISTENT_FAILURE", "RETIRE_REQUIRES_PAUSED"),
            evidence_digest=recommendation.evidence_digest,
            policy_digest=recommendation.policy_digest,
        )
    reason = _HEALTH_REASON[recommendation.reason_codes[0]]
    manager.advance_health(
        alpha_id,
        HealthVerdict(HealthState.FAILED, reason),
        actor,
        timestamp=timestamp,
    )
    return recommendation


__all__ = [
    "MaintenanceAction",
    "HourlyMaintenancePolicy",
    "DEFAULT_HOURLY_MAINTENANCE_POLICY",
    "HourlyMaintenanceEvidence",
    "MaintenanceRecommendation",
    "evaluate_hourly_maintenance",
    "apply_hourly_maintenance",
]
