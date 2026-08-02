from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from services.governance.artifact_admission_external_seal import (
    AUTHORITY,
    LIMITATIONS,
    RETENTION,
    STATUS,
    ArtifactAdmissionExternalSealError,
    artifact_admission_seal_contract_sha256,
    verify_artifact_admission_external_seal,
)


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "sigstore-evidence.yml"
EVIDENCE = (
    ROOT / "evidence" / "external_sigstore" / "2026-07-31-admission"
)


def test_external_chain_head_verifier_fails_closed_without_evidence(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ArtifactAdmissionExternalSealError,
        match="directory is unavailable",
    ):
        verify_artifact_admission_external_seal(tmp_path / "missing")


def test_external_chain_head_contract_has_no_operational_authority() -> None:
    assert STATUS == (
        "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE_REKOR_"
        "ARTIFACT_ADMISSION_CHAIN_HEAD"
    )
    assert AUTHORITY == {
        "credential_access": False,
        "deployment": False,
        "execution": False,
        "live_trading": False,
        "order_submission": False,
    }
    assert RETENTION == {
        "github_artifact_days": 90,
        "rekor_transparency_log": True,
        "worm_object_lock": False,
    }
    assert "GITHUB_ARTIFACT_IS_NOT_WORM_OBJECT_LOCK" in LIMITATIONS


def test_external_sigstore_workflow_signs_and_verifies_chain_head() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "Build artifact-admission chain-head receipt" in source
    assert "evidence/artifact_admission_seal_latest.json" in source
    assert (
        "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE_REKOR_"
        in source
    )
    assert "ARTIFACT_ADMISSION_CHAIN_HEAD" in source
    assert (
        "artifact-admission-external-receipt.sigstore.json"
        in source
    )
    assert "verify_artifact_admission_external_seal" in source
    assert (
        "artifact-admission-chain-head-${{ github.run_id }}-"
        "${{ github.run_attempt }}"
        in source
    )
    assert "retention-days: 90" in source
    assert "worm_object_lock" in source
    assert "GITHUB_ARTIFACT_IS_NOT_WORM_OBJECT_LOCK" in source
    assert 'type(seal.get("anchor_created")) is not bool' in source


def test_retained_external_chain_head_is_cryptographically_verified() -> None:
    report = verify_artifact_admission_external_seal(EVIDENCE)

    assert report["status"] == STATUS
    assert report["source_commit"] == (
        "fd860b3b3ab80a8e17aa31dbfc329b5297214352"
    )
    assert report["workflow_run"] == (
        "https://github.com/kerstker/QUANTOS/actions/runs/"
        "30608526530/attempts/1"
    )
    assert report["external_signature_verified"] is True
    assert report["transparency_log_verified"] is True
    assert report["signed_certificate_timestamp_verified"] is True
    assert report["worm_retention_verified"] is False
    assert report["commitment"]["event_count"] == 4
    assert report["commitment"]["decision_count"] == 0
    assert report["commitment"]["overall_hash"] == (
        "9e2ba2d3add5642a376dbcbfd5cf4adb61a9649ae727e03925d890ed6cb229b7"
    )


def test_chain_head_contract_ignores_only_local_observation_time() -> None:
    seal = json.loads(
        (EVIDENCE / "artifact-admission-seal.json").read_text(encoding="utf-8")
    )
    later_observation = copy.deepcopy(seal)
    later_observation["observed_at_ms"] += 1
    changed_authority = copy.deepcopy(seal)
    changed_authority["commitment"]["overall_hash"] = "f" * 64

    assert artifact_admission_seal_contract_sha256(later_observation) == (
        artifact_admission_seal_contract_sha256(seal)
    )
    assert artifact_admission_seal_contract_sha256(changed_authority) != (
        artifact_admission_seal_contract_sha256(seal)
    )
