"""Read-only maintenance of the owning source's original net-account weeks."""

import copy
from quantos.contracts import gold_net_maintenance_policy as contract
from quantos.contracts import gold_net_forward_policy as forward
from quantos.streaming_identity import exact_stream_identity_hash as digest


def assess_maintenance(
    *,
    net_forward_review,
    net_forward_policy,
    policy,
    subject,
    candidate_id,
    native_result_hash,
    project_cut_sha256,
    snapshot_as_of_ms,
    declarations,
):
    contract.validate(policy, net_forward_policy=net_forward_policy)
    forward.validate_review(
        net_forward_review,
        policy=net_forward_policy,
        subject=subject,
        candidate_id=candidate_id,
        native_result_hash=native_result_hash,
        project_cut_sha256=project_cut_sha256,
        snapshot_as_of_ms=snapshot_as_of_ms,
    )
    rows = copy.deepcopy(declarations)
    ready = contract.validate_declarations(
        rows, policy=policy, net_forward_policy=net_forward_policy, net_forward_review=net_forward_review
    )
    result = dict(
        schema=contract.REVIEW_SCHEMA,
        scope=contract.SCOPE,
        **{k: subject[k] for k in ("invocation_id", "native_trial_id", "config_hash")},
        candidate_id=candidate_id,
        native_result_hash=native_result_hash,
        project_cut_sha256=project_cut_sha256,
        snapshot_as_of_ms=snapshot_as_of_ms,
        policy_sha256=contract.policy_sha256(policy, net_forward_policy=net_forward_policy),
        series_sha256=policy["series_sha256"],
        net_forward_review_sha256=net_forward_review["review_sha256"],
        declarations=rows,
        **contract.period_assessment(net_forward_review, declarations_ready=ready),
        limitations=[
            "NET_PERFORMANCE_MAINTENANCE_IS_NOT_IC_OR_FULL_ALPHA_HEALTH",
            "NO_LIFECYCLE_CHANGE_OR_INDEPENDENT_SOURCE_TIME_AUTHORITY",
            "NO_FROZEN_VALIDATION_SEARCH_SELECTION_OR_OOS_LEAKAGE_ASSESSMENT",
        ],
        invented_ic=False,
        lifecycle_changed=False,
        independent_verification=False,
        economic_authority=False,
        submission_enabled=False,
    )
    result["review_sha256"] = digest(result)
    return contract.validate_review(
        result,
        policy=policy,
        net_forward_policy=net_forward_policy,
        net_forward_review=net_forward_review,
        subject=subject,
        candidate_id=candidate_id,
        native_result_hash=native_result_hash,
        project_cut_sha256=project_cut_sha256,
        snapshot_as_of_ms=snapshot_as_of_ms,
    )
