"""Restartable backup/retention rehearsal on isolated copies, never production.

No service operations, credentials, network calls or production apply switch.
Every completed rehearsal returns the restored copy to its full backup inventory.
"""

from contextlib import contextmanager
from copy import deepcopy

import argparse
import fcntl
import hashlib
import json
import os
import stat
import zipfile

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_namespace_checkpoint as checkpoint
from tools import demo_retention_transaction as retention
from tools.demo_maintenance_services import _save

require = retention.require
SCHEMA = "DEMO_MAINTENANCE_REHEARSAL_V1"
PHASES = {
    "RESTORING",
    "PREPARING",
    "APPLYING",
    "VERIFYING",
    "ROLLING_BACK",
    "RECOVERY_REQUIRED",
    "REHEARSAL_COMPLETE",
    "REHEARSAL_FAILED_ROLLED_BACK",
}


def prepare(workspace, *, capture, plan, review, expected_capture_sha256, expected_review_sha256):
    workspace = retention.isolated(workspace)
    retention.directory(workspace.parent)
    plan_raw = retention.read(retention.isolated(plan))
    review_raw = retention.read(retention.isolated(review))
    require(hashlib.sha256(review_raw).hexdigest() == expected_review_sha256, "REVIEW_DIGEST")
    require(decode(review_raw).get("plan_sha256") == hashlib.sha256(plan_raw).hexdigest(), "PLAN_DIGEST")
    workspace.mkdir(mode=0o700)
    retention.sync(workspace.parent)
    checkpoint._copy_private_capture(retention.isolated(capture), workspace / "capture.zip", expected_capture_sha256)
    retention.write_new(workspace / "plan.json", plan_raw)
    retention.write_new(workspace / "review.json", review_raw)
    request = {
        "schema": SCHEMA,
        "capture_sha256": expected_capture_sha256,
        "plan_sha256": hashlib.sha256(plan_raw).hexdigest(),
        "review_sha256": expected_review_sha256,
    }
    retention.write_new(workspace / "request.json", checkpoint.canonical(request))
    retention.write_new(workspace / "lock", b"")
    retention.write_new(
        workspace / "journal.json",
        checkpoint.canonical(
            {
                "schema": SCHEMA,
                "phase": "RESTORING",
                "selected_files": 0,
                "request_sha256": hashlib.sha256(checkpoint.canonical(request)).hexdigest(),
            }
        ),
    )
    return {"status": "REHEARSAL_PREPARED", "production_changes": False}


def _manifest(workspace, request):
    capture = workspace / "capture.zip"
    info = capture.lstat()
    require(
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and info.st_uid == os.geteuid()
        and not info.st_mode & 0o077
        and info.st_size <= checkpoint.MAX_BYTES + 32 * 1024**2,
        "CAPTURE_CUSTODY",
    )
    require(checkpoint.sha(capture) == request["capture_sha256"], "CAPTURE_DIGEST")
    with zipfile.ZipFile(capture) as archive:
        require(archive.getinfo("checkpoint-manifest.json").file_size <= 32 * 1024**2, "MANIFEST_SIZE")
        manifest = checkpoint.manifest_check(decode(archive.read("checkpoint-manifest.json")))
    require(manifest["namespace"] == "executor", "EXECUTOR_CAPTURE_REQUIRED")
    return manifest


def _verify(root, manifest, removed=()):
    expected = {name: row for name, row in manifest["entries"].items() if name not in removed}
    inventory, directories = checkpoint.inventory(root, "executor")
    require(set(inventory) == set(expected) and directories == manifest["directories"], "RESTORED_INVENTORY")
    require(checkpoint.verify_files(root, expected, "executor") == manifest["state"], "RESTORED_STATE")
    # Some SQLite validators connect to copies; verify that they changed no bytes.
    require(all(checkpoint.sha(root / name) == entry["sha256"] for name, entry in expected.items()), "VERIFIER_MUTATED")


def _result(document):
    return {
        "schema": SCHEMA,
        "status": document["phase"],
        "selected_files": document["selected_files"],
        "production_changes": False,
        "all_references_proven": False,
        "submission_enabled": False,
        "apply_available": False,
    }


