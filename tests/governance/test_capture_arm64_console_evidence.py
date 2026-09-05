from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path

import pytest

import scripts.capture_arm64_console_evidence as capture_module
from scripts.capture_arm64_console_evidence import (
    BUILDER_A,
    BUILDER_B,
    PROXY_ENV_NAMES,
    SMOKE_CONTAINER,
    CaptureError,
    CommandResult,
    SubprocessRunner,
    _assert_evidence_identity,
    _assert_output_boundary,
    _build_argv,
    _database_digest,
    _scanner_argv,
    _smoke_run_argv,
    _source_capture,
    _publish_staged_evidence,
    _parse_buildx_inspect_identity,
    capture,
)


class FakeRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = list(results)
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    def run(self, argv: tuple[str, ...], *, cwd: Path) -> CommandResult:
        assert isinstance(argv, tuple)
        self.calls.append((argv, cwd))
        if not self.results:
            raise AssertionError(f"unexpected command: {argv!r}")
        result = self.results.pop(0)
        assert result.argv == argv
        return result


def _result(argv: list[str], *, code: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> CommandResult:
    return CommandResult(tuple(argv), code, stdout, stderr)


def _option_values(argv: list[str], option: str) -> list[str]:
    return [argv[index + 1] for index, item in enumerate(argv[:-1]) if item == option]


def test_subprocess_runner_is_permanently_shell_free() -> None:
    source = inspect.getsource(SubprocessRunner.run)
    assert "shell=False" in source
    assert "timeout=COMMAND_TIMEOUT_SECONDS" in source
    assert "shell=True" not in source
    assert "os.system" not in source
    assert "Popen" not in source


def test_source_capture_requires_exact_clean_canonical_aarch64_checkout(tmp_path: Path) -> None:
    commit = "a" * 40
    commands = [
        ["git", "rev-parse", "HEAD"],
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        ["uname", "-a"],
    ]
    fake = FakeRunner([
        _result(commands[0], stdout=(commit + "\n").encode()),
        _result(commands[1]),
        _result(commands[2], stdout=b"Linux dgx aarch64 GNU/Linux\n"),
    ])

    actual_commit, capture = _source_capture(fake, tmp_path, lambda _root: commit)

    assert actual_commit == commit
    assert [item["argv"] for item in capture["commands"]] == commands
    assert all(item["exit_code"] == 0 for item in capture["commands"])


@pytest.mark.parametrize(
    ("status", "resolved"),
    [(b"?? surprise\n", "a" * 40), (b"", "b" * 40)],
)
def test_source_capture_rejects_dirty_or_noncanonical_checkout(
    tmp_path: Path, status: bytes, resolved: str
) -> None:
    commit = "a" * 40
    fake = FakeRunner([
        _result(["git", "rev-parse", "HEAD"], stdout=(commit + "\n").encode()),
        _result(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            stdout=status,
        ),
        _result(["uname", "-a"], stdout=b"Linux dgx aarch64 GNU/Linux\n"),
    ])

    with pytest.raises(CaptureError):
        _source_capture(fake, tmp_path, lambda _root: resolved)


def test_two_builds_are_exact_no_cache_native_oci_and_independent(tmp_path: Path) -> None:
    first = _build_argv(tmp_path, BUILDER_A, tmp_path / "a.oci.tar", 123)
    second = _build_argv(tmp_path, BUILDER_B, tmp_path / "b.oci.tar", 123)

    assert first[:3] == ["docker", "buildx", "build"]
    assert second[:3] == ["docker", "buildx", "build"]
    assert "--no-cache" in first and "--no-cache" in second
    assert [first[first.index("--builder") + 1], second[second.index("--builder") + 1]] == [
        BUILDER_A,
        BUILDER_B,
    ]
    assert "--platform" in first and first[first.index("--platform") + 1] == "linux/arm64"
    assert "--network" in first and first[first.index("--network") + 1] == "host"
    assert "--network" in second and second[second.index("--network") + 1] == "host"
    assert "type=oci,name=quantos-console:l2-arm64,dest=" in first[first.index("--output") + 1]
    assert "name=quantos-console:l2-arm64" in first[first.index("--output") + 1]
    assert "rewrite-timestamp=true" in first[first.index("--output") + 1]
    expected_build_args = ["SOURCE_DATE_EPOCH=123", *PROXY_ENV_NAMES]
    assert _option_values(first, "--build-arg") == expected_build_args
    assert _option_values(second, "--build-arg") == expected_build_args
    assert all("=" not in value for value in expected_build_args[1:])
    assert "--env" not in first and "-e" not in first
    assert first != second


def test_builder_create_uses_reviewed_digest_pinned_buildkit() -> None:
    source = inspect.getsource(capture)
    assert "--driver-opt" in source
    assert '"network=host"' in source
    from scripts.capture_arm64_console_evidence import BUILDKIT_IMAGE_REF

    assert BUILDKIT_IMAGE_REF.startswith("moby/buildkit@sha256:")


def test_buildx_inspect_parser_accepts_actual_dgx_v031_shape() -> None:
    raw = b"""Name:          quantos-arm64-evidence-builder-a
Driver:        docker-container
Last Activity: 2026-08-13 00:00:00 +0000 UTC

Nodes:
Name:                  quantos-arm64-evidence-builder-a0
Endpoint:              unix:///var/run/docker.sock
Status:                running
BuildKit version:      v0.26.2
Platforms:             linux/arm64*
"""

    assert _parse_buildx_inspect_identity(raw) == (
        "docker-container",
        "v0.26.2",
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"Driver: docker-container\nStatus: stopped\nBuildKit version: v0.26.2\nPlatforms: linux/arm64\n",
        b"Driver: docker-container\nStatus: running\nBuildKit version: v0.26.2\nPlatforms: linux/amd64\n",
        b"Driver: docker-container\nStatus: running\nBuildKit version: v0.26.2\nPlatforms: linux/arm64**\n",
        b"Driver: docker-container\nStatus: running\nStatus: running\nBuildKit version: v0.26.2\nPlatforms: linux/arm64\n",
        b"Driver: docker-container\nStatus: running\nBuildKit version: v0.26.2\nPlatforms: linux/arm64\nPlatforms: linux/arm64\n",
        b"Driver: docker-container\nStatus: running\nBuildKit version: v0.27.1\nPlatforms: linux/arm64\n",
        b"Driver: docker-container\nDriver: docker\nStatus: running\nBuildKit version: v0.26.2\nPlatforms: linux/arm64\n",
        b"Driver: docker-container\nStatus: running\nBuildKit version: v0.26.2\nBuildKit version: v0.27.1\nPlatforms: linux/arm64\n",
    ],
)
def test_buildx_inspect_parser_rejects_nonunique_or_non_arm64_builder(
    raw: bytes,
) -> None:
    with pytest.raises(CaptureError, match="buildx inspect"):
        _parse_buildx_inspect_identity(raw)


