"""Fresh evidence preparation inside a locked paused maintenance window.

This is a trusted root coordinator component, not an archive grant issuer.
Only the two executor children can write their new private workspace; their
source namespace is mounted read-only. A failed preparation never licenses apply.
"""

import hashlib
import os
import secrets
import subprocess
import time

from live.execution.demo_authority import _read_root_owned_json, _REGISTRY_PATH
from services.validation.demo_submission import validate_registry, registry_sha256
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_maintenance_prepare_worker as worker
from tools import demo_retention_worker_handoff as handoff
from tools import demo_retention_reference_review as reference
from tools import demo_maintenance_processes as processes
from tools.demo_maintenance_services import _save
from tools.demo_namespace_checkpoint import canonical
from tools.demo_retention_transaction import read, require, sync, write_new

SCHEMA = "DEMO_WINDOW_PREPARATION_V1"


def command(*, source_commit, uid, action, operation, unit, digest, plan_sha256=None, review_sha256=None):
    require(action in {"PLAN", "CAPTURE"}, "PREPARATION_ACTION")
    root = worker.workspace(operation)
    argv = handoff.command(source_commit=source_commit, uid=uid, action="PREPARE", unit=unit, grant_sha256=digest)
    replacement = {
        "WorkingDirectory": str(root),
        "RuntimeMaxSec": "1800s",
        "ReadOnlyPaths": "/opt/quantos/releases/" + source_commit + " /var/lib/quantos-demo-executor",
        "ReadWritePaths": str(root),
        "InaccessiblePaths": "-/var/lib/quantos-demo-issuer -/var/lib/quantos-demo-controller -/var/lib/quantos-demo-archives",
    }
    for index, value in enumerate(argv):
        if value.startswith("--property="):
            key = value.removeprefix("--property=").split("=", 1)[0]
            if key in replacement:
                argv[index] = "--property=" + key + "=" + replacement[key]
    index = argv.index("tools.demo_retention_worker")
    argv[index:] = [
        "tools.demo_maintenance_prepare_worker",
        "--action",
        action,
        "--operation",
        operation,
        "--source-commit",
        source_commit,
    ]
    if action == "CAPTURE":
        require(all(handoff.grants.digest(v) for v in (plan_sha256, review_sha256)), "PREPARATION_INPUTS")
        argv += ["--plan-sha256", plan_sha256, "--review-sha256", review_sha256]
    else:
        require(plan_sha256 is None and review_sha256 is None, "PREPARATION_INPUTS")
    return argv


def packet(raw, action):
    value = decode(raw)
    require(
        type(value) is dict
        and set(value) == {"schema", "action", "ok", "summary"}
        and value["schema"] == worker.SCHEMA
        and value["action"] == action
        and type(value["ok"]) is bool
        and type(value["summary"]) is dict,
        "PREPARATION_PROTOCOL",
    )
    summary = value["summary"]
    if not value["ok"]:
        require(summary == {"status": "PREPARATION_FAILED", "production_changes": False}, "PREPARATION_PROTOCOL")
    elif action == "PLAN":
        require(
            set(summary) == {"status", "plan_sha256", "candidate_files", "candidate_bytes", "production_changes"}
            and summary["status"] == "PLAN_CREATED"
            and handoff.grants.digest(summary["plan_sha256"])
            and summary["production_changes"] is False,
            "PREPARATION_PROTOCOL",
        )
        for field, limit in (("candidate_files", 120000), ("candidate_bytes", 1024**3)):
            require(type(summary[field]) is int and 0 <= summary[field] <= limit, "PREPARATION_PROTOCOL")
    elif action == "CAPTURE":
        require(
            set(summary)
            == {
                "status",
                "inputs",
                "authorization_issued",
                "production_changes",
                "all_references_proven",
                "selected_files",
            }
            and summary["status"] == "VERIFIED_INPUTS_EXPORTED"
            and all(
                summary[k] is False for k in ("authorization_issued", "production_changes", "all_references_proven")
            )
            and type(summary["selected_files"]) is int
            and 0 <= summary["selected_files"] <= 30000
            and type(summary["inputs"]) is dict
            and set(summary["inputs"]) == handoff.grants.INPUTS
            and all(handoff.grants.digest(v) for v in summary["inputs"].values()),
            "PREPARATION_PROTOCOL",
        )
    else:
        raise ValueError("PREPARATION_ACTION")
    return value


