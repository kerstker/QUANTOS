"""Export evidence only from a completed, reverified, byte-bound rehearsal."""
import hashlib
import json

import pytest

from tools import demo_maintenance_rehearsal as rehearsal
from tools import demo_retention_evidence as evidence
from tests.tools.test_demo_maintenance_rehearsal import job as job


def test_incomplete_rehearsal_is_not_resumed_by_export(job, tmp_path):
    workspace, _, _ = job
    target = tmp_path / "inputs"
    with pytest.raises(ValueError, match="REHEARSAL_NOT_COMPLETE"):
        evidence.export_verified(workspace, target)
    assert not target.exists()
    assert not (workspace / "restored").exists()


def test_export_matches_actual_capture_manifest_and_review(job, tmp_path):
    workspace, _, _ = job
    rehearsal.resume(workspace)
    target = tmp_path / "inputs"
    result = evidence.export_verified(workspace, target)
    assert result["status"] == "VERIFIED_INPUTS_EXPORTED"
    assert set(result["inputs"]) == {"plan.json", "review.json", "backup-manifest.json", "capture.zip", "verification.json"}
    for name, digest in result["inputs"].items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == digest
        assert (target / name).stat().st_mode & 0o077 == 0
    proof = json.loads((target / "verification.json").read_bytes())
    assert proof["status"] == "REHEARSAL_COMPLETE"
    assert proof["capture_sha256"] == result["inputs"]["capture.zip"]
    assert proof["manifest_sha256"] == result["inputs"]["backup-manifest.json"]
    assert result["authorization_issued"] is False


def test_changed_restored_record_cannot_export_success(job, tmp_path):
    workspace, _, _ = job
    rehearsal.resume(workspace)
    next((workspace / "restored/observation-views").glob("*.json")).write_bytes(b"changed")
    target = tmp_path / "inputs"
    with pytest.raises(ValueError):
        evidence.export_verified(workspace, target)
    assert not target.exists()


def test_change_during_export_leaves_no_completion_marker(job, tmp_path, monkeypatch):
    workspace, _, _ = job
    rehearsal.resume(workspace)
    original = evidence.checkpoint._copy_private_capture
    def changed(*args):
        original(*args)
        (workspace / "plan.json").write_bytes(b"changed")
    monkeypatch.setattr(evidence.checkpoint, "_copy_private_capture", changed)
    target = tmp_path / "inputs"
    with pytest.raises(ValueError):
        evidence.export_verified(workspace, target)
    assert not (target / "verification.json").exists()


def test_existing_output_is_never_overwritten(job, tmp_path):
    workspace, _, _ = job
    rehearsal.resume(workspace)
    target = tmp_path / "inputs"
    target.mkdir(mode=0o700)
    (target / "operator-note").write_text("preserve")
    with pytest.raises(FileExistsError):
        evidence.export_verified(workspace, target)
    assert (target / "operator-note").read_text() == "preserve"
