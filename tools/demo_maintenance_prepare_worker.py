"""Read-only source preparation worker; never accepts an archive execution grant."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

from live.execution.demo_authority import load_registry_for_executor
from tools import demo_retention_plan as planner
from tools import demo_retention_capture as capture
from tools.demo_issuer import verify_source
from tools.demo_namespace_checkpoint import canonical
from tools.demo_retention_transaction import directory, require, write_new

SCHEMA = "DEMO_MAINTENANCE_PREPARATION_RESULT_V1"


def workspace(operation):
    require(type(operation) is str and re.fullmatch("[a-f0-9]{64}", operation), "PREPARATION_OPERATION")
    return Path("/var/tmp") / ("quantos-demo-evidence-" + operation)


def admit(operation, source_commit):
    uid = os.geteuid()
    require(uid > 0 and os.getresuid() == (uid, uid, uid), "PREPARATION_EXECUTOR")
    require(os.getresgid() == (uid, uid, uid) and set(os.getgroups()) <= {uid}, "PREPARATION_EXECUTOR")
    registry = load_registry_for_executor()
    require(registry["executor_uid"] == uid, "PREPARATION_EXECUTOR")
    verify_source(source_commit)
    root = workspace(operation)
    require(not any(p.is_symlink() for p in (root, *root.parents)), "PREPARATION_PATH")
    directory(root)
    return root


def perform(action, operation, source_commit, plan_sha256=None, review_sha256=None):
    require(action in {"PLAN", "CAPTURE"}, "PREPARATION_ACTION")
    root = admit(operation, source_commit)
    if action == "PLAN":
        require(plan_sha256 is None and review_sha256 is None, "PREPARATION_INPUTS")
        plan = planner.build()
        raw = canonical(plan)
        write_new(root / "plan.json", raw)
        rows = [row for scope in plan["directories"].values() for row in scope["candidates"]]
        return {
            "status": "PLAN_CREATED",
            "plan_sha256": hashlib.sha256(raw).hexdigest(),
            "candidate_files": len(rows),
            "candidate_bytes": sum(row["bytes"] for row in rows),
            "production_changes": False,
        }
    require(
        all(type(v) is str and re.fullmatch("[a-f0-9]{64}", v) for v in (plan_sha256, review_sha256)),
        "PREPARATION_INPUTS",
    )
    return capture.prepare(
        root / "capture-work",
        plan=root / "plan.json",
        review=root / "review.json",
        source_commit=source_commit,
        expected_plan_sha256=plan_sha256,
        expected_review_sha256=review_sha256,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=("PLAN", "CAPTURE"), required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--review-sha256")
    args = parser.parse_args(argv)
    try:
        summary = perform(args.action, args.operation, args.source_commit, args.plan_sha256, args.review_sha256)
        ok = True
    except Exception:
        summary = {"status": "PREPARATION_FAILED", "production_changes": False}
        ok = False
    print(
        json.dumps({"schema": SCHEMA, "action": args.action, "ok": ok, "summary": summary}, sort_keys=True), flush=True
    )
    return 0 if ok else 78


if __name__ == "__main__":
    raise SystemExit(main())
