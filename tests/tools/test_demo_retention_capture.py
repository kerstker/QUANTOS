"""Capture admission failures cannot publish success or repeat existing work."""

import hashlib

import pytest

from tools import demo_retention_capture as worker
from tools import demo_retention_reference_review as reference
from tools import demo_namespace_checkpoint as checkpoint
from tests.tools.test_demo_retention_reference_review import namespace as namespace


def inputs(namespace, monkeypatch):
    root, plan, digest, _ = namespace
    monkeypatch.setattr(worker, "EXECUTOR", root)
    review = plan.parent / "review.json"
    review.write_bytes(checkpoint.canonical(reference.review(plan, expected_sha256=digest)))
    return dict(
        plan=plan,
        review=review,
        source_commit="c" * 40,
        expected_plan_sha256=digest,
        expected_review_sha256=hashlib.sha256(review.read_bytes()).hexdigest(),
    )


def test_plan_digest_failure_creates_no_workspace(namespace, monkeypatch, tmp_path):
    args = inputs(namespace, monkeypatch)
    args["expected_plan_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="PLAN_DIGEST"):
        worker.prepare(tmp_path / "work", **args)
    assert not (tmp_path / "work").exists()


def test_capture_failure_preserves_inputs_without_success(namespace, monkeypatch, tmp_path):
    args = inputs(namespace, monkeypatch)

    def fail(*a, **kw):
        assert kw["read_only_source"] is True
        raise ValueError("CAPTURE_FAILURE")

    monkeypatch.setattr(worker.checkpoint, "create_checkpoint", fail)
    with pytest.raises(ValueError, match="CAPTURE_FAILURE"):
        worker.prepare(tmp_path / "work", **args)
    assert (tmp_path / "work/plan.json").exists()
    assert not (tmp_path / "work/inputs/verification.json").exists()
    with pytest.raises(FileExistsError):
        worker.prepare(tmp_path / "work", **args)


def test_root_capture_rejected_before_filesystem(monkeypatch, tmp_path):
    monkeypatch.setattr(worker.os, "getuid", lambda: 0)
    with pytest.raises(ValueError, match="ORIGINAL_EXECUTOR_IDENTITY_REQUIRED"):
        worker.prepare(
            tmp_path / "work",
            plan=None,
            review=None,
            source_commit=None,
            expected_plan_sha256=None,
            expected_review_sha256=None,
        )
