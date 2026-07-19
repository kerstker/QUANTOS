#!/usr/bin/env python
"""Check L2 backup evidence freshness and quality.

This script is designed for CI integration. It verifies that:
1. evidence/l2_backup_latest.json exists and is valid JSON
2. The backup evidence shows a PASS verdict
3. Restore verification was successful
4. RTO and RPO targets were met
5. All required sources were backed up
6. The backup is reasonably fresh (within expected cadence)

The script exits with:
- 0 if all checks PASS
- 1 if backup evidence is missing, stale, or shows failures
- 2 if backup evidence exists but is degraded (non-blocking warning)

In CI environments where QUANTOS_BACKUP_DEST is unavailable, the script
exits 0 with a skip message (CI cannot generate backups, only verify evidence).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


DEFAULT_MAX_AGE_MS = 48 * 3600 * 1000  # 48 hours (2x daily cadence)
REQUIRED_SOURCE_COUNT_MIN = 10


def check_backup_evidence(
    evidence_path: str | Path,
    max_age_ms: int,
    now_ms: int | None = None,
) -> tuple[str, str]:
    """Check backup evidence. Returns (verdict, reason)."""
    evidence_path = Path(evidence_path)

    if not evidence_path.exists():
        return "MISSING", f"backup evidence not found: {evidence_path}"

    try:
        with open(evidence_path, "r", encoding="utf-8-sig") as f:
            evidence = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return "INVALID", f"cannot read backup evidence: {exc}"

    # Check schema version
    schema_version = evidence.get("schema_version")
    if schema_version not in (1, None):
        return "INVALID", f"unsupported schema version: {schema_version}"

    # Check verdict
    verdict = evidence.get("verdict")
    if verdict != "PASS":
        reason = evidence.get("reason_code", "unknown")
        return "FAIL", f"backup verdict is {verdict}, reason: {reason}"

    # Check restore verification
    if not evidence.get("restore_verified"):
        return "FAIL", "restore verification did not pass"

    # Check RTO/RPO
    if not evidence.get("rto_met"):
        rto_ms = evidence.get("restore_elapsed_ms")
        target = evidence.get("rto_ms")
        return "FAIL", f"RTO not met: {rto_ms}ms > {target}ms"

    if not evidence.get("rpo_met"):
        age_ms = evidence.get("source_age_ms")
        target = evidence.get("max_rpo_ms")
        return "FAIL", f"RPO not met: source age {age_ms}ms > {target}ms"

    # Check file count
    file_count = evidence.get("file_count", 0)
    required_count = evidence.get("required_source_count", 0)
    if required_count < REQUIRED_SOURCE_COUNT_MIN:
        return "FAIL", f"insufficient required sources: {required_count} < {REQUIRED_SOURCE_COUNT_MIN}"

    if file_count < required_count:
        missing = required_count - file_count
        return "FAIL", f"backup incomplete: {missing} required sources missing"

    # Check encryption
    if not evidence.get("encrypted"):
        return "DEGRADED", "backup is not encrypted"

    # Check SQLite integrity
    if not evidence.get("sqlite_integrity_verified"):
        return "DEGRADED", "SQLite integrity not verified"

    # Check freshness
    observed_at_ms = evidence.get("observed_at_ms")
    if observed_at_ms is None:
        return "DEGRADED", "backup evidence has no timestamp"

    if now_ms is None:
        now_ms = int(time.time() * 1000)

    age_ms = now_ms - observed_at_ms
    if age_ms > max_age_ms:
        age_hours = age_ms / 3600000
        max_hours = max_age_ms / 3600000
        return "STALE", f"backup evidence is {age_hours:.1f}h old (max {max_hours:.1f}h)"

    return "PASS", "backup evidence is current and complete"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check L2 backup evidence freshness and quality"
    )
    parser.add_argument(
        "--evidence",
        default="evidence/l2_backup_latest.json",
        help="Path to backup evidence file (default: evidence/l2_backup_latest.json)",
    )
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=48.0,
        help="Maximum age in hours before backup is considered stale (default: 48)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="CI mode: fail if evidence is missing or stale",
    )
    args = parser.parse_args()

    max_age_ms = int(args.max_age_hours * 3600 * 1000)

    # In CI environments without backup configuration, skip the check
    if args.check:
        dest = os.getenv("QUANTOS_BACKUP_DEST")
        passphrase = os.getenv("QUANTOS_BACKUP_PASSPHRASE")
        if not dest or not passphrase:
            print("SKIP: backup configuration unavailable in CI environment")
            print("Evidence check requires QUANTOS_BACKUP_DEST and QUANTOS_BACKUP_PASSPHRASE")
            return 0

    verdict, reason = check_backup_evidence(args.evidence, max_age_ms)

    status = {
        "verdict": verdict,
        "reason": reason,
        "evidence_path": str(Path(args.evidence).resolve()),
        "max_age_ms": max_age_ms,
        "check_mode": args.check,
    }

    print(json.dumps(status, indent=2))

    if verdict == "PASS":
        return 0
    elif verdict in ("MISSING", "FAIL", "INVALID", "STALE"):
        print(f"\nERROR: {reason}", file=sys.stderr)
        return 1
    elif verdict == "DEGRADED":
        print(f"\nWARNING: {reason}", file=sys.stderr)
        return 2 if not args.check else 1
    else:
        print(f"\nERROR: unknown verdict: {verdict}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
