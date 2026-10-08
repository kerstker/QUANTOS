import hashlib
import json
from types import SimpleNamespace

import pytest


def setup(tmp_path, monkeypatch):
    from tools import gold_bar_worker_inputs as loader
    source_commit = 'a'*40
    manifest = json.dumps({'source': {'commit': source_commit}}).encode()
    result = b'{}'
    mid = hashlib.sha256(manifest).hexdigest()
    oid = hashlib.sha256(result).hexdigest()
    observation = tmp_path/'strategy-bar-observations'/oid
    trial = tmp_path/'strategy-bar-trials'/mid
    source = tmp_path/'strategy-bar-sources'/source_commit
    for p in (observation/'journal', trial, source):
        p.mkdir(parents=True)
    for name in loader.OBSERVATION_FILES:
        (observation/name).write_bytes(result)
    for name in loader.JOURNAL_FILES:
        (observation/'journal'/name).write_bytes(result)
    for name in loader.TRIAL_FILES:
        (trial/name).write_bytes(manifest if name == 'manifest.json' else result)
    for path in tmp_path.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)
    definition = dict(archive_files={'manifest.json': mid}, source={'commit': source_commit}, definition_hash='d'*64)
    calls = []
    monkeypatch.setattr(loader, 'restore_engine', lambda *a, **kw: calls.append((a, kw)) or
        SimpleNamespace(verified_definition=lambda: definition))
    bundle = dict(schema='GOLD_BAR_RETAINED_EPISODE_INPUT_V1', source_commit='c'*40,
        observation_sha256=oid, trial_manifest_sha256=mid, archive_source_commit=source_commit, entry_decision_ms=1000)
    return loader, bundle, observation, trial, source, calls


def test_fixed_paths_and_original_hashes_are_checked_before_restore(tmp_path, monkeypatch):
    loader, bundle, observation, trial, source, calls = setup(tmp_path, monkeypatch)
    engine, checked = loader.load(tmp_path, bundle, source_commit='c'*40, uid=tmp_path.stat().st_uid)
    assert calls == [((observation,), dict(archive=trial, source_directory=source))]
    assert checked['definition_hash'] == engine.verified_definition()['definition_hash']
    (trial/'manifest.json').write_text('{}')
    with pytest.raises(ValueError):
        loader.load(tmp_path, bundle, source_commit='c'*40, uid=tmp_path.stat().st_uid)
    assert len(calls) == 1


@pytest.mark.parametrize('damage', ['path', 'source_symlink', 'wal'])
def test_untrusted_paths_or_mutating_archive_fail_before_restore(tmp_path, monkeypatch, damage):
    loader, bundle, observation, trial, source, calls = setup(tmp_path, monkeypatch)
    if damage == 'path':
        bundle['observation_sha256'] = '../../elsewhere'
    elif damage == 'source_symlink':
        (source/'redirect').symlink_to(tmp_path)
    else:
        (trial/'trials.db-wal').write_bytes(b'pending')
    with pytest.raises(ValueError):
        loader.load(tmp_path, bundle, source_commit='c'*40, uid=tmp_path.stat().st_uid)
    assert calls == []


def test_source_tree_work_is_bounded_by_nodes_and_preserves_every_fact(tmp_path, monkeypatch):
    from pathlib import Path
    from tools import gold_bar_worker_inputs as loader
    root = tmp_path / 'protected-source'
    for index in range(80):
        directory = root / 'services' / str(index % 8)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / (str(index) + '.py')
        path.write_bytes(b'original source\n')
        path.chmod(0o600)
    for path in root.rglob('*'):
        if path.is_dir():
            path.chmod(0o700)
    expected = []
    for path in (root, *root.rglob('*')):
        info = path.lstat()
        expected.append((str(path.relative_to(root)), info.st_ino, info.st_size,
                         info.st_mtime_ns, info.st_ctime_ns))
    allocations = []
    original = Path.with_segments

    def counted(path, *segments):
        allocations.append(True)
        return original(path, *segments)

    monkeypatch.setattr(Path, 'with_segments', counted)
    actual = loader.protected_tree(root, root.stat().st_uid)
    assert actual == sorted(expected)
    # This is a work bound on the complete custody scan, independent of wall
    # clock load. Descendant names must not repeatedly rebuild root ancestors.
    assert len(allocations) <= 4 * len(expected)


@pytest.mark.parametrize('damage', ['writable', 'hardlink', 'fifo', 'symlink'])
def test_source_tree_custody_still_checks_every_nested_original(tmp_path, damage):
    import os
    from tools import gold_bar_worker_inputs as loader
    root = tmp_path / 'protected-source'
    nested = root / 'services' / 'research'
    nested.mkdir(mode=0o700, parents=True)
    for path in (root, root / 'services'):
        path.chmod(0o700)
    target = nested / 'original.py'
    target.write_bytes(b'original\n')
    target.chmod(0o600)
    if damage == 'writable':
        target.chmod(0o620)
    elif damage == 'hardlink':
        os.link(target, nested / 'alias.py')
    else:
        target.unlink()
        if damage == 'fifo':
            os.mkfifo(target, 0o600)
        else:
            target.symlink_to(tmp_path)
    with pytest.raises(ValueError, match='CUSTODY'):
        loader.protected_tree(root, root.stat().st_uid)
