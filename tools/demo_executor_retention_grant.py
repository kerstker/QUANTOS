"""Validate a root-owned sealed maintenance descriptor in an existing executor.

This is not a grant issuer or an operator CLI. It cannot authorize trades. A
root coordinator must establish the exclusive maintenance window and
verify the bound backup/reference evidence before delivering this descriptor.
"""

from contextlib import contextmanager
import fcntl
import os
import re
import select
import stat
import time
from pathlib import Path

from live.execution.demo_authority import load_registry_for_executor
from services.validation.demo_submission import registry_sha256
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.demo_issuer import verify_source
from tools.demo_maintenance_processes import _identity, _uids
from tools.demo_retention_transaction import require

INPUTS = {"plan.json", "review.json", "backup-manifest.json", "verification.json", "capture.zip"}
SEALS = fcntl.F_SEAL_SEAL | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_GROW | fcntl.F_SEAL_WRITE
FIELDS = {
    "schema",
    "action",
    "source_commit",
    "executor_uid",
    "registry_sha256",
    "root_identity",
    "archive_id",
    "window_sha256",
    "boot_id",
    "controller_pid",
    "controller_start_ticks",
    "issued_monotonic_ns",
    "expires_monotonic_ns",
    "inputs",
    "manifest_sha256",
    "receipt_sha256",
}


def digest(value):
    return type(value) is str and re.fullmatch("[a-f0-9]{64}", value) is not None


def boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip().replace("-", "")


def _controller(document):
    pid = document["controller_pid"]
    require(type(pid) is int and pid > 0 and type(document["controller_start_ticks"]) is int, "MAINTENANCE_CONTROLLER")
    fd = os.pidfd_open(pid, 0)
    try:
        path = Path("/proc") / str(pid)
        require(
            _identity(path) == (pid, document["controller_start_ticks"]) and _uids(path) == (0, 0, 0, 0),
            "MAINTENANCE_CONTROLLER",
        )
        return fd
    except BaseException:
        os.close(fd)
        raise


class Lease:
    def __init__(self, document, raw, pidfd):
        self.document, self.raw, self.pidfd = document, raw, pidfd
        self.poller = select.poll()
        self.poller.register(pidfd, select.POLLIN)

    def check(self):
        require(not self.poller.poll(0), "MAINTENANCE_CONTROLLER_EXITED")
        require(
            self.document["issued_monotonic_ns"] <= time.monotonic_ns() < self.document["expires_monotonic_ns"],
            "MAINTENANCE_GRANT_EXPIRED",
        )


def read_descriptor(fd, action):
    require(type(fd) is int and fd >= 0 and action in {"PREPARE", "APPLY", "ROLLBACK"}, "MAINTENANCE_GRANT")
    info = os.fstat(fd)
    require(
        stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 0 and 0 < info.st_size <= 65536,
        "MAINTENANCE_GRANT_CUSTODY",
    )
    require(fcntl.fcntl(fd, fcntl.F_GET_SEALS) & SEALS == SEALS, "MAINTENANCE_GRANT_UNSEALED")
    raw = os.pread(fd, 65537, 0)
    require(len(raw) == info.st_size, "MAINTENANCE_GRANT_BYTES")
    doc = decode(raw)
    require(
        type(doc) is dict
        and set(doc) == FIELDS
        and doc["schema"] == "DEMO_RETENTION_WORKER_GRANT_V1"
        and doc["action"] == action,
        "MAINTENANCE_GRANT_SCOPE",
    )
    require(
        all(digest(doc[key]) for key in ("archive_id", "window_sha256", "registry_sha256"))
        and type(doc["inputs"]) is dict
        and set(doc["inputs"]) == INPUTS
        and all(digest(value) for value in doc["inputs"].values()),
        "MAINTENANCE_GRANT_DIGEST",
    )
    require(
        type(doc["root_identity"]) is list
        and len(doc["root_identity"]) == 2
        and all(type(value) is int and value >= 0 for value in doc["root_identity"]),
        "MAINTENANCE_ROOT_IDENTITY",
    )
    require(
        type(doc["issued_monotonic_ns"]) is int
        and type(doc["expires_monotonic_ns"]) is int
        and 0 < doc["expires_monotonic_ns"] - doc["issued_monotonic_ns"] <= 600 * 10**9
        and doc["boot_id"] == boot_id(),
        "MAINTENANCE_GRANT_LIFETIME",
    )
    for field in ("manifest_sha256", "receipt_sha256"):
        require(doc[field] is None if action == "PREPARE" else digest(doc[field]), "MAINTENANCE_GRANT_BINDING")
    return doc, raw


@contextmanager
def admitted(fd, action):
    doc, raw = read_descriptor(fd, action)
    registry = load_registry_for_executor()
    require(
        type(doc["executor_uid"]) is int
        and 0 < os.geteuid() == doc["executor_uid"] == registry["executor_uid"]
        and registry_sha256(registry) == doc["registry_sha256"],
        "MAINTENANCE_EXECUTOR_IDENTITY",
    )
    uid = doc["executor_uid"]
    require(
        os.getresuid() == (uid, uid, uid) and os.getresgid() == (uid, uid, uid) and set(os.getgroups()) <= {uid},
        "MAINTENANCE_EXECUTOR_PRIVILEGES",
    )
    verify_source(doc["source_commit"])
    pidfd = _controller(doc)
    try:
        lease = Lease(doc, raw, pidfd)
        lease.check()
        yield lease
        lease.check()
    finally:
        os.close(pidfd)
