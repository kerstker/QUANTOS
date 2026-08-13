#!/usr/bin/env python
"""Capture native DGX ARM64 console evidence without invoking a shell.

This is the only mutating entry point for the three retained ARM64 evidence
contracts.  It owns narrowly named temporary builders and one smoke container;
it never mounts repository data, state, credentials, or keys into the smoke
container.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Protocol, Sequence


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.evidence_binding import resolve_canonical_source_anchor  # noqa: E402
from services.governance.arm64_console_evidence import (  # noqa: E402
    ARM64_DOCKERFILE_REF,
    ARM64_RUNTIME_IMAGE_REF,
    PROXY_ENV_NAMES,
    TRIVY_DATABASE_REPOSITORY,
    TRIVY_SCANNER_ARM64_MANIFEST_DIGEST,
    TRIVY_SCANNER_IMAGE_REF,
    Arm64ConsoleEvidenceError,
    build_arm64_console_smoke_evidence,
    build_arm64_container_audit_evidence,
    build_arm64_container_reproducibility_evidence,
)
from services.governance.json_integrity import load_json_without_duplicate_keys  # noqa: E402


BUILDER_A = "quantos-arm64-evidence-builder-a"
BUILDER_B = "quantos-arm64-evidence-builder-b"
SMOKE_CONTAINER = "quantos-arm64-console-smoke"
TARGET_PLATFORM = "linux/arm64"
BUILDKIT_IMAGE_REF = (
    "moby/buildkit@sha256:59795d5a98a9e12ddf49567fcd1976a30f2d2a60ef469c2f9cedb9e5e3251b25"
)
_SOURCE_DATE_EPOCH = re.compile(
    r"^\s*ARG\s+SOURCE_DATE_EPOCH=([0-9]+)\s*$", re.MULTILINE
)
_PYTHON_IMAGE = re.compile(r"^\s*ARG\s+PYTHON_IMAGE=([^\s#]+)\s*$", re.MULTILINE)
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_HEALTH_CODE = (
    "import json,urllib.request;"
    "r=urllib.request.urlopen('http://127.0.0.1:8400/health',timeout=5);"
    "print(json.dumps({'body':json.loads(r.read().decode('utf-8')),'status_code':r.status},"
    "sort_keys=True,separators=(',',':')))"
)
_SAFETY_ENV = (
    "LIVE_TRADING_ENABLED=false",
    "REAL_BROKER_ENABLED=false",
    "ALLOW_ORDER_SUBMISSION=false",
    "ALLOW_AI_DIRECT_TRADING=false",
    "REQUIRE_HUMAN_APPROVAL=true",
)


class CaptureError(RuntimeError):
    """The ARM64 capture could not be completed safely."""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: bytes
    stderr: bytes

    def receipt(self) -> dict[str, object]:
        return {
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "stdout_sha256": hashlib.sha256(self.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(self.stderr).hexdigest(),
        }


class Runner(Protocol):
    def run(self, argv: Sequence[str], *, cwd: Path) -> CommandResult: ...


class SubprocessRunner:
    """Exact-argv process runner.  Shell interpretation is always disabled."""

    def run(self, argv: Sequence[str], *, cwd: Path) -> CommandResult:
        if not argv or any(not isinstance(item, str) or not item for item in argv):
            raise CaptureError("command argv must contain non-empty strings")
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            check=False,
            capture_output=True,
            shell=False,
        )
        return CommandResult(
            tuple(argv), completed.returncode, completed.stdout, completed.stderr
        )


def _run(runner: Runner, argv: Sequence[str], root: Path) -> CommandResult:
    result = runner.run(tuple(argv), cwd=root)
    if result.argv != tuple(argv):
        raise CaptureError("runner changed command argv")
    if result.exit_code != 0:
        raise CaptureError(
            f"command failed ({result.exit_code}): {list(argv)!r}; "
            f"stderr_sha256={hashlib.sha256(result.stderr).hexdigest()}"
        )
    return result


def _absent(runner: Runner, argv: Sequence[str], root: Path, label: str) -> None:
    result = runner.run(tuple(argv), cwd=root)
    if result.argv != tuple(argv):
        raise CaptureError("runner changed conflict-check argv")
    if result.exit_code == 0:
        raise CaptureError(f"reserved {label} already exists")
    message = result.stderr.decode("utf-8", errors="replace").lower()
    if result.exit_code != 1 or not any(
        marker in message
        for marker in ("no such", "not found", "does not exist", "no builder")
    ):
        raise CaptureError(f"cannot prove reserved {label} is absent")


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = load_json_without_duplicate_keys(raw)
    except (UnicodeError, ValueError) as exc:
        raise CaptureError(f"{label} is not strict JSON") from exc
    if not isinstance(value, Mapping):
        raise CaptureError(f"{label} must be a JSON object")
    return dict(value)


def _inspect_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = load_json_without_duplicate_keys(raw)
    except (UnicodeError, ValueError) as exc:
        raise CaptureError(f"{label} is not strict JSON") from exc
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], Mapping):
        raise CaptureError(f"{label} must contain exactly one inspect object")
    return dict(value[0])


def _recipe(root: Path) -> tuple[int, str]:
    text = (root / ARM64_DOCKERFILE_REF).read_text(encoding="utf-8")
    epochs = _SOURCE_DATE_EPOCH.findall(text)
    bases = _PYTHON_IMAGE.findall(text)
    if len(epochs) != 1 or len(bases) != 1 or "@sha256:" not in bases[0]:
        raise CaptureError("ARM64 Dockerfile recipe is not uniquely pinned")
    return int(epochs[0]), bases[0]


def _build_argv(
    root: Path, builder: str, archive: Path, epoch: int
) -> list[str]:
    argv = [
        "docker", "buildx", "build",
        "--builder", builder,
        "--platform", TARGET_PLATFORM,
        "--no-cache",
        "--provenance=false",
        "--build-arg", f"SOURCE_DATE_EPOCH={epoch}",
    ]
    for name in PROXY_ENV_NAMES:
        argv.extend(["--build-arg", f"{name}="])
    argv.extend([
        "--tag", ARM64_RUNTIME_IMAGE_REF,
        "--output", (
            f"type=oci,name={ARM64_RUNTIME_IMAGE_REF},dest={archive},"
            "rewrite-timestamp=true"
        ),
        "-f", str(root / ARM64_DOCKERFILE_REF),
        str(root),
    ])
    return argv


def _docker_export_argv(
    root: Path, builder: str, archive: Path, epoch: int
) -> list[str]:
    argv = [
        "docker", "buildx", "build", "--builder", builder,
        "--platform", TARGET_PLATFORM, "--no-cache", "--provenance=false",
        "--build-arg", f"SOURCE_DATE_EPOCH={epoch}",
    ]
    for name in PROXY_ENV_NAMES:
        argv.extend(["--build-arg", f"{name}="])
    argv.extend([
        "--output", f"type=docker,name={ARM64_RUNTIME_IMAGE_REF},dest={archive}",
        "-f", str(root / ARM64_DOCKERFILE_REF), str(root),
    ])
    return argv


def _scanner_argv(
    cache: Path,
    *arguments: str,
    network: str = "none",
    input_archive: Path | None = None,
    input_name: str = "image.tar",
    user: str | None = None,
) -> list[str]:
    argv = [
        "docker", "run", "--rm", "--platform", TARGET_PLATFORM,
        "--network", network,
        "--read-only", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
        "--env", "HOME=/tmp",
        "--mount", f"type=bind,src={cache},dst=/cache",
    ]
    for name in PROXY_ENV_NAMES:
        argv.extend(["--env", f"{name}="])
    if user is not None:
        argv.extend(["--user", user])
    if input_archive is not None:
        argv.extend(
            [
                "--mount",
                f"type=bind,src={input_archive},dst=/input/{input_name},readonly",
            ]
        )
    argv.append(TRIVY_SCANNER_IMAGE_REF)
    argv.extend(arguments)
    return argv


def _smoke_run_argv() -> list[str]:
    argv = [
        "docker", "run", "--rm", "--detach", "--name", SMOKE_CONTAINER,
        "--network", "none", "--read-only", "--user", "65532:65532",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
    ]
    for setting in _SAFETY_ENV:
        argv.extend(["--env", setting])
    for name in PROXY_ENV_NAMES:
        argv.extend(["--env", f"{name}="])
    argv.append(ARM64_RUNTIME_IMAGE_REF)
    return argv


def _database_digest(raw_manifest: bytes) -> str:
    document = _json_object(raw_manifest, "Trivy database raw manifest")
    if document.get("schemaVersion") != 2 or not isinstance(
        document.get("layers"), list
    ):
        raise CaptureError("Trivy database raw manifest has an invalid shape")
    return "sha256:" + hashlib.sha256(raw_manifest).hexdigest()


def _image_id(raw: bytes, label: str) -> str:
    value = _inspect_object(raw, label).get("Id")
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise CaptureError(f"{label} omits a canonical image Id")
    return value


def _assert_evidence_identity(
    reproducibility: Mapping[str, Any],
    audit: Mapping[str, Any],
    smoke: Mapping[str, Any],
    loaded_image_id: str,
) -> None:
    image = reproducibility.get("image")
    if not isinstance(image, Mapping):
        raise CaptureError("reproducibility image identity is unavailable")
    identities = {
        image.get("config_digest"),
        audit.get("runtime_image_digest"),
        smoke.get("runtime_image_config_digest"),
        loaded_image_id,
    }
    if len(identities) != 1 or loaded_image_id not in identities:
        raise CaptureError("ARM64 evidence image identity is inconsistent")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_OUTPUT_RELATIVES = (
    "reproducibility/container_build_arm64_latest.json",
    "vulnerability/container_trivy_arm64_latest.json",
    "runtime/console_arm64_smoke_latest.json",
)
_BUNDLE_DIRECTORY = "arm64-console-evidence-bundle"


def _assert_output_boundary(root: Path, output: Path) -> None:
    root = root.resolve()
    output = output.resolve()
    if output == root or root in output.parents:
        raise CaptureError("output-root must be outside repo-root")
    cursor = output
    while not cursor.exists():
        cursor = cursor.parent
    if cursor.is_symlink() or not cursor.is_dir():
        raise CaptureError("output-root ancestor must be a real directory")
    if output.exists():
        mode = output.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise CaptureError("output-root cannot be a symlink or non-directory")
        for candidate in output.rglob("*"):
            if stat.S_ISLNK(candidate.lstat().st_mode):
                raise CaptureError("output-root contains a symlink")
    bundle = output / _BUNDLE_DIRECTORY
    if bundle.exists() or bundle.is_symlink():
        raise CaptureError("output artifact bundle already exists")


def _source_capture(
    runner: Runner,
    root: Path,
    resolver: Callable[[Path], str],
) -> tuple[str, dict[str, object]]:
    head = _run(runner, ["git", "rev-parse", "HEAD"], root)
    status = _run(
        runner,
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        root,
    )
    uname = _run(runner, ["uname", "-a"], root)
    commit = head.stdout.decode("ascii", errors="strict").strip()
    host_uname = uname.stdout.decode("utf-8", errors="strict").strip()
    if _SHA40.fullmatch(commit) is None:
        raise CaptureError("git HEAD is not a canonical commit SHA")
    if status.stdout != b"":
        raise CaptureError("source checkout is not strictly clean")
    if resolver(root) != commit:
        raise CaptureError("HEAD is not the canonical source anchor")
    if "aarch64" not in host_uname:
        raise CaptureError("capture host is not native aarch64")
    return commit, {
        "host_uname": host_uname,
        "commands": [head.receipt(), status.receipt(), uname.receipt()],
    }


def _parse_buildx_inspect_identity(raw: bytes) -> tuple[str, str]:
    text = raw.decode("utf-8", errors="strict")
    drivers = re.findall(r"^Driver:\s*(\S+)\s*$", text, re.MULTILINE)
    versions = re.findall(
        r"^BuildKit(?: version)?:\s*(v[0-9]+(?:\.[0-9]+){2})\s*$",
        text,
        re.MULTILINE,
    )
    statuses = re.findall(r"^Status:\s*(\S+)\s*$", text, re.MULTILINE)
    platform_lines = re.findall(r"^Platforms:\s*(.+?)\s*$", text, re.MULTILINE)
    platforms = {
        platform.strip().removesuffix("*")
        for line in platform_lines
        for platform in line.split(",")
        if platform.strip() and not platform.strip().endswith("**")
    }
    if (
        len(drivers) != 1
        or len(versions) != 1
        or versions[0] != "v0.26.2"
        or statuses != ["running"]
        or len(platform_lines) != 1
        or "linux/arm64" not in platforms
    ):
        raise CaptureError("buildx inspect identity is invalid or incomplete")
    return drivers[0], versions[0]


def _build_tool(
    runner: Runner, root: Path, builder_a: str, builder_b: str
) -> dict[str, str]:
    buildx = _run(runner, ["docker", "buildx", "version"], root)
    docker = _run(runner, ["docker", "version", "--format", "{{json .}}"], root)
    first = _run(runner, ["docker", "buildx", "inspect", builder_a, "--bootstrap"], root)
    second = _run(runner, ["docker", "buildx", "inspect", builder_b, "--bootstrap"], root)
    docker_value = _json_object(docker.stdout, "docker version")
    client = docker_value.get("Client")
    server = docker_value.get("Server")
    if not isinstance(client, Mapping) or not isinstance(server, Mapping):
        raise CaptureError("docker version omits client or server identity")

    identity_a = _parse_buildx_inspect_identity(first.stdout)
    identity_b = _parse_buildx_inspect_identity(second.stdout)
    if identity_a != identity_b or identity_a[0] != "docker-container":
        raise CaptureError("independent builders do not share the required driver/version")
    return {
        "builder_name": f"{builder_a},{builder_b}",
        "builder_driver": identity_a[0],
        "buildx_version": buildx.stdout.decode("utf-8", errors="strict").strip(),
        "buildkit_version": identity_a[1],
        "docker_client_version": str(client.get("Version", "")),
        "docker_server_version": str(server.get("Version", "")),
        "host_os": "linux",
        "host_architecture": "aarch64",
    }


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _publish_staged_evidence(
    output: Path, staging: Path, documents: Mapping[str, Mapping[str, Any]]
) -> dict[str, Path]:
    destinations = {
        name: output / _BUNDLE_DIRECTORY / relative
        for name, relative in zip(documents, _OUTPUT_RELATIVES, strict=True)
    }
    staged = {name: staging / relative for name, relative in zip(documents, _OUTPUT_RELATIVES, strict=True)}
    for name, document in documents.items():
        _write_json(staged[name], document)
    bundle_source = staging.resolve()
    bundle_target = output / _BUNDLE_DIRECTORY
    output.mkdir(parents=True, exist_ok=True)
    bundle_source.replace(bundle_target)
    return destinations


def capture(
    repo_root: str | Path,
    output_root: str | Path,
    *,
    runner: Runner | None = None,
    source_resolver: Callable[[Path], str] | None = None,
    clock: Callable[[], float] = time.time,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Path]:
    """Run one fail-closed capture and atomically retain all three documents."""
    root = Path(repo_root).resolve()
    output = Path(output_root).resolve()
    if not root.is_dir() or not (root / ".git").exists():
        raise CaptureError("repo-root must be a Git worktree")
    _assert_output_boundary(root, output)
    executor = runner or SubprocessRunner()
    resolver = source_resolver or resolve_canonical_source_anchor
    owned_builders: list[str] = []
    owned_container = False
    owned_runtime_image = False
    session: Path | None = None
    primary_error: BaseException | None = None
    try:
        commit, source_capture = _source_capture(executor, root, resolver)
        output.mkdir(parents=True, exist_ok=True)
        session = Path(
            tempfile.mkdtemp(prefix=".arm64-console-capture-", dir=output)
        )
        epoch, base_image = _recipe(root)
        _absent(
            executor, ["docker", "image", "inspect", ARM64_RUNTIME_IMAGE_REF],
            root, "runtime image",
        )
        _absent(
            executor, ["docker", "container", "inspect", SMOKE_CONTAINER],
            root, "smoke container",
        )
        for builder in (BUILDER_A, BUILDER_B):
            _absent(
                executor, ["docker", "buildx", "inspect", builder], root,
                f"builder {builder}",
            )

        for builder in (BUILDER_A, BUILDER_B):
            _run(
                executor,
                [
                    "docker", "buildx", "create", "--name", builder,
                    "--driver", "docker-container", "--platform", TARGET_PLATFORM,
                    "--driver-opt", f"image={BUILDKIT_IMAGE_REF}",
                ],
                root,
            )
            owned_builders.append(builder)

        build_tool = _build_tool(executor, root, BUILDER_A, BUILDER_B)
        archive_a = session / "console-a.oci.tar"
        archive_b = session / "console-b.oci.tar"
        build_a = _run(executor, _build_argv(root, BUILDER_A, archive_a, epoch), root)
        build_b = _run(executor, _build_argv(root, BUILDER_B, archive_b, epoch), root)
        reproducibility = build_arm64_container_reproducibility_evidence(
            root,
            archive_a=archive_a,
            archive_b=archive_b,
            observed_at=int(clock()),
            build_tool=build_tool,
            source_commit=commit,
            source_clean=True,
            source_capture=source_capture,
            build_captures=[build_a.receipt(), build_b.receipt()],
        )

        runtime_archive = session / "runtime.docker.tar"
        _run(
            executor,
            _docker_export_argv(root, BUILDER_A, runtime_archive, epoch),
            root,
        )
        _run(executor, ["docker", "load", "--input", str(runtime_archive)], root)
        owned_runtime_image = True
        loaded_image_inspect = _run(
            executor, ["docker", "image", "inspect", ARM64_RUNTIME_IMAGE_REF], root
        )
        loaded_image_id = _image_id(loaded_image_inspect.stdout, "loaded runtime image")
        if loaded_image_id != reproducibility["image"]["config_digest"]:
            raise CaptureError("loaded runtime image identity differs from OCI config")
        _run(executor, ["docker", "pull", "--platform", TARGET_PLATFORM, base_image], root)

        cache = session / "trivy-cache"
        cache.mkdir()
        uid = _run(executor, ["id", "-u"], root)
        gid = _run(executor, ["id", "-g"], root)
        scanner_user = (
            uid.stdout.decode("ascii", errors="strict").strip()
            + ":"
            + gid.stdout.decode("ascii", errors="strict").strip()
        )
        if re.fullmatch(r"[1-9][0-9]*:[1-9][0-9]*", scanner_user) is None:
            raise CaptureError("scanner uid/gid must be non-root positive integers")
        scanner_manifest = _run(
            executor,
            [
                "docker", "buildx", "imagetools", "inspect",
                TRIVY_SCANNER_IMAGE_REF,
                "--raw",
            ],
            root,
        )
        scanner_manifest_value = _json_object(
            scanner_manifest.stdout, "Trivy scanner raw manifest"
        )
        scanner_manifests = scanner_manifest_value.get("manifests")
        if not isinstance(scanner_manifests, list) or not any(
            isinstance(item, Mapping)
            and item.get("digest") == TRIVY_SCANNER_ARM64_MANIFEST_DIGEST
            and isinstance(item.get("platform"), Mapping)
            and item["platform"].get("architecture") == "arm64"
            and item["platform"].get("os") == "linux"
            for item in scanner_manifests
        ):
            raise CaptureError("pinned Trivy index omits expected ARM64 manifest")
        scanner_version = _run(
            executor,
            _scanner_argv(
                cache, "version", "--format", "json", user=scanner_user
            ),
            root,
        )
        database_manifest = _run(
            executor,
            [
                "docker", "buildx", "imagetools", "inspect",
                TRIVY_DATABASE_REPOSITORY,
                "--raw",
            ],
            root,
        )
        database_digest = _database_digest(database_manifest.stdout)
        database_ref = (
            TRIVY_DATABASE_REPOSITORY.rsplit(":", 1)[0] + "@" + database_digest
        )
        download_database = _run(
            executor,
            _scanner_argv(
                cache,
                "--cache-dir", "/cache", "image", "--download-db-only",
                "--db-repository", database_ref,
                network="bridge",
                user=scanner_user,
            ),
            root,
        )
        database = _run(
            executor,
            _scanner_argv(
                cache,
                "--cache-dir", "/cache", "version", "--format", "json",
                user=scanner_user,
            ),
            root,
        )
        base_archive = session / "base.docker.tar"
        runtime_scan_archive = session / "runtime-scan.docker.tar"
        base_image_inspect = _run(
            executor, ["docker", "image", "inspect", base_image], root
        )
        runtime_image_inspect = _run(
            executor, ["docker", "image", "inspect", ARM64_RUNTIME_IMAGE_REF], root
        )
        base_image_save = _run(executor, ["docker", "image", "save", "--output", str(base_archive), base_image], root)
        runtime_image_save = _run(
            executor,
            [
                "docker", "image", "save", "--output", str(runtime_scan_archive),
                ARM64_RUNTIME_IMAGE_REF,
            ],
            root,
        )
        scan_common = (
            "--cache-dir", "/cache", "image",
            "--skip-db-update", "--skip-java-db-update", "--skip-check-update",
            "--skip-vex-repo-update", "--disable-telemetry", "--offline-scan",
            "--scanners", "vuln", "--format", "json", "--no-progress",
        )
        base_scan = _run(
            executor,
            _scanner_argv(
                cache,
                *scan_common,
                "--input", "/input/base.tar",
                input_archive=base_archive,
                input_name="base.tar",
                user=scanner_user,
            ),
            root,
        )
        runtime_scan = _run(
            executor,
            _scanner_argv(
                cache,
                *scan_common,
                "--input", "/input/runtime.tar",
                input_archive=runtime_scan_archive,
                input_name="runtime.tar",
                user=scanner_user,
            ),
            root,
        )
        base_raw = base_scan.stdout.decode("utf-8", errors="strict")
        runtime_raw = runtime_scan.stdout.decode("utf-8", errors="strict")
        database_raw = database.stdout.decode("utf-8", errors="strict")
        database_value = _json_object(database.stdout, "Trivy database metadata")
        vulnerability_db = database_value.get("VulnerabilityDB")
        if not isinstance(vulnerability_db, Mapping):
            raise CaptureError("Trivy version JSON omits VulnerabilityDB")
        observed_at = int(clock())
        audit = build_arm64_container_audit_evidence(
            root,
            base_report=_json_object(base_scan.stdout, "base Trivy report"),
            runtime_report=_json_object(runtime_scan.stdout, "runtime Trivy report"),
            observed_at=observed_at,
            database_updated_at=str(vulnerability_db.get("UpdatedAt", "")),
            database_next_update=str(vulnerability_db.get("NextUpdate", "")),
            database_downloaded_at=str(vulnerability_db.get("DownloadedAt", "")),
            database_image_digest=database_digest,
            source_commit=commit,
            source_clean=True,
            source_capture=source_capture,
            scanner_capture={
                "version": scanner_version.receipt(),
                "download_database": download_database.receipt(),
                "database": database.receipt(),
                "database_registry": database_manifest.receipt(),
                "scanner_registry": scanner_manifest.receipt(),
                "host_uid": uid.receipt(),
                "host_gid": gid.receipt(),
                "base_image_inspect": base_image_inspect.receipt(),
                "runtime_image_inspect": runtime_image_inspect.receipt(),
                "base_image_save": base_image_save.receipt(),
                "runtime_image_save": runtime_image_save.receipt(),
                "base_archive_sha256": _sha256_file(base_archive),
                "runtime_archive_sha256": _sha256_file(runtime_scan_archive),
                "base_scan": base_scan.receipt(),
                "runtime_scan": runtime_scan.receipt(),
                "scanner_version_stdout": scanner_version.stdout.decode(
                    "utf-8", errors="strict"
                ),
                "database_metadata_stdout": database_raw,
                "database_image_digest": database_digest,
            },
            base_report_raw=base_raw,
            runtime_report_raw=runtime_raw,
        )
        if audit.get("verdict") != "PASS":
            raise CaptureError("ARM64 vulnerability policy did not pass")

        smoke_run_capture = _run(executor, _smoke_run_argv(), root)
        owned_container = True

        container_inspect = _run(
            executor, ["docker", "inspect", SMOKE_CONTAINER], root
        )
        image_inspect = _run(
            executor, ["docker", "image", "inspect", ARM64_RUNTIME_IMAGE_REF], root
        )
        health_argv = [
            "docker", "exec", SMOKE_CONTAINER, "python", "-c", _HEALTH_CODE
        ]
        health: CommandResult | None = None
        for attempt in range(30):
            candidate = executor.run(tuple(health_argv), cwd=root)
            if candidate.argv != tuple(health_argv):
                raise CaptureError("runner changed health-probe argv")
            if candidate.exit_code == 0:
                health = candidate
                break
            if attempt != 29:
                sleeper(1.0)
        if health is None:
            raise CaptureError("container-loopback health probe did not become ready")
        health_value = _json_object(health.stdout, "container-loopback health response")
        body = health_value.get("body")
        if health_value.get("status_code") != 200 or not isinstance(body, Mapping):
            raise CaptureError("container-loopback health response is invalid")
        smoke_observed_at = int(clock())
        observation = {
            "schema_version": 2,
            "observed_at": smoke_observed_at,
            "container_inspect": _inspect_object(
                container_inspect.stdout, "container inspect"
            ),
            "image_inspect": _inspect_object(image_inspect.stdout, "image inspect"),
            "health_request": {
                "method": "GET",
                "url": "http://127.0.0.1:8400/health",
                "status_code": 200,
                "probe_scope": "CONTAINER_LOOPBACK",
            },
            "health_response": dict(body),
            "capture": {
                "commands": [
                    smoke_run_capture.receipt(),
                    container_inspect.receipt(),
                    image_inspect.receipt(),
                    health.receipt(),
                ],
                "host_uname": source_capture["host_uname"],
                "source_commit": commit,
                "source_clean": True,
            },
        }
        smoke = build_arm64_console_smoke_evidence(
            root,
            observation=observation,
            source_commit=commit,
            source_clean=True,
            source_capture=source_capture,
        )
        _assert_evidence_identity(
            reproducibility, audit, smoke, loaded_image_id
        )

        final_commit, _final_source_capture = _source_capture(executor, root, resolver)
        if final_commit != commit:
            raise CaptureError("source checkout changed during ARM64 capture")
        paths = _publish_staged_evidence(
            output,
            session / "staged-evidence",
            {
                "reproducibility": reproducibility,
                "audit": audit,
                "smoke": smoke,
            },
        )
        return paths
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        if owned_container:
            result = executor.run(("docker", "rm", "--force", SMOKE_CONTAINER), cwd=root)
            if result.exit_code != 0:
                cleanup_errors.append(CaptureError("failed to remove owned smoke container"))
        if owned_runtime_image:
            result = executor.run(
                ("docker", "image", "rm", ARM64_RUNTIME_IMAGE_REF), cwd=root
            )
            if result.exit_code != 0:
                cleanup_errors.append(CaptureError("failed to remove owned runtime image"))
        for builder in reversed(owned_builders):
            result = executor.run(
                ("docker", "buildx", "rm", "--force", builder), cwd=root
            )
            if result.exit_code != 0:
                cleanup_errors.append(CaptureError(f"failed to remove owned builder {builder}"))
        if session is not None and session.exists() and session.parent == output and session.name.startswith(
            ".arm64-console-capture-"
        ):
            try:
                shutil.rmtree(session)
            except OSError as exc:
                cleanup_errors.append(exc)
        if cleanup_errors and primary_error is None:
            raise CaptureError("ARM64 capture cleanup failed") from ExceptionGroup(
                "cleanup errors", cleanup_errors
            )
        if cleanup_errors and primary_error is not None:
            primary_error.add_note(
                f"ARM64 cleanup also failed ({len(cleanup_errors)} error(s))"
            )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args(argv)
    try:
        paths = capture(args.repo_root, args.output_root)
        print(
            "ARM64 console capture: PASS; "
            + "; ".join(f"{name}={path}" for name, path in paths.items())
        )
        return 0
    except (CaptureError, Arm64ConsoleEvidenceError, OSError, ValueError) as exc:
        print(f"ARM64 console capture failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
