from __future__ import annotations

import copy
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile
from typing import Any

import pytest

from services.governance.arm64_console_evidence import (
    ARM64_RUNTIME_IMAGE_REF,
    PROXY_ENV_NAMES,
    Arm64ConsoleEvidenceError,
    build_arm64_console_smoke_evidence,
    build_arm64_container_audit_evidence,
    build_arm64_container_reproducibility_evidence,
    verify_arm64_console_smoke_evidence,
    verify_arm64_container_audit_evidence,
    verify_arm64_container_reproducibility_evidence,
)
from services.governance.json_integrity import DuplicateJsonKeyError


EPOCH = 1_000_000
BASE_DIGEST = "sha256:" + "a" * 64
BASE_ID = "sha256:" + "b" * 64
RUNTIME_CONFIG_DIGEST = "sha256:" + "c" * 64
RUNTIME_MANIFEST_DIGEST = "sha256:" + "d" * 64
RUNTIME_MANIFEST_SIZE = 1234
# Audit findings remain bound to the OCI config identity.
RUNTIME_DIGEST = RUNTIME_CONFIG_DIGEST
BASE_REF = f"public.ecr.aws/docker/library/python@{BASE_DIGEST}"
BUILD_TOOL = {
    "builder_name": "default",
    "builder_driver": "docker",
    "buildx_version": "github.com/docker/buildx v0.31.1 " + "d" * 40,
    "buildkit_version": "v0.27.1",
    "docker_client_version": "29.2.1",
    "docker_server_version": "29.2.1",
    "host_os": "linux",
    "host_architecture": "aarch64",
}
SOURCE_COMMIT = "f" * 40


def _capture(argv: list[str], byte: str = "0") -> dict[str, object]:
    return {
        "argv": argv,
        "exit_code": 0,
        "stdout_sha256": byte * 64,
        "stderr_sha256": byte * 64,
    }


def _build_capture(
    root: Path, archive: Path, builder: str, byte: str
) -> dict[str, object]:
    return _capture(
        [
            "docker",
            "buildx",
            "build",
            "--builder",
            builder,
            "--platform",
            "linux/arm64",
            "--no-cache",
            "--provenance=false",
            "--build-arg",
            f"SOURCE_DATE_EPOCH={EPOCH}",
            *[
                item
                for name in PROXY_ENV_NAMES
                for item in ("--build-arg", f"{name}=")
            ],
            "--tag",
            ARM64_RUNTIME_IMAGE_REF,
            "--output",
            f"type=oci,name={ARM64_RUNTIME_IMAGE_REF},dest={archive},rewrite-timestamp=true",
            "-f",
            str(root / "deploy/console.arm64.Dockerfile"),
            str(root),
        ],
        byte,
    )


def _source_capture() -> dict[str, object]:
    return {
        "host_uname": "Linux dgx 6.11 aarch64 GNU/Linux",
        "commands": [
            _capture(["git", "rev-parse", "HEAD"], "d"),
            _capture(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                "e",
            ),
            _capture(["uname", "-a"], "f"),
        ],
    }


