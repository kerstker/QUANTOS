from __future__ import annotations

import copy
from pathlib import Path

import pytest

from services.governance.vulnerability_evidence import (
    ActionsAuditEvidenceError,
    build_actions_audit_evidence,
    verify_actions_audit_evidence,
)


CHECKOUT = "9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"
SETUP = "a26af69be951a213d495a4c3e4e4022e16d87065"


def _write_workflow(root: Path) -> None:
    workflow = root / ".github/workflows/validate.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n"
        "  validate:\n"
        "    steps:\n"
        f"      - uses: actions/checkout@{CHECKOUT}\n"
        f"      - uses: actions/setup-python@{SETUP}\n"
        f"      - uses: actions/checkout@{CHECKOUT}\n",
        encoding="utf-8",
    )


def test_actions_osv_evidence_is_reproducible_and_zero_known(tmp_path) -> None:
    _write_workflow(tmp_path)
    responses = {CHECKOUT: {}, SETUP: {}}

    evidence = build_actions_audit_evidence(
        tmp_path, responses=responses, observed_at=123
    )

    assert evidence["verdict"] == "PASS"
    assert evidence["advisory_source"] == "OSV_API_COMMIT_QUERY"
    assert evidence["unique_action_count"] == 2
    assert evidence["action_usage_count"] == 3
    assert evidence["vulnerability_count"] == 0
    assert verify_actions_audit_evidence(evidence, tmp_path) == {
        "action_usage_count": 3,
        "observed_at": 123,
        "unique_action_count": 2,
        "vulnerability_count": 0,
        "verdict": "PASS",
    }
    assert evidence == build_actions_audit_evidence(
        tmp_path, responses=responses, observed_at=123
    )


def test_actions_osv_evidence_preserves_findings_and_fails_verdict(tmp_path) -> None:
    _write_workflow(tmp_path)
    evidence = build_actions_audit_evidence(
        tmp_path,
        responses={CHECKOUT: {"vulns": [{"id": "GHSA-demo"}]}, SETUP: {}},
        observed_at=123,
    )

    assert evidence["verdict"] == "FAIL"
    assert evidence["vulnerable_action_count"] == 1
    assert evidence["vulnerability_count"] == 1
    assert verify_actions_audit_evidence(evidence, tmp_path)["verdict"] == "FAIL"


def test_actions_osv_evidence_rejects_response_and_workflow_tampering(tmp_path) -> None:
    _write_workflow(tmp_path)
    evidence = build_actions_audit_evidence(
        tmp_path, responses={CHECKOUT: {}, SETUP: {}}, observed_at=123
    )
    tampered = copy.deepcopy(evidence)
    tampered["actions"][0]["query_response"] = {
        "vulns": [{"id": "GHSA-hidden"}]
    }
    with pytest.raises(ActionsAuditEvidenceError, match="response digest"):
        verify_actions_audit_evidence(tampered, tmp_path)

    workflow = tmp_path / ".github/workflows/validate.yml"
    workflow.write_text(workflow.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    with pytest.raises(ActionsAuditEvidenceError, match="workflow digest"):
        verify_actions_audit_evidence(evidence, tmp_path)