def test_capture_has_exactly_two_oci_solves_and_no_docker_exporter() -> None:
    source = inspect.getsource(capture)

    assert source.count("_build_argv(") == 2
    assert "type=docker" not in inspect.getsource(
        __import__(
            "scripts.capture_arm64_console_evidence",
            fromlist=["capture_arm64_console_evidence"],
        )
    )
    assert "_docker_export_argv" not in source
    assert '"--platform",' in source
    assert '"--input",' in source
    assert "str(archive_a)" in source


def test_scanner_is_hardened_offline_without_docker_socket(tmp_path: Path) -> None:
    image_tar = tmp_path / "runtime.docker.tar"
    argv = _scanner_argv(
        tmp_path / "cache",
        "image",
        "--input",
        "/input/image.tar",
        role="runtime_scan",
        input_archive=image_tar,
    )

    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--platform") + 1] == "linux/arm64"
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges:true"
    assert argv[argv.index("--tmpfs") + 1] == "/tmp:rw,noexec,nosuid,size=32m"
    assert f"type=bind,src={tmp_path / 'cache'},dst=/cache" in argv
    assert f"type=bind,src={image_tar},dst=/input/image.tar,readonly" in argv
    assert any("@sha256:" in item for item in argv)
    assert all("docker.sock" not in item for item in argv)
    assert not any("*" in item or "?" in item for item in argv)
    assert _option_values(argv, "--env") == [
        "HOME=/tmp",
        *(f"{name}=" for name in PROXY_ENV_NAMES),
    ]


