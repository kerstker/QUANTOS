"""Linux service-lifetime ownership, held before restart reconciliation."""
from contextlib import contextmanager
import os
from pathlib import Path
import stat


@contextmanager
def runner_ownership(path):
    if os.name != 'posix':
        raise RuntimeError('RUNNER_OWNERSHIP_PLATFORM_UNSUPPORTED')
    import fcntl
    path = Path(path).absolute()
    if path.resolve() != path:
        raise RuntimeError('RUNNER_OWNERSHIP_UNSAFE_PATH')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1:
            raise RuntimeError('RUNNER_OWNERSHIP_UNSAFE_FILE')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('RUNNER_ALREADY_OWNED') from None
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
