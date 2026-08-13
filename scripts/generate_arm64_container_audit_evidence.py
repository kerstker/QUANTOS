#!/usr/bin/env python
"""Retain and verify native ARM64 Trivy container evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.evidence_binding import resolve_canonical_source_anchor  # noqa: E402
from services.governance.arm64_console_evidence import (  # noqa: E402
    Arm64ConsoleEvidenceError,
    verify_arm64_container_audit_evidence,
)
from services.governance.json_integrity import load_json_without_duplicate_keys  # noqa: E402


DEFAULT_OUTPUT = REPO / "evidence/vulnerability/container_trivy_arm64_latest.json"
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def _read_object(path: Path) -> dict[str, Any]:
    document = load_json_without_duplicate_keys(path.read_bytes())
    if not isinstance(document, dict):
        raise Arm64ConsoleEvidenceError("ARM64 audit input must be an object")
    return document


def _write(path: Path, document: Mapping[str, Any]) -> None:
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
    parser.add_argument("--max-age-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve()
    output = Path(args.output)
    try:
        if args.check:
            summary = verify_arm64_container_audit_evidence(
                _read_object(output),
                root,
                expected_source_commit=resolve_canonical_source_anchor(root),
            )
            now = int(time.time())
            if args.max_age_seconds <= 0:
                raise Arm64ConsoleEvidenceError("max-age-seconds must be positive")
            if (
                summary["observed_at"] > now + 300
                or now - summary["observed_at"] > args.max_age_seconds
            ):
                raise Arm64ConsoleEvidenceError(
                    "retained ARM64 container audit is stale or future-dated"
                )
            if (
                summary["verdict"] != "PASS"
                or summary["runtime_vulnerability_count"] != 0
            ):
                raise Arm64ConsoleEvidenceError(
                    "retained ARM64 container audit does not satisfy policy"
                )
            print(
                "ARM64 container Trivy evidence: current; "
                f"base={summary['base_vulnerability_count']}; runtime=0; "
                f"image={summary['runtime_image_digest']}"
            )
            return 0
        raise Arm64ConsoleEvidenceError(
            "generation is available only through capture_arm64_console_evidence.py"
        )
    except (
        Arm64ConsoleEvidenceError,
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as exc:
        print(f"ARM64 container Trivy evidence failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
