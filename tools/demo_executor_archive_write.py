"""Grant-bound executor retention worker, without a CLI or grant issuer.

Only a separately controlled root maintenance coordinator can provide the sealed
descriptor. Its bound verification receipt attests a completed backup restore /
rehearsal; this worker checks the actual capture hash and candidate coverage.
No credentials, broker connection, identity switch, or trading-policy changes.
"""

import hashlib
import os
import stat

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_retention_transaction as io
from tools import demo_executor_archive_read as reader
from tools import demo_executor_retention_grant as grants
from tools import demo_namespace_checkpoint as checkpoint
from tools._demo_retention_storage import Storage
from tools.demo_archive_barrier import executor_barrier


def _hash_capture(path, lease):
    before = path.lstat()
    io.require(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == os.geteuid()
        and before.st_nlink == 1
        and not before.st_mode & 0o077
        and 0 < before.st_size <= checkpoint.MAX_BYTES + 32 * 1024**2,
        "MAINTENANCE_CAPTURE_CUSTODY",
    )

    def identity(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    digest = hashlib.sha256()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        io.require(identity(os.fstat(stream.fileno())) == identity(before), "MAINTENANCE_CAPTURE_CHANGED")
        size = 0
        while True:
            lease.check()
            part = stream.read(4 * 1024**2)
            if not part:
                break
            size += len(part)
            io.require(size <= before.st_size, "MAINTENANCE_CAPTURE_CHANGED")
            digest.update(part)
        io.require(
            size == before.st_size
            and identity(os.fstat(stream.fileno())) == identity(before) == identity(path.lstat()),
            "MAINTENANCE_CAPTURE_CHANGED",
        )
    return digest.hexdigest()


def _paths(lease):
    doc = lease.document
    reader._private_root(reader.EXECUTOR)
    reader._private_root(reader.ARCHIVES)
    io.require(
        reader.EXECUTOR != reader.ARCHIVES
        and reader.EXECUTOR not in reader.ARCHIVES.parents
        and reader.ARCHIVES not in reader.EXECUTOR.parents,
        "ARCHIVE_LOCATION",
    )
    info = reader.EXECUTOR.lstat()
    io.require([info.st_dev, info.st_ino] == doc["root_identity"], "MAINTENANCE_ROOT_CHANGED")
    requests = reader.ARCHIVES / "requests"
    inputs = requests / doc["archive_id"]
    if doc["action"] != "ROLLBACK":
        io.directory(requests)
        io.directory(inputs)
    archive = reader.ARCHIVES / doc["archive_id"]
    io.require(not archive.is_symlink(), "ARCHIVE_PATH")
    return inputs, archive


def _inputs(inputs, lease):
    doc = lease.document
    bodies = {}
    for name in sorted(grants.INPUTS - {"capture.zip"}):
        lease.check()
        bodies[name] = io.read(inputs / name)
        io.require(hashlib.sha256(bodies[name]).hexdigest() == doc["inputs"][name], "MAINTENANCE_INPUT_DIGEST")
    io.require(_hash_capture(inputs / "capture.zip", lease) == doc["inputs"]["capture.zip"], "MAINTENANCE_INPUT_DIGEST")
    return _validate_inputs(bodies, doc)


def _validate_inputs(bodies, doc):
    """Pure binding checks shared with the root evidence staging coordinator."""
    proof = decode(bodies["verification.json"])
    expected = {
        "schema": "DEMO_RETENTION_BACKUP_VERIFICATION_V1",
        "status": "REHEARSAL_COMPLETE",
        "capture_sha256": doc["inputs"]["capture.zip"],
        "manifest_sha256": doc["inputs"]["backup-manifest.json"],
        "plan_sha256": doc["inputs"]["plan.json"],
        "review_sha256": doc["inputs"]["review.json"],
    }
    io.require(proof == expected, "MAINTENANCE_BACKUP_VERIFICATION")
    manifest = checkpoint.manifest_check(decode(bodies["backup-manifest.json"]))
    io.require(
        manifest["namespace"] == "executor" and manifest["source_uid"] == doc["executor_uid"],
        "MAINTENANCE_BACKUP_IDENTITY",
    )
    plan = decode(bodies["plan.json"])
    io.require(type(plan.get("directories")) is dict and set(plan["directories"]) == io.SCOPES, "PLAN_SCOPE")
    # Every reviewed candidate, including pins, must be represented by the exact
    # backed-up bytes. Whole-namespace restore verification belongs to the issuer.
    for scope, rows in plan["directories"].items():
        for entry in rows["candidates"]:
            name = scope + "/" + entry["sha256"] + ".json"
            io.relative(name)
            io.require(
                manifest["entries"].get(name)
                == {"sha256": entry["sha256"], "size": entry["bytes"], "snapshot_method": "STABLE_FILE_READ"},
                "MAINTENANCE_CANDIDATE_NOT_BACKED_UP",
            )
    return bodies


class GuardedIO:
    def __init__(self, lease, archive):
        self.lease, self.archive = lease, archive

    def __getattr__(self, name):
        return getattr(io, name)

    def read(self, path, maximum=16 * 1024**2):
        self.lease.check()
        raw = io.read(path, maximum)
        if path == self.archive / "manifest.json" and self.lease.document["action"] != "PREPARE":
            io.require(
                hashlib.sha256(raw).hexdigest() == self.lease.document["manifest_sha256"], "MAINTENANCE_MANIFEST_GRANT"
            )
            receipt = io.read(self.archive / "maintenance.json")
            io.require(
                hashlib.sha256(receipt).hexdigest() == self.lease.document["receipt_sha256"],
                "MAINTENANCE_RECEIPT_GRANT",
            )
        return raw

    def write_new(self, path, raw):
        self.lease.check()
        return io.write_new(path, raw)

    def move(self, source, target):
        self.lease.check()
        io.move(source, target)
        self.lease.check()


def prepare(grant_fd):
    with grants.admitted(grant_fd, "PREPARE") as lease:
        inputs, archive = _paths(lease)
        with executor_barrier(reader.EXECUTOR):
            _inputs(inputs, lease)
            storage = Storage(GuardedIO(lease, archive), schema=reader.SCHEMA)
            result = storage.prepare_reviewed(
                reader.EXECUTOR,
                archive,
                plan=inputs / "plan.json",
                review=inputs / "review.json",
                expected_review_sha256=lease.document["inputs"]["review.json"],
            )
            if result["status"] == "NO_ELIGIBLE_RECORDS":
                return result
            lease.check()
            # A failed/partial preparation cannot be applied without this receipt
            # and a second action-specific root grant binding its exact bytes.
            io.write_new(archive / "maintenance.json", lease.raw)
            return result | {
                "manifest_sha256": hashlib.sha256(io.read(archive / "manifest.json")).hexdigest(),
                "receipt_sha256": hashlib.sha256(lease.raw).hexdigest(),
                "production_data_archived": False,
            }


def _mutate(grant_fd, action):
    with grants.admitted(grant_fd, action) as lease:
        inputs, archive = _paths(lease)
        with executor_barrier(reader.EXECUTOR):
            if action != "ROLLBACK":
                _inputs(inputs, lease)
            receipt_raw = io.read(archive / "maintenance.json")
            io.require(
                hashlib.sha256(receipt_raw).hexdigest() == lease.document["receipt_sha256"], "MAINTENANCE_RECEIPT_GRANT"
            )
            receipt = decode(receipt_raw)
            fields = {"archive_id", "root_identity", "executor_uid", "registry_sha256", "inputs", "source_commit"}
            if action == "APPLY":
                fields.add("window_sha256")
            io.require(
                receipt.get("schema") == "DEMO_RETENTION_WORKER_GRANT_V1"
                and receipt.get("action") == "PREPARE"
                and all(receipt.get(key) == lease.document[key] for key in fields),
                "MAINTENANCE_RECEIPT_CONTEXT",
            )
            storage = Storage(GuardedIO(lease, archive), schema=reader.SCHEMA)
            operation = storage.apply if action == "APPLY" else storage.rollback
            result = operation(reader.EXECUTOR, archive)
            return result | {"production_data_archived": action == "APPLY", "submission_enabled": False}


def apply(grant_fd):
    return _mutate(grant_fd, "APPLY")


def rollback(grant_fd):
    return _mutate(grant_fd, "ROLLBACK")
