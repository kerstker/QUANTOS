"""Single root operator entrypoint for scoped Demo rehearsal/archive and recovery.

Requires an installed, fully validated source release. No sudo password handling,
credential access, service deployment, trading approval or automatic live orders.
Archive consent is requested only after the actual batch is prepared and shown.
"""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import select
import signal
import sys
import tempfile

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_maintenance_window as window
from tools import demo_maintenance_preparation as preparation
from tools import demo_maintenance_inhibit as inhibit
from tools.demo_executor_retention_grant import digest, verify_source
from tools.demo_namespace_checkpoint import canonical
from tools.demo_retention_transaction import directory, read, require, sync, write_new

ROOT_PARENT = Path("/var/tmp")
SCHEMA = "DEMO_OPERATOR_MAINTENANCE_V1"


def _admit(source_commit):
    require(os.getresuid() == (0, 0, 0), "OPERATOR_ROOT_REQUIRED")
    verify_source(source_commit)


def _base(operation):
    require(type(operation) is str and re.fullmatch("[a-f0-9]{64}", operation), "OPERATOR_OPERATION")
    path = ROOT_PARENT / ("quantos-maintenance-" + operation)
    require(not any(p.is_symlink() for p in (path, *path.parents)), "OPERATOR_PATH")
    return path


@contextmanager
def exclusive():
    require(os.getresuid() == (0, 0, 0), "OPERATOR_ROOT_REQUIRED")
    # Separate inode from the inhibitor directory lock, which each service
    # install/remove operation acquires independently.
    with inhibit._locked():
        path = inhibit.CONTROL / "operator.lock"
        if not path.exists() and not path.is_symlink():
            write_new(path, b"")
        require(read(path, 1) == b"", "OPERATOR_LOCK")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            before = path.lstat()
            require((before.st_dev, before.st_ino) == (os.fstat(fd).st_dev, os.fstat(fd).st_ino), "OPERATOR_LOCK")
        except BaseException:
            os.close(fd)
            raise
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _archive_state(previous):
    if previous is None or previous["action"] == "PREPARE":
        return "NOT_RUN"
    result = previous["result"]
    if result is not None and result["status"] == "WORKER_COMPLETED":
        return "ARCHIVED" if previous["action"] == "APPLY" else "ROLLED_BACK"
    return "UNKNOWN"


def _cleanup(root, approved):
    with window._locked(root) as (_, document):
        previous = window._worker_record(root, document)
        if document["phase"] == "RESTORED":
            return _archive_state(previous)
        record = preparation.inspect(root, document)
        if record is not None:
            for job in record["jobs"]:
                if not window.handoff.drained(job["unit"]):
                    window.handoff.stop_owned(job["unit"], job["digest"])
                require(window.handoff.drained(job["unit"]), "PREPARATION_WORKER_STILL_RUNNING")
    state = _archive_state(previous)
    if previous is not None and (previous["result"] is None or previous["result"]["status"] != "WORKER_COMPLETED"):
        require(approved is not None, "OPERATOR_APPROVAL_RECORD_REQUIRED")
        if previous["action"] == "PREPARE":
            window.abandon_prepare(root, approved_sha256=approved)
            state = "NOT_RUN"
        else:
            window.recover_archive(root, approved_sha256=approved)
            state = "ROLLED_BACK"
    window.restore(root)
    return state


def _summary(operation, status):
    return {
        "schema": SCHEMA,
        "operation_id": operation,
        "status": status,
        "archive_state": "UNKNOWN",
        "services_restored": False,
        "submission_enabled": False,
        "selected_files": None,
    }


def _save_result(base, result):
    write_new(base / ("result-" + secrets.token_hex(8) + ".json"), canonical(result))
    return result


def _progress(operation, phase):
    print(
        json.dumps(
            {"schema": SCHEMA, "operation_id": operation, "phase": phase, "submission_enabled": False}, sort_keys=True
        ),
        flush=True,
    )


def start(*, source_commit, mode, confirm):
    _admit(source_commit)
    require(mode in {"REHEARSE", "ARCHIVE"}, "OPERATOR_MODE")
    operation = secrets.token_hex(32)
    base = _base(operation)
    base.mkdir(mode=0o700)
    sync(base.parent)
    write_new(
        base / "run.json",
        canonical({"schema": SCHEMA, "operation_id": operation, "source_commit": source_commit, "mode": mode}),
    )
    root = base / "window"
    result = _summary(operation, "MAINTENANCE_INCOMPLETE")
    approved = None
    window_created = False
    try:
        _progress(operation, "PREPARING_WINDOW")
        window.prepare(root, operation)
        window_created = True
        window.pause(root)
        _progress(operation, "CAPTURING_AND_REHEARSING")
        evidence = window.prepare_evidence(root, source_commit=source_commit)
        result["selected_files"] = evidence["selected_files"]
        if mode == "REHEARSE":
            result["status"] = "REHEARSAL_COMPLETE"
        else:
            _progress(operation, "STAGING_VERIFIED_INPUTS")
            batch = window.stage_evidence(root)
            if confirm(batch) is True:
                approved = batch["approval_sha256"]
                write_new(base / "approval.json", canonical({"operation_id": operation, "approved_sha256": approved}))
                _progress(operation, "EXECUTING_APPROVED_ARCHIVE")
                outcome = window.execute_archive(root, approved_sha256=approved)
                result["status"] = outcome["status"]
            else:
                result["status"] = "ARCHIVE_DECLINED"
    except (Exception, KeyboardInterrupt):
        # Never expose source file contents, filesystem errors or worker stderr.
        result["status"] = "MAINTENANCE_INCOMPLETE"
    finally:
        if (root / "window.json").exists():
            try:
                _progress(operation, "RESTORING_SERVICES")
                result["archive_state"] = _cleanup(root, approved)
                result["services_restored"] = True
            except (Exception, KeyboardInterrupt):
                result["status"] = "RECOVERY_REQUIRED"
        else:
            if window_created:
                result["status"] = "RECOVERY_REQUIRED"
            else:
                result["archive_state"] = "NOT_RUN"
    return _save_result(base, result)


