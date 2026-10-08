#!/usr/bin/env python
"""Build a deterministic source archive directly from immutable Git blobs."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
from typing import Sequence


_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_ALLOWED_MODES = {"100644": 0o644, "100755": 0o755}


class ReproducibleSourceBundleError(ValueError):
    """The source tree cannot be represented by the portable bundle contract."""


def _git(repo: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReproducibleSourceBundleError(
            f"git command failed: {' '.join(args)}"
        ) from exc


def _canonical_json(document: object) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _safe_path(raw: bytes, seen: set[str]) -> str:
    try:
        path = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReproducibleSourceBundleError(
            "tracked paths must be valid UTF-8"
        ) from exc
    pure = PurePosixPath(path)
    if (
        not path
        or "\\" in path
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or str(pure) != path
    ):
        raise ReproducibleSourceBundleError(f"unsafe tracked path: {path!r}")
    portable_key = path.casefold()
    if portable_key in seen:
        raise ReproducibleSourceBundleError(
            f"case-insensitive tracked-path collision: {path!r}"
        )
    seen.add(portable_key)
    return path


def _blob_contents(repo: Path, object_ids: list[str]) -> list[bytes]:
    request = b"".join(object_id.encode("ascii") + b"\n" for object_id in object_ids)
    try:
        completed = subprocess.run(
            ["git", "cat-file", "--batch"],
            cwd=repo,
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReproducibleSourceBundleError(
            "git batch blob read failed"
        ) from exc
    stream = io.BytesIO(completed.stdout)
    contents: list[bytes] = []
    for expected in object_ids:
        header = stream.readline().rstrip(b"\n")
        try:
            actual_raw, kind_raw, size_raw = header.split(b" ", 2)
            actual = actual_raw.decode("ascii")
            kind = kind_raw.decode("ascii")
            size = int(size_raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReproducibleSourceBundleError(
                "git batch blob header is malformed"
            ) from exc
        if actual != expected or kind != "blob" or size < 0:
            raise ReproducibleSourceBundleError(
                "git batch blob identity or type mismatch"
            )
        content = stream.read(size)
        if len(content) != size or stream.read(1) != b"\n":
            raise ReproducibleSourceBundleError(
                "git batch blob payload is truncated"
            )
        contents.append(content)
    if stream.read(1):
        raise ReproducibleSourceBundleError(
            "git batch blob response has trailing data"
        )
    return contents


def _tree_entries(repo: Path, commit: str) -> list[dict[str, object]]:
    output = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit)
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_entry in output.split(b"\0"):
        if not raw_entry:
            continue
        try:
            header, raw_path = raw_entry.split(b"\t", 1)
            mode_raw, kind_raw, object_raw = header.split(b" ", 2)
            mode = mode_raw.decode("ascii")
            kind = kind_raw.decode("ascii")
            object_id = object_raw.decode("ascii")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ReproducibleSourceBundleError(
                "git tree entry is malformed"
            ) from exc
        if kind != "blob" or mode not in _ALLOWED_MODES:
            raise ReproducibleSourceBundleError(
                "source bundle accepts only regular blobs with modes 100644 or 100755"
            )
        path = _safe_path(raw_path, seen)
        entries.append(
            {
                "path": path,
                "mode": mode,
                "tar_mode": _ALLOWED_MODES[mode],
                "git_blob_sha1": object_id,
            }
        )
    entries.sort(key=lambda entry: str(entry["path"]).encode("utf-8"))
    if not entries:
        raise ReproducibleSourceBundleError("source tree contains no files")
    contents = _blob_contents(
        repo,
        [str(entry["git_blob_sha1"]) for entry in entries],
    )
    for entry, content in zip(entries, contents, strict=True):
        entry["sha256"] = hashlib.sha256(content).hexdigest()
        entry["size"] = len(content)
        entry["content"] = content
    return entries


def _archive(entries: list[dict[str, object]], timestamp: int) -> bytes:
    tar_buffer = io.BytesIO()
    with tarfile.open(
        fileobj=tar_buffer,
        mode="w",
        format=tarfile.PAX_FORMAT,
    ) as archive:
        for entry in entries:
            content = entry["content"]
            if not isinstance(content, bytes):
                raise AssertionError("tree entry content must be bytes")
            info = tarfile.TarInfo(str(entry["path"]))
            info.size = len(content)
            info.mode = int(entry["tar_mode"])
            info.mtime = timestamp
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            archive.addfile(info, io.BytesIO(content))

    gzip_buffer = io.BytesIO()
    with gzip.GzipFile(
        filename="",
        mode="wb",
        fileobj=gzip_buffer,
        compresslevel=9,
        mtime=timestamp,
    ) as compressed:
        compressed.write(tar_buffer.getvalue())
    return gzip_buffer.getvalue()


def build_reproducible_source_bundle(
    repo_root: str | Path,
    commit: str,
    output_path: str | Path,
    manifest_path: str | Path,
) -> dict[str, object]:
    """Build and retain a portable source archive plus canonical manifest."""
    repo = Path(repo_root).resolve()
    if _COMMIT_SHA.fullmatch(commit) is None:
        raise ReproducibleSourceBundleError(
            "commit must be a full lowercase commit SHA"
        )
    if not repo.is_dir():
        raise ReproducibleSourceBundleError("repository root is unavailable")
    if _git(repo, "cat-file", "-t", commit).strip() != b"commit":
        raise ReproducibleSourceBundleError("source object is not a commit")
    timestamp_raw = _git(repo, "show", "-s", "--format=%ct", commit).strip()
    try:
        timestamp = int(timestamp_raw)
    except ValueError as exc:
        raise ReproducibleSourceBundleError(
            "commit timestamp is invalid"
        ) from exc
    if timestamp < 0:
        raise ReproducibleSourceBundleError(
            "commit timestamp must be non-negative"
        )

    entries = _tree_entries(repo, commit)
    archive = _archive(entries, timestamp)
    portable_entries = [
        {
            "git_blob_sha1": entry["git_blob_sha1"],
            "mode": entry["mode"],
            "path": entry["path"],
            "sha256": entry["sha256"],
            "size": entry["size"],
        }
        for entry in entries
    ]
    content_root = hashlib.sha256(
        json.dumps(
            portable_entries,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    manifest: dict[str, object] = {
        "schema_version": 1,
        "artifact_kind": "QUANTOS_REPRODUCIBLE_SOURCE_BUNDLE",
        "source_commit": commit,
        "source_timestamp": timestamp,
        "archive_format": "POSIX_PAX_TAR_GZIP",
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "content_root_sha256": content_root,
        "file_count": len(portable_entries),
        "files": portable_entries,
        "authority": {
            "credential_access": False,
            "deployment": False,
            "execution": False,
            "live_trading": False,
            "order_submission": False,
        },
    }

    output = Path(output_path)
    manifest_output = Path(manifest_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(archive)
    manifest_output.write_bytes(_canonical_json(manifest))
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = build_reproducible_source_bundle(
        args.repo_root,
        args.commit,
        args.output,
        args.manifest,
    )
    print(
        "reproducible source bundle: "
        f"{manifest['file_count']} files; sha256={manifest['archive_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
