"""Shared fail-closed configuration boundary for L2 backup consumers.

This module contains no backup worker entry point and performs no publication.
It centralizes the external-destination proof and the read-only passphrase
configuration checks used by the backup worker and its independent watchdog.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import stat
import sys
from typing import Callable, Iterable


PASSPHRASE_ENV = "QUANTOS_BACKUP_PASSPHRASE"
PASSPHRASE_FILE_ENV = "QUANTOS_BACKUP_PASSPHRASE_FILE"
CREDENTIALS_DIRECTORY_ENV = "CREDENTIALS_DIRECTORY"
SYSTEMD_PASSPHRASE_CREDENTIAL = "l2-backup-passphrase"
EXTERNAL_DESTINATION_CLASSES = frozenset(
    {"OFF_HOST_UNC", "SEPARATE_VOLUME", "SEPARATE_FILESYSTEM"}
)
# Compatibility name retained for existing governance consumers.
_EXTERNAL_DESTINATION_CLASSES = EXTERNAL_DESTINATION_CLASSES
_MAX_PASSPHRASE_FILE_BYTES = 4_096
_MIN_PASSPHRASE_LENGTH = 12
_PERSISTENT_LINUX_FILESYSTEMS = frozenset(
    {
        "bcachefs",
        "btrfs",
        "ceph",
        "cifs",
        "ext2",
        "ext3",
        "ext4",
        "f2fs",
        "fuseblk",
        "glusterfs",
        "jfs",
        "lustre",
        "nfs",
        "nfs4",
        "ntfs",
        "ntfs3",
        "reiserfs",
        "smb3",
        "xfs",
        "zfs",
    }
)
_NON_DURABLE_LINUX_SOURCES = frozenset(
    {"", "-", "none", "overlay", "proc", "ramfs", "rootfs", "sysfs", "tmpfs"}
)


class L2BackupError(RuntimeError):
    """The backup configuration cannot prove safety or recoverability."""


def _is_posix_platform() -> bool:
    return os.name == "posix"


def _filesystem_device_id(path: os.PathLike[str] | str) -> int:
    """Return ``st_dev`` for a path or its nearest existing parent."""

    candidate = Path(os.fspath(path))
    while True:
        try:
            return candidate.stat().st_dev
        except FileNotFoundError:
            parent = candidate.parent
            if parent == candidate:
                raise
            candidate = parent


def _decode_mountinfo_path(value: str) -> str:
    """Decode only the four escapes defined for Linux mountinfo fields."""

    escapes = {
        r"\040": " ",
        r"\011": "\t",
        r"\012": "\n",
        r"\134": "\\",
    }
    decoded: list[str] = []
    offset = 0
    while offset < len(value):
        if value[offset] != "\\":
            decoded.append(value[offset])
            offset += 1
            continue
        encoded = value[offset : offset + 4]
        replacement = escapes.get(encoded)
        if replacement is None:
            raise ValueError("unsupported mountinfo escape")
        decoded.append(replacement)
        offset += 4
    return "".join(decoded)


def _filesystem_device_numbers(device_id: int) -> tuple[int, int] | None:
    """Return the platform major/minor pair for a Linux ``st_dev`` value."""

    major = getattr(os, "major", None)
    minor = getattr(os, "minor", None)
    if not callable(major) or not callable(minor):
        return None
    try:
        return int(major(device_id)), int(minor(device_id))
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def _mount_provenance_from_lines(
    path: PurePosixPath,
    device_numbers: tuple[int, int],
    lines: Iterable[str],
) -> tuple[str, str] | None:
    """Return the deepest matching mount's ``(source, fstype)``."""

    if (
        not path.is_absolute()
        or len(device_numbers) != 2
        or any(type(number) is not int or number < 0 for number in device_numbers)
    ):
        return None
    best: tuple[int, int, str, str] | None = None
    for index, line in enumerate(lines):
        before, separator, after = line.partition(" - ")
        fields = before.split()
        trailing = after.split()
        if not separator or len(fields) < 6 or len(trailing) < 3:
            return None
        device_fields = fields[2].split(":", 1)
        try:
            row_device = tuple(int(field) for field in device_fields)
            mount_point = PurePosixPath(_decode_mountinfo_path(fields[4]))
            source = _decode_mountinfo_path(trailing[1])
        except (TypeError, ValueError):
            return None
        if (
            len(row_device) != 2
            or any(number < 0 for number in row_device)
            or not mount_point.is_absolute()
        ):
            return None
        if row_device != device_numbers:
            continue
        try:
            path.relative_to(mount_point)
        except ValueError:
            continue
        candidate = (
            len(mount_point.parts),
            index,
            source,
            trailing[0].casefold(),
        )
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return None if best is None else (best[2], best[3])


