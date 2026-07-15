#!/usr/bin/env python
"""Retain and verify Trivy evidence for the console base and runtime images."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Sequence


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services.governance.vulnerability_evidence import (
    ContainerAuditEvidenceError,
    build_container_audit_evidence,
    verify_container_audit_evidence,
)


DEFAULT_OUTPUT = REPO / "evidence/vulnerability/container_trivy_latest.json"
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def _read_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ContainerAuditEvidenceError("container audit input must be an object")
    return document


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--base-report")
    parser.add_argument("--runtime-report")
    parser.add_argument("--database-updated-at")
    parser.add_argument("--database-next-update")
    parser.add_argument("--database-downloaded-at")
    parser.add_argument("--observed-at", type=int)
    parser.add_argument("--max-age-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve()
    output = Path(args.output)
    try:
        if args.check:
            summary = verify_container_audit_evidence(_read_object(output), root)
            now = int(time.time())
            if args.max_age_seconds <= 0:
                raise ContainerAuditEvidenceError(
                    "max-age-seconds must be a positive integer"
                )
            if (
                summary["observed_at"] > now + 300
                or now - summary["observed_at"] > args.max_age_seconds
            ):
                raise ContainerAuditEvidenceError(
                    "retained container audit evidence is stale or future-dated"
                )
            if (
                summary["verdict"] != "PASS"
                or summary["runtime_vulnerability_count"] != 0
            ):
                raise ContainerAuditEvidenceError(
                    "retained container audit does not satisfy the vulnerability policy"
                )
            print(
                "container Trivy evidence: current; "
                f"base={summary['base_vulnerability_count']}; runtime=0; "
                f"image={summary['runtime_image_digest']}"
            )
            return 0

        required = {
            "base_report": args.base_report,
            "runtime_report": args.runtime_report,
            "database_updated_at": args.database_updated_at,
            "database_next_update": args.database_next_update,
            "database_downloaded_at": args.database_downloaded_at,
        }
        missing = sorted(key for key, value in required.items() if not value)
        if missing:
            raise ContainerAuditEvidenceError(
                "generation requires: " + ", ".join(missing)
            )
        evidence = build_container_audit_evidence(
            root,
            base_report=_read_object(Path(args.base_report)),
            runtime_report=_read_object(Path(args.runtime_report)),
            observed_at=(
                args.observed_at if args.observed_at is not None else int(time.time())
            ),
            database_updated_at=args.database_updated_at,
            database_next_update=args.database_next_update,
            database_downloaded_at=args.database_downloaded_at,
        )
        _write(output, evidence)
        print(
            "container Trivy evidence: retained external result; "
            f"base={evidence['base_vulnerability_counts']['total']}; "
            f"runtime={evidence['runtime_vulnerability_counts']['total']}; "
            f"verdict={evidence['verdict']}"
        )
        return 0 if evidence["verdict"] == "PASS" else 1
    except (
        OSError,
        json.JSONDecodeError,
        ContainerAuditEvidenceError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"container Trivy evidence failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
