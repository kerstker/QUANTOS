from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile

import pytest

from tools.build_reproducible_source_bundle import (
    ReproducibleSourceBundleError,
    build_reproducible_source_bundle,
)


def _git(
    repo: Path,
    *args: str,
    input_bytes: bytes | None = None,
    env: dict[str, str] | None = None,
) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input_bytes,
        env={**os.environ, **(env or {})},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.decode("utf-8").strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "QUANTOS Test")
    _git(repo, "config", "user.email", "quantos-test@example.invalid")
    (repo / "alpha.txt").write_bytes(b"alpha\n")
    scripts = repo / "scripts"
    scripts.mkdir()
    (scripts / "run.py").write_bytes(b"#!/usr/bin/env python\nprint('ok')\n")
    _git(repo, "add", ".")
    _git(repo, "update-index", "--chmod=+x", "scripts/run.py")
    _git(
        repo,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "--quiet",
        "-m",
        "fixture",
        env={
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        },
    )
    return repo, _git(repo, "rev-parse", "HEAD")


def _members(archive: bytes) -> dict[str, tuple[tarfile.TarInfo, bytes]]:
    with gzip.GzipFile(fileobj=io.BytesIO(archive), mode="rb") as compressed:
        with tarfile.open(fileobj=compressed, mode="r:") as tar:
            return {
                member.name: (
                    member,
                    tar.extractfile(member).read() if member.isfile() else b"",
                )
                for member in tar.getmembers()
            }


def test_source_bundle_is_byte_identical_and_ignores_worktree_mutation(tmp_path):
    repo, commit = _repo(tmp_path)
    first = tmp_path / "first.tar.gz"
    first_manifest = tmp_path / "first.json"
    second = tmp_path / "second.tar.gz"
    second_manifest = tmp_path / "second.json"

    build_reproducible_source_bundle(repo, commit, first, first_manifest)
    (repo / "alpha.txt").write_text("uncommitted mutation\n", encoding="utf-8")
    build_reproducible_source_bundle(repo, commit, second, second_manifest)

    assert first.read_bytes() == second.read_bytes()
    assert first_manifest.read_bytes() == second_manifest.read_bytes()
    manifest = json.loads(first_manifest.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["source_commit"] == commit
    assert manifest["file_count"] == 2
    assert len(manifest["archive_sha256"]) == 64
    assert len(manifest["content_root_sha256"]) == 64
    assert manifest["authority"] == {
        "credential_access": False,
        "deployment": False,
        "execution": False,
        "live_trading": False,
        "order_submission": False,
    }

    members = _members(first.read_bytes())
    assert list(members) == ["alpha.txt", "scripts/run.py"]
    assert members["alpha.txt"][1] == b"alpha\n"
    assert members["scripts/run.py"][1].endswith(b"print('ok')\n")
    assert members["alpha.txt"][0].mode == 0o644
    assert members["scripts/run.py"][0].mode == 0o755
    assert {item[0].mtime for item in members.values()} == {946684800}
    assert all(item[0].uid == item[0].gid == 0 for item in members.values())


def test_source_bundle_rejects_noncanonical_commit_and_non_blob_entry(tmp_path):
    repo, commit = _repo(tmp_path)
    with pytest.raises(
        ReproducibleSourceBundleError,
        match="full lowercase commit SHA",
    ):
        build_reproducible_source_bundle(
            repo,
            commit[:12],
            tmp_path / "short.tar.gz",
            tmp_path / "short.json",
        )

    link_target_blob = _git(
        repo,
        "hash-object",
        "-w",
        "--stdin",
        input_bytes=b"alpha.txt",
    )
    _git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"120000,{link_target_blob},unsafe-link",
    )
    _git(repo, "commit", "--quiet", "-m", "symlink")
    link_commit = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(
        ReproducibleSourceBundleError,
        match="regular blobs",
    ):
        build_reproducible_source_bundle(
            repo,
            link_commit,
            tmp_path / "link.tar.gz",
            tmp_path / "link.json",
        )
