"""Native isolated Gold evidence: no accounts, services, or deployment keys.

Git bundles, SQLite schemas, enrollment and custody are real. Only fixed OS
namespace roots and synthetic account transport are isolated by the fixtures.
"""
from contextlib import ExitStack
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tools import demo_namespace_checkpoint as original
from tests.tools.test_gold_pilot_reobservation import stale_attempt, build_request, execute, original_origin_run


@pytest.fixture(autouse=True)
def private_files():
    previous = os.umask(0o077)
    try:
        yield
    finally:
        os.umask(previous)


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_source_tree_snapshot_preserves_all_blobs_without_repeated_ancestor_search(tmp_path, monkeypatch):
    from tools import demo_gold_namespace_checkpoint as module
    root = tmp_path/'source'
    root.mkdir()
    tree = []
    for index in range(100):
        name = f'nested/深层/leaf-{index}.py'
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        body = f'original-{index}\n'.encode()
        path.write_bytes(body)
        tree.append(dict(relative=name, mode='100644', bytes=len(body),
            blob_sha1=hashlib.sha1(b'blob '+str(len(body)).encode()+b'\0'+body,
                                 usedforsecurity=False).hexdigest()))
    before = hashes(root)
    path_type = type(root)
    original = path_type.parents.fget
    calls = []

    def counted(path):
        calls.append(True)
        return original(path)

    with monkeypatch.context() as local:
        local.setattr(path_type, 'parents', property(counted))
        rows = module._tree_snapshot(root, tree)
    assert rows == sorted((dict(row, sha256=before[row['relative']]) for row in tree),
                          key=lambda row: row['relative'])
    assert hashes(root) == before
    assert len(calls) <= 12


@pytest.fixture
def source_attachment(tmp_path):
    """A real commit/bundle/clone, including the original semantic source files."""
    from tools import gold_bar_definition as definition
    source = tmp_path / 'source'; source.mkdir()
    names = (*definition.SEMANTICS, 'requirements-ci-linux-aarch64.lock')
    for name in names:
        target = source / name; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((definition.ROOT / name).read_bytes())
    (source / 'quantos').mkdir()
    (source / 'quantos/__init__.py').write_text('')

    def git(*args, cwd=source):
        return subprocess.check_output(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null',
            '-c', 'user.name=Isolated Fixture', '-c', 'user.email=fixture@example.invalid',
            *args], cwd=cwd, text=True, stderr=subprocess.PIPE).strip()

    git('init', '--quiet'); git('add', '.')
    git('commit', '--quiet', '-m', 'Synthetic source identity fixture')
    commit = git('rev-parse', 'HEAD')
    bundle = tmp_path / 'release.bundle'; git('bundle', 'create', str(bundle), 'HEAD')
    clone = tmp_path / 'source-restored'
    git('clone', '--quiet', str(bundle), str(clone), cwd=tmp_path)
    digest = hashlib.sha256()
    for path in sorted(p for folder in ('services', 'quantos', 'tools')
                       for p in (clone / folder).rglob('*.py')):
        digest.update(path.relative_to(clone).as_posix().encode() + b'\0' + path.read_bytes() + b'\0')
    identity = dict(commit=commit, python_source_sha256=digest.hexdigest(),
        dependency_lock_sha256=hashlib.sha256((clone / names[-1]).read_bytes()).hexdigest(),
        python_version=platform.python_version(), architecture=platform.machine())
    definition.verify_source(clone, identity)
    return dict(schema='GOLD_CHECKPOINT_VERIFIED_SOURCE_V1', source_root=str(clone),
        bundle_path=str(bundle), bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
        identity=identity)


@pytest.fixture
def gold_namespace(tmp_path, monkeypatch, source_attachment):
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    from live.execution.order_event_store import SqliteOrderEventStore
    from services.research.demo_user_stream import UserStreamJournal
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from live.execution import demo_gold_pilot as pilot, demo_position_context as position
    from live.execution import demo_market_partition_runtime as partition
    from live.execution.demo_pre_submit import _preparation_lease
    from tools.demo_resident_recovery import _check_path
    from tests.tools.test_gold_strategy_entry import origin_at

    root = tmp_path / 'executor'; root.mkdir(mode=0o700)
    at = 10_000_000; origin = origin_at(at); account = origin['account_ref_sha256']
    commit = source_attachment['identity']['commit']
    factories = [('dispatch', DemoDispatchStore), ('recovery', SqliteOrderRecoveryStore), ('oms', LiveLedger)]
    for name, factory in factories:
        factory(str(root / (name + '.sqlite3')), create=True).close()
    SqliteOrderEventStore(str(root / 'order-events.sqlite3'), create=True).close()
    stream = UserStreamJournal(root / 'user-stream.sqlite3', account_ref_sha256=account)
    stream.record('SESSION_START', at_ms=at, source_sha256='a' * 64); stream.__exit__()
    before = original.create_checkpoint(root, tmp_path / 'before-gold.zip', namespace='executor',
        source_commit=commit, passphrase=None, capture_only=True, read_only_source=True)
    assert before['restore_verified'] and before['state']['reservations'] == 0
    monkeypatch.setattr(position, '_ROOT', root)
    monkeypatch.setattr(position, '_custody', lambda path, **kw: _check_path(path, os.getuid(), **kw))
    for name in ('EXECUTOR_RECORD', 'ISSUER_RECORD', 'ACTIVATION'):
        monkeypatch.setattr(partition, name, tmp_path / ('isolated-' + name))
    request = dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1', source_commit=commit,
        account_ref_sha256=account, duration_seconds=86400, max_loss_fraction='0.05', submission_enabled=False)
    pilot.retain_once(root / 'gold-pilot-request.json', request)
    with ExitStack() as stack:
        stores = {name: factory(str(root / (name + '.sqlite3'))) for name, factory in factories}
        for value in stores.values(): stack.callback(value.close)
        with _preparation_lease(stores['dispatch']):
            config = enrollment.enroll_requested(root=root, source_commit=commit, origin=origin,
                **stores, now_ms=at)
    assert enrollment.completed_configuration(root / 'gold-pilot', request=request) == config
    registry = dict(schema='DEMO_AUTHORITY_REGISTRY_V1', deployment_sha256='d' * 64,
        executor_uid=os.getuid(), issuer_uid=os.getuid() + 1000,
        research_uids=[os.getuid() + 2000], keys=[])
    public = dict(schema='GOLD_CHECKPOINT_PUBLIC_CONTEXT_V1', source_commit=commit,
        registry=registry, account_ref_sha256=account, base_runtime=None, current_runtime=None,
        records={})
    cut = dict(schema='GOLD_CHECKPOINT_CUT_REQUEST_V1', operation_sha256='e' * 64,
        source_commit=commit, registry_sha256=_digest(registry), public_bundle_sha256=_digest(public),
        roles=['executor', 'controller', 'issuer', 'research'], writer_barrier_sha256='f' * 64,
        opening_at_ms=at, submission_enabled=False, economic_authority=False)
    return SimpleNamespace(root=root, account=account, commit=commit, request=request, config=config,
        attachment=source_attachment, public=public, cut=cut, guard_calls=[])


