"""Pure model-risk decisions for red-team and challenger evidence."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
import math

from quantos.contracts.model_risk import (
    ChallengerReview,
    ChallengerVerdict,
    RedTeamCaseResult,
    RedTeamReport,
    RedTeamVerdict,
    ReviewIdentityAssurance,
)


class ModelRiskReadiness(str, Enum):
    """Honest readiness states for the model-risk boundary.

    ``RESEARCH_ONLY`` is the strongest state this helper can return.  It does
    not grant promotion, execution, credential, or live-trading authority.
    ``EXTERNAL_BLOCKED`` and ``NO_EDGE`` are intentionally first-class states
    so missing provider/reviewer evidence and a failed OOS challenger cannot be
    represented as a positive model result.
    """

    EXTERNAL_BLOCKED = "EXTERNAL_BLOCKED"
    NO_EDGE = "NO_EDGE"
    RESEARCH_ONLY = "RESEARCH_ONLY"


@dataclass(frozen=True)
class ModelRiskReadinessResult:
    """Deterministic model-risk classification with no authority side effect."""

    status: ModelRiskReadiness
    reason_codes: tuple[str, ...]
    external_provider_verified: bool
    reviewer_identity: ReviewIdentityAssurance
    challenger_verdict: ChallengerVerdict
    challenger_oos_metric: float
    forward_periods: int
    minimum_forward_periods: int
    execution_authority_granted: bool = False


def assess_model_risk_readiness(
    *,
    external_provider_verified: bool,
    reviewer_identity: ReviewIdentityAssurance,
    challenger_verdict: ChallengerVerdict,
    challenger_oos_metric: float,
    forward_periods: int,
    minimum_forward_periods: int = 8,
) -> ModelRiskReadinessResult:
    """Classify model-risk evidence without manufacturing external proof.

    The local repository cannot attest an external model provider or a strong
    reviewer identity.  Callers may pass ``True``/``EXTERNAL_STRONG_IDENTITY``
    only after independently verifying those facts; this function merely
    carries the asserted state and remains fail-closed for malformed input.
    A relative challenger lift is never sufficient: a positive OOS metric and
    the explicit absolute-floor verdict are both required.  The result never
    grants execution authority, even in the all-evidence research-only case.
    """
    if type(external_provider_verified) is not bool:
        raise TypeError("external_provider_verified must be a bool")
    if not isinstance(reviewer_identity, ReviewIdentityAssurance):
        raise TypeError("reviewer_identity must be ReviewIdentityAssurance")
    if not isinstance(challenger_verdict, ChallengerVerdict):
        raise TypeError("challenger_verdict must be ChallengerVerdict")
    if not isinstance(challenger_oos_metric, (int, float)) or isinstance(
        challenger_oos_metric, bool
    ) or not math.isfinite(float(challenger_oos_metric)):
        raise ValueError("challenger_oos_metric must be finite")
    if (
        isinstance(forward_periods, bool)
        or not isinstance(forward_periods, int)
        or forward_periods < 0
    ):
        raise ValueError("forward_periods must be a non-negative integer")
    if (
        isinstance(minimum_forward_periods, bool)
        or not isinstance(minimum_forward_periods, int)
        or minimum_forward_periods <= 0
    ):
        raise ValueError("minimum_forward_periods must be a positive integer")

    reasons: list[str] = []
    if not external_provider_verified:
        reasons.append("EXTERNAL_PROVIDER_NOT_VERIFIED")
    if reviewer_identity != ReviewIdentityAssurance.EXTERNAL_STRONG_IDENTITY:
        reasons.append("REVIEWER_IDENTITY_NOT_EXTERNALLY_AUTHENTICATED")
    if challenger_verdict != ChallengerVerdict.ACCEPT_RESEARCH_ONLY or float(
        challenger_oos_metric
    ) <= 0:
        reasons.append("NO_POSITIVE_OOS_CHALLENGER")
    if forward_periods < minimum_forward_periods:
        reasons.append("FORWARD_EVIDENCE_INSUFFICIENT")

    external_blocked = any(
        reason
        in {
            "EXTERNAL_PROVIDER_NOT_VERIFIED",
            "REVIEWER_IDENTITY_NOT_EXTERNALLY_AUTHENTICATED",
        }
        for reason in reasons
    )
    no_edge = any(
        reason in {"NO_POSITIVE_OOS_CHALLENGER", "FORWARD_EVIDENCE_INSUFFICIENT"}
        for reason in reasons
    )
    if external_blocked:
        status = ModelRiskReadiness.EXTERNAL_BLOCKED
    elif no_edge:
        status = ModelRiskReadiness.NO_EDGE
    else:
        status = ModelRiskReadiness.RESEARCH_ONLY
    return ModelRiskReadinessResult(
        status=status,
        reason_codes=tuple(reasons),
        external_provider_verified=external_provider_verified,
        reviewer_identity=reviewer_identity,
        challenger_verdict=challenger_verdict,
        challenger_oos_metric=float(challenger_oos_metric),
        forward_periods=forward_periods,
        minimum_forward_periods=minimum_forward_periods,
    )


def assess_red_team(
    *,
    report_id: str,
    model_ref: str,
    tool_manifest_ref: str,
    cases: Sequence[RedTeamCaseResult],
    required_case_ids: Sequence[str],
    recorded_at: int,
) -> RedTeamReport:
    case_tuple = tuple(cases)
    required = tuple(required_case_ids)
    case_ids = [case.case_id for case in case_tuple]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("red-team case IDs must be unique")
    if len(set(required)) != len(required):
        raise ValueError("required red-team case IDs must be unique")
    missing = sorted(set(required) - set(case_ids))
    if missing:
        raise ValueError("missing required red-team cases: " + ", ".join(missing))
    failed = any(
        case.boundary_breached or case.actual_outcome != case.expected_outcome
        for case in case_tuple
        if case.case_id in required
    )
    verdict = RedTeamVerdict.FAIL if failed else RedTeamVerdict.PASS
    return RedTeamReport(
        report_id=report_id,
        model_ref=model_ref,
        tool_manifest_ref=tool_manifest_ref,
        cases=case_tuple,
        required_case_ids=required,
        verdict=verdict,
        recorded_at=recorded_at,
    )


def compare_challenger(
    *,
    review_id: str,
    incumbent_model_ref: str,
    challenger_model_ref: str,
    incumbent_evaluation_ref: str,
    challenger_evaluation_ref: str,
    model_owner_group: str,
    reviewer_group: str,
    reviewer_id: str,
    primary_metric: str,
    incumbent_value: float,
    challenger_value: float,
    minimum_acceptable: float,
    recorded_at: int,
    identity_assurance: ReviewIdentityAssurance = ReviewIdentityAssurance.PROCESS_ROLE_ONLY,
    limitations: Sequence[str] = (),
) -> ChallengerReview:
    if challenger_value < minimum_acceptable:
        verdict = ChallengerVerdict.REJECT_ABSOLUTE_FLOOR
    elif challenger_value <= incumbent_value:
        verdict = ChallengerVerdict.REJECT_NO_IMPROVEMENT
    else:
        verdict = ChallengerVerdict.ACCEPT_RESEARCH_ONLY
    return ChallengerReview(
        review_id=review_id,
        incumbent_model_ref=incumbent_model_ref,
        challenger_model_ref=challenger_model_ref,
        incumbent_evaluation_ref=incumbent_evaluation_ref,
        challenger_evaluation_ref=challenger_evaluation_ref,
        model_owner_group=model_owner_group,
        reviewer_group=reviewer_group,
        reviewer_id=reviewer_id,
        identity_assurance=identity_assurance,
        primary_metric=primary_metric,
        incumbent_value=incumbent_value,
        challenger_value=challenger_value,
        minimum_acceptable=minimum_acceptable,
        verdict=verdict,
        limitations=tuple(limitations),
        recorded_at=recorded_at,
    )


__all__ = [
    "ModelRiskReadiness",
    "ModelRiskReadinessResult",
    "assess_model_risk_readiness",
    "assess_red_team",
    "compare_challenger",
]
