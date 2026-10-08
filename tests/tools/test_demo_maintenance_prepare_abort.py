"""Incomplete PREPARE may be abandoned only with all source candidates intact."""

import json

import pytest

from tools import demo_maintenance_staging as staging
from tests.tools.test_demo_maintenance_staging import prepared as prepared, job as job


def test_unmoved_candidates_allow_readonly_abort_proof(prepared):
    root, doc, _, _, _ = prepared
    staged = staging.stage(root, doc)
    result = staging.verify_unmoved(root, doc, approved_sha256=staged["approval_sha256"])
    assert result["status"] == "SOURCE_CANDIDATES_INTACT"
    assert result["candidate_files"] == 3


def test_missing_source_candidate_prevents_abort(prepared):
    root, doc, _, _, _ = prepared
    staged = staging.stage(root, doc)
    plan = json.loads((staging.reader.ARCHIVES / "requests" / staged["archive_id"] / "plan.json").read_bytes())
    row = plan["directories"]["observation-views"]["candidates"][0]
    (staging.reader.EXECUTOR / "observation-views" / (row["sha256"] + ".json")).unlink()
    with pytest.raises((ValueError, OSError)):
        staging.verify_unmoved(root, doc, approved_sha256=staged["approval_sha256"])


def test_wrong_approval_cannot_abandon_prepare(prepared):
    root, doc, _, _, _ = prepared
    staging.stage(root, doc)
    with pytest.raises(ValueError, match="MAINTENANCE_APPROVAL_BINDING"):
        staging.verify_unmoved(root, doc, approved_sha256="f" * 64)
