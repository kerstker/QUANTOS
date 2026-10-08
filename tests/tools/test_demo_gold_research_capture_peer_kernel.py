"""Two actual research UIDs, captured roster, native archives and root consumer.

Release acceptance/fixed public authority remain synthetic fixture boundaries.
Each producer runs original registration, ledgers and cost replay before capture;
kernel identities, sealed requests, bundle restore and final controls are real.
No service manager or host state is used.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from tests.tools.test_demo_gold_research_inventory import native_research as native_research
from tests.tools.test_registered_research_run import registered_case as original_registered_case
from tests.tools.test_demo_gold_namespace_checkpoint import source_attachment as original_source_attachment
from tests.tools.test_demo_gold_namespace_checkpoint import private_files as private_files
from tests.tools.test_demo_gold_checkpoint_public import fixed_public as fixed_public, public_cut
from tests.tools.test_demo_market_partition_install import book as book, env as env
from tools import demo_gold_research_handoff as handoff
from tools import demo_gold_research_layout as roster
from tools import demo_gold_research_capture_peer as peer
from tools import gold_bar_definition as definition
from tools.gold_pilot_retention_store import canonical

_CHOWN, _FCHOWN = os.chown, os.fchown


@pytest.fixture
def source_attachment(tmp_path):
    shared = os.environ.get('QUANTOS_KERNEL_SOURCE')
    if shared is None:
        return original_source_attachment.__wrapped__(tmp_path)
    value = handoff.decode(Path(shared).read_bytes())
    bundle = tmp_path / 'release.bundle'
    shutil.copyfile(value['bundle_path'], bundle)
    bundle.chmod(0o600)
    clone = tmp_path / 'source-restored'
    subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', 'clone', '--quiet',
        str(bundle), str(clone)], check=True, capture_output=True, timeout=30)
    definition.verify_source(clone, value['identity'])
    return dict(value, source_root=str(clone), bundle_path=str(bundle))


@pytest.fixture
def registered_case(tmp_path, monkeypatch):
    # Choose the final configured project path before preparing any original
    # invocation; never move a completed producer and relabel its old root.
    generator = original_registered_case.__wrapped__(tmp_path, monkeypatch)
    value = next(generator)
    project = tmp_path / 'projects' / 'one'
    project.parent.mkdir(mode=0o700)
    def prepare(name='one', **extra):
        plan = value['run'].prepare_gold(value['arguments'], project_root=project, search_id=name,
            producer_id='isolated-native', actor_id='AUTOMATED_TEST', dataset_version='native-xau',
            registered_at_ms=value['clock'][0], **extra)
        value['directories'][name] = value['run'].run_directory(project, plan)
        return plan
    value['prepare'] = prepare
    try:
        yield value
    finally:
        try:
            next(generator)
        except StopIteration:
            pass


@pytest.mark.skipif(os.getuid() == 0 or 'QUANTOS_KERNEL_EXPORT' not in os.environ,
    reason='native producer runs only in the isolated selected UID process')
def test_export_original_native_project(native_research):
    f = native_research
    value = {key: f[key] for key in ('layout', 'source')}
    value['root'] = str(f['root'])
    value['layout']['components'][0]['path'] = 'projects/one'
    value['input_path'] = str(f['f']['directories']['one'] / 'input.json')
    Path(os.environ['QUANTOS_KERNEL_EXPORT']).write_bytes(canonical(value))


def produce(tmp_path, uid, *, shared=None):
    base = tmp_path / ('producer-' + str(uid))
    base.mkdir(mode=0o700)
    os.chown(base, uid, uid)
    exported = base / 'export.json'
    environment = dict(PATH='/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1', QUANTOS_KERNEL_EXPORT=str(exported))
    if shared is not None:
        environment['QUANTOS_KERNEL_SOURCE'] = str(shared)
    result = subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
        'tests/tools/test_demo_gold_research_capture_peer_kernel.py::test_export_original_native_project',
        '--basetemp=' + str(base / 'pytest')], user=uid, group=uid, extra_groups=[],
        env=environment, cwd='/code', stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return handoff.decode(exported.read_bytes())


@pytest.fixture
def native_roster(fixed_public, tmp_path, monkeypatch):
    assert os.getresuid() == os.getresgid() == (0, 0, 0)
    monkeypatch.setattr(os, 'chown', _CHOWN)
    monkeypatch.setattr(os, 'fchown', _FCHOWN)
    for path in (tmp_path, *tmp_path.parents):
        if path == Path('/work') or path.is_relative_to('/work'):
            path.chmod(0o755)
    uids = [10001, 10002]
    first = produce(tmp_path, uids[0])
    shared = tmp_path / 'shared'
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    bundle = shared / 'source.bundle'
    shutil.copyfile(first['source']['bundle_path'], bundle)
    bundle.chmod(0o644)
    accepted_source = shared / 'accepted-source'
    subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', 'clone', '--quiet',
        str(bundle), str(accepted_source)], check=True, capture_output=True, timeout=30)
    source = dict(first['source'], bundle_path=str(bundle))
    declaration = shared / 'source.json'
    declaration.write_bytes(canonical(source))
    declaration.chmod(0o644)
    second = produce(tmp_path, uids[1], shared=declaration)
    assert first['source']['identity'] == second['source']['identity']
    f = fixed_public
    commit = first['source']['identity']['commit']
    f.now = time.time_ns() // 1_000_000
    from tools import demo_supervisor as supervisor
    monkeypatch.setattr(supervisor, 'now_ms', lambda: time.time_ns() // 1_000_000)
    f.args.update(source_commit=commit, operation_sha256=handoff.native._digest(dict(case=str(tmp_path))))
    f.registry['research_uids'] = uids
    f.policy.update(executor_release_commit=commit, registry_sha256=handoff.native._digest(f.registry))
    for name, value in (('executor_registry', f.registry), ('issuer_registry', f.registry), ('policy', f.policy)):
        f.paths[name].write_bytes(canonical(value))
    release = f.module.RELEASES / commit
    (release / 'evidence').mkdir(parents=True, mode=0o755)
    (release / 'evidence/l2_release_evidence_manifest.json').write_bytes(canonical(dict(
        schema_version=1, readiness='L2_DRY_RUN_ONLY', source_commit=commit)))
    (release / 'evidence/l2_release_evidence_manifest.json').chmod(0o644)
    monkeypatch.setattr(supervisor, 'verify_source', lambda source: (
        definition.verify_source(accepted_source, first['source']['identity'])
        if source == commit else pytest.fail('wrong synthetic accepted-release boundary')))
    snapshot = f.module.capture(**f.args)
    public, cut = public_cut(f, snapshot)
    handle = f.module.bind_captured(snapshot, public_bundle=public, cut_request=cut, guard=f.args['guard'])
    roots = [dict(name='research_' + str(uid), root=value['root'], layout=value['layout'],
        project_collections=['projects']) for uid, value in zip(uids, (first, second))]
    config = tmp_path / 'fixed-layout.json'
    config.write_bytes(canonical(dict(schema=roster.DOCUMENT_SCHEMA, source_commit=commit,
        account_ref_sha256=public['account_ref_sha256'], registry_sha256=handoff.native._digest(f.registry),
        research_roots=roots, submission_enabled=False, economic_authority=False)))
    config.chmod(0o644)
    monkeypatch.setattr(roster, 'CONFIG', config)
    captured = roster.capture(verified_public=handle, public_bundle=public, cut_request=cut, guard=f.args['guard'])
    return dict(public=handle, roster=captured, values=[first, second], cut=cut,
        fixed_paths={key: str(path) for key, path in f.module.PATHS.items()},
        releases=str(f.module.RELEASES), partition_history=str(f.module.PARTITION_HISTORY))


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Linux root/UID container')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
@pytest.mark.parametrize('damage,channel_mode', [
    ('stable', 'inherited'), ('stable', 'stdin'), ('bytes', 'inherited'), ('omitted_uid', 'inherited')])
def test_root_peer_validates_both_native_archives_before_final_controls(native_roster, damage, channel_mode):
    from contextlib import ExitStack
    f = native_roster
    peers, children = [], []
    capture = peer.ResearchCaptureSet(verified_layout=f['roster'], verified_public=f['public'])
    with ExitStack() as stack:
        try:
            for value in f['values']:
                uid = value['layout']['uid']
                request = stack.enter_context(handoff.issue(verified_layout=f['roster'], verified_public=f['public'],
                    uid=uid, source_attachment=value['source']))
                workspace = handoff._workspace(f['cut'], uid)
                workspace.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                workspace.parent.chmod(0o755)
                workspace.mkdir(mode=0o700)
                os.chown(workspace, uid, uid)
                parent, inherited = handoff.channel_pair()
                program = '''
import json, sys, traceback
from pathlib import Path
sys.path.insert(0, '/code')
from tools import demo_gold_checkpoint_public as fixed
boundaries = json.loads(sys.argv[1])
fixed.PATHS = {key: Path(path) for key, path in boundaries['paths'].items()}
fixed.RELEASES = Path(boundaries['releases'])
fixed.PARTITION_HISTORY = Path(boundaries['history'])
from tools import demo_gold_research_capture_worker as worker
original = worker.run
def traced(channel):
    try:
        return original(channel)
    except Exception:
        traceback.print_exc()
        raise
worker.run = traced
del sys.argv[1]
raise SystemExit(worker.main())
'''
                boundaries = canonical(dict(paths=f['fixed_paths'], releases=f['releases'],
                    history=f['partition_history'])).decode()
                child = subprocess.Popen([sys.executable, '-I', '-B', '-c', program, boundaries,
                    '--control-fd', '0' if channel_mode == 'stdin' else str(inherited.fileno())],
                    user=uid, group=uid, extra_groups=[],
                    pass_fds=() if channel_mode == 'stdin' else (inherited.fileno(),), cwd='/work',
                    env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'},
                    stdin=inherited if channel_mode == 'stdin' else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                children.append(child)
                inherited.close()
                stack.callback(parent.close)
                pid, ticks = handoff.process_identity(Path('/proc') / str(child.pid))
                consumer = peer.connect(request, parent, worker_pid=pid, worker_start_ticks=ticks)
                peers.append(consumer)
                prepared = consumer.prepare()
                assert prepared['uid'] == uid
                with pytest.raises(ValueError, match='NATIVE_VERIFICATION_REQUIRED'):
                    consumer.recheck()
                if damage != 'omitted_uid' or len(peers) == 1:
                    capture.add(consumer)
            if damage == 'omitted_uid':
                with pytest.raises(ValueError, match='COMPLETE_RESEARCH_ROSTER'):
                    capture.verify_archives()
                assert not any((handoff.OUTPUT_ROOT / f['cut']['operation_sha256'] /
                    ('root-' + str(consumer.uid))).exists() for consumer in peers)
                return
            for uid, result in capture.verify_archives().items():
                assert result['source_uid'] == uid
                assert result['state']['research']['components']['registered']['native']['attempt_count'] == 6
                assert result['complete_gold_set_verified'] is result['production_resumable'] is False
            assert len(peers) == 2 and peers[0].uid != peers[1].uid
            if damage == 'bytes':
                original = Path(f['values'][1]['input_path'])
                original.write_bytes(original.read_bytes() + b' ')
                with pytest.raises(ValueError):
                    capture.recheck_all()
                with pytest.raises(ValueError, match='SET_ORDER'):
                    capture.close_all()
                assert not any(consumer._status['phase'] == 'CLOSED' for consumer in peers)
            else:
                capture.recheck_all()
                final = capture.close_all()
                assert final['research_uids'] == list(capture.uids) == [10001, 10002]
                assert final['native_research_archives_verified'] is True
                assert final['worker_exit_verified'] is final['whole_set_verified'] is False
                for status in final['captures'].values():
                    assert status['phase'] == 'CLOSED'
                    assert status['writer_barrier_verified'] is status['whole_set_verified'] is False
        finally:
            for consumer in peers:
                consumer.disconnect()
            for child in children:
                output, error = child.communicate(timeout=30)
                if damage == 'stable':
                    assert child.returncode == 0, output + error
