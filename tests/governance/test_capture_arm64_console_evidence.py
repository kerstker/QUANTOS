from __future__ import annotations

import hashlib
import inspect
from pathlib import Path

import pytest

from scripts.capture_arm64_console_evidence import (
    BUILDER_A,
    BUILDER_B,
    SMOKE_CONTAINER,
    CaptureError,
    CommandResult,
    SubprocessRunner,
    _assert_evidence_identity,
    _assert_output_boundary,
    _build_argv,
    _database_digest,
    _docker_export_argv,
    _scanner_argv,
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


def test_subprocess_runner_is_permanently_shell_free() -> None:
    source = inspect.getsource(SubprocessRunner.run)
    assert "shell=False" in source
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
    assert "type=oci,name=quantos-console:l2-arm64,dest=" in first[first.index("--output") + 1]
    assert "name=quantos-console:l2-arm64" in first[first.index("--output") + 1]
    assert "rewrite-timestamp=true" in first[first.index("--output") + 1]
    assert first != second


def test_builder_create_uses_reviewed_digest_pinned_buildkit() -> None:
    source = inspect.getsource(capture)
    assert "--driver-opt" in source
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


def test_third_build_is_no_cache_named_docker_archive(tmp_path: Path) -> None:
    argv = _docker_export_argv(
        tmp_path, BUILDER_A, tmp_path / "runtime.docker.tar", 123
    )

    assert argv[:3] == ["docker", "buildx", "build"]
    assert "--no-cache" in argv
    assert argv[argv.index("--platform") + 1] == "linux/arm64"
    assert argv[argv.index("--output") + 1] == (
        f"type=docker,name=quantos-console:l2-arm64,dest={tmp_path / 'runtime.docker.tar'}"
    )


def test_scanner_is_hardened_offline_without_docker_socket(tmp_path: Path) -> None:
    image_tar = tmp_path / "runtime.docker.tar"
    argv = _scanner_argv(
        tmp_path / "cache",
        "image",
        "--input",
        "/input/image.tar",
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


def test_dynamic_database_digest_is_parsed_from_registry_receipt() -> None:
    raw = b'{"schemaVersion":2,"config":{},"layers":[]}\n'
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()

    assert _database_digest(raw) == digest

    with pytest.raises(CaptureError):
        _database_digest(b'{"Descriptor":{}}\n')


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


def test_evidence_identity_requires_same_oci_loaded_audit_and_smoke_digest() -> None:
    digest = "sha256:" + "e" * 64
    reproducibility = {"image": {"config_digest": digest}}
    audit = {"runtime_image_digest": digest}
    smoke = {"runtime_image_config_digest": digest}

    _assert_evidence_identity(reproducibility, audit, smoke, digest)

    with pytest.raises(CaptureError, match="identity"):
        _assert_evidence_identity(
            reproducibility,
            {"runtime_image_digest": "sha256:" + "f" * 64},
            smoke,
            digest,
        )


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


def test_reserved_resource_names_are_fixed_and_unambiguous() -> None:
    assert BUILDER_A == "quantos-arm64-evidence-builder-a"
    assert BUILDER_B == "quantos-arm64-evidence-builder-b"
    assert SMOKE_CONTAINER == "quantos-arm64-console-smoke"
    assert len({BUILDER_A, BUILDER_B, SMOKE_CONTAINER}) == 3


def _capture_repo(root: Path) -> str:
    commit = "a" * 40
    (root / ".git").mkdir()
    dockerfile = root / "deploy/console.arm64.Dockerfile"
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
    tmp_path: Path,
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
    ]
    create_second = [
        "docker", "buildx", "create", "--name", BUILDER_B,
        "--driver", "docker-container", "--platform", "linux/arm64",
        "--driver-opt", "image=moby/buildkit@sha256:59795d5a98a9e12ddf49567fcd1976a30f2d2a60ef469c2f9cedb9e5e3251b25",
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
