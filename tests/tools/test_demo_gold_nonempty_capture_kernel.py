"""Nonempty native issuer/executor and retained mirrors in real role workers.

Only test material, venue responses, manager/cgroup and accepted-release inputs
are synthetic. This does not prove a field deployment or trading authority.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from tests.tools.test_demo_gold_all_role_capture_kernel import (
    manager_boundary as manager_boundary,
    source_attachment as source_attachment, registered_case as registered_case,
    native_research as native_research, private_files as private_files,
    fixed_public as fixed_public, book as book, env as env, sources,
)
from tools import demo_gold_all_role_capture as coordinator
from tools import demo_gold_file_leases as leases
from tools import demo_gold_research_layout as roster
from tools import demo_supervisor as supervisor
from tools.gold_pilot_retention_store import Retention, canonical
from tests.live.gold_checkpoint_fixture import native_bar_checkpoint
from tests.live.test_gold_strategy_constraints import case as case
from tests.tools.test_demo_gold_research_capture_peer_kernel import produce, _CHOWN, _FCHOWN
from tests.tools.test_demo_gold_role_capture_kernel import restricted


@pytest.fixture
def original_nonempty(case, source_attachment, tmp_path, monkeypatch):
    from tests.live import test_gold_delegated_admission as delegated
    original = delegated.native_case
    def selected(*args, **kw):
        assert kw.get('configure_registry') is None
        # The original helper invokes this hook before reservation/signing.
        return original(*args, **dict(kw, configure_registry=lambda value:
            value.update(research_uids=[10001, 10002])))
    monkeypatch.setattr(delegated, 'native_case', selected)
    generator = native_bar_checkpoint.__wrapped__(case, source_attachment, tmp_path, monkeypatch)
    try:
        yield next(generator)
    finally:
        try:
            next(generator)
        except StopIteration:
            pass


@pytest.mark.skipif(os.getuid() != 2001 or 'QUANTOS_NONEMPTY_EXPORT' not in os.environ,
    reason='data producer runs only in the isolated selected executor UID')
def test_export_original_nonempty_material(original_nonempty, tmp_path):
    from tools import demo_namespace_checkpoint as checkpoint
    from tools.gold_bar_pilot_capacity import original_context, _capacity
    f = original_nonempty
    original = original_context(f.root, f.base_runtime, uid=os.getuid(), account=f.account)
    with Retention(f.root, original=original, base_runtime=f.base_runtime,
            uid=os.getuid(), role='executor', read_only=True, verify_history=True) as source:
        frames = []
        for _, body in source.conn.execute('SELECT ordinal,claim FROM frames ORDER BY ordinal'):
            claim = json.loads(body)
            raw = source.read_claim(claim, 'result')
            assert raw is not None
            frames.append(dict(claim=claim, result=json.loads(raw)))
    frozen, _, _ = checkpoint.snapshot_readonly_sqlite(f.ledger, tmp_path)
    frozen.rename(tmp_path / 'original-issuer.sqlite3')
    pilot = {key: json.loads((f.root / path).read_bytes()) for key, path in (
        ('request', 'gold-pilot-request.json'), ('configuration', 'gold-pilot/configuration.json'),
        ('intent', 'gold-pilot/enrollment-intent.json'), ('receipt', 'gold-pilot/enrollment.json'))}
    pilot['stop'] = None
    value = dict(root=str(f.root), issuer_database=str(tmp_path / 'original-issuer.sqlite3'),
        registry=f.registry, policy=f.base_policy, account_ref_sha256=f.account,
        source=f.source_attachment, base_runtime=f.base_runtime, current_runtime=f.current_runtime,
        original=original, frames=frames, records=dict(pilot=pilot, rotation=dict(base=f.base_runtime,
            current=f.current_runtime, records=getattr(f, 'rotation_records', []),
            capacity=_capacity(original, f.base_runtime))))
    Path(os.environ['QUANTOS_NONEMPTY_EXPORT']).write_bytes(canonical(value))


def _producer(tmp_path, declaration):
    base = tmp_path / 'native-material'
    base.mkdir(mode=0o700)
    os.chown(base, 2001, 2001)
    code = base / 'code'
    # This specific read-only input is the frozen source mounted by the driver.
    # Give only the private copy to the producer; no global Git exception or
    # mutation of /code is used, and source_identity still performs real reads.
    shutil.copytree('/code', code, symlinks=True)
    assert not (code / '.git/objects/info/alternates').exists()
    def python_tree(root):
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*.py') if '.git' not in path.parts}
    assert python_tree(code) == python_tree(Path('/code'))
    for path in (code, *code.rglob('*')):
        os.chown(path, 2001, 2001, follow_symlinks=False)
    export = base / 'export.json'
    environment = dict(PATH='/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1',
        OMP_NUM_THREADS='1', QUANTOS_KERNEL_SOURCE=str(declaration), QUANTOS_NONEMPTY_EXPORT=str(export))
    result = subprocess.run([sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
        'tests/tools/test_demo_gold_nonempty_capture_kernel.py::test_export_original_nonempty_material',
        '--basetemp=' + str(base / 'pytest')], user=2001, group=2001, extra_groups=[],
        env=environment, cwd=code, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return coordinator.native.decode(export.read_bytes())


@pytest.fixture
def role_roster(fixed_public, tmp_path, monkeypatch):
    assert os.getresuid() == os.getresgid() == (0, 0, 0)
    monkeypatch.setattr(os, 'chown', _CHOWN)
    monkeypatch.setattr(os, 'fchown', _FCHOWN)
    for path in (tmp_path, *tmp_path.parents):
        if path == Path('/work') or path.is_relative_to('/work'):
            path.chmod(0o755)
    first = produce(tmp_path, 10001)
    shared = tmp_path / 'shared'
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    bundle = shared / 'source.bundle'
    shutil.copyfile(first['source']['bundle_path'], bundle)
    bundle.chmod(0o644)
    declaration = shared / 'source.json'
    declaration.write_bytes(canonical(dict(first['source'], bundle_path=str(bundle))))
    declaration.chmod(0o644)
    second = produce(tmp_path, 10002, shared=declaration)
    material = _producer(tmp_path, declaration)
    assert material['registry']['executor_uid'] == 2001 and material['registry']['issuer_uid'] == 2002
    assert material['registry']['research_uids'] == [10001, 10002]
    commit = first['source']['identity']['commit']
    assert material['source']['current_commit'] == commit
    assert first['source']['identity'] == second['source']['identity']
    f = fixed_public
    f.registry, f.policy = deepcopy(material['registry']), deepcopy(material['policy'])
    f.now = time.time_ns() // 1_000_000
    f.args.update(source_commit=commit, account_ref_sha256=material['account_ref_sha256'],
        operation_sha256=coordinator.native._digest(dict(case=str(tmp_path))))
    for name, value in (('executor_registry', f.registry), ('issuer_registry', f.registry), ('policy', f.policy)):
        f.paths[name].write_bytes(canonical(value))
    release = f.module.RELEASES / commit
    (release / 'evidence').mkdir(parents=True, mode=0o755)
    manifest = release / 'evidence/l2_release_evidence_manifest.json'
    manifest.write_bytes(canonical(dict(schema_version=1, readiness='L2_DRY_RUN_ONLY', source_commit=commit)))
    manifest.chmod(0o644)
    accepted = shared / 'accepted-source'
    subprocess.run(['/usr/bin/git', '-c', 'core.hooksPath=/dev/null', 'clone', '--quiet',
        str(bundle), str(accepted)], check=True, capture_output=True, timeout=30)
    from tools.gold_bar_definition import verify_source
    monkeypatch.setattr(supervisor, 'now_ms', lambda: time.time_ns() // 1_000_000)
    monkeypatch.setattr(supervisor, 'verify_source', lambda value: (
        verify_source(accepted, first['source']['identity']) if value == commit else pytest.fail('wrong source')))
    snapshot = f.module.capture(**f.args)
    from tests.tools.test_demo_gold_checkpoint_public import public_cut
    public, cut = public_cut(f, snapshot)
    public.update(base_runtime=material['base_runtime'], current_runtime=material['current_runtime'])
    public['records'].update(material['records'])
    cut['public_bundle_sha256'] = coordinator.native._digest(public)
    handle = f.module.bind_captured(snapshot, public_bundle=public, cut_request=cut, guard=f.args['guard'])
    values = [first, second]
    config = tmp_path / 'fixed-layout.json'
    config.write_bytes(canonical(dict(schema=roster.DOCUMENT_SCHEMA, source_commit=commit,
        account_ref_sha256=public['account_ref_sha256'], registry_sha256=coordinator.native._digest(f.registry),
        research_roots=[dict(name='research_' + str(value['layout']['uid']), root=value['root'],
            layout=value['layout'], project_collections=['projects']) for value in values],
        submission_enabled=False, economic_authority=False)))
    config.chmod(0o644)
    monkeypatch.setattr(roster, 'CONFIG', config)
    captured = roster.capture(verified_public=handle, public_bundle=public, cut_request=cut, guard=f.args['guard'])
    roots = dict(executor=Path(material['root']), issuer=tmp_path / 'nonempty-issuer',
        controller=tmp_path / 'nonempty-controller')
    roots['issuer'].mkdir(mode=0o700)
    os.chown(roots['issuer'], 2002, 2002)
    shutil.copyfile(material['issuer_database'], roots['issuer'] / 'issuance.sqlite3')
    (roots['issuer'] / 'issuance.sqlite3').chmod(0o600)
    os.chown(roots['issuer'] / 'issuance.sqlite3', 2002, 2002)
    roots['controller'].mkdir(mode=0o700)
    from tools.demo_namespace_checkpoint import CONTROLLER_REQUIRED_DIRECTORIES
    for name in CONTROLLER_REQUIRED_DIRECTORIES:
        (roots['controller'] / name).mkdir(mode=0o700)
    with Retention(roots['controller'], original=material['original'], base_runtime=material['base_runtime'],
            uid=0, role='controller') as controller:
        for frame in material['frames']:
            controller.adopt(frame['claim'])
            controller.put(frame['claim'], 'result', frame['result'])
    monkeypatch.setattr(coordinator.native, 'ROLE_ROOTS', roots)
    role_sources = {}
    bundles = {}
    for attachment in coordinator.native.source_attachments(material['source']):
        path = shared / (attachment['identity']['commit'] + '.bundle')
        shutil.copyfile(attachment['bundle_path'], path)
        path.chmod(0o644)
        bundles[attachment['identity']['commit']] = path
    research_sources = {}
    consumers = [(namespace, coordinator.native._role_uid(f.registry, namespace)) for namespace in roots]
    consumers += [('research_' + str(value['layout']['uid']), value['layout']['uid']) for value in values]
    for namespace, uid in consumers:
        base = tmp_path / ('source-' + namespace)
        base.mkdir(mode=0o700)
        os.chown(base, uid, uid)
        attachments = []
        for attachment in coordinator.native.source_attachments(material['source']):
            original_commit = attachment['identity']['commit']
            clone = base / original_commit
            result = subprocess.run(restricted(uid, "import subprocess,sys;subprocess.run(['/usr/bin/git',"
                "'-c','core.hooksPath=/dev/null','clone','--quiet',sys.argv[1],sys.argv[2]],check=True)",
                str(bundles[original_commit]), str(clone)), cwd='/code', env=dict(PATH='/usr/bin:/bin'),
                capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stdout + result.stderr
            attachments.append(dict(attachment, source_root=str(clone), bundle_path=str(bundles[original_commit])))
        selected = dict(schema='GOLD_CHECKPOINT_SOURCE_SET_V1', current_commit=commit, attachments=attachments)
        if namespace in roots:
            role_sources[namespace] = selected
        else:
            research_sources[uid] = selected
    capture_values = [dict(value, source=research_sources[value['layout']['uid']]) for value in values]
    return dict(public=handle, roster=captured, values=capture_values, cut=cut, roots=roots, sources=role_sources,
        fixed_paths={key: str(path) for key, path in f.module.PATHS.items()}, releases=str(f.module.RELEASES),
        partition_history=str(f.module.PARTITION_HISTORY), material=material)


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Root/role container')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_full_nonempty_native_capture_joins_original_consumption_and_retained_mirrors(manager_boundary):
    b = manager_boundary
    f = b['f']
    def snapshot():
        return {role: {str(path.relative_to(root)): (path.stat().st_ino,
            hashlib.sha256(path.read_bytes()).hexdigest()) for path in root.rglob('*') if path.is_file()}
            for role, root in f['roots'].items()}
    before = snapshot()
    with leases.hold(verified_layout=f['roster'], verified_public=f['public']) as held:
        result = coordinator.capture_all(verified_layout=f['roster'], verified_public=f['public'],
            source_attachments=sources(f), file_leases=held)
    report = result['restored_role_joins']
    assert report['schema'] == 'GOLD_RESTORED_ROLE_JOINS_V3'
    assert report['research_restore_continuity_verified'] is True
    assert set(report['research_archives']) == {str(uid) for uid in result['research_uids']}
    for uid, binding in report['research_archives'].items():
        assert binding['archive_manifest_sha256'] == result['captures']['research'][uid]['archive_manifest_sha256']
        assert binding['inventory_sha256'] == result['captures']['research'][uid]['inventory_sha256']
    cash = report['cash_components']
    assert cash['as_of_ms'] == f['roster']._cut['opening_at_ms']
    assert cash['reported_cash_replay_verified'] is True
    assert cash['technical_cash_components_verified'] is False
    assert cash['status'] == 'PARTIAL'
    assert cash['episodes'][0]['cash']['status'] == 'OPENING_RETAINED'
    assert cash['episodes'][0]['reported_capture']['status'] == 'COMPLETE'
    assert cash['episodes'][0]['feedback']['status'] == 'COMPLETE'
    assert report['issuer_executor']['issuer_record_count'] == 5
    assert report['issuer_executor']['executor_reservation_count'] == 2
    assert len(report['issuer_executor']['bindings']) == 2
    assert report['issuer_executor']['unmatched_reservations'][0]['status'] == 'UNSIGNED_RESERVED'
    assert report['retention_pair_verified'] is True
    assert report['retention']['executor_checkpoint']['frames'] > 0
    assert report['retention']['executor_checkpoint']['frames'] == report['retention']['controller_checkpoint']['frames']
    assert report['retention']['verified_partial'] == []
    assert len(b['children']) == 5 and all(child.poll() == 0 for child in b['children'].values())
    assert all(report[key] is False for key in ('cross_role_business_join_verified', 'writer_barrier_verified',
        'whole_set_verified', 'cash_reconciled', 'mutation_permitted', 'submission_enabled', 'economic_authority'))
    assert snapshot() == before


@pytest.mark.skipif(os.getuid() != 0, reason='requires isolated Root/role container')
@pytest.mark.parametrize('fixed_public', ['absent'], indirect=True)
def test_individually_valid_empty_issuer_cannot_close_nonempty_execution_set(manager_boundary, monkeypatch, tmp_path):
    b = manager_boundary
    f = b['f']
    # Select a separate valid empty issuer source. Keep the original five-row
    # ledger and its copied source unchanged; never delete signing history.
    empty = tmp_path / 'empty-issuer-source'
    empty.mkdir(mode=0o700)
    os.chown(empty, 2002, 2002)
    # The manager fixture intercepts Popen only for capture workers. This
    # separate native database producer uses the saved real process primitive.
    with b['actual_popen'](restricted(2002,
        "import sys;sys.path.insert(0,'/code');from pathlib import Path;"
        "from live.execution.demo_issuer_store import IssuerStore;"
        "IssuerStore(Path(sys.argv[1])/'issuance.sqlite3',create=True).close()", str(empty)),
            cwd='/code', env=dict(PATH='/usr/bin:/bin'), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as child:
        output, _ = child.communicate(timeout=30)
        assert child.returncode == 0, output
    f['roots']['issuer'] = empty
    verified, closes = [], []
    original_fixed = coordinator.fixed.peer.RolePeer.verify_archive
    original_research = coordinator.research.peer.ResearchPeer.verify_archive
    original_close = coordinator.research.peer.ResearchPeer.close_capture
    def fixed(consumer):
        result = original_fixed(consumer)
        verified.append((consumer.namespace, consumer.uid))
        return result
    def research(consumer):
        result = original_research(consumer)
        verified.append(('research', consumer.uid))
        return result
    def close(consumer):
        closes.append(consumer.uid)
        return original_close(consumer)
    monkeypatch.setattr(coordinator.fixed.peer.RolePeer, 'verify_archive', fixed)
    monkeypatch.setattr(coordinator.research.peer.ResearchPeer, 'verify_archive', research)
    monkeypatch.setattr(coordinator.research.peer.ResearchPeer, 'close_capture', close)
    with pytest.raises(ValueError, match='SIGNED_ANCESTOR'):
        with leases.hold(verified_layout=f['roster'], verified_public=f['public']) as held:
            coordinator.capture_all(verified_layout=f['roster'], verified_public=f['public'],
                source_attachments=sources(f), file_leases=held)
    assert len(verified) == 5 and closes == []
    assert len(b['children']) == 5 and all(child.poll() is not None for child in b['children'].values())
