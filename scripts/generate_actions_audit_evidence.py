#!/usr/bin/env python
"""Query OSV for every pinned GitHub Action commit and retain the results."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services.governance.vulnerability_evidence import (
    ActionsAuditEvidenceError,
    build_actions_audit_evidence,
    discover_action_commits,
    verify_actions_audit_evidence,
)


DEFAULT_OUTPUT = REPO / "evidence/vulnerability/actions_osv_latest.json"
OSV_QUERY_URL = "https://api.osv.dev/v1/query"


def _read_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ActionsAuditEvidenceError("Actions audit evidence must be a JSON object")
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


def _query_osv(commit: str) -> dict[str, Any]:
    request = Request(
        OSV_QUERY_URL,
        data=json.dumps({"commit": commit}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ActionsAuditEvidenceError(
            f"OSV query failed for action commit {commit}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ActionsAuditEvidenceError(
            f"OSV returned a non-object response for action commit {commit}"
        )
    return payload


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
            summary = verify_actions_audit_evidence(_read_object(output), root)
            if summary["verdict"] != "PASS" or summary["vulnerability_count"] != 0:
                raise ActionsAuditEvidenceError(
                    "retained Actions OSV audit contains known vulnerabilities"
                )
            print(
                "Actions OSV evidence: current; "
                f"actions={summary['unique_action_count']}; vulnerabilities=0"
            )
            return 0

        responses = {
            commit: _query_osv(commit) for commit in discover_action_commits(root)
        }
        evidence = build_actions_audit_evidence(
            root,
            responses=responses,
            observed_at=(
                args.observed_at if args.observed_at is not None else int(time.time())
            ),
        )
        _write(output, evidence)
        print(
            "Actions OSV evidence: retained external result; "
            f"actions={evidence['unique_action_count']}; "
            f"vulnerabilities={evidence['vulnerability_count']}; "
            f"verdict={evidence['verdict']}"
        )
        return 0 if evidence["verdict"] == "PASS" else 1
    except (
        OSError,
        json.JSONDecodeError,
        ActionsAuditEvidenceError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"Actions OSV evidence failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