def test_networked_database_download_inherits_proxy_names_over_host_network(
    tmp_path: Path,
) -> None:
    argv = _scanner_argv(
        tmp_path / "cache",
        "--cache-dir",
        "/cache",
        "image",
        "--download-db-only",
        role="download_database",
    )

    assert argv[argv.index("--network") + 1] == "host"
    assert argv[argv.index("--timeout") + 1] == "45m"
    assert argv[argv.index("--tmpfs") + 1] == (
        "/tmp:rw,noexec,nosuid,size=256m"
    )
    assert _option_values(argv, "--env") == ["HOME=/tmp", *PROXY_ENV_NAMES]
    assert not any(f"{name}=" in argv for name in PROXY_ENV_NAMES)


def test_networked_database_download_can_explicitly_clear_proxy_names(
    tmp_path: Path,
) -> None:
    argv = _scanner_argv(
        tmp_path / "cache",
        "--cache-dir",
        "/cache",
        "image",
        "--download-db-only",
        role="download_database",
        inherit_proxy=False,
    )

    assert argv[argv.index("--network") + 1] == "host"
    assert argv[argv.index("--timeout") + 1] == "45m"
    assert _option_values(argv, "--env") == [
        "HOME=/tmp",
        *(f"{name}=" for name in PROXY_ENV_NAMES),
    ]


@pytest.mark.parametrize(
    "role",
    ["version", "database", "base_scan", "runtime_scan"],
)
def test_non_download_scanner_roles_keep_32m_tmpfs(
    tmp_path: Path, role: str
) -> None:
    argv = _scanner_argv(tmp_path / "cache", "version", role=role)

    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--tmpfs") + 1] == (
        "/tmp:rw,noexec,nosuid,size=32m"
    )


def test_scanner_rejects_unknown_capture_role(tmp_path: Path) -> None:
    with pytest.raises(CaptureError, match="scanner capture role"):
        _scanner_argv(tmp_path / "cache", "version", role="future_networked_role")


def test_smoke_run_clears_proxy_after_exact_safety_environment() -> None:
    argv = _smoke_run_argv()

    assert argv[:3] == ["docker", "run", "--rm"]
    assert argv[argv.index("--tmpfs") + 1] == (
        "/tmp:rw,noexec,nosuid,size=33554432"
    )
    assert _option_values(argv, "--env") == [
        "LIVE_TRADING_ENABLED=false",
        "REAL_BROKER_ENABLED=false",
        "ALLOW_ORDER_SUBMISSION=false",
        "ALLOW_AI_DIRECT_TRADING=false",
        "REQUIRE_HUMAN_APPROVAL=true",
        *(f"{name}=" for name in PROXY_ENV_NAMES),
    ]
    assert argv[argv.index("--network") + 1] == "none"


def test_proxy_values_are_never_read_or_serialized() -> None:
    source = inspect.getsource(__import__(
        "scripts.capture_arm64_console_evidence",
        fromlist=["capture_arm64_console_evidence"],
    ))
    assert "os.environ" not in source
    assert "os.getenv" not in source
    assert "getenv(" not in source


def test_dynamic_database_digest_is_parsed_from_registry_receipt() -> None:
    raw = b'{"schemaVersion":2,"config":{},"layers":[]}\n'
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()

    assert _database_digest(raw) == digest

    with pytest.raises(CaptureError):
        _database_digest(b'{"Descriptor":{}}\n')


