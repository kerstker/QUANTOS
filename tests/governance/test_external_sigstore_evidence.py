from __future__ import annotations

import ast
import json
from pathlib import Path
import shutil

import pytest

from services.governance.external_sigstore_evidence import (
    ExternalSigstoreEvidenceError,
    verify_external_sigstore_evidence,
)


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "evidence" / "external_sigstore" / "2026-07-30"
TARGET = ROOT / "services" / "governance" / "external_sigstore_evidence.py"


def test_retained_external_sigstore_evidence_verifies_offline() -> None:
    report = verify_external_sigstore_evidence(EVIDENCE)
    assert report["status"] == "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE_CROSS_HOST"
    assert report["source_commit"] == "6042317f913569a912a34fb54d7879cbbbcf4d0c"
    assert report["artifact_sha256"] == (
        "4f9562489953a7a54da40120c789365e9a3079ef3448598f327cf0c49bfc012a"
    )
    assert report["workflow_run_id"] == 30540314233
    assert report["artifact_id"] == 8758408888
    assert report["cross_host_builders"] == ["ubuntu-22.04", "ubuntu-24.04"]
    assert report["transparency_integrated_at_ms"] > 0
    assert report["certificate_sha256"]
    assert report["signed_certificate_timestamp_verified"] is True
    assert report["transparency_log_verified"] is True
    assert report["artifact_bytes_retained_in_repository"] is False
    assert report["external_artifact_retention_expires_at"] == (
        "2026-10-28T11:53:32Z"
    )
    assert report["m4_limitations"] == [
        "EXTERNAL_RECEIPT_AUTHORITY_NOT_PROVIDED",
        "INDEPENDENT_ADMISSION_BOUNDARY_NOT_PROVIDED",
        "REVOCATION_OPERATIONS_NOT_PROVIDED",
        "EXTERNAL_CHAIN_HEAD_SEAL_NOT_PROVIDED",
        "WORM_RETENTION_NOT_PROVIDED",
    ]


def test_external_sigstore_evidence_rejects_bundle_or_receipt_tamper(tmp_path) -> None:
    copied = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, copied)
    bundle_path = copied / "quantos-source.sigstore.json"
    bundle_path.write_bytes(bundle_path.read_bytes() + b"\n")
    with pytest.raises(ExternalSigstoreEvidenceError, match="bundle digest"):
        verify_external_sigstore_evidence(copied)

    shutil.rmtree(copied)
    shutil.copytree(EVIDENCE, copied)
    receipt_path = copied / "external-evidence-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["source_commit"] = "0" * 40
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ExternalSigstoreEvidenceError, match="source commit"):
        verify_external_sigstore_evidence(copied)


def test_external_sigstore_verifier_has_no_network_signing_or_execution_surface() -> None:
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports |= {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not imports & {
        "httpx",
        "requests",
        "socket",
        "subprocess",
        "urllib",
        "live",
        "trading",
    }
    lowered = source.lower()
    for forbidden in (
        "signing_key",
        "private_key",
        "submit_order",
        "place_order",
        "deploy(",
    ):
        assert forbidden not in lowered
