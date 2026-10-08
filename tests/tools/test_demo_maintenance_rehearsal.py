"""A restartable rehearsal uses real backup and retention implementations."""

import hashlib
import json
import os

import pytest

from tools import demo_namespace_checkpoint as checkpoint
from tools import demo_retention_transaction as retention
from tools import demo_maintenance_rehearsal as workflow
from tests.tools.test_demo_namespace_checkpoint import executor, COMMIT


@pytest.fixture
def job(tmp_path, monkeypatch):
    previous = os.umask(0o077)
    uid = os.geteuid()
    try:
        source, _ = executor(tmp_path, monkeypatch)
        monkeypatch.setattr(os, "geteuid", lambda: uid)
        area = source / "observation-views"
        area.mkdir(mode=0o700)
        rows = []
        for i in range(4):
            raw = json.dumps({"observed_at_ms": i + 1}).encode()
            digest = hashlib.sha256(raw).hexdigest()
            (area / (digest + ".json")).write_bytes(raw)
            rows.append({"sha256": digest, "bytes": len(raw)})
        (area / "latest.json").write_text(json.dumps({"sha256": rows[-1]["sha256"]}))
        plan = tmp_path / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "schema": "DEMO_RETENTION_REVIEW_PLAN_V1",
                    "apply_available": False,
                    "directories": {
                        s: {"candidates": rows[:3] if s == "observation-views" else []} for s in retention.SCOPES
                    },
                }
            )
        )
        review = tmp_path / "review.json"
        review.write_text(
            json.dumps(
                {
                    "schema": "DEMO_RETENTION_REFERENCE_REVIEW_V2",
                    "status": "SCOPED_REVIEW_ONLY",
                    "plan_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
                    "candidate_files": 3,
                    "pinned_files": 1,
                    "pinned_candidates": ["observation-views/" + rows[0]["sha256"] + ".json"],
                    "candidate_reference_closure_checked": True,
                    "all_references_proven": False,
                    "apply_available": False,
                    "production_changes": False,
                }
            )
        )
        backup = tmp_path / "capture.zip"
        captured = checkpoint.create_checkpoint(
            source, backup, namespace="executor", source_commit=COMMIT, passphrase=None, capture_only=True
        )
        workspace = tmp_path / "rehearsal"
        workflow.prepare(
            workspace,
            capture=backup,
            plan=plan,
            review=review,
            expected_capture_sha256=captured["sha256"],
            expected_review_sha256=hashlib.sha256(review.read_bytes()).hexdigest(),
        )
        yield workspace, source, captured
    finally:
        os.umask(previous)


def test_complete_rehearsal_restores_all_bytes_and_pending_state(job):
    workspace, source, captured = job
    before = {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    result = workflow.resume(workspace)
    assert result["status"] == "REHEARSAL_COMPLETE" and result["selected_files"] == 2
    assert not result["production_changes"] and not result["all_references_proven"]
    assert checkpoint.verify_state(workspace / "restored", "executor") == captured["state"]
    assert before == {p.relative_to(source): p.read_bytes() for p in source.rglob("*") if p.is_file()}
    assert workflow.resume(workspace) == result


def test_failure_after_a_move_rolls_back_and_never_reports_complete(job, monkeypatch):
    workspace, _, _ = job
    original = retention.move
    calls = 0

    def fail(source, target):
        nonlocal calls
        original(source, target)
        calls += 1
        if calls == 1:
            raise RuntimeError("fixture interruption")

    monkeypatch.setattr(retention, "move", fail)
    result = workflow.resume(workspace)
    assert result["status"] == "REHEARSAL_FAILED_ROLLED_BACK"
    assert workflow.resume(workspace) == result


def test_abrupt_exit_after_move_resumes_existing_transaction(job):
    import subprocess
    import sys

    workspace, _, _ = job
    script = """import os,sys
from tools import demo_maintenance_rehearsal as workflow, demo_retention_transaction as retention
original=retention.move
def stop(source,target):
    original(source,target)
    os._exit(9)
retention.move=stop
workflow.resume(sys.argv[1])
"""
    child = subprocess.run([sys.executable, "-c", script, str(workspace)], capture_output=True, timeout=30)
    assert child.returncode == 9, child.stderr.decode()
    assert workflow.resume(workspace)["status"] == "REHEARSAL_COMPLETE"


def test_completed_rehearsal_does_not_hide_later_corruption(job):
    workspace, _, _ = job
    workflow.resume(workspace)
    path = next((workspace / "restored/observation-views").glob("*.json"))
    path.write_bytes(b"{}")
    with pytest.raises(ValueError):
        workflow.resume(workspace)


def test_failed_rollback_requires_recovery_and_cannot_turn_into_success(job, monkeypatch):
    workspace, _, _ = job
    original = retention.move

    def interrupted(source, target):
        original(source, target)
        raise RuntimeError("fixture move failure")

    def unavailable(*args, **kwargs):
        raise RuntimeError("fixture rollback failure")

    with monkeypatch.context() as failure:
        failure.setattr(retention, "move", interrupted)
        failure.setattr(retention, "rollback", unavailable)
        assert workflow.resume(workspace)["status"] == "RECOVERY_REQUIRED"
    assert workflow.resume(workspace)["status"] == "REHEARSAL_FAILED_ROLLED_BACK"


def test_modified_review_is_rejected_before_restoring_or_moving(job):
    workspace, _, _ = job
    (workspace / "review.json").write_text("{}")
    with pytest.raises(ValueError, match="REQUEST_INPUT_CHANGED"):
        workflow.resume(workspace)
    assert not (workspace / "restored").exists()


def test_completed_report_requires_retained_transaction_evidence(job):
    workspace, _, _ = job
    workflow.resume(workspace)
    (workspace / "retention/manifest.json").write_text("{}")
    with pytest.raises(ValueError):
        workflow.resume(workspace)


def test_cli_resume_emits_only_bounded_summary(job, capsys):
    workspace, _, _ = job
    assert workflow.main(['resume', '--workspace', str(workspace)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'REHEARSAL_COMPLETE'
    assert result['selected_files'] == 2 and result['apply_available'] is False
