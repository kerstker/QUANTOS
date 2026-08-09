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
PLATFORM_TARGET = "ubuntu_24_04_x86_64"
SOURCE_FILES = ("requirements.txt", "requirements-dev.txt")
DEFAULT_OUTPUT = REPO / "requirements-ci.lock"


def _source_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
        for name in SOURCE_FILES
    }


def _generate_report() -> dict[str, object]:
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
            "--only-binary=:all:",
            "--python-version",
            PYTHON_TARGET,
            "--implementation",
            "cp",
            "--abi",
            "cp312",
            "--platform",
            "manylinux_2_28_x86_64",
            "--platform",
            "manylinux_2_17_x86_64",
            "--platform",
            "manylinux2014_x86_64",
            "--report",
            os.fspath(report_path),
            "-r",
            os.fspath(REPO / "requirements-dev.txt"),
        ]
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
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        source_hashes = _source_hashes()
        if args.check:
            verified = verify_lock_document(
                args.output.read_text(encoding="utf-8"),
                expected_source_hashes=source_hashes,
                required_python_version=PYTHON_TARGET,
                required_platform_tag=PLATFORM_TARGET,
            )
            print(f"CI dependency lock current: {verified.package_count} packages")
            return 0
        document = build_lock_document(
            _generate_report(),
            source_hashes=source_hashes,
            python_version=PYTHON_TARGET,
            platform_tag=PLATFORM_TARGET,
        )
        args.output.write_text(document, encoding="utf-8", newline="\n")
        verified = verify_lock_document(
            document,
            expected_source_hashes=source_hashes,
            required_python_version=PYTHON_TARGET,
            required_platform_tag=PLATFORM_TARGET,
        )
        print(f"CI dependency lock generated: {verified.package_count} packages")
        return 0
    except (CiReproducibilityError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"CI dependency lock failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
