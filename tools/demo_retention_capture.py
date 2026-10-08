"""Executor-owned read-only capture and isolated rehearsal for a fresh review.

No service control, authorization issuance or production archive operations.
The root coordinator must supply inputs produced in its current paused window.
"""

import hashlib
import os

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_maintenance_rehearsal as rehearsal
from tools import demo_namespace_checkpoint as checkpoint
from tools import demo_retention_evidence as evidence
from tools import demo_retention_transaction as retention
from tools.demo_executor_archive_read import EXECUTOR


def prepare(workspace, *, plan, review, source_commit, expected_plan_sha256, expected_review_sha256):
    retention.require(os.getuid() == os.geteuid() != 0, "ORIGINAL_EXECUTOR_IDENTITY_REQUIRED")
    retention.require(not any(p.is_symlink() for p in (EXECUTOR, *EXECUTOR.parents)), "SYMLINK_PARENT")
    retention.directory(EXECUTOR)
    workspace = retention.isolated(workspace)
    retention.directory(workspace.parent)
    plan_raw = retention.read(retention.isolated(plan))
    review_raw = retention.read(retention.isolated(review))
    retention.require(hashlib.sha256(plan_raw).hexdigest() == expected_plan_sha256, "PLAN_DIGEST")
    retention.require(hashlib.sha256(review_raw).hexdigest() == expected_review_sha256, "REVIEW_DIGEST")
    retention.require(decode(review_raw).get("plan_sha256") == expected_plan_sha256, "REVIEW_PLAN_BINDING")
    workspace.mkdir(mode=0o700)
    retention.sync(workspace.parent)
    retention.write_new(workspace / "plan.json", plan_raw)
    retention.write_new(workspace / "review.json", review_raw)
    capture = workspace / "capture.zip"
    captured = checkpoint.create_checkpoint(
        EXECUTOR,
        capture,
        namespace="executor",
        source_commit=source_commit,
        passphrase=None,
        capture_only=True,
        read_only_source=True,
    )
    job = workspace / "rehearsal"
    rehearsal.prepare(
        job,
        capture=capture,
        plan=workspace / "plan.json",
        review=workspace / "review.json",
        expected_capture_sha256=captured["sha256"],
        expected_review_sha256=expected_review_sha256,
    )
    result = rehearsal.resume(job)
    retention.require(result["status"] == "REHEARSAL_COMPLETE", "REHEARSAL_NOT_COMPLETE")
    exported = evidence.export_verified(job, workspace / "inputs")
    return exported | {"selected_files": result["selected_files"]}
