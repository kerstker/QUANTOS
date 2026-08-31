"""Strict retained evidence contracts for the canonical ARM64 console image.

Historical AMD64 receipts remain separately verifiable, but current builds,
scans, smoke tests, and release gates all bind to this native ARM64 contract.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import tarfile
from typing import Any, BinaryIO, Mapping

from services.governance.json_integrity import load_json_without_duplicate_keys


class Arm64ConsoleEvidenceError(ValueError):
    """ARM64 console evidence is malformed, inconsistent, or unsafe."""


ARM64_DOCKERFILE_REF = "deploy/console.Dockerfile"
ARM64_RUNTIME_IMAGE_REF = "quantos-console:l2-arm64"
ARM64_TARGET_PLATFORM = "linux/arm64"
ARM64_REPRODUCIBILITY_MODE = (
    "LOCAL_NATIVE_ARM64_TWO_COLD_OCI_BUILDS_BYTE_IDENTICAL"
)
ARM64_AUDIT_MODE = "EXTERNAL_TRIVY_ARM64_CONTAINER_AUDIT"
ARM64_SMOKE_MODE = "LOCAL_NATIVE_ARM64_CONFIGURATION_AND_HEALTH_EVIDENCE"
TRIVY_VERSION = "0.72.0"
TRIVY_SCANNER_INDEX_DIGEST = (
    "sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f"
)
TRIVY_SCANNER_ARM64_MANIFEST_DIGEST = (
    "sha256:405015d1cd07a2630301169e694a5a420afc4dd553fb462189d4f109ba56a6df"
)
TRIVY_SCANNER_IMAGE_REF = (
    "public.ecr.aws/aquasecurity/trivy@" + TRIVY_SCANNER_INDEX_DIGEST
)
TRIVY_DATABASE_REPOSITORY = "public.ecr.aws/aquasecurity/trivy-db:2"
PROXY_ENV_NAMES = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "FTP_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "ftp_proxy",
    "no_proxy",
    "all_proxy",
)
TRIVY_DATABASE_IMAGE_DIGEST = (
    "sha256:a31116509ea91463d10ae72280c852d312180691205a3177a3ae3f49f9379edd"
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_BASE_IMAGE = re.compile(r"^\s*ARG\s+PYTHON_IMAGE=([^\s#]+)\s*$", re.MULTILINE)
_SOURCE_DATE_EPOCH = re.compile(
    r"^\s*ARG\s+SOURCE_DATE_EPOCH=([0-9]+)\s*$", re.MULTILINE
)
_TARGETARCH_GUARD = re.compile(
    r'^\s*RUN\s+test\s+"\$\{TARGETARCH\}"\s*=\s*"arm64"\s*$', re.MULTILINE
)
_BUILD_TOOL_FIELDS = {
    "builder_name",
    "builder_driver",
    "buildx_version",
    "buildkit_version",
    "docker_client_version",
    "docker_server_version",
    "host_os",
    "host_architecture",
}
_BUILDX_VERSION = re.compile(
    r"^github\.com/docker/buildx v[0-9]+(?:\.[0-9]+){2}(?:-[A-Za-z0-9.]+)? [0-9a-f]{40}$"
)
_BUILDKIT_VERSION = re.compile(r"^v[0-9]+(?:\.[0-9]+){2}$")
_DOCKER_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){2}$")
_TRIVY_SEVERITIES = ("UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL")
_FILE_BINDING_FIELDS = {"path", "sha256", "size"}
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
_REPRODUCIBILITY_FIELDS = {
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
    "source",
    "build_captures",
}
_REPRODUCIBILITY_LIMITATIONS = (
    "SAME_NATIVE_ARM64_HOST_AND_BUILDER_ONLY",
    "LOCAL_OBSERVATION_NOT_IDENTITY_SIGNED",
    "NOT_A_CROSS_HOST_ARTIFACT_ATTESTATION",
    "LOCAL_UNSIGNED_RUNNER_RECEIPT_NOT_CRYPTOGRAPHIC_ATTESTATION",
    "HOST_PROXY_NAMES_INHERITED_VALUES_NOT_RETAINED_NETWORK_PATH_NOT_ATTESTED",
)
_AUDIT_FIELDS = {
    "schema_version",
    "verdict",
    "mode",
    "advisory_source",
    "network_query_executed",
    "observed_at",
    "scanner_name",
    "scanner_version",
    "scanner_image_ref",
    "scanner_index_digest",
    "scanner_arm64_manifest_digest",
    "database_repository",
    "database_schema_version",
    "database_updated_at",
    "database_next_update",
    "database_downloaded_at",
    "database_image_digest",
    "platform",
    "dockerfile_ref",
    "dockerfile_sha256",
    "base_image_ref",
    "base_image_id",
    "base_architecture",
    "base_os",
    "base_vulnerability_counts",
    "runtime_image_ref",
    "runtime_image_config_digest",
    "runtime_image_manifest_digest",
    "runtime_image_manifest_size",
    "runtime_architecture",
    "runtime_os",
    "runtime_vulnerability_counts",
    "remediated_vulnerability_count",
    "scan_policy",
    "base_report_sha256",
    "runtime_report_sha256",
    "base_report",
    "runtime_report",
    "source",
    "scanner_capture",
    "base_report_raw",
    "runtime_report_raw",
}
_SMOKE_FIELDS = {
    "schema_version",
    "verdict",
    "mode",
    "observed_at",
    "platform",
    "runtime_image_ref",
    "runtime_image_config_digest",
    "runtime_image_manifest_digest",
    "dockerfile",
    "observation_sha256",
    "observation",
    "limitations",
    "source",
}
_SMOKE_LIMITATIONS = (
    "TARGET_HOST_LOCAL_OBSERVATION_ONLY",
    "RETAINED_DOCKER_INSPECT_AND_CONTAINER_LOOPBACK_HTTP_RESULT",
    "CONFIGURATION_AND_HEALTH_ONLY",
    "DOES_NOT_PROVE_CREDENTIAL_NON_ACCESS",
    "DOES_NOT_PROVE_ORDER_NON_SUBMISSION",
    "DOES_NOT_PROVE_ABSENCE_OF_OUTBOUND_NETWORK_ACTIVITY",
    "NOT_IDENTITY_SIGNED",
    "LOCAL_UNSIGNED_RUNNER_RECEIPT_NOT_CRYPTOGRAPHIC_ATTESTATION",
)
_SAFETY_ENVIRONMENT = {
    "LIVE_TRADING_ENABLED": "false",
    "REAL_BROKER_ENABLED": "false",
    "ALLOW_ORDER_SUBMISSION": "false",
    "ALLOW_AI_DIRECT_TRADING": "false",
    "REQUIRE_HUMAN_APPROVAL": "true",
}
_CONSOLE_COMMAND = [
    "python",
    "-m",
    "uvicorn",
    "services.console.main:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8400",
]
_FORBIDDEN_ENV_NAME = re.compile(
    r"(?i)(?:^|_)(?:secret|password|passwd|token|credential|api_?key|private_?key)(?:$|_)"
)
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_CAPTURE_FIELDS = {"argv", "exit_code", "stdout_sha256", "stderr_sha256"}
_OCI_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_ARM64_BUILDERS = (
    "quantos-arm64-evidence-builder-a",
    "quantos-arm64-evidence-builder-b",
)


def _option_values(argv: list[str], option: str) -> list[str] | None:
    """Return values for exact two-token options, rejecting joined spellings."""
    if any(item.startswith(option + "=") for item in argv):
        return None
    values: list[str] = []
    index = 0
    while index < len(argv):
        if argv[index] != option:
            index += 1
            continue
        if index + 1 >= len(argv):
            return None
        value = argv[index + 1]
        if value.startswith("-"):
            return None
        values.append(value)
        index += 2
    return values


def _valid_build_capture_argv(
    argv: list[str],
    epoch: int,
    root: Path,
    builder: str,
    archive_name: str,
) -> bool:
    outputs = _option_values(argv, "--output")
    if outputs is None or len(outputs) != 1:
        return False
    output_prefix = f"type=oci,name={ARM64_RUNTIME_IMAGE_REF},dest="
    output_suffix = ",rewrite-timestamp=true"
    output = outputs[0]
    if not output.startswith(output_prefix) or not output.endswith(output_suffix):
        return False
    destination = Path(output[len(output_prefix) : -len(output_suffix)])
    build_args = [f"SOURCE_DATE_EPOCH={epoch}", *PROXY_ENV_NAMES]
    expected = [
        "docker",
        "buildx",
        "build",
        "--builder",
        builder,
        "--platform",
        ARM64_TARGET_PLATFORM,
        "--no-cache",
        "--provenance=false",
    ]
    for value in build_args:
        expected.extend(["--build-arg", value])
    expected.extend(
        [
            "--tag",
            ARM64_RUNTIME_IMAGE_REF,
            "--output",
            output,
            "-f",
            str(root / ARM64_DOCKERFILE_REF),
            str(root),
        ]
    )
    return (
        destination.is_absolute()
        and destination.name == archive_name
        and argv == expected
        and "type=docker" not in output
        and "--load" not in argv
    )


def _valid_scanner_role_argv(argv: list[str], *, role: str) -> bool:
    expected = {
        "version": ("none", "/tmp:rw,noexec,nosuid,size=32m"),
        "download_database": (
            "host",
            "/tmp:rw,noexec,nosuid,size=256m",
        ),
        "database": ("none", "/tmp:rw,noexec,nosuid,size=32m"),
        "base_scan": ("none", "/tmp:rw,noexec,nosuid,size=32m"),
        "runtime_scan": ("none", "/tmp:rw,noexec,nosuid,size=32m"),
    }.get(role)
    if expected is None:
        return False
    expected_network, expected_tmpfs = expected
    cleared_environment = [
        "HOME=/tmp",
        *(f"{name}=" for name in PROXY_ENV_NAMES),
    ]
    expected_environments = (
        (
            ["HOME=/tmp", *PROXY_ENV_NAMES],
            cleared_environment,
        )
        if role == "download_database"
        else (cleared_environment,)
    )
    return (
        _option_values(argv, "--env") in expected_environments
        and _option_values(argv, "--network") == [expected_network]
        and _option_values(argv, "--tmpfs") == [expected_tmpfs]
        and "-e" not in argv
    )


def _expected_smoke_run_argv() -> list[str]:
    argv = [
        "docker", "run", "--rm", "--detach", "--name",
        "quantos-arm64-console-smoke", "--network", "none", "--read-only",
        "--user", "65532:65532", "--cap-drop", "ALL", "--security-opt",
        "no-new-privileges:true", "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=33554432",
    ]
    for name, setting in _SAFETY_ENVIRONMENT.items():
        argv.extend(["--env", f"{name}={setting}"])
    for name in PROXY_ENV_NAMES:
        argv.extend(["--env", f"{name}="])
    argv.append(ARM64_RUNTIME_IMAGE_REF)
    return argv


def _source(
    commit: object,
    clean: object,
    capture: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    if not isinstance(commit, str) or _SHA40.fullmatch(commit) is None:
        raise Arm64ConsoleEvidenceError("source commit must be a canonical 40-char SHA")
    if clean is not True:
        raise Arm64ConsoleEvidenceError("source checkout must be clean")
    if capture is None:
        raise Arm64ConsoleEvidenceError("source capture is required")
    raw_capture = dict(capture)
    commands = raw_capture.get("commands")
    if (
        set(raw_capture) != {"host_uname", "commands"}
        or not isinstance(raw_capture.get("host_uname"), str)
        or "aarch64" not in raw_capture["host_uname"]
        or not isinstance(commands, list)
        or len(commands) != 3
    ):
        raise Arm64ConsoleEvidenceError("source capture is invalid")
    normalized = [
        _capture(value, f"source capture command {index}")
        for index, value in enumerate(commands)
    ]
    if (
        normalized[0]["argv"] != ["git", "rev-parse", "HEAD"]
        or normalized[1]["argv"]
        != ["git", "status", "--porcelain=v1", "--untracked-files=all"]
        or normalized[2]["argv"] != ["uname", "-a"]
    ):
        raise Arm64ConsoleEvidenceError("source capture argv is invalid")
    return {
        "commit": commit,
        "clean": True,
        "host_uname": raw_capture["host_uname"],
        "commands": normalized,
    }


def _capture(value: object, label: str) -> dict[str, Any]:
    capture = _object(value, label)
    if (
        set(capture) != _CAPTURE_FIELDS
        or not isinstance(capture.get("argv"), list)
        or not capture["argv"]
        or any(not isinstance(item, str) or not item for item in capture["argv"])
        or capture.get("exit_code") != 0
        or _HEX_DIGEST.fullmatch(str(capture.get("stdout_sha256"))) is None
        or _HEX_DIGEST.fullmatch(str(capture.get("stderr_sha256"))) is None
    ):
        raise Arm64ConsoleEvidenceError(f"{label} is invalid")
    return capture


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Arm64ConsoleEvidenceError("evidence is not canonical JSON") from exc


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


def _timestamp(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise Arm64ConsoleEvidenceError(f"{label} must be a non-negative integer")
    return value


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise Arm64ConsoleEvidenceError(f"{label} must be an object")
    return dict(value)


def _docker29_manifest_identity(
    value: object,
    label: str,
    *,
    expected_digest: str,
    expected_size: int,
) -> dict[str, Any]:
    image = _object(value, label)
    descriptor = _object(image.get("Descriptor"), f"{label} Descriptor")
    if (
        _DIGEST.fullmatch(expected_digest) is None
        or type(expected_size) is not int
        or expected_size <= 0
        or image.get("Id") != expected_digest
        or descriptor.get("digest") != expected_digest
        or descriptor.get("mediaType") != _OCI_MANIFEST_MEDIA_TYPE
        or descriptor.get("size") != expected_size
        or type(descriptor.get("size")) is not int
        or descriptor["size"] <= 0
    ):
        raise Arm64ConsoleEvidenceError(
            f"{label} Docker 29 manifest descriptor identity is invalid"
        )
    return image


def _file_binding(root: Path, relative: str) -> dict[str, Any]:
    path = root / relative
    content = path.read_bytes()
    return {"path": relative, "sha256": _sha256_bytes(content), "size": len(content)}


def _verify_file_binding(value: object, expected: Mapping[str, Any], label: str) -> None:
    if (
        not isinstance(value, Mapping)
        or set(value) != _FILE_BINDING_FIELDS
        or dict(value) != dict(expected)
    ):
        raise Arm64ConsoleEvidenceError(f"{label} recipe input binding is inconsistent")


def _recipe(root: Path) -> dict[str, Any]:
    dockerfile = root / ARM64_DOCKERFILE_REF
    try:
        text = dockerfile.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise Arm64ConsoleEvidenceError("ARM64 Dockerfile must be UTF-8") from exc
    bases = _BASE_IMAGE.findall(text)
    epochs = _SOURCE_DATE_EPOCH.findall(text)
    if len(bases) != 1 or len(epochs) != 1:
        raise Arm64ConsoleEvidenceError(
            "ARM64 Dockerfile must define one base image and SOURCE_DATE_EPOCH"
        )
    base_image = bases[0]
    logical_text = re.sub(r"\\\r?\n\s*", " ", text)
    proxy_declarations = any(
        re.match(r"^\s*(?:ARG|ENV)\s+", line, re.IGNORECASE)
        and any(
            re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
                line,
                re.IGNORECASE,
            )
            for name in PROXY_ENV_NAMES
        )
        for line in logical_text.splitlines()
    )
    if "@" not in base_image or _DIGEST.fullmatch(base_image.rsplit("@", 1)[1]) is None:
        raise Arm64ConsoleEvidenceError("ARM64 base image must be digest pinned")
    if proxy_declarations:
        raise Arm64ConsoleEvidenceError(
            "ARM64 Dockerfile must not retain proxy ARG or ENV declarations"
        )
    if len(_TARGETARCH_GUARD.findall(text)) != 2:
        raise Arm64ConsoleEvidenceError(
            "ARM64 Dockerfile must guard both build stages with TARGETARCH"
        )
    return {
        "source_date_epoch": int(epochs[0]),
        "base_image_ref": base_image,
        "dockerfile": _file_binding(root, ARM64_DOCKERFILE_REF),
        "requirements": _file_binding(root, "requirements.txt"),
        "dockerignore": _file_binding(root, ".dockerignore"),
    }


def _build_command(epoch: int) -> str:
    proxies = "".join(f"--build-arg {name} " for name in PROXY_ENV_NAMES)
    return (
        "docker buildx build --platform linux/arm64 --no-cache "
        "--provenance=false "
        f"--build-arg SOURCE_DATE_EPOCH={epoch} "
        + proxies
        +
        "--output type=oci,dest=OUTPUT.oci.tar,rewrite-timestamp=true "
        "-f deploy/console.Dockerfile ."
    )


def _read_json_member(
    archive: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    name: str,
    *,
    maximum_size: int = 2 * 1024 * 1024,
) -> tuple[dict[str, Any], bytes]:
    member = members.get(name)
    if member is None or not member.isfile() or member.size > maximum_size:
        raise Arm64ConsoleEvidenceError(f"OCI member is missing or invalid: {name}")
    handle = archive.extractfile(member)
    if handle is None:
        raise Arm64ConsoleEvidenceError(f"cannot read OCI member: {name}")
    content = handle.read()
    if len(content) != member.size:
        raise Arm64ConsoleEvidenceError(f"truncated OCI member: {name}")
    try:
        document = load_json_without_duplicate_keys(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Arm64ConsoleEvidenceError(f"OCI member is not valid JSON: {name}") from exc
    return _object(document, name), content


def _blob_name(digest: str) -> str:
    if _DIGEST.fullmatch(digest) is None:
        raise Arm64ConsoleEvidenceError(f"invalid OCI digest: {digest}")
    return "blobs/sha256/" + digest.split(":", 1)[1]


def _verify_blob(
    archive: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    digest: str,
    size: object,
) -> None:
    if type(size) is not int or size < 0:
        raise Arm64ConsoleEvidenceError("OCI descriptor size is invalid")
    member = members.get(_blob_name(digest))
    if member is None or not member.isfile() or member.size != size:
        raise Arm64ConsoleEvidenceError(f"OCI blob size mismatch: {digest}")
    handle = archive.extractfile(member)
    if handle is None or _sha256_stream(handle) != digest.split(":", 1)[1]:
        raise Arm64ConsoleEvidenceError(f"OCI blob digest mismatch: {digest}")


def _parse_arm64_oci(path: Path, source_date_epoch: int) -> dict[str, Any]:
    if not path.is_file():
        raise Arm64ConsoleEvidenceError(f"missing OCI archive: {path}")
    try:
        opened = tarfile.open(path, mode="r:*")
    except tarfile.TarError as exc:
        raise Arm64ConsoleEvidenceError(f"invalid OCI archive: {path.name}") from exc
    with opened as archive:
        listed = archive.getmembers()
        members = {member.name: member for member in listed}
        if len(members) != len(listed):
            raise Arm64ConsoleEvidenceError("OCI archive contains duplicate member names")
        layout, _ = _read_json_member(archive, members, "oci-layout")
        if layout != {"imageLayoutVersion": "1.0.0"}:
            raise Arm64ConsoleEvidenceError("invalid OCI layout version")
        index, _ = _read_json_member(archive, members, "index.json")
        manifests = index.get("manifests")
        if (
            index.get("schemaVersion") != 2
            or index.get("mediaType") != "application/vnd.oci.image.index.v1+json"
            or not isinstance(manifests, list)
            or len(manifests) != 1
        ):
            raise Arm64ConsoleEvidenceError("OCI index must contain one image manifest")
        descriptor = _object(manifests[0], "OCI manifest descriptor")
        platform = _object(descriptor.get("platform"), "OCI platform")
        annotations = _object(descriptor.get("annotations"), "OCI annotations")
        created = _epoch_iso(source_date_epoch)
        if (
            descriptor.get("mediaType")
            != "application/vnd.oci.image.manifest.v1+json"
            or platform.get("architecture") != "arm64"
            or platform.get("os") != "linux"
            or annotations.get("org.opencontainers.image.created") != created
        ):
            raise Arm64ConsoleEvidenceError(
                "OCI index architecture, OS, or creation time is invalid"
            )
        manifest_digest = descriptor.get("digest")
        manifest_size = descriptor.get("size")
        _verify_blob(archive, members, manifest_digest, manifest_size)
        manifest, manifest_bytes = _read_json_member(
            archive, members, _blob_name(manifest_digest)
        )
        if _sha256_bytes(manifest_bytes) != manifest_digest.split(":", 1)[1]:
            raise Arm64ConsoleEvidenceError("OCI manifest digest mismatch")
        config_descriptor = _object(manifest.get("config"), "OCI config descriptor")
        layers_raw = manifest.get("layers")
        if (
            manifest.get("schemaVersion") != 2
            or manifest.get("mediaType")
            != "application/vnd.oci.image.manifest.v1+json"
            or config_descriptor.get("mediaType")
            != "application/vnd.oci.image.config.v1+json"
            or not isinstance(layers_raw, list)
            or not layers_raw
        ):
            raise Arm64ConsoleEvidenceError("OCI image manifest shape is invalid")
        config_digest = config_descriptor.get("digest")
        config_size = config_descriptor.get("size")
        _verify_blob(archive, members, config_digest, config_size)
        config, config_bytes = _read_json_member(
            archive, members, _blob_name(config_digest)
        )
        if _sha256_bytes(config_bytes) != config_digest.split(":", 1)[1]:
            raise Arm64ConsoleEvidenceError("OCI config digest mismatch")
        rootfs = _object(config.get("rootfs"), "OCI rootfs")
        diff_ids = rootfs.get("diff_ids")
        if (
            config.get("architecture") != "arm64"
            or config.get("os") != "linux"
            or config.get("created") != created
            or rootfs.get("type") != "layers"
            or not isinstance(diff_ids, list)
            or len(diff_ids) != len(layers_raw)
            or any(not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in diff_ids)
        ):
            raise Arm64ConsoleEvidenceError(
                "OCI config architecture, creation time, or diff IDs are invalid"
            )
        layers: list[dict[str, Any]] = []
        rewritten_count = 0
        for raw in layers_raw:
            layer = _object(raw, "OCI layer")
            if layer.get("mediaType") != "application/vnd.oci.image.layer.v1.tar+gzip":
                raise Arm64ConsoleEvidenceError("OCI layer media type is invalid")
            digest = layer.get("digest")
            size = layer.get("size")
            _verify_blob(archive, members, digest, size)
            annotations = layer.get("annotations", {})
            if not isinstance(annotations, Mapping):
                raise Arm64ConsoleEvidenceError("OCI layer annotations are invalid")
            rewritten_value = annotations.get("buildkit/rewritten-timestamp")
            if rewritten_value not in (None, str(source_date_epoch)):
                raise Arm64ConsoleEvidenceError("OCI layer rewrite timestamp is invalid")
            rewritten = rewritten_value == str(source_date_epoch)
            rewritten_count += int(rewritten)
            layers.append({"digest": digest, "size": size, "rewritten_timestamp": rewritten})
        if rewritten_count == 0:
            raise Arm64ConsoleEvidenceError("OCI image has no rewritten timestamp layer")
    return {
        "manifest_digest": manifest_digest,
        "manifest_size": manifest_size,
        "config_digest": config_digest,
        "config_size": config_size,
        "architecture": "arm64",
        "os": "linux",
        "created": created,
        "layers": layers,
        "diff_ids": diff_ids,
        "rewritten_timestamp_layer_count": rewritten_count,
    }


def _validated_build_tool(value: Mapping[str, Any]) -> dict[str, str]:
    if set(value) != _BUILD_TOOL_FIELDS:
        raise Arm64ConsoleEvidenceError("ARM64 build tool identity is invalid")
    result = dict(value)
    if (
        not isinstance(result.get("builder_name"), str)
        or not result["builder_name"]
        or result.get("builder_driver") not in {"docker", "docker-container"}
        or not isinstance(result.get("buildx_version"), str)
        or _BUILDX_VERSION.fullmatch(result["buildx_version"]) is None
        or not isinstance(result.get("buildkit_version"), str)
        or _BUILDKIT_VERSION.fullmatch(result["buildkit_version"]) is None
        or not isinstance(result.get("docker_client_version"), str)
        or _DOCKER_VERSION.fullmatch(result["docker_client_version"]) is None
        or not isinstance(result.get("docker_server_version"), str)
        or _DOCKER_VERSION.fullmatch(result["docker_server_version"]) is None
        or result.get("host_os") != "linux"
        or result.get("host_architecture") != "aarch64"
    ):
        raise Arm64ConsoleEvidenceError("ARM64 build tool versions or host are invalid")
    return result


def build_arm64_container_reproducibility_evidence(
    repo_root: str | Path,
    *,
    archive_a: str | Path,
    archive_b: str | Path,
    observed_at: int,
    build_tool: Mapping[str, Any],
    source_commit: str,
    source_clean: bool,
    source_capture: Mapping[str, Any],
    build_captures: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Inspect two native ARM64 OCI exports and retain byte equality evidence."""
    _timestamp(observed_at, "observed_at")
    first_path = Path(archive_a).resolve()
    second_path = Path(archive_b).resolve()
    if first_path == second_path:
        raise Arm64ConsoleEvidenceError("two distinct OCI archive paths are required")
    root = Path(repo_root).resolve()
    source = _source(source_commit, source_clean, source_capture)
    recipe = _recipe(root)
    if not isinstance(build_captures, list) or len(build_captures) != 2:
        raise Arm64ConsoleEvidenceError("exactly two cold-build captures are required")
    captures = [
        _capture(value, f"cold-build capture {index}")
        for index, value in enumerate(build_captures)
    ]
    if (
        any(
            not _valid_build_capture_argv(
                value["argv"],
                recipe["source_date_epoch"],
                root,
                builder,
                archive.name,
            )
            for value, builder, archive in zip(
                captures,
                _ARM64_BUILDERS,
                (first_path, second_path),
                strict=True,
            )
        )
        or captures[0]["argv"] == captures[1]["argv"]
    ):
        raise Arm64ConsoleEvidenceError("cold builds must use two distinct no-cache argv")
    first_image = _parse_arm64_oci(first_path, recipe["source_date_epoch"])
    second_image = _parse_arm64_oci(second_path, recipe["source_date_epoch"])
    builds = []
    for label, path, image in (
        ("A", first_path, first_image),
        ("B", second_path, second_image),
    ):
        builds.append(
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
        builds[0]["archive_sha256"] != builds[1]["archive_sha256"]
        or builds[0]["archive_size"] != builds[1]["archive_size"]
        or first_image != second_image
    ):
        raise Arm64ConsoleEvidenceError(
            "two cold-build ARM64 OCI archives are not byte-identical"
        )
    return {
        "schema_version": 1,
        "verdict": "PASS",
        "mode": ARM64_REPRODUCIBILITY_MODE,
        "observed_at": observed_at,
        "source_date_epoch": recipe["source_date_epoch"],
        "build_command_template": _build_command(recipe["source_date_epoch"]),
        "build_count": 2,
        "no_cache": True,
        "provenance_disabled": True,
        "rewrite_timestamp": True,
        "exporter": "oci",
        "target_platform": ARM64_TARGET_PLATFORM,
        "base_image_ref": recipe["base_image_ref"],
        "dockerfile": recipe["dockerfile"],
        "requirements": recipe["requirements"],
        "dockerignore": recipe["dockerignore"],
        "build_tool": _validated_build_tool(build_tool),
        "builds": builds,
        "archive_byte_identical": True,
        "image": first_image,
        "limitations": list(_REPRODUCIBILITY_LIMITATIONS),
        "source": source,
        "build_captures": captures,
    }


