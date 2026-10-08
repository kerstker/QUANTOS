"""Bounded reads for full continuous-account inputs, retaining original bytes."""
import os
import stat

from tools.gold_cost_review import _read

SESSION_INPUT_BYTES = 64 * 1024 * 1024
ACCOUNT_CHAIN_BYTES = 256 * 1024 * 1024


def read(root, name):
    if name != 'inputs.json':
        return _read(root, name)
    path = root/name
    if path.resolve() != path.absolute():
        raise ValueError('GOLD_SESSION_UNSAFE_PATH')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), 'rb') as handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('GOLD_SESSION_UNSAFE_PATH')
        if before.st_size > SESSION_INPUT_BYTES:
            raise ValueError('GOLD_SESSION_INPUT_TOO_LARGE')
        raw = handle.read(SESSION_INPUT_BYTES+1)
        after = os.fstat(handle.fileno())
        named = path.stat(follow_symlinks=False)
    def identity(value):
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if len(raw) > SESSION_INPUT_BYTES:
        raise ValueError('GOLD_SESSION_INPUT_TOO_LARGE')
    if len(raw) != before.st_size or identity(before) != identity(after) or identity(after) != identity(named):
        raise ValueError('GOLD_SESSION_INPUT_CHANGED')
    return raw
