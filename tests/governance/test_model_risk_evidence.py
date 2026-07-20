import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from quantos.contracts.hashing import stable_hash
from scripts.generate_model_risk_evidence import (
    build_model_risk_evidence,
    execute_agent_boundary_red_team,
)
from scripts.generate_model_risk_dataset import generate_bundle
from services.governance.model_risk_history import build_model_risk_revision
from services.intelligence.runtime import RESEARCH_EVALUATION_TEMPLATE


def _write_fixture_repo(tmp_path: Path) -> tuple[Path, Path]:
    sources = {
        "services/intelligence/agent_loop.py": b"agent-loop-fixture\n",
        "services/intelligence/guardrails.py": b"guardrails-fixture\n",
        "services/intelligence/runtime.py": (
            f"RESEARCH_EVALUATION_TEMPLATE = {RESEARCH_EVALUATION_TEMPLATE!r}\n"
        ).encode("utf-8"),
        "services/research/ml/stacker.py": b"stacker-fixture\n",
        "tools/ml_stack_research.py": b"driver-fixture\n",
        "quantos/contracts/model_risk.py": b"contracts-fixture\n",
        "services/governance/model_risk.py": b"decisions-fixture\n",
        "services/governance/model_risk_dataset.py": b"dataset-fixture\n",
        "services/governance/model_risk_history.py": b"history-fixture\n",
        "services/governance/model_risk_registry.py": b"registry-fixture\n",
        "scripts/generate_model_risk_dataset.py": b"dataset-generator-fixture\n",
        "scripts/generate_model_risk_evidence.py": b"generator-fixture\n",
        "scripts/update_model_risk_history.py": b"history-generator-fixture\n",
    }
    for relative, content in sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    snapshot = {
        "asset": "fx",
        "cost_bps": 5.0,
        "factors": ["momentum_120"],
        "feature_rows": 123882,
        "fwd_horizon": 5,
        "single_best_name": "momentum_120",
        "single_best_oos_sharpe": -2.0389,
        "stack_gbm_folds": 24,
        "stack_gbm_oos_sharpe": -1.1173,
        "stack_ridge_folds": 24,
        "stack_ridge_oos_sharpe": -0.4035,
        "trials": 8,
        "universe_size": 18,
        "verdict": "REFUTED_NO_EDGE",
    }
    snapshot_path = tmp_path / "data/cache/ml_stack_snapshot.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    dataset_path = tmp_path / "data/cache/fx.db"
    connection = sqlite3.connect(dataset_path)
    connection.execute(
        "CREATE TABLE bars (key TEXT NOT NULL, event_time INTEGER NOT NULL, "
        "knowledge_time INTEGER NOT NULL, payload_json TEXT NOT NULL, "
        "PRIMARY KEY (key, event_time, knowledge_time))"
    )
    connection.executemany(
        "INSERT INTO bars VALUES (?, ?, ?, ?)",
        [
            ("AUD", 1, 1, '{"close":1}'),
            ("AUD", 2, 2, '{"close":2}'),
            ("EUR", 1, 1, '{"close":3}'),
        ],
    )
    connection.commit()
    connection.close()
    return snapshot_path, dataset_path


def test_real_agent_boundary_red_team_denies_privilege_and_allows_handoff():
    report = execute_agent_boundary_red_team(recorded_at=1)
    assert report.verdict.value == "PASS"
    assert report.tool_manifest_ref == "research-agent-tools@v6"
    assert {case.case_id for case in report.cases} == set(report.required_case_ids)
    assert all(not case.boundary_breached for case in report.cases)
    exceptions = {case.case_id: case.observed_exception for case in report.cases}
    assert exceptions == {
        "human-actor-constructor": "ValueError",
        "agent-approve": "ForbiddenAgentAction",
        "agent-reset-kill-switch": "ForbiddenAgentAction",
        "agent-request-human-approval": "",
    }


def test_evidence_is_deterministic_honest_and_hash_verified(tmp_path):
    snapshot_path, dataset_path = _write_fixture_repo(tmp_path)
    first = build_model_risk_evidence(
        tmp_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        baseline_recorded_at=1,
        recorded_at=2,
    )
    second = build_model_risk_evidence(
        tmp_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        baseline_recorded_at=1,
        recorded_at=2,
    )
    assert first == second
    assert first["current_maturity"] == "M2"
    assert first["source_result_verdict"] == "REFUTED_NO_EDGE"
    assert first["red_team_verdict"] == "PASS"
    assert first["challenger_verdicts"] == [
        "REJECT_ABSOLUTE_FLOOR",
        "REJECT_ABSOLUTE_FLOOR",
    ]
    assert first["prompt_template_count"] == 1
    assert first["runtime_prompt_surface_status"] == (
        "GOVERNED_OFFLINE_BOUNDARY_NO_EXTERNAL_PROVIDER"
    )
    assert first["runtime_invocation_count"] == 2
    assert first["runtime_history_verified"] is True
    assert len(first["runtime_history_sha256"]) == 64
    assert [event["sequence"] for event in first["runtime_events"]] == [1, 2]
    assert first["artifact_type_counts"]["RedTeamReport"] == 2
    assert first["red_team_report_ref"] == (
        "red-team-report:research-agent-runtime-boundary-v7"
    )
    active_report = next(
        record
        for record in first["artifacts"]
        if record["artifact_id"] == first["red_team_report_ref"]
    )
    assert {
        "runtime-action-override",
        "runtime-sensitive-output",
        "runtime-forbidden-tool-action",
    } <= set(active_report["payload"]["required_case_ids"])
    assert "LONGITUDINAL_REGISTRY_HISTORY_NOT_RETAINED" not in first["m3_blockers"]
    assert first["review_identity_assurance"] == "PROCESS_ROLE_ONLY"
    assert first["raw_dataset_retained_in_versioned_evidence"] is False
    assert first["execution_authority_granted"] is False
    assert first["live_trading_authority_granted"] is False
    assert set(first["governance_implementation_sha256"]) == {
        "quantos/contracts/model_risk.py",
        "services/governance/model_risk.py",
        "services/governance/model_risk_dataset.py",
        "services/governance/model_risk_history.py",
        "services/governance/model_risk_registry.py",
        "services/intelligence/runtime.py",
        "scripts/generate_model_risk_dataset.py",
        "scripts/generate_model_risk_evidence.py",
        "scripts/update_model_risk_history.py",
    }
    assert all(
        stable_hash(record["payload"]) == record["content_hash"]
        for record in first["artifacts"]
    )
    assert first["dataset_sha256"] == hashlib.sha256(dataset_path.read_bytes()).hexdigest()


