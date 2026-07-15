"""Pure model-risk decisions for red-team and challenger evidence."""
from __future__ import annotations

from collections.abc import Sequence

from quantos.contracts.model_risk import (
    ChallengerReview,
    ChallengerVerdict,
    RedTeamCaseResult,
    RedTeamReport,
    RedTeamVerdict,
    ReviewIdentityAssurance,
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


__all__ = ["assess_red_team", "compare_challenger"]