def capture(f, monkeypatch, **changes):
    module = importlib.import_module('tools.demo_gold_namespace_checkpoint')
    monkeypatch.setitem(module.ROLE_ROOTS, 'executor', f.root)
    # Simulate the original dedicated worker's supplementary-group isolation.
    # All actual file UID/GID/mode/inode checks remain native.
    monkeypatch.setattr(module.os, 'getgroups', lambda: [os.getuid()])
    arguments = dict(namespace='executor', source_commit=f.commit, operation_sha256='e' * 64,
        cut_request=f.cut, public_bundle=f.public, source_attachment=f.attachment,
        guard=lambda: f.guard_calls.append('guard'))
    arguments.update(changes)
    return module.capture_inventory(**arguments)


@pytest.mark.parametrize('damage', ['minimal', 'missing-current', 'changed-source', 'changed-plan'])
def test_inventory_rejects_unvalidated_runtime_context(gold_namespace, monkeypatch, damage):
    from copy import deepcopy
    f = gold_namespace
    runtime = dict(schema='GOLD_BAR_AUTO_EPISODE_INPUT_V1', source_commit=f.commit,
        trial_manifest_sha256='a' * 64, archive_source_commit=f.commit,
        candidate='channel', seconds=20, episode_id='b' * 64)
    f.public['base_runtime'] = deepcopy(runtime)
    f.public['current_runtime'] = deepcopy(runtime)
    if damage == 'minimal':
        f.public['base_runtime'] = f.public['current_runtime'] = {'archive_source_commit': f.commit}
    elif damage == 'missing-current':
        f.public['current_runtime'] = None
    elif damage == 'changed-source':
        f.public['current_runtime']['source_commit'] = 'f' * 40
    else:
        f.public['current_runtime']['seconds'] = 21
    f.cut['public_bundle_sha256'] = _digest(f.public)
    with pytest.raises(ValueError, match='(CHECKPOINT_RUNTIME|BUDGET_SCOPE_|PILOT_CLOSURE_RUNTIME)'):
        with capture(f, monkeypatch):
            pass


@pytest.fixture
def bar_archive_namespace(gold_namespace, tmp_path, monkeypatch):
    """Two actual commits and native archived BAR definition, before first GET.

    The source-identity provider is isolated to the verified local checkout;
    archive, trial ledger, recomputation, source verifier and definition are real.
    No placeholder runtime or synthetic archive/report/definition is accepted.
    """
    from copy import deepcopy
    from tools import gold_bar_experiment as experiment, gold_bar_definition as definition
    from tools.gold_bar_review import review_request
    from tests.tools.test_gold_bar_cost_replay import inputs
    f = gold_namespace
    source = tmp_path / 'archive-source'
    def git(*args, cwd=source):
        return subprocess.check_output(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null',
            '-c', 'user.name=Isolated Fixture', '-c', 'user.email=fixture@example.invalid', *args],
            cwd=cwd, text=True, stderr=subprocess.PIPE).strip()
    git('clone', '--quiet', f.attachment['bundle_path'], str(source), cwd=tmp_path)
    (source / 'archive-release.txt').write_text('Distinct archived release, identical strategy semantics.\n')
    git('add', 'archive-release.txt'); git('commit', '--quiet', '-m', 'Archived release fixture')
    commit = git('rev-parse', 'HEAD')
    identity = dict(f.attachment['identity'], commit=commit)
    definition.verify_source(source, identity)
    bundle = tmp_path / 'archive-source.bundle'; git('bundle', 'create', str(bundle), 'HEAD')
    attachment = dict(schema='GOLD_CHECKPOINT_VERIFIED_SOURCE_V1', source_root=str(source),
        bundle_path=str(bundle), bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(), identity=identity)
    args = inputs(); args['release_identity'] = identity
    trial = tmp_path / 'native-bar-archive'
    with monkeypatch.context() as local:
        local.setattr(experiment, 'source_identity', lambda: deepcopy(identity))
        experiment.archive(args, trial)
        review_request(trial)
    trial_hash = hashlib.sha256((trial / 'manifest.json').read_bytes()).hexdigest()
    target = f.root / 'strategy-bar-trials' / trial_hash
    target.parent.mkdir(mode=0o700); shutil.move(trial, target)
    installed_source = f.root / 'strategy-bar-sources' / commit
    installed_source.parent.mkdir(mode=0o700)
    git('clone', '--quiet', str(bundle), str(installed_source), cwd=tmp_path)
    # Match the original installer fixture's copy_definition_source: only
    # semantic source inputs and the lockfile are resident, plus Git identity.
    (installed_source / 'archive-release.txt').unlink()
    for path in (installed_source, *installed_source.rglob('*')):
        path.chmod(0o700 if path.is_dir() else 0o600)
    f.attachment = dict(schema='GOLD_CHECKPOINT_SOURCE_SET_V1', current_commit=f.commit,
        attachments=sorted([f.attachment, attachment], key=lambda row: row['identity']['commit']))
    f.runtime = dict(schema='GOLD_BAR_AUTO_EPISODE_INPUT_V1', source_commit=f.commit,
        trial_manifest_sha256=trial_hash, archive_source_commit=commit,
        candidate='channel', seconds=20, episode_id='b' * 64)
    f.public.update(base_runtime=deepcopy(f.runtime), current_runtime=deepcopy(f.runtime))
    f.cut['public_bundle_sha256'] = _digest(f.public)
    from tools.gold_strategy_budget_enrollment import load_definition
    f.definition = load_definition(f.root, f.runtime, source_commit=f.commit, uid=os.getuid())
    f.archive_source = installed_source
    return f