def _scanner_preflight_results(
    cache: Path, *, download_code: int = 0, download_stderr: bytes = b""
) -> tuple[list[CommandResult], str]:
    scanner_user = "1000:1001"
    scanner_manifest_raw = (
        '{"manifests":[{"digest":"'
        + capture_module.TRIVY_SCANNER_ARM64_MANIFEST_DIGEST
        + '","platform":{"architecture":"arm64","os":"linux"}}]}'
    ).encode()
    database_manifest_raw = b'{"schemaVersion":2,"config":{},"layers":[]}\n'
    database_digest = "sha256:" + hashlib.sha256(database_manifest_raw).hexdigest()
    database_ref = (
        capture_module.TRIVY_DATABASE_REPOSITORY.rsplit(":", 1)[0]
        + "@"
        + database_digest
    )
    results = [
        _result(["id", "-u"], stdout=b"1000\n"),
        _result(["id", "-g"], stdout=b"1001\n"),
        _result(
            [
                "docker", "buildx", "imagetools", "inspect",
                capture_module.TRIVY_SCANNER_IMAGE_REF,
                "--raw",
            ],
            stdout=scanner_manifest_raw,
        ),
        _result(
            _scanner_argv(
                cache, "version", "--format", "json",
                role="version", user=scanner_user,
            ),
            stdout=b'{"Version":"0.72.0"}',
        ),
        _result(
            [
                "docker", "buildx", "imagetools", "inspect",
                capture_module.TRIVY_DATABASE_REPOSITORY,
                "--raw",
            ],
            stdout=database_manifest_raw,
        ),
        _result(
            _scanner_argv(
                cache,
                "--cache-dir", "/cache", "image", "--download-db-only",
                "--db-repository", database_ref,
                role="download_database", user=scanner_user,
            ),
            code=download_code,
            stderr=download_stderr,
        ),
    ]
    if download_code == 0:
        results.append(
            _result(
                _scanner_argv(
                    cache,
                    "--cache-dir", "/cache", "version", "--format", "json",
                    role="database", user=scanner_user,
                ),
                stdout=(
                    b'{"VulnerabilityDB":{"UpdatedAt":"2026-08-13T00:00:00Z",'
                    b'"NextUpdate":"2026-08-14T00:00:00Z",'
                    b'"DownloadedAt":"2026-08-13T00:01:00Z"}}'
                ),
            )
        )
    return results, database_digest


def test_scanner_database_preflight_is_complete_and_reuses_one_cache(
    tmp_path: Path,
) -> None:
    cache = tmp_path / "trivy-cache"
    results, database_digest = _scanner_preflight_results(cache)
    fake = FakeRunner(results)

    preflight = capture_module._prepare_scanner_database(fake, tmp_path, cache)

    assert cache.is_dir()
    assert preflight.database_digest == database_digest
    assert preflight.database_updated_at == "2026-08-13T00:00:00Z"
    assert preflight.database_next_update == "2026-08-14T00:00:00Z"
    assert preflight.database_downloaded_at == "2026-08-13T00:01:00Z"
    scanner_calls = [argv for argv, _cwd in fake.calls if argv[:2] == ("docker", "run")]
    assert len(scanner_calls) == 3
    assert all(f"type=bind,src={cache},dst=/cache" in argv for argv in scanner_calls)
    assert preflight.scanner_version.receipt()["argv"] == list(scanner_calls[0])
    assert preflight.download_database.receipt()["argv"] == list(scanner_calls[1])
    assert preflight.database.receipt()["argv"] == list(scanner_calls[2])
    assert preflight.database_metadata_raw == (
        '{"VulnerabilityDB":{"UpdatedAt":"2026-08-13T00:00:00Z",'
        '"NextUpdate":"2026-08-14T00:00:00Z",'
        '"DownloadedAt":"2026-08-13T00:01:00Z"}}'
    )


def test_scanner_database_preflight_precedes_builder_creation_and_builds() -> None:
    source = inspect.getsource(capture)

    preflight = source.index("scanner_preflight = _prepare_scanner_database(")
    create = source.index('"docker", "buildx", "create"')
    first_build = source.index("build_a = _run(")

    assert preflight < create < first_build


def test_post_preflight_scans_stay_offline_and_digest_bound() -> None:
    source = inspect.getsource(capture)

    assert '"--skip-db-update"' in source
    assert '"--offline-scan"' in source
    assert 'role="base_scan"' in source
    assert 'role="runtime_scan"' in source
    assert source.count(
        "database_image_digest=scanner_preflight.database_digest"
    ) == 1
    assert source.count(
        '"database_image_digest": scanner_preflight.database_digest'
    ) == 1


