#!/usr/bin/env python
"""Run npm's external advisory audit and retain canonical evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Sequence


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services.governance.vulnerability_evidence import (
    NpmAuditEvidenceError,
    build_npm_audit_evidence,
    verify_npm_audit_evidence,
)


DEFAULT_OUTPUT = REPO / "evidence/vulnerability/npm_audit_latest.json"


def _read_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise NpmAuditEvidenceError("npm audit evidence must be a JSON object")
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


def _run_external_audit(repo_root: Path) -> tuple[dict[str, Any], str]:
    npm = shutil.which("npm")
    if npm is None:
        raise NpmAuditEvidenceError("npm executable is unavailable")
    console = repo_root / "apps/research-console"
    version_process = subprocess.run(
        [npm, "--version"],
        cwd=console,
        capture_output=True,
        text=True,
        check=False,
    )
    if version_process.returncode != 0:
        raise NpmAuditEvidenceError("npm version query failed")
    process = subprocess.run(
        [npm, "audit", "--json", "--package-lock-only"],
        cwd=console,
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        report = json.loads(process.stdout)
    except json.JSONDecodeError as exc:
        raise NpmAuditEvidenceError(
            "npm audit did not return a JSON advisory report"
        ) from exc
    if not isinstance(report, dict) or process.returncode not in (0, 1):
        raise NpmAuditEvidenceError(
            f"npm audit failed without a usable report (exit {process.returncode})"
        )
    return report, version_process.stdout.strip()


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
            summary = verify_npm_audit_evidence(_read_object(output), root)
            if summary["verdict"] != "PASS" or summary["vulnerability_count"] != 0:
                raise NpmAuditEvidenceError(
                    "retained npm audit contains known vulnerabilities"
                )
            print(
                "npm audit evidence: current; "
                f"observed_at={summary['observed_at']}; vulnerabilities=0"
            )
            return 0

        report, npm_version = _run_external_audit(root)
        evidence = build_npm_audit_evidence(
            root,
            report=report,
            npm_version=npm_version,
            observed_at=(
                args.observed_at if args.observed_at is not None else int(time.time())
            ),
        )
        _write(output, evidence)
        total = evidence["vulnerability_counts"]["total"]
        print(
            "npm audit evidence: retained external result; "
            f"vulnerabilities={total}; verdict={evidence['verdict']}"
        )
        return 0 if evidence["verdict"] == "PASS" else 1
    except (
        OSError,
        json.JSONDecodeError,
        NpmAuditEvidenceError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"npm audit evidence failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
