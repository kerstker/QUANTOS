#!/usr/bin/env python
"""Generate and verify recipe-bound cross-host OCI reproducibility evidence."""
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

from services.governance.cross_host_container_reproducibility import (  # noqa: E402
    CrossHostContainerEvidenceError,
    build_container_build_claim,
    build_cross_host_container_evidence,
    verify_container_build_claim,
    verify_cross_host_container_evidence,
)


def _read(path: str | Path) -> dict[str, Any]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise CrossHostContainerEvidenceError("evidence document must be an object")
    return document


def _write(path: str | Path, document: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO))
    subparsers = parser.add_subparsers(dest="command", required=True)

    claim = subparsers.add_parser("claim")
    claim.add_argument("--archive", required=True)
    claim.add_argument("--output", required=True)
    claim.add_argument("--source-commit", required=True)
    claim.add_argument("--runner-label", required=True)
    claim.add_argument("--workflow-job", required=True)
    claim.add_argument("--setup-buildx-action-sha", required=True)
    claim.add_argument("--buildx-version", required=True)
    claim.add_argument("--buildkit-version", required=True)
    claim.add_argument("--buildkit-image-ref", required=True)

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--claim", action="append", required=True)
    aggregate.add_argument("--output", required=True)
    aggregate.add_argument("--observed-at", type=int)
    aggregate.add_argument("--workflow-ref", required=True)
    aggregate.add_argument("--workflow-run", required=True)

    check_claim = subparsers.add_parser("check-claim")
    check_claim.add_argument("--input", required=True)

    check = subparsers.add_parser("check")
    check.add_argument("--input", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.repo_root).resolve()
    try:
        if args.command == "claim":
            document = build_container_build_claim(
                root,
                archive=args.archive,
                source_commit=args.source_commit,
                runner_label=args.runner_label,
                workflow_job=args.workflow_job,
                build_tool={
                    "setup_buildx_action_sha": args.setup_buildx_action_sha,
                    "buildx_version": args.buildx_version,
                    "buildkit_version": args.buildkit_version,
                    "buildkit_image_ref": args.buildkit_image_ref,
                },
            )
            _write(args.output, document)
            print(
                "cross-host container claim: "
                f"runner={args.runner_label}; archive={document['archive']['sha256']}"
            )
            return 0
        if args.command == "aggregate":
            if len(args.claim) != 2:
                raise CrossHostContainerEvidenceError(
                    "aggregate requires exactly two --claim arguments"
                )
            document = build_cross_host_container_evidence(
                root,
                claims=[_read(path) for path in args.claim],
                observed_at=(
                    args.observed_at
                    if args.observed_at is not None
                    else int(time.time())
                ),
                workflow_ref=args.workflow_ref,
                workflow_run=args.workflow_run,
            )
            _write(args.output, document)
            print(
                "cross-host container evidence: "
                f"mode={document['mode']}; archive={document['archive_sha256']}"
            )
            return 0
        if args.command == "check-claim":
            summary = verify_container_build_claim(_read(args.input), root)
            print(
                "cross-host container claim: verified; "
                f"runner={summary['builder']}; archive={summary['archive_sha256']}"
            )
            return 0
        summary = verify_cross_host_container_evidence(_read(args.input), root)
        print(
            "cross-host container evidence: verified; "
            f"builders={','.join(summary['builders'])}; "
            f"archive={summary['archive_sha256']}"
        )
        return 0
    except (
        CrossHostContainerEvidenceError,
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"cross-host container evidence failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