def _resolved_posix_path(path: os.PathLike[str] | str) -> PurePosixPath:
    return PurePosixPath(os.fspath(Path(path).resolve(strict=True)))


def _read_linux_mountinfo_lines() -> tuple[str, ...] | None:
    """Read one coherent-enough mount table snapshot without invoking a shell."""

    if not _is_posix_platform() or not sys.platform.startswith("linux"):
        return None
    try:
        return tuple(
            Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
        )
    except (OSError, UnicodeError):
        return None


def _posix_destination_is_durable(
    repo_root: Path,
    destination_root: Path,
    *,
    repo_device_id: int | None = None,
    destination_device_id: int | None = None,
    filesystem_device_id: Callable[[os.PathLike[str] | str], int] | None = None,
    is_posix_platform: Callable[[], bool] | None = None,
    resolved_posix_path: Callable[[os.PathLike[str] | str], PurePosixPath]
    | None = None,
    filesystem_device_numbers: Callable[[int], tuple[int, int] | None]
    | None = None,
    read_linux_mountinfo_lines: Callable[[], tuple[str, ...] | None] | None = None,
    mount_provenance_from_lines: Callable[
        [PurePosixPath, tuple[int, int], Iterable[str]], tuple[str, str] | None
    ]
    | None = None,
) -> bool:
    """Prove a distinct Linux mount source backed by a durable filesystem."""

    device_id_fn = filesystem_device_id or _filesystem_device_id
    is_posix_fn = is_posix_platform or _is_posix_platform
    resolve_fn = resolved_posix_path or _resolved_posix_path
    device_numbers_fn = filesystem_device_numbers or _filesystem_device_numbers
    mountinfo_fn = read_linux_mountinfo_lines or _read_linux_mountinfo_lines
    provenance_fn = mount_provenance_from_lines or _mount_provenance_from_lines
    if not is_posix_fn():
        return True
    if not sys.platform.startswith("linux"):
        # Keep the pre-hardening POSIX behavior on platforms without mountinfo.
        return True
    try:
        repo_device_id = (
            device_id_fn(repo_root) if repo_device_id is None else repo_device_id
        )
        destination_device_id = (
            device_id_fn(destination_root)
            if destination_device_id is None
            else destination_device_id
        )
        repo_path = resolve_fn(repo_root)
        destination_path = resolve_fn(destination_root)
    except (OSError, TypeError, ValueError):
        return False
    if repo_device_id == destination_device_id:
        return False
    repo_device_numbers = device_numbers_fn(repo_device_id)
    destination_device_numbers = device_numbers_fn(destination_device_id)
    lines = mountinfo_fn()
    if (
        repo_device_numbers is None
        or destination_device_numbers is None
        or lines is None
    ):
        return False
    repo_provenance = provenance_fn(repo_path, repo_device_numbers, lines)
    destination_provenance = provenance_fn(
        destination_path, destination_device_numbers, lines
    )
    if repo_provenance is None or destination_provenance is None:
        return False
    repo_source, _repo_filesystem_type = repo_provenance
    source, filesystem_type = destination_provenance
    if filesystem_type not in _PERSISTENT_LINUX_FILESYSTEMS:
        return False
    return bool(
        source
        and source.casefold() not in _NON_DURABLE_LINUX_SOURCES
        and repo_source
        and source != repo_source
    )


def classify_destination(
    repo_root: Path,
    destination_root: Path,
    *,
    filesystem_device_id: Callable[[os.PathLike[str] | str], int] | None = None,
    posix_destination_is_durable: Callable[..., bool] | None = None,
) -> str:
    """Classify a backup destination without treating POSIX ``//`` as UNC."""

    device_id_fn = filesystem_device_id or _filesystem_device_id
    durable_fn = posix_destination_is_durable or _posix_destination_is_durable
    destination_drive = destination_root.drive.casefold()
    if destination_drive.startswith("\\\\"):
        return "OFF_HOST_UNC"
    repo_drive = repo_root.drive.casefold()
    if repo_drive and destination_drive and repo_drive != destination_drive:
        return "SEPARATE_VOLUME"
    if not repo_drive and not destination_drive:
        try:
            repo_device_id = device_id_fn(repo_root)
            destination_device_id = device_id_fn(destination_root)
            if repo_device_id != destination_device_id and durable_fn(
                repo_root,
                destination_root,
                repo_device_id=repo_device_id,
                destination_device_id=destination_device_id,
            ):
                return "SEPARATE_FILESYSTEM"
        except OSError:
            pass
    return "LOCAL_ONLY"


