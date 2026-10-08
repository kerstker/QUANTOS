"""Scoped maintenance action issuance from current-window staged evidence.

Trusted root coordinator component; no CLI, keys, trading grants or arbitrary
payload sealing API. The caller holds the paused window lock and checks services.
"""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_maintenance_staging as staging
from tools import demo_executor_retention_grant as grants
from tools import demo_retention_transaction as io
from tools.demo_namespace_checkpoint import canonical


def _transaction(root, context, previous):
    path = root / "archive-transaction.json"
    if not path.exists() and not path.is_symlink():
        io.require(
            previous is not None
            and previous["action"] == "PREPARE"
            and previous["result"] is not None
            and previous["result"]["status"] == "WORKER_COMPLETED",
            "MAINTENANCE_PREPARE_REQUIRED",
        )
        summary = previous["result"]["result"]["summary"]
        io.require(
            summary["status"] == "PREPARED" and summary["receipt_sha256"] == previous["grant_sha256"],
            "MAINTENANCE_PREPARE_BINDING",
        )
        record = {
            "schema": "DEMO_MAINTENANCE_TRANSACTION_BINDING_V1",
            "approval_sha256": context["approval_sha256"],
            "manifest_sha256": summary["manifest_sha256"],
            "receipt_sha256": summary["receipt_sha256"],
        }
        _check_transaction(context, record)
        io.write_new(path, canonical(record))
    record = decode(io.read(path, 4096))
    io.require(
        type(record) is dict
        and set(record) == {"schema", "approval_sha256", "manifest_sha256", "receipt_sha256"}
        and record["schema"] == "DEMO_MAINTENANCE_TRANSACTION_BINDING_V1"
        and record["approval_sha256"] == context["approval_sha256"]
        and all(grants.digest(record[k]) for k in ("manifest_sha256", "receipt_sha256")),
        "MAINTENANCE_TRANSACTION_BINDING",
    )
    _check_transaction(context, record)
    return record


def _check_transaction(context, record):
    archive = staging.reader.ARCHIVES / context["archive_id"]
    staging._archive_parent()
    staging._directory(staging.reader.ARCHIVES, context["executor_uid"])
    staging._file(archive / "manifest.json", context["executor_uid"], record["manifest_sha256"])
    raw = staging._file(archive / "maintenance.json", context["executor_uid"], record["receipt_sha256"])
    receipt = decode(raw)
    fields = (
        "archive_id",
        "window_sha256",
        "root_identity",
        "executor_uid",
        "registry_sha256",
        "source_commit",
        "inputs",
    )
    io.require(
        receipt.get("schema") == "DEMO_RETENTION_WORKER_GRANT_V1"
        and receipt.get("action") == "PREPARE"
        and all(receipt.get(k) == context[k] for k in fields),
        "MAINTENANCE_RECEIPT_CONTEXT",
    )


@contextmanager
def issue(root, document, *, action, approved_sha256):
    from tools import demo_maintenance_window as window

    io.require(os.getresuid() == (0, 0, 0), "OPERATOR_ROOT_REQUIRED")
    io.require(action in {"PREPARE", "APPLY", "ROLLBACK"}, "MAINTENANCE_ACTION")
    context = (staging.bound_receipt if action == "ROLLBACK" else staging.load)(root, document)
    io.require(
        grants.digest(approved_sha256) and approved_sha256 == context["approval_sha256"], "MAINTENANCE_APPROVAL_BINDING"
    )
    previous = window._worker_record(root, document)
    transaction = None
    if action == "PREPARE":
        io.require(previous is None, "MAINTENANCE_ALREADY_DISPATCHED")
    else:
        if action == "APPLY":
            io.require(
                previous is not None
                and previous["action"] == "PREPARE"
                and previous["result"] is not None
                and previous["result"]["status"] == "WORKER_COMPLETED",
                "MAINTENANCE_PREPARE_REQUIRED",
            )
        else:
            io.require(
                previous is not None and previous["action"] in {"APPLY", "ROLLBACK"}, "MAINTENANCE_ROLLBACK_REQUIRED"
            )
        transaction = _transaction(root, context, previous)
    if previous is not None:
        io.require(window.handoff.drained(previous["unit"]), "WINDOW_WORKER_STILL_RUNNING")
    now = time.monotonic_ns()
    pid, ticks = grants._identity(Path("/proc") / str(os.getpid()))
    body = {
        "schema": "DEMO_RETENTION_WORKER_GRANT_V1",
        "action": action,
        **{
            k: context[k]
            for k in (
                "source_commit",
                "executor_uid",
                "registry_sha256",
                "root_identity",
                "archive_id",
                "window_sha256",
                "inputs",
            )
        },
        "boot_id": grants.boot_id(),
        "controller_pid": pid,
        "controller_start_ticks": ticks,
        "issued_monotonic_ns": now,
        "expires_monotonic_ns": now + 600 * 10**9,
        "manifest_sha256": None if transaction is None else transaction["manifest_sha256"],
        "receipt_sha256": None if transaction is None else transaction["receipt_sha256"],
    }
    raw = canonical(body)
    fd = os.memfd_create("quantos-maintenance-action", os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
    try:
        io.require(os.write(fd, raw) == len(raw), "MAINTENANCE_GRANT_WRITE")
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, grants.SEALS)
        io.require(os.pread(fd, 65536, 0) == raw, "MAINTENANCE_GRANT_WRITE")
        yield fd
    finally:
        os.close(fd)
