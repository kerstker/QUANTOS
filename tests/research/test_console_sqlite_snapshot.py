from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

from services.console.api import alpha_family_views, sqlite_snapshot
from services.console.api.sqlite_snapshot import (
    SqliteSnapshotError,
    stable_sqlite_snapshot,
)
from services.research.alpha_family_registry import AlphaFamilyRegistry


def _candidate(index: int) -> dict:
    return {
        "candidate_id": f"snapshot_family_{index}",
        "family_id": "snapshot-test",
        "expression": "(cs_rank (field close))",
        "required_fields": ["close"],
        "status": "SUPPORTED",
    }


def _authority_state(path: Path) -> dict[str, tuple[bytes, int, int]]:
    observed = {}
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        if candidate.exists():
            observed[candidate.name] = (
                candidate.read_bytes(),
                candidate.stat().st_mtime_ns,
                candidate.stat().st_size,
            )
    return observed


def test_console_snapshot_reads_committed_live_wal_without_touching_authority(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "alpha-family.db"
    writer = AlphaFamilyRegistry(str(path), create=True)
    try:
        writer._conn.execute("PRAGMA wal_autocheckpoint=0")
        writer.register_candidate(_candidate(1), recorded_at=1)
        assert Path(f"{path}-wal").stat().st_size > 0
        before = _authority_state(path)
        monkeypatch.setattr(alpha_family_views, "_DB", str(path))

        payload = alpha_family_views._payload()

        assert payload["snapshot_status"] == "REAL"
        assert payload["event_count"] == 1
        assert [row["candidate_id"] for row in payload["families"]] == [
            "snapshot_family_1"
        ]
        assert _authority_state(path) == before
    finally:
        writer.close()


def test_console_snapshot_does_not_create_source_sidecars_after_clean_checkpoint(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "alpha-family.db"
    with AlphaFamilyRegistry(str(path), create=True) as writer:
        writer.register_candidate(_candidate(1), recorded_at=1)
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()
    before = _authority_state(path)
    monkeypatch.setattr(alpha_family_views, "_DB", str(path))

    payload = alpha_family_views._payload()

    assert payload["snapshot_status"] == "REAL"
    assert payload["event_count"] == 1
    assert _authority_state(path) == before
    assert not Path(f"{path}-wal").exists()
    assert not Path(f"{path}-shm").exists()


def test_stable_snapshot_retries_one_wal_change_without_mixing_authorities(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "alpha-family.db"
    writer = AlphaFamilyRegistry(str(path), create=True)
    writer._conn.execute("PRAGMA wal_autocheckpoint=0")
    writer.register_candidate(_candidate(1), recorded_at=1)
    original = sqlite_snapshot._read_or_copy
    changed = False

    def change_once(source, destination):
        nonlocal changed
        result = original(source, destination)
        if destination is not None and source == path and not changed:
            changed = True
            writer.register_candidate(_candidate(2), recorded_at=2)
        return result

    monkeypatch.setattr(sqlite_snapshot, "_read_or_copy", change_once)
    try:
        with stable_sqlite_snapshot(path) as snapshot:
            with AlphaFamilyRegistry(str(snapshot), read_only=True) as reader:
                assert [row["candidate_id"] for row in reader.candidates()] == [
                    "snapshot_family_1",
                    "snapshot_family_2",
                ]
    finally:
        writer.close()


def test_stable_snapshot_retries_one_main_metadata_change(tmp_path, monkeypatch):
    path = tmp_path / "alpha-family.db"
    with AlphaFamilyRegistry(str(path), create=True) as writer:
        writer.register_candidate(_candidate(1), recorded_at=1)
    original = sqlite_snapshot._read_or_copy
    changed = False

    def change_once(source, destination):
        nonlocal changed
        result = original(source, destination)
        if destination is not None and source == path and not changed:
            changed = True
            current = path.stat().st_mtime_ns
            os.utime(path, ns=(current + 1, current + 1))
        return result

    monkeypatch.setattr(sqlite_snapshot, "_read_or_copy", change_once)
    with stable_sqlite_snapshot(path) as snapshot:
        with AlphaFamilyRegistry(str(snapshot), read_only=True) as reader:
            assert reader.event_count() == 1
    assert changed is True


def test_continuously_changing_wal_fails_closed_and_cleans_temp(
    tmp_path,
    monkeypatch,
):
    authority = tmp_path / "authority"
    authority.mkdir()
    path = authority / "alpha-family.db"
    temp_parent = tmp_path / "scratch"
    temp_parent.mkdir()
    writer = AlphaFamilyRegistry(str(path), create=True)
    writer._conn.execute("PRAGMA wal_autocheckpoint=0")
    writer.register_candidate(_candidate(1), recorded_at=1)
    original = sqlite_snapshot._read_or_copy
    next_candidate = 2

    def always_change(source, destination):
        nonlocal next_candidate
        result = original(source, destination)
        if destination is not None and source == path:
            writer.register_candidate(
                _candidate(next_candidate), recorded_at=next_candidate
            )
            next_candidate += 1
        return result

    monkeypatch.setattr(sqlite_snapshot, "_read_or_copy", always_change)
    try:
        with pytest.raises(SqliteSnapshotError, match="did not remain stable"):
            with stable_sqlite_snapshot(path, temp_parent=temp_parent):
                pytest.fail("unstable authority was yielded")
        assert list(temp_parent.iterdir()) == []

        monkeypatch.setattr(alpha_family_views, "_DB", str(path))
        payload = alpha_family_views._payload()
        assert payload["snapshot_status"] == "UNAVAILABLE"
        assert payload["families"] == []
    finally:
        writer.close()


def test_snapshot_temp_tree_is_removed_after_reader_error(tmp_path):
    authority = tmp_path / "authority"
    authority.mkdir()
    path = authority / "alpha-family.db"
    temp_parent = tmp_path / "scratch"
    temp_parent.mkdir()
    with AlphaFamilyRegistry(str(path), create=True):
        pass

    with pytest.raises(RuntimeError, match="consumer failed"):
        with stable_sqlite_snapshot(path, temp_parent=temp_parent):
            raise RuntimeError("consumer failed")

    assert list(temp_parent.iterdir()) == []


def test_snapshot_rejects_scratch_inside_authority_before_creating_files(tmp_path):
    authority = tmp_path / "authority"
    authority.mkdir()
    path = authority / "alpha-family.db"
    with AlphaFamilyRegistry(str(path), create=True):
        pass
    scratch = authority / "scratch"
    scratch.mkdir()
    before = set(authority.iterdir())

    with pytest.raises(SqliteSnapshotError, match="outside the authority"):
        with stable_sqlite_snapshot(path, temp_parent=scratch):
            pytest.fail("authority-local scratch was yielded")

    assert set(authority.iterdir()) == before
    assert list(scratch.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="Windows chmod does not enforce directory write denial")
def test_snapshot_reads_from_non_writable_authority_directory(tmp_path):
    authority = tmp_path / "authority"
    authority.mkdir()
    path = authority / "alpha-family.db"
    with AlphaFamilyRegistry(str(path), create=True) as writer:
        writer.register_candidate(_candidate(1), recorded_at=1)
    before = _authority_state(path)
    authority.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with stable_sqlite_snapshot(path) as snapshot:
            with AlphaFamilyRegistry(str(snapshot), read_only=True) as reader:
                assert reader.event_count() == 1
        assert _authority_state(path) == before
    finally:
        authority.chmod(stat.S_IRWXU)


def test_snapshot_rejects_symlink_authority(tmp_path):
    target = tmp_path / "target.db"
    with AlphaFamilyRegistry(str(target), create=True):
        pass
    link = tmp_path / "authority.db"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    with pytest.raises(SqliteSnapshotError, match="regular non-symlink"):
        with stable_sqlite_snapshot(link):
            pytest.fail("symlink authority was yielded")
