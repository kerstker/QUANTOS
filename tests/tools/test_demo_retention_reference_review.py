"""Fresh scoped references include database and transitive retained dependencies."""

import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from tools import demo_retention_reference_review as review
from tools import demo_retention_plan as planner


@pytest.fixture
def namespace(tmp_path, monkeypatch):
    previous = os.umask(0o077)
    root = tmp_path / "executor"
    root.mkdir()
    monkeypatch.setattr(planner, "ROOT", root)
    monkeypatch.setattr(review, "EXECUTOR", root)
    monkeypatch.setattr(review, "NAMESPACES", {"executor": (root, ("observation-views",), ("refs.sqlite3",), ())})

    def record(body):
        raw = json.dumps(body).encode()
        sha = hashlib.sha256(raw).hexdigest()
        path = root / "observation-views" / (sha + ".json")
        path.write_bytes(raw)
        return sha

    for name, _ in planner.SCOPES:
        (root / name).mkdir(parents=True, exist_ok=True)
    leaf = record({"observed_at_ms": 1})
    parent = record({"observed_at_ms": 2, "ref": leaf})
    loose = record({"observed_at_ms": 3})
    latest = record({"observed_at_ms": 9_999_999})
    for name, _ in planner.SCOPES:
        area = root / name
        if name != "observation-views":
            raw = json.dumps({"observed_at_ms": 9_999_999}).encode()
            (area / (latest + ".json")).write_bytes(raw)
        (area / "latest.json").write_text(json.dumps({"sha256": latest}))
    with sqlite3.connect(root / "refs.sqlite3") as conn:
        conn.execute("CREATE TABLE refs (value TEXT)")
        conn.execute("INSERT INTO refs VALUES (?)", (parent,))
    monkeypatch.setattr(planner, "SCOPES", tuple((name, 1) for name, _ in planner.SCOPES))
    plan = {
        "schema": "DEMO_RETENTION_REVIEW_PLAN_V1",
        "apply_available": False,
        "directories": {
            name: planner.inspect_directory(root / name, keep_count=1, now_ms=10_000_000) for name, _ in planner.SCOPES
        },
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    # Only privilege check is mocked. All JSON, SQLite copies and digests are real.
    monkeypatch.setattr(review, "_operator", lambda: None)
    yield root, path, digest, (leaf, parent, loose)
    os.umask(previous)


def test_review_pins_database_references_and_candidate_closure(namespace):
    root, plan, digest, (leaf, parent, loose) = namespace
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    result = review.review(plan, expected_sha256=digest)
    assert result["pinned_candidates"] == sorted("observation-views/" + s + ".json" for s in (leaf, parent))
    assert result["direct_pinned_files"] == result["transitive_pinned_files"] == 1
    assert loose not in str(result["pinned_candidates"])
    assert result["all_references_proven"] is result["apply_available"] is False
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_changed_plan_cannot_reuse_digest(namespace):
    _, path, digest, _ = namespace
    path.write_text("{}")
    with pytest.raises(review.ReviewError, match="PLAN_DIGEST"):
        review.review(path, expected_sha256=digest)


def test_candidate_changed_after_plan_rejected(namespace):
    root, path, digest, (_, _, loose) = namespace
    (root / "observation-views" / (loose + ".json")).write_text("{}")
    with pytest.raises(review.ReviewError, match="CANDIDATE_CHANGED"):
        review.review(path, expected_sha256=digest)


def test_missing_location_is_explicit_unknown(namespace):
    root, path, digest, _ = namespace
    (root / "refs.sqlite3").unlink()
    result = review.review(path, expected_sha256=digest)
    assert result["missing_locations"] == ["executor/refs.sqlite3"]
    assert result["all_references_proven"] is False


def test_symlink_plan_parent_rejected(namespace, tmp_path):
    _, path, digest, _ = namespace
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(review.ReviewError, match="SYMLINK_PARENT"):
        review.review(alias / path.name, expected_sha256=digest)


def test_nonroot_review_rejects_before_source_io(monkeypatch):
    monkeypatch.setattr(review.os, "getuid", lambda: 1000)
    with pytest.raises(review.ReviewError, match="OPERATOR_ROOT_REQUIRED"):
        review.review(Path("/does-not-exist"), expected_sha256="a" * 64)
