"""Root-owned staging receipts from actual current-window preparation outputs.

No operator CLI or grant issuance. Root callers must hold the paused window lock.
Copies retain all source evidence and never replace existing archive requests.
"""

import hashlib
import os
import stat

from live.execution.demo_authority import _read_root_owned_json, _REGISTRY_PATH
from services.validation.demo_submission import validate_registry, registry_sha256
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_maintenance_preparation as preparation
from tools import demo_executor_archive_read as reader
from tools import demo_executor_archive_write as writer
from tools import demo_executor_retention_grant as grants
from tools import demo_retention_transaction as io
from tools.demo_namespace_checkpoint import canonical, MAX_BYTES


def _identity(record):
    registry = validate_registry(decode(_read_root_owned_json(_REGISTRY_PATH)))
    io.require(
        record["executor_uid"] == registry["executor_uid"] and record["registry_sha256"] == registry_sha256(registry),
        "STAGING_IDENTITY",
    )
    grants.verify_source(record["source_commit"])
    return registry


def _directory(path, uid):
    io.require(not any(p.is_symlink() for p in (path, *path.parents)), "STAGING_PATH")
    info = path.lstat()
    io.require(
        stat.S_ISDIR(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o077, "STAGING_DIRECTORY_CUSTODY"
    )
    return info


def _archive_parent():
    path = reader.ARCHIVES.parent
    io.require(not any(p.is_symlink() for p in (path, *path.parents)), "STAGING_PATH")
    for parent in (path, *path.parents):
        info = parent.lstat()
        io.require(
            stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, "STAGING_PARENT_CUSTODY"
        )


def _file(path, uid, digest, *, destination=None):
    _directory(path.parent, uid)
    before = path.lstat()
    large = path.name == "capture.zip"
    maximum = MAX_BYTES + 32 * 1024**2 if large else 16 * 1024**2
    io.require(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == uid
        and before.st_nlink == 1
        and not before.st_mode & 0o077
        and 0 < before.st_size <= maximum,
        "STAGING_FILE_CUSTODY",
    )

    def identity(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    hasher = hashlib.sha256()
    parts = []
    out = None
    try:
        if destination is not None:
            out = os.fdopen(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
            io.require(identity(os.fstat(stream.fileno())) == identity(before), "STAGING_FILE_CHANGED")
            size = 0
            while chunk := stream.read(4 * 1024**2):
                size += len(chunk)
                io.require(size <= before.st_size, "STAGING_FILE_CHANGED")
                hasher.update(chunk)
                if not large:
                    parts.append(chunk)
                if out is not None:
                    out.write(chunk)
            io.require(
                size == before.st_size
                and identity(os.fstat(stream.fileno())) == identity(before) == identity(path.lstat()),
                "STAGING_FILE_CHANGED",
            )
        io.require(hasher.hexdigest() == digest, "STAGING_INPUT_DIGEST")
        if out is not None:
            out.flush()
            os.fsync(out.fileno())
            os.fchown(out.fileno(), uid, uid)
            os.fsync(out.fileno())
    finally:
        if out is not None:
            out.close()
    if destination is not None:
        io.sync(destination.parent)
    return b"".join(parts) if not large else None


def _context(root, document):
    io.require(os.getresuid() == (0, 0, 0), "OPERATOR_ROOT_REQUIRED")
    io.require(document["phase"] == "PAUSED", "WINDOW_NOT_PAUSED")
    record = preparation.inspect(root, document)
    io.require(record is not None and record["complete"], "PREPARATION_NOT_COMPLETE")
    preparation.restore_guard(root, document)
    _identity(record)
    result = record["jobs"][-1]["result"]["result"]["summary"]
    info = _directory(reader.EXECUTOR, record["executor_uid"])
    return {
        "schema": "DEMO_MAINTENANCE_STAGED_INPUTS_V1",
        "window_sha256": document["inhibitor_sha256"],
        "archive_id": document["operation_sha256"],
        "executor_uid": record["executor_uid"],
        "registry_sha256": record["registry_sha256"],
        "source_commit": record["source_commit"],
        "root_identity": [info.st_dev, info.st_ino],
        "inputs": result["inputs"],
        "selected_files": result["selected_files"],
        "authorization_issued": False,
    }


def _validate(bodies, context):
    writer._validate_inputs(bodies, context)
    report = decode(bodies["review.json"])
    io.require(report.get("plan_sha256") == context["inputs"]["plan.json"], "STAGING_REVIEW_BINDING")
    io.require(
        type(report.get("candidate_files")) is int
        and type(report.get("pinned_files")) is int
        and report["candidate_files"] - report["pinned_files"] == context["selected_files"],
        "STAGING_SELECTION_BINDING",
    )


def stage(root, document):
    context = _context(root, document)
    # A receipt already exists only for a complete prior stage. Never overwrite it.
    if (root / "staged.json").exists() or (root / "staged.json").is_symlink():
        raise FileExistsError("STAGING_ALREADY_EXISTS")
    uid = context["executor_uid"]
    source = preparation.worker.workspace(document["inhibitor_sha256"]) / "capture-work" / "inputs"
    _directory(source, uid)
    _archive_parent()
    for path in (reader.ARCHIVES, reader.ARCHIVES / "requests"):
        if not path.exists() and not path.is_symlink():
            path.mkdir(mode=0o700)
            os.chown(path, uid, uid)
            io.sync(path.parent)
        _directory(path, uid)
    destination = reader.ARCHIVES / "requests" / context["archive_id"]
    destination.mkdir(mode=0o700)
    os.chown(destination, uid, uid)
    io.sync(destination.parent)
    bodies = {
        name: _file(source / name, uid, digest, destination=destination / name)
        for name, digest in sorted(context["inputs"].items())
    }
    _validate(bodies, context)
    context["approval_sha256"] = hashlib.sha256(canonical(context)).hexdigest()
    io.write_new(root / "staged.json", canonical(context))
    return context


def bound_receipt(root, document):
    """Validate root custody/context without requiring backup files for rollback."""
    context = _context(root, document)
    context["approval_sha256"] = hashlib.sha256(canonical(context)).hexdigest()
    io.require(decode(io.read(root / "staged.json")) == context, "STAGING_RECEIPT_CHANGED")
    return context


def load(root, document):
    context = bound_receipt(root, document)
    uid = context["executor_uid"]
    _archive_parent()
    for path in (reader.ARCHIVES, reader.ARCHIVES / "requests"):
        _directory(path, uid)
    destination = reader.ARCHIVES / "requests" / context["archive_id"]
    bodies = {name: _file(destination / name, uid, digest) for name, digest in context["inputs"].items()}
    _validate(bodies, context)
    return context


def verify_unmoved(root, document, *, approved_sha256):
    """Prove a failed PREPARE left every reviewed candidate at its source path."""
    context = bound_receipt(root, document)
    io.require(context["approval_sha256"] == approved_sha256, "MAINTENANCE_APPROVAL_BINDING")
    path = reader.ARCHIVES / "requests" / context["archive_id"] / "plan.json"
    plan = decode(_file(path, context["executor_uid"], context["inputs"]["plan.json"]))
    io.require(type(plan.get("directories")) is dict and set(plan["directories"]) == io.SCOPES, "PLAN_SCOPE")
    seen = set()
    for scope, value in plan["directories"].items():
        for row in value["candidates"]:
            name = scope + "/" + row["sha256"] + ".json"
            io.relative(name)
            io.require(name not in seen and len(seen) < 30000, "CANDIDATE_SET")
            seen.add(name)
            raw = _file(reader.EXECUTOR / name, context["executor_uid"], row["sha256"])
            io.require(len(raw) == row["bytes"], "STAGING_FILE_CHANGED")
    return {"status": "SOURCE_CANDIDATES_INTACT", "candidate_files": len(seen), "approval_sha256": approved_sha256}