def test_native_two_source_bar_archive_roundtrip_has_explicit_source_dependency(
        bar_archive_namespace, monkeypatch, tmp_path):
    from tools import demo_gold_namespace_checkpoint as module
    f = bar_archive_namespace
    before = hashes(f.root)
    with capture(f, monkeypatch) as inventory:
        assert inventory.semantic_view()['bar_inputs']['definition_sha256'] == f.definition['definition_hash']
        entries = list(inventory.iter_entries())
        assert not any('.git' in Path(row['relative']).parts for row in entries)
        restored = tmp_path / 'restored-bar'
        manifest, receipt = materialize(inventory, restored)
        assert len(manifest['source_dependencies']) == 1
        expected = inventory.semantic_view()
    restored_before = hashes(restored)
    assert module.verify_restored_namespace(restored, inventory=manifest, role_receipt=receipt,
        public_bundle=f.public, source_attachment=f.attachment) == expected
    assert hashes(restored) == restored_before and hashes(f.root) == before
    assert not (restored / 'strategy-bar-sources' / f.runtime['archive_source_commit'] / '.git').exists()


def test_controller_retention_reads_bound_public_pilot_without_cross_uid_access(
        bar_archive_namespace, monkeypatch, tmp_path):
    from tools import demo_gold_namespace_checkpoint as module
    from tools.gold_pilot_retention_store import Retention
    f = bar_archive_namespace
    controller = tmp_path / 'controller'; controller.mkdir(mode=0o700)
    pilot = dict(request=f.request, configuration=f.config,
        intent=json.loads((f.root / 'gold-pilot/enrollment-intent.json').read_bytes()),
        receipt=json.loads((f.root / 'gold-pilot/enrollment.json').read_bytes()), stop=None)
    f.public['records']['pilot'] = pilot
    f.cut['public_bundle_sha256'] = _digest(f.public)
    original_pilot = dict(request=f.request, configuration=f.config)
    with Retention(controller, original=original_pilot, base_runtime=f.runtime,
            uid=os.getuid(), role='controller'):
        pass
    monkeypatch.setitem(module.ROLE_ROOTS, 'controller', controller)
    # Only the dedicated controller UID is virtualized; fd/path metadata and
    # the original public enrollment validator remain real.
    monkeypatch.setattr(module, '_role_uid', lambda registry, role: os.getuid(), raising=False)
    monkeypatch.setattr(module.os, 'getgroups', lambda: [os.getuid()])
    before = hashes(controller)
    with module.capture_inventory(namespace='controller', source_commit=f.commit,
            operation_sha256=f.cut['operation_sha256'], cut_request=f.cut, public_bundle=f.public,
            source_attachment=f.attachment, guard=lambda: None) as inventory:
        assert inventory.semantic_view()['enrollment']['configuration'] == f.config
        assert inventory.semantic_view()['retention']['checkpoint']['frames'] == 0
        restored = tmp_path / 'restored-controller'; manifest, receipt = materialize(inventory, restored)
    assert module.verify_restored_namespace(restored, inventory=manifest, role_receipt=receipt,
        public_bundle=f.public, source_attachment=f.attachment)['retention']['checkpoint']['frames'] == 0
    assert hashes(controller) == before


def test_source_tree_limits_require_actual_opaque_bundle_verification(bar_archive_namespace, monkeypatch):
    from copy import deepcopy
    from tools import demo_gold_namespace_checkpoint as module
    f = bar_archive_namespace
    with capture(f, monkeypatch) as inventory:
        manifest = inventory.manifest()
        early = module.inventory_limits(manifest, public_bundle=f.public, source_attachment=f.attachment)
        assert early['pending_source_tree_components'] == ['bar_inputs']
        with pytest.raises(ValueError, match='SOURCE_TREE_VERIFICATION'):
            module.inventory_limits(manifest, public_bundle=f.public, source_attachment=f.attachment,
                verified_source_trees={'verified': True})
        with module.verified_source_trees(f.attachment) as trees:
            later = module.inventory_limits(manifest, public_bundle=f.public, source_attachment=f.attachment,
                verified_source_trees=trees)
            assert later['pending_source_tree_components'] == []
            private = trees.source_attachment()
            assert module.source_attachment_identity(private) == module.source_attachment_identity(f.attachment)
            assert all(Path(row['source_root']).is_dir() for row in module.source_attachments(private))
            changed = deepcopy(f.attachment); changed['current_commit'] = next(
                row['identity']['commit'] for row in changed['attachments'] if row['identity']['commit'] != f.commit)
            with pytest.raises(ValueError, match='SOURCE_TREE_VERIFICATION'):
                module.inventory_limits(manifest, public_bundle=f.public, source_attachment=changed,
                    verified_source_trees=trees)
        with pytest.raises(ValueError, match='SOURCE_TREE_VERIFICATION'):
            trees.source_attachment()


