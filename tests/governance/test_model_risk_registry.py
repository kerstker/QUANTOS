import json

import pytest
from pydantic import ValidationError


def _model_card():
    from quantos.contracts.model_risk import (
        ModelCard,
        ModelUseStatus,
        PromptLineageStatus,
    )

    return ModelCard(
        model_id="fx-stack-ridge",
        version="v1",
        owner_group="research-ml",
        model_type="RIDGE_STACKER",
        implementation_ref="services/research/ml/stacker.py",
        implementation_sha256="a" * 64,
        dataset_ref="data/cache/fx.db",
        dataset_sha256="b" * 64,
        intended_use="offline factor stacking research",
        prohibited_uses=("LIVE_TRADING", "ORDER_SUBMISSION"),
        prompt_lineage_status=PromptLineageStatus.NO_RUNTIME_LLM_PROMPT_SURFACE,
        tool_manifest_ref="",
        status=ModelUseStatus.RESEARCH_ONLY,
        recorded_at=1,
    )


def test_model_card_is_frozen_hashed_and_digest_validated():
    from quantos.contracts.model_risk import ModelCard

    card = _model_card()
    assert card.model_ref == "fx-stack-ridge@v1"
    assert card.artifact_id == "model-card:fx-stack-ridge:v1"
    assert len(card.content_hash) == 64
    assert card.content_hash == _model_card().content_hash
    with pytest.raises(ValidationError):
        ModelCard.model_validate(
            {**card.model_dump(), "implementation_sha256": "not-a-digest"}
        )


def test_tool_manifest_rejects_overlapping_allowed_and_prohibited_actions():
    from quantos.contracts.model_risk import ToolManifest

    with pytest.raises(ValidationError, match="overlap"):
        ToolManifest(
            manifest_id="research-agent-tools",
            version="v1",
            implementation_ref="services/intelligence/guardrails.py",
            implementation_sha256="c" * 64,
            allowed_actions=("EVALUATE", "APPROVE"),
            prohibited_actions=("APPROVE", "LIVE_TRADE"),
            recorded_at=1,
        )


def test_registry_is_append_only_idempotent_and_detects_tamper(tmp_path):
    from services.governance.model_risk_registry import (
        GovernedModelRegistry,
        ImmutableModelRiskArtifact,
        SqliteModelRiskStore,
    )

    store = SqliteModelRiskStore(str(tmp_path / "model-risk.db"), create=True)
    registry = GovernedModelRegistry(store)
    card = _model_card()
    assert registry.record_model_card(card) is True
    assert registry.record_model_card(card) is False

    changed = card.model_copy(update={"intended_use": "changed under same identity"})
    with pytest.raises(ImmutableModelRiskArtifact):
        registry.record_model_card(changed)
    assert registry.verify_artifact(card.artifact_id) is True

    row = store._conn.execute(
        "SELECT payload_json FROM model_risk_artifacts WHERE artifact_id=?",
        (card.artifact_id,),
    ).fetchone()
    payload = json.loads(row[0])
    payload["owner_group"] = "tampered"
    store._conn.execute(
        "UPDATE model_risk_artifacts SET payload_json=? WHERE artifact_id=?",
        (json.dumps(payload, sort_keys=True), card.artifact_id),
    )
    store._conn.commit()
    assert registry.verify_artifact(card.artifact_id) is False


def test_red_team_requires_all_cases_and_fails_on_any_boundary_breach():
    from quantos.contracts.model_risk import RedTeamCaseResult, RedTeamVerdict
    from services.governance.model_risk import assess_red_team

    denied = RedTeamCaseResult(
        case_id="agent-approve",
        attack_class="PRIVILEGE_ESCALATION",
        expected_outcome="DENIED",
        actual_outcome="DENIED",
        boundary_breached=False,
        observed_exception="ForbiddenAgentAction",
    )
    with pytest.raises(ValueError, match="missing"):
        assess_red_team(
            report_id="rt-1",
            model_ref="research-agent-loop@v1",
            tool_manifest_ref="tool-manifest:research-agent-tools:v1",
            cases=(denied,),
            required_case_ids=("agent-approve", "agent-reset"),
            recorded_at=1,
        )

    breached = denied.model_copy(
        update={
            "case_id": "agent-reset",
            "actual_outcome": "ALLOWED",
            "boundary_breached": True,
            "observed_exception": "",
        }
    )
    report = assess_red_team(
        report_id="rt-2",
        model_ref="research-agent-loop@v1",
        tool_manifest_ref="tool-manifest:research-agent-tools:v1",
        cases=(denied, breached),
        required_case_ids=("agent-approve", "agent-reset"),
        recorded_at=1,
    )
    assert report.verdict == RedTeamVerdict.FAIL