def _scanner_capture() -> dict[str, object]:
    version = "Version: 0.72.0\n"
    metadata = json.dumps(
        {
            "Version": 2,
            "UpdatedAt": "1970-01-12T12:00:00Z",
            "NextUpdate": "1970-01-13T12:00:00Z",
            "DownloadedAt": "1970-01-12T12:30:00Z",
        }
    )
    database_manifest = b'{"schemaVersion":2,"config":{},"layers":[]}\n'
    database_digest = "sha256:" + hashlib.sha256(database_manifest).hexdigest()
    runtime_inspect = json.dumps(
        [
            {
                "Id": RUNTIME_MANIFEST_DIGEST,
                "Descriptor": {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": RUNTIME_MANIFEST_DIGEST,
                    "size": RUNTIME_MANIFEST_SIZE,
                },
            }
        ],
        separators=(",", ":"),
    )
    def hardened(role: str) -> list[str]:
        network = "bridge" if role == "download_database" else "none"
        tmpfs = (
            "/tmp:rw,noexec,nosuid,size=256m"
            if role == "download_database"
            else "/tmp:rw,noexec,nosuid,size=32m"
        )
        return [
            "docker", "run", "--rm", "--platform", "linux/arm64",
            "--network", network, "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--tmpfs", tmpfs,
            "--env", "HOME=/tmp",
            *[
                item
                for name in PROXY_ENV_NAMES
                for item in ("--env", f"{name}=")
            ],
            "--user", "1000:1000",
        ]
    scan_flags = [
        "--skip-db-update", "--skip-java-db-update", "--skip-check-update",
        "--skip-vex-repo-update", "--disable-telemetry", "--offline-scan",
        "--scanners", "vuln",
    ]
    return {
        "version": {
            **_capture(hardened("version") + ["trivy-pinned", "--version"], "5"),
            "stdout_sha256": hashlib.sha256(version.encode()).hexdigest(),
        },
        "download_database": _capture(
            hardened("download_database") + ["trivy-pinned", "--download-db-only"]
        ),
        "database": {
            **_capture(
                [
                    *hardened("database"), "trivy-pinned", "version",
                    "--format", "json", "--cache-dir", "/cache",
                ],
                "7",
            ),
            "stdout_sha256": hashlib.sha256(metadata.encode()).hexdigest(),
        },
        "base_scan": _capture(
            [
                *hardened("base_scan"), "trivy-pinned", "image", *scan_flags,
                "--input", "/input/base.tar",
            ],
            "9",
        ),
        "runtime_scan": _capture(
            [
                *hardened("runtime_scan"), "trivy-pinned", "image", *scan_flags,
                "--input", "/input/runtime.tar",
            ],
            "b",
        ),
        "scanner_version_stdout": version,
        "database_metadata_stdout": metadata,
        "database_image_digest": database_digest,
        "database_registry": {
            **_capture(
                ["docker", "buildx", "imagetools", "inspect", "public.ecr.aws/aquasecurity/trivy-db:2", "--raw"]
            ),
            "stdout_sha256": database_digest.split(":", 1)[1],
        },
        "scanner_registry": _capture(
            ["docker", "buildx", "imagetools", "inspect", "public.ecr.aws/aquasecurity/trivy@sha256:cffe3f5161a47a6823fbd23d985795b3ed72a4c806da4c4df16266c02accdd6f", "--raw"]
        ),
        "host_uid": _capture(["id", "-u"]),
        "host_gid": _capture(["id", "-g"]),
        "base_image_inspect": _capture(
            ["docker", "image", "inspect", BASE_REF]
        ),
        "runtime_image_inspect": {
            **_capture(["docker", "image", "inspect", ARM64_RUNTIME_IMAGE_REF]),
            "stdout_sha256": hashlib.sha256(runtime_inspect.encode()).hexdigest(),
        },
        "runtime_image_inspect_stdout": runtime_inspect,
        "runtime_image_load": _capture(
            [
                "docker",
                "load",
                "--platform",
                "linux/arm64",
                "--input",
                "/tmp/console-a.oci.tar",
            ]
        ),
        "loaded_oci_archive_sha256": "3" * 64,
        "base_image_save": _capture(
            ["docker", "image", "save", "--output", "/tmp/base.tar", BASE_REF]
        ),
        "runtime_image_save": _capture(
            ["docker", "image", "save", "--output", "/tmp/runtime.tar", ARM64_RUNTIME_IMAGE_REF]
        ),
        "base_archive_sha256": "1" * 64,
        "runtime_archive_sha256": "2" * 64,
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
    dockerfile = root / "deploy/console.arm64.Dockerfile"
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text(
        f"ARG PYTHON_IMAGE={BASE_REF}\n"
        "ARG PIP_VERSION=26.1.2\n"
        f"ARG SOURCE_DATE_EPOCH={EPOCH}\n"
        "FROM ${PYTHON_IMAGE} AS builder\n"
        'ARG TARGETARCH\nRUN test "${TARGETARCH}" = "arm64"\n'
        "FROM ${PYTHON_IMAGE}\n"
        'ARG TARGETARCH\nRUN test "${TARGETARCH}" = "arm64"\n',
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("demo==1.0\n", encoding="utf-8")
    (root / ".dockerignore").write_text("key/\ndata/\nlive/\n", encoding="utf-8")


def _add_tar_member(
    archive: tarfile.TarFile, name: str, content: bytes, epoch: int
) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(content)
    member.mtime = epoch
    member.mode = 0o644
    archive.addfile(member, io.BytesIO(content))


def _write_oci(
    path: Path,
    *,
    architecture: str = "arm64",
    layer_content: bytes = b"arm64-layer\n",
) -> None:
    compressed_layer = gzip.compress(layer_content, mtime=0)
    layer_digest = _digest(compressed_layer)
    config = _json_bytes(
        {
            "architecture": architecture,
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
                    "platform": {"architecture": architecture, "os": "linux"},
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


def _reproducibility(root: Path) -> dict[str, object]:
    _write_recipe(root)
    first = root / "first-arm64.oci.tar"
    second = root / "second-arm64.oci.tar"
    _write_oci(first)
    _write_oci(second)
    return build_arm64_container_reproducibility_evidence(
        root,
        archive_a=first,
        archive_b=second,
        observed_at=EPOCH,
        build_tool=BUILD_TOOL,
        source_commit=SOURCE_COMMIT,
        source_clean=True,
        source_capture=_source_capture(),
        build_captures=[
            _build_capture(root, first, "quantos-arm64-evidence-builder-a", "1"),
            _build_capture(root, second, "quantos-arm64-evidence-builder-b", "2"),
        ],
    )


def _vulnerability(identifier: str, severity: str = "MEDIUM") -> dict[str, str]:
    return {
        "VulnerabilityID": identifier,
        "PkgName": "demo-package",
        "InstalledVersion": "1.0",
        "FixedVersion": "1.1",
        "Status": "fixed",
        "Severity": severity,
    }


def _trivy_report(
    artifact: str,
    image_id: str,
    vulnerabilities: list[dict[str, str]] | None = None,
    *,
    architecture: str = "arm64",
    archive_name: str | None = None,
) -> dict[str, object]:
    repo_digest = artifact if "@" in artifact else f"quantos-console@{image_id}"
    scanned_artifact = archive_name or artifact
    return {
        "SchemaVersion": 2,
        "Trivy": {"Version": "0.72.0"},
        "CreatedAt": "1970-01-12T13:45:00Z",
        "ArtifactName": scanned_artifact,
        "ArtifactType": "container_image",
        "Metadata": {
            "ImageID": image_id,
            "RepoDigests": [] if archive_name else [repo_digest],
            "RepoTags": (
                [artifact]
                if ":" in artifact and "@" not in artifact
                else []
            ),
            "OS": {"Family": "alpine", "Name": "3.24.1"},
            "ImageConfig": {
                "architecture": architecture,
                "os": "linux",
                "config": {"User": "65532:65532"},
            },
        },
        "Results": [
            {
                "Target": f"{scanned_artifact} (alpine 3.24.1)",
                "Class": "os-pkgs",
                "Type": "alpine",
                "Vulnerabilities": vulnerabilities,
            }
        ],
    }


def _audit(
    root: Path,
    *,
    runtime_vulnerabilities: list[dict[str, str]] | None = None,
    omit_optional_archive_metadata: bool = False,
) -> dict[str, object]:
    _write_recipe(root)
    base_report = _trivy_report(BASE_REF, BASE_ID, archive_name="/input/base.tar")
    runtime_report = _trivy_report(
        ARM64_RUNTIME_IMAGE_REF,
        RUNTIME_DIGEST,
        runtime_vulnerabilities,
        archive_name="/input/runtime.tar",
    )
    if omit_optional_archive_metadata:
        base_report["Metadata"].pop("RepoDigests")
        base_report["Metadata"].pop("RepoTags")
        runtime_report["Metadata"].pop("RepoDigests")
    base_raw = json.dumps(base_report)
    runtime_raw = json.dumps(runtime_report)
    scanner_capture = _scanner_capture()
    scanner_capture["base_scan"]["stdout_sha256"] = hashlib.sha256(
        base_raw.encode()
    ).hexdigest()
    scanner_capture["runtime_scan"]["stdout_sha256"] = hashlib.sha256(
        runtime_raw.encode()
    ).hexdigest()
    return build_arm64_container_audit_evidence(
        root,
        base_report=base_report,
        runtime_report=runtime_report,
        observed_at=EPOCH,
        database_updated_at="1970-01-12T12:00:00Z",
        database_next_update="1970-01-13T12:00:00Z",
        database_downloaded_at="1970-01-12T12:30:00Z",
        database_image_digest=scanner_capture["database_image_digest"],
        source_commit=SOURCE_COMMIT,
        source_clean=True,
        source_capture=_source_capture(),
        scanner_capture=scanner_capture,
        base_report_raw=base_raw,
        runtime_report_raw=runtime_raw,
        runtime_image_config_digest=RUNTIME_CONFIG_DIGEST,
        runtime_image_manifest_digest=RUNTIME_MANIFEST_DIGEST,
        runtime_image_manifest_size=RUNTIME_MANIFEST_SIZE,
    )


def _smoke_observation() -> dict[str, object]:
    image_environment = [
        "LIVE_TRADING_ENABLED=false",
        "REAL_BROKER_ENABLED=false",
        "ALLOW_ORDER_SUBMISSION=false",
        "ALLOW_AI_DIRECT_TRADING=false",
        "REQUIRE_HUMAN_APPROVAL=true",
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONUNBUFFERED=1",
    ]
    container_environment = [
        *image_environment,
        *(f"{name}=" for name in PROXY_ENV_NAMES),
    ]
    smoke_run_argv = [
        "docker", "run", "--rm", "--detach", "--name",
        "quantos-arm64-console-smoke", "--network", "none", "--read-only",
        "--user", "65532:65532", "--cap-drop", "ALL", "--security-opt",
        "no-new-privileges:true", "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=32m",
        *[
            item
            for setting in (
                "LIVE_TRADING_ENABLED=false",
                "REAL_BROKER_ENABLED=false",
                "ALLOW_ORDER_SUBMISSION=false",
                "ALLOW_AI_DIRECT_TRADING=false",
                "REQUIRE_HUMAN_APPROVAL=true",
                *(f"{name}=" for name in PROXY_ENV_NAMES),
            )
            for item in ("--env", setting)
        ],
        ARM64_RUNTIME_IMAGE_REF,
    ]
    ports = {"8400/tcp": None}
    return {
        "schema_version": 3,
        "observed_at": EPOCH,
        "container_inspect": {
            "Image": RUNTIME_MANIFEST_DIGEST,
            "ImageManifestDescriptor": {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": RUNTIME_MANIFEST_DIGEST,
                "size": RUNTIME_MANIFEST_SIZE,
            },
            "State": {"Status": "running", "Running": True},
            "Config": {
                "Image": ARM64_RUNTIME_IMAGE_REF,
                "User": "65532:65532",
                "Cmd": [
                    "python",
                    "-m",
                    "uvicorn",
                    "services.console.main:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8400",
                ],
                "Env": container_environment,
                "ExposedPorts": {"8400/tcp": {}},
            },
            "HostConfig": {
                "ReadonlyRootfs": True,
                "Privileged": False,
                "AutoRemove": True,
                "CapAdd": None,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=33554432"},
                "Binds": None,
                "NetworkMode": "none",
                "PortBindings": {},
            },
            "Mounts": [],
            "NetworkSettings": {"Ports": ports},
        },
        "image_inspect": {
            "Id": RUNTIME_MANIFEST_DIGEST,
            "Descriptor": {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": RUNTIME_MANIFEST_DIGEST,
                "size": RUNTIME_MANIFEST_SIZE,
            },
            "Architecture": "arm64",
            "Os": "linux",
            "RepoTags": [ARM64_RUNTIME_IMAGE_REF],
            "Config": {
                "User": "65532:65532",
                "Cmd": [
                    "python",
                    "-m",
                    "uvicorn",
                    "services.console.main:app",
                    "--host",
                    "0.0.0.0",
                    "--port",
                    "8400",
                ],
                "Env": image_environment,
                "ExposedPorts": {"8400/tcp": {}},
            },
        },
        "health_request": {
            "method": "GET",
            "url": "http://127.0.0.1:8400/health",
            "status_code": 200,
            "probe_scope": "CONTAINER_LOOPBACK",
        },
        "health_response": {
            "status": "healthy",
            "service": "research-console-api",
            "mode": "READ_ONLY",
        },
        "capture": {
            "commands": [
                {
                    "argv": smoke_run_argv,
                    "exit_code": 0,
                    "stdout_sha256": "5" * 64,
                    "stderr_sha256": "6" * 64,
                },
                {
                    "argv": ["docker", "inspect", "quantos-arm64-console-smoke"],
                    "exit_code": 0,
                    "stdout_sha256": "7" * 64,
                    "stderr_sha256": "8" * 64,
                },
                {
                    "argv": ["docker", "image", "inspect", ARM64_RUNTIME_IMAGE_REF],
                    "exit_code": 0,
                    "stdout_sha256": "9" * 64,
                    "stderr_sha256": "a" * 64,
                },
                {
                    "argv": [
                        "docker",
                        "exec",
                        "quantos-arm64-console-smoke",
                        "python",
                        "-c",
                        "import json,urllib.request;r=urllib.request.urlopen('http://127.0.0.1:8400/health',timeout=5);print(json.dumps({'body':json.loads(r.read().decode('utf-8')),'status_code':r.status},sort_keys=True,separators=(',',':')))",
                    ],
                    "exit_code": 0,
                    "stdout_sha256": "b" * 64,
                    "stderr_sha256": "c" * 64,
                },
            ],
            "host_uname": "Linux dgx 6.11 aarch64 GNU/Linux",
            "source_commit": SOURCE_COMMIT,
            "source_clean": True,
        },
        "runtime_image_config_digest": RUNTIME_CONFIG_DIGEST,
        "runtime_image_manifest_digest": RUNTIME_MANIFEST_DIGEST,
    }


def test_arm64_reproducibility_is_separate_recipe_bound_and_byte_identical(
    tmp_path: Path,
) -> None:
    evidence = _reproducibility(tmp_path)
    assert evidence["target_platform"] == "linux/arm64"
    assert evidence["dockerfile"]["path"] == "deploy/console.arm64.Dockerfile"
    assert evidence["image"]["architecture"] == "arm64"
    assert evidence["builds"][0]["archive_sha256"] == evidence["builds"][1][
        "archive_sha256"
    ]
    summary = verify_arm64_container_reproducibility_evidence(evidence, tmp_path)
    assert summary["manifest_digest"] == evidence["image"]["manifest_digest"]
    assert summary["verdict"] == "PASS"


def test_arm64_reproducibility_rejects_amd64_or_nonidentical_archive(
    tmp_path: Path,
) -> None:
    _write_recipe(tmp_path)
    first = tmp_path / "first.oci.tar"
    second = tmp_path / "second.oci.tar"
    _write_oci(first)
    _write_oci(second, architecture="amd64")
    with pytest.raises(Arm64ConsoleEvidenceError):
        build_arm64_container_reproducibility_evidence(
            tmp_path,
            archive_a=first,
            archive_b=second,
            observed_at=EPOCH,
            build_tool=BUILD_TOOL,
            source_commit=SOURCE_COMMIT,
            source_clean=True,
            source_capture=_source_capture(),
                build_captures=[
                    _build_capture(tmp_path, first, "quantos-arm64-evidence-builder-a", "1"),
                    _build_capture(tmp_path, second, "quantos-arm64-evidence-builder-b", "2"),
            ],
        )

    _write_oci(second, layer_content=b"different\n")
    with pytest.raises(Arm64ConsoleEvidenceError, match="byte-identical"):
        build_arm64_container_reproducibility_evidence(
            tmp_path,
            archive_a=first,
            archive_b=second,
            observed_at=EPOCH,
            build_tool=BUILD_TOOL,
            source_commit=SOURCE_COMMIT,
            source_clean=True,
            source_capture=_source_capture(),
                build_captures=[
                    _build_capture(tmp_path, first, "quantos-arm64-evidence-builder-a", "1"),
                    _build_capture(tmp_path, second, "quantos-arm64-evidence-builder-b", "2"),
            ],
        )


def test_arm64_reproducibility_rejects_recipe_tamper(tmp_path: Path) -> None:
    evidence = _reproducibility(tmp_path)
    path = tmp_path / "deploy/console.arm64.Dockerfile"
    path.write_text(path.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    with pytest.raises(Arm64ConsoleEvidenceError, match="Dockerfile"):
        verify_arm64_container_reproducibility_evidence(evidence, tmp_path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda argv: argv.__delitem__(slice(8, 10)),
        lambda argv: argv.extend(["--build-arg", "HTTP_PROXY="]),
        lambda argv: argv.__setitem__(5, "SOURCE_DATE_EPOCH=999999"),
        lambda argv: argv.__setitem__(7, "HTTP_PROXY=http://secret.invalid"),
        lambda argv: argv.extend(["--build-arg", "UNEXPECTED_PROXY"]),
        lambda argv: argv.__setitem__(6, "--build-arg=HTTP_PROXY="),
    ],
)
def test_arm64_reproducibility_rejects_noncanonical_proxy_build_args(
    tmp_path: Path, mutate: object
) -> None:
    _write_recipe(tmp_path)
    first = tmp_path / "first.oci.tar"
    second = tmp_path / "second.oci.tar"
    _write_oci(first)
    _write_oci(second)
    captures = [
        _build_capture(tmp_path, first, "quantos-arm64-evidence-builder-a", "1"),
        _build_capture(tmp_path, second, "quantos-arm64-evidence-builder-b", "2"),
    ]
    mutate(captures[0]["argv"])  # type: ignore[operator]

    with pytest.raises(Arm64ConsoleEvidenceError, match="cold builds"):
        build_arm64_container_reproducibility_evidence(
            tmp_path,
            archive_a=first,
            archive_b=second,
            observed_at=EPOCH,
            build_tool=BUILD_TOOL,
            source_commit=SOURCE_COMMIT,
            source_clean=True,
            source_capture=_source_capture(),
            build_captures=captures,
        )


@pytest.mark.parametrize(
    "declaration",
    [
        "ARG http_proxy\n",
        "ENV FOO=bar \\\n    Http_Proxy=http://secret.invalid\n",
    ],
)
def test_arm64_recipe_rejects_proxy_arg_or_env_declaration(
    tmp_path: Path, declaration: str
) -> None:
    evidence = _reproducibility(tmp_path)
    assert (
        "PROXY_ENV_EXPLICITLY_CLEARED_NETWORK_PATH_NOT_ATTESTED"
        in evidence["limitations"]
    )
    path = tmp_path / "deploy/console.arm64.Dockerfile"
    path.write_text(path.read_text(encoding="utf-8") + declaration, encoding="utf-8")

    with pytest.raises(Arm64ConsoleEvidenceError, match="proxy"):
        verify_arm64_container_reproducibility_evidence(evidence, tmp_path)


def test_arm64_audit_binds_native_reports_scanner_and_zero_runtime_findings(
    tmp_path: Path,
) -> None:
    evidence = _audit(tmp_path)
    assert evidence["platform"] == "linux/arm64"
    assert evidence["dockerfile_ref"] == "deploy/console.arm64.Dockerfile"
    assert evidence["runtime_vulnerability_counts"]["total"] == 0
    assert evidence["scanner_image_ref"].startswith(
        "public.ecr.aws/aquasecurity/trivy@sha256:"
    )
    summary = verify_arm64_container_audit_evidence(evidence, tmp_path)
    assert summary["runtime_image_config_digest"] == RUNTIME_CONFIG_DIGEST
    assert summary["runtime_image_manifest_digest"] == RUNTIME_MANIFEST_DIGEST
    assert summary["verdict"] == "PASS"


def test_arm64_audit_accepts_missing_optional_archive_repository_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import generate_arm64_container_audit_evidence as audit_cli

    evidence = _audit(tmp_path, omit_optional_archive_metadata=True)
    assert "RepoDigests" not in evidence["base_report"]["Metadata"]
    assert "RepoTags" not in evidence["base_report"]["Metadata"]
    assert "RepoDigests" not in evidence["runtime_report"]["Metadata"]
    assert evidence["runtime_report"]["Metadata"]["RepoTags"] == [
        ARM64_RUNTIME_IMAGE_REF
    ]
    assert verify_arm64_container_audit_evidence(evidence, tmp_path)["verdict"] == "PASS"

    output = tmp_path / "container_trivy_arm64_latest.json"
    output.write_text(json.dumps(evidence), encoding="utf-8")
    monkeypatch.setattr(audit_cli, "resolve_canonical_source_anchor", lambda root: SOURCE_COMMIT)
    monkeypatch.setattr(audit_cli.time, "time", lambda: EPOCH)
    assert audit_cli.main(
        [
            "--repo-root",
            str(tmp_path),
            "--output",
            str(output),
            "--max-age-seconds",
            "60",
            "--check",
        ]
    ) == 0


@pytest.mark.parametrize(
    ("report_name", "field", "value"),
    [
        ("base_report", "RepoDigests", [f"base@{BASE_DIGEST}"]),
        ("runtime_report", "RepoDigests", [f"runtime@{RUNTIME_CONFIG_DIGEST}"]),
        ("base_report", "RepoDigests", "not-a-list"),
        ("runtime_report", "RepoDigests", {}),
        ("base_report", "RepoTags", ["unexpected:latest"]),
        ("base_report", "RepoTags", "not-a-list"),
    ],
)
def test_arm64_audit_rejects_nonempty_or_wrong_type_archive_repository_metadata(
    tmp_path: Path, report_name: str, field: str, value: object
) -> None:
    evidence = _audit(tmp_path)
    evidence[report_name]["Metadata"][field] = value
    raw_name = report_name.replace("report", "report_raw")
    capture_name = "base_scan" if report_name == "base_report" else "runtime_scan"
    raw = json.dumps(evidence[report_name])
    evidence[raw_name] = raw
    evidence["scanner_capture"][capture_name]["stdout_sha256"] = hashlib.sha256(
        raw.encode()
    ).hexdigest()

    with pytest.raises(Arm64ConsoleEvidenceError):
        verify_arm64_container_audit_evidence(evidence, tmp_path)


def test_arm64_audit_rejects_wrong_architecture_or_runtime_finding(
    tmp_path: Path,
) -> None:
    _write_recipe(tmp_path)
    bad_base = _trivy_report(BASE_REF, BASE_ID, architecture="amd64")
    runtime = _trivy_report(ARM64_RUNTIME_IMAGE_REF, RUNTIME_DIGEST)
    bad_base_raw = json.dumps(bad_base)
    runtime_raw = json.dumps(runtime)
    scanner_capture = _scanner_capture()
    scanner_capture["base_scan"]["stdout_sha256"] = hashlib.sha256(
        bad_base_raw.encode()
    ).hexdigest()
    scanner_capture["runtime_scan"]["stdout_sha256"] = hashlib.sha256(
        runtime_raw.encode()
    ).hexdigest()
    with pytest.raises(Arm64ConsoleEvidenceError, match="architecture"):
        build_arm64_container_audit_evidence(
            tmp_path,
            base_report=bad_base,
            runtime_report=runtime,
            observed_at=EPOCH,
            database_updated_at="1970-01-12T12:00:00Z",
            database_next_update="1970-01-13T12:00:00Z",
                database_downloaded_at="1970-01-12T12:30:00Z",
                database_image_digest=scanner_capture["database_image_digest"],
                source_commit=SOURCE_COMMIT,
                source_clean=True,
                source_capture=_source_capture(),
                scanner_capture=scanner_capture,
                base_report_raw=bad_base_raw,
                runtime_report_raw=runtime_raw,
                runtime_image_config_digest=RUNTIME_CONFIG_DIGEST,
                runtime_image_manifest_digest=RUNTIME_MANIFEST_DIGEST,
                runtime_image_manifest_size=RUNTIME_MANIFEST_SIZE,
        )
    failed = _audit(
        tmp_path,
        runtime_vulnerabilities=[_vulnerability("CVE-DEMO", "LOW")],
    )
    assert failed["verdict"] == "FAIL"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_descriptor",
        "wrong_descriptor_digest",
        "wrong_descriptor_media_type",
        "wrong_descriptor_size",
        "image_id_is_config",
        "trivy_image_id_is_manifest",
    ],
)
def test_arm64_audit_rejects_cross_domain_or_unverified_load(
    tmp_path: Path, mutation: str
) -> None:
    evidence = _audit(tmp_path)
    capture = evidence["scanner_capture"]
    inspect_value = json.loads(capture["runtime_image_inspect_stdout"])
    if mutation == "missing_descriptor":
        del inspect_value[0]["Descriptor"]
    elif mutation == "wrong_descriptor_digest":
        inspect_value[0]["Descriptor"]["digest"] = RUNTIME_CONFIG_DIGEST
    elif mutation == "wrong_descriptor_media_type":
        inspect_value[0]["Descriptor"]["mediaType"] = (
            "application/vnd.docker.distribution.manifest.v2+json"
        )
    elif mutation == "wrong_descriptor_size":
        inspect_value[0]["Descriptor"]["size"] = 0
    elif mutation == "image_id_is_config":
        inspect_value[0]["Id"] = RUNTIME_CONFIG_DIGEST
    elif mutation == "trivy_image_id_is_manifest":
        evidence["runtime_report"]["Metadata"]["ImageID"] = RUNTIME_MANIFEST_DIGEST
        evidence["runtime_report_raw"] = json.dumps(evidence["runtime_report"])
        capture["runtime_scan"]["stdout_sha256"] = hashlib.sha256(
            evidence["runtime_report_raw"].encode()
        ).hexdigest()
    if mutation.startswith(("missing_", "wrong_", "image_id_")):
        raw = json.dumps(inspect_value, separators=(",", ":"))
        capture["runtime_image_inspect_stdout"] = raw
        capture["runtime_image_inspect"]["stdout_sha256"] = hashlib.sha256(
            raw.encode()
        ).hexdigest()

    with pytest.raises(Arm64ConsoleEvidenceError):
        verify_arm64_container_audit_evidence(evidence, tmp_path)


@pytest.mark.parametrize(
    ("capture_name", "mutation"),
    [
        ("download_database", "missing_home"),
        ("version", "missing_proxy"),
        ("download_database", "duplicate_home"),
        ("download_database", "proxy_name"),
        ("download_database", "duplicate_proxy"),
        ("download_database", "proxy_value"),
        ("download_database", "unexpected"),
        ("download_database", "joined"),
        ("download_database", "short"),
        ("base_scan", "offline_proxy"),
        ("database", "wrong_network"),
        ("download_database", "wrong_network"),
    ],
)
def test_arm64_audit_rejects_noncanonical_proxy_environment_receipts(
    tmp_path: Path, capture_name: str, mutation: str
) -> None:
    evidence = _audit(tmp_path)
    argv = evidence["scanner_capture"][capture_name]["argv"]
    if mutation == "missing_home":
        index = argv.index("HOME=/tmp")
        del argv[index - 1 : index + 1]
    elif mutation == "missing_proxy":
        index = argv.index("HTTP_PROXY=")
        del argv[index - 1 : index + 1]
    elif mutation == "duplicate_home":
        argv.extend(["--env", "HOME=/tmp"])
    elif mutation == "proxy_name":
        argv.extend(["--env", "HTTP_PROXY"])
    elif mutation == "duplicate_proxy":
        argv.extend(["--env", "HTTP_PROXY="])
    elif mutation == "proxy_value":
        argv[argv.index("HTTP_PROXY=")] = "HTTP_PROXY=http://secret.invalid"
    elif mutation == "unexpected":
        argv.extend(["--env", "UNEXPECTED=value"])
    elif mutation == "joined":
        argv.extend(["--env=HTTP_PROXY="])
    elif mutation == "short":
        argv.extend(["-e", "HTTP_PROXY="])
    elif mutation == "offline_proxy":
        argv[argv.index("HTTP_PROXY=")] = "HTTP_PROXY"
    else:
        index = argv.index("--network") + 1
        argv[index] = "none" if argv[index] == "bridge" else "bridge"

    with pytest.raises(Arm64ConsoleEvidenceError, match="scanner capture"):
        verify_arm64_container_audit_evidence(evidence, tmp_path)


@pytest.mark.parametrize(
    ("capture_name", "mutation"),
    [
        ("download_database", "download_32m"),
        ("version", "offline_256m"),
        ("database", "offline_256m"),
        ("base_scan", "offline_256m"),
        ("runtime_scan", "offline_256m"),
        ("download_database", "extra_tmpfs"),
        ("base_scan", "duplicate_tmpfs"),
        ("runtime_scan", "joined_tmpfs"),
    ],
)
def test_arm64_audit_rejects_noncanonical_role_tmpfs_receipts(
    tmp_path: Path, capture_name: str, mutation: str
) -> None:
    evidence = _audit(tmp_path)
    argv = evidence["scanner_capture"][capture_name]["argv"]
    tmpfs_index = argv.index("--tmpfs")
    canonical = argv[tmpfs_index + 1]
    if mutation == "download_32m":
        argv[tmpfs_index + 1] = "/tmp:rw,noexec,nosuid,size=32m"
    elif mutation == "offline_256m":
        argv[tmpfs_index + 1] = "/tmp:rw,noexec,nosuid,size=256m"
    elif mutation == "extra_tmpfs":
        argv.extend(["--tmpfs", "/work:rw,noexec,nosuid,size=1m"])
    elif mutation == "duplicate_tmpfs":
        argv.extend(["--tmpfs", canonical])
    else:
        argv[tmpfs_index : tmpfs_index + 2] = [f"--tmpfs={canonical}"]

    with pytest.raises(Arm64ConsoleEvidenceError, match="scanner capture"):
        verify_arm64_container_audit_evidence(evidence, tmp_path)


def test_arm64_smoke_requires_read_only_loopback_non_root_and_no_capability(
    tmp_path: Path,
) -> None:
    _write_recipe(tmp_path)
    evidence = build_arm64_console_smoke_evidence(
        tmp_path,
        observation=_smoke_observation(),
        source_commit=SOURCE_COMMIT,
        source_clean=True,
        source_capture=_source_capture(),
    )
    assert evidence["verdict"] == "PASS"
    assert evidence["runtime_image_config_digest"] == RUNTIME_CONFIG_DIGEST
    assert evidence["runtime_image_manifest_digest"] == RUNTIME_MANIFEST_DIGEST
    assert evidence["schema_version"] == 3
    container_env = evidence["observation"]["container_inspect"]["Config"]["Env"]
    image_env = evidence["observation"]["image_inspect"]["Config"]["Env"]
    assert all(f"{name}=" in container_env for name in PROXY_ENV_NAMES)
    assert all(
        not item.startswith(tuple(f"{name}=" for name in PROXY_ENV_NAMES))
        for item in image_env
    )
    assert verify_arm64_console_smoke_evidence(evidence, tmp_path)["verdict"] == "PASS"


@pytest.mark.parametrize(
    "mutation",
    ["missing", "valueless", "nonempty", "duplicate", "extra", "joined", "short"],
)
def test_arm64_smoke_rejects_noncanonical_run_environment_receipt(
    tmp_path: Path, mutation: str
) -> None:
    _write_recipe(tmp_path)
    observation = _smoke_observation()
    argv = observation["capture"]["commands"][0]["argv"]
    index = argv.index("HTTP_PROXY=")
    if mutation == "missing":
        del argv[index - 1 : index + 1]
    elif mutation == "valueless":
        argv[index] = "HTTP_PROXY"
    elif mutation == "nonempty":
        argv[index] = "HTTP_PROXY=http://secret.invalid"
    elif mutation == "duplicate":
        argv[-1:-1] = ["--env", "HTTP_PROXY="]
    elif mutation == "extra":
        argv[-1:-1] = ["--env", "UNEXPECTED=value"]
    elif mutation == "joined":
        argv[-1:-1] = ["--env=HTTP_PROXY="]
    else:
        argv[-1:-1] = ["-e", "HTTP_PROXY="]

    with pytest.raises(Arm64ConsoleEvidenceError, match="capture provenance"):
        build_arm64_console_smoke_evidence(
            tmp_path,
            observation=observation,
            source_commit=SOURCE_COMMIT,
            source_clean=True,
            source_capture=_source_capture(),
        )


def test_arm64_smoke_rejects_legacy_capture_without_run_receipt(
    tmp_path: Path,
) -> None:
    _write_recipe(tmp_path)
    observation = _smoke_observation()
    del observation["capture"]["commands"][0]

    with pytest.raises(Arm64ConsoleEvidenceError, match="capture commands"):
        build_arm64_console_smoke_evidence(
            tmp_path,
            observation=observation,
            source_commit=SOURCE_COMMIT,
            source_clean=True,
            source_capture=_source_capture(),
        )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("container_inspect", "HostConfig", "ReadonlyRootfs"), False),
        (("container_inspect", "HostConfig", "NetworkMode"), "bridge"),
        (
            ("container_inspect", "Config", "Env"),
            ["ALLOW_ORDER_SUBMISSION=true"],
        ),
        (
            ("container_inspect", "Mounts"),
            [{"Source": "/home/user/KEY", "Destination": "/key"}],
        ),
        (("container_inspect", "HostConfig", "CapDrop"), []),
    ],
)
def test_arm64_smoke_fails_closed_on_unsafe_observation(
    tmp_path: Path, path: tuple[object, ...], value: object
) -> None:
    _write_recipe(tmp_path)
    observation = _smoke_observation()
    target: object = observation
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(Arm64ConsoleEvidenceError):
        build_arm64_console_smoke_evidence(
            tmp_path,
            observation=observation,
            source_commit=SOURCE_COMMIT,
            source_clean=True,
            source_capture=_source_capture(),
        )


