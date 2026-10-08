"""One grant-bound archive action on sealed stdin; no prompt or grant issuer."""

import argparse
import json

from tools import demo_executor_archive_write as writer

SCHEMA = "DEMO_RETENTION_WORKER_RESULT_V1"
REASONS = {
    "ARCHIVE_WRITER_BUSY",
    "MAINTENANCE_GRANT_EXPIRED",
    "MAINTENANCE_CONTROLLER_EXITED",
    "MAINTENANCE_INPUT_DIGEST",
    "MAINTENANCE_CAPTURE_CHANGED",
    "MAINTENANCE_MANIFEST_GRANT",
    "MAINTENANCE_RECEIPT_GRANT",
    "MAINTENANCE_RECEIPT_CONTEXT",
    "RECORD_CHANGED",
    "PLACEMENT_CONFLICT_OR_MISSING",
    "LATEST_SELECTED",
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("PREPARE", "APPLY", "ROLLBACK"), required=True)
    action = parser.parse_args(argv).action
    try:
        operation = {"PREPARE": writer.prepare, "APPLY": writer.apply, "ROLLBACK": writer.rollback}[action]
        summary = operation(0)
        ok = True
    except Exception as exc:
        reason = str(exc)
        summary = {
            "status": "REJECTED",
            "reason": reason if reason in REASONS else "WORKER_UNAVAILABLE",
            "transaction_state": "INSPECTION_REQUIRED",
            "submission_enabled": False,
        }
        ok = False
    print(json.dumps({"schema": SCHEMA, "action": action, "ok": ok, "summary": summary}, sort_keys=True), flush=True)
    return 0 if ok else 78


if __name__ == "__main__":
    raise SystemExit(main())
