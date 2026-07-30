from __future__ import annotations

import copy
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from services.governance.cross_host_container_reproducibility import (
    CrossHostContainerEvidenceError,
    build_container_build_claim,
    build_cross_host_container_evidence,
    verify_container_build_claim,
    verify_cross_host_container_evidence,
)


EPOCH = 1_000_000
COMMIT = "a" * 40
BASE_REF = "public.ecr.aws/docker/library/python@sha256:" + "b" * 64
BUILD_TOOL = {
    "setup_buildx_action_sha": "4d04d5d9486b7bd6fa91e7baf45bbb4f8b9deedd",
    "buildx_version": "github.com/docker/buildx v0.30.1 " + "c" * 40,
    "buildkit_version": "v0.26.2",
    "buildkit_image_ref": "moby/buildkit@sha256:" + "d" * 64,
}


def test_claim_module_imports_without_site_packages() -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            (
                "from services.governance."
                "cross_host_container_reproducibility "
                "import build_container_build_claim; print('IMPORT_OK')"
            ),
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "IMPORT_OK"


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _write_recipe(root: Path) -> None:
    dockerfile = root / "deploy/console.Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text(
        f"ARG PYTHON_IMAGE={BASE_REF}\n"
        f"ARG SOURCE_DATE_EPOCH={EPOCH}\n"
        "FROM ${PYTHON_IMAGE} AS builder\n"
        "FROM ${PYTHON_IMAGE}\n",
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("demo==1.0\n", encoding="utf-8")
    (root / ".dockerignore").write_text(".git/\n", encoding="utf-8")
    services = root / "services"
    services.mkdir()
    (services / "console.py").write_text("APP = 'console'\n", encoding="utf-8")
    quantos = root / "quantos"
    quantos.mkdir()
    (quantos / "__init__.py").write_text("", encoding="utf-8")


def _add_member(
    archive: tarfile.TarFile, name: str, content: bytes
) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mtime = EPOCH
    member.mode = 0o644
    archive.addfile(member, io.BytesIO(content))