def inspect(root, document):
    path = root / "preparation.json"
    if not path.exists() and not path.is_symlink():
        return None
    value = decode(read(path, 32768))
    require(
        type(value) is dict
        and set(value)
        == {"schema", "window_sha256", "source_commit", "executor_uid", "registry_sha256", "jobs", "complete"}
        and value["schema"] == SCHEMA
        and value["window_sha256"] == document["inhibitor_sha256"]
        and type(value["jobs"]) is list
        and len(value["jobs"]) <= 2
        and type(value["complete"]) is bool,
        "PREPARATION_JOURNAL",
    )
    for index, job in enumerate(value["jobs"]):
        require(
            type(job) is dict
            and set(job) == {"action", "unit", "digest", "result"}
            and job["action"] == ("PLAN", "CAPTURE")[index]
            and handoff.grants.digest(job["digest"]),
            "PREPARATION_JOURNAL",
        )
        handoff._unit(job["unit"])
        if job["result"] is not None:
            result = job["result"]
            require(
                type(result) is dict
                and set(result) == {"status", "worker_drained", "result", "safe_to_restore_services"}
                and type(result["worker_drained"]) is bool
                and result["safe_to_restore_services"] is False,
                "PREPARATION_JOURNAL",
            )
            if result["status"] == "WORKER_COMPLETED":
                require(
                    result["worker_drained"] is True
                    and packet(canonical(result["result"]), job["action"])["ok"] is True,
                    "PREPARATION_JOURNAL",
                )
            else:
                require(
                    result["status"] == "WORKER_INSPECTION_REQUIRED" and result["result"] is None, "PREPARATION_JOURNAL"
                )
    if value["complete"]:
        require(
            len(value["jobs"]) == 2
            and all(j["result"] is not None and j["result"]["status"] == "WORKER_COMPLETED" for j in value["jobs"]),
            "PREPARATION_JOURNAL",
        )
    return value


def restore_guard(root, document):
    record = inspect(root, document)
    if record is not None:
        require(all(handoff.drained(job["unit"]) for job in record["jobs"]), "PREPARATION_WORKER_STILL_RUNNING")


def run(root, document, *, source_commit):
    """Caller holds the window lock and verifies loaded inhibition before/after."""
    require(os.getresuid() == (0, 0, 0), "OPERATOR_ROOT_REQUIRED")
    require(document["phase"] == "PAUSED", "WINDOW_NOT_PAUSED")
    handoff.grants.verify_source(source_commit)
    registry = validate_registry(decode(_read_root_owned_json(_REGISTRY_PATH)))
    uid = registry["executor_uid"]
    require(inspect(root, document) is None, "PREPARATION_ALREADY_STARTED")
    observation = processes.observe(executor_uid=uid, issuer_uid=registry["issuer_uid"])
    _save(root / "preparation-processes.json", observation)
    require(observation["status"] == "NO_SCOPED_PROCESS_OBSERVED", "PREPARATION_PROCESSES_UNKNOWN")
    operation = document["inhibitor_sha256"]
    area = worker.workspace(operation)
    require(not any(p.is_symlink() for p in (area, *area.parents)), "PREPARATION_PATH")
    area.mkdir(mode=0o700)
    os.chown(area, uid, uid)
    sync(area.parent)
    record = {
        "schema": SCHEMA,
        "window_sha256": operation,
        "source_commit": source_commit,
        "executor_uid": uid,
        "registry_sha256": registry_sha256(registry),
        "jobs": [],
        "complete": False,
    }
    write_new(root / "preparation.json", canonical(record))

    def dispatch(action, **inputs):
        unit = "quantos-demo-retention-" + secrets.token_hex(16) + ".service"
        digest = hashlib.sha256(canonical([operation, source_commit, uid, action, inputs, unit])).hexdigest()
        require(handoff.show(unit).get("LoadState") == "not-found" and handoff.drained(unit), "WORKER_UNIT_IN_USE")
        argv = command(
            source_commit=source_commit, uid=uid, action=action, operation=operation, unit=unit, digest=digest, **inputs
        )
        job = {"action": action, "unit": unit, "digest": digest, "result": None}
        record["jobs"].append(job)
        _save(root / "preparation.json", record)
        result = handoff._transport(
            subprocess.DEVNULL,
            argv=argv,
            deadline=time.monotonic() + 1800,
            unit=unit,
            digest=digest,
            parse=lambda raw: packet(raw, action),
        )
        job["result"] = result
        _save(root / "preparation.json", record)
        require(result["status"] == "WORKER_COMPLETED", "PREPARATION_INCOMPLETE")
        return result["result"]["summary"]

    planned = dispatch("PLAN")
    report = reference.review(area / "plan.json", expected_sha256=planned["plan_sha256"])
    raw = canonical(report)
    write_new(area / "review.json", raw)
    os.chown(area / "review.json", uid, uid)
    sync(area)
    review_sha = hashlib.sha256(raw).hexdigest()
    captured = dispatch("CAPTURE", plan_sha256=planned["plan_sha256"], review_sha256=review_sha)
    require(
        captured["inputs"]["plan.json"] == planned["plan_sha256"] and captured["inputs"]["review.json"] == review_sha,
        "PREPARATION_INPUT_BINDING",
    )
    record["complete"] = True
    _save(root / "preparation.json", record)
    return {
        "status": "WINDOW_EVIDENCE_PREPARED",
        "inputs": captured["inputs"],
        "selected_files": captured["selected_files"],
        "authorization_issued": False,
        "production_changes": False,
        "all_references_proven": False,
    }
