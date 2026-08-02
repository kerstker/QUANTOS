from __future__ import annotations

from quantos.contracts.hashing import stable_hash
from services.research import alpha_expression as ax
from services.research.alpha_production_workflow import (
    AlphaProductionWorkflow,
    CandidateIdentity,
    MaterialChangeClass,
)
from tools.alpha_production_workflow import reconcile_workflow


COMMIT = "b" * 40


def _identity(candidate_id: str) -> CandidateIdentity:
    expression = ("cs_rank", ("neg", ("field", "global_ls")))
    return CandidateIdentity(
        candidate_id=candidate_id,
        expression=ax.serialize(expression),
        quantile=0.3,
        cadence_days=7,
        universe="cs_majors",
        family_id="CROWDING_REVERSAL",
        source_kind="STATIC_PREREGISTERED",
        source_ref="test",
        source_definition_hash=stable_hash({"candidate": candidate_id}),
        required_datasets=("global_ls",),
        economic_hypothesis="Crowding should mean revert.",
        falsification_criteria=("Held-out net spread is non-positive.",),
        created_at_ms=0,
        change_class=MaterialChangeClass.NEW_CANDIDATE,
    )


def _evaluation(candidate_id: str, verdict: str = "PASS") -> dict:
    return {
        "schema_version": "forward_alpha_evaluations.v1",
        "generated_at": 2_000,
        "dataset_version": "dataset-v1",
        "alphas": {
            candidate_id: {
                "status": "VERIFIED",
                "evaluation": {
                    "verified": True,
                    "verdict": verdict,
                    "run_id": f"run-{candidate_id}",
                },
            }
        },
    }


def _reconcile(
    workflow: AlphaProductionWorkflow,
    identities: tuple[CandidateIdentity, ...],
    *,
    risk_ready: bool = True,
) -> dict:
    candidate_ids = [identity.candidate_id for identity in identities]
    forward_eval = {
        "schema_version": "forward_alpha_evaluations.v1",
        "generated_at": 2_000,
        "alphas": {
            candidate_id: _evaluation(candidate_id)["alphas"][candidate_id]
            for candidate_id in candidate_ids
        },
    }
    return reconcile_workflow(
        workflow=workflow,
        identities=identities,
        historical_document={},
        historical_path="historical.json",
        forward_evaluations_document=forward_eval,
        forward_evaluations_path="forward-evaluations.json",
        forward_status_document={
            "ran_at": 2_100,
            "alphas": {
                candidate_id: {"n_forward_periods": 8, "evidence_state": "RUNNING"}
                for candidate_id in candidate_ids
            },
        },
        forward_status_path="forward-status.json",
        risk_document={
            "generated_at": 2_200,
            "config_hash": "risk-v1",
            "alphas": {
                candidate_id: {"status": "READY" if risk_ready else "PENDING"}
                for candidate_id in candidate_ids
            },
        },
        risk_path="risk.json",
        realtime_document={
            "generated_at": 2_300,
            "alphas": {
                candidate_id: {"status": "PAPER_MARKED"}
                for candidate_id in candidate_ids
            },
        },
        realtime_path="realtime.json",
        lifecycle_document={},
        lifecycle_path="lifecycle.json",
        release_commit=COMMIT,
    )


def test_reconciler_creates_one_authoritative_candidate_state(tmp_path) -> None:
    identity = _identity("factory_alpha")
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,))
        assert snapshot["status"] == "VERIFIED"
        assert snapshot["active_candidate_ids"] == [identity.candidate_id]
        assert snapshot["execution_paper_eligible_ids"] == [identity.candidate_id]
        assert snapshot["execution_paper_ids"] == [identity.candidate_id]
        assert snapshot["reconciliation"]["inserted_candidates"] == 1
        assert snapshot["reconciliation"]["inserted_events"] == 1
        row = snapshot["candidates"][0]
        assert row["workflow"]["stage"] == "EXECUTION_PAPER"
        assert row["workflow"]["eligible_for_execution_paper"] is True

        replay = _reconcile(workflow, (identity,))
        assert replay["reconciliation"]["inserted_candidates"] == 0
        assert replay["reconciliation"]["inserted_events"] == 0


def test_current_gate_regression_blocks_eligibility_without_erasing_stage(tmp_path) -> None:
    identity = _identity("factory_alpha")
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        _reconcile(workflow, (identity,))
        regressed = _reconcile(workflow, (identity,), risk_ready=False)
        row = regressed["candidates"][0]["workflow"]
        assert row["stage"] == "EXECUTION_PAPER"
        assert row["eligible_for_execution_paper"] is False
        assert "RESEARCH_RISK_NOT_READY" in row["blockers"]
        assert regressed["execution_paper_eligible_ids"] == []
        assert regressed["execution_paper_ids"] == [identity.candidate_id]


def test_legacy_paper_mark_remains_quarantined(tmp_path) -> None:
    identity = _identity("crowd_contra")
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        snapshot = _reconcile(workflow, (identity,))
        row = snapshot["candidates"][0]["workflow"]
        assert row["stage"] == "PARALLEL_VALIDATION"
        assert row["legacy_diagnostic"] is True
        assert row["eligible_for_execution_paper"] is False
        assert "LEGACY_DIAGNOSTIC_NOT_DEPLOYMENT_ELIGIBLE" in row["blockers"]
