#!/usr/bin/env python
"""Retain and verify a two-build OCI reproducibility observation."""
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

from services.governance.reproducibility_evidence import (
    ContainerReproducibilityEvidenceError,
    build_container_reproducibility_evidence,
    verify_container_reproducibility_evidence,
)


DEFAULT_OUTPUT = REPO / "evidence/reproducibility/container_build_latest.json"
DEFAULT_MAX_AGE_SECONDS = 30 * 24 * 60 * 60


def _read_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ContainerReproducibilityEvidenceError(
            "container reproducibility evidence must be an object"
        )
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(REPO))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--archive-a")
    parser.add_argument("--archive-b")
    parser.add_argument("--observed-at", type=int)
    parser.add_argument("--builder-name", default="quantos-cross-host")
    parser.add_argument("--builder-driver", default="docker-container")
    parser.add_argument("--buildx-version")
    parser.add_argument("--buildkit-version")
    parser.add_argument("--docker-client-version")
    parser.add_argument("--docker-server-version")
    parser.add_argument("--max-age-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve()
    output = Path(args.output)
    try:
        if args.check:
            summary = verify_container_reproducibility_evidence(
                _read_object(output), root
            )
            now = int(time.time())
            if args.max_age_seconds <= 0:
                raise ContainerReproducibilityEvidenceError(
                    "max-age-seconds must be positive"
                )
            if (
                summary["observed_at"] > now + 300
                or now - summary["observed_at"] > args.max_age_seconds
            ):
                raise ContainerReproducibilityEvidenceError(
                    "retained reproducibility evidence is stale or future-dated"
                )
            print(
                "container reproducibility evidence: current; "
                f"archive={summary['archive_sha256']}; "
                f"buildkit={summary['buildkit_version']}"
            )
            return 0

        required = {
            "archive-a": args.archive_a,
            "archive-b": args.archive_b,
            "buildx-version": args.buildx_version,
            "buildkit-version": args.buildkit_version,
            "docker-client-version": args.docker_client_version,
            "docker-server-version": args.docker_server_version,
        }
        missing = sorted(key for key, value in required.items() if not value)
        if missing:
            raise ContainerReproducibilityEvidenceError(
                "generation requires: " + ", ".join(missing)
            )
        evidence = build_container_reproducibility_evidence(
            root,
            archive_a=args.archive_a,
            archive_b=args.archive_b,
            observed_at=(
                args.observed_at if args.observed_at is not None else int(time.time())
            ),
            build_tool={
                "builder_name": args.builder_name,
                "builder_driver": args.builder_driver,
                "buildx_version": args.buildx_version,
                "buildkit_version": args.buildkit_version,
                "docker_client_version": args.docker_client_version,
                "docker_server_version": args.docker_server_version,
            },
        )
        _write(output, evidence)
        print(
            "container reproducibility evidence: retained two cold builds; "
            f"archive={evidence['builds'][0]['archive_sha256']}; verdict=PASS"
        )
        return 0
    except (
        ContainerReproducibilityEvidenceError,
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"container reproducibility evidence failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
