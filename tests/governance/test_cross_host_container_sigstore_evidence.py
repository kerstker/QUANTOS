from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from services.governance.cross_host_container_reproducibility import (
    CrossHostContainerEvidenceError,
    verify_signed_cross_host_container_evidence,
)


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = (
    ROOT
    / "evidence"
    / "external_sigstore"
    / "2026-07-31-container-548ff"
)
TRUSTED_ROOT = (
    ROOT / "evidence" / "external_sigstore" / "2026-07-30" / "trusted-root.json"
)


def test_retained_cross_host_container_sigstore_evidence_verifies_offline() -> None:
    result = verify_signed_cross_host_container_evidence(
        EVIDENCE,
        repo_root=ROOT,
        trusted_root_path=TRUSTED_ROOT,
        require_current_input=False,
    )

    assert result["status"] == (
        "VERIFIED_EXTERNAL_GITHUB_OIDC_SIGSTORE_CROSS_HOST_CONTAINER"
    )
    assert result["source_commit"] == (
        "548ffdaa68289319578338e1ecb9181ab3c1aca2"
    )
    assert result["container_input_sha256"] == (
        "f63d0ef5a6778455dda07af44e167b829a136a1b7e785740348daade0f34b2f3"
    )
    assert result["builders"] == ["ubuntu-22.04", "ubuntu-24.04"]
    assert result["archive_byte_identical"] is True
    assert result["transparency_log_verified"] is True
    assert result["signed_certificate_timestamp_verified"] is True


def test_retained_signature_rejects_evidence_tampering(tmp_path: Path) -> None:
    copied = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, copied)
    target = copied / "quantos-console-cross-host.json"
    document = json.loads(target.read_text(encoding="utf-8"))
    document["archive_sha256"] = "f" * 64
    target.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":"), indent=2)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(CrossHostContainerEvidenceError):
        verify_signed_cross_host_container_evidence(
            copied,
            repo_root=ROOT,
            trusted_root_path=TRUSTED_ROOT,
            require_current_input=False,
        )


def test_retained_signature_rejects_receipt_tampering(tmp_path: Path) -> None:
    copied = tmp_path / "evidence"
    shutil.copytree(EVIDENCE, copied)
    target = copied / "cross-host-container-receipt.json"
    document = json.loads(target.read_text(encoding="utf-8"))
    document["authority"]["deployment"] = True
    target.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":"), indent=2)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        CrossHostContainerEvidenceError, match="receipt is inconsistent"
    ):
        verify_signed_cross_host_container_evidence(
            copied,
            repo_root=ROOT,
            trusted_root_path=TRUSTED_ROOT,
            require_current_input=False,
        )