def _verify_image_summary(value: object, epoch: int) -> dict[str, Any]:
    image = _object(value, "ARM64 image summary")
    layers = image.get("layers")
    diff_ids = image.get("diff_ids")
    if (
        set(image) != _IMAGE_FIELDS
        or _DIGEST.fullmatch(str(image.get("manifest_digest"))) is None
        or type(image.get("manifest_size")) is not int
        or image["manifest_size"] <= 0
        or _DIGEST.fullmatch(str(image.get("config_digest"))) is None
        or type(image.get("config_size")) is not int
        or image["config_size"] <= 0
        or image.get("architecture") != "arm64"
        or image.get("os") != "linux"
        or image.get("created") != _epoch_iso(epoch)
        or not isinstance(layers, list)
        or not layers
        or not isinstance(diff_ids, list)
        or len(diff_ids) != len(layers)
        or any(not isinstance(item, str) or _DIGEST.fullmatch(item) is None for item in diff_ids)
    ):
        raise Arm64ConsoleEvidenceError("ARM64 image summary is invalid")
    rewritten = 0
    for layer in layers:
        if (
            not isinstance(layer, Mapping)
            or set(layer) != _LAYER_FIELDS
            or _DIGEST.fullmatch(str(layer.get("digest"))) is None
            or type(layer.get("size")) is not int
            or layer["size"] < 0
            or type(layer.get("rewritten_timestamp")) is not bool
        ):
            raise Arm64ConsoleEvidenceError("ARM64 layer summary is invalid")
        rewritten += int(layer["rewritten_timestamp"])
    if rewritten == 0 or image.get("rewritten_timestamp_layer_count") != rewritten:
        raise Arm64ConsoleEvidenceError("ARM64 rewritten layer count is invalid")
    return image


