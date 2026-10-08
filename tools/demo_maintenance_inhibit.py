"""Root-operated runtime start inhibition for the fixed Demo maintenance scope.

No CLI or service commands. The coordinator must reload systemd, verify effective
conditions, drain jobs, and establish writer exclusion separately. File installation
alone never proves activation inhibition or quiescence. Do not use runtime masks:
the installed /etc unit definitions take precedence over /run masks.
"""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from tools.demo_maintenance_services import UNITS, GOLD_UNITS, validate_scope
from tools.demo_retention_transaction import move, require, sync
from services.governance.json_integrity import load_json_without_duplicate_keys

OPERATOR_UID = 0
SYSTEM = Path("/run/systemd/system")
CONTROL = Path("/run/quantos-demo-maintenance")
NAME = "90-quantos-maintenance-inhibit.conf"


def dropin(unit):
    require(unit in GOLD_UNITS, "UNIT_SCOPE")
    return SYSTEM / (unit + ".d") / NAME


def verify_loaded(unit, operation_sha256):
    """Read effective D-Bus conditions, not systemctl's '[unprintable]' field."""
    path = dropin(unit)
    _directory(CONTROL)
    allow = CONTROL / "allow-start"
    require(not allow.exists() and not allow.is_symlink(), "INHIBITOR_ALLOW_PATH")
    require(_existing(path, _payload(operation_sha256)), "INHIBITOR_MISSING")

    def query(args):
        result = subprocess.run(["/usr/bin/busctl", "--system", "--json=short", *args],
                                capture_output=True, check=True, timeout=30)
        require(len(result.stdout) <= 128 * 1024, "INHIBITOR_BUS_RESPONSE")
        return load_json_without_duplicate_keys(result.stdout)

    # GetUnit only finds resident units. An inactive unit can be garbage-collected
    # between systemctl show and that lookup. Property dispatch resolves the unit
    # directly; this neither starts it nor queues an activation job. Unit names
    # already passed the fixed UNITS allowlist in dropin().
    escaped = "".join(char if char.isascii() and char.isalnum() else
                      "_" + format(ord(char), "02x") for char in unit)
    object_path = "/org/freedesktop/systemd1/unit/" + escaped
    value = query(["get-property", "org.freedesktop.systemd1", object_path,
                   "org.freedesktop.systemd1.Unit", "Conditions"])
    require(type(value) is dict and value.get("type") == "a(sbbsi)" and
            type(value.get("data")) is list, "INHIBITOR_BUS_RESPONSE")
    rows = value["data"]
    require(all(type(row) is list and len(row) == 5 and type(row[0]) is str and
                type(row[1]) is bool and type(row[2]) is bool and type(row[3]) is str and
                type(row[4]) is int for row in rows), "INHIBITOR_BUS_RESPONSE")
    require(any(row[:4] == ["ConditionPathExists", False, False, str(allow)] for row in rows),
            "INHIBITOR_NOT_EFFECTIVE")
    require(not allow.exists() and not allow.is_symlink(), "INHIBITOR_ALLOW_PATH")


def _directory(path):
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == OPERATOR_UID
                and not info.st_mode & 0o022, "INHIBITOR_DIRECTORY")


def _payload(token):
    require(type(token) is str and re.fullmatch("[a-f0-9]{64}", token), "INHIBITOR_OPERATION")
    return ("# QUANTOS maintenance operation " + token + "\n[Unit]\n"
            "ConditionPathExists=" + str(CONTROL / "allow-start") + "\n").encode()


@contextmanager
def _locked():
    require(os.geteuid() == OPERATOR_UID, "OPERATOR_ROOT_REQUIRED")
    _directory(SYSTEM)
    _directory(CONTROL.parent)
    CONTROL.mkdir(mode=0o700, exist_ok=True)
    _directory(CONTROL)
    require(not CONTROL.stat().st_mode & 0o077, "INHIBITOR_DIRECTORY")
    # Lock the directory itself: no persistent lock-file creation race.
    fd = os.open(CONTROL, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = CONTROL.stat()
        opened = os.fstat(fd)
        require((current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino), "INHIBITOR_DIRECTORY")
        allow = CONTROL / "allow-start"
        require(not allow.exists() and not allow.is_symlink(), "INHIBITOR_ALLOW_PATH")
        yield
    finally:
        os.close(fd)


def _existing(path, expected):
    try:
        before = path.lstat()
    except FileNotFoundError:
        return False
    require(stat.S_ISREG(before.st_mode) and before.st_uid == OPERATOR_UID
            and before.st_nlink == 1 and not before.st_mode & 0o022
            and before.st_size == len(expected), "INHIBITOR_CONFLICT")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        opened = os.fstat(stream.fileno())
        require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino), "INHIBITOR_CONFLICT")
        require(stream.read(len(expected) + 1) == expected, "INHIBITOR_CONFLICT")
    return True


def _paths(expected,units=UNITS):
    paths = [SYSTEM / (unit + ".d") / NAME for unit in validate_scope(units)]
    # Check the entire existing set before the first mutation.
    for path in paths:
        if path.parent.exists() or path.parent.is_symlink():
            _directory(path.parent)
            _existing(path, expected)
    return paths


def _publish(path, raw):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".quantos-inhibit-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            # No-replace atomic publication; an interrupted write is never loaded
            # by systemd because the temporary name does not end in .conf.
            move(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
            sync(path.parent)


def install(operation_sha256,*,units=UNITS):
    raw = _payload(operation_sha256)
    with _locked():
        paths = _paths(raw,units)
        for path in paths:
            if not path.parent.exists():
                path.parent.mkdir(mode=0o755)
                sync(SYSTEM)
            if not _existing(path, raw):
                _publish(path, raw)
        return {"status": "DROPINS_INSTALLED_RELOAD_REQUIRED", "files": len(paths),
                "activation_verified": False, "quiescence_proven": False}


def remove(operation_sha256,*,units=UNITS):
    raw = _payload(operation_sha256)
    with _locked():
        paths = _paths(raw,units)
        for path in paths:
            if path.parent.exists() and _existing(path, raw):
                path.unlink()
                sync(path.parent)
        # Leave shared drop-in directories and the private control directory.
        return {"status": "DROPINS_REMOVED_RELOAD_REQUIRED", "activation_verified": False,
                "quiescence_proven": False}
