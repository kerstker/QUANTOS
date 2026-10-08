"""Isolated retention transaction development primitive, not a production entrypoint.

Call only on offline copies. Protected runtime roots are rejected. Candidate/reference
approval and service quiescence are deliberately NOT inferred by this module.
An immutable manifest precedes renames; source/archive placement is the recovery log.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
from contextlib import contextmanager
import sys

from tools._demo_retention_storage import Storage

from services.governance.json_integrity import load_json_without_duplicate_keys

SCOPES = {"observation-views", "resident-recovery/views", "execution-origins/BTCUSDC", "execution-origins/BTCUSDT"}
PROTECTED = (
    "/var/lib/quantos-demo-archives",
    "/var/lib/quantos-demo-executor",
    "/var/lib/quantos-demo-issuer",
    "/var/lib/quantos-demo-controller",
    "/srv/quantos",
    "/opt/quantos",
)


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def isolated(path):
    path = Path(os.path.abspath(path))
    resolved = path.resolve()
    require(not any(resolved == Path(p) or Path(p) in resolved.parents for p in PROTECTED), "ISOLATED_ONLY")
    require(not any(p.is_symlink() for p in (path, *path.parents)), "SYMLINK_PARENT")
    return path


def directory(path):
    info = path.lstat()
    require(
        stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o077, "DIRECTORY_CUSTODY"
    )
    return info


def read(path, maximum=16 * 1024**2):
    before = path.lstat()
    require(
        stat.S_ISREG(before.st_mode)
        and before.st_uid == os.geteuid()
        and before.st_nlink == 1
        and not before.st_mode & 0o077
        and before.st_size <= maximum,
        "FILE_CUSTODY",
    )

    def identity(s):
        return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        require(identity(os.fstat(stream.fileno())) == identity(before), "FILE_CHANGED")
        raw = stream.read(maximum + 1)
        require(identity(os.fstat(stream.fileno())) == identity(before), "FILE_CHANGED")
    require(len(raw) == before.st_size and identity(path.lstat()) == identity(before), "FILE_CHANGED")
    return raw


def sync(directory_path):
    fd = os.open(directory_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, raw):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync(path.parent)


def move(source, target):
    # Atomic no-replace: an unexpected destination can never be overwritten.
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    require(function is not None, "ATOMIC_NOREPLACE_UNAVAILABLE")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, errno.errorcode.get(code, "RENAME_FAILED"))
    sync(target.parent)
    sync(source.parent)


def relative(name):
    require(type(name) is str, "RECORD_PATH")
    path = PurePosixPath(name)
    require(
        path.as_posix() == name
        and path.parent.as_posix() in SCOPES
        and re.fullmatch(r"[a-f0-9]{64}\.json", path.name) is not None,
        "RECORD_PATH",
    )
    return path


def parents(root, name):
    path = root
    for part in PurePosixPath(name).parent.parts:
        path /= part
        directory(path)


def latest_protected(root, names):
    for scope in {str(relative(n).parent) for n in names}:
        raw = load_json_without_duplicate_keys(read(root / scope / "latest.json", 1024))
        require(
            type(raw) is dict
            and set(raw) == {"sha256"}
            and type(raw["sha256"]) is str
            and re.fullmatch("[a-f0-9]{64}", raw["sha256"]),
            "LATEST_POINTER",
        )
        target = scope + "/" + raw["sha256"] + ".json"
        require(target not in names, "LATEST_SELECTED")
        require(hashlib.sha256(read(root / target)).hexdigest() == raw["sha256"], "LATEST_TARGET")


def _storage():
    # Resolve IO through this module so existing fault-injection and custody
    # checks still exercise the same public adapter after engine extraction.
    return Storage(sys.modules[__name__], schema="ISOLATED_RETENTION_TRANSACTION_V1")


def _isolated_result(result):
    if result["status"] in {"PREPARED", "ARCHIVED", "RESTORED"}:
        return result | {"status": result["status"] + "_ISOLATED"}
    return result


def prepare(root, archive, names):
    return _prepare(root, archive, names, evidence={})


def _prepare(root, archive, names, *, evidence):
    root, archive = isolated(root), isolated(archive)
    return _isolated_result(_storage()._prepare(root, archive, names, evidence=evidence))


@contextmanager
def transaction(root, archive):
    root, archive = isolated(root), isolated(archive)
    with _storage().transaction(root, archive) as opened:
        yield opened


def placements(root, archive, entries):
    return _storage().placements(isolated(root), isolated(archive), entries)


def apply(root, archive):
    return _isolated_result(_storage().apply(isolated(root), isolated(archive)))


def rollback(root, archive):
    return _isolated_result(_storage().rollback(isolated(root), isolated(archive)))


def prepare_reviewed(root, archive, *, plan, review, expected_review_sha256):
    """Bind the same scoped review to offline bytes; never promote its authority."""
    root, archive = isolated(root), isolated(archive)
    plan, review = isolated(plan), isolated(review)
    return _isolated_result(
        _storage().prepare_reviewed(
            root, archive, plan=plan, review=review, expected_review_sha256=expected_review_sha256
        )
    )


def lookup(root, archive, name):
    """Read exact historical bytes, never substitute latest or infer authority."""
    return _storage().lookup(isolated(root), isolated(archive), name)


def lookup_many(root, archives, name):
    """Read an explicit bounded archive set; all paths remain isolated-only."""
    root = isolated(root)
    require(type(archives) is list and len(archives) <= 32, "ARCHIVE_SET")
    return _storage().lookup_many(root, [isolated(path) for path in archives], name)