def test_output_root_must_be_outside_repository(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _capture_repo(root)

    with pytest.raises(CaptureError, match="outside repo-root"):
        capture(root, root / "evidence-arm64", runner=FakeRunner([]))


def test_output_boundary_rejects_symlink_or_preexisting_final_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "outside"
    root.mkdir()
    output.mkdir()
    (output / "runtime").mkdir()
    final = output / "arm64-console-evidence-bundle"
    final.mkdir()

    with pytest.raises(CaptureError, match="already exists"):
        _assert_output_boundary(root, output)

    final.rmdir()
    target = tmp_path / "target"
    target.mkdir()
    try:
        (output / "runtime/link").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(CaptureError, match="symlink"):
        _assert_output_boundary(root, output)


def _identity_documents() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    config = "sha256:" + "c" * 64
    manifest = "sha256:" + "d" * 64
    archive_sha = "a" * 64
    reproducibility = {
        "image": {
            "config_digest": config,
            "manifest_digest": manifest,
            "manifest_size": 1234,
        },
        "builds": [
            {"archive_name": "console-a.oci.tar", "archive_sha256": archive_sha},
            {"archive_name": "console-b.oci.tar", "archive_sha256": archive_sha},
        ],
    }
    inspect_raw = (
        '[{"Id":"' + manifest + '","Descriptor":{"mediaType":'
        '"application/vnd.oci.image.manifest.v1+json","digest":"'
        + manifest
        + '","size":1234}}]'
    )
    audit = {
        "runtime_image_config_digest": config,
        "runtime_image_manifest_digest": manifest,
        "scanner_capture": {
            "runtime_image_load": {
                "argv": [
                    "docker",
                    "load",
                    "--platform",
                    "linux/arm64",
                    "--input",
                    "/tmp/console-a.oci.tar",
                ],
                "exit_code": 0,
                "stdout_sha256": "0" * 64,
                "stderr_sha256": "0" * 64,
            },
            "loaded_oci_archive_sha256": archive_sha,
            "runtime_image_inspect_stdout": inspect_raw,
        },
    }
    smoke = {
        "runtime_image_config_digest": config,
        "runtime_image_manifest_digest": manifest,
        "observation": {
            "image_inspect": {
                "Id": manifest,
                "Descriptor": {
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": manifest,
                    "size": 1234,
                },
            }
        },
    }
    return reproducibility, audit, smoke


def test_evidence_identity_keeps_config_and_manifest_domains_separate() -> None:
    reproducibility, audit, smoke = _identity_documents()

    _assert_evidence_identity(reproducibility, audit, smoke)

    assert reproducibility["image"]["config_digest"] != reproducibility["image"][
        "manifest_digest"
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "audit_config",
        "smoke_manifest",
        "swap_audit",
        "load_b",
        "archive_drift",
        "descriptor_size",
    ],
)
def test_evidence_identity_rejects_mismatch_swap_or_unverified_load(
    mutation: str,
) -> None:
    reproducibility, audit, smoke = _identity_documents()
    if mutation == "audit_config":
        audit["runtime_image_config_digest"] = "sha256:" + "e" * 64
    elif mutation == "smoke_manifest":
        smoke["runtime_image_manifest_digest"] = "sha256:" + "e" * 64
    elif mutation == "swap_audit":
        audit["runtime_image_config_digest"], audit["runtime_image_manifest_digest"] = (
            audit["runtime_image_manifest_digest"],
            audit["runtime_image_config_digest"],
        )
    elif mutation == "load_b":
        audit["scanner_capture"]["runtime_image_load"]["argv"][-1] = (
            "/tmp/console-b.oci.tar"
        )
    elif mutation == "archive_drift":
        audit["scanner_capture"]["loaded_oci_archive_sha256"] = "f" * 64
    else:
        smoke["observation"]["image_inspect"]["Descriptor"]["size"] = 1235

    with pytest.raises(CaptureError, match="identity|archive|load|descriptor"):
        _assert_evidence_identity(reproducibility, audit, smoke)


def test_scans_bind_exact_archives_and_image_inspect_receipts(tmp_path: Path) -> None:
    source = inspect.getsource(capture)
    assert '["docker", "image", "inspect", base_image]' in source
    assert '["docker", "image", "save", "--output", str(base_archive), base_image]' in source
    assert '"base_archive_sha256"' in source
    assert '"runtime_archive_sha256"' in source
    assert '"base_image_inspect"' in source
    assert '"runtime_image_inspect"' in source
    assert "/input/base.tar" in source
    assert "/input/runtime.tar" in source


def test_owned_runtime_image_is_removed_exactly_on_cleanup() -> None:
    source = inspect.getsource(capture)
    assert "owned_runtime_image" in source
    assert '("docker", "image", "rm", ARM64_RUNTIME_IMAGE_REF)' in source


def test_evidence_is_staged_then_published_as_one_set(tmp_path: Path) -> None:
    output = tmp_path / "output"
    staging = tmp_path / "staging"
    documents = {
        "reproducibility": {"kind": "repro"},
        "audit": {"kind": "audit"},
        "smoke": {"kind": "smoke"},
    }

    paths = _publish_staged_evidence(output, staging, documents)

    assert set(paths) == set(documents)
    assert all(path.is_file() for path in paths.values())
    assert not any(path.is_file() for path in staging.rglob("*"))