def verify_arm64_container_reproducibility_evidence(
    document: Mapping[str, Any], repo_root: str | Path, *, expected_source_commit: str | None = None
) -> dict[str, Any]:
    """Verify retained ARM64 reproducibility evidence against the live recipe."""
    if not isinstance(document, Mapping) or set(document) != _REPRODUCIBILITY_FIELDS:
        raise Arm64ConsoleEvidenceError("ARM64 reproducibility evidence shape is invalid")
    if (
        document.get("schema_version") != 1
        or document.get("verdict") != "PASS"
        or document.get("mode") != ARM64_REPRODUCIBILITY_MODE
        or _timestamp(document.get("observed_at"), "observed_at") < 0
        or document.get("build_count") != 2
        or document.get("no_cache") is not True
        or document.get("provenance_disabled") is not True
        or document.get("rewrite_timestamp") is not True
        or document.get("exporter") != "oci"
        or document.get("target_platform") != ARM64_TARGET_PLATFORM
        or document.get("archive_byte_identical") is not True
        or document.get("limitations") != list(_REPRODUCIBILITY_LIMITATIONS)
    ):
        raise Arm64ConsoleEvidenceError("ARM64 reproducibility claims are invalid")
    root = Path(repo_root).resolve()
    source_value = _object(document.get("source"), "source")
    source = _source(
        source_value.get("commit"),
        source_value.get("clean"),
        {
            "host_uname": source_value.get("host_uname"),
            "commands": source_value.get("commands"),
        },
    )
    if expected_source_commit is not None and source["commit"] != expected_source_commit:
        raise Arm64ConsoleEvidenceError("source evidence is not bound to current anchor")
    captures_value = document.get("build_captures")
    if not isinstance(captures_value, list) or len(captures_value) != 2:
        raise Arm64ConsoleEvidenceError("cold-build captures are invalid")
    captures = [
        _capture(value, f"cold-build capture {index}")
        for index, value in enumerate(captures_value)
    ]
    recipe = _recipe(root)
    if (
        any(
            not _valid_build_capture_argv(
                value["argv"],
                recipe["source_date_epoch"],
                root,
                builder,
                str(_object(record, "ARM64 build record").get("archive_name")),
            )
            for value, builder, record in zip(
                captures,
                _ARM64_BUILDERS,
                document.get("builds", []),
                strict=True,
            )
        )
        or captures[0]["argv"] == captures[1]["argv"]
    ):
        raise Arm64ConsoleEvidenceError("cold-build capture argv is invalid")
    if (
        document.get("source_date_epoch") != recipe["source_date_epoch"]
        or document.get("base_image_ref") != recipe["base_image_ref"]
        or document.get("build_command_template")
        != _build_command(recipe["source_date_epoch"])
    ):
        raise Arm64ConsoleEvidenceError("ARM64 build recipe binding is inconsistent")
    _verify_file_binding(document.get("dockerfile"), recipe["dockerfile"], "Dockerfile")
    _verify_file_binding(document.get("requirements"), recipe["requirements"], "requirements")
    _verify_file_binding(document.get("dockerignore"), recipe["dockerignore"], "dockerignore")
    tool = _validated_build_tool(_object(document.get("build_tool"), "build tool"))
    image = _verify_image_summary(document.get("image"), recipe["source_date_epoch"])
    builds = document.get("builds")
    if not isinstance(builds, list) or len(builds) != 2:
        raise Arm64ConsoleEvidenceError("ARM64 build records are invalid")
    normalized = []
    for label, raw in zip(("A", "B"), builds, strict=True):
        record = _object(raw, "ARM64 build record")
        name = record.get("archive_name")
        if (
            set(record) != _BUILD_FIELDS
            or record.get("label") != label
            or not isinstance(name, str)
            or not name
            or Path(name).name != name
            or _HEX_DIGEST.fullmatch(str(record.get("archive_sha256"))) is None
            or type(record.get("archive_size")) is not int
            or record["archive_size"] <= 0
            or record.get("manifest_digest") != image["manifest_digest"]
            or record.get("config_digest") != image["config_digest"]
        ):
            raise Arm64ConsoleEvidenceError("ARM64 build record values are invalid")
        normalized.append(record)
    if (
        normalized[0]["archive_sha256"] != normalized[1]["archive_sha256"]
        or normalized[0]["archive_size"] != normalized[1]["archive_size"]
    ):
        raise Arm64ConsoleEvidenceError("ARM64 records do not prove byte-identical archives")
    return {
        "archive_sha256": normalized[0]["archive_sha256"],
        "buildkit_version": tool["buildkit_version"],
        "manifest_digest": image["manifest_digest"],
        "observed_at": document["observed_at"],
        "source_commit": source["commit"],
        "verdict": "PASS",
    }


