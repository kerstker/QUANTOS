#!/usr/bin/env python
"""Run canonical backend pytest commands under one branch-coverage authority."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.ci_reproducibility import (  # noqa: E402
    CiReproducibilityError,
    evaluate_coverage_summary,
    extract_backend_pytest_commands,
    load_policy,
)
from scripts.validate import CHECKS  # noqa: E402


POLICY_PATH = REPO / "config" / "ci_coverage_policy.json"


def _run(command: list[str]) -> int:
    completed = subprocess.run(command, cwd=REPO, check=False)
    return completed.returncode


def _require_success(command: list[str]) -> None:
    returncode = _run(command)
    if returncode != 0:
        raise CiReproducibilityError(
            f"coverage command failed with exit code {returncode}: "
            + subprocess.list2cmdline(command)
        )


def main() -> int:
    try:
        policy = load_policy(str(POLICY_PATH))
        backend = policy.get("backend")
        if not isinstance(backend, dict):
            raise CiReproducibilityError("coverage policy backend section is missing")
        _require_success([sys.executable, "-m", "coverage", "erase"])
        failed_test_commands: list[tuple[int, list[str]]] = []
        for canonical in extract_backend_pytest_commands(CHECKS):
            pytest_args = list(canonical[3:])
            command = [
                sys.executable,
                "-m",
                "coverage",
                "run",
                "--parallel-mode",
                "--branch",
                "--source=quantos,services,live,tools",
                "-m",
                "pytest",
                *pytest_args,
            ]
            returncode = _run(command)
            if returncode != 0:
                failed_test_commands.append((returncode, command))
        _require_success([sys.executable, "-m", "coverage", "combine"])
        with tempfile.TemporaryDirectory(prefix="quantos-coverage-") as temp_dir:
            report = Path(temp_dir) / "coverage.json"
            _require_success([sys.executable, "-m", "coverage", "json", "-o", str(report)])
            document = json.loads(report.read_text(encoding="utf-8"))
        totals = document.get("totals") if isinstance(document, dict) else None
        if not isinstance(totals, dict):
            raise CiReproducibilityError("coverage JSON lacks totals")
        result = evaluate_coverage_summary(
            totals,
            minimum_line_percent=backend.get("minimum_line_percent"),
            minimum_branch_percent=backend.get("minimum_branch_percent"),
        )
        print(
            "backend coverage accepted: "
            f"lines={result.line_percent:.2f}% branches={result.branch_percent:.2f}%"
        )
        if failed_test_commands:
            details = "; ".join(
                f"exit={returncode} {subprocess.list2cmdline(command)}"
                for returncode, command in failed_test_commands
            )
            raise CiReproducibilityError(
                "coverage was measured but canonical pytest commands failed: " + details
            )
        return 0
    except (CiReproducibilityError, OSError, ValueError, TypeError, KeyError) as exc:
        print(f"backend coverage failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
