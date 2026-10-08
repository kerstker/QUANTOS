from __future__ import annotations

import json

import pytest

from services.governance.artifact_admission_store import ArtifactAdmissionStore
from tools.artifact_admission_seal import (
    ArtifactAdmissionSealError,
    export_artifact_admission_seal,
    verify_artifact_admission_seal,
)


def _authority(path) -> None:
    store = ArtifactAdmissionStore(str(path), create=True)
    store.close()


def test_create_once_external_anchor_and_verify_extension(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    authority = repo / "authority.db"
    _authority(authority)
    destination = tmp_path / "external"
    destination.mkdir()
    evidence = repo / "evidence.json"

    first = export_artifact_admission_seal(
        repo_root=repo,
        authority_path=authority,
        destination_root=destination,
        evidence_path=evidence,
        now_ms=100,
        allow_local_destination=True,
    )
    second = export_artifact_admission_seal(
        repo_root=repo,
        authority_path=authority,
        destination_root=destination,
        evidence_path=evidence,
        now_ms=101,
        allow_local_destination=True,
    )
    verified = verify_artifact_admission_seal(
        authority_path=authority,
        anchor_path=first["anchor_path"],
    )

    assert first["anchor_created"] is True
    assert second["anchor_created"] is False
    assert first["anchor_sha256"] == second["anchor_sha256"]
    assert first["institutional_seal_status"] == (
        "PARTIAL_EXTERNAL_WORM_UNVERIFIED"
    )
    assert first["worm_retention_verified"] is False
    assert verified["verdict"] == "PASS"
    assert verified["authority_extends_anchor"] is True
    assert json.loads(evidence.read_text(encoding="utf-8"))["operation_verdict"] == (
        "PASS"
    )


def test_anchor_tampering_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    authority = repo / "authority.db"
    _authority(authority)
    (tmp_path / "external").mkdir()
    result = export_artifact_admission_seal(
        repo_root=repo,
        authority_path=authority,
        destination_root=tmp_path / "external",
        evidence_path=repo / "evidence.json",
        now_ms=100,
        allow_local_destination=True,
    )
    anchor = result["anchor_path"]
    with open(anchor, "ab") as output:
        output.write(b"tamper")

    with pytest.raises(ArtifactAdmissionSealError):
        verify_artifact_admission_seal(
            authority_path=authority,
            anchor_path=anchor,
        )


def test_workspace_destination_is_rejected(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    authority = repo / "authority.db"
    _authority(authority)

    with pytest.raises(ArtifactAdmissionSealError):
        export_artifact_admission_seal(
            repo_root=repo,
            authority_path=authority,
            destination_root=repo / "seals",
            evidence_path=repo / "evidence.json",
            now_ms=100,
        )