def test_arm64_smoke_rejects_embedded_observation_tamper(tmp_path: Path) -> None:
    _write_recipe(tmp_path)
    evidence = build_arm64_console_smoke_evidence(
        tmp_path,
        observation=_smoke_observation(),
        source_commit=SOURCE_COMMIT,
        source_clean=True,
        source_capture=_source_capture(),
    )
    tampered = copy.deepcopy(evidence)
    tampered["observation"]["health_response"]["mode"] = "LIVE"
    with pytest.raises(Arm64ConsoleEvidenceError):
        verify_arm64_console_smoke_evidence(tampered, tmp_path)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_descriptor",
        "wrong_descriptor",
        "id_is_config",
        "trivy_config_is_manifest",
        "swap_domains",
    ],
)
def test_arm64_smoke_rejects_config_manifest_domain_confusion(
    tmp_path: Path, mutation: str
) -> None:
    _write_recipe(tmp_path)
    observation = _smoke_observation()
    if mutation == "missing_descriptor":
        del observation["image_inspect"]["Descriptor"]
    elif mutation == "wrong_descriptor":
        observation["image_inspect"]["Descriptor"]["digest"] = (
            RUNTIME_CONFIG_DIGEST
        )
    elif mutation == "id_is_config":
        observation["image_inspect"]["Id"] = RUNTIME_CONFIG_DIGEST
    elif mutation == "trivy_config_is_manifest":
        observation["runtime_image_config_digest"] = RUNTIME_MANIFEST_DIGEST
    else:
        observation["runtime_image_config_digest"], observation[
            "runtime_image_manifest_digest"
        ] = (
            observation["runtime_image_manifest_digest"],
            observation["runtime_image_config_digest"],
        )

    with pytest.raises(Arm64ConsoleEvidenceError):
        build_arm64_console_smoke_evidence(
            tmp_path,
            observation=observation,
            source_commit=SOURCE_COMMIT,
            source_clean=True,
            source_capture=_source_capture(),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "container_missing",
        "container_nonempty",
        "container_duplicate",
        "container_mixed_case",
        "container_extra",
        "image_proxy",
    ],
)
def test_arm64_smoke_rejects_noncanonical_image_or_container_environment(
    tmp_path: Path, mutation: str
) -> None:
    _write_recipe(tmp_path)
    observation = _smoke_observation()
    container_env = list(observation["container_inspect"]["Config"]["Env"])
    image_env = list(observation["image_inspect"]["Config"]["Env"])
    if mutation == "container_missing":
        container_env.remove("HTTP_PROXY=")
    elif mutation == "container_nonempty":
        container_env[container_env.index("HTTP_PROXY=")] = (
            "HTTP_PROXY=http://secret.invalid"
        )
    elif mutation == "container_duplicate":
        container_env.append("HTTP_PROXY=")
    elif mutation == "container_mixed_case":
        container_env.append("Http_Proxy=")
    elif mutation == "container_extra":
        container_env.append("UNEXPECTED=value")
    else:
        image_env.append("HTTP_PROXY=")
    observation["container_inspect"]["Config"]["Env"] = container_env
    observation["image_inspect"]["Config"]["Env"] = image_env

    with pytest.raises(Arm64ConsoleEvidenceError):
        build_arm64_console_smoke_evidence(
            tmp_path,
            observation=observation,
            source_commit=SOURCE_COMMIT,
            source_clean=True,
            source_capture=_source_capture(),
        )


