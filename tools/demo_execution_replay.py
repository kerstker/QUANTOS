"""Import a bounded offline Demo receipt batch or rebuild its V2 diagnostic.

No private API, credentials, submission, or resident service is used.
"""
import argparse
import json
from pathlib import Path

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_execution_journal import DemoExecutionJournal, replay_report, MAX_BYTES
from tools.cache_io import atomic_write_json


def _read(path):
    with path.open("rb") as source:
        raw = source.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("INPUT_TOO_LARGE")
    return load_json_without_duplicate_keys(raw.decode())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--messages", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.report.resolve() in {p.resolve() for p in (args.journal, args.manifest, args.messages) if p is not None}:
        parser.error("report must not overwrite journal or inputs")
    try:
        manifest = _read(args.manifest) if args.manifest else None
        messages = _read(args.messages) if args.messages else None
        with DemoExecutionJournal(args.journal, manifest=manifest, read_only=manifest is None and messages is None) as journal:
            added = journal.append(messages) if messages is not None else 0
            report = replay_report(journal)
        atomic_write_json(args.report, report)
        print(json.dumps({"status": report["projection_status"], "added_records": added,
                          "journal_records": report["journal_records"], "reason": report["projection_reason"],
                          "submission_enabled": False}))
        return 0 if report["projection_status"] == "REPLAYED_TECHNICAL_ONLY" else 2
    except Exception:
        print(json.dumps({"status": "FAILED", "reason": "DEMO_REPLAY_INPUT_OR_STORE_INVALID"}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