def _parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise Arm64ConsoleEvidenceError(f"{label} must be an ISO-8601 timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise Arm64ConsoleEvidenceError(f"{label} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise Arm64ConsoleEvidenceError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _canonical_trivy_report(value: object, label: str) -> dict[str, Any]:
    report = copy.deepcopy(_object(value, label))
    if (
        report.get("SchemaVersion") != 2
        or _object(report.get("Trivy"), f"{label} Trivy").get("Version") != TRIVY_VERSION
        or report.get("ArtifactType") != "container_image"
        or not isinstance(report.get("ArtifactName"), str)
        or not report["ArtifactName"]
    ):
        raise Arm64ConsoleEvidenceError(f"{label} shape is invalid")
    _parse_utc(report.get("CreatedAt"), f"{label} CreatedAt")
    metadata = _object(report.get("Metadata"), f"{label} metadata")
    image_id = metadata.get("ImageID")
    repo_digests = metadata.get("RepoDigests", [])
    repo_tags = metadata.get("RepoTags", [])
    operating_system = _object(metadata.get("OS"), f"{label} OS")
    image_config = _object(metadata.get("ImageConfig"), f"{label} image config")
    if (
        _DIGEST.fullmatch(str(image_id)) is None
        or not isinstance(repo_digests, list)
        or any(
            not isinstance(item, str)
            or "@" not in item
            or _DIGEST.fullmatch(item.rsplit("@", 1)[1]) is None
            for item in repo_digests
        )
        or not isinstance(repo_tags, list)
        or any(not isinstance(item, str) or not item for item in repo_tags)
        or not isinstance(operating_system.get("Family"), str)
        or not operating_system["Family"]
        or not isinstance(operating_system.get("Name"), str)
        or not operating_system["Name"]
        or image_config.get("architecture") != "arm64"
        or image_config.get("os") != "linux"
    ):
        raise Arm64ConsoleEvidenceError(f"{label} architecture or metadata is invalid")
    results = report.get("Results")
    if not isinstance(results, list) or not results:
        raise Arm64ConsoleEvidenceError(f"{label} results are invalid")
    for result_value in results:
        result = _object(result_value, f"{label} result")
        if not isinstance(result.get("Target"), str) or not result["Target"]:
            raise Arm64ConsoleEvidenceError(f"{label} result target is invalid")
        vulnerabilities = result.get("Vulnerabilities")
        if vulnerabilities is None:
            continue
        if not isinstance(vulnerabilities, list):
            raise Arm64ConsoleEvidenceError(f"{label} vulnerabilities are invalid")
        for vulnerability_value in vulnerabilities:
            vulnerability = _object(vulnerability_value, f"{label} vulnerability")
            if (
                not isinstance(vulnerability.get("VulnerabilityID"), str)
                or not vulnerability["VulnerabilityID"]
                or not isinstance(vulnerability.get("PkgName"), str)
                or not vulnerability["PkgName"]
                or not isinstance(vulnerability.get("InstalledVersion"), str)
                or not vulnerability["InstalledVersion"]
                or vulnerability.get("Severity") not in _TRIVY_SEVERITIES
            ):
                raise Arm64ConsoleEvidenceError(f"{label} vulnerability is invalid")
    _canonical_bytes(report)
    return report


def _trivy_findings(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for result in report["Results"]:
        for vulnerability in result.get("Vulnerabilities") or []:
            findings.append(
                {
                    "target": result["Target"],
                    "id": vulnerability["VulnerabilityID"],
                    "package": vulnerability["PkgName"],
                    "installed_version": vulnerability["InstalledVersion"],
                    "severity": vulnerability["Severity"],
                }
            )
    findings.sort(
        key=lambda item: (
            item["target"],
            item["id"],
            item["package"],
            item["installed_version"],
        )
    )
    return findings


def _trivy_counts(report: Mapping[str, Any]) -> dict[str, int]:
    findings = _trivy_findings(report)
    result = {
        severity.lower(): sum(item["severity"] == severity for item in findings)
        for severity in _TRIVY_SEVERITIES
    }
    result["total"] = len(findings)
    return result


def _base_has_unfixed_high_finding(report: Mapping[str, Any]) -> bool:
    """Return whether an upstream HIGH lacks a vendor-published fix."""
    for result in report["Results"]:
        for vulnerability in result.get("Vulnerabilities") or []:
            if vulnerability.get("Severity") != "HIGH":
                continue
            fixed_version = vulnerability.get("FixedVersion")
            if not isinstance(fixed_version, str) or not fixed_version.strip():
                return True
    return False


def build_arm64_container_audit_evidence(
    repo_root: str | Path,
    *,
    base_report: Mapping[str, Any],
    runtime_report: Mapping[str, Any],
    observed_at: int,
    database_updated_at: str,
    database_next_update: str,
    database_downloaded_at: str,
    database_image_digest: str,
    source_commit: str,
    source_clean: bool,
    source_capture: Mapping[str, Any],
    scanner_capture: Mapping[str, Any],
    base_report_raw: str,
    runtime_report_raw: str,
    runtime_image_config_digest: str,
    runtime_image_manifest_digest: str,
    runtime_image_manifest_size: int,
) -> dict[str, Any]:
    """Bind native ARM64 Trivy reports to the additive console recipe."""
    _timestamp(observed_at, "observed_at")
    if _DIGEST.fullmatch(database_image_digest) is None:
        raise Arm64ConsoleEvidenceError("Trivy database image digest is invalid")
    root = Path(repo_root).resolve()
    source = _source(source_commit, source_clean, source_capture)
    raw_scanner_capture = dict(scanner_capture)
    required_capture = {
        "version",
        "database",
        "base_scan",
        "runtime_scan",
        "scanner_version_stdout",
        "database_metadata_stdout",
        "database_image_digest",
        "download_database",
        "database_registry",
        "scanner_registry",
        "host_uid",
        "host_gid",
        "base_image_inspect",
        "runtime_image_inspect",
        "base_image_save",
        "runtime_image_save",
        "base_archive_sha256",
        "runtime_archive_sha256",
        "runtime_image_inspect_stdout",
        "runtime_image_load",
        "loaded_oci_archive_sha256",
    }
    if set(raw_scanner_capture) != required_capture:
        raise Arm64ConsoleEvidenceError("scanner capture shape is invalid")
    captures = {
        name: _capture(raw_scanner_capture[name], f"scanner {name} capture")
        for name in (
            "version",
            "download_database",
            "database",
            "base_scan",
            "runtime_scan",
            "database_registry",
            "scanner_registry",
            "host_uid",
            "host_gid",
            "base_image_inspect",
            "runtime_image_inspect",
            "base_image_save",
            "runtime_image_save",
            "runtime_image_load",
        )
    }
    scanner_names = {
        "version", "download_database", "database", "base_scan", "runtime_scan"
    }
    hardened_flags = {
        "--read-only", "--cap-drop", "--security-opt", "--tmpfs", "--user"
    }
    scan_flags = {
        "--skip-db-update",
        "--skip-java-db-update",
        "--skip-check-update",
        "--skip-vex-repo-update",
        "--disable-telemetry",
        "--offline-scan",
        "--input",
    }
    archive_sha256 = {
        "base": raw_scanner_capture.get("base_archive_sha256"),
        "runtime": raw_scanner_capture.get("runtime_archive_sha256"),
    }
    if (
        any(
            captures[name]["argv"][:3] != ["docker", "run", "--rm"]
            or not hardened_flags.issubset(set(captures[name]["argv"]))
            or any("docker.sock" in item for item in captures[name]["argv"])
            or not _valid_scanner_role_argv(captures[name]["argv"], role=name)
            for name in scanner_names
        )
        or captures["database_registry"]["argv"]
        != [
            "docker", "buildx", "imagetools", "inspect",
            TRIVY_DATABASE_REPOSITORY, "--raw",
        ]
        or captures["scanner_registry"]["argv"]
        != [
            "docker", "buildx", "imagetools", "inspect",
            TRIVY_SCANNER_IMAGE_REF, "--raw",
        ]
        or captures["host_uid"]["argv"] != ["id", "-u"]
        or captures["host_gid"]["argv"] != ["id", "-g"]
        or captures["base_image_inspect"]["argv"][:3]
        != ["docker", "image", "inspect"]
        or captures["runtime_image_inspect"]["argv"]
        != ["docker", "image", "inspect", ARM64_RUNTIME_IMAGE_REF]
        or captures["runtime_image_load"]["argv"][:5]
        != ["docker", "load", "--platform", ARM64_TARGET_PLATFORM, "--input"]
        or len(captures["runtime_image_load"]["argv"]) != 6
        or captures["base_image_save"]["argv"][:4]
        != ["docker", "image", "save", "--output"]
        or captures["runtime_image_save"]["argv"][:4]
        != ["docker", "image", "save", "--output"]
        or any(_HEX_DIGEST.fullmatch(str(value)) is None for value in archive_sha256.values())
        or "--download-db-only" not in captures["download_database"]["argv"]
        or "--cache-dir" not in captures["database"]["argv"]
        or not scan_flags.issubset(set(captures["base_scan"]["argv"]))
        or not scan_flags.issubset(set(captures["runtime_scan"]["argv"]))
        or "--scanners" not in captures["base_scan"]["argv"]
        or "vuln" not in captures["base_scan"]["argv"]
        or "--scanners" not in captures["runtime_scan"]["argv"]
        or "vuln" not in captures["runtime_scan"]["argv"]
        or "/input/base.tar" not in captures["base_scan"]["argv"]
        or "/input/runtime.tar" not in captures["runtime_scan"]["argv"]
        or raw_scanner_capture.get("database_image_digest") != database_image_digest
        or captures["database_registry"]["stdout_sha256"]
        != database_image_digest.split(":", 1)[1]
        or TRIVY_VERSION not in str(raw_scanner_capture.get("scanner_version_stdout"))
        or captures["version"]["stdout_sha256"]
        != _sha256_bytes(str(raw_scanner_capture.get("scanner_version_stdout")).encode())
        or captures["database"]["stdout_sha256"]
        != _sha256_bytes(str(raw_scanner_capture.get("database_metadata_stdout")).encode())
        or not isinstance(base_report_raw, str)
        or not isinstance(runtime_report_raw, str)
        or captures["base_scan"]["stdout_sha256"]
        != _sha256_bytes(base_report_raw.encode("utf-8"))
        or captures["runtime_scan"]["stdout_sha256"]
        != _sha256_bytes(runtime_report_raw.encode("utf-8"))
        or _HEX_DIGEST.fullmatch(
            str(raw_scanner_capture.get("loaded_oci_archive_sha256"))
        ) is None
    ):
        raise Arm64ConsoleEvidenceError("scanner capture identity or argv is invalid")
    try:
        database_metadata = load_json_without_duplicate_keys(
            raw_scanner_capture.get("database_metadata_stdout")
        )
    except (TypeError, ValueError) as exc:
        raise Arm64ConsoleEvidenceError("scanner database metadata is invalid") from exc
    if isinstance(database_metadata, Mapping) and isinstance(
        database_metadata.get("VulnerabilityDB"), Mapping
    ):
        database_metadata = database_metadata["VulnerabilityDB"]
    if not isinstance(database_metadata, Mapping) or (
        database_metadata.get("Version") != 2
        or database_metadata.get("UpdatedAt") != database_updated_at
        or database_metadata.get("NextUpdate") != database_next_update
        or database_metadata.get("DownloadedAt") != database_downloaded_at
    ):
        raise Arm64ConsoleEvidenceError("scanner database metadata does not match audit")
    try:
        parsed_base = load_json_without_duplicate_keys(base_report_raw)
        parsed_runtime = load_json_without_duplicate_keys(runtime_report_raw)
    except (TypeError, ValueError) as exc:
        raise Arm64ConsoleEvidenceError("raw Trivy report is invalid") from exc
    if parsed_base != base_report or parsed_runtime != runtime_report:
        raise Arm64ConsoleEvidenceError("raw Trivy report does not match parsed report")
    recipe = _recipe(root)
    canonical_base = _canonical_trivy_report(parsed_base, "base Trivy report")
    canonical_runtime = _canonical_trivy_report(parsed_runtime, "runtime Trivy report")
    base_metadata = _object(canonical_base["Metadata"], "base metadata")
    runtime_metadata = _object(canonical_runtime["Metadata"], "runtime metadata")
    if (
        canonical_base["ArtifactName"] != "/input/base.tar"
        or base_metadata.get("RepoDigests", []) != []
        or base_metadata.get("RepoTags", []) != []
    ):
        raise Arm64ConsoleEvidenceError("base Trivy archive report is invalid")
    if (
        canonical_runtime["ArtifactName"] != "/input/runtime.tar"
        or runtime_metadata.get("RepoDigests", []) != []
        or runtime_metadata.get("RepoTags", []) != [ARM64_RUNTIME_IMAGE_REF]
    ):
        raise Arm64ConsoleEvidenceError("runtime Trivy archive report is invalid")
    runtime_digest = runtime_metadata["ImageID"]
    if runtime_digest != runtime_image_config_digest:
        raise Arm64ConsoleEvidenceError(
            "runtime Trivy ImageID does not match the OCI config digest"
        )
    try:
        runtime_image_inspect = load_json_without_duplicate_keys(
            raw_scanner_capture.get("runtime_image_inspect_stdout")
        )
    except (TypeError, ValueError) as exc:
        raise Arm64ConsoleEvidenceError("runtime image inspect is invalid") from exc
    if (
        not isinstance(runtime_image_inspect, list)
        or len(runtime_image_inspect) != 1
    ):
        raise Arm64ConsoleEvidenceError("runtime image inspect shape is invalid")
    _docker29_manifest_identity(
        runtime_image_inspect[0],
        "runtime image inspect",
        expected_digest=runtime_image_manifest_digest,
        expected_size=runtime_image_manifest_size,
    )
    if captures["runtime_image_inspect"]["stdout_sha256"] != _sha256_bytes(
        str(raw_scanner_capture["runtime_image_inspect_stdout"]).encode("utf-8")
    ):
        raise Arm64ConsoleEvidenceError("runtime image inspect receipt is inconsistent")
    base_os = dict(base_metadata["OS"])
    runtime_os = dict(runtime_metadata["OS"])
    if base_os != runtime_os:
        raise Arm64ConsoleEvidenceError("base and runtime OS identities differ")
    observed = datetime.fromtimestamp(observed_at, tz=timezone.utc)
    report_times = (
        _parse_utc(canonical_base["CreatedAt"], "base CreatedAt"),
        _parse_utc(canonical_runtime["CreatedAt"], "runtime CreatedAt"),
    )
    if any(
        value > observed + timedelta(minutes=5)
        or observed - value > timedelta(hours=24)
        for value in report_times
    ):
        raise Arm64ConsoleEvidenceError("Trivy report time is not contemporaneous")
    database_updated = _parse_utc(database_updated_at, "database_updated_at")
    database_next = _parse_utc(database_next_update, "database_next_update")
    database_downloaded = _parse_utc(database_downloaded_at, "database_downloaded_at")
    if (
        database_next <= database_updated
        or database_downloaded < database_updated
        or database_downloaded > observed + timedelta(minutes=5)
        or database_updated > observed + timedelta(minutes=5)
        or observed - database_updated > timedelta(hours=48)
    ):
        raise Arm64ConsoleEvidenceError("Trivy database timing is invalid or stale")
    base_counts = _trivy_counts(canonical_base)
    runtime_counts = _trivy_counts(canonical_runtime)
    runtime_identities = {
        (item["target"], item["id"], item["package"], item["installed_version"])
        for item in _trivy_findings(canonical_runtime)
    }
    remediated = sum(
        (item["target"], item["id"], item["package"], item["installed_version"])
        not in runtime_identities
        for item in _trivy_findings(canonical_base)
    )
    passed = (
        base_counts["critical"] == 0
        and not _base_has_unfixed_high_finding(canonical_base)
        and runtime_counts["total"] == 0
    )
    return {
        "schema_version": 2,
        "verdict": "PASS" if passed else "FAIL",
        "mode": ARM64_AUDIT_MODE,
        "advisory_source": "AQUASEC_TRIVY_DB",
        "network_query_executed": True,
        "observed_at": observed_at,
        "scanner_name": "Trivy",
        "scanner_version": TRIVY_VERSION,
        "scanner_image_ref": TRIVY_SCANNER_IMAGE_REF,
        "scanner_index_digest": TRIVY_SCANNER_INDEX_DIGEST,
        "scanner_arm64_manifest_digest": TRIVY_SCANNER_ARM64_MANIFEST_DIGEST,
        "database_repository": TRIVY_DATABASE_REPOSITORY,
        "database_schema_version": 2,
        "database_updated_at": database_updated_at,
        "database_next_update": database_next_update,
        "database_downloaded_at": database_downloaded_at,
        "database_image_digest": database_image_digest,
        "platform": ARM64_TARGET_PLATFORM,
        "dockerfile_ref": ARM64_DOCKERFILE_REF,
        "dockerfile_sha256": recipe["dockerfile"]["sha256"],
        "base_image_ref": recipe["base_image_ref"],
        "base_image_id": base_metadata["ImageID"],
        "base_architecture": "arm64",
        "base_os": base_os,
        "base_vulnerability_counts": base_counts,
        "runtime_image_ref": ARM64_RUNTIME_IMAGE_REF,
        "runtime_image_config_digest": runtime_digest,
        "runtime_image_manifest_digest": runtime_image_manifest_digest,
        "runtime_image_manifest_size": runtime_image_manifest_size,
        "runtime_architecture": "arm64",
        "runtime_os": runtime_os,
        "runtime_vulnerability_counts": runtime_counts,
        "remediated_vulnerability_count": remediated,
        "scan_policy": {
            "base_block_severities": ["CRITICAL"],
            "base_high_requires_fixed_and_runtime_absent": True,
            "database_download_network_path": (
                "HOST_NETWORK_PROXY_NAMES_INHERITED_VALUES_NOT_RETAINED"
            ),
            "runtime_requires_zero_known": True,
        },
        "base_report_sha256": _sha256_bytes(_canonical_bytes(canonical_base)),
        "runtime_report_sha256": _sha256_bytes(_canonical_bytes(canonical_runtime)),
        "base_report": canonical_base,
        "runtime_report": canonical_runtime,
        "source": source,
        "scanner_capture": raw_scanner_capture,
        "base_report_raw": base_report_raw,
        "runtime_report_raw": runtime_report_raw,
    }


def verify_arm64_container_audit_evidence(
    document: Mapping[str, Any], repo_root: str | Path, *, expected_source_commit: str | None = None
) -> dict[str, Any]:
    """Verify ARM64 Trivy evidence and its exact scanner/recipe identity."""
    if not isinstance(document, Mapping) or set(document) != _AUDIT_FIELDS:
        raise Arm64ConsoleEvidenceError("ARM64 container audit shape is invalid")
    if (
        document.get("schema_version") != 2
        or document.get("mode") != ARM64_AUDIT_MODE
        or document.get("advisory_source") != "AQUASEC_TRIVY_DB"
        or document.get("network_query_executed") is not True
        or document.get("scanner_name") != "Trivy"
        or document.get("scanner_version") != TRIVY_VERSION
        or document.get("scanner_image_ref") != TRIVY_SCANNER_IMAGE_REF
        or document.get("scanner_index_digest") != TRIVY_SCANNER_INDEX_DIGEST
        or document.get("scanner_arm64_manifest_digest")
        != TRIVY_SCANNER_ARM64_MANIFEST_DIGEST
        or document.get("database_repository") != TRIVY_DATABASE_REPOSITORY
        or document.get("database_schema_version") != 2
        or document.get("platform") != ARM64_TARGET_PLATFORM
        or document.get("dockerfile_ref") != ARM64_DOCKERFILE_REF
        or document.get("runtime_image_ref") != ARM64_RUNTIME_IMAGE_REF
        or document.get("base_architecture") != "arm64"
        or document.get("runtime_architecture") != "arm64"
    ):
        raise Arm64ConsoleEvidenceError("ARM64 audit identity claims are invalid")
    expected = build_arm64_container_audit_evidence(
        repo_root,
        base_report=_object(document.get("base_report"), "base report"),
        runtime_report=_object(document.get("runtime_report"), "runtime report"),
        observed_at=document.get("observed_at"),
        database_updated_at=document.get("database_updated_at"),
        database_next_update=document.get("database_next_update"),
        database_downloaded_at=document.get("database_downloaded_at"),
        database_image_digest=document.get("database_image_digest"),
        source_commit=_object(document.get("source"), "source").get("commit"),
        source_clean=_object(document.get("source"), "source").get("clean"),
        source_capture={
            "host_uname": _object(document.get("source"), "source").get("host_uname"),
            "commands": _object(document.get("source"), "source").get("commands"),
        },
        scanner_capture=_object(document.get("scanner_capture"), "scanner capture"),
        base_report_raw=document.get("base_report_raw"),
        runtime_report_raw=document.get("runtime_report_raw"),
        runtime_image_config_digest=document.get("runtime_image_config_digest"),
        runtime_image_manifest_digest=document.get("runtime_image_manifest_digest"),
        runtime_image_manifest_size=document.get("runtime_image_manifest_size"),
    )
    if expected_source_commit is not None and expected["source"]["commit"] != expected_source_commit:
        raise Arm64ConsoleEvidenceError("source evidence is not bound to current anchor")
    if dict(document) != expected:
        raise Arm64ConsoleEvidenceError("ARM64 container audit fields are inconsistent")
    return {
        "base_vulnerability_count": expected["base_vulnerability_counts"]["total"],
        "observed_at": expected["observed_at"],
        "runtime_image_config_digest": expected["runtime_image_config_digest"],
        "runtime_image_manifest_digest": expected["runtime_image_manifest_digest"],
        "runtime_vulnerability_count": expected["runtime_vulnerability_counts"]["total"],
        "scanner_version": expected["scanner_version"],
        "verdict": expected["verdict"],
    }


def _validated_smoke_observation(value: Mapping[str, Any]) -> dict[str, Any]:
    observation = copy.deepcopy(dict(value))
    if set(observation) != {
        "schema_version",
        "observed_at",
        "container_inspect",
        "image_inspect",
        "runtime_image_config_digest",
        "runtime_image_manifest_digest",
        "health_request",
        "health_response",
        "capture",
    }:
        raise Arm64ConsoleEvidenceError("ARM64 smoke observation shape is invalid")
    observed_at = _timestamp(observation.get("observed_at"), "observed_at")
    container = _object(observation.get("container_inspect"), "container inspect")
    image = _object(observation.get("image_inspect"), "image inspect")
    state = _object(container.get("State"), "container state")
    host = _object(container.get("HostConfig"), "container host config")
    config = _object(container.get("Config"), "container config")
    network = _object(container.get("NetworkSettings"), "container network settings")
    image_config = _object(image.get("Config"), "image config")
    raw_capture = _object(observation.get("capture"), "target-host capture")
    if set(raw_capture) != {"commands", "host_uname", "source_commit", "source_clean"}:
        raise Arm64ConsoleEvidenceError("target-host capture shape is invalid")
    capture_commit = raw_capture.get("source_commit")
    capture_clean = raw_capture.get("source_clean")
    if (
        not isinstance(capture_commit, str)
        or _SHA40.fullmatch(capture_commit) is None
        or capture_clean is not True
    ):
        raise Arm64ConsoleEvidenceError("target-host capture source is invalid")
    commands = raw_capture.get("commands")
    if not isinstance(commands, list) or len(commands) != 4:
        raise Arm64ConsoleEvidenceError("target-host capture commands are invalid")
    captured_commands = [
        _capture(value, f"target-host capture command {index}")
        for index, value in enumerate(commands)
    ]
    if (
        captured_commands[0]["argv"] != _expected_smoke_run_argv()
        or captured_commands[1]["argv"]
        != ["docker", "inspect", "quantos-arm64-console-smoke"]
        or captured_commands[2]["argv"]
        != ["docker", "image", "inspect", ARM64_RUNTIME_IMAGE_REF]
        or captured_commands[3]["argv"]
        != [
            "docker",
            "exec",
            "quantos-arm64-console-smoke",
            "python",
            "-c",
            "import json,urllib.request;r=urllib.request.urlopen('http://127.0.0.1:8400/health',timeout=5);print(json.dumps({'body':json.loads(r.read().decode('utf-8')),'status_code':r.status},sort_keys=True,separators=(',',':')))",
        ]
        or not isinstance(raw_capture.get("host_uname"), str)
        or "aarch64" not in raw_capture["host_uname"]
    ):
        raise Arm64ConsoleEvidenceError("target-host capture provenance is invalid")
    config_digest = observation.get("runtime_image_config_digest")
    manifest_digest = observation.get("runtime_image_manifest_digest")
    descriptor = _object(image.get("Descriptor"), "image inspect Descriptor")
    descriptor_size = descriptor.get("size")
    host_bindings = host.get("PortBindings")
    network_ports = network.get("Ports")
    no_published_ports = host_bindings in (None, {}) and network_ports in (
        None,
        {},
        {"8400/tcp": None},
    )
    env = config.get("Env")
    image_env = image_config.get("Env")
    if not isinstance(env, list) or any(not isinstance(item, str) for item in env):
        raise Arm64ConsoleEvidenceError("container environment is invalid")
    if not isinstance(image_env, list) or any(
        not isinstance(item, str) for item in image_env
    ):
        raise Arm64ConsoleEvidenceError("image environment is invalid")
    def parse_environment(entries: list[str], label: str) -> dict[str, str]:
        parsed: dict[str, str] = {}
        for entry in entries:
            if "=" not in entry:
                raise Arm64ConsoleEvidenceError(f"{label} environment entry is invalid")
            name, setting = entry.split("=", 1)
            if not name or name in parsed:
                raise Arm64ConsoleEvidenceError(f"{label} environment names are invalid")
            if _FORBIDDEN_ENV_NAME.search(name):
                raise Arm64ConsoleEvidenceError(
                    f"{label} environment contains a credential-like name"
                )
            parsed[name] = setting
        return parsed

    parsed_environment = parse_environment(env, "container")
    parsed_image_environment = parse_environment(image_env, "image")
    proxy_names_casefolded = {name.casefold() for name in PROXY_ENV_NAMES}
    if any(name.casefold() in proxy_names_casefolded for name in parsed_image_environment):
        raise Arm64ConsoleEvidenceError("image environment retains proxy configuration")
    container_proxy_names = {
        name for name in parsed_environment if name.casefold() in proxy_names_casefolded
    }
    if (
        container_proxy_names != set(PROXY_ENV_NAMES)
        or any(parsed_environment[name] != "" for name in PROXY_ENV_NAMES)
    ):
        raise Arm64ConsoleEvidenceError(
            "container proxy environment must be explicitly cleared"
        )
    if any(item not in env for item in image_env):
        raise Arm64ConsoleEvidenceError("container environment omits image settings")
    expected_environment = {
        **parsed_image_environment,
        **_SAFETY_ENVIRONMENT,
        **{name: "" for name in PROXY_ENV_NAMES},
    }
    if parsed_environment != expected_environment:
        raise Arm64ConsoleEvidenceError("container environment is not exact")
    temporary = host.get("Tmpfs")
    temporary_value = temporary.get("/tmp") if isinstance(temporary, Mapping) else None
    temporary_options = (
        set(temporary_value.split(",")) if isinstance(temporary_value, str) else set()
    )
    if (
        observation.get("schema_version") != 3
        or not isinstance(config_digest, str)
        or _DIGEST.fullmatch(config_digest) is None
        or not isinstance(manifest_digest, str)
        or _DIGEST.fullmatch(manifest_digest) is None
        or config_digest == manifest_digest
        or image.get("Id") != manifest_digest
        or descriptor.get("digest") != manifest_digest
        or descriptor.get("mediaType") != _OCI_MANIFEST_MEDIA_TYPE
        or type(descriptor_size) is not int
        or descriptor_size <= 0
        or image.get("Architecture") != "arm64"
        or image.get("Os") != "linux"
        or ARM64_RUNTIME_IMAGE_REF not in image.get("RepoTags", [])
        or container.get("Image") != manifest_digest
        or config.get("Image") != ARM64_RUNTIME_IMAGE_REF
        or config.get("User") != "65532:65532"
        or image_config.get("User") != "65532:65532"
        or config.get("Cmd") != _CONSOLE_COMMAND
        or image_config.get("Cmd") != _CONSOLE_COMMAND
        or config.get("ExposedPorts") != {"8400/tcp": {}}
        or image_config.get("ExposedPorts") != {"8400/tcp": {}}
        or state.get("Status") != "running"
        or state.get("Running") is not True
        or host.get("ReadonlyRootfs") is not True
        or host.get("Privileged") is not False
        or host.get("AutoRemove") is not True
        or host.get("CapAdd") not in (None, [])
        or host.get("CapDrop") != ["ALL"]
        or host.get("SecurityOpt") != ["no-new-privileges:true"]
        or temporary_options != {"rw", "noexec", "nosuid", "size=33554432"}
        or host.get("Binds") not in (None, [])
        or container.get("Mounts") != []
        or host.get("NetworkMode") != "none"
        or not no_published_ports
        or observation.get("health_request")
        != {
            "method": "GET",
            "url": "http://127.0.0.1:8400/health",
            "status_code": 200,
            "probe_scope": "CONTAINER_LOOPBACK",
        }
        or observation.get("health_response")
        != {
            "status": "healthy",
            "service": "research-console-api",
            "mode": "READ_ONLY",
        }
    ):
        raise Arm64ConsoleEvidenceError("ARM64 smoke observation is unsafe or invalid")
    container_descriptor = container.get("ImageManifestDescriptor")
    if container_descriptor is not None:
        value = _object(container_descriptor, "container image manifest descriptor")
        if (
            value.get("digest") != manifest_digest
            or value.get("mediaType") != _OCI_MANIFEST_MEDIA_TYPE
            or type(value.get("size")) is not int
            or value["size"] <= 0
        ):
            raise Arm64ConsoleEvidenceError(
                "container image manifest descriptor is inconsistent"
            )
    _canonical_bytes(observation)
    observation["observed_at"] = observed_at
    observation["capture"]["source_commit"] = capture_commit
    return observation


def build_arm64_console_smoke_evidence(
    repo_root: str | Path,
    *,
    observation: Mapping[str, Any],
    source_commit: str,
    source_clean: bool,
    source_capture: Mapping[str, Any],
) -> dict[str, Any]:
    """Retain a strict native ARM64 read-only console smoke observation."""
    root = Path(repo_root).resolve()
    recipe = _recipe(root)
    source = _source(source_commit, source_clean, source_capture)
    validated = _validated_smoke_observation(observation)
    if validated["capture"]["source_commit"] != source["commit"]:
        raise Arm64ConsoleEvidenceError("smoke capture source does not match evidence source")
    return {
        "schema_version": 3,
        "verdict": "PASS",
        "mode": ARM64_SMOKE_MODE,
        "observed_at": validated["observed_at"],
        "platform": ARM64_TARGET_PLATFORM,
        "runtime_image_ref": ARM64_RUNTIME_IMAGE_REF,
        "runtime_image_config_digest": validated["runtime_image_config_digest"],
        "runtime_image_manifest_digest": validated["runtime_image_manifest_digest"],
        "dockerfile": recipe["dockerfile"],
        "observation_sha256": _sha256_bytes(_canonical_bytes(validated)),
        "observation": validated,
        "limitations": list(_SMOKE_LIMITATIONS),
        "source": source,
    }


def verify_arm64_console_smoke_evidence(
    document: Mapping[str, Any], repo_root: str | Path, *, expected_source_commit: str | None = None
) -> dict[str, Any]:
    """Verify ARM64 smoke evidence against the current additive recipe."""
    if not isinstance(document, Mapping) or set(document) != _SMOKE_FIELDS:
        raise Arm64ConsoleEvidenceError("ARM64 console smoke shape is invalid")
    if (
        document.get("schema_version") != 3
        or document.get("verdict") != "PASS"
        or document.get("mode") != ARM64_SMOKE_MODE
        or document.get("platform") != ARM64_TARGET_PLATFORM
        or document.get("runtime_image_ref") != ARM64_RUNTIME_IMAGE_REF
        or document.get("limitations") != list(_SMOKE_LIMITATIONS)
    ):
        raise Arm64ConsoleEvidenceError("ARM64 console smoke claims are invalid")
    root = Path(repo_root).resolve()
    source_value = _object(document.get("source"), "source")
    source = _source(
        source_value.get("commit"),
        source_value.get("clean"),
        {
            "host_uname": source_value.get("host_uname"),
            "commands": source_value.get("commands"),
        },
    )
    if expected_source_commit is not None and source["commit"] != expected_source_commit:
        raise Arm64ConsoleEvidenceError("source evidence is not bound to current anchor")
    recipe = _recipe(root)
    _verify_file_binding(document.get("dockerfile"), recipe["dockerfile"], "Dockerfile")
    observation = _validated_smoke_observation(
        _object(document.get("observation"), "smoke observation")
    )
    if (
        document.get("observed_at") != observation["observed_at"]
        or document.get("runtime_image_config_digest")
        != observation["runtime_image_config_digest"]
        or document.get("runtime_image_manifest_digest")
        != observation["runtime_image_manifest_digest"]
        or document.get("observation_sha256")
        != _sha256_bytes(_canonical_bytes(observation))
        or observation["capture"]["source_commit"] != source["commit"]
    ):
        raise Arm64ConsoleEvidenceError("ARM64 smoke evidence is inconsistent")
    return {
        "observed_at": observation["observed_at"],
        "runtime_image_config_digest": observation["runtime_image_config_digest"],
        "runtime_image_manifest_digest": observation[
            "runtime_image_manifest_digest"
        ],
        "verdict": "PASS",
    }


__all__ = [
    "ARM64_RUNTIME_IMAGE_REF",
    "Arm64ConsoleEvidenceError",
    "build_arm64_console_smoke_evidence",
    "build_arm64_container_audit_evidence",
    "build_arm64_container_reproducibility_evidence",
    "verify_arm64_console_smoke_evidence",
    "verify_arm64_container_audit_evidence",
    "verify_arm64_container_reproducibility_evidence",
]
