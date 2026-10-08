"""Exact read-only history access inside the independently configured Demo executor.

No CLI, archive discovery, mutations, credential reads or economic authority.
The registry reader consumes public verification configuration only. The caller
must already be the configured executor; neither root nor research is impersonated.
"""

import os
from pathlib import Path
import re
import stat

from live.execution.demo_authority import load_registry_for_executor
from tools import demo_retention_transaction as io
from tools._demo_retention_storage import Storage

EXECUTOR = Path("/var/lib/quantos-demo-executor")
ARCHIVES = Path("/var/lib/quantos-demo-archives")
SCHEMA = "DEMO_EXECUTOR_RETENTION_TRANSACTION_V1"


def _private_root(path):
    io.require(
        path.is_absolute() and ".." not in path.parts and not any(p.is_symlink() for p in (path, *path.parents)),
        "ARCHIVE_PATH",
    )
    io.directory(path)
    for parent in path.parents:
        info = parent.lstat()
        io.require(
            stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, "ARCHIVE_PARENT_CUSTODY"
        )


def lookup(archive_ids, name):
    """Return original bytes by exact content-addressed path, never latest fallback."""
    registry = load_registry_for_executor()
    io.require(
        type(registry.get("executor_uid")) is int and 0 < registry["executor_uid"] == os.geteuid(),
        "ARCHIVE_EXECUTOR_IDENTITY",
    )
    io.require(
        type(archive_ids) is list
        and len(archive_ids) <= 32
        and all(type(value) is str and re.fullmatch("[a-f0-9]{64}", value) for value in archive_ids)
        and len(archive_ids) == len(set(archive_ids)),
        "ARCHIVE_SET",
    )
    io.relative(name)
    _private_root(EXECUTOR)
    archives = []
    if archive_ids:
        _private_root(ARCHIVES)
        io.require(
            EXECUTOR != ARCHIVES and EXECUTOR not in ARCHIVES.parents and ARCHIVES not in EXECUTOR.parents,
            "ARCHIVE_PATH",
        )
        for identity in archive_ids:
            path = ARCHIVES / identity
            io.require(not path.is_symlink(), "ARCHIVE_PATH")
            io.directory(path)
            archives.append(path)
    return Storage(io, schema=SCHEMA).lookup_many(EXECUTOR, archives, name)