@contextmanager
def _opened(workspace):
    workspace = retention.isolated(workspace)
    retention.directory(workspace)
    require(retention.read(workspace / "lock", 1) == b"", "LOCK_CONTENT")
    with os.fdopen(os.open(workspace / "lock", os.O_RDONLY | os.O_NOFOLLOW), "rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        request_raw = retention.read(workspace / "request.json", 4096)
        request = decode(request_raw)
        require(request.get("schema") == SCHEMA, "REQUEST_SCHEMA")
        for name in ("plan", "review"):
            require(
                hashlib.sha256(retention.read(workspace / (name + ".json"))).hexdigest() == request[name + "_sha256"],
                "REQUEST_INPUT_CHANGED",
            )
        document = decode(retention.read(workspace / "journal.json", 4096))
        require(
            document.get("schema") == SCHEMA
            and document.get("phase") in PHASES
            and document.get("request_sha256") == hashlib.sha256(request_raw).hexdigest()
            and type(document.get("selected_files")) is int
            and 0 <= document["selected_files"] <= 30000,
            "JOURNAL_BINDING",
        )
        manifest = _manifest(workspace, request)
        yield workspace, request, document, manifest


@contextmanager
def completed_evidence(workspace):
    """Reverify a completed rehearsal while holding its exclusive workflow lock."""
    with _opened(workspace) as (workspace, request, document, manifest):
        require(document["phase"] == "REHEARSAL_COMPLETE", "REHEARSAL_NOT_COMPLETE")
        bound = {name: retention.read(workspace / name) for name in
                 ("request.json", "journal.json", "plan.json", "review.json")}

        def verify():
            require(all(retention.read(workspace / name) == raw for name, raw in bound.items()),
                    "REHEARSAL_INPUT_CHANGED")
            require(_manifest(workspace, request) == manifest, "CAPTURE_CHANGED")
            root, archive = workspace / "restored", workspace / "retention"
            _verify(root, manifest)
            if document["selected_files"]:
                with retention.transaction(root, archive) as (_, _, entries):
                    require(len(entries) == document["selected_files"], "JOURNAL_ARCHIVE_COUNT")
                    require(all(p == s for s, _, p in retention.placements(root, archive, entries)),
                            "ROLLBACK_INCOMPLETE")

        verify()
        yield deepcopy(request), deepcopy(manifest), _result(document)
        verify()


def resume(workspace):
    with _opened(workspace) as (workspace, request, document, manifest):
        root, archive = workspace / "restored", workspace / "retention"

        def phase(value):
            document["phase"] = value
            _save(workspace / "journal.json", document)

        if document["phase"] in ("REHEARSAL_COMPLETE", "REHEARSAL_FAILED_ROLLED_BACK"):
            _verify(root, manifest)
            if document["selected_files"]:
                with retention.transaction(root, archive) as (_, _, entries):
                    require(len(entries) == document["selected_files"], "JOURNAL_ARCHIVE_COUNT")
                    require(
                        all(p == s for s, _, p in retention.placements(root, archive, entries)), "ROLLBACK_INCOMPLETE"
                    )
            return _result(document)
        if document["phase"] == "RESTORING":
            if not root.exists():
                checkpoint.restore_private_capture(
                    workspace / "capture.zip", root, expected_sha256=request["capture_sha256"]
                )
            _verify(root, manifest)
            phase("PREPARING")
        if document["phase"] == "PREPARING":
            _verify(root, manifest)
            if not archive.exists():
                prepared = retention.prepare_reviewed(
                    root,
                    archive,
                    plan=workspace / "plan.json",
                    review=workspace / "review.json",
                    expected_review_sha256=request["review_sha256"],
                )
                if prepared["status"] == "NO_ELIGIBLE_RECORDS":
                    phase("REHEARSAL_COMPLETE")
                    return _result(document)
            # A partially written preparation never licenses moving records.
            with retention.transaction(root, archive) as (_, _, entries):
                document["selected_files"] = len(entries)
            phase("APPLYING")
        failed = document["phase"] == "RECOVERY_REQUIRED"
        try:
            if document["phase"] == "APPLYING":
                retention.apply(root, archive)
                phase("VERIFYING")
            if document["phase"] == "VERIFYING":
                with retention.transaction(root, archive) as (_, _, entries):
                    located = retention.placements(root, archive, entries)
                    require(all(p == t for _, t, p in located), "ARCHIVE_INCOMPLETE")
                    removed = {entry["name"] for entry in entries}
                _verify(root, manifest, removed)
                phase("ROLLING_BACK")
            retention.rollback(root, archive)
            _verify(root, manifest)
        except Exception:
            phase("RECOVERY_REQUIRED")
            try:
                retention.rollback(root, archive)
                _verify(root, manifest)
            except Exception:
                return _result(document)
            failed = True
        phase("REHEARSAL_FAILED_ROLLED_BACK" if failed else "REHEARSAL_COMPLETE")
        return _result(document)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    new = sub.add_parser("prepare")
    for name in ("workspace", "capture", "plan", "review", "expected-capture-sha256", "expected-review-sha256"):
        new.add_argument("--" + name, required=True)
    again = sub.add_parser("resume")
    again.add_argument("--workspace", required=True)
    args = vars(parser.parse_args(argv))
    action = args.pop("action")
    previous = os.umask(0o077)
    try:
        if action == "prepare":
            prepare(**args)
        result = resume(args["workspace"])
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] == "REHEARSAL_COMPLETE" else 3
    except Exception:
        print(json.dumps({"status": "REHEARSAL_INCOMPLETE", "production_changes": False, "apply_available": False}))
        return 3
    finally:
        os.umask(previous)


if __name__ == "__main__":
    raise SystemExit(main())
