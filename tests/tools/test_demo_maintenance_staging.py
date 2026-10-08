"""Staging binds actual completed evidence bytes, not a success flag alone."""

import hashlib
import os

import pytest

from tools import demo_maintenance_staging as staging
from tools import demo_maintenance_rehearsal as rehearsal
from tools import demo_retention_evidence as evidence
from tests.tools.test_demo_maintenance_rehearsal import job as job


@pytest.fixture
def prepared(job, tmp_path, monkeypatch):
    workspace, source, _ = job
    rehearsal.resume(workspace)
    area = tmp_path / "area"
    area.mkdir(mode=0o700)
    capture = area / "capture-work"
    capture.mkdir(mode=0o700)
    result = evidence.export_verified(workspace, capture / "inputs") | {"selected_files": 2}
    monkeypatch.setattr(staging.preparation.worker, "workspace", lambda *args: area)
    record = {
        "complete": True,
        "executor_uid": os.geteuid(),
        "registry_sha256": "c" * 64,
        "source_commit": "d" * 40,
        "jobs": [{"action": "CAPTURE", "result": {"status": "WORKER_COMPLETED", "result": {"summary": result}}}],
    }
    monkeypatch.setattr(staging.preparation, "inspect", lambda *args: record)
    monkeypatch.setattr(staging.preparation, "restore_guard", lambda *args: None)
    monkeypatch.setattr(staging, "_identity", lambda record: {"executor_uid": os.geteuid()})
    monkeypatch.setattr(staging, "_archive_parent", lambda: None)
    monkeypatch.setattr(staging.os, "getresuid", lambda: (0, 0, 0))
    monkeypatch.setattr(staging.os, "chown", lambda *args: None)
    monkeypatch.setattr(staging.reader, "EXECUTOR", source)
    monkeypatch.setattr(staging.reader, "ARCHIVES", tmp_path / "archives")
    window = tmp_path / "window"
    window.mkdir(mode=0o700)
    document = {"phase": "PAUSED", "inhibitor_sha256": "a" * 64, "operation_sha256": "b" * 64}
    return window, document, result, capture / "inputs", record


def test_stage_copies_exact_inputs_and_binds_window(prepared):
    window, document, result, source, _ = prepared
    output = staging.stage(window, document)
    target = staging.reader.ARCHIVES / "requests" / document["operation_sha256"]
    assert output["inputs"] == result["inputs"]
    assert output["window_sha256"] == document["inhibitor_sha256"]
    for name, digest in result["inputs"].items():
        assert hashlib.sha256((target / name).read_bytes()).hexdigest() == digest
        assert (target / name).read_bytes() == (source / name).read_bytes()
    assert output["authorization_issued"] is False
    assert staging.load(window, document) == output


def test_changed_capture_cannot_publish_staging_receipt(prepared):
    window, document, _, source, _ = prepared
    (source / "capture.zip").write_bytes(b"changed")
    with pytest.raises(ValueError):
        staging.stage(window, document)
    assert not (window / "staged.json").exists()


def test_failed_preparation_cannot_stage(prepared):
    window, document, _, _, record = prepared
    record["complete"] = False
    with pytest.raises(ValueError, match="PREPARATION_NOT_COMPLETE"):
        staging.stage(window, document)
    assert not staging.reader.ARCHIVES.exists()


def test_changed_staged_input_cannot_reload(prepared):
    window, document, _, _, _ = prepared
    staging.stage(window, document)
    target = staging.reader.ARCHIVES / "requests" / document["operation_sha256"] / "plan.json"
    target.write_bytes(b"{}")
    with pytest.raises(ValueError):
        staging.load(window, document)


def test_completed_stage_is_not_overwritten(prepared):
    window, document, _, _, _ = prepared
    staging.stage(window, document)
    before = (window / "staged.json").read_bytes()
    with pytest.raises(FileExistsError):
        staging.stage(window, document)
    assert (window / "staged.json").read_bytes() == before
