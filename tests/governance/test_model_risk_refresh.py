import hashlib
import json
from pathlib import Path

import pytest

from scripts.generate_model_risk_dataset import generate_bundle
from scripts.generate_model_risk_evidence import (
    _RetainedDatasetAnchor,
    _build_model_risk_evidence_with_retained_bundle,
    build_model_risk_evidence,
)
from scripts.refresh_model_risk_evidence import refresh_model_risk_evidence
from services.governance.model_risk_history import (
    ModelRiskHistoryError,
    append_model_risk_revision,
    validate_model_risk_history,
)

from tests.governance.test_model_risk_evidence import _write_fixture_repo


def _versioned_refresh_fixture(tmp_path: Path) -> dict[str, object]:
    snapshot_path, dataset_path = _write_fixture_repo(tmp_path)
    bundle_path = tmp_path / "evidence/datasets/fx.jsonl.gz"
    manifest_path = tmp_path / "evidence/datasets/fx.manifest.json"
    manifest = generate_bundle(
        dataset_path,
        bundle_path,
        manifest_path,
        keys=("AUD", "EUR"),
        repo_root=tmp_path,
    )
    baseline = build_model_risk_evidence(
        tmp_path,
        snapshot_path=snapshot_path,
        dataset_path=dataset_path,
        dataset_bundle_path=bundle_path,
        dataset_manifest_path=manifest_path,
        baseline_recorded_at=1,
        recorded_at=2,
    )
    history, _ = append_model_risk_revision(None, baseline)
    evidence_path = tmp_path / "evidence/model-risk.json"
    history_path = tmp_path / "evidence/model-risk-history.json"
    evidence_path.write_text(json.dumps(baseline), encoding="utf-8")
    history_path.write_text(json.dumps(history), encoding="utf-8")
    return {
        "root": tmp_path,
        "snapshot_path": snapshot_path,
        "dataset_path": dataset_path,
        "bundle_path": bundle_path,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "baseline": baseline,
        "history": history,
        "evidence_path": evidence_path,
        "history_path": history_path,
    }


