"""Operator-only read-only inventory; never moves/deletes files or changes services.

Produces a private review plan outside production state. Candidate selection is
NOT permission to archive: cross-record reference review remains outstanding.
No credentials, database, policy, grant, or kill-switch paths are accessed.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

ROOT = Path("/var/lib/quantos-demo-executor")
SCOPES = (
    ("observation-views", 1024),
    ("execution-origins/BTCUSDC", 128),
    ("execution-origins/BTCUSDT", 128),
    ("resident-recovery/views", 1024),
)
MAX_FILES = 30000
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_DIRECTORY_BYTES = 256 * 1024 * 1024
RECENT_MS = 3600000


class PlanError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise PlanError(code)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def strict_json(data):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "DUPLICATE_JSON_FIELD")
            result[key] = value
        return result

    def constant(_):
        raise PlanError("NONFINITE_JSON")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError) as error:
        raise PlanError("INVALID_JSON") from error


def read_regular(path, uid, maximum):
    try:
        info = path.lstat()
        require(
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == uid and not info.st_mode & 0o077,
            "FILE_CUSTODY",
        )
        require(info.st_size <= maximum, "FILE_BUDGET")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            require(identity(os.fstat(stream.fileno())) == identity(info), "FILE_CHANGED")
            data = stream.read(maximum + 1)
            require(identity(os.fstat(stream.fileno())) == identity(info), "FILE_CHANGED")
        require(identity(path.lstat()) == identity(info) and len(data) == info.st_size, "FILE_CHANGED")
        return data, info
    except OSError as error:
        raise PlanError("FILE_UNAVAILABLE") from error


def inspect_directory(directory, *, keep_count, now_ms):
    require(type(keep_count) is int and keep_count > 0, "KEEP_COUNT")
    require(type(now_ms) is int and now_ms > 0, "OBSERVATION_TIME")
    try:
        before = directory.lstat()
        require(stat.S_ISDIR(before.st_mode) and not before.st_mode & 0o077, "DIRECTORY_CUSTODY")
        uid = before.st_uid
        paths = []
        with os.scandir(directory) as entries:
            for entry in entries:
                paths.append(Path(entry.path))
                require(len(paths) <= MAX_FILES, "DIRECTORY_FILE_BUDGET")
        pointer_bytes, _ = read_regular(directory / "latest.json", uid, 1024)
        pointer = strict_json(pointer_bytes)
        require(
            type(pointer) is dict
            and set(pointer) == {"sha256"}
            and type(pointer["sha256"]) is str
            and re.fullmatch("[a-f0-9]{64}", pointer["sha256"]),
            "LATEST_POINTER",
        )
        rows = []
        total = len(pointer_bytes)
        for path in sorted(paths):
            if path.name == "latest.json":
                continue
            require(re.fullmatch(r"[a-f0-9]{64}\.json", path.name), "UNEXPECTED_ENTRY")
            data, info = read_regular(path, uid, MAX_FILE_BYTES)
            total += len(data)
            require(total <= MAX_DIRECTORY_BYTES, "DIRECTORY_BYTE_BUDGET")
            digest = hashlib.sha256(data).hexdigest()
            require(digest == path.stem, "CONTENT_DIGEST")
            body = strict_json(data)
            require(
                type(body) is dict and type(body.get("observed_at_ms")) is int and 0 < body["observed_at_ms"] <= now_ms,
                "RECORD_TIME",
            )
            rows.append(
                {
                    "sha256": digest,
                    "bytes": len(data),
                    "observed_at_ms": body["observed_at_ms"],
                    "identity": list(identity(info)),
                }
            )
        require(pointer["sha256"] in {row["sha256"] for row in rows}, "LATEST_MISSING")
        require(identity(directory.lstat()) == identity(before), "DIRECTORY_CHANGED")
        require(read_regular(directory / "latest.json", uid, 1024)[0] == pointer_bytes, "LATEST_CHANGED")
        newest = sorted(rows, key=lambda r: (r["observed_at_ms"], r["sha256"]), reverse=True)
        kept = {r["sha256"] for r in newest[:keep_count]} | {pointer["sha256"]}
        kept |= {r["sha256"] for r in rows if r["observed_at_ms"] >= now_ms - RECENT_MS}
        candidates = [r for r in rows if r["sha256"] not in kept]
        return {
            "entries": len(paths),
            "bytes": total,
            "latest_sha256": pointer["sha256"],
            "latest_pointer_bytes_sha256": hashlib.sha256(pointer_bytes).hexdigest(),
            "keep_minimum": keep_count,
            "protect_recent_ms": RECENT_MS,
            "retained": [r for r in rows if r["sha256"] in kept],
            "candidates": candidates,
            "atomic_snapshot": False,
            "reference_integrity_verified": False,
            "semantic_validation_complete": False,
            "apply_available": False,
        }
    except OSError as error:
        raise PlanError("DIRECTORY_UNAVAILABLE") from error


def build():
    """Observe the four fixed executor scopes under the original executor UID."""
    require(not any(p.is_symlink() for p in (ROOT, *ROOT.parents)), "SYMLINK_PARENT")
    root_info = ROOT.lstat()
    require(os.getuid() == os.geteuid() == root_info.st_uid != 0, "ORIGINAL_EXECUTOR_IDENTITY_REQUIRED")
    require(stat.S_ISDIR(root_info.st_mode) and not root_info.st_mode & 0o077, "ROOT_CUSTODY")
    observed = time.time_ns() // 1000000
    directories = {}
    for name, keep in SCOPES:
        current = ROOT
        for part in Path(name).parts:
            current = current / part
            info = current.lstat()
            require(
                stat.S_ISDIR(info.st_mode) and info.st_uid == root_info.st_uid and not info.st_mode & 0o077,
                "PARENT_CUSTODY",
            )
        directories[name] = inspect_directory(current, keep_count=keep, now_ms=observed)
    plan = {
        "schema": "DEMO_RETENTION_REVIEW_PLAN_V1",
        "observed_at_ms": observed,
        "status": "REFERENCE_REVIEW_REQUIRED",
        "directories": directories,
        "production_changes": False,
        "credentials_read": False,
        "atomic_snapshot": False,
        "apply_available": False,
    }
    return plan