def test_source_tree_declared_blob_budget_rejects_before_checkout(source_attachment, monkeypatch):
    from tools import demo_gold_namespace_checkpoint as module
    monkeypatch.setattr(module, 'SOURCE_MAX_EXPANDED_BYTES', 1, raising=False)
    run = module.subprocess.run; called = []
    def observed(command, **kwargs):
        called.append(command)
        return run(command, **kwargs)
    monkeypatch.setattr(module.subprocess, 'run', observed)
    with pytest.raises(ValueError, match='SOURCE_TREE_BUDGET'):
        with module.verified_source_trees(source_attachment):
            pass
    assert not any('checkout' in command for command in called)


def test_source_checkout_attributes_cannot_relabel_same_length_blob(source_attachment, tmp_path):
    from tools import demo_gold_namespace_checkpoint as module
    source = Path(source_attachment['source_root'])
    command = ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Fixture',
        '-c', 'user.email=fixture@example.invalid', '-C', str(source)]
    (source / '.gitattributes').write_text('encoded.dat working-tree-encoding=UTF-16LE\n')
    (source / 'encoded.dat').write_bytes('é'.encode('utf-16le'))
    subprocess.run(command + ['add', '.gitattributes', 'encoded.dat'], check=True)
    subprocess.run(command + ['commit', '--quiet', '-m', 'Same-length encoded fixture'], check=True)
    commit = subprocess.check_output(command + ['rev-parse', 'HEAD'], text=True).strip()
    blob = subprocess.check_output(command + ['show', 'HEAD:encoded.dat'])
    assert len(blob) == (source / 'encoded.dat').stat().st_size == 2
    assert blob != (source / 'encoded.dat').read_bytes()
    bundle = tmp_path / 'encoded.bundle'
    subprocess.run(command + ['bundle', 'create', str(bundle), 'HEAD'], check=True)
    value = dict(source_attachment, bundle_path=str(bundle),
        bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
        identity=dict(source_attachment['identity'], commit=commit))
    with pytest.raises(ValueError, match='SOURCE_TREE_BLOB'):
        with module.verified_source_trees(value):
            pass


def test_historical_pack_expansion_is_process_bounded(source_attachment, tmp_path, monkeypatch):
    """A tiny current tree must not exempt a large compressed historical blob."""
    from tools import demo_gold_namespace_checkpoint as module
    source = Path(source_attachment['source_root'])
    command = ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Fixture',
        '-c', 'user.email=fixture@example.invalid', '-C', str(source)]
    historical = source / 'historical.dat'
    with historical.open('wb') as stream:
        for _ in range(128):
            stream.write(b'X' * (1024 * 1024))
    subprocess.run(command + ['add', 'historical.dat'], check=True)
    subprocess.run(command + ['commit', '--quiet', '-m', 'Historical large object'], check=True)
    subprocess.run(command + ['rm', '--quiet', 'historical.dat'], check=True)
    subprocess.run(command + ['commit', '--quiet', '-m', 'Small current tree'], check=True)
    commit = subprocess.check_output(command + ['rev-parse', 'HEAD'], text=True).strip()
    bundle = tmp_path / 'history.bundle'
    subprocess.run(command + ['bundle', 'create', str(bundle), 'HEAD'], check=True)
    assert bundle.stat().st_size < 1024 * 1024
    value = dict(source_attachment, bundle_path=str(bundle),
        bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
        identity=dict(source_attachment['identity'], commit=commit))
    monkeypatch.setattr(module, 'SOURCE_PROCESS_MEMORY_BYTES', 96 * 1024**2, raising=False)
    # Same wrapper, same low address-space limit: a small real pack can be
    # cloned and listed. This rules out failure merely launching Python/Git.
    small = tmp_path / 'small-limit-control'
    module._source_git(['clone', '--quiet', '--no-checkout', source_attachment['bundle_path'], small])
    assert module._git_tree(small, source_attachment['identity']['commit'])
    native_git = module._source_git; calls = []; scratch = []
    native_temp = module.tempfile.TemporaryDirectory
    def observed(arguments, **kwargs):
        calls.append(tuple(map(str, arguments)))
        return native_git(arguments, **kwargs)
    def private_directory(*args, **kwargs):
        temporary = native_temp(*args, **kwargs)
        scratch.append(Path(temporary.name))
        return temporary
    monkeypatch.setattr(module, '_source_git', observed)
    monkeypatch.setattr(module.tempfile, 'TemporaryDirectory', private_directory)
    with pytest.raises(ValueError, match='GOLD_CHECKPOINT_SOURCE_PROCESS'):
        with module.verified_source_trees(value):
            pass
    assert len(calls) == 1 and calls[0][0] == 'clone'
    assert scratch and not any(path.exists() for path in scratch)


def test_source_native_verifier_does_not_inherit_git_environment(source_attachment, monkeypatch):
    from tools import demo_gold_namespace_checkpoint as module
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'not-a-valid-git-key')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'synthetic')
    with module.verified_source_trees(source_attachment) as checked:
        assert checked.tree(source_attachment['identity']['commit'])


def test_source_process_deadline_covers_inherited_pipe_and_process_group(tmp_path, monkeypatch):
    import sys
    import time
    from tools import demo_gold_namespace_checkpoint as module
    marker = tmp_path / 'child.pid'
    code = '''import os,sys,time
child=os.fork()
if child == 0:
    time.sleep(60)
else:
    with open(sys.argv[1],'w') as stream:
        stream.write(str(child)); stream.flush(); os.fsync(stream.fileno())
    os._exit(0)
'''
    monkeypatch.setattr(module, 'SOURCE_PROCESS_WALL_SECONDS', 0.5)
    started = time.monotonic()
    with pytest.raises(ValueError, match='SOURCE_PROCESS_TIMEOUT'):
        module._source_process([sys.executable, '-I', '-c', code, str(marker)])
    assert time.monotonic() - started < 3
    child = int(marker.read_text())
    # A grandchild is reaped by init; it must already be dead rather than an
    # orphan that keeps the inherited read pipe and work alive.
    proc = Path('/proc') / str(child) / 'stat'
    until = time.monotonic() + 1
    while proc.exists() and proc.read_text().split(') ', 1)[1].split()[0] != 'Z':
        assert time.monotonic() < until
        time.sleep(0.01)


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_source_process_output_is_bounded_while_child_is_live(monkeypatch, stream):
    import sys
    from tools import demo_gold_namespace_checkpoint as module
    monkeypatch.setattr(module, 'SOURCE_PROCESS_OUTPUT_BYTES', 1024)
    fd = 1 if stream == 'stdout' else 2
    code = f'import os,time; os.write({fd},b"X"*2048); time.sleep(60)'
    with pytest.raises(ValueError, match='SOURCE_PROCESS_OUTPUT'):
        module._source_process([sys.executable, '-I', '-c', code], output_limit=1024)


