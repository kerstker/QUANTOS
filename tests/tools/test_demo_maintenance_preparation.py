"""Preparation is source-read-only and its results carry no archive permission."""

import json
import os
import hashlib

import pytest

from tools import demo_maintenance_preparation as preparation
from tools import demo_maintenance_prepare_worker as worker


def test_sandbox_writes_only_this_operation_workspace():
    command = preparation.command(
        source_commit="a" * 40,
        uid=1001,
        action="CAPTURE",
        operation="b" * 64,
        unit="quantos-demo-retention-" + "c" * 32 + ".service",
        digest="d" * 64,
        plan_sha256="e" * 64,
        review_sha256="f" * 64,
    )
    properties = dict(v.removeprefix("--property=").split("=", 1) for v in command if v.startswith("--property="))
    assert properties["ReadWritePaths"] == str(worker.workspace("b" * 64))
    assert "/var/lib/quantos-demo-executor" in properties["ReadOnlyPaths"].split()
    assert "demo_retention_worker" not in " ".join(command)
    assert "tools.demo_maintenance_prepare_worker" in command
    assert properties["PrivateNetwork"] == "yes"
    assert properties["TemporaryFileSystem"] == "/etc/quantos:ro,mode=0755"


@pytest.mark.parametrize("change", [{"production_changes": True}, {"candidate_files": -1}, {"extra": "private"}])
def test_plan_result_rejects_unsafe_or_unbounded_fields(change):
    summary = {
        "status": "PLAN_CREATED",
        "plan_sha256": "a" * 64,
        "candidate_files": 2,
        "candidate_bytes": 100,
        "production_changes": False,
    } | change
    raw = json.dumps({"schema": worker.SCHEMA, "action": "PLAN", "ok": True, "summary": summary}).encode()
    with pytest.raises(ValueError):
        preparation.packet(raw, "PLAN")


def test_incomplete_capture_cannot_become_success():
    raw = json.dumps(
        {
            "schema": worker.SCHEMA,
            "action": "CAPTURE",
            "ok": True,
            "summary": {"status": "PREPARATION_FAILED", "production_changes": False},
        }
    ).encode()
    with pytest.raises(ValueError):
        preparation.packet(raw, "CAPTURE")


def test_root_required_before_reading_window(monkeypatch, tmp_path):
    monkeypatch.setattr(preparation.os, "getresuid", lambda: (1000, 1000, 1000))
    with pytest.raises(ValueError, match="OPERATOR_ROOT_REQUIRED"):
        preparation.run(tmp_path, {}, source_commit="a" * 40)


@pytest.fixture
def staged(tmp_path, monkeypatch):
    root = tmp_path / "window"
    root.mkdir(mode=0o700)
    area = tmp_path / "evidence"
    uid = os.geteuid()
    registry = {"executor_uid": uid, "issuer_uid": uid + 1}
    monkeypatch.setattr(preparation.os, "getresuid", lambda: (0, 0, 0))
    monkeypatch.setattr(preparation.os, "chown", lambda *args: None)
    monkeypatch.setattr(preparation.handoff.grants, "verify_source", lambda *args: None)
    monkeypatch.setattr(preparation, "_read_root_owned_json", lambda *args: b"{}")
    monkeypatch.setattr(preparation, "validate_registry", lambda *args: registry)
    monkeypatch.setattr(preparation, "registry_sha256", lambda *args: "e" * 64)
    monkeypatch.setattr(preparation.processes, "observe", lambda **kw: {"status": "NO_SCOPED_PROCESS_OBSERVED"})
    monkeypatch.setattr(worker, "workspace", lambda *args: area)
    monkeypatch.setattr(preparation.handoff, "show", lambda *args: {"LoadState": "not-found"})
    monkeypatch.setattr(preparation.handoff, "drained", lambda *args: True)
    monkeypatch.setattr(preparation.reference, "review", lambda path, **kw: {"plan_sha256": kw["expected_sha256"]})
    doc = {"phase": "PAUSED", "inhibitor_sha256": "a" * 64}
    actions = []

    def transport(fd, *, argv, parse, **kw):
        action = argv[argv.index("--action") + 1]
        actions.append(action)
        journal = preparation.inspect(root, doc)
        assert journal["jobs"][-1]["result"] is None  # Intent precedes process launch.
        if action == "PLAN":
            preparation.write_new(area / "plan.json", b"{}")
            summary = {
                "status": "PLAN_CREATED",
                "plan_sha256": hashlib.sha256(b"{}").hexdigest(),
                "candidate_files": 1,
                "candidate_bytes": 2,
                "production_changes": False,
            }
        else:
            summary = {
                "status": "VERIFIED_INPUTS_EXPORTED",
                "inputs": {
                    key: hashlib.sha256((area / key).read_bytes()).hexdigest()
                    if key in {"plan.json", "review.json"}
                    else "f" * 64
                    for key in preparation.handoff.grants.INPUTS
                },
                "selected_files": 1,
                "authorization_issued": False,
                "production_changes": False,
                "all_references_proven": False,
            }
        result = parse(json.dumps({"schema": worker.SCHEMA, "action": action, "ok": True, "summary": summary}).encode())
        return {
            "status": "WORKER_COMPLETED",
            "worker_drained": True,
            "result": result,
            "safe_to_restore_services": False,
        }

    monkeypatch.setattr(preparation.handoff, "_transport", transport)
    return root, doc, actions


def test_preparation_binds_same_window_and_does_not_repeat(staged):
    root, doc, actions = staged
    result = preparation.run(root, doc, source_commit="a" * 40)
    assert actions == ["PLAN", "CAPTURE"]
    assert result["status"] == "WINDOW_EVIDENCE_PREPARED" and result["authorization_issued"] is False
    assert preparation.inspect(root, doc)["complete"] is True
    with pytest.raises(ValueError, match="PREPARATION_ALREADY_STARTED"):
        preparation.run(root, doc, source_commit="a" * 40)
    assert actions == ["PLAN", "CAPTURE"]


def test_review_failure_preserves_partial_work_and_can_restore_after_drain(staged, monkeypatch):
    root, doc, actions = staged

    def fail(*args, **kw):
        raise ValueError("fixture review failure")

    monkeypatch.setattr(preparation.reference, "review", fail)
    with pytest.raises(ValueError):
        preparation.run(root, doc, source_commit="a" * 40)
    assert actions == ["PLAN"]
    assert preparation.inspect(root, doc)["complete"] is False
    preparation.restore_guard(root, doc)
    monkeypatch.setattr(preparation.handoff, "drained", lambda *args: False)
    with pytest.raises(ValueError, match="PREPARATION_WORKER_STILL_RUNNING"):
        preparation.restore_guard(root, doc)


def test_process_scan_unknown_prevents_creating_evidence(staged, monkeypatch):
    root, doc, actions = staged
    monkeypatch.setattr(preparation.processes, "observe", lambda **kw: {"status": "PROCESS_SCAN_INCOMPLETE"})
    with pytest.raises(ValueError, match="PREPARATION_PROCESSES_UNKNOWN"):
        preparation.run(root, doc, source_commit="a" * 40)
    assert not actions and not (root / "preparation.json").exists()
