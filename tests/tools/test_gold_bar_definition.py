import json
from pathlib import Path

import pytest

from tests.tools.test_gold_bar_cost_replay import inputs
from tools import gold_bar_experiment as experiment
from tools.gold_bar_review import review_request
from tools.perpetual_replay_research import source_identity


def archive(tmp_path):
    args = inputs()
    args['release_identity'] = source_identity()
    root = tmp_path/'archive'
    experiment.archive(args, root)
    review_request(root)
    return root


def test_definition_binds_one_candidate_without_promotion_or_approval(tmp_path):
    from tools.gold_bar_definition import define
    root = archive(tmp_path)
    before = {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    result = define(root, candidate='reversion', source_directory=Path(__file__).resolve().parents[2])
    assert result['candidate'] == 'reversion'
    assert result['rules']['max_holding_bars'] == 16
    assert result['sides'] == ['BUY', 'SELL']
    assert result['risk']['max_loss_fraction'] == '0.05'
    assert result['trial_id'] == json.loads((root/'manifest.json').read_text())['trial_ids'][2]
    assert result['economic_eligible'] is result['submission_enabled'] is False
    assert result['execution_adapter_status'] == 'BAR_DEMO_ADAPTER_NOT_IMPLEMENTED'
    assert before == {p.name:p.read_bytes() for p in root.iterdir() if p.is_file()}
    with pytest.raises(ValueError):
        define(root, candidate='net_portfolio', source_directory=Path(__file__).resolve().parents[2])
    (root/'report.json').write_text('{}')
    with pytest.raises(ValueError):
        define(root, candidate='reversion', source_directory=Path(__file__).resolve().parents[2])


def test_wrong_archived_source_fingerprint_is_rejected():
    from tools.gold_bar_definition import verify_source
    expected = dict(source_identity(), python_source_sha256='0'*64)
    with pytest.raises(ValueError, match='SOURCE_IDENTITY'):
        verify_source(Path(__file__).resolve().parents[2], expected)


def test_cold_source_identity_verifier_does_not_load_execution_or_risk_engines():
    """A fresh identity process needs filesystem evidence, never engine startup."""
    import hashlib
    import platform
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in sorted(p for folder in ('services', 'quantos', 'tools')
                       for p in (root/folder).rglob('*.py')):
        digest.update(path.relative_to(root).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    expected = dict(commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root,
                    text=True).strip(), python_source_sha256=digest.hexdigest(),
                    dependency_lock_sha256=hashlib.sha256(
                        (root/'requirements-ci-linux-aarch64.lock').read_bytes()).hexdigest(),
                    python_version=platform.python_version(), architecture=platform.machine())
    code = '''import hashlib,json,sys
sys.path.insert(0,sys.argv[1])
class SourceOnly:
    def find_spec(self,fullname,path=None,target=None):
        if fullname in ('services.execution','services.risk') or fullname.startswith(
                ('services.execution.','services.risk.')):
            raise AssertionError('SOURCE_IDENTITY_LOADED_ENGINE: '+fullname)
sys.meta_path.insert(0,SourceOnly())
from tools.gold_bar_definition import ROOT,SEMANTICS,verify_source
expected=json.loads(sys.argv[2])
observed=verify_source(ROOT,expected)
assert observed == {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SEMANTICS}
for field in expected:
    changed=dict(expected); changed[field]=expected[field]+'-wrong'
    try:
        verify_source(ROOT,changed)
    except ValueError as exc:
        assert str(exc) == 'GOLD_BAR_DEFINITION_SOURCE_IDENTITY'
    else:
        raise AssertionError('SOURCE_IDENTITY_ACCEPTED_CHANGED_FIELD: '+field)
print(json.dumps(observed,sort_keys=True))
'''
    child = subprocess.run([sys.executable, '-B', '-I', '-c', code, str(root), json.dumps(expected)],
                           capture_output=True, text=True, timeout=30)
    assert child.returncode == 0, child.stderr
    assert len(json.loads(child.stdout)) == 3


def source_fixture(tmp_path, monkeypatch):
    import hashlib
    import platform
    from tools import gold_bar_definition as module

    source = tmp_path / 'source'
    source.mkdir()
    for folder in ('services', 'quantos', 'tools'):
        nested = source / folder / 'nested'
        nested.mkdir(parents=True)
        for index in range(30):
            (nested / f'{index}.py').write_bytes(b'original source\n')
    for name in module.SEMANTICS:
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((module.ROOT / name).read_bytes())
    lock = source / 'requirements-ci-linux-aarch64.lock'
    lock.write_bytes(b'lock')
    digest = hashlib.sha256()
    paths = sorted(path for folder in ('services', 'quantos', 'tools')
                   for path in (source / folder).rglob('*.py'))
    for path in paths:
        digest.update(path.relative_to(source).as_posix().encode() + b'\0'
                      + path.read_bytes() + b'\0')
    expected = dict(commit='a' * 40, python_source_sha256=digest.hexdigest(),
                    dependency_lock_sha256=hashlib.sha256(lock.read_bytes()).hexdigest(),
                    python_version=platform.python_version(), architecture=platform.machine())
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *a, **kw: 'a' * 40 + '\n')
    return module, source, expected