def _validate_passphrase_file_mode(
    mode: int, *, systemd_managed: bool = False
) -> None:
    permissions = stat.S_IMODE(mode)
    if systemd_managed:
        # LoadCredential= exposes a read-only copy inside a per-unit directory
        # that systemd makes accessible only to the unit UID (and root).  On
        # systems without id-mapped credential mounts this copy may be 0440 so
        # the configured service group can read it.  Preserve the directory
        # isolation contract while still rejecting all write/execute/other
        # access.
        if (
            permissions & 0o337
            or not permissions & 0o400
        ):
            raise L2BackupError(
                "systemd backup credential permissions are unsafe"
            )
        return
    if permissions & 0o077:
        raise L2BackupError(
            "backup passphrase file permissions must be owner-only"
        )


def _is_systemd_credential_path(path: Path) -> bool:
    directory = os.environ.get(CREDENTIALS_DIRECTORY_ENV)
    if not directory:
        return False
    credential_directory = Path(directory)
    return bool(
        credential_directory.is_absolute()
        and path == credential_directory / SYSTEMD_PASSPHRASE_CREDENTIAL
    )


def _validate_passphrase_strength(passphrase: object) -> None:
    if (
        not isinstance(passphrase, str)
        or not passphrase.strip()
        or len(passphrase.strip()) < _MIN_PASSPHRASE_LENGTH
    ):
        raise ValueError("backup passphrase is too weak")


def _read_passphrase_file(
    path_value: str | os.PathLike[str],
    *,
    is_posix_platform: Callable[[], bool] | None = None,
    passphrase_validator: Callable[[object], None] | None = None,
) -> str:
    """Read a small immutable-looking secret file without exposing its identity."""

    is_posix_fn = is_posix_platform or _is_posix_platform
    validator = passphrase_validator or _validate_passphrase_strength
    try:
        path = Path(path_value)
        systemd_managed = _is_systemd_credential_path(path)
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or path.is_symlink():
            raise L2BackupError(
                "backup passphrase file must be a regular non-symlink file"
            )
        if is_posix_fn():
            _validate_passphrase_file_mode(
                before.st_mode, systemd_managed=systemd_managed
            )
        if before.st_size > _MAX_PASSPHRASE_FILE_BYTES:
            raise L2BackupError("backup passphrase file is invalid")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(os.fspath(path), flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or opened.st_size != before.st_size
            ):
                raise L2BackupError("backup passphrase file is invalid")
            if is_posix_fn():
                _validate_passphrase_file_mode(
                    opened.st_mode, systemd_managed=systemd_managed
                )
            content = os.read(descriptor, _MAX_PASSPHRASE_FILE_BYTES + 1)
            after = os.fstat(descriptor)
            if (
                len(content) > _MAX_PASSPHRASE_FILE_BYTES
                or len(content) != opened.st_size
                or after.st_size != opened.st_size
                or after.st_mtime_ns != opened.st_mtime_ns
            ):
                raise L2BackupError("backup passphrase file is invalid")
        finally:
            os.close(descriptor)
        passphrase = content.decode("utf-8")
        if passphrase.endswith("\r\n"):
            passphrase = passphrase[:-2]
        elif passphrase.endswith("\n"):
            passphrase = passphrase[:-1]
        if "\n" in passphrase or "\r" in passphrase or "\x00" in passphrase:
            raise L2BackupError("backup passphrase file is invalid")
        try:
            validator(passphrase)
        except Exception:
            raise L2BackupError(
                "backup passphrase file must contain a strong passphrase"
            ) from None
        return passphrase
    except L2BackupError:
        raise
    except (OSError, TypeError, ValueError, UnicodeError):
        raise L2BackupError(
            "backup passphrase file is unavailable or invalid"
        ) from None


def resolve_backup_passphrase(
    *,
    environment_passphrase: str | None,
    passphrase_file: str | os.PathLike[str] | None,
    passphrase_validator: Callable[[object], None] | None = None,
    passphrase_file_reader: Callable[[str | os.PathLike[str]], str] | None = None,
) -> str:
    """Resolve exactly one secret source without putting identity in errors."""

    validator = passphrase_validator or _validate_passphrase_strength
    reader = passphrase_file_reader or _read_passphrase_file
    environment_configured = environment_passphrase is not None
    file_configured = passphrase_file is not None
    if environment_configured == file_configured:
        raise L2BackupError(
            "configure exactly one backup passphrase source (environment or file)"
        )
    if file_configured:
        return reader(passphrase_file)
    try:
        validator(environment_passphrase)
    except Exception:
        raise L2BackupError(
            "backup passphrase environment source must contain a strong passphrase"
        ) from None
    return environment_passphrase


def _default_passphrase_file() -> str | None:
    explicit = os.environ.get(PASSPHRASE_FILE_ENV)
    if explicit is not None:
        return explicit
    credentials_directory = os.environ.get(CREDENTIALS_DIRECTORY_ENV)
    if not credentials_directory:
        return None
    return os.fspath(Path(credentials_directory) / SYSTEMD_PASSPHRASE_CREDENTIAL)
