"""Read-only source backup must not create SQLite sidecars beside originals."""
import sqlite3
import os
from pathlib import Path

import pytest

from tools import demo_namespace_checkpoint as checkpoint


@pytest.fixture(autouse=True)
def private_files():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def contents(directory):
    return {p.name:p.read_bytes() for p in directory.iterdir()}


@pytest.mark.parametrize('keep_wal', [False, True])
def test_readonly_wal_snapshot_preserves_rows_and_original_sidecars(tmp_path, keep_wal):
    source = tmp_path/'source'; source.mkdir(mode=0o700)
    scratch = tmp_path/'scratch'; scratch.mkdir(mode=0o700)
    database = source/'fixture.sqlite3'
    connection = sqlite3.connect(database)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA wal_autocheckpoint=0')
    connection.execute('CREATE TABLE records(value INTEGER)')
    connection.execute('INSERT INTO records VALUES(42)'); connection.commit()
    if not keep_wal: connection.close()
    before = contents(source)
    for path in source.iterdir(): path.chmod(0o400)
    source.chmod(0o500)
    try:
        snapshot, method, _ = checkpoint.snapshot_readonly_sqlite(database, scratch)
        with sqlite3.connect(snapshot) as verified:
            assert verified.execute('SELECT value FROM records').fetchall() == [(42,)]
        assert method == 'SQLITE_ONLINE_BACKUP'
        assert before == contents(source)
    finally:
        source.chmod(0o700)
        for path in source.iterdir(): path.chmod(0o600)
        connection.close()


def test_source_mutation_during_freeze_is_rejected(tmp_path, monkeypatch):
    source = tmp_path/'source'; source.mkdir()
    scratch = tmp_path/'scratch'; scratch.mkdir()
    database = source/'fixture.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE records(value INTEGER)')
    original = checkpoint.copy_database_member
    def change(path, target, info):
        original(path, target, info)
        with sqlite3.connect(database) as writer:
            writer.execute('INSERT INTO records VALUES(99)')
    monkeypatch.setattr(checkpoint, 'copy_database_member', change)
    with pytest.raises(ValueError, match='CHECKPOINT_DATABASE_CHANGED'):
        checkpoint.snapshot_readonly_sqlite(database, scratch)
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize('kind', ['symlink', 'rollback'])
def test_untrusted_or_unrecoverable_sidecars_are_rejected(tmp_path, kind):
    source = tmp_path/'source'; source.mkdir()
    scratch = tmp_path/'scratch'; scratch.mkdir()
    database = source/'fixture.sqlite3'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE records(value INTEGER)')
    if kind == 'symlink': Path(str(database)+'-wal').symlink_to(database)
    else: Path(str(database)+'-journal').write_bytes(b'not a settled journal')
    with pytest.raises(ValueError): checkpoint.snapshot_readonly_sqlite(database, scratch)
    assert list(scratch.iterdir()) == []


def test_full_executor_backup_from_readonly_tree_keeps_pending_orders(tmp_path, monkeypatch):
    from tests.tools.test_demo_namespace_checkpoint import executor, COMMIT
    root, account = executor(tmp_path, monkeypatch)
    original = {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    modes = {p:p.stat().st_mode & 0o777 for p in [root,*root.rglob('*')]}
    for path in modes: path.chmod(0o500 if path.is_dir() else 0o400)
    try:
        captured = checkpoint.create_checkpoint(root, tmp_path/'capture.zip', namespace='executor',
            source_commit=COMMIT, passphrase=None, capture_only=True, read_only_source=True)
        assert captured['restore_verified'] is True
        assert captured['state']['account_ref_sha256'] == account
        assert captured['state']['pending_recoveries'] == captured['state']['orders'] == 1
        assert original == {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    finally:
        for path, mode in modes.items(): path.chmod(mode)
