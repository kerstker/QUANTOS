"""Bounded, strict, read-only JSON loader for console evidence artifacts."""

from __future__ import annotations

import os
import hashlib
from pathlib import Path
import stat
from typing import Any

from services.governance.json_integrity import load_json_without_duplicate_keys


VALID = "VALID"
MISSING = "MISSING"
INVALID = "INVALID"
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_JSON_NESTING = 128


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def _is_reparse_point(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _inspect_components(
    target: Path,
    *,
    trusted_root: Path,
) -> tuple[str, tuple[tuple[str, tuple[int, int, int, int, int]], ...] | None]:
    try:
        relative = target.relative_to(trusted_root)
    except ValueError:
        return INVALID, None
    if not relative.parts:
        return INVALID, None

    current = trusted_root
    observed: list[tuple[str, tuple[int, int, int, int, int]]] = []
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return MISSING, None
        except OSError:
            return INVALID, None
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse_point(metadata):
            return INVALID, None
        is_final = index == len(relative.parts) - 1
        if (is_final and not stat.S_ISREG(metadata.st_mode)) or (
            not is_final and not stat.S_ISDIR(metadata.st_mode)
        ):
            return INVALID, None
        observed.append((part, _identity(metadata)))
    return VALID, tuple(observed)


def _nesting_within_limit(content: bytes) -> bool:
    """Bound structural nesting without treating brackets inside strings as syntax."""
    depth = 0
    in_string = False
    escaped = False
    for value in content:
        if in_string:
            if escaped:
                escaped = False
            elif value == 0x5C:  # backslash
                escaped = True
            elif value == 0x22:  # quote
                in_string = False
            continue
        if value == 0x22:
            in_string = True
        elif value in (0x5B, 0x7B):  # [ or {
            depth += 1
            if depth > MAX_JSON_NESTING:
                return False
        elif value in (0x5D, 0x7D):  # ] or }
            depth -= 1
            if depth < 0:
                return False
    return depth == 0 and not in_string and not escaped


def read_json_artifact(
    path: str | os.PathLike[str],
    *,
    trusted_root: str | os.PathLike[str],
    with_digest: bool = False,
) -> tuple[str, Any | None]:
    """Read one stable regular file, optionally binding its exact validated bytes.

    with_digest wraps the decoded value as document/input_sha256; default callers
    retain their existing return shape. Failed reads never return a digest.
    """
    root = Path(trusted_root).resolve(strict=False)
    target = Path(os.path.abspath(os.fspath(path)))
    component_status, before_components = _inspect_components(
        target, trusted_root=root
    )
    if component_status != VALID or before_components is None:
        return component_status, None
    try:
        before = target.lstat()
    except (FileNotFoundError, OSError):
        # The component walk already observed this path. Disappearance or loss
        # of access is an unstable/invalid read, not an honest absence.
        return INVALID, None
    if before.st_size > MAX_ARTIFACT_BYTES:
        return INVALID, None

    flags = (
        os.O_RDONLY
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is not None:
        flags |= no_follow
    try:
        fd = os.open(target, flags)
    except FileNotFoundError:
        return INVALID, None
    except OSError:
        return INVALID, None

    close_failed = False
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_size > MAX_ARTIFACT_BYTES
            or _identity(opened) != _identity(before)
        ):
            return INVALID, None
        remaining = opened.st_size + 1
        chunks: list[bytes] = []
        while remaining > 0:
            chunk = os.read(fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) != opened.st_size:
            return INVALID, None
        after_fd = os.fstat(fd)
    except OSError:
        return INVALID, None
    finally:
        try:
            os.close(fd)
        except OSError:
            close_failed = True

    if close_failed:
        return INVALID, None

    component_status, after_components = _inspect_components(
        target, trusted_root=root
    )
    if component_status != VALID or after_components != before_components:
        return INVALID, None
    if _identity(after_fd) != _identity(opened):
        return INVALID, None
    if not _nesting_within_limit(content):
        return INVALID, None
    try:
        document = load_json_without_duplicate_keys(content)
        if with_digest:
            return VALID, {'document': document, 'input_sha256': hashlib.sha256(content).hexdigest()}
        return VALID, document
    except (RecursionError, TypeError, ValueError, UnicodeError):
        return INVALID, None


__all__ = [
    "INVALID",
    "MAX_ARTIFACT_BYTES",
    "MAX_JSON_NESTING",
    "MISSING",
    "VALID",
    "read_json_artifact",
]
