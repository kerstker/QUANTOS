from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from services.governance.vulnerability_evidence import (
    NpmAuditEvidenceError,
    build_npm_audit_evidence,
    verify_npm_audit_evidence,
)


def _write_manifests(root: Path) -> None:
    console = root / "apps/research-console"
    console.mkdir(parents=True)
    (console / "package.json").write_text(
        json.dumps({"name": "fixture", "devDependencies": {"vitest": "4.1.10"}}),
        encoding="utf-8",
    )
    (console / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "fixture",
                "lockfileVersion": 3,
                "packages": {
                    "": {"name": "fixture"},
                    "node_modules/vitest": {
                        "version": "4.1.10",
                        "integrity": "sha512-Zml4dHVyZQ==",
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def _report(*, critical: int = 0) -> dict[str, object]:
    vulnerabilities = (
        {}
        if critical == 0
        else {
            "demo": {
                "name": "demo",
                "severity": "critical",
                "isDirect": True,
                "via": [],
                "effects": [],
                "range": "<2",
                "nodes": ["node_modules/demo"],
                "fixAvailable": True,
            }
        }
    )
    return {
        "auditReportVersion": 2,
        "vulnerabilities": vulnerabilities,
        "metadata": {
            "vulnerabilities": {
                "info": 0,
                "low": 0,
                "moderate": 0,
                "high": 0,
                "critical": critical,
                "total": critical,
            },
            "dependencies": {
                "prod": 1,
                "dev": 1,
                "optional": 0,
                "peer": 0,
                "peerOptional": 0,
                "total": 2,
            },
        },
    }


def test_external_npm_audit_evidence_is_reproducible_and_zero_known(tmp_path) -> None:
    _write_manifests(tmp_path)

    evidence = build_npm_audit_evidence(
        tmp_path,
        report=_report(),
        npm_version="11.8.0",
        observed_at=123,
    )

    assert evidence["verdict"] == "PASS"
    assert evidence["advisory_source"] == "NPM_REGISTRY_AUDIT_API"
    assert evidence["network_query_executed"] is True
    assert evidence["vulnerability_counts"]["total"] == 0
    assert verify_npm_audit_evidence(evidence, tmp_path) == {
        "observed_at": 123,
        "scanner_version": "11.8.0",
        "vulnerability_count": 0,
        "verdict": "PASS",
    }
    assert evidence == build_npm_audit_evidence(
        tmp_path,
        report=_report(),
        npm_version="11.8.0",
        observed_at=123,
    )


def test_npm_audit_evidence_preserves_findings_and_fails_verdict(tmp_path) -> None:
    _write_manifests(tmp_path)

    evidence = build_npm_audit_evidence(
        tmp_path,
        report=_report(critical=1),
        npm_version="11.8.0",
        observed_at=123,
    )

    assert evidence["verdict"] == "FAIL"
    assert evidence["vulnerability_counts"]["critical"] == 1
    assert verify_npm_audit_evidence(evidence, tmp_path)["vulnerability_count"] == 1


def test_npm_audit_evidence_rejects_report_and_manifest_tampering(tmp_path) -> None:
    _write_manifests(tmp_path)
    evidence = build_npm_audit_evidence(
        tmp_path,
        report=_report(),
        npm_version="11.8.0",
        observed_at=123,
    )
    tampered = copy.deepcopy(evidence)
    tampered["audit_report"]["metadata"]["vulnerabilities"]["total"] = 1
    with pytest.raises(NpmAuditEvidenceError, match="report digest"):
        verify_npm_audit_evidence(tampered, tmp_path)

    package = tmp_path / "apps/research-console/package.json"
    package.write_text(package.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(NpmAuditEvidenceError, match="package manifest digest"):
        verify_npm_audit_evidence(evidence, tmp_path)
