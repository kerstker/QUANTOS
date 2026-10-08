"""Read-only preparation has no archive grant or trading capabilities."""

import json
import os

import pytest

from tools import demo_maintenance_prepare_worker as worker


def test_plan_writes_one_private_bound_input(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "admit", lambda *args: tmp_path)
    plan = {"schema": "fixture", "directories": {"fixture": {"candidates": [{"bytes": 7}]}}}
    monkeypatch.setattr(worker.planner, "build", lambda: plan)
    result = worker.perform("PLAN", "a" * 64, "b" * 40)
    assert json.loads((tmp_path / "plan.json").read_bytes()) == plan
    assert result["candidate_files"] == 1
    assert result["candidate_bytes"] == 7
    assert (tmp_path / "plan.json").stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        worker.perform("PLAN", "a" * 64, "b" * 40)


def test_capture_uses_only_fixed_job_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "admit", lambda *args: tmp_path)
    seen = []

    def capture(path, **kwargs):
        seen.append((path, kwargs))
        return {"status": "VERIFIED_INPUTS_EXPORTED"}

    monkeypatch.setattr(worker.capture, "prepare", capture)
    worker.perform("CAPTURE", "a" * 64, "b" * 40, "c" * 64, "d" * 64)
    path, kwargs = seen[0]
    assert path == tmp_path / "capture-work"
    assert kwargs["plan"] == tmp_path / "plan.json"
    assert kwargs["review"] == tmp_path / "review.json"
    assert kwargs["expected_review_sha256"] == "d" * 64


def test_cli_never_echoes_private_failure(monkeypatch, capsys):
    def fail(*args):
        raise ValueError("private fixture contents")

    monkeypatch.setattr(worker, "perform", fail)
    assert worker.main(["--action", "PLAN", "--operation", "a" * 64, "--source-commit", "b" * 40]) == 78
    raw = capsys.readouterr().out
    assert "private" not in raw
    assert json.loads(raw)["summary"]["status"] == "PREPARATION_FAILED"


@pytest.mark.parametrize("operation", ["../other", "a" * 63, "A" * 64])
def test_workspace_cannot_be_selected_by_arbitrary_path(operation):
    with pytest.raises(ValueError):
        worker.workspace(operation)


def test_root_cannot_run_preparation_worker(monkeypatch):
    monkeypatch.setattr(os, "getresuid", lambda: (0, 0, 0))
    with pytest.raises(ValueError, match="PREPARATION_EXECUTOR"):
        worker.admit("a" * 64, "b" * 40)
