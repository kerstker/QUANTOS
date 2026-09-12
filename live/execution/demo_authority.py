"""Read deployment-controlled Demo verification keys in the isolated executor.

Only public keys are read. The path is fixed, every path component is opened
without following symlinks, and root ownership/write permissions are checked
from open file descriptors. No caller/env key path and no test-key fallback.
This is not a signer, mandate constructor, or ordinary submission bypass.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.validation.demo_submission import DemoSubmissionRejected, parse_envelope, validate_registry, verify

_REGISTRY_PATH = Path('/etc/quantos/demo-submission/authority.json')
# Process-local nonce: new starts and forked children have distinct identities.
_BOOT_PID = os.getpid()
_BOOT_NONCE = os.urandom(32)


def executor_boot_sha256():
    global _BOOT_PID, _BOOT_NONCE
    if os.getpid()!=_BOOT_PID:
        _BOOT_PID=os.getpid();_BOOT_NONCE=os.urandom(32)
    return hashlib.sha256(b'QUANTOS_DEMO_EXECUTOR_BOOT_V1\0'+str(_BOOT_PID).encode()+b'\0'+_BOOT_NONCE).hexdigest()


def _require_protected(info, *, directory):
    expected=stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not expected or info.st_uid!=0 or info.st_mode & (stat.S_IWGRP|stat.S_IWOTH):
        raise DemoSubmissionRejected('DEMO_REGISTRY_CUSTODY')
    if not directory and (info.st_nlink!=1 or not 0<info.st_size<=65536):
        raise DemoSubmissionRejected('DEMO_REGISTRY_FILE')


def _read_root_owned_json(path):
    """Descriptor-relative traversal also prevents symlink replacement races."""
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise DemoSubmissionRejected('DEMO_REGISTRY_PATH')
    current=None;leaf=None
    try:
        current=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
        _require_protected(os.fstat(current),directory=True)
        for part in path.parts[1:-1]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=current)
            os.close(current);current=child
            _require_protected(os.fstat(current),directory=True)
        leaf=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=current)
        before=os.fstat(leaf);_require_protected(before,directory=False)
        chunks=[];size=0
        while size<=65536:
            data=os.read(leaf,min(8192,65537-size))
            if not data:break
            chunks.append(data);size+=len(data)
        after=os.fstat(leaf);_require_protected(after,directory=False)
        fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
        if size!=before.st_size or any(getattr(before,k)!=getattr(after,k) for k in fields):
            raise DemoSubmissionRejected('DEMO_REGISTRY_CHANGED_DURING_READ')
        return b''.join(chunks)
    except DemoSubmissionRejected:
        raise
    except OSError:
        raise DemoSubmissionRejected('DEMO_REGISTRY_UNAVAILABLE') from None
    finally:
        if leaf is not None:os.close(leaf)
        if current is not None:os.close(current)


def load_registry_for_executor():
    try:
        registry=validate_registry(load_json_without_duplicate_keys(_read_root_owned_json(_REGISTRY_PATH)))
    except DemoSubmissionRejected:
        raise
    except Exception:
        raise DemoSubmissionRejected('DEMO_REGISTRY_INVALID') from None
    if os.geteuid()!=registry['executor_uid']:
        raise DemoSubmissionRejected('DEMO_EXECUTOR_IDENTITY')
    return registry


def verify_for_executor(envelope_bytes, *, expected_context, orders, now_ms):
    registry=load_registry_for_executor()
    if type(expected_context) is not dict or expected_context.get('executor_boot_sha256')!=executor_boot_sha256():
        raise DemoSubmissionRejected('DEMO_EXECUTOR_BOOT')
    return verify(parse_envelope(envelope_bytes),registry=registry,expected_context=expected_context,orders=orders,now_ms=now_ms)
