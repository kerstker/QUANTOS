"""Hold existing executor writer locks for a separately authorized maintenance job.

No CLI, credentials, service operations, file creation or archive permission.
These locks cover cooperating writers only. Older resident publishers that write
after releasing cycle.lock and controller/manual readers need separate exclusion.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat

LOCKS = ('observation.lock', 'origin-capture.lock', 'resident-recovery/cycle.lock')


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def identity(info):
    return info.st_dev, info.st_ino


def private_directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
            and not info.st_mode & 0o077, 'ARCHIVE_DIRECTORY_CUSTODY')
    return identity(info)


@contextmanager
def executor_barrier(root):
    root = Path(root).absolute()
    require(not any(p.is_symlink() for p in (root, *root.parents)), 'ARCHIVE_ROOT_PATH')
    directories = {root: private_directory(root)}
    descriptors = []
    try:
        for name in LOCKS:
            path = root / name
            directories.setdefault(path.parent, private_directory(path.parent))
            before = path.lstat()
            require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                    and before.st_nlink == 1 and before.st_size == 0
                    and not before.st_mode & 0o077, 'ARCHIVE_LOCK_CUSTODY')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            descriptors.append((path, fd, identity(before)))
            require(identity(os.fstat(fd)) == identity(before), 'ARCHIVE_LOCK_CHANGED')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('ARCHIVE_WRITER_BUSY') from None

        def verify():
            for path, original in directories.items():
                require(private_directory(path) == original, 'ARCHIVE_DIRECTORY_CHANGED')
            for path, fd, original in descriptors:
                info = path.lstat()
                require(identity(info) == original == identity(os.fstat(fd))
                        and stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                        and info.st_size == 0 and info.st_uid == os.geteuid()
                        and not info.st_mode & 0o077, 'ARCHIVE_LOCK_CHANGED')

        verify()
        yield
        verify()
    finally:
        for _, fd, _ in reversed(descriptors):
            os.close(fd)