def test_source_verification_reads_every_byte_without_rewalking_each_leaf_ancestor(tmp_path, monkeypatch):
    import hashlib
    import os

    module, source, expected = source_fixture(tmp_path, monkeypatch)
    original = os.lstat
    calls = []

    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(os, 'lstat', counted)
    result = module.verify_source(source, expected)
    assert result == {name: hashlib.sha256((module.ROOT / name).read_bytes()).hexdigest()
                      for name in module.SEMANTICS}
    # All 93 current Python files still contribute to the independent reference
    # digest. This filesystem-operation bound does not depend on machine load.
    assert len(calls) <= 600


def test_source_order_preserves_identity_without_repeated_path_rich_comparisons(tmp_path, monkeypatch):
    import hashlib

    module, source, expected = source_fixture(tmp_path, monkeypatch)
    # Component ordering differs from a flat POSIX string order at a.py vs a/b.py.
    for name in ('a.py', 'a/b.py', 'A.py', 'a-b.py', 'a b.py', 'é.py', '🙂.py'):
        target = source/'tools'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(name.encode())
    paths = sorted(path for folder in ('services', 'quantos', 'tools')
                   for path in (source/folder).rglob('*.py'))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(source).as_posix().encode()+b'\0'+path.read_bytes()+b'\0')
    expected['python_source_sha256'] = digest.hexdigest()
    comparisons = []
    path_type = type(source)
    original_eq, original_lt = path_type.__eq__, path_type.__lt__

    def counted_eq(left, right):
        comparisons.append(True)
        return original_eq(left, right)

    def counted_lt(left, right):
        comparisons.append(True)
        return original_lt(left, right)

    monkeypatch.setattr(path_type, '__eq__', counted_eq)
    monkeypatch.setattr(path_type, '__lt__', counted_lt)
    result = module.verify_source(source, expected)
    assert result == {name:hashlib.sha256((module.ROOT/name).read_bytes()).hexdigest()
                      for name in module.SEMANTICS}
    # Full source bytes and component order still match the independent digest.
    # Path rich-comparison overhead must stay linear; no machine-time threshold.
    assert len(comparisons) <= 4*len(paths)


def test_source_digest_does_not_search_each_leaf_ancestor_for_its_relative_name(tmp_path, monkeypatch):
    module, source, expected = source_fixture(tmp_path, monkeypatch)
    # source_fixture computes the complete digest using independent Path naming.
    # Count ancestor searches rather than a machine-dependent elapsed time.
    path_type = type(source)
    original = path_type.parents.fget
    calls = []

    def counted(path):
        calls.append(True)
        return original(path)

    monkeypatch.setattr(path_type, 'parents', property(counted))
    module.verify_source(source, expected)
    assert len(calls) <= 12


