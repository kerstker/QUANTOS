"""Exact archive history is readable only through the executor's fixed boundary."""

import hashlib
import os
from pathlib import Path

import pytest

from tools import demo_executor_archive_read as reader
from tools import demo_retention_transaction as offline
from tools._demo_retention_storage import Storage
from tests.tools.test_demo_retention_transaction import fixture


@pytest.fixture
def archived(tmp_path, monkeypatch):
    root, names = fixture(tmp_path)
    archives = tmp_path / "archives"
    archives.mkdir(mode=0o700)
    token = "a" * 64
    archive = archives / token
    monkeypatch.setattr(reader, "EXECUTOR", root)
    monkeypatch.setattr(reader, "ARCHIVES", archives)
    monkeypatch.setattr(reader, "load_registry_for_executor", lambda: {"executor_uid": os.geteuid()})
    # Model the production root-owned /var/lib ancestors using isolated fixtures.
    original_lstat = Path.lstat
    ancestors = set(root.parents) | set(archives.parents)

    def metadata(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path in ancestors:
            values = list(info)
            values[4] = 0
            values[0] &= ~0o022
            return os.stat_result(values)
        return info

    monkeypatch.setattr(Path, "lstat", metadata)
    store = Storage(offline, schema=reader.SCHEMA)
    store._prepare(root, archive, names[:1], evidence={})
    original = (root / names[0]).read_bytes()
    store.apply(root, archive)
    return root, archive, token, names, original


def test_exact_read_after_archive_and_rollback(archived):
    root, archive, token, names, original = archived
    assert reader.lookup([token], names[0]) == original
    Storage(offline, schema=reader.SCHEMA).rollback(root, archive)
    assert reader.lookup([token], names[0]) == original
    assert reader.lookup([], names[1]) == (root / names[1]).read_bytes()


def test_wrong_identity_fails_before_any_state_read(archived, monkeypatch):
    _, _, token, names, _ = archived
    monkeypatch.setattr(reader, "load_registry_for_executor", lambda: {"executor_uid": os.geteuid() + 1})
    monkeypatch.setattr(offline, "read", lambda *a: pytest.fail("state read before identity rejection"))
    with pytest.raises(ValueError, match="ARCHIVE_EXECUTOR_IDENTITY"):
        reader.lookup([token], names[0])


@pytest.mark.parametrize("ids", [["../outside"], ["/etc"], ["a" * 64, "a" * 64], ["a" * 64] * 33])
def test_arbitrary_or_ambiguous_archive_set_is_rejected(archived, ids):
    _, _, _, names, _ = archived
    with pytest.raises(ValueError, match="ARCHIVE_SET"):
        reader.lookup(ids, names[0])


def test_offline_manifest_cannot_be_misread_as_executor_archive(archived):
    import json

    _, archive, token, names, _ = archived
    path = archive / "manifest.json"
    value = json.loads(path.read_bytes())
    value["schema"] = "ISOLATED_RETENTION_TRANSACTION_V1"
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    (archive / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match="MANIFEST_BINDING"):
        reader.lookup([token], names[0])


def test_corruption_is_not_replaced_with_latest(archived):
    _, archive, token, names, _ = archived
    (archive / "records" / names[0]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="RECORD_CHANGED"):
        reader.lookup([token], names[0])


def test_archive_symlink_is_rejected(archived):
    _, archive, token, names, _ = archived
    alias = archive.parent / ("b" * 64)
    alias.symlink_to(archive, target_is_directory=True)
    with pytest.raises(ValueError, match="ARCHIVE_PATH"):
        reader.lookup(["b" * 64], names[0])


def test_writable_parent_is_rejected_before_reading_records(archived, monkeypatch):
    root, _, token, names, _ = archived
    original_lstat = Path.lstat

    def unsafe(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == root.parent:
            values = list(info)
            values[0] |= 0o020
            return os.stat_result(values)
        return info

    monkeypatch.setattr(Path, "lstat", unsafe)
    monkeypatch.setattr(offline, "read", lambda *args: pytest.fail("record read before parent rejection"))
    with pytest.raises(ValueError, match="ARCHIVE_PARENT_CUSTODY"):
        reader.lookup([token], names[0])


def test_omitted_archive_is_not_silently_discovered(archived):
    _, _, _, names, _ = archived
    with pytest.raises(FileNotFoundError):
        reader.lookup([], names[0])
