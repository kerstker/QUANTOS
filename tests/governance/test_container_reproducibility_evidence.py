from __future__ import annotations

import copy
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

import pytest

from services.governance.reproducibility_evidence import (
    ContainerReproducibilityEvidenceError,
    build_container_reproducibility_evidence,
    verify_container_reproducibility_evidence,
)


EPOCH = 1_000_000
BASE_REF = "public.ecr.aws/docker/library/python@sha256:" + "a" * 64
BUILD_TOOL = {
    "builder_name": "desktop-linux",
    "builder_driver": "docker",
    "buildx_version": (
        "github.com/docker/buildx v0.30.1-desktop.1 " + "b" * 40
    ),
    "buildkit_version": "v0.26.2",
    "docker_client_version": "29.1.3",
    "docker_server_version": "29.1.3",
}


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


def _add_tar_member(
    archive: tarfile.TarFile, name: str, content: bytes, epoch: int
) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mtime = epoch
    member.mode = 0o644
    archive.addfile(member, io.BytesIO(content))


def _write_oci(path: Path, *, layer_content: bytes = b"demo-layer\n") -> None:
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
        _add_tar_member(
            archive,
            "oci-layout",
            _json_bytes({"imageLayoutVersion": "1.0.0"}),
            EPOCH,
        )
        _add_tar_member(archive, "index.json", index, EPOCH)
        for name, content in sorted(blobs.items()):
            _add_tar_member(archive, name, content, EPOCH)


def _build(root: Path) -> dict[str, object]:
    _write_recipe(root)
    first = root / "first.oci.tar"
    second = root / "second.oci.tar"
    _write_oci(first)
    _write_oci(second)
    return build_container_reproducibility_evidence(
        root,
        archive_a=first,
        archive_b=second,
        observed_at=EPOCH,
        build_tool=BUILD_TOOL,
    )


def test_two_cold_oci_archives_are_inspected_and_recipe_bound(tmp_path: Path) -> None:
    evidence = _build(tmp_path)

    assert evidence["verdict"] == "PASS"
    assert evidence["archive_byte_identical"] is True
    assert evidence["builds"][0]["archive_sha256"] == evidence["builds"][1][
        "archive_sha256"
    ]
    assert evidence["image"]["rewritten_timestamp_layer_count"] == 1
    assert verify_container_reproducibility_evidence(evidence, tmp_path) == {
        "archive_sha256": evidence["builds"][0]["archive_sha256"],
        "buildkit_version": "v0.26.2",
        "manifest_digest": evidence["image"]["manifest_digest"],
        "observed_at": EPOCH,
        "verdict": "PASS",
    }


def test_generation_rejects_nonidentical_cold_builds(tmp_path: Path) -> None:
    _write_recipe(tmp_path)
    first = tmp_path / "first.oci.tar"
    second = tmp_path / "second.oci.tar"
    _write_oci(first)
    _write_oci(second, layer_content=b"different-layer\n")

    with pytest.raises(
        ContainerReproducibilityEvidenceError, match="not byte-identical"
    ):
        build_container_reproducibility_evidence(
            tmp_path,
            archive_a=first,
            archive_b=second,
            observed_at=EPOCH,
            build_tool=BUILD_TOOL,
        )


def test_verifier_rejects_recipe_and_evidence_tampering(tmp_path: Path) -> None:
    evidence = _build(tmp_path)
    tampered = copy.deepcopy(evidence)
    tampered["builds"][1]["archive_sha256"] = "c" * 64
    with pytest.raises(
        ContainerReproducibilityEvidenceError, match="byte-identical archives"
    ):
        verify_container_reproducibility_evidence(tampered, tmp_path)

    dockerfile = tmp_path / "deploy/console.Dockerfile"
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8"
    )
    with pytest.raises(ContainerReproducibilityEvidenceError, match="Dockerfile"):
        verify_container_reproducibility_evidence(evidence, tmp_path)


def test_generation_rejects_archive_byte_tampering(tmp_path: Path) -> None:
    _write_recipe(tmp_path)
    first = tmp_path / "first.oci.tar"
    second = tmp_path / "second.oci.tar"
    _write_oci(first)
    second.write_bytes(first.read_bytes() + b"tampered")

    with pytest.raises(ContainerReproducibilityEvidenceError):
        build_container_reproducibility_evidence(
            tmp_path,
            archive_a=first,
            archive_b=second,
            observed_at=EPOCH,
            build_tool=BUILD_TOOL,
        )
