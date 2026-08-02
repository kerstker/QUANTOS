from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.governance.external_evidence import (
    ContinuityObservation,
    ExternalEvidenceStatus,
    build_external_blocked_status,
    evaluate_continuity,
)


def test_external_blocked_status_is_explicit_and_frozen() -> None:
    status = build_external_blocked_status(
        capability="oidc_identity",
        reason_code="OIDC_DEPLOYMENT_NOT_PROVIDED",
        required_action="Provision an operator-owned OIDC issuer and trusted mTLS scope",
        observed_at_ms=100,
    )

    assert status.schema_version == 1
    assert status.status == "EXTERNAL_BLOCKED"
    assert status.capability == "oidc_identity"
    assert status.external_authority == ""
    with pytest.raises(ValidationError):
        ExternalEvidenceStatus.model_validate(
            {**status.model_dump(), "status": "VERIFIED"}
        )
    with pytest.raises(Exception):
        status.status = "BREACH"


def test_status_contract_requires_external_proof_when_verified() -> None:
    with pytest.raises(ValidationError):
        ExternalEvidenceStatus(
            capability="artifact_admission",
            status="VERIFIED",
            reason_code="VERIFIED",
            required_action="",
            observed_at_ms=1,
        )


def test_continuity_passes_only_after_full_window_and_clean_observations() -> None:
    observations = tuple(
        ContinuityObservation(observed_at_ms=at, status="PASS")
        for at in (0, 100, 200)
    )
    result = evaluate_continuity(
        observations,
        now_ms=200,
        max_gap_ms=100,
        minimum_history_span_ms=200,
        minimum_passes=3,
        clean_lookback_ms=200,
    )
    assert result.verdict == "PASS"
    assert result.observation_count == 3
    assert result.pass_count == 3
    assert result.history_span_ms == 200
    assert result.maximum_gap_ms == 100
    assert result.latest_age_ms == 0
    assert result.reason_codes == ()


@pytest.mark.parametrize(
    ("status", "verdict", "reason"),
    [
        ("EXTERNAL_BLOCKED", "EXTERNAL_BLOCKED", "EXTERNAL_EVIDENCE_BLOCKED"),
        ("UNAVAILABLE", "UNAVAILABLE", "EVIDENCE_UNAVAILABLE"),
        ("BREACH", "BREACH", "EVIDENCE_BREACH"),
    ],
)
def test_continuity_preserves_external_and_local_failure_states(
    status: str, verdict: str, reason: str
) -> None:
    result = evaluate_continuity(
        (ContinuityObservation(observed_at_ms=10, status=status),),
        now_ms=10,
        max_gap_ms=100,
        minimum_history_span_ms=0,
        minimum_passes=1,
        clean_lookback_ms=100,
    )
    assert result.verdict == verdict
    assert reason in result.reason_codes


def test_continuity_rejects_out_of_order_and_stale_observations() -> None:
    out_of_order = evaluate_continuity(
        (
            ContinuityObservation(observed_at_ms=20, status="PASS"),
            ContinuityObservation(observed_at_ms=10, status="PASS"),
        ),
        now_ms=20,
        max_gap_ms=100,
        minimum_history_span_ms=0,
        minimum_passes=1,
        clean_lookback_ms=100,
    )
    assert out_of_order.verdict == "BREACH"
    assert "OBSERVATIONS_NOT_MONOTONIC" in out_of_order.reason_codes

    stale = evaluate_continuity(
        (ContinuityObservation(observed_at_ms=10, status="PASS"),),
        now_ms=111,
        max_gap_ms=100,
        minimum_history_span_ms=0,
        minimum_passes=1,
        clean_lookback_ms=100,
    )
    assert stale.verdict == "BREACH"
    assert "LATEST_OBSERVATION_STALE" in stale.reason_codes


def test_identity_and_signing_report_external_blockers_without_side_effects() -> None:
    from services.governance.signing import NullSigningAuthority, signing_capability_status
    from services.governance.strong_identity import oidc_policy_configuration_status

    identity = oidc_policy_configuration_status({}, observed_at_ms=10)
    assert identity.status == "EXTERNAL_BLOCKED"
    assert identity.reason_code == "IDENTITY_POLICY_UNAVAILABLE"

    signing = signing_capability_status(NullSigningAuthority(), observed_at_ms=10)
    assert signing.status == "EXTERNAL_BLOCKED"
    assert signing.reason_code == "SIGNING_AUTHORITY_NOT_PROVISIONED"


def test_artifact_external_evidence_is_blocked_until_all_external_inputs_exist() -> None:
    from services.governance.artifact_admission import artifact_external_evidence_status

    blocked = artifact_external_evidence_status(observed_at_ms=10)
    assert blocked.status == "EXTERNAL_BLOCKED"
    assert blocked.reason_code == "ARTIFACT_EXTERNAL_EVIDENCE_NOT_PROVIDED"
    partial = artifact_external_evidence_status(
        observed_at_ms=10,
        external_bundle_available=True,
        signed_receipt_available=True,
        independent_deployment_available=True,
        external_authority="operator-authority",
    )
    assert partial.status == "PARTIAL"
    verified = artifact_external_evidence_status(
        observed_at_ms=10,
        external_bundle_available=True,
        signed_receipt_available=True,
        independent_deployment_available=True,
        external_authority="operator-authority",
        evidence_ref="receipt-1",
    )
    assert verified.status == "VERIFIED"


@pytest.mark.parametrize(
    "evaluator",
    [
        "heartbeat",
        "seal",
        "restore",
    ],
)
def test_restore_heartbeat_and_seal_expose_the_same_continuity_contract(evaluator: str) -> None:
    if evaluator == "heartbeat":
        from tools.external_heartbeat import evaluate_heartbeat_continuity as evaluate
    elif evaluator == "seal":
        from tools.domain_event_seal import evaluate_event_seal_continuity as evaluate
    else:
        from tools.l2_backup import evaluate_restore_continuity as evaluate
    result = evaluate(
        (ContinuityObservation(observed_at_ms=1, status="EXTERNAL_BLOCKED"),),
        now_ms=1,
        max_gap_ms=100,
        minimum_history_span_ms=0,
        minimum_passes=1,
        clean_lookback_ms=100,
    )
    assert result.verdict == "EXTERNAL_BLOCKED"
    assert "EXTERNAL_EVIDENCE_BLOCKED" in result.reason_codes