@pytest.mark.parametrize('change', ['original-extra', 'changed-extra', 'untracked-extra', 'nested-git'])
def test_extra_checkout_inputs_must_be_actual_exact_bundle_blobs(bar_archive_namespace, monkeypatch, change):
    f = bar_archive_namespace
    source = next(row for row in f.attachment['attachments']
        if row['identity']['commit'] == f.runtime['archive_source_commit'])
    extra = f.archive_source / 'archive-release.txt'
    extra.write_bytes((Path(source['source_root']) / 'archive-release.txt').read_bytes())
    if change == 'changed-extra':
        extra.write_bytes(b'changed')
    elif change == 'untracked-extra':
        (f.archive_source / 'credentials.txt').write_bytes(b'synthetic-unknown-no-secret')
    elif change == 'nested-git':
        (f.archive_source / 'vendor').mkdir(mode=0o700)
        (f.archive_source / 'vendor/.git').mkdir(mode=0o700)
    if change != 'original-extra':
        with pytest.raises(ValueError, match='GOLD_CHECKPOINT_(SOURCE_PAYLOAD|BAR_SOURCE_BOUND_REQUIRED)'):
            with capture(f, monkeypatch):
                pass
    else:
        with capture(f, monkeypatch) as inventory:
            names = {row['relative'] for row in inventory.iter_entries()}
            assert extra.relative_to(f.root).as_posix() in names
            assert all('.git' not in Path(name).parts for name in names)


def test_original_pending_namespace_positive(tmp_path, monkeypatch):
    from tests.tools.test_demo_namespace_checkpoint import executor
    root, account = executor(tmp_path, monkeypatch); before = hashes(root)
    value = original.create_checkpoint(root, tmp_path / 'ordinary.zip', namespace='executor',
        source_commit='c' * 40, passphrase=None, capture_only=True, read_only_source=True)
    assert value['restore_verified'] and value['state']['account_ref_sha256'] == account
    assert value['state']['reservations'] == value['state']['pending_recoveries'] == 1
    assert hashes(root) == before


def test_original_capture_rejects_real_enrollment_without_changing_it(gold_namespace, tmp_path):
    f = gold_namespace; before = hashes(f.root)
    with pytest.raises(ValueError, match='CHECKPOINT_UNDECLARED_(DIRECTORY|FILE)'):
        original.create_checkpoint(f.root, tmp_path / 'old-gold.zip', namespace='executor',
            source_commit=f.commit, passphrase=None, capture_only=True, read_only_source=True)
    assert hashes(f.root) == before and not (tmp_path / 'old-gold.zip').exists()


def test_native_gold_inventory_retains_original_enrollment(gold_namespace, monkeypatch):
    f = gold_namespace; before = hashes(f.root)
    with capture(f, monkeypatch) as inventory:
        entries = list(inventory.iter_entries())
        view = inventory.semantic_view()
        receipt = inventory.recheck()
    assert 'gold-pilot/enrollment-intent.json' in {row['relative'] for row in entries}
    assert view['enrollment']['configuration_sha256'] == _digest(f.config)
    assert view['execution']['reservations'] == 0 and view['mutation_permitted'] is False
    assert receipt['stable'] and receipt['before_sha256'] == receipt['after_sha256']
    assert len(f.guard_calls) >= 2 and hashes(f.root) == before


def test_capture_requires_real_guard_and_source_attachment(gold_namespace, monkeypatch):
    f = gold_namespace
    for change, reason in [({'guard': None}, 'GOLD_CHECKPOINT_GUARD_REQUIRED'),
                           ({'source_attachment': None}, 'GOLD_CHECKPOINT_SOURCE_ATTACHMENT_REQUIRED')]:
        with pytest.raises(ValueError, match=reason):
            with capture(f, monkeypatch, **change):
                pass


