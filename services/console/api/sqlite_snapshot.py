"""Stable, WAL-aware SQLite snapshots for physically read-only console views."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from typing import Iterator


_CHUNK_SIZE = 1024 * 1024
_MAX_ATTEMPTS = 3


class SqliteSnapshotError(RuntimeError):
    """The SQLite authority could not be copied without observing a change."""


class _SnapshotChanged(SqliteSnapshotError):
    pass


@dataclass(frozen=True)
class _Fingerprint:
    device: int
    inode: int
    size: int
    mtime_ns: int
    sha256: str


def _metadata(value: os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _regular_lstat(path: Path) -> os.stat_result:
    try:
        result = path.lstat()
    except FileNotFoundError as exc:
        raise _SnapshotChanged(f"SQLite snapshot source disappeared: {path.name}") from exc
    if not stat.S_ISREG(result.st_mode):
        raise SqliteSnapshotError(
            f"SQLite snapshot source must be a regular non-symlink file: {path.name}"
        )
    return result


def _source_flags() -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _read_or_copy(path: Path, destination: Path | None) -> _Fingerprint:
    before = _regular_lstat(path)
    descriptor = None
    digest = hashlib.sha256()
    copied = 0
    try:
        descriptor = os.open(path, _source_flags())
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _metadata(opened) != _metadata(before):
            raise _SnapshotChanged(f"SQLite snapshot source identity changed: {path.name}")
        output = destination.open("xb") if destination is not None else None
        try:
            while True:
                chunk = os.read(descriptor, _CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                copied += len(chunk)
                if output is not None:
                    output.write(chunk)
            if output is not None:
                output.flush()
                os.fsync(output.fileno())
        finally:
            if output is not None:
                output.close()
        closed_read = os.fstat(descriptor)
    except (OSError, ValueError) as exc:
        raise _SnapshotChanged(f"SQLite snapshot source could not be read: {path.name}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    after = _regular_lstat(path)
    if (
        _metadata(before) != _metadata(closed_read)
        or _metadata(before) != _metadata(after)
        or copied != after.st_size
    ):
        raise _SnapshotChanged(f"SQLite snapshot source changed while copied: {path.name}")
    return _Fingerprint(*_metadata(after), digest.hexdigest())


def _is_present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _capture_once(source: Path, destination_dir: Path) -> Path:
    wal = Path(f"{source}-wal")
    destination = destination_dir / source.name
    destination_wal = Path(f"{destination}-wal")

    main_before = _read_or_copy(source, None)
    wal_present_before = _is_present(wal)
    wal_before = _read_or_copy(wal, None) if wal_present_before else None

    main_copied = _read_or_copy(source, destination)
    wal_copied = (
        _read_or_copy(wal, destination_wal) if wal_present_before else None
    )

    wal_present_after = _is_present(wal)
    if wal_present_after != wal_present_before:
        raise _SnapshotChanged("SQLite WAL presence changed while copied")
    main_after = _read_or_copy(source, None)
    wal_after = _read_or_copy(wal, None) if wal_present_after else None
    if not (
        main_before == main_copied == main_after
        and wal_before == wal_copied == wal_after
    ):
        raise _SnapshotChanged("SQLite authority changed while copied")
    return destination


@contextmanager
def stable_sqlite_snapshot(
    source: Path,
    *,
    attempts: int = _MAX_ATTEMPTS,
    temp_parent: Path | None = None,
) -> Iterator[Path]:
    """Yield a stable main+WAL copy without writing beside ``source``.

    ``-shm`` is deliberately not copied. SQLite reconstructs it only beside the
    private temporary copy, allowing live committed WAL rows to remain visible
    while the authority directory is never mutated by the console reader.
    """
    source = Path(os.path.abspath(source))
    if type(attempts) is not int or attempts < 1:
        raise ValueError("attempts must be a positive integer")
    temporary_base = Path(
        tempfile.gettempdir() if temp_parent is None else temp_parent
    ).resolve()
    authority_directory = source.parent.resolve()
    if temporary_base == authority_directory or temporary_base.is_relative_to(
        authority_directory
    ):
        raise SqliteSnapshotError(
            "SQLite snapshot scratch must be outside the authority directory"
        )
    parent = os.fspath(temporary_base)
    last_error: _SnapshotChanged | None = None
    with tempfile.TemporaryDirectory(
        prefix="quantos-console-sqlite-", dir=parent
    ) as temporary:
        temporary_root = Path(temporary)
        for attempt in range(attempts):
            attempt_root = temporary_root / f"attempt-{attempt + 1}"
            attempt_root.mkdir(mode=0o700)
            try:
                snapshot = _capture_once(source, attempt_root)
            except _SnapshotChanged as exc:
                last_error = exc
                continue
            try:
                yield snapshot
            finally:
                pass
            return
    raise SqliteSnapshotError(
        "SQLite authority did not remain stable during snapshot"
    ) from last_error


__all__ = ["SqliteSnapshotError", "stable_sqlite_snapshot"]