def test_challenger_relative_lift_is_rejected_when_absolute_metric_is_negative():
    from quantos.contracts.model_risk import ChallengerReview, ChallengerVerdict
    from services.governance.model_risk import compare_challenger

    review = compare_challenger(
        review_id="review-1",
        incumbent_model_ref="fx-single-momentum120@v1",
        challenger_model_ref="fx-stack-ridge@v1",
        incumbent_evaluation_ref="evaluation:fx-single:v1",
        challenger_evaluation_ref="evaluation:fx-ridge:v1",
        model_owner_group="research-ml",
        reviewer_group="independent-model-risk",
        reviewer_id="offline-review-policy-v1",
        primary_metric="oos_sharpe_net_5bps",
        incumbent_value=-2.0389,
        challenger_value=-0.4035,
        minimum_acceptable=0.0,
        recorded_at=1,
    )
    assert review.metric_delta == pytest.approx(1.6354)
    assert review.verdict == ChallengerVerdict.REJECT_ABSOLUTE_FLOOR

    with pytest.raises(ValidationError, match="independent"):
        ChallengerReview.model_validate(
            {**review.model_dump(), "reviewer_group": "research-ml"}
        )


def test_model_risk_readiness_is_external_blocked_until_provider_and_identity_exist():
    from quantos.contracts.model_risk import (
        ChallengerVerdict,
        ReviewIdentityAssurance,
    )
    from services.governance.model_risk import (
        ModelRiskReadiness,
        assess_model_risk_readiness,
    )

    result = assess_model_risk_readiness(
        external_provider_verified=False,
        reviewer_identity=ReviewIdentityAssurance.PROCESS_ROLE_ONLY,
        challenger_verdict=ChallengerVerdict.REJECT_ABSOLUTE_FLOOR,
        challenger_oos_metric=-0.4035,
        forward_periods=0,
        minimum_forward_periods=8,
    )
    assert result.status == ModelRiskReadiness.EXTERNAL_BLOCKED
    assert "EXTERNAL_PROVIDER_NOT_VERIFIED" in result.reason_codes
    assert "REVIEWER_IDENTITY_NOT_EXTERNALLY_AUTHENTICATED" in result.reason_codes
    assert "NO_POSITIVE_OOS_CHALLENGER" in result.reason_codes
    assert result.execution_authority_granted is False


def test_model_risk_readiness_never_invents_positive_edge_from_relative_lift():
    from quantos.contracts.model_risk import (
        ChallengerVerdict,
        ReviewIdentityAssurance,
    )
    from services.governance.model_risk import (
        ModelRiskReadiness,
        assess_model_risk_readiness,
    )

    result = assess_model_risk_readiness(
        external_provider_verified=True,
        reviewer_identity=ReviewIdentityAssurance.EXTERNAL_STRONG_IDENTITY,
        challenger_verdict=ChallengerVerdict.ACCEPT_RESEARCH_ONLY,
        challenger_oos_metric=-0.01,
        forward_periods=8,
        minimum_forward_periods=8,
    )
    assert result.status == ModelRiskReadiness.NO_EDGE
    assert "NO_POSITIVE_OOS_CHALLENGER" in result.reason_codes
    assert result.execution_authority_granted is False


def test_model_risk_readiness_can_only_be_research_only_with_positive_oos_and_full_external_evidence():
    from quantos.contracts.model_risk import (
        ChallengerVerdict,
        ReviewIdentityAssurance,
    )
    from services.governance.model_risk import (
        ModelRiskReadiness,
        assess_model_risk_readiness,
    )

    result = assess_model_risk_readiness(
        external_provider_verified=True,
        reviewer_identity=ReviewIdentityAssurance.EXTERNAL_STRONG_IDENTITY,
        challenger_verdict=ChallengerVerdict.ACCEPT_RESEARCH_ONLY,
        challenger_oos_metric=0.21,
        forward_periods=8,
        minimum_forward_periods=8,
    )
    assert result.status == ModelRiskReadiness.RESEARCH_ONLY
    assert result.reason_codes == ()
    assert result.execution_authority_granted is False
