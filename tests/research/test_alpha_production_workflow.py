from __future__ import annotations

from dataclasses import replace
import sqlite3

import pytest

from quantos.contracts.hashing import stable_hash
from services.research import alpha_expression as ax
from services.research.alpha_production_workflow import (
    AlphaProductionWorkflow,
    AlphaProductionWorkflowError,
    CandidateDefinitionKind,
    CandidateIdentity,
    EvidenceGate,
    EvidenceStatus,
    GateEvidence,
    MaterialChangeClass,
    ProductionStage,
    classify_material_change,
    derive_stage,
)


EXPRESSION = ("cs_rank", ("neg", ("field", "global_ls")))
COMMIT = "a" * 40


def _identity(**updates: object) -> CandidateIdentity:
    values = {
        "candidate_id": "crowd_candidate_v1",
        "expression": ax.serialize(EXPRESSION),
        "quantile": 0.3,
        "cadence_days": 7,
        "universe": "cs_majors",
        "family_id": "CROWDING_REVERSAL",
        "source_kind": "STATIC_PREREGISTERED",
        "source_ref": "forward_track_advance._ALPHAS",
        "source_definition_hash": stable_hash({"source": "test"}),
        "required_datasets": ("global_ls",),
        "economic_hypothesis": "Crowded positioning mean reverts cross-sectionally.",
        "falsification_criteria": ("Held-out net spread is non-positive.",),
        "created_at_ms": 1234,
    }
    values.update(updates)
    return CandidateIdentity(**values)


def _gates(
    *,
    historical: EvidenceStatus = EvidenceStatus.PASS,
    shadow: EvidenceStatus = EvidenceStatus.MATURE,
    risk: EvidenceStatus = EvidenceStatus.READY,
    execution: EvidenceStatus = EvidenceStatus.PENDING,
    lifecycle: EvidenceStatus = EvidenceStatus.RUNNING,
) -> tuple[GateEvidence, ...]:
    statuses = (historical, shadow, risk, execution, lifecycle)
    return tuple(
        GateEvidence(
            gate=gate,
            status=status,
            reference=f"evidence/{gate.value.lower()}.json",
            content_hash=stable_hash({"gate": gate.value, "status": status.value}),
            observed_at_ms=2000,
        )
        for gate, status in zip(EvidenceGate, statuses)
    )


def _bundle(identity: CandidateIdentity, **updates: object):
    values = {
        "gates": _gates(),
        "identity_hash": identity.identity_hash,
        "candidate_id": identity.candidate_id,
        "observed_forward_periods": 8,
        "required_forward_periods": 8,
        "blockers": (),
        "release_commit": COMMIT,
        "legacy_diagnostic": False,
        "as_of_ms": 2000,
    }
    values.update(updates)
    return derive_stage(**values)


def test_material_change_classification_is_explicit_and_conservative() -> None:
    base = _identity()
    assert classify_material_change(base, base) is MaterialChangeClass.NO_CHANGE
    assert classify_material_change(
        base,
        replace(base, source_ref="new immutable receipt"),
    ) is MaterialChangeClass.NON_MATERIAL_EVIDENCE
    assert classify_material_change(
        base,
        replace(base, quantile=0.2),
    ) is MaterialChangeClass.MATERIAL_SIGNAL
    assert classify_material_change(
        base,
        replace(base, required_datasets=("global_ls", "taker_ls")),
    ) is MaterialChangeClass.MATERIAL_DATA
    assert classify_material_change(
        base,
        replace(base, quantile=0.2, required_datasets=("global_ls", "taker_ls")),
    ) is MaterialChangeClass.MATERIAL_MULTIPLE


def test_existing_expression_identity_hashes_remain_byte_stable() -> None:
    identity = _identity()
    assert identity.identity_hash == (
        "9f5a639a9e6323d85dfbaca5755ba896273cde3a7134f1c480cf32bc16c59a0f"
    )
    assert identity.definition_hash == (
        "3649f5d27141c02a41a17b878b9be61e5e8c925ddac6067b0e698c14fb2d589e"
    )


def test_realtime_mechanism_identity_is_canonical_and_append_only(tmp_path) -> None:
    signal_spec = (
        '{"candidate_id":"rt_test_5m_v1","decision_interval_ms":30000,'
        '"label_horizon_ms":300000,"schema_version":'
        '"realtime-candidate-signal-spec.v1"}'
    )
    identity = _identity(
        candidate_id="rt_test_5m_v1",
        expression="",
        cadence_days=0,
        universe="binance_usdm_14_realtime_public",
        family_id="order_flow",
        source_kind="AUTONOMOUS_REALTIME_PREREGISTERED",
        required_datasets=("rt_mid_price",),
        definition_kind=CandidateDefinitionKind.REALTIME_MECHANISM,
        signal_spec_json=signal_spec,
        decision_interval_ms=30_000,
        label_horizon_ms=300_000,
    )
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        assert workflow.register_candidate(identity) is True
        assert workflow.register_candidate(identity) is False
        stored = workflow.identity(identity.candidate_id)
        assert stored == identity
        assert stored.definition_kind is CandidateDefinitionKind.REALTIME_MECHANISM
        with pytest.raises(AlphaProductionWorkflowError, match="different immutable"):
            workflow.register_candidate(replace(identity, label_horizon_ms=600_000))


