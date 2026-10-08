import pytest


def snapshot_fixture(tmp_path, monkeypatch):
    from tools import gold_bar_definition as module
    archive, source = tmp_path/'archive', tmp_path/'source'
    archive.mkdir()
    source.mkdir()
    for name in ('input.json', 'report.json', 'manifest.json', 'independent-request.json', 'trials.db'):
        (archive/name).write_bytes(b'original')
    for folder in ('services', 'quantos', 'tools'):
        (source/folder).mkdir()
        for index in range(30):
            nested = source/folder/'nested'
            nested.mkdir(exist_ok=True)
            (nested/(str(index)+'.py')).write_bytes(b'original source\n')
    (source/'requirements-ci-linux-aarch64.lock').write_bytes(b'lock')
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *a, **kw:'a'*40+'\n')
    monkeypatch.setattr(module, 'define', lambda *a, **kw:{'candidate':'channel'})
    return module, archive, source


def test_full_source_byte_snapshot_avoids_repeated_parent_resolution(tmp_path, monkeypatch):
    import os
    module, archive, source = snapshot_fixture(tmp_path, monkeypatch)
    prepared = module.PreparedBarDefinition(archive, candidate='channel', source_directory=source)
    calls = []
    original = os.lstat

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(os, 'lstat', counted)
    assert prepared.definition(archive, candidate='channel', source_directory=source)['candidate'] == 'channel'
    # Full current bytes are required, but root ancestors need not be re-walked
    # separately for every leaf. This bound is independent of host load.
    assert len(calls) <= 600


@pytest.mark.parametrize('change', ['add', 'remove', 'same_size_changed_bytes', 'symlink'])
def test_prepared_source_snapshot_rechecks_membership_and_actual_bytes(tmp_path, monkeypatch, change):
    module, archive, source = snapshot_fixture(tmp_path, monkeypatch)
    prepared = module.PreparedBarDefinition(archive, candidate='channel', source_directory=source)
    target = source/'tools/nested/0.py'
    if change == 'add':
        (source/'tools/nested/new.py').write_bytes(b'original source\n')
    elif change == 'remove':
        target.unlink()
    elif change == 'same_size_changed_bytes':
        target.write_bytes(b'changed! source\n')
    else:
        target.unlink()
        target.symlink_to(archive/'input.json')
    with pytest.raises(ValueError):
        prepared.definition(archive, candidate='channel', source_directory=source)


@pytest.mark.parametrize('change', ['write', 'replace', 'parent_replace'])
def test_source_changes_during_read_are_rejected_without_descriptor_leaks(tmp_path, monkeypatch, change):
    import os
    from contextlib import contextmanager
    from pathlib import Path

    module, archive, source = snapshot_fixture(tmp_path, monkeypatch)
    target = source/'tools/nested/0.py'
    fdopen = os.fdopen

    class ChangingReader:
        def __init__(self, stream):
            self.stream = stream

        def read(self, limit):
            raw = self.stream.read(limit)
            if change == 'write':
                target.write_bytes(b'changed! source\n')
            elif change == 'replace':
                target.unlink()
                target.write_bytes(raw)
            else:
                target.parent.rename(target.parent.with_name('old'))
                target.parent.mkdir()
                target.write_bytes(raw)
            return raw

    @contextmanager
    def changing(fd, *args, **kwargs):
        with fdopen(fd, *args, **kwargs) as stream:
            yield ChangingReader(stream) if Path(os.readlink('/proc/self/fd/'+str(fd))) == target else stream

    before = set(os.listdir('/proc/self/fd'))
    monkeypatch.setattr(os, 'fdopen', changing)
    with pytest.raises(ValueError, match='GOLD_BAR_PREPARED_CHANGED'):
        module._python_originals(source)
    assert set(os.listdir('/proc/self/fd')) == before


def test_source_fifo_is_rejected_without_blocking_or_descriptor_leaks(tmp_path, monkeypatch):
    import os
    module, _, source = snapshot_fixture(tmp_path, monkeypatch)
    os.mkfifo(source/'tools/nested/fifo.py')
    before = set(os.listdir('/proc/self/fd'))
    with pytest.raises(ValueError, match='GOLD_BAR_PREPARED_PATH'):
        module._python_originals(source)
    assert set(os.listdir('/proc/self/fd')) == before


def test_source_snapshot_preserves_sorted_paths_and_every_current_byte(tmp_path, monkeypatch):
    module, _, source = snapshot_fixture(tmp_path, monkeypatch)
    (source/'tools/nested/ignored.txt').write_bytes(b'not Python')
    expected = sorted((path, path.read_bytes()) for folder in ('services', 'quantos', 'tools')
                      for path in (source/folder).rglob('*.py'))
    assert module._python_originals(source) == expected


@pytest.mark.parametrize('change', ['source_alias', 'folder_alias', 'oversized'])
def test_source_snapshot_rejects_aliases_and_file_budget(tmp_path, monkeypatch, change):
    import os
    module, _, source = snapshot_fixture(tmp_path, monkeypatch)
    if change == 'source_alias':
        alias = source.with_name('alias')
        alias.symlink_to(source, target_is_directory=True)
        source = alias
    elif change == 'folder_alias':
        folder = source/'tools'
        folder.rename(source/'old-tools')
        folder.symlink_to(source/'old-tools', target_is_directory=True)
    else:
        monkeypatch.setattr(module, 'MAX_BYTES', 1)
    before = set(os.listdir('/proc/self/fd'))
    with pytest.raises(ValueError):
        module._python_originals(source)
    assert set(os.listdir('/proc/self/fd')) == before


def test_prepared_definition_checks_original_bytes_and_returns_detached_value(tmp_path, monkeypatch):
    from tools import gold_bar_definition as module
    archive = tmp_path/'archive'
    source = tmp_path/'source'
    archive.mkdir()
    source.mkdir()
    for name in ('input.json', 'report.json', 'manifest.json', 'independent-request.json', 'trials.db'):
        (archive/name).write_bytes(b'original')
    for folder in ('services', 'quantos', 'tools'):
        (source/folder).mkdir()
        (source/folder/'test.py').write_text('original')
    (source/'requirements-ci-linux-aarch64.lock').write_text('lock')
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *a, **kw: 'a'*40+'\n')
    calls = []
    def define(*args, **kwargs):
        calls.append(1)
        return {'candidate':'channel', 'nested':{'value':1}}
    monkeypatch.setattr(module, 'define', define)
    prepared = module.PreparedBarDefinition(archive, candidate='channel', source_directory=source)
    value = prepared.definition(archive, candidate='channel', source_directory=source)
    value['nested']['value'] = 2
    assert prepared.definition(archive, candidate='channel', source_directory=source)['nested']['value'] == 1
    assert calls == [1]
    with pytest.raises(ValueError):
        prepared.definition(archive, candidate='trend', source_directory=source)
    path = source/'tools'/'test.py'
    path.write_text('modified')
    with pytest.raises(ValueError):
        prepared.definition(archive, candidate='channel', source_directory=source)
    path.write_text('original')
    (archive/'trials.db-wal').write_bytes(b'pending')
    with pytest.raises(ValueError):
        prepared.definition(archive, candidate='channel', source_directory=source)
