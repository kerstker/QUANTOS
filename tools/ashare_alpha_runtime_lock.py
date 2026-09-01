"""Cross-process single-flight lock for the admitted A-share research workers."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import threading
from typing import Iterator

from quantos.runtime_paths import runtime_state_root


DEFAULT_RUNTIME_LOCK = (
    runtime_state_root() / "data" / "cache" / "ashare_alpha_runtime.lock"
)
_IN_PROCESS_LOCK = threading.Lock()


class AShareRuntimeLockError(RuntimeError):
    """The fixed single-flight lock could not be opened safely."""


def _lock_fd(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt

        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock_fd(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def acquire_runtime_lock(
    path: str | Path = DEFAULT_RUNTIME_LOCK,
) -> Iterator[bool]:
    """Attempt one non-blocking lock acquisition and yield its result.

    The file is retained after release so every process continues to address
    the same inode.  Both the in-process mutex and OS file lock are required:
    the former makes thread-pool behavior deterministic, while the latter
    arbitrates the systemd service and the localhost research runner.
    """

    if not _IN_PROCESS_LOCK.acquire(blocking=False):
        yield False
        return

    fd: int | None = None
    acquired = False
    try:
        lock_path = Path(path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(lock_path, flags, 0o600)
        except OSError as exc:
            raise AShareRuntimeLockError("A-share runtime lock cannot be opened") from exc
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise AShareRuntimeLockError("A-share runtime lock is not a regular file")
        if os.name == "posix" and metadata.st_mode & 0o077:
            raise AShareRuntimeLockError("A-share runtime lock permissions exceed 0600")
        acquired = _lock_fd(fd)
        yield acquired
    finally:
        try:
            if fd is not None and acquired:
                _unlock_fd(fd)
        finally:
            if fd is not None:
                os.close(fd)
            _IN_PROCESS_LOCK.release()


__all__ = [
    "AShareRuntimeLockError",
    "DEFAULT_RUNTIME_LOCK",
    "acquire_runtime_lock",
]