def test_final_source_recheck_precedes_evidence_publish() -> None:
    source = inspect.getsource(capture)
    assert source.index("final_commit") < source.index("_publish_staged_evidence")


def test_capture_assembles_smoke_observation_schema_v3() -> None:
    source = inspect.getsource(capture)
    assert source.count('"schema_version": 3') == 1
    assert '"schema_version": 1' not in source


def test_reserved_resource_names_are_fixed_and_unambiguous() -> None:
    assert BUILDER_A == "quantos-arm64-evidence-builder-a"
    assert BUILDER_B == "quantos-arm64-evidence-builder-b"
    assert SMOKE_CONTAINER == "quantos-arm64-console-smoke"
    assert len({BUILDER_A, BUILDER_B, SMOKE_CONTAINER}) == 3


def test_rejected_scan_is_retained_without_publishing_accepted_evidence(tmp_path):
    audit = {'verdict': 'FAIL', 'runtime_report': {'Results': [{'Vulnerabilities': [
        {'VulnerabilityID': 'CVE-FIXTURE', 'PkgName': 'fixture', 'Severity': 'HIGH'}
    ]}]}}
    with pytest.raises(CaptureError, match='rejected audit retained'):
        capture_module._require_clean_audit(audit, tmp_path)
    files = list(tmp_path.glob('rejected-audit-*.json'))
    assert len(files) == 1
    assert json.loads(files[0].read_text()) == audit
    before = files[0].read_bytes()
    with pytest.raises(CaptureError, match='rejected audit retained'):
        capture_module._require_clean_audit(audit, tmp_path)
    assert files[0].read_bytes() == before
    assert not (tmp_path / 'arm64-console-evidence-bundle').exists()


def test_successful_scan_creates_no_rejected_artifact(tmp_path):
    capture_module._require_clean_audit({'verdict': 'PASS'}, tmp_path)
    assert list(tmp_path.iterdir()) == []


def _capture_repo(root: Path) -> str:
    commit = "a" * 40
    (root / ".git").mkdir()
    dockerfile = root / "deploy/console.Dockerfile"
    dockerfile.parent.mkdir()
    dockerfile.write_text(
        "ARG PYTHON_IMAGE=example.invalid/python@sha256:" + "b" * 64 + "\n"
        "ARG SOURCE_DATE_EPOCH=123\n",
        encoding="utf-8",
    )
    return commit


def _source_results(commit: str) -> list[CommandResult]:
    return [
        _result(["git", "rev-parse", "HEAD"], stdout=(commit + "\n").encode()),
        _result(["git", "status", "--porcelain=v1", "--untracked-files=all"]),
        _result(["uname", "-a"], stdout=b"Linux dgx aarch64 GNU/Linux\n"),
    ]