def recover(*, source_commit, operation):
    _admit(source_commit)
    base = _base(operation)
    directory(base)
    run = decode(read(base / "run.json", 4096))
    require(
        type(run) is dict
        and set(run) == {"schema", "operation_id", "source_commit", "mode"}
        and run["schema"] == SCHEMA
        and run["operation_id"] == operation
        and run["source_commit"] == source_commit
        and run["mode"] in {"REHEARSE", "ARCHIVE"},
        "OPERATOR_RUN_BINDING",
    )
    approved = None
    path = base / "approval.json"
    if path.exists() or path.is_symlink():
        record = decode(read(path, 4096))
        require(
            type(record) is dict
            and set(record) == {"operation_id", "approved_sha256"}
            and record["operation_id"] == operation
            and digest(record["approved_sha256"]),
            "OPERATOR_APPROVAL_BINDING",
        )
        approved = record["approved_sha256"]
    result = _summary(operation, "RECOVERY_REQUIRED")
    try:
        result["archive_state"] = _cleanup(base / "window", approved)
        result.update(status="RECOVERY_COMPLETE", services_restored=True)
    except (Exception, KeyboardInterrupt):
        pass
    return _save_result(base, result)


def confirm_batch(batch):
    require(sys.stdin.isatty(), "OPERATOR_TTY_REQUIRED")
    print(
        json.dumps(
            {
                "status": "ARCHIVE_APPROVAL_REQUIRED",
                "selected_files": batch["selected_files"],
                "approval_sha256": batch["approval_sha256"],
            },
            sort_keys=True,
        ),
        flush=True,
    )
    print("确认归档本批历史记录请输入 ARCHIVE；其他输入取消并恢复服务（等待上限180秒）：", flush=True)
    if not select.select([sys.stdin], [], [], 180)[0]:
        return False
    return sys.stdin.readline(128).strip() == "ARCHIVE"


def _public_result(result):
    # A fresh root-owned sanitized file is readable by the local operator/agent.
    fd, name = tempfile.mkstemp(prefix="quantos-maintenance-result-", suffix=".json", dir=ROOT_PARENT)
    with os.fdopen(fd, "wb") as stream:
        stream.write(canonical(result))
        stream.flush()
        os.fsync(stream.fileno())
        os.fchmod(stream.fileno(), 0o644)
        os.fsync(stream.fileno())
    sync(ROOT_PARENT)
    return name


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--operator-rehearse", action="store_true")
    mode.add_argument("--operator-archive", action="store_true")
    mode.add_argument("--recover", metavar="OPERATION_ID")
    args = parser.parse_args(argv)
    previous_umask = os.umask(0o077)
    old_handlers = {}

    def interrupted(signum, frame):
        raise KeyboardInterrupt()

    try:
        _admit(args.source_commit)
        if args.operator_archive:
            require(sys.stdin.isatty(), "OPERATOR_TTY_REQUIRED")
        for sig in (signal.SIGTERM, signal.SIGHUP):
            old_handlers[sig] = signal.signal(sig, interrupted)
        with exclusive():
            if args.recover:
                result = recover(source_commit=args.source_commit, operation=args.recover)
            else:
                result = start(
                    source_commit=args.source_commit,
                    mode="ARCHIVE" if args.operator_archive else "REHEARSE",
                    confirm=confirm_batch,
                )
        public = _public_result(result)
        print(json.dumps(result | {"summary_path": public}, sort_keys=True), flush=True)
        return (
            0
            if result["services_restored"]
            and result["status"] in {"REHEARSAL_COMPLETE", "ARCHIVED", "NO_ELIGIBLE_RECORDS", "RECOVERY_COMPLETE"}
            else 3
        )
    except (Exception, KeyboardInterrupt):
        print(json.dumps({"schema": SCHEMA, "status": "OPERATOR_INCOMPLETE", "submission_enabled": False}), flush=True)
        return 3
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
        os.umask(previous_umask)


if __name__ == "__main__":
    raise SystemExit(main())