def test_generator_rejects_attempt_to_turn_null_result_positive(tmp_path):
    snapshot_path, dataset_path = _write_fixture_repo(tmp_path)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["stack_ridge_oos_sharpe"] = 0.5
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    with pytest.raises(ValueError, match="negative OOS result"):
        build_model_risk_evidence(
            tmp_path,
            snapshot_path=snapshot_path,
            dataset_path=dataset_path,
            baseline_recorded_at=1,
            recorded_at=2,
        )


def test_versioned_pit_bundle_replaces_local_dataset_reference(tmp_path):
    snapshot_path, dataset_path = _write_fixture_repo(tmp_path)
    bundle = tmp_path / "evidence/datasets/fx.jsonl.gz"
    manifest = tmp_path / "evidence/datasets/fx.manifest.json"
    generate_bundle(
        dataset_path,
        bundle,
        manifest,
        keys=("AUD", "EUR"),
        repo_root=tmp_path,
    )
    evidence = build_model_risk_evidence(
        tmp_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        dataset_bundle_path=bundle,
        dataset_manifest_path=manifest,
        baseline_recorded_at=1,
        recorded_at=2,
    )

    assert evidence["dataset_bundle_status"] == "VERSIONED_PIT_SUBSET_BUNDLE"
    assert evidence["raw_dataset_retained_in_versioned_evidence"] is True
    assert evidence["dataset_ref"] == "evidence/datasets/fx.jsonl.gz"
    assert evidence["dataset_bundle_row_count"] == 3
    assert "RAW_DATASET_NOT_RETAINED_IN_VERSIONED_EVIDENCE" not in evidence["m3_blockers"]
    stale_limitations = {
        "RAW_FX_DATASET_LOCAL_NOT_VERSIONED",
        "RAW_DATASET_NOT_RETAINED_IN_EVIDENCE_BUNDLE",
    }
    for record in evidence["artifacts"]:
        limitations = set(record["payload"].get("limitations", ()))
        assert limitations.isdisjoint(stale_limitations)
    evaluation_datasets = {
        record["payload"].get("dataset_ref")
        for record in evidence["artifacts"]
        if record["artifact_type"] == "OfflineEvaluation"
    }
    assert evaluation_datasets == {"evidence/datasets/fx.jsonl.gz"}


def test_single_revision_mode_remains_reconstructable(tmp_path):
    snapshot_path, dataset_path = _write_fixture_repo(tmp_path)
    baseline = build_model_risk_evidence(
        tmp_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        baseline_recorded_at=1,
        recorded_at=2,
        include_longitudinal_v2=False,
    )

    assert baseline["recorded_at"] == 1
    assert baseline["runtime_invocation_count"] == 1
    assert baseline["artifact_type_counts"]["RedTeamReport"] == 1
    assert baseline["red_team_report_ref"] == (
        "red-team-report:research-agent-boundary-v6"
    )
    assert "LONGITUDINAL_REGISTRY_HISTORY_NOT_RETAINED" in baseline["m3_blockers"]


def test_new_release_appends_prior_artifacts_and_runtime_events(tmp_path):
    snapshot_path, dataset_path = _write_fixture_repo(tmp_path)
    first = build_model_risk_evidence(
        tmp_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        baseline_recorded_at=1,
        recorded_at=2,
        release_version="v1",
    )
    prior = build_model_risk_revision(first)["snapshot"]
    guardrails = tmp_path / "services/intelligence/guardrails.py"
    guardrails.write_bytes(guardrails.read_bytes() + b"release-two\n")

    second = build_model_risk_evidence(
        tmp_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        baseline_recorded_at=3,
        recorded_at=4,
        prior_snapshot=prior,
        release_version="v2",
    )

    assert second["artifact_count"] == first["artifact_count"] * 2
    assert second["runtime_invocation_count"] == 4
    assert second["runtime_events"][:2] == first["runtime_events"]
    assert [event["sequence"] for event in second["runtime_events"]] == [1, 2, 3, 4]
    assert second["runtime_events"][-1]["invocation_id"] == (
        "model-risk-runtime-red-team-v3"
    )
    assert second["red_team_report_ref"] == (
        "red-team-report:research-agent-runtime-boundary-v3"
    )
    artifact_ids = {record["artifact_id"] for record in second["artifacts"]}
    assert "model-card:research-agent-loop:v1" in artifact_ids
    assert "model-card:research-agent-loop:v2" in artifact_ids

    current = build_model_risk_revision(second)["snapshot"]
    repeated = build_model_risk_evidence(
        tmp_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        baseline_recorded_at=3,
        recorded_at=4,
        prior_snapshot=current,
        release_version="v2",
    )
    assert repeated == second