def test_arm64_evidence_clis_are_check_only_without_amd64_paths(
    tmp_path: Path,
) -> None:
    from scripts.generate_arm64_console_smoke_evidence import main as smoke_main
    from scripts.generate_arm64_container_audit_evidence import main as audit_main
    from scripts.generate_arm64_container_reproducibility_evidence import (
        main as reproducibility_main,
    )

    reproducibility_output = tmp_path / "container_build_arm64_latest.json"
    audit_output = tmp_path / "container_trivy_arm64_latest.json"
    smoke_output = tmp_path / "console_arm64_smoke_latest.json"
    for main, output in (
        (reproducibility_main, reproducibility_output),
        (audit_main, audit_output),
        (smoke_main, smoke_output),
    ):
        assert main(["--repo-root", str(tmp_path), "--output", str(output)]) == 1
        assert not output.exists()

    assert "arm64" in reproducibility_output.name
    assert "arm64" in audit_output.name
    assert "arm64" in smoke_output.name


def test_arm64_contracts_bind_clean_canonical_source(tmp_path: Path) -> None:
    evidence = _reproducibility(tmp_path)
    assert evidence["source"]["commit"] == SOURCE_COMMIT
    assert evidence["source"]["clean"] is True
    assert evidence["source"]["commands"] == _source_capture()["commands"]
    bad = copy.deepcopy(evidence)
    bad["source"]["commit"] = "0" * 40
    with pytest.raises(Arm64ConsoleEvidenceError, match="source"):
        verify_arm64_container_reproducibility_evidence(
            bad, tmp_path, expected_source_commit=SOURCE_COMMIT
        )


