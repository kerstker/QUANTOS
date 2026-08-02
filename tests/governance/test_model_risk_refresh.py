import json
from pathlib import Path

from scripts.generate_model_risk_evidence import build_model_risk_evidence
from scripts.refresh_model_risk_evidence import refresh_model_risk_evidence
from services.governance.model_risk_history import append_model_risk_revision, validate_model_risk_history

from tests.governance.test_model_risk_evidence import _write_fixture_repo


def test_versioned_refresh_retains_prior_artifacts_and_appends_runtime_history(tmp_path: Path):
    snapshot_path, dataset_path = _write_fixture_repo(tmp_path)
    baseline = build_model_risk_evidence(
        tmp_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        baseline_recorded_at=1,
        recorded_at=2,
    )
    history, _ = append_model_risk_revision(None, baseline)
    prior_evidence_path = tmp_path / "evidence/model-risk.json"
    prior_history_path = tmp_path / "evidence/model-risk-history.json"
    prior_evidence_path.parent.mkdir(parents=True, exist_ok=True)
    prior_evidence_path.write_text(json.dumps(baseline), encoding="utf-8")
    prior_history_path.write_text(json.dumps(history), encoding="utf-8")

    refreshed, refreshed_history = refresh_model_risk_evidence(
        tmp_path,
        prior_evidence_path=prior_evidence_path,
        prior_history_path=prior_history_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        dataset_bundle_path=None,
        dataset_manifest_path=None,
        recorded_at=3,
    )

    assert refreshed["recorded_at"] == 3
    assert refreshed["artifact_count"] == 26
    assert refreshed["runtime_invocation_count"] == 4
    assert refreshed["prompt_template_count"] == 2
    assert refreshed["red_team_report_ref"] == "red-team-report:research-agent-runtime-boundary-v8"
    ids = {record["artifact_id"] for record in refreshed["artifacts"]}
    assert "model-card:research-agent-loop:v6" in ids
    assert "model-card:research-agent-loop:v7" in ids
    assert "prompt-card:research-evaluation:v7" in ids
    assert refreshed_history["revision_count"] == 2
    validate_model_risk_history(refreshed_history)
    assert refreshed_history["revisions"][0]["snapshot"]["runtime_events"] == refreshed["runtime_events"][:2]


def test_default_refresh_versions_advance_from_immutable_history(tmp_path: Path):
    snapshot_path, dataset_path = _write_fixture_repo(tmp_path)
    baseline = build_model_risk_evidence(
        tmp_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        baseline_recorded_at=1,
        recorded_at=2,
    )
    history, _ = append_model_risk_revision(None, baseline)
    evidence_path = tmp_path / "evidence/model-risk.json"
    history_path = tmp_path / "evidence/model-risk-history.json"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(json.dumps(baseline), encoding="utf-8")
    history_path.write_text(json.dumps(history), encoding="utf-8")

    first, first_history = refresh_model_risk_evidence(
        tmp_path,
        prior_evidence_path=evidence_path,
        prior_history_path=history_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        dataset_bundle_path=None,
        dataset_manifest_path=None,
        recorded_at=3,
    )
    evidence_path.write_text(json.dumps(first), encoding="utf-8")
    history_path.write_text(json.dumps(first_history), encoding="utf-8")

    second, second_history = refresh_model_risk_evidence(
        tmp_path,
        prior_evidence_path=evidence_path,
        prior_history_path=history_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        dataset_bundle_path=None,
        dataset_manifest_path=None,
        recorded_at=4,
    )

    ids = {record["artifact_id"] for record in second["artifacts"]}
    assert "model-card:research-agent-loop:v8" in ids
    assert second["red_team_report_ref"] == "red-team-report:research-agent-runtime-boundary-v9"
    assert second["artifact_count"] == 39
    assert second["runtime_invocation_count"] == 6
    assert second_history["revision_count"] == 3
    validate_model_risk_history(second_history)
