"""Fixed-scope operator reference inventory. No archive or service operations.

SQLite files are copied into private scratch space before any SQLite connection.
Source databases, WALs, credentials and authority settings are never written.
This report is non-atomic and never grants permission to move a candidate.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time

EXECUTOR = Path("/var/lib/quantos-demo-executor")
SCOPES = {"observation-views", "execution-origins/BTCUSDC", "execution-origins/BTCUSDT", "resident-recovery/views"}
NAMESPACES = {
    "executor": (
        EXECUTOR,
        (
            "evidence",
            "execution-origins",
            "resident-recovery",
            "receipt-reconciliation",
            "observation-views",
            "stream-capture-results",
            "market-inputs-BTCUSDC",
            "market-inputs-BTCUSDT",
            "request-preparation-results",
            "session-results",
            "cancellation-session-results",
        ),
        (
            "dispatch.sqlite3",
            "recovery.sqlite3",
            "oms.sqlite3",
            "order-events.sqlite3",
            "user-stream.sqlite3",
            "cancellations.sqlite3",
        ),
        (),
    ),
    "issuer": (Path("/var/lib/quantos-demo-issuer"), ("results", "views"), ("issuance.sqlite3",), ()),
    "controller": (
        Path("/var/lib/quantos-demo-controller"),
        (
            "requests",
            "claims",
            "results",
            "tasks",
            "task-claims",
            "task-results",
            "functional-cycle",
            "portfolio-decisions",
            "portfolio-results",
            "portfolio-closes",
            "portfolio-views",
        ),
        (),
        ("installation.json", "portfolio-runtime.json"),
    ),
}
HEX = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


class ReviewError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise ReviewError(reason)


class Budget:
    def __init__(self):
        self.files = self.bytes = self.rows = self.entries = 0
        self.started = time.monotonic()

    def add(self, *, files=0, size=0, rows=0, entries=0):
        self.files += files
        self.bytes += size
        self.rows += rows
        self.entries += entries
        require(
            self.files <= 100000
            and self.bytes <= 2 * 1024**3
            and self.rows <= 500000
            and self.entries <= 100000
            and time.monotonic() - self.started <= 600,
            "REVIEW_BUDGET_EXHAUSTED",
        )


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_file(path, budget, maximum=16 * 1024**2):
    try:
        before = path.lstat()
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and not before.st_mode & 0o022, "FILE_CUSTODY")
        require(before.st_size <= maximum, "FILE_SIZE")
        budget.add(files=1, size=before.st_size)
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            require(identity(os.fstat(stream.fileno())) == identity(before), "FILE_CHANGED")
            data = stream.read(maximum + 1)
            require(identity(os.fstat(stream.fileno())) == identity(before), "FILE_CHANGED")
        require(len(data) == before.st_size and identity(path.lstat()) == identity(before), "FILE_CHANGED")
        return data, before
    except OSError as error:
        raise ReviewError("FILE_UNAVAILABLE") from error


def decode(data):
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def constant(_):
        raise ReviewError("NONFINITE_JSON")

    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError) as error:
        raise ReviewError("INVALID_JSON") from error


def references(value, targets, depth=0):
    require(depth <= 64, "REFERENCE_DEPTH")
    found = set()
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeError as error:
            raise ReviewError("UNSUPPORTED_DATABASE_BLOB") from error
    if isinstance(value, str):
        for token in HEX.findall(value.lower()):
            found.update(targets.get(token, ()))
        if value.lstrip().startswith(("{", "[")):
            found.update(references(decode(value), targets, depth + 1))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.update(references(key, targets, depth + 1))
            found.update(references(item, targets, depth + 1))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(references(item, targets, depth + 1))
    return found


def candidate_identifiers(path, row, budget, bodies=None):
    data, info = read_file(path, budget)
    digest = hashlib.sha256(data).hexdigest()
    require(
        path.name == digest + ".json"
        and row["sha256"] == digest
        and row["bytes"] == len(data)
        and row["identity"] == list(identity(info)),
        "CANDIDATE_CHANGED",
    )
    body = decode(data)
    if bodies is not None:
        bodies[path] = body
    semantic = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return {digest, semantic}


def database_references(path, targets, budget):
    """Freeze DB + WAL bytes, reject changes, then query only the scratch copies."""
    wal = Path(str(path) + "-wal")
    rollback = Path(str(path) + "-journal")
    try:
        require(not (rollback.exists() or rollback.is_symlink()), "ROLLBACK_JOURNAL_PRESENT")
        sources = [path] + ([wal] if wal.exists() or wal.is_symlink() else [])
        before = {p: identity(p.lstat()) for p in sources}
        require(all(p.lstat().st_uid == path.parent.lstat().st_uid for p in sources), "DATABASE_CUSTODY")
        with tempfile.TemporaryDirectory(prefix="quantos-ref-db-", dir="/var/tmp") as scratch:
            hashes = {}
            for source in sources:
                data, info = read_file(source, budget, 512 * 1024**2)
                require(identity(info) == before[source], "DATABASE_CHANGED")
                target = Path(scratch) / source.name
                target.write_bytes(data)
                target.chmod(0o600)
                hashes[source.name] = hashlib.sha256(data).hexdigest()
            require((wal.exists() or wal.is_symlink()) == (wal in before), "DATABASE_CHANGED")
            require(not (rollback.exists() or rollback.is_symlink()), "ROLLBACK_JOURNAL_PRESENT")
            require(all(identity(p.lstat()) == original for p, original in before.items()), "DATABASE_CHANGED")
            connection = sqlite3.connect((Path(scratch) / path.name).as_uri() + "?mode=ro", uri=True)
            try:
                connection.set_progress_handler(lambda: int(time.monotonic() - budget.started > 600), 10000)
                connection.execute("PRAGMA query_only=ON")
                connection.execute("PRAGMA trusted_schema=OFF")
                connection.execute("BEGIN")
                require(connection.execute("PRAGMA quick_check").fetchall() == [("ok",)], "DATABASE_INTEGRITY")
                tables = connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
                found = set()
                count = 0
                for (name,) in tables:
                    quoted = '"' + name.replace('"', '""') + '"'
                    for row in connection.execute("SELECT * FROM " + quoted):
                        budget.add(rows=1, size=sum(len(v) for v in row if isinstance(v, (str, bytes))))
                        count += 1
                        found.update(references(row, targets))
                return found, count, hashes
            finally:
                connection.close()
    except (OSError, sqlite3.Error) as error:
        raise ReviewError("DATABASE_UNAVAILABLE") from error


def directory(path, uid):
    require(not any(p.is_symlink() for p in (path, *path.parents)), "SYMLINK_PARENT")
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o022, "DIRECTORY_CUSTODY")


def walk_json(path, uid, budget, depth=0):
    require(depth <= 32, "DIRECTORY_DEPTH")
    directory(path, uid)
    with os.scandir(path) as entries:
        for entry in entries:
            child = Path(entry.path)
            info = child.lstat()
            require(info.st_uid == uid and not info.st_mode & 0o022, "ENTRY_CUSTODY")
            budget.add(entries=1)
            if stat.S_ISDIR(info.st_mode):
                yield from walk_json(child, uid, budget, depth + 1)
            else:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "ENTRY_CUSTODY")
                if child.suffix == ".lock":
                    continue
                require(child.suffix == ".json", "UNSUPPORTED_REFERENCE_FILE")
                yield child


def _operator():
    require(os.getuid() == os.geteuid() == 0, "OPERATOR_ROOT_REQUIRED")


def review(plan_path, *, expected_sha256):
    _operator()
    plan_path = Path(plan_path)
    require(not any(p.is_symlink() for p in (plan_path, *plan_path.parents)), "SYMLINK_PARENT")
    require(type(expected_sha256) is str and re.fullmatch("[a-f0-9]{64}", expected_sha256), "PLAN_DIGEST")
    budget = Budget()
    executor_uid = EXECUTOR.lstat().st_uid
    directory(EXECUTOR, executor_uid)
    plan_uid = plan_path.lstat().st_uid
    require(plan_uid in {0, executor_uid}, "PLAN_CUSTODY")
    directory(plan_path.parent, plan_uid)
    data, _ = read_file(plan_path, budget)
    require(hashlib.sha256(data).hexdigest() == expected_sha256, "PLAN_DIGEST")
    plan = decode(data)
    require(
        plan.get("schema") == "DEMO_RETENTION_REVIEW_PLAN_V1"
        and plan.get("apply_available") is False
        and set(plan["directories"]) == SCOPES,
        "PLAN_SCOPE",
    )
    candidates = set()
    targets = {}
    bodies = {}
    for name, scope in plan["directories"].items():
        path = EXECUTOR
        for part in Path(name).parts:
            path /= part
            directory(path, executor_uid)
        for row in scope["candidates"]:
            require(type(row.get("sha256")) is str and re.fullmatch("[a-f0-9]{64}", row["sha256"]), "CANDIDATE_NAME")
            candidate = path / (row["sha256"] + ".json")
            require(candidate not in candidates, "DUPLICATE_CANDIDATE")
            candidates.add(candidate)
            for identifier in candidate_identifiers(candidate, row, budget, bodies):
                targets.setdefault(identifier, set()).add(str(candidate.relative_to(EXECUTOR)))
    findings = []
    missing = []
    pinned = set()
    for namespace, (root, directories, databases, files) in NAMESPACES.items():
        uid = root.lstat().st_uid
        directory(root, uid)
        paths = []
        for name in directories:
            path = root / name
            if not path.exists() and not path.is_symlink():
                missing.append(namespace + "/" + name)
                continue
            paths.extend(walk_json(path, uid, budget))
            require(len(paths) <= 100000, "REFERENCE_FILE_BUDGET")
        for name in files:
            path = root / name
            if path.exists() or path.is_symlink():
                paths.append(path)
            else:
                missing.append(namespace + "/" + name)
        for path in paths:
            if path in candidates:
                continue
            require(path.lstat().st_uid == uid, "FILE_CUSTODY")
            data, _ = read_file(path, budget)
            found = references(decode(data), targets)
            pinned.update(found)
            if found:
                findings.append(
                    {
                        "source": namespace + "/" + str(path.relative_to(root)),
                        "source_sha256": hashlib.sha256(data).hexdigest(),
                        "referenced_candidates": sorted(found),
                    }
                )
        for name in databases:
            path = root / name
            if not path.exists() and not path.is_symlink():
                missing.append(namespace + "/" + name)
                continue
            found, rows, hashes = database_references(path, targets, budget)
            pinned.update(found)
            findings.append(
                {
                    "source": namespace + "/" + name,
                    "snapshot_sha256": hashes,
                    "rows": rows,
                    "referenced_candidates": sorted(found),
                }
            )
    direct_pinned = len(pinned)
    pending = list(pinned)
    while pending:
        budget.add()
        current = pending.pop()
        for dependency in references(bodies[EXECUTOR / current], targets) - pinned:
            pinned.add(dependency)
            pending.append(dependency)
    return {
        "schema": "DEMO_RETENTION_REFERENCE_REVIEW_V2",
        "status": "SCOPED_REVIEW_ONLY",
        "plan_sha256": expected_sha256,
        "observed_at_ms": time.time_ns() // 1000000,
        "candidate_files": len(candidates),
        "pinned_files": len(pinned),
        "direct_pinned_files": direct_pinned,
        "transitive_pinned_files": len(pinned) - direct_pinned,
        "candidate_reference_closure_checked": True,
        "pinned_candidates": sorted(pinned),
        "findings": findings,
        "missing_locations": missing,
        "scanned_files": budget.files,
        "scanned_database_rows": budget.rows,
        "all_references_proven": False,
        "cross_namespace_atomic": False,
        "apply_available": False,
        "production_changes": False,
        "credentials_read": False,
        "coarchived_candidate_references_excluded": False,
    }