def test_arm64_contracts_reject_dirty_or_malformed_source(tmp_path: Path) -> None:
    _write_recipe(tmp_path)
    first = tmp_path / "a.oci.tar"
    second = tmp_path / "b.oci.tar"
    _write_oci(first)
    _write_oci(second)
    with pytest.raises(Arm64ConsoleEvidenceError, match="clean"):
        build_arm64_container_reproducibility_evidence(
            tmp_path,
            archive_a=first,
            archive_b=second,
            observed_at=EPOCH,
            build_tool=BUILD_TOOL,
            source_commit=SOURCE_COMMIT,
            source_clean=False,
            source_capture=_source_capture(),
            build_captures=[],
        )


def test_arm64_strict_json_rejects_duplicate_keys_in_cli_and_oci(
    tmp_path: Path,
) -> None:
    from scripts.generate_arm64_console_smoke_evidence import _read_object

    duplicated = tmp_path / "duplicate.json"
    duplicated.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(DuplicateJsonKeyError):
        _read_object(duplicated)

    _write_recipe(tmp_path)
    first = tmp_path / "first.oci.tar"
    second = tmp_path / "second.oci.tar"
    _write_oci(first)
    _write_oci(second)
    with tarfile.open(second, mode="w") as archive:
        _add_tar_member(archive, "oci-layout", b'{"imageLayoutVersion":"1.0.0"}', EPOCH)
        _add_tar_member(
            archive,
            "index.json",
            b'{"schemaVersion":2,"schemaVersion":2,"manifests":[]}',
            EPOCH,
        )
    with pytest.raises((DuplicateJsonKeyError, Arm64ConsoleEvidenceError)):
        build_arm64_container_reproducibility_evidence(
            tmp_path,
            archive_a=first,
            archive_b=second,
            observed_at=EPOCH,
            build_tool=BUILD_TOOL,
            source_commit=SOURCE_COMMIT,
            source_clean=True,
            source_capture=_source_capture(),
            build_captures=[],
        )


def test_arm64_audit_rejects_duplicate_raw_report_keys(tmp_path: Path) -> None:
    evidence = _audit(tmp_path)
    duplicated = evidence["base_report_raw"].replace(
        '"SchemaVersion": 2', '"SchemaVersion": 2, "SchemaVersion": 2', 1
    )
    tampered = copy.deepcopy(evidence)
    tampered["base_report_raw"] = duplicated
    tampered["scanner_capture"]["base_scan"]["stdout_sha256"] = hashlib.sha256(
        duplicated.encode()
    ).hexdigest()
    with pytest.raises(Arm64ConsoleEvidenceError, match="raw Trivy"):
        verify_arm64_container_audit_evidence(tampered, tmp_path)
