"""Real synthetic planning -> reference scan -> capture -> rehearsal -> export."""

import hashlib
import json
import os

from tools import demo_retention_plan as planner
from tools import demo_retention_reference_review as reference
from tools import demo_namespace_checkpoint as checkpoint
from tools import demo_retention_capture as capture_worker
from tests.tools.test_demo_namespace_checkpoint import executor, COMMIT


def test_fresh_inputs_flow_through_actual_backup_rehearsal(tmp_path, monkeypatch):
    uid = os.geteuid()
    previous = os.umask(0o077)
    try:
        root, _ = executor(tmp_path, monkeypatch)
        monkeypatch.setattr(os, "geteuid", lambda: uid)
        monkeypatch.setattr(planner, "ROOT", root)
        monkeypatch.setattr(planner, "SCOPES", tuple((name, 1) for name, _ in planner.SCOPES))
        monkeypatch.setattr(reference, "EXECUTOR", root)
        directories, databases, files = reference.NAMESPACES["executor"][1:]
        monkeypatch.setattr(reference, "NAMESPACES", {"executor": (root, directories, databases, files)})
        monkeypatch.setattr(reference, "_operator", lambda: None)
        for name, _ in planner.SCOPES:
            area = root / name
            area.mkdir(parents=True, exist_ok=True)
            for at in (1, 2, 3):
                raw = json.dumps({"observed_at_ms": at}).encode()
                digest = hashlib.sha256(raw).hexdigest()
                (area / (digest + ".json")).write_bytes(raw)
            (area / "latest.json").write_text(json.dumps({"sha256": digest}))
        before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
        plan = tmp_path / "fresh-plan.json"
        plan.write_bytes(checkpoint.canonical(planner.build()))
        review = tmp_path / "fresh-review.json"
        report = reference.review(plan, expected_sha256=hashlib.sha256(plan.read_bytes()).hexdigest())
        assert report["candidate_files"] == 8
        review.write_bytes(checkpoint.canonical(report))
        monkeypatch.setattr(capture_worker, "EXECUTOR", root)
        result = capture_worker.prepare(
            tmp_path / "capture-work",
            plan=plan,
            review=review,
            source_commit=COMMIT,
            expected_plan_sha256=report["plan_sha256"],
            expected_review_sha256=hashlib.sha256(review.read_bytes()).hexdigest(),
        )
        assert result["status"] == "VERIFIED_INPUTS_EXPORTED"
        assert result["selected_files"] == 8
        assert result["inputs"]["plan.json"] == report["plan_sha256"]
        assert result["authorization_issued"] is False
        assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    finally:
        os.umask(previous)