def materialize(inventory, destination):
    destination.mkdir(mode=0o700)
    for row in inventory.iter_entries():
        path = destination / row['relative']
        if row['kind'] == 'DIRECTORY':
            path.mkdir(parents=True, mode=0o700, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            shutil.copyfile(inventory.payload_path(row['relative']), path)
            path.chmod(0o600)
    shutil.copytree(inventory.index_directory, destination.parent / 'index')
    return inventory.manifest(), inventory.recheck()


def test_isolated_restore_checks_original_content_without_claiming_live_identity(gold_namespace, monkeypatch, tmp_path):
    f = gold_namespace; before = hashes(f.root)
    with capture(f, monkeypatch) as inventory:
        restored = tmp_path / 'restored'; manifest, receipt = materialize(inventory, restored)
        expected = inventory.semantic_view()
    module = importlib.import_module('tools.demo_gold_namespace_checkpoint')
    restored_before = hashes(restored)
    view = module.verify_restored_namespace(restored, inventory=manifest, role_receipt=receipt,
        public_bundle=f.public, source_attachment=f.attachment)
    assert view == expected and hashes(restored) == restored_before and hashes(f.root) == before
    assert restored.stat().st_ino != f.root.stat().st_ino


@pytest.mark.parametrize('damage', ['worker_uid', 'worker_gid', 'worker_groups',
    'root_uid', 'root_gid', 'root_mode', 'root_shape'])
def test_restore_original_custody_is_bound_to_registry_role(gold_namespace, monkeypatch, tmp_path, damage):
    f = gold_namespace
    with capture(f, monkeypatch) as inventory:
        restored = tmp_path / 'restored'; manifest, receipt = materialize(inventory, restored)
    if damage.startswith('worker_'):
        key = damage.removeprefix('worker_')
        receipt['worker_identity'][key] = [9999] if key == 'groups' else 9999
    elif damage == 'root_shape':
        manifest['source_root'].pop('inode')
    else:
        key = damage.removeprefix('root_')
        manifest['source_root'][key] = 0o40777 if key == 'mode' else 9999
    receipt['inventory_sha256'] = _digest(manifest)
    module = importlib.import_module('tools.demo_gold_namespace_checkpoint')
    with pytest.raises(ValueError, match='GOLD_CHECKPOINT_(ORIGINAL_CUSTODY|ROLE_RECEIPT)|DEMO_RISK_INPUT_FIELDS'):
        module.verify_restored_namespace(restored, inventory=manifest, role_receipt=receipt,
            public_bundle=f.public, source_attachment=f.attachment)


@pytest.mark.parametrize('damage', ['content', 'missing', 'extra', 'hardlink', 'index'])
def test_isolated_restore_refuses_changed_originals(gold_namespace, monkeypatch, tmp_path, damage):
    f = gold_namespace
    with capture(f, monkeypatch) as inventory:
        restored = tmp_path / 'restored'; manifest, receipt = materialize(inventory, restored)
    leaf = restored / 'gold-pilot/configuration.json'
    if damage == 'content':
        value = json.loads(leaf.read_bytes()); value['initial_equity'] = '9999'
        leaf.write_text(json.dumps(value))
    elif damage == 'missing':
        leaf.unlink()
    elif damage == 'extra':
        (restored / 'unknown.json').write_text('{}')
    elif damage == 'hardlink':
        os.link(leaf, tmp_path / 'alias')
    else:
        next((tmp_path / 'index').iterdir()).write_text('{}\n')
    module = importlib.import_module('tools.demo_gold_namespace_checkpoint')
    with pytest.raises(ValueError):
        module.verify_restored_namespace(restored, inventory=manifest, role_receipt=receipt,
            public_bundle=f.public, source_attachment=f.attachment)


def test_same_bytes_new_original_inode_is_not_a_stable_capture(gold_namespace, monkeypatch):
    f = gold_namespace
    with pytest.raises(ValueError, match='GOLD_CHECKPOINT_CUT_CHANGED'):
        with capture(f, monkeypatch) as inventory:
            path = f.root / 'gold-pilot/configuration.json'
            body = path.read_bytes(); path.unlink(); path.write_bytes(body)
            inventory.recheck()


def test_source_attachment_cannot_pair_an_unrelated_bundle(gold_namespace, monkeypatch, tmp_path):
    f = gold_namespace
    unrelated = tmp_path / 'unrelated'; unrelated.mkdir()
    env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1')
    command = ['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', '-c', 'user.name=Fixture',
        '-c', 'user.email=fixture@example.invalid']
    for args in [('init', '--quiet'), ('commit', '--quiet', '--allow-empty', '-m', 'Unrelated')]:
        subprocess.run(command + list(args), cwd=unrelated, env=env, check=True)
    bundle = tmp_path / 'unrelated.bundle'
    subprocess.run(command + ['bundle', 'create', str(bundle), 'HEAD'], cwd=unrelated, env=env, check=True)
    attachment = dict(f.attachment, bundle_path=str(bundle), bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match='GOLD_CHECKPOINT_SOURCE_BUNDLE'):
        with capture(f, monkeypatch, source_attachment=attachment):
            pass


@pytest.mark.parametrize('field', ['worker_identity', 'source_commit', 'stable', 'submission_enabled', 'economic_authority'])
def test_restore_requires_complete_role_receipt(gold_namespace, monkeypatch, tmp_path, field):
    f = gold_namespace
    with capture(f, monkeypatch) as inventory:
        restored = tmp_path / 'restored'; manifest, receipt = materialize(inventory, restored)
    del receipt[field]
    module = importlib.import_module('tools.demo_gold_namespace_checkpoint')
    with pytest.raises(ValueError):
        module.verify_restored_namespace(restored, inventory=manifest, role_receipt=receipt,
            public_bundle=f.public, source_attachment=f.attachment)


def test_role_receipt_has_actual_process_start_ticks(gold_namespace, monkeypatch):
    with capture(gold_namespace, monkeypatch) as inventory:
        worker = inventory.recheck()['worker_identity']
    actual = Path('/proc/self/stat').read_text().rsplit(') ', 1)[1].split()
    assert worker['pid'] == os.getpid() and worker['start_ticks'] == int(actual[19])


def test_ordinary_file_budget_is_not_increased_by_directory_allowance(gold_namespace, monkeypatch):
    from tools import demo_gold_namespace_checkpoint as module
    monkeypatch.setattr(original, 'MAX_FILES', 4)
    with pytest.raises(ValueError, match='GOLD_CHECKPOINT_FILE_BUDGET'):
        with capture(gold_namespace, monkeypatch):
            pass


def test_committed_wal_is_preserved_as_the_same_cut(gold_namespace, monkeypatch, tmp_path):
    from services.research.demo_user_stream import UserStreamJournal
    from tools import demo_gold_namespace_checkpoint as module
    f = gold_namespace
    with UserStreamJournal(f.root / 'user-stream.sqlite3', account_ref_sha256=f.account) as stream:
        stream.db.execute('PRAGMA wal_autocheckpoint=0')
        stream.record('SESSION_START', at_ms=10_000_001, source_sha256='b' * 64)
        assert (f.root / 'user-stream.sqlite3-wal').stat().st_size > 32
        before = hashes(f.root)
        with capture(f, monkeypatch) as inventory:
            entries = {row['relative']: row for row in inventory.iter_entries()}
            assert entries['user-stream.sqlite3']['snapshot_method'] == 'STABLE_FILE_READ'
            assert entries['user-stream.sqlite3-wal']['original_sha256'] == before['user-stream.sqlite3-wal']
            restored = tmp_path / 'restored'; manifest, receipt = materialize(inventory, restored)
            assert inventory.semantic_view()['execution']['stream_records'] == 2
        restored_before = hashes(restored)
        view = module.verify_restored_namespace(restored, inventory=manifest, role_receipt=receipt,
            public_bundle=f.public, source_attachment=f.attachment)
        assert view['execution']['stream_records'] == 2
        assert hashes(restored) == restored_before and hashes(f.root) == before


def test_restored_final_membership_is_rechecked(gold_namespace, monkeypatch, tmp_path):
    from tools import demo_gold_namespace_checkpoint as module
    f = gold_namespace
    with capture(f, monkeypatch) as inventory:
        restored = tmp_path / 'restored'; manifest, receipt = materialize(inventory, restored)
    validate = module._semantic_view
    def changed(*args, **kwargs):
        result = validate(*args, **kwargs)
        (restored / 'unexpected.json').write_text('{}')
        return result
    monkeypatch.setattr(module, '_semantic_view', changed)
    with pytest.raises(ValueError, match='GOLD_CHECKPOINT_RESTORE_EXTRA'):
        module.verify_restored_namespace(restored, inventory=manifest, role_receipt=receipt,
            public_bundle=f.public, source_attachment=f.attachment)


from tests.tools.test_gold_pilot_retention import enrolled as enrolled


def test_readonly_native_pair_validates_existing_mirrors_without_adoption(enrolled, tmp_path):
    from tests.tools.test_gold_pilot_retention import store, complete, bundle, original as pilot_original, NOW
    from tools import gold_pilot_retention as bridge
    from tools.gold_pilot_retention_store import Retention
    controller = tmp_path / 'native-controller'; controller.mkdir(mode=0o700)
    with store(enrolled) as db:
        claim, result = complete(enrolled, db, NOW)
    with store(enrolled, role='controller', root=controller) as db:
        db.adopt(claim); db.put(claim, 'result', result)
    before = (hashes(enrolled.root), hashes(controller))
    with Retention(enrolled.root, original=pilot_original(enrolled), base_runtime=bundle(),
            uid=os.getuid(), role='executor', read_only=True) as source, \
            Retention(controller, original=pilot_original(enrolled), base_runtime=bundle(),
            uid=os.getuid(), role='controller', read_only=True) as mirror:
        view = bridge.verify_retained_pair(source, mirror, history=(bundle(),), closure=None, reobservation=None)
        assert view['executor_checkpoint']['frames'] == view['controller_checkpoint']['frames'] == 1
        assert view['verified_partial'] == [] and view['feedback'] == []
    assert before == (hashes(enrolled.root), hashes(controller))


def test_readonly_native_pair_distinguishes_pending_mirror_from_unknown_partial(enrolled, tmp_path):
    from tests.tools.test_gold_pilot_retention import store, complete, bundle, original as pilot_original, NOW
    from tools import gold_pilot_retention as bridge
    from tools.gold_pilot_retention_store import Retention
    controller = tmp_path / 'native-controller'; controller.mkdir(mode=0o700)
    with store(enrolled) as db:
        complete(enrolled, db, NOW)
    with store(enrolled, role='controller', root=controller):
        pass
    arguments = dict(original=pilot_original(enrolled), base_runtime=bundle(), uid=os.getuid(), read_only=True)
    with Retention(enrolled.root, role='executor', **arguments) as source, Retention(controller, role='controller', **arguments) as mirror:
        view = bridge.verify_retained_pair(source, mirror, history=(bundle(),), closure=None, reobservation=None)
        assert view['verified_partial'] == [{'status': 'CONTROLLER_MIRROR_PENDING', 'first_ordinal': 1, 'last_ordinal': 1}]
    with store(enrolled) as db:
        first = db.reference('result', _digest(__import__('tests.tools.test_gold_pilot_retention', fromlist=['packet']).packet(enrolled, NOW)))
        first_result = json.loads(db.read_claim(first, 'result'))
        db.claim(bundle=bundle(), now_ms=NOW + 1000)
    with store(enrolled, role='controller', root=controller) as mirror:
        mirror.adopt(first); mirror.put(first, 'result', first_result)
    with Retention(enrolled.root, role='executor', **arguments) as source, Retention(controller, role='controller', **arguments) as mirror:
        with pytest.raises(ValueError, match='INCOMPLETE_FRAME'):
            bridge.verify_retained_pair(source, mirror, history=(bundle(),), closure=None, reobservation=None)


def test_restore_source_attachment_is_portable_without_original_paths(gold_namespace, monkeypatch, tmp_path):
    from tools import demo_gold_namespace_checkpoint as module
    f = gold_namespace
    with capture(f, monkeypatch) as inventory:
        restored = tmp_path / 'restored'; manifest, receipt = materialize(inventory, restored)
    relocated = tmp_path / 'transport'; relocated.mkdir()
    bundle = relocated / 'release.bundle'; shutil.copyfile(f.attachment['bundle_path'], bundle)
    source = relocated / 'source'
    subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', 'clone', '--quiet', str(bundle), str(source)], check=True)
    attachment = dict(f.attachment, bundle_path=str(bundle), source_root=str(source))
    shutil.rmtree(f.attachment['source_root']); Path(f.attachment['bundle_path']).unlink()
    view = module.verify_restored_namespace(restored, inventory=manifest, role_receipt=receipt,
        public_bundle=f.public, source_attachment=attachment)
    assert view == manifest['semantic_view']


def test_capture_source_bundle_must_match_original_release(gold_namespace, monkeypatch):
    f = gold_namespace
    public = dict(f.public, source_commit='1' * 40)
    cut = dict(f.cut, source_commit='1' * 40, public_bundle_sha256=_digest(public))
    with pytest.raises(ValueError, match='GOLD_CHECKPOINT_SOURCE_COMMIT'):
        with capture(f, monkeypatch, source_commit='1' * 40, public_bundle=public, cut_request=cut):
            pass


from tests.tools.test_gold_pilot_closure import opening_case as opening_case, idle_empty_case as idle_empty_case


def test_legacy_restore_proves_original_inode_commitment_without_rebinding_production_reader(
        idle_empty_case, monkeypatch, tmp_path):
    from tools import gold_pilot_reobservation as retry, gold_pilot_closure as closure, demo_executor_origin as origin
    from tools.gold_feedback_legacy import _scan
    f = idle_empty_case
    f.base_runtime = dict(schema='GOLD_STRATEGY_WORKER_INPUT_V1', source_commit=f.source_commit,
        trial_manifest_sha256=f.base_runtime['trial_manifest_sha256'], observation_sha256='e' * 64)
    monkeypatch.setattr(origin, 'run', original_origin_run)
    f.original_closure_request = stale_attempt(f, monkeypatch, closure.idle_request(f.base_runtime, f.window))
    (f.root / 'strategy-decisions').mkdir(mode=0o700)
    result = execute(f, build_request(f))
    kwargs = dict(retention=None, base_runtime=f.base_runtime, source_commit=f.source_commit,
        account=f.account, executor_uid=f.executor_uid, expected_runtime=f.base_runtime)
    view = retry.read_reobservation(f.root, **kwargs)
    snapshot = _scan(f.root, 'strategy-decisions', f.executor_uid)
    restored = tmp_path / 'isolated-legacy'; restored.mkdir(mode=0o700)
    for name in ('gold-pilot', 'strategy-decisions'):
        shutil.copytree(f.root / name, restored / name)
    shutil.copyfile(f.root / 'gold-pilot-request.json', restored / 'gold-pilot-request.json')
    for path in restored.rglob('*'):
        path.chmod(0o700 if path.is_dir() else 0o600)
    before = hashes(restored)
    with pytest.raises(ValueError, match='REOBSERVATION_LEGACY_CHANGED'):
        retry.read_reobservation(restored, **kwargs)
    content = retry._read_reobservation_content(restored, **kwargs)
    assert content == view and content['result'] == result
    assert retry.verify_legacy_snapshot(content, snapshot) == view['intent']['subject']['legacy_snapshot_sha256']
    with pytest.raises(ValueError, match='REOBSERVATION_LEGACY_CHANGED'):
        retry.verify_legacy_snapshot(content, _scan(restored, 'strategy-decisions', f.executor_uid))
    assert hashes(restored) == before


def test_part_plan_covers_exact_original_extents_under_each_original_volume_limit(gold_namespace, monkeypatch):
    from tools import demo_gold_namespace_checkpoint as module
    f = gold_namespace
    with capture(f, monkeypatch) as inventory:
        # Lower transport limits only; the source inventory keeps its original caps.
        monkeypatch.setattr(module, 'PART_MAX_MEMBERS', 3, raising=False)
        plan = module.part_plan([inventory], source_attachment=f.attachment)
        assert len(plan['parts']) > 1
        actual = []
        for part in plan['parts']:
            assert len(part['members']) <= 3 and part['payload_bytes'] <= original.MAX_BYTES
            assert part['metadata_bytes'] <= 32 * 1024**2
            actual.extend((part['namespace'], member['relative'], member['extent_ordinal'],
                member['offset'], member['bytes'], member['sha256']) for member in part['members'])
        expected = [('executor', row['relative'], extent['ordinal'], extent['offset'], extent['bytes'], extent['sha256'])
            for row in inventory.iter_entries() if row['kind'] == 'FILE' for extent in row['extents']]
        assert actual == expected
        assert len({member['member'] for part in plan['parts'] for member in part['members']}) == len(actual)
        assert module.part_plan([inventory], source_attachment=f.attachment) == plan
    with pytest.raises(ValueError, match='SESSION_CLOSED'):
        module.part_plan([inventory], source_attachment=f.attachment)


def test_inventory_limits_are_derived_before_any_payload_extraction(gold_namespace, monkeypatch):
    from tools import demo_gold_namespace_checkpoint as module
    from copy import deepcopy
    f = gold_namespace
    with capture(f, monkeypatch) as inventory:
        manifest = inventory.manifest()
        limits = module.inventory_limits(manifest, public_bundle=f.public, source_attachment=f.attachment)
        assert limits['max_files'] == sum(row['max_files'] for row in manifest['component_bounds'].values())
        assert limits['max_bytes'] == sum(row['max_bytes'] for row in manifest['component_bounds'].values())
        changed = deepcopy(manifest); changed['component_bounds']['ordinary']['max_files'] += 1
        with pytest.raises(ValueError, match='GOLD_CHECKPOINT_COMPONENT_BOUND'):
            module.inventory_limits(changed, public_bundle=f.public, source_attachment=f.attachment)
        changed = deepcopy(manifest); changed['files'] = limits['max_files'] + 1
        with pytest.raises(ValueError, match='GOLD_CHECKPOINT_INVENTORY_BUDGET'):
            module.inventory_limits(changed, public_bundle=f.public, source_attachment=f.attachment)


def test_inventory_rejects_ordinary_partial_native_frame(bar_archive_namespace, monkeypatch):
    from tools.gold_pilot_retention_store import Retention
    f = bar_archive_namespace; runtime = f.runtime
    with Retention(f.root, original=dict(request=f.request, configuration=f.config), base_runtime=runtime,
            uid=os.getuid(), role='executor') as store:
        store.claim(bundle=runtime, now_ms=f.config['started_at_ms'])
    before = hashes(f.root)
    with pytest.raises(ValueError, match='GOLD_CHECKPOINT_RETENTION_PARTIAL_UNPROVEN'):
        with capture(f, monkeypatch):
            pass
    assert hashes(f.root) == before
