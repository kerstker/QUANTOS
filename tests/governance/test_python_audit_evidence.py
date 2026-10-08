from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from services.governance.vulnerability_evidence import (
    PythonAuditEvidenceError,
    build_python_audit_evidence,
    verify_python_audit_evidence,
)


def _write_manifests(root: Path) -> None:
    (root / "requirements.txt").write_text("fastapi==0.139.0\n", encoding="utf-8")
    (root / "requirements-dev.txt").write_text(
        "-r requirements.txt\npip-audit==2.10.1\n", encoding="utf-8"
    )
    (root / "pyproject.toml").write_text(
        '[project]\ndependencies = ["fastapi==0.139.0"]\n', encoding="utf-8"
    )


def _report(*, vulnerable: bool = False) -> dict[str, object]:
    vulns = (
        []
        if not vulnerable
        else [
            {
                "id": "GHSA-demo",
                "fix_versions": ["2.0.0"],
                "aliases": ["CVE-2099-1"],
                "description": "fixture advisory",
            }
        ]
    )
    return {
        "dependencies": [
            {"name": "fastapi", "version": "0.139.0", "vulns": vulns},
            {"name": "pip-audit", "version": "2.10.1", "vulns": []},
        ],
        "fixes": [],
    }


def test_external_python_audit_evidence_is_reproducible_and_zero_known(tmp_path) -> None:
    _write_manifests(tmp_path)

    evidence = build_python_audit_evidence(
        tmp_path,
        report=_report(),
        scanner_version="2.10.1",
        observed_at=123,
    )

    assert evidence["verdict"] == "PASS"
    assert evidence["advisory_source"] == "PYPI_OSV_PIP_AUDIT"
    assert evidence["dependency_count"] == 2
    assert evidence["vulnerability_count"] == 0
    assert verify_python_audit_evidence(evidence, tmp_path) == {
        "dependency_count": 2,
        "observed_at": 123,
        "scanner_version": "2.10.1",
        "vulnerability_count": 0,
        "verdict": "PASS",
    }
    assert evidence == build_python_audit_evidence(
        tmp_path,
        report=_report(),
        scanner_version="2.10.1",
        observed_at=123,
    )


def test_python_audit_evidence_preserves_findings_and_fails_verdict(tmp_path) -> None:
    _write_manifests(tmp_path)
    evidence = build_python_audit_evidence(
        tmp_path,
        report=_report(vulnerable=True),
        scanner_version="2.10.1",
        observed_at=123,
    )

    assert evidence["verdict"] == "FAIL"
    assert evidence["vulnerable_dependency_count"] == 1
    assert evidence["vulnerability_count"] == 1
    assert verify_python_audit_evidence(evidence, tmp_path)["verdict"] == "FAIL"


def test_python_audit_evidence_rejects_report_and_manifest_tampering(tmp_path) -> None:
    _write_manifests(tmp_path)
    evidence = build_python_audit_evidence(
        tmp_path,
        report=_report(),
        scanner_version="2.10.1",
        observed_at=123,
    )
    tampered = copy.deepcopy(evidence)
    tampered["audit_report"]["dependencies"][0]["version"] = "0.1.0"
    with pytest.raises(PythonAuditEvidenceError, match="report digest"):
        verify_python_audit_evidence(tampered, tmp_path)

    manifest = tmp_path / "requirements-dev.txt"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    with pytest.raises(PythonAuditEvidenceError, match="manifest digest"):
        verify_python_audit_evidence(evidence, tmp_path)