def test_execution_paper_eligibility_requires_every_current_gate() -> None:
    identity = _identity()
    eligible = _bundle(identity)
    assert eligible.stage is ProductionStage.EXECUTION_PAPER_ELIGIBLE
    assert eligible.eligible_for_execution_paper is True

    pending = _bundle(
        identity,
        gates=_gates(shadow=EvidenceStatus.RUNNING),
        observed_forward_periods=7,
        blockers=("FORWARD_EVIDENCE_MATURITY_PENDING",),
    )
    assert pending.stage is ProductionStage.PARALLEL_VALIDATION
    assert pending.eligible_for_execution_paper is False

    marked = _bundle(
        identity,
        gates=_gates(execution=EvidenceStatus.PAPER_MARKED),
    )
    assert marked.stage is ProductionStage.EXECUTION_PAPER

    legacy = _bundle(
        identity,
        legacy_diagnostic=True,
        blockers=("LEGACY_DIAGNOSTIC_NOT_DEPLOYMENT_ELIGIBLE",),
        gates=_gates(execution=EvidenceStatus.LEGACY_DIAGNOSTIC),
    )
    assert legacy.stage is ProductionStage.PARALLEL_VALIDATION
    assert legacy.eligible_for_execution_paper is False


def test_registry_is_idempotent_hash_chained_and_append_only(tmp_path) -> None:
    identity = _identity()
    path = tmp_path / "production_workflow.db"
    with AlphaProductionWorkflow(path) as workflow:
        assert workflow.register_candidate(identity) is True
        assert workflow.register_candidate(identity) is False
        eligible = _bundle(identity)
        assert workflow.record_evidence(eligible) is True
        assert workflow.record_evidence(eligible) is False
        marked = _bundle(
            identity,
            gates=_gates(execution=EvidenceStatus.PAPER_MARKED),
            as_of_ms=3000,
        )
        assert workflow.record_evidence(marked) is True
        assert workflow.chain_valid() is True
        snapshot = workflow.snapshot()
        assert snapshot["status"] == "VERIFIED"
        assert snapshot["active_candidate_ids"] == [identity.candidate_id]
        assert snapshot["execution_paper_ids"] == [identity.candidate_id]
        assert snapshot["safety"]["live_allowed"] is False

        with pytest.raises(AlphaProductionWorkflowError, match="different immutable"):
            workflow.register_candidate(replace(identity, quantile=0.2))
        with pytest.raises(AlphaProductionWorkflowError, match="move backward"):
            workflow.record_evidence(
                _bundle(
                    identity,
                    gates=_gates(shadow=EvidenceStatus.RUNNING),
                    observed_forward_periods=3,
                    blockers=("FORWARD_EVIDENCE_MATURITY_PENDING",),
                    previous_stage=ProductionStage.FROZEN,
                    as_of_ms=4000,
                )
            )
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            workflow._connection.execute("DELETE FROM alpha_population")  # noqa: SLF001

    with AlphaProductionWorkflow(path, create=False) as reopened:
        assert reopened.chain_valid() is True
        assert reopened.latest_bundle(identity.candidate_id).stage is ProductionStage.EXECUTION_PAPER


def test_material_successor_requires_matching_declared_change(tmp_path) -> None:
    base = _identity()
    successor = _identity(
        candidate_id="crowd_candidate_v2",
        quantile=0.2,
        predecessor_id=base.candidate_id,
        change_class=MaterialChangeClass.MATERIAL_MULTIPLE,
    )
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        workflow.register_candidate(base)
        with pytest.raises(AlphaProductionWorkflowError, match="does not match"):
            workflow.register_candidate(successor)
        workflow.register_candidate(
            replace(successor, change_class=MaterialChangeClass.MATERIAL_SIGNAL)
        )


@pytest.mark.parametrize(
    "updates,match",
    (
        ({"candidate_id": "bad id"}, "candidate_id"),
        ({"expression": "(broken"}, "expression"),
        ({"quantile": 0.0}, "quantile"),
        ({"cadence_days": 0}, "cadence_days"),
        ({"source_definition_hash": "bad"}, "source_definition_hash"),
        ({"required_datasets": ("z", "a")}, "required_datasets"),
        ({"created_at_ms": -1}, "created_at_ms"),
    ),
)
def test_registry_rejects_malformed_identity(tmp_path, updates, match) -> None:
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        with pytest.raises(AlphaProductionWorkflowError, match=match):
            workflow.register_candidate(_identity(**updates))


@pytest.mark.parametrize(
    "updates,match",
    (
        ({"signal_spec_json": "{} "}, "not canonical"),
        ({"decision_interval_ms": 0}, "decision_interval_ms"),
        ({"label_horizon_ms": None}, "label_horizon_ms"),
        ({"cadence_days": 1}, "cadence_days"),
        ({"expression": ax.serialize(EXPRESSION)}, "expression must be empty"),
    ),
)
def test_registry_rejects_malformed_realtime_identity(tmp_path, updates, match) -> None:
    values = {
        "candidate_id": "rt_test_5m_v1",
        "expression": "",
        "quantile": 0.2,
        "cadence_days": 0,
        "universe": "binance_usdm_14_realtime_public",
        "family_id": "order_flow",
        "source_kind": "AUTONOMOUS_REALTIME_PREREGISTERED",
        "source_ref": "realtime_alpha_registry:" + "a" * 64,
        "source_definition_hash": stable_hash({"source": "realtime"}),
        "required_datasets": ("rt_mid_price",),
        "economic_hypothesis": "Flow predicts a post-latency move.",
        "falsification_criteria": ("Net return is non-positive.",),
        "created_at_ms": 1234,
        "definition_kind": CandidateDefinitionKind.REALTIME_MECHANISM,
        "signal_spec_json": (
            '{"candidate_id":"rt_test_5m_v1","decision_interval_ms":30000,'
            '"label_horizon_ms":300000}'
        ),
        "decision_interval_ms": 30_000,
        "label_horizon_ms": 300_000,
    }
    values.update(updates)
    with AlphaProductionWorkflow(tmp_path / "workflow.db") as workflow:
        with pytest.raises(AlphaProductionWorkflowError, match=match):
            workflow.register_candidate(CandidateIdentity(**values))