def _write_oci(path: Path, layer_content: bytes = b"cross-host-layer\n") -> None:
    compressed_layer = gzip.compress(layer_content, mtime=0)
    layer_digest = _digest(compressed_layer)
    config = _json_bytes(
        {
            "architecture": "amd64",
            "created": _iso(EPOCH),
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": [_digest(layer_content)]},
        }
    )
    config_digest = _digest(config)
    manifest = _json_bytes(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                    "digest": layer_digest,
                    "size": len(compressed_layer),
                    "annotations": {
                        "buildkit/rewritten-timestamp": str(EPOCH)
                    },
                }
            ],
        }
    )
    manifest_digest = _digest(manifest)
    index = _json_bytes(
        {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [
                {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": manifest_digest,
                    "size": len(manifest),
                    "annotations": {
                        "org.opencontainers.image.created": _iso(EPOCH)
                    },
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        }
    )
    blobs = {
        "blobs/sha256/" + manifest_digest.split(":", 1)[1]: manifest,
        "blobs/sha256/" + config_digest.split(":", 1)[1]: config,
        "blobs/sha256/" + layer_digest.split(":", 1)[1]: compressed_layer,
    }
    with tarfile.open(path, mode="w") as archive:
        _add_member(
            archive,
            "oci-layout",
            _json_bytes({"imageLayoutVersion": "1.0.0"}),
        )
        _add_member(archive, "index.json", index)
        for name, content in sorted(blobs.items()):
            _add_member(archive, name, content)


def _claim(root: Path, archive: Path, runner: str) -> dict[str, object]:
    return build_container_build_claim(
        root,
        archive=archive,
        source_commit=COMMIT,
        runner_label=runner,
        workflow_job=f"container-{runner}",
        build_tool=BUILD_TOOL,
    )


def test_distinct_runner_claims_produce_recipe_bound_cross_host_evidence(
    tmp_path: Path,
) -> None:
    _write_recipe(tmp_path)
    first = tmp_path / "first.oci.tar"
    second = tmp_path / "second.oci.tar"
    _write_oci(first)
    _write_oci(second)

    claim_22 = _claim(tmp_path, first, "ubuntu-22.04")
    claim_24 = _claim(tmp_path, second, "ubuntu-24.04")
    evidence = build_cross_host_container_evidence(
        tmp_path,
        claims=[claim_24, claim_22],
        observed_at=EPOCH,
        workflow_ref=(
            "kerstker/QUANTOS/.github/workflows/"
            "sigstore-evidence.yml@refs/heads/codex/top-tier-wave0"
        ),
        workflow_run="https://github.com/kerstker/QUANTOS/actions/runs/123",
    )

    assert verify_container_build_claim(claim_22, tmp_path)["verdict"] == "PASS"
    assert evidence["builders"] == ["ubuntu-22.04", "ubuntu-24.04"]
    assert evidence["archive_byte_identical"] is True
    assert evidence["container_input_sha256"] == claim_22[
        "container_input_sha256"
    ]
    assert verify_cross_host_container_evidence(evidence, tmp_path) == {
        "archive_sha256": claim_22["archive"]["sha256"],
        "builders": ["ubuntu-22.04", "ubuntu-24.04"],
        "container_input_sha256": claim_22["container_input_sha256"],
        "manifest_digest": claim_22["image"]["manifest_digest"],
        "observed_at": EPOCH,
        "source_commit": COMMIT,
        "verdict": "PASS",
    }


def test_aggregate_rejects_same_runner_or_nonidentical_image(tmp_path: Path) -> None:
    _write_recipe(tmp_path)
    first = tmp_path / "first.oci.tar"
    second = tmp_path / "second.oci.tar"
    _write_oci(first)
    _write_oci(second)
    claim = _claim(tmp_path, first, "ubuntu-22.04")

    with pytest.raises(CrossHostContainerEvidenceError, match="distinct"):
        build_cross_host_container_evidence(
            tmp_path,
            claims=[claim, copy.deepcopy(claim)],
            observed_at=EPOCH,
            workflow_ref="kerstker/QUANTOS/.github/workflows/sigstore-evidence.yml@x",
            workflow_run="https://github.com/kerstker/QUANTOS/actions/runs/123",
        )

    _write_oci(second, b"different\n")
    claim_24 = _claim(tmp_path, second, "ubuntu-24.04")
    with pytest.raises(CrossHostContainerEvidenceError, match="byte-identical"):
        build_cross_host_container_evidence(
            tmp_path,
            claims=[claim, claim_24],
            observed_at=EPOCH,
            workflow_ref="kerstker/QUANTOS/.github/workflows/sigstore-evidence.yml@x",
            workflow_run="https://github.com/kerstker/QUANTOS/actions/runs/123",
        )


def test_verifiers_reject_claim_and_aggregate_tampering(tmp_path: Path) -> None:
    _write_recipe(tmp_path)
    first = tmp_path / "first.oci.tar"
    second = tmp_path / "second.oci.tar"
    _write_oci(first)
    _write_oci(second)
    claim_22 = _claim(tmp_path, first, "ubuntu-22.04")
    claim_24 = _claim(tmp_path, second, "ubuntu-24.04")
    evidence = build_cross_host_container_evidence(
        tmp_path,
        claims=[claim_22, claim_24],
        observed_at=EPOCH,
        workflow_ref="kerstker/QUANTOS/.github/workflows/sigstore-evidence.yml@x",
        workflow_run="https://github.com/kerstker/QUANTOS/actions/runs/123",
    )

    tampered_claim = copy.deepcopy(claim_22)
    tampered_claim["archive"]["sha256"] = "e" * 64
    with pytest.raises(CrossHostContainerEvidenceError):
        verify_container_build_claim(tampered_claim, tmp_path)

    tampered_evidence = copy.deepcopy(evidence)
    tampered_evidence["builders"].reverse()
    with pytest.raises(CrossHostContainerEvidenceError):
        verify_cross_host_container_evidence(tampered_evidence, tmp_path)


def test_claim_and_aggregate_reject_changed_container_source(tmp_path: Path) -> None:
    _write_recipe(tmp_path)
    first = tmp_path / "first.oci.tar"
    second = tmp_path / "second.oci.tar"
    _write_oci(first)
    _write_oci(second)
    claim_22 = _claim(tmp_path, first, "ubuntu-22.04")
    claim_24 = _claim(tmp_path, second, "ubuntu-24.04")
    evidence = build_cross_host_container_evidence(
        tmp_path,
        claims=[claim_22, claim_24],
        observed_at=EPOCH,
        workflow_ref="kerstker/QUANTOS/.github/workflows/sigstore-evidence.yml@x",
        workflow_run="https://github.com/kerstker/QUANTOS/actions/runs/123",
    )

    (tmp_path / "services/console.py").write_text(
        "APP = 'changed'\n", encoding="utf-8"
    )

    with pytest.raises(
        CrossHostContainerEvidenceError, match="content root"
    ):
        verify_container_build_claim(claim_22, tmp_path)
    with pytest.raises(
        CrossHostContainerEvidenceError, match="content root"
    ):
        verify_cross_host_container_evidence(evidence, tmp_path)
