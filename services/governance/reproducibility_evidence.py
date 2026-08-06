"""Verification for retained container build reproducibility evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tarfile
from typing import Any, BinaryIO, Mapping


class ContainerReproducibilityEvidenceError(ValueError):
    """The retained two-build OCI observation is malformed or stale."""


MODE = "LOCAL_TWO_COLD_OCI_BUILDS_BYTE_IDENTICAL"
DOCKERFILE_REF = "deploy/console.Dockerfile"
REQUIREMENTS_REF = "requirements.txt"
DOCKERIGNORE_REF = ".dockerignore"
TARGET_PLATFORM = "linux/amd64"
LIMITATIONS = (
    "SAME_HOST_AND_BUILDER_ONLY",
    "LOCAL_OBSERVATION_NOT_IDENTITY_SIGNED",
    "NOT_A_CURRENT_SOURCE_ARTIFACT_ATTESTATION",
)

_OCI_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
_OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_OCI_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
_OCI_LAYER_MEDIA_TYPE = "application/vnd.oci.image.layer.v1.tar+gzip"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_BASE_IMAGE = re.compile(r"^\s*ARG\s+PYTHON_IMAGE=([^\s#]+)\s*$", re.MULTILINE)
_SOURCE_DATE_EPOCH = re.compile(
    r"^\s*ARG\s+SOURCE_DATE_EPOCH=([0-9]+)\s*$", re.MULTILINE
)
_BUILDX_VERSION = re.compile(
    r"^github\.com/docker/buildx v[0-9]+(?:\.[0-9]+){2}(?:-[A-Za-z0-9.]+)? [0-9a-f]{40}$"
)
_BUILDKIT_VERSION = re.compile(r"^v[0-9]+(?:\.[0-9]+){2}$")
_DOCKER_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){2}$")
_BUILD_FIELDS = {
    "label",
    "archive_name",
    "archive_sha256",
    "archive_size",
    "manifest_digest",
    "config_digest",
}
_IMAGE_FIELDS = {
    "manifest_digest",
    "manifest_size",
    "config_digest",
    "config_size",
    "architecture",
    "os",
    "created",
    "layers",
    "diff_ids",
    "rewritten_timestamp_layer_count",
}
_LAYER_FIELDS = {"digest", "size", "rewritten_timestamp"}
_TOOL_FIELDS = {
    "builder_name",
    "builder_driver",
    "buildx_version",
    "buildkit_version",
    "docker_client_version",
    "docker_server_version",
}
_FILE_BINDING_FIELDS = {"path", "sha256", "size"}
_FIELDS = {
    "schema_version",
    "verdict",
    "mode",
    "observed_at",
    "source_date_epoch",
    "build_command_template",
    "build_count",
    "no_cache",
    "provenance_disabled",
    "rewrite_timestamp",
    "exporter",
    "target_platform",
    "base_image_ref",
    "dockerfile",
    "requirements",
    "dockerignore",
    "build_tool",
    "builds",
    "archive_byte_identical",
    "image",
    "limitations",
}


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_stream(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _epoch_iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _file_binding(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    content = path.read_bytes()
    return {
        "path": relative,
        "sha256": _sha256_bytes(content),
        "size": len(content),
    }


def _recipe_bindings(root: Path) -> dict[str, Any]:
    dockerfile = root / DOCKERFILE_REF
    try:
        text = dockerfile.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ContainerReproducibilityEvidenceError(
            "container Dockerfile must be UTF-8"
        ) from exc
    base_matches = _BASE_IMAGE.findall(text)
    epoch_matches = _SOURCE_DATE_EPOCH.findall(text)
    if len(base_matches) != 1 or len(epoch_matches) != 1:
        raise ContainerReproducibilityEvidenceError(
            "Dockerfile must define one base image and SOURCE_DATE_EPOCH"
        )
    base_image_ref = base_matches[0]
    if "@" not in base_image_ref or _DIGEST.fullmatch(
        base_image_ref.rsplit("@", 1)[1]
    ) is None:
        raise ContainerReproducibilityEvidenceError(
            "container base image must be digest pinned"
        )
    epoch = int(epoch_matches[0])
    return {
        "source_date_epoch": epoch,
        "base_image_ref": base_image_ref,
        "dockerfile": _file_binding(root, DOCKERFILE_REF),
        "requirements": _file_binding(root, REQUIREMENTS_REF),
        "dockerignore": _file_binding(root, DOCKERIGNORE_REF),
    }


def _build_command(epoch: int) -> str:
    return (
        "rtk docker buildx build --no-cache --provenance=false "
        f"--build-arg SOURCE_DATE_EPOCH={epoch} "
        "--output type=oci,dest=OUTPUT.oci.tar,rewrite-timestamp=true "
        "-f deploy/console.Dockerfile ."
    )


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContainerReproducibilityEvidenceError(f"{label} must be an object")
    return value


def _read_json_member(
    archive: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    name: str,
    *,
    maximum_size: int = 2 * 1024 * 1024,
) -> tuple[dict[str, Any], bytes]:
    member = members.get(name)
    if member is None or not member.isfile() or member.size > maximum_size:
        raise ContainerReproducibilityEvidenceError(
            f"OCI member is missing or invalid: {name}"
        )
    handle = archive.extractfile(member)
    if handle is None:
        raise ContainerReproducibilityEvidenceError(f"cannot read OCI member: {name}")
    content = handle.read()
    if len(content) != member.size:
        raise ContainerReproducibilityEvidenceError(f"truncated OCI member: {name}")
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContainerReproducibilityEvidenceError(
            f"OCI member is not valid JSON: {name}"
        ) from exc
    return _object(document, name), content


def _blob_name(digest: str) -> str:
    if _DIGEST.fullmatch(digest) is None:
        raise ContainerReproducibilityEvidenceError(f"invalid OCI digest: {digest}")
    return "blobs/sha256/" + digest.split(":", 1)[1]


def _verify_blob(
    archive: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    digest: str,
    size: Any,
) -> tarfile.TarInfo:
    if type(size) is not int or size < 0:
        raise ContainerReproducibilityEvidenceError("OCI descriptor size is invalid")
    member = members.get(_blob_name(digest))
    if member is None or not member.isfile() or member.size != size:
        raise ContainerReproducibilityEvidenceError(
            f"OCI blob size mismatch: {digest}"
        )
    handle = archive.extractfile(member)
    if handle is None or _sha256_stream(handle) != digest.split(":", 1)[1]:
        raise ContainerReproducibilityEvidenceError(
            f"OCI blob digest mismatch: {digest}"
        )
    return member


def _parse_oci_archive(path: Path, source_date_epoch: int) -> dict[str, Any]:
    if not path.is_file():
        raise ContainerReproducibilityEvidenceError(f"missing OCI archive: {path}")
    try:
        archive = tarfile.open(path, mode="r:*")
    except tarfile.TarError as exc:
        raise ContainerReproducibilityEvidenceError(
            f"invalid OCI archive: {path.name}"
        ) from exc
    with archive:
        member_list = archive.getmembers()
        members = {member.name: member for member in member_list}
        if len(members) != len(member_list):
            raise ContainerReproducibilityEvidenceError(
                "OCI archive contains duplicate member names"
            )
        layout, _ = _read_json_member(archive, members, "oci-layout")
        if layout != {"imageLayoutVersion": "1.0.0"}:
            raise ContainerReproducibilityEvidenceError("invalid OCI layout version")
        index, _ = _read_json_member(archive, members, "index.json")
        manifests = index.get("manifests")
        if (
            index.get("schemaVersion") != 2
            or index.get("mediaType") != _OCI_INDEX_MEDIA_TYPE
            or not isinstance(manifests, list)
            or len(manifests) != 1
        ):
            raise ContainerReproducibilityEvidenceError(
                "OCI index must contain one image manifest"
            )
        descriptor = _object(manifests[0], "OCI manifest descriptor")
        platform = _object(descriptor.get("platform"), "OCI platform")
        annotations = _object(descriptor.get("annotations"), "OCI annotations")
        expected_created = _epoch_iso(source_date_epoch)
        if (
            descriptor.get("mediaType") != _OCI_MANIFEST_MEDIA_TYPE
            or platform.get("architecture") != "amd64"
            or platform.get("os") != "linux"
            or annotations.get("org.opencontainers.image.created")
            != expected_created
        ):
            raise ContainerReproducibilityEvidenceError(
                "OCI index platform or creation time is invalid"
            )
        manifest_digest = descriptor.get("digest")
        manifest_size = descriptor.get("size")
        _verify_blob(archive, members, manifest_digest, manifest_size)
        manifest, manifest_bytes = _read_json_member(
            archive, members, _blob_name(manifest_digest)
        )
        if _sha256_bytes(manifest_bytes) != manifest_digest.split(":", 1)[1]:
            raise ContainerReproducibilityEvidenceError(
                "OCI manifest bytes do not match the index digest"
            )
        config_descriptor = _object(manifest.get("config"), "OCI config descriptor")
        layer_descriptors = manifest.get("layers")
        if (
            manifest.get("schemaVersion") != 2
            or manifest.get("mediaType") != _OCI_MANIFEST_MEDIA_TYPE
            or config_descriptor.get("mediaType") != _OCI_CONFIG_MEDIA_TYPE
            or not isinstance(layer_descriptors, list)
            or not layer_descriptors
        ):
            raise ContainerReproducibilityEvidenceError(
                "OCI image manifest shape is invalid"
            )
        config_digest = config_descriptor.get("digest")
        config_size = config_descriptor.get("size")
        _verify_blob(archive, members, config_digest, config_size)
        config, config_bytes = _read_json_member(
            archive, members, _blob_name(config_digest)
        )
        if _sha256_bytes(config_bytes) != config_digest.split(":", 1)[1]:
            raise ContainerReproducibilityEvidenceError(
                "OCI config bytes do not match the manifest digest"
            )
        rootfs = _object(config.get("rootfs"), "OCI config rootfs")
        diff_ids = rootfs.get("diff_ids")
        if (
            config.get("architecture") != "amd64"
            or config.get("os") != "linux"
            or config.get("created") != expected_created
            or rootfs.get("type") != "layers"
            or not isinstance(diff_ids, list)
            or len(diff_ids) != len(layer_descriptors)
            or any(not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in diff_ids)
        ):
            raise ContainerReproducibilityEvidenceError(
                "OCI config platform, creation time, or diff IDs are invalid"
            )
        layers: list[dict[str, Any]] = []
        rewritten_count = 0
        for raw_layer in layer_descriptors:
            layer = _object(raw_layer, "OCI layer descriptor")
            if layer.get("mediaType") != _OCI_LAYER_MEDIA_TYPE:
                raise ContainerReproducibilityEvidenceError(
                    "OCI layer compression or media type is invalid"
                )
            digest = layer.get("digest")
            size = layer.get("size")
            _verify_blob(archive, members, digest, size)
            layer_annotations = layer.get("annotations", {})
            if not isinstance(layer_annotations, dict):
                raise ContainerReproducibilityEvidenceError(
                    "OCI layer annotations must be an object"
                )
            rewritten_value = layer_annotations.get("buildkit/rewritten-timestamp")
            if rewritten_value not in (None, str(source_date_epoch)):
                raise ContainerReproducibilityEvidenceError(
                    "OCI layer rewrite timestamp does not match SOURCE_DATE_EPOCH"
                )
            rewritten = rewritten_value == str(source_date_epoch)
            rewritten_count += int(rewritten)
            layers.append(
                {"digest": digest, "size": size, "rewritten_timestamp": rewritten}
            )
        if rewritten_count == 0:
            raise ContainerReproducibilityEvidenceError(
                "OCI image has no BuildKit rewritten timestamp layers"
            )
    return {
        "manifest_digest": manifest_digest,
        "manifest_size": manifest_size,
        "config_digest": config_digest,
        "config_size": config_size,
        "architecture": "amd64",
        "os": "linux",
        "created": _epoch_iso(source_date_epoch),
        "layers": layers,
        "diff_ids": diff_ids,
        "rewritten_timestamp_layer_count": rewritten_count,
    }


def _validated_build_tool(value: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _TOOL_FIELDS:
        raise ContainerReproducibilityEvidenceError("build tool identity is invalid")
    result = dict(value)
    if (
        result.get("builder_name") != "quantos-cross-host"
        or result.get("builder_driver") != "docker-container"
        or not isinstance(result.get("buildx_version"), str)
        or _BUILDX_VERSION.fullmatch(result["buildx_version"]) is None
        or not isinstance(result.get("buildkit_version"), str)
        or _BUILDKIT_VERSION.fullmatch(result["buildkit_version"]) is None
        or not isinstance(result.get("docker_client_version"), str)
        or _DOCKER_VERSION.fullmatch(result["docker_client_version"]) is None
        or not isinstance(result.get("docker_server_version"), str)
        or _DOCKER_VERSION.fullmatch(result["docker_server_version"]) is None
    ):
        raise ContainerReproducibilityEvidenceError("build tool versions are invalid")
    return result


def build_container_reproducibility_evidence(
    repo_root: str | Path,
    *,
    archive_a: str | Path,
    archive_b: str | Path,
    observed_at: int,
    build_tool: Mapping[str, Any],
) -> dict[str, Any]:
    """Inspect two OCI archives and retain a recipe-bound equality observation."""
    if type(observed_at) is not int or observed_at < 0:
        raise ContainerReproducibilityEvidenceError(
            "observed_at must be a non-negative integer"
        )
    first_path = Path(archive_a).resolve()
    second_path = Path(archive_b).resolve()
    if first_path == second_path:
        raise ContainerReproducibilityEvidenceError(
            "reproducibility requires two distinct archive paths"
        )
    root = Path(repo_root).resolve()
    recipe = _recipe_bindings(root)
    first_image = _parse_oci_archive(first_path, recipe["source_date_epoch"])
    second_image = _parse_oci_archive(second_path, recipe["source_date_epoch"])
    records = []
    for label, path, image in (
        ("A", first_path, first_image),
        ("B", second_path, second_image),
    ):
        records.append(
            {
                "label": label,
                "archive_name": path.name,
                "archive_sha256": _sha256_file(path),
                "archive_size": path.stat().st_size,
                "manifest_digest": image["manifest_digest"],
                "config_digest": image["config_digest"],
            }
        )
    if (
        records[0]["archive_sha256"] != records[1]["archive_sha256"]
        or records[0]["archive_size"] != records[1]["archive_size"]
        or first_image != second_image
    ):
        raise ContainerReproducibilityEvidenceError(
            "two cold-build OCI archives are not byte-identical"
        )
    return {
        "schema_version": 1,
        "verdict": "PASS",
        "mode": MODE,
        "observed_at": observed_at,
        "source_date_epoch": recipe["source_date_epoch"],
        "build_command_template": _build_command(recipe["source_date_epoch"]),
        "build_count": 2,
        "no_cache": True,
        "provenance_disabled": True,
        "rewrite_timestamp": True,
        "exporter": "oci",
        "target_platform": TARGET_PLATFORM,
        "base_image_ref": recipe["base_image_ref"],
        "dockerfile": recipe["dockerfile"],
        "requirements": recipe["requirements"],
        "dockerignore": recipe["dockerignore"],
        "build_tool": _validated_build_tool(build_tool),
        "builds": records,
        "archive_byte_identical": True,
        "image": first_image,
        "limitations": list(LIMITATIONS),
    }


def _verify_file_binding(value: Any, expected: Mapping[str, Any], label: str) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != _FILE_BINDING_FIELDS
        or dict(value) != dict(expected)
    ):
        raise ContainerReproducibilityEvidenceError(
            f"{label} recipe input binding is inconsistent"
        )


def _verify_image_summary(value: Any, source_date_epoch: int) -> dict[str, Any]:
    image = _object(value, "image summary")
    if set(image) != _IMAGE_FIELDS:
        raise ContainerReproducibilityEvidenceError("image summary shape is invalid")
    layers = image.get("layers")
    diff_ids = image.get("diff_ids")
    if (
        _DIGEST.fullmatch(str(image.get("manifest_digest"))) is None
        or type(image.get("manifest_size")) is not int
        or image["manifest_size"] <= 0
        or _DIGEST.fullmatch(str(image.get("config_digest"))) is None
        or type(image.get("config_size")) is not int
        or image["config_size"] <= 0
        or image.get("architecture") != "amd64"
        or image.get("os") != "linux"
        or image.get("created") != _epoch_iso(source_date_epoch)
        or not isinstance(layers, list)
        or not layers
        or not isinstance(diff_ids, list)
        or len(diff_ids) != len(layers)
        or any(not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in diff_ids)
    ):
        raise ContainerReproducibilityEvidenceError("image summary values are invalid")
    rewritten_count = 0
    for layer in layers:
        if (
            not isinstance(layer, Mapping)
            or set(layer) != _LAYER_FIELDS
            or not isinstance(layer.get("digest"), str)
            or _DIGEST.fullmatch(layer["digest"]) is None
            or type(layer.get("size")) is not int
            or layer["size"] < 0
            or type(layer.get("rewritten_timestamp")) is not bool
        ):
            raise ContainerReproducibilityEvidenceError(
                "image layer summary is invalid"
            )
        rewritten_count += int(layer["rewritten_timestamp"])
    if (
        rewritten_count == 0
        or image.get("rewritten_timestamp_layer_count") != rewritten_count
    ):
        raise ContainerReproducibilityEvidenceError(
            "rewritten timestamp layer count is invalid"
        )
    return image


def verify_container_reproducibility_evidence(
    document: Mapping[str, Any], repo_root: str | Path
) -> dict[str, Any]:
    """Verify retained equality evidence against the current build recipe."""
    if not isinstance(document, Mapping) or set(document) != _FIELDS:
        raise ContainerReproducibilityEvidenceError(
            "container reproducibility evidence has an invalid shape"
        )
    if (
        document.get("schema_version") != 1
        or document.get("verdict") != "PASS"
        or document.get("mode") != MODE
        or type(document.get("observed_at")) is not int
        or document["observed_at"] < 0
        or document.get("build_count") != 2
        or document.get("no_cache") is not True
        or document.get("provenance_disabled") is not True
        or document.get("rewrite_timestamp") is not True
        or document.get("exporter") != "oci"
        or document.get("target_platform") != TARGET_PLATFORM
        or document.get("archive_byte_identical") is not True
        or document.get("limitations") != list(LIMITATIONS)
    ):
        raise ContainerReproducibilityEvidenceError(
            "container reproducibility claims are invalid"
        )
    root = Path(repo_root).resolve()
    recipe = _recipe_bindings(root)
    if (
        document.get("source_date_epoch") != recipe["source_date_epoch"]
        or document.get("base_image_ref") != recipe["base_image_ref"]
        or document.get("build_command_template")
        != _build_command(recipe["source_date_epoch"])
    ):
        raise ContainerReproducibilityEvidenceError(
            "container build recipe binding is inconsistent"
        )
    _verify_file_binding(document.get("dockerfile"), recipe["dockerfile"], "Dockerfile")
    _verify_file_binding(
        document.get("requirements"), recipe["requirements"], "requirements"
    )
    _verify_file_binding(
        document.get("dockerignore"), recipe["dockerignore"], "dockerignore"
    )
    build_tool = _validated_build_tool(
        _object(document.get("build_tool"), "build tool identity")
    )
    image = _verify_image_summary(
        document.get("image"), recipe["source_date_epoch"]
    )
    builds = document.get("builds")
    if not isinstance(builds, list) or len(builds) != 2:
        raise ContainerReproducibilityEvidenceError("build records are invalid")
    normalized: list[dict[str, Any]] = []
    for expected_label, raw in zip(("A", "B"), builds, strict=True):
        if not isinstance(raw, Mapping) or set(raw) != _BUILD_FIELDS:
            raise ContainerReproducibilityEvidenceError("build record shape is invalid")
        record = dict(raw)
        name = record.get("archive_name")
        if (
            record.get("label") != expected_label
            or not isinstance(name, str)
            or not name
            or Path(name).name != name
            or not isinstance(record.get("archive_sha256"), str)
            or _HEX_DIGEST.fullmatch(record["archive_sha256"]) is None
            or type(record.get("archive_size")) is not int
            or record["archive_size"] <= 0
            or record.get("manifest_digest") != image["manifest_digest"]
            or record.get("config_digest") != image["config_digest"]
        ):
            raise ContainerReproducibilityEvidenceError("build record values are invalid")
        normalized.append(record)
    if (
        normalized[0]["archive_sha256"] != normalized[1]["archive_sha256"]
        or normalized[0]["archive_size"] != normalized[1]["archive_size"]
    ):
        raise ContainerReproducibilityEvidenceError(
            "build records do not prove byte-identical archives"
        )
    return {
        "archive_sha256": normalized[0]["archive_sha256"],
        "buildkit_version": build_tool["buildkit_version"],
        "manifest_digest": image["manifest_digest"],
        "observed_at": document["observed_at"],
        "verdict": "PASS",
    }


__all__ = [
    "ContainerReproducibilityEvidenceError",
    "build_container_reproducibility_evidence",
    "verify_container_reproducibility_evidence",
]