def _resign_manifest(path: Path, **changes: object) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    document.update(changes)
    document.pop("manifest_sha256", None)
    canonical = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    document["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    path.write_text(
        json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def _refresh_fixture(fixture: dict[str, object]):
    return refresh_model_risk_evidence(
        fixture["root"],
        prior_evidence_path=fixture["evidence_path"],
        prior_history_path=fixture["history_path"],
        snapshot_path=fixture["snapshot_path"],
        dataset_path=fixture["dataset_path"],
        dataset_bundle_path=fixture["bundle_path"],
        dataset_manifest_path=fixture["manifest_path"],
        recorded_at=3,
    )


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


@pytest.mark.parametrize("source_state", ["missing", "advanced"])
def test_refresh_uses_only_history_anchored_bundle_when_source_is_unavailable(
    tmp_path: Path,
    source_state: str,
):
    fixture = _versioned_refresh_fixture(tmp_path)
    dataset_path = fixture["dataset_path"]
    assert isinstance(dataset_path, Path)
    if source_state == "missing":
        dataset_path.unlink()
    else:
        dataset_path.write_bytes(dataset_path.read_bytes() + b"advanced-source")

    # The public baseline builder remains strict.  Only refresh may use the
    # private seam after binding the retained bundle to immutable history.
    with pytest.raises(ValueError, match="source database digest"):
        build_model_risk_evidence(
            fixture["root"],
            snapshot_path=fixture["snapshot_path"],
            dataset_path=dataset_path,
            dataset_bundle_path=fixture["bundle_path"],
            dataset_manifest_path=fixture["manifest_path"],
            baseline_recorded_at=1,
            recorded_at=3,
        )

    refreshed, refreshed_history = _refresh_fixture(fixture)
    baseline = fixture["baseline"]
    manifest = fixture["manifest"]
    assert isinstance(baseline, dict)
    assert isinstance(manifest, dict)
    assert refreshed["dataset_ref"] == manifest["bundle_ref"]
    assert refreshed["dataset_sha256"] == manifest["bundle_sha256"]
    assert refreshed["source_dataset_ref"] == manifest["source_db_ref"]
    assert refreshed["source_dataset_sha256"] == manifest["source_db_sha256"]
    assert refreshed["dataset_bundle_manifest_sha256"] == manifest["manifest_sha256"]
    assert refreshed["dataset_bundle_row_count"] == manifest["row_count"]
    retained = {record["artifact_id"]: record for record in refreshed["artifacts"]}
    for record in baseline["artifacts"]:
        assert retained[record["artifact_id"]] == record
    assert refreshed_history["revision_count"] == 2
    validate_model_risk_history(refreshed_history)


def test_refresh_with_exact_source_uses_public_strict_builder(
    tmp_path: Path,
    monkeypatch,
):
    import scripts.refresh_model_risk_evidence as refresh_module

    fixture = _versioned_refresh_fixture(tmp_path)

    def forbidden(*_args, **_kwargs):
        pytest.fail("exact source unexpectedly used the no-source private seam")

    monkeypatch.setattr(
        refresh_module,
        "_build_model_risk_evidence_with_retained_bundle",
        forbidden,
    )
    refreshed, _history = _refresh_fixture(fixture)
    assert refreshed["source_dataset_sha256"] == fixture["manifest"][
        "source_db_sha256"
    ]


def test_private_no_source_seam_rechecks_immutable_anchor(tmp_path: Path):
    fixture = _versioned_refresh_fixture(tmp_path)
    dataset_path = fixture["dataset_path"]
    manifest = fixture["manifest"]
    assert isinstance(dataset_path, Path)
    assert isinstance(manifest, dict)
    dataset_path.unlink()
    wrong = _RetainedDatasetAnchor(
        schema_version=int(manifest["schema_version"]),
        bundle_ref=str(manifest["bundle_ref"]),
        bundle_sha256=str(manifest["bundle_sha256"]),
        manifest_ref="evidence/datasets/fx.manifest.json",
        manifest_sha256=str(manifest["manifest_sha256"]),
        row_count=int(manifest["row_count"]) + 1,
        source_db_ref=str(manifest["source_db_ref"]),
        source_db_sha256=str(manifest["source_db_sha256"]),
        source_query=str(manifest["source_query"]),
    )

    with pytest.raises(ValueError, match="anchor mismatch: row_count"):
        _build_model_risk_evidence_with_retained_bundle(
            fixture["root"],
            retained_dataset_anchor=wrong,
            snapshot_path=fixture["snapshot_path"],
            dataset_path=dataset_path,
            dataset_bundle_path=fixture["bundle_path"],
            dataset_manifest_path=fixture["manifest_path"],
            baseline_recorded_at=1,
            recorded_at=3,
        )


@pytest.mark.parametrize("tamper", ["source_ref", "query", "row_count"])
def test_refresh_rejects_resigned_manifest_identity_changes(
    tmp_path: Path,
    tamper: str,
):
    fixture = _versioned_refresh_fixture(tmp_path)
    manifest_path = fixture["manifest_path"]
    manifest = fixture["manifest"]
    assert isinstance(manifest_path, Path)
    assert isinstance(manifest, dict)
    if tamper == "source_ref":
        changes = {"source_db_ref": "data/cache/replacement.db"}
    elif tamper == "query":
        changes = {"source_query": str(manifest["source_query"]) + " LIMIT 1"}
    else:
        changes = {"row_count": int(manifest["row_count"]) + 1}
    _resign_manifest(manifest_path, **changes)

    with pytest.raises(ModelRiskHistoryError):
        _refresh_fixture(fixture)


def test_refresh_rejects_retained_bundle_byte_tamper(tmp_path: Path):
    fixture = _versioned_refresh_fixture(tmp_path)
    bundle_path = fixture["bundle_path"]
    assert isinstance(bundle_path, Path)
    content = bytearray(bundle_path.read_bytes())
    content[-1] ^= 1
    bundle_path.write_bytes(content)

    with pytest.raises(ModelRiskHistoryError, match="retained model-risk dataset bundle"):
        _refresh_fixture(fixture)


def test_refresh_rejects_missing_half_of_retained_pair(tmp_path: Path):
    fixture = _versioned_refresh_fixture(tmp_path)
    dataset_path = fixture["dataset_path"]
    bundle_path = fixture["bundle_path"]
    assert isinstance(dataset_path, Path)
    assert isinstance(bundle_path, Path)
    dataset_path.unlink()
    bundle_path.unlink()

    with pytest.raises(ModelRiskHistoryError, match="must both be retained"):
        _refresh_fixture(fixture)


def test_refresh_rejects_prior_that_is_not_validated_history_tail(tmp_path: Path):
    fixture = _versioned_refresh_fixture(tmp_path)
    evidence_path = fixture["evidence_path"]
    baseline = fixture["baseline"]
    assert isinstance(evidence_path, Path)
    assert isinstance(baseline, dict)
    stale = json.loads(json.dumps(baseline))
    stale["recorded_at"] = int(stale["recorded_at"]) + 1
    evidence_path.write_text(json.dumps(stale), encoding="utf-8")

    with pytest.raises(ModelRiskHistoryError, match="validated history tail"):
        _refresh_fixture(fixture)


@pytest.mark.parametrize("snapshot_state", ["missing", "tampered"])
def test_refresh_requires_exact_retained_snapshot_bytes(
    tmp_path: Path,
    snapshot_state: str,
):
    fixture = _versioned_refresh_fixture(tmp_path)
    snapshot_path = fixture["snapshot_path"]
    assert isinstance(snapshot_path, Path)
    if snapshot_state == "missing":
        snapshot_path.unlink()
        match = "snapshot source is missing"
    else:
        snapshot_path.write_bytes(snapshot_path.read_bytes() + b"\n")
        match = "snapshot digest changed"

    with pytest.raises(ModelRiskHistoryError, match=match):
        _refresh_fixture(fixture)


def test_refresh_rejects_unanchored_embedded_snapshot_change(tmp_path: Path):
    fixture = _versioned_refresh_fixture(tmp_path)
    evidence_path = fixture["evidence_path"]
    baseline = fixture["baseline"]
    assert isinstance(evidence_path, Path)
    assert isinstance(baseline, dict)
    tampered = json.loads(json.dumps(baseline))
    tampered["source_snapshot"]["stack_ridge_oos_sharpe"] = -0.1
    evidence_path.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(ModelRiskHistoryError, match="snapshot content or verdict"):
        _refresh_fixture(fixture)