@pytest.mark.parametrize('change', ['add', 'remove', 'same_size_changed_bytes'])
def test_source_verification_rechecks_current_membership_and_bytes(tmp_path, monkeypatch, change):
    module, source, expected = source_fixture(tmp_path, monkeypatch)
    module.verify_source(source, expected)
    target = source / 'tools/nested/0.py'
    if change == 'add':
        (source / 'tools/nested/new.py').write_bytes(b'original source\n')
    elif change == 'remove':
        target.unlink()
    else:
        target.write_bytes(b'changed! source\n')
    with pytest.raises(ValueError, match='SOURCE_IDENTITY'):
        module.verify_source(source, expected)


def test_source_verification_rejects_same_bytes_inode_replacement_during_read(tmp_path, monkeypatch):
    import io
    import os

    module, source, expected = source_fixture(tmp_path, monkeypatch)
    target = source / 'tools/nested/0.py'
    original_open, original_fdopen = io.open, os.fdopen
    changes = []

    class ChangingReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def read(self, *args, **kwargs):
            raw = self.stream.read(*args, **kwargs)
            replacement = target.with_suffix('.replacement')
            replacement.write_bytes(raw)
            replacement.replace(target)
            changes.append(True)
            return raw

    def changing_open(file, *args, **kwargs):
        stream = original_open(file, *args, **kwargs)
        return ChangingReader(stream) if isinstance(file, (str, Path)) and Path(file) == target else stream

    def changing_fdopen(fd, *args, **kwargs):
        stream = original_fdopen(fd, *args, **kwargs)
        return ChangingReader(stream) if Path(os.readlink(f'/proc/self/fd/{fd}')) == target else stream

    monkeypatch.setattr(io, 'open', changing_open)
    monkeypatch.setattr(os, 'fdopen', changing_fdopen)
    before = set(os.listdir('/proc/self/fd'))
    try:
        with pytest.raises(ValueError, match='SOURCE_CHANGED'):
            module.verify_source(source, expected)
    finally:
        assert changes == [True]
        assert set(os.listdir('/proc/self/fd')) == before


@pytest.mark.parametrize('change', ['source_alias', 'folder_alias', 'oversized', 'fifo'])
def test_source_verification_rejects_unsafe_paths_and_file_budget_without_fd_leaks(tmp_path, monkeypatch, change):
    import os

    module, source, expected = source_fixture(tmp_path, monkeypatch)
    if change == 'source_alias':
        alias = source.with_name('alias')
        alias.symlink_to(source, target_is_directory=True)
        source = alias
    elif change == 'folder_alias':
        folder = source / 'tools'
        folder.rename(source / 'old-tools')
        folder.symlink_to(source / 'old-tools', target_is_directory=True)
    elif change == 'oversized':
        monkeypatch.setattr(module, 'MAX_BYTES', 1)
    else:
        os.mkfifo(source / 'tools/nested/fifo.py')
    before = set(os.listdir('/proc/self/fd'))
    with pytest.raises(ValueError, match='GOLD_BAR_DEFINITION_SOURCE_'):
        module.verify_source(source, expected)
    assert set(os.listdir('/proc/self/fd')) == before


@pytest.mark.parametrize('key', ['commit', 'dependency_lock_sha256', 'python_version', 'architecture'])
def test_source_verification_preserves_all_identity_fields(tmp_path, monkeypatch, key):
    module, source, expected = source_fixture(tmp_path, monkeypatch)
    expected[key] = 'different'
    with pytest.raises(ValueError, match='SOURCE_IDENTITY'):
        module.verify_source(source, expected)


def test_source_verification_rejects_changed_semantics_even_with_matching_fingerprint(tmp_path, monkeypatch):
    import hashlib

    module, source, expected = source_fixture(tmp_path, monkeypatch)
    (source / module.SEMANTICS[0]).write_bytes(b'changed semantics\n')
    digest = hashlib.sha256()
    paths = sorted(path for folder in ('services', 'quantos', 'tools')
                   for path in (source / folder).rglob('*.py'))
    for path in paths:
        digest.update(path.relative_to(source).as_posix().encode() + b'\0'
                      + path.read_bytes() + b'\0')
    expected['python_source_sha256'] = digest.hexdigest()
    with pytest.raises(ValueError, match='SEMANTICS_CHANGED'):
        module.verify_source(source, expected)
