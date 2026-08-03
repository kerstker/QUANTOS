"""Run the independent full raw-market archive integrity audit.

This L2-only worker is intentionally outside the forward-paper critical path.
It verifies every immutable observation and object, refreshes the derived SQLite
index audit anchor, and publishes a compact operator receipt.
"""
from __future__ import annotations

import os
import time

from services.data.raw_archive import DEFAULT_RAW_ARCHIVE, summarize_archive
from tools.cache_io import atomic_write_json


OUTPUT = os.path.join("evidence", "raw_archive_audit.json")


def run_audit(*, root: str = DEFAULT_RAW_ARCHIVE, now_ms: int | None = None) -> dict:
    started_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    started = time.monotonic()
    summary = summarize_archive(root)
    report = {
        "schema_version": 1,
        "readiness": "L2_DRY_RUN_ONLY",
        "purpose": "FULL_RAW_ARCHIVE_INTEGRITY_AUDIT",
        "started_at_ms": started_ms,
        "completed_at_ms": int(time.time() * 1000),
        "duration_ms": int((time.monotonic() - started) * 1000),
        "archive_root": root.replace("\\", "/"),
        "summary": summary,
        "verdict": "PASS" if summary.get("status") == "PASS" else "BLOCK",
    }
    return report


def main() -> int:
    report = run_audit()
    atomic_write_json(OUTPUT, report, indent=2)
    summary = report["summary"]
    print(
        "raw archive audit: "
        f"{report['verdict']} observations={summary.get('observation_count')} "
        f"invalid={summary.get('invalid_observation_count')} "
        f"duration_ms={report['duration_ms']}"
    )
    return 0 if report["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
