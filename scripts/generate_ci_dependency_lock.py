#!/usr/bin/env python
"""Generate or verify the CPython 3.12 Linux CI dependency lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.ci_reproducibility import (  # noqa: E402
    CiReproducibilityError,
    build_lock_document,
    verify_lock_document,
)


PYTHON_TARGET = "3.12"
PYPI_INDEX = "https://pypi.org/simple"
TARGETS = {
    "linux-amd64": {
        "platform_tag": "ubuntu_24_04_x86_64",
        "pip_platforms": (
            "manylinux_2_28_x86_64",
            "manylinux_2_17_x86_64",
            "manylinux2014_x86_64",
        ),
        "output": "requirements-ci.lock",
    },
    "linux-arm64": {
        "platform_tag": "ubuntu_24_04_aarch64",
        "pip_platforms": (
            "manylinux_2_28_aarch64",
            "manylinux_2_17_aarch64",
            "manylinux2014_aarch64",
        ),
        "output": "requirements-ci-linux-aarch64.lock",
    },
}
SOURCE_FILES = ("requirements.txt", "requirements-dev.txt")


def _source_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
        for name in SOURCE_FILES
    }


def _generate_report(*, pip_platforms: tuple[str, ...], locked_versions: tuple[str, ...] = (), locked_artifacts: tuple[str, ...] = ()) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="quantos-ci-lock-") as temp_dir:
        report_path = Path(temp_dir) / "pip-report.json"
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--ignore-installed",
            "--disable-pip-version-check",
            "--index-url",
            PYPI_INDEX,
            "--only-binary=:all:",
            "--python-version",
            PYTHON_TARGET,
            "--implementation",
            "cp",
            "--abi",
            "cp312",
            "--report",
            os.fspath(report_path),
            "-r",
            os.fspath(REPO / "requirements-dev.txt"),
        ]
        platform_arguments = [
            argument
            for platform in pip_platforms
            for argument in ("--platform", platform)
        ]
        report_position = command.index("--report")
        command[report_position:report_position] = platform_arguments
        if locked_versions or locked_artifacts:
            constraints = Path(temp_dir) / "locked-versions.txt"
            constraints.write_text("\n".join(locked_artifacts or locked_versions)+"\n", encoding="utf-8")
            command.extend(["--constraint", os.fspath(constraints)])
        completed = subprocess.run(command, cwd=REPO, check=False)
        if completed.returncode != 0:
            raise CiReproducibilityError(
                f"pip resolver failed with exit code {completed.returncode}"
            )
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise CiReproducibilityError("pip resolver report must be an object")
        return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--verify-resolution", action="store_true", help="with --check, resolve current index and compare exact lock bytes without writing")
    parser.add_argument("--use-locked-versions", action="store_true", help="with --verify-resolution, constrain every existing transitive version")
    parser.add_argument("--use-locked-artifacts", action="store_true", help="with --verify-resolution, constrain exact locked wheel URLs and verify their hashes")
    parser.add_argument("--target", choices=tuple(TARGETS), default="linux-amd64")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.use_locked_versions and args.use_locked_artifacts:
        parser.error("choose locked versions or locked artifacts")
    if args.use_locked_artifacts and not args.verify_resolution:
        parser.error("--use-locked-artifacts requires --verify-resolution")
    if args.use_locked_versions and not args.verify_resolution:
        parser.error("--use-locked-versions requires --verify-resolution")
    if args.verify_resolution and not args.check:
        parser.error("--verify-resolution requires --check")
    try:
        target = TARGETS[args.target]
        output = args.output or REPO / str(target["output"])
        platform_tag = str(target["platform_tag"])
        pip_platforms = tuple(str(value) for value in target["pip_platforms"])
        source_hashes = _source_hashes()
        if args.check:
            verified = verify_lock_document(
                output.read_text(encoding="utf-8"),
                expected_source_hashes=source_hashes,
                required_python_version=PYTHON_TARGET,
                required_platform_tag=platform_tag,
            )
            if args.verify_resolution:
                versions = tuple(line[len("# package: "):] for line in output.read_text(encoding="utf-8").splitlines()
                                 if line.startswith("# package: ")) if args.use_locked_versions else ()
                artifacts = tuple(line.split(" --hash=",1)[0] for line in output.read_text(encoding="utf-8").splitlines()
                                  if line and not line.startswith("#")) if args.use_locked_artifacts else ()
                regenerated = build_lock_document(
                    _generate_report(pip_platforms=pip_platforms, locked_versions=versions, locked_artifacts=artifacts), source_hashes=source_hashes,
                    python_version=PYTHON_TARGET, platform_tag=platform_tag,
                )
                if regenerated != output.read_text(encoding="utf-8"):
                    raise CiReproducibilityError("fresh resolution differs from the committed artifacts; lock was not modified")
                print(f"CI dependency resolution matches: {verified.package_count} packages")
            else:
                print(f"CI dependency lock structure/source hashes verified: {verified.package_count} packages; fresh resolution not checked")
            return 0
        document = build_lock_document(
            _generate_report(pip_platforms=pip_platforms),
            source_hashes=source_hashes,
            python_version=PYTHON_TARGET,
            platform_tag=platform_tag,
        )
        output.write_text(document, encoding="utf-8", newline="\n")
        verified = verify_lock_document(
            document,
            expected_source_hashes=source_hashes,
            required_python_version=PYTHON_TARGET,
            required_platform_tag=platform_tag,
        )
        print(f"CI dependency lock generated: {verified.package_count} packages")
        return 0
    except (CiReproducibilityError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"CI dependency lock failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
