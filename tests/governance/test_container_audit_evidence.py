from __future__ import annotations

import copy
from pathlib import Path

import pytest

from services.governance.vulnerability_evidence import (
    ContainerAuditEvidenceError,
    build_container_audit_evidence,
    verify_container_audit_evidence,
)


BASE_DIGEST = "sha256:" + "a" * 64
BASE_ID = "sha256:" + "b" * 64
RUNTIME_DIGEST = "sha256:" + "c" * 64
BASE_REF = f"public.ecr.aws/docker/library/python@{BASE_DIGEST}"


def _write_dockerfile(root: Path, image: str = BASE_REF) -> None:
    dockerfile = root / "deploy/console.Dockerfile"
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text(
        f"ARG PYTHON_IMAGE={image}\nFROM ${{PYTHON_IMAGE}}\n",
        encoding="utf-8",
    )


def _vulnerability(
    vulnerability_id: str, *, severity: str = "MEDIUM"
) -> dict[str, str]:
    return {
        "VulnerabilityID": vulnerability_id,
        "PkgName": "demo-package",
        "InstalledVersion": "1.0",
        "FixedVersion": "1.1",
        "Status": "fixed",
        "Severity": severity,
    }


def _report(
    artifact: str,
    image_id: str,
    vulnerabilities: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    repo_digest = artifact if "@" in artifact else f"quantos-console@{image_id}"
    return {
        "SchemaVersion": 2,
        "CreatedAt": "1970-01-12T13:45:00Z",
        "ArtifactName": artifact,
        "ArtifactType": "container_image",
        "Metadata": {
            "ImageID": image_id,
            "RepoDigests": [repo_digest],
            "OS": {"Family": "alpine", "Name": "3.24.1"},
        },
        "Results": [
            {
                "Target": f"{artifact} (alpine 3.24.1)",
                "Class": "os-pkgs",
                "Type": "alpine",
                "Vulnerabilities": vulnerabilities,
            }
        ],
    }


def _build(
    root: Path,
    *,
    base_vulnerabilities: list[dict[str, str]] | None = None,
    runtime_vulnerabilities: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    _write_dockerfile(root)
    return build_container_audit_evidence(
        root,
        base_report=_report(BASE_REF, BASE_ID, base_vulnerabilities),
        runtime_report=_report(
            "quantos-console:hardened", RUNTIME_DIGEST, runtime_vulnerabilities
        ),
        observed_at=1_000_000,
        database_updated_at="1970-01-12T12:00:00Z",
        database_next_update="1970-01-13T12:00:00Z",
        database_downloaded_at="1970-01-12T12:30:00Z",
    )


def test_container_audit_is_reproducible_and_accepts_remediated_base_findings(
    tmp_path: Path,
) -> None:
    evidence = _build(
        tmp_path,
        base_vulnerabilities=[_vulnerability("CVE-DEMO-1")],
    )

    assert evidence["verdict"] == "PASS"
    assert evidence["scanner_version"] == "0.72.0"
    assert evidence["base_vulnerability_counts"]["medium"] == 1
    assert evidence["runtime_vulnerability_counts"]["total"] == 0
    assert evidence["remediated_vulnerability_count"] == 1
    assert verify_container_audit_evidence(evidence, tmp_path) == {
        "base_vulnerability_count": 1,
        "observed_at": 1_000_000,
        "runtime_image_digest": RUNTIME_DIGEST,
        "runtime_vulnerability_count": 0,
        "scanner_version": "0.72.0",
        "verdict": "PASS",
    }
    assert evidence == _build(
        tmp_path,
        base_vulnerabilities=[_vulnerability("CVE-DEMO-1")],
    )


def test_container_audit_fails_on_base_high_or_any_runtime_finding(
    tmp_path: Path,
) -> None:
    base_failed = _build(
        tmp_path,
        base_vulnerabilities=[_vulnerability("CVE-HIGH", severity="HIGH")],
    )
    assert base_failed["verdict"] == "FAIL"

    runtime_failed = _build(
        tmp_path,
        runtime_vulnerabilities=[_vulnerability("CVE-LOW", severity="LOW")],
    )
    assert runtime_failed["verdict"] == "FAIL"


def test_container_audit_rejects_report_and_dockerfile_tampering(
    tmp_path: Path,
) -> None:
    evidence = _build(tmp_path)
    tampered = copy.deepcopy(evidence)
    tampered["runtime_report"]["Results"][0]["Vulnerabilities"] = [
        _vulnerability("CVE-HIDDEN")
    ]
    with pytest.raises(ContainerAuditEvidenceError, match="inconsistent"):
        verify_container_audit_evidence(tampered, tmp_path)

    dockerfile = tmp_path / "deploy/console.Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8"
    )
    with pytest.raises(ContainerAuditEvidenceError, match="inconsistent"):
        verify_container_audit_evidence(evidence, tmp_path)


def test_container_audit_rejects_report_for_a_different_base_digest(
    tmp_path: Path,
) -> None:
    _write_dockerfile(tmp_path)
    other_ref = "public.ecr.aws/docker/library/python@sha256:" + "d" * 64
    with pytest.raises(ContainerAuditEvidenceError, match="Dockerfile image digest"):
        build_container_audit_evidence(
            tmp_path,
            base_report=_report(other_ref, BASE_ID),
            runtime_report=_report(
                "quantos-console:hardened", RUNTIME_DIGEST
            ),
            observed_at=1_000_000,
            database_updated_at="1970-01-12T12:00:00Z",
            database_next_update="1970-01-13T12:00:00Z",
            database_downloaded_at="1970-01-12T12:30:00Z",
        )


def test_container_audit_rejects_stale_database_at_scan_time(
    tmp_path: Path,
) -> None:
    _write_dockerfile(tmp_path)
    with pytest.raises(ContainerAuditEvidenceError, match="database timing"):
        build_container_audit_evidence(
            tmp_path,
            base_report=_report(BASE_REF, BASE_ID),
            runtime_report=_report(
                "quantos-console:hardened", RUNTIME_DIGEST
            ),
            observed_at=1_000_000,
            database_updated_at="1970-01-09T00:00:00Z",
            database_next_update="1970-01-10T00:00:00Z",
            database_downloaded_at="1970-01-09T00:30:00Z",
        )