def test_failure_cleanup_is_owned_exact_and_does_not_mask_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "out"
    root.mkdir()
    commit = _capture_repo(root)
    image_check = ["docker", "image", "inspect", "quantos-console:l2-arm64"]
    container_check = ["docker", "container", "inspect", SMOKE_CONTAINER]
    first_check = ["docker", "buildx", "inspect", BUILDER_A]
    second_check = ["docker", "buildx", "inspect", BUILDER_B]
    create_first = [
        "docker", "buildx", "create", "--name", BUILDER_A,
        "--driver", "docker-container", "--platform", "linux/arm64",
        "--driver-opt", "image=moby/buildkit@sha256:59795d5a98a9e12ddf49567fcd1976a30f2d2a60ef469c2f9cedb9e5e3251b25",
        "--driver-opt", "network=host",
        "--driver-opt", "cpu-period=100000",
        "--driver-opt", "cpu-quota=400000",
        "--driver-opt", "memory=16g",
        "--driver-opt", "memory-swap=16g",
    ]
    create_second = [
        "docker", "buildx", "create", "--name", BUILDER_B,
        "--driver", "docker-container", "--platform", "linux/arm64",
        "--driver-opt", "image=moby/buildkit@sha256:59795d5a98a9e12ddf49567fcd1976a30f2d2a60ef469c2f9cedb9e5e3251b25",
        "--driver-opt", "network=host",
        "--driver-opt", "cpu-period=100000",
        "--driver-opt", "cpu-quota=400000",
        "--driver-opt", "memory=16g",
        "--driver-opt", "memory-swap=16g",
    ]
    cleanup_first = ["docker", "buildx", "rm", "--force", BUILDER_A]
    fake = FakeRunner(
        _source_results(commit)
        + [
            _result(image_check, code=1, stderr=b"No such image"),
            _result(container_check, code=1, stderr=b"No such container"),
            _result(first_check, code=1, stderr=b"no builder found"),
            _result(second_check, code=1, stderr=b"no builder found"),
            _result(create_first),
            _result(create_second, code=2, stderr=b"injected create failure"),
            # Even a cleanup failure must not replace the original exception.
            _result(cleanup_first, code=3, stderr=b"injected cleanup failure"),
        ]
    )
    monkeypatch.setattr(
        capture_module,
        "_prepare_scanner_database",
        lambda *_args, **_kwargs: object(),
    )

    expected_stderr_hash = hashlib.sha256(b"injected create failure").hexdigest()
    with pytest.raises(CaptureError, match=expected_stderr_hash):
        capture(
            root,
            output,
            runner=fake,
            source_resolver=lambda _root: commit,
        )

    cleanup_calls = [argv for argv, _cwd in fake.calls if "rm" in argv]
    assert cleanup_calls == [tuple(cleanup_first)]
    assert all("*" not in item and "?" not in item for item in cleanup_calls[0])
    assert not any(path.name.startswith(".arm64-console-capture-") for path in output.iterdir())


def test_preflight_download_failure_creates_no_builder_or_build_and_cleans_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "out"
    root.mkdir()
    commit = _capture_repo(root)
    session = output / ".arm64-console-capture-test"

    def make_session(*, prefix: str, dir: Path) -> str:
        assert prefix == ".arm64-console-capture-"
        assert Path(dir) == output
        session.mkdir()
        return str(session)

    monkeypatch.setattr(capture_module.tempfile, "mkdtemp", make_session)
    absent = [
        _result(
            ["docker", "image", "inspect", "quantos-console:l2-arm64"],
            code=1, stderr=b"No such image",
        ),
        _result(
            ["docker", "container", "inspect", SMOKE_CONTAINER],
            code=1, stderr=b"No such container",
        ),
        _result(
            ["docker", "buildx", "inspect", BUILDER_A],
            code=1, stderr=b"no builder found",
        ),
        _result(
            ["docker", "buildx", "inspect", BUILDER_B],
            code=1, stderr=b"no builder found",
        ),
    ]
    download_error = b"injected database download failure"
    preflight, _database_digest = _scanner_preflight_results(
        session / "trivy-cache",
        download_code=2,
        download_stderr=download_error,
    )
    fake = FakeRunner(_source_results(commit) + absent + preflight)

    with pytest.raises(
        CaptureError, match=hashlib.sha256(download_error).hexdigest()
    ):
        capture(
            root, output, runner=fake,
            source_resolver=lambda _root: commit,
        )

    assert any("--download-db-only" in argv for argv, _cwd in fake.calls)
    assert not any(
        argv[:3] in {
            ("docker", "buildx", "create"),
            ("docker", "buildx", "build"),
        }
        for argv, _cwd in fake.calls
    )
    assert not any(
        argv[:4] == ("docker", "buildx", "rm", "--force")
        for argv, _cwd in fake.calls
    )
    assert not session.exists()
    assert not (output / "arm64-console-evidence-bundle").exists()


def test_preexisting_reserved_resource_is_never_deleted(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    output = tmp_path / "out"
    root.mkdir()
    commit = _capture_repo(root)
    image_check = ["docker", "image", "inspect", "quantos-console:l2-arm64"]
    fake = FakeRunner(
        _source_results(commit)
        + [_result(image_check, stdout=b"[{\"Id\":\"preexisting\"}]")]
    )

    with pytest.raises(CaptureError, match="reserved runtime image already exists"):
        capture(
            root,
            output,
            runner=fake,
            source_resolver=lambda _root: commit,
        )

    assert not any("rm" in argv for argv, _cwd in fake.calls)
    assert not any(path.name.startswith(".arm64-console-capture-") for path in output.iterdir())
