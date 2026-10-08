#!/usr/bin/env python
"""Run pip-audit against pinned QUANTOS inputs and retain canonical evidence."""
from __future__ import annotations

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Sequence


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services.governance.vulnerability_evidence import (
    PythonAuditEvidenceError,
    build_python_audit_evidence,
    verify_python_audit_evidence,
)


DEFAULT_OUTPUT = REPO / "evidence/vulnerability/python_audit_latest.json"


def _read_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise PythonAuditEvidenceError("Python audit evidence must be a JSON object")
    return document


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(
        (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
    )
    temporary.replace(path)


def _run_external_audit(repo_root: Path) -> dict[str, Any]:
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip_audit",
            "-r",
            "requirements-dev.txt",
            "-f",
            "json",
        ],
        cwd=repo_root,
        capture_output=True,
        check=False,
    )
    # pip-audit mixes progress indicators into stdout before the JSON object;
    # on Windows the system encoding may also be non-UTF-8, so operate in binary
    # mode and locate the JSON start explicitly.
    raw = process.stdout
    json_start = raw.find(b"{")
    if json_start < 0:
        raise PythonAuditEvidenceError(
            "pip-audit did not return a JSON advisory report"
        )
    try:
        report = json.loads(raw[json_start:].decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PythonAuditEvidenceError(
            "pip-audit did not return a JSON advisory report"
        ) from exc
    if not isinstance(report, dict) or process.returncode not in (0, 1):
        raise PythonAuditEvidenceError(
            f"pip-audit failed without a usable report (exit {process.returncode})"
        )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--observed-at", type=int)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve()
    output = Path(args.output)
    try:
        if args.check:
            summary = verify_python_audit_evidence(_read_object(output), root)
            if summary["verdict"] != "PASS" or summary["vulnerability_count"] != 0:
                raise PythonAuditEvidenceError(
                    "retained Python audit contains known vulnerabilities"
                )
            print(
                "Python audit evidence: current; "
                f"observed_at={summary['observed_at']}; vulnerabilities=0"
            )
            return 0

        report = _run_external_audit(root)
        evidence = build_python_audit_evidence(
            root,
            report=report,
            scanner_version=version("pip-audit"),
            observed_at=(
                args.observed_at if args.observed_at is not None else int(time.time())
            ),
        )
        _write(output, evidence)
        total = evidence["vulnerability_count"]
        print(
            "Python audit evidence: retained external result; "
            f"vulnerabilities={total}; verdict={evidence['verdict']}"
        )
        return 0 if evidence["verdict"] == "PASS" else 1
    except (
        OSError,
        json.JSONDecodeError,
        PythonAuditEvidenceError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"Python audit evidence failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
