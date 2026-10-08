"""Fixed public reads over native partition installation, with virtual OS roles.

The original BTC receipt/cash/book, partition candidate/install and pure content
checks are real. Root/UID/GID and accepted-release verification are synthetic;
all reads/writes stay under tmp_path and no credentials or services are used.
"""
from copy import deepcopy
import hashlib
import json
import stat
from types import SimpleNamespace

import pytest

from live.execution import demo_authority
from live.execution.demo_batch_risk import _digest
from tools import demo_supervisor as supervisor, demo_operating_bundle as operating
from tests.tools.test_demo_market_partition_install import book as book, env as env

_READER = demo_authority._read_root_owned_json
_PRIVATE = operating.read_private
_PROTECTED = demo_authority._require_protected
_PRIVATE_METADATA = operating.private_metadata


@pytest.fixture
def fixed_public(env, book, tmp_path, monkeypatch, request):
    tool, candidate, registry, controller = env
    if getattr(request,'param','installed') == 'installed':
        installed = tool.install_approved(source_commit='c' * 40)
        assert installed['installed'] is True
    # Import follows a complete native installation, so missing API is not
    # confused with missing original partition evidence.
    from tools import demo_gold_checkpoint_public as public
    from tests.live.test_demo_issuer import policy_for
    proof = json.loads(next((book[3]['dispatch'].path.parent / 'evidence').glob('*.json')).read_bytes())
    policy = policy_for(proof, registry)
    paths = dict(executor_registry=tool.EXECUTOR_RECORD.parent/'authority.json',
        issuer_registry=tool.ISSUER_RECORD.parent/'authority.json',
        policy=tool.ISSUER_RECORD.parent/'policy.json', executor_partition=tool.EXECUTOR_RECORD,
        issuer_partition=tool.ISSUER_RECORD, partition_intent=tool.INTENT, activation=tool.ACTIVATION)
    for name, value in (('executor_registry', registry), ('issuer_registry', registry), ('policy', policy)):
        paths[name].write_bytes(operating.canonical(value)); paths[name].chmod(0o640)
    releases=tmp_path/'releases'; release=releases/('c'*40); (release/'evidence').mkdir(parents=True)
    for directory in (releases,release,release/'evidence'):directory.chmod(0o755)
    evidence=release/'evidence/l2_release_evidence_manifest.json'
    evidence.write_text(json.dumps(dict(schema_version=1,readiness='L2_DRY_RUN_ONLY',source_commit='c'*40)))
    evidence.chmod(0o644)
    monkeypatch.setattr(public,'PATHS',paths)
    monkeypatch.setattr(public,'RELEASES',releases)
    monkeypatch.setattr(public,'PARTITION_HISTORY',controller/'market-partition-installations')
    monkeypatch.setattr(supervisor,'EXECUTOR_REGISTRY',paths['executor_registry'])
    monkeypatch.setattr(supervisor,'ISSUER_REGISTRY',paths['issuer_registry'])
    monkeypatch.setattr(supervisor,'ISSUER_POLICY',paths['policy'])
    monkeypatch.setattr(public.os,'geteuid',lambda:0)
    verified=[]
    def source(commit):
        assert commit=='c'*40; verified.append(commit)
    monkeypatch.setattr(supervisor,'verify_source',source)
    # Only ownership is virtual. Original descriptor traversal, regular-file,
    # single-link, size, mode and byte checks still run.
    expected_gids={path.stat().st_ino:(registry['executor_uid'] if name.startswith('executor')
        else 0 if name in ('partition_intent','activation') else registry['issuer_uid'])
        for name,path in paths.items() if path.exists()}
    expected_gids[evidence.stat().st_ino]=0
    # /tmp and the pytest runner's ancestors are not deployment directories.
    # Virtualize only those ancestors, keeping every test-owned mode real.
    ancestors={p.stat().st_ino for p in tmp_path.parents}
    def virtual(info):
        fields={name:getattr(info,name) for name in ('st_dev','st_ino','st_uid','st_gid','st_mode',
            'st_nlink','st_size','st_mtime_ns','st_ctime_ns')}
        fields.update(st_uid=0,st_gid=expected_gids.get(info.st_ino,0))
        if info.st_ino in ancestors:fields['st_mode'] &= ~0o022
        return SimpleNamespace(**fields)
    original_identity=public._identity
    monkeypatch.setattr(public,'_identity',lambda info:original_identity(virtual(info)))
    def protected(info,*,directory):
        _PROTECTED(virtual(info),directory=directory)
    monkeypatch.setattr(demo_authority,'_require_protected',protected)
    monkeypatch.setattr(operating,'_require_protected',protected)
    monkeypatch.setattr(operating,'private_metadata',lambda info,maximum:
        _PRIVATE_METADATA(virtual(info),maximum=maximum))
    monkeypatch.setattr(public,'_require_protected',protected)
    def reader(path,*,file_check=None):
        return _READER(path,file_check=None if file_check is None else lambda info:file_check(virtual(info)))
    monkeypatch.setattr(public,'_read_root_owned_json',reader)
    monkeypatch.setattr(supervisor,'_read_root_owned_json',reader)
    monkeypatch.setattr(public,'_read_private',_PRIVATE)
    args=dict(source_commit='c'*40,operation_sha256='d'*64,
        account_ref_sha256=candidate['binding']['account_ref_sha256'],guard=lambda:None)
    return SimpleNamespace(module=public,paths=paths,args=args,registry=registry,policy=policy,
        now=tool.supervisor.now_ms(),history=controller/'market-partition-installations',verified=verified)


def test_fixed_native_install_is_captured_and_rechecked_without_effects(fixed_public):
    f=fixed_public;before={key:path.read_bytes() for key,path in f.paths.items()}
    assert f.now > f.policy['valid_until_ms']  # Historical inspection grants nothing.
    snapshot=f.module.capture(**f.args)
    assert snapshot['partition_status']=='INSTALLED'
    assert f.module.recheck(snapshot,guard=f.args['guard'])==snapshot
    assert {key:path.read_bytes() for key,path in f.paths.items()}==before
    assert len(f.verified)>=2


@pytest.mark.parametrize('damage',['executor_partition','activation','partition_intent'])
def test_partial_install_is_never_absence(fixed_public,damage):
    f=fixed_public;f.paths[damage].unlink()
    with pytest.raises(ValueError):f.module.capture(**f.args)


def test_registry_mirror_drift_rejects(fixed_public):
    f=fixed_public;value=deepcopy(f.registry);value['deployment_sha256']='f'*64
    f.paths['issuer_registry'].write_bytes(operating.canonical(value))
    with pytest.raises(ValueError):f.module.capture(**f.args)


def test_same_bytes_replaced_after_capture_reject(fixed_public):
    f=fixed_public;snapshot=f.module.capture(**f.args);path=f.paths['activation'];raw=path.read_bytes()
    path.unlink();path.write_bytes(raw);path.chmod(0o644)
    with pytest.raises(ValueError):f.module.recheck(snapshot,guard=f.args['guard'])


def test_existing_history_prevents_deleted_originals_becoming_absence(fixed_public):
    f=fixed_public
    for name in ('executor_partition','issuer_partition','activation','partition_intent'):f.paths[name].unlink()
    with pytest.raises(ValueError):f.module.capture(**f.args)


def test_leaf_read_cannot_bind_new_parent_bytes_to_old_descriptor(fixed_public,monkeypatch):
    f=fixed_public;path=f.paths['activation'];reader=f.module._read_root_owned_json
    def replaced(target,**kwargs):
        if target==path:
            raw=path.read_bytes(); parent=path.parent; parent.rename(parent.with_name('old-controller'))
            parent.mkdir(mode=0o700); path.write_bytes(raw);path.chmod(0o644)
        return reader(target,**kwargs)
    monkeypatch.setattr(f.module,'_read_root_owned_json',replaced)
    with pytest.raises(ValueError,match='CHANGED'):f.module._leaf(path)


def public_cut(f,snapshot):
    public=dict(schema='GOLD_CHECKPOINT_PUBLIC_CONTEXT_V1',source_commit=f.args['source_commit'],
        registry=deepcopy(f.registry),account_ref_sha256=f.args['account_ref_sha256'],
        base_runtime=None,current_runtime=None,records={'fixed_public':deepcopy(snapshot)})
    cut=dict(schema='GOLD_CHECKPOINT_CUT_REQUEST_V1',operation_sha256=f.args['operation_sha256'],
        source_commit=f.args['source_commit'],registry_sha256=_digest(f.registry),
        public_bundle_sha256=_digest(public),roles=['executor','controller','issuer','research'],
        writer_barrier_sha256='a'*64,opening_at_ms=f.now,submission_enabled=False,economic_authority=False)
    return public,cut


@pytest.mark.parametrize('fixed_public',['absent'],indirect=True)
def test_never_installed_absence_requires_all_fixed_originals(fixed_public):
    f=fixed_public;snapshot=f.module.capture(**f.args);public,cut=public_cut(f,snapshot)
    assert snapshot['partition_status']=='VERIFIED_ABSENT'
    handle=f.module.bind_captured(snapshot,public_bundle=public,cut_request=cut,guard=f.args['guard'])
    assert handle.partition_at(f.now)=={'status':'VERIFIED_ABSENT'}
    assert handle.snapshot_sha256==_digest(snapshot)
    assert handle.public_bundle_sha256==_digest(public) and handle.cut_request_sha256==_digest(cut)
    assert f.module.require_verified(handle,source_commit=f.args['source_commit'],
        account_ref_sha256=f.args['account_ref_sha256']) is handle
    with pytest.raises(ValueError):f.module.require_verified({'status':'VERIFIED_ABSENT'},
        source_commit=f.args['source_commit'],account_ref_sha256=f.args['account_ref_sha256'])


@pytest.mark.parametrize('fixed_public',['absent'],indirect=True)
@pytest.mark.parametrize('damage',['activation','intent','permission','parent','io'])
def test_absence_does_not_swallow_partial_or_inaccessible_originals(fixed_public,monkeypatch,damage):
    f=fixed_public
    if damage in ('activation','intent'):
        path=f.paths['activation' if damage=='activation' else 'partition_intent'];path.write_text('{}');path.chmod(0o600)
    elif damage=='permission':f.paths['issuer_registry'].chmod(0o660)
    elif damage=='parent':f.paths['executor_registry'].parent.chmod(0o777)
    else:
        reader=f.module._read_root_owned_json
        def read(path,**kwargs):
            if path==f.paths['executor_registry']:raise PermissionError('isolated I/O denial')
            return reader(path,**kwargs)
        monkeypatch.setattr(f.module,'_read_root_owned_json',read)
    with pytest.raises((ValueError,OSError)):f.module.capture(**f.args)


def test_capture_handle_rechecks_actual_originals_for_every_partition_lookup(fixed_public):
    f=fixed_public;snapshot=f.module.capture(**f.args);public,cut=public_cut(f,snapshot)
    handle=f.module.bind_captured(snapshot,public_bundle=public,cut_request=cut,guard=f.args['guard'])
    view=handle.partition_at(f.now)
    assert view['status']=='INSTALLED'
    view['record']['installed']=False
    assert handle.partition_at(f.now)['record']['installed'] is True
    f.paths['activation'].unlink()
    with pytest.raises(ValueError):handle.partition_at(f.now)


def test_installed_source_is_not_relabelled_as_new_release(fixed_public):
    f=fixed_public;snapshot=f.module.capture(**f.args)
    record=json.loads(f.paths['executor_partition'].read_bytes());record['source_commit']='e'*40
    for name in ('executor_partition','issuer_partition'):f.paths[name].write_bytes(operating.canonical(record))
    with pytest.raises(ValueError):f.module.capture(**f.args)
    assert snapshot['source_commit']=='c'*40


def archive_materials(f,tmp_path):
    """Original index writer over actual retained files; role launch is synthetic.

    This tests the saved chain contract. B's namespace integration exercises
    full CaptureInventory/transport; this helper does not claim a writer barrier.
    """
    from tools import demo_gold_namespace_checkpoint as native
    snapshot=f.module.capture(**f.args);public,cut=public_cut(f,snapshot)
    entries=[]
    for name,path in sorted(f.paths.items()):
        info=path.stat();raw=path.read_bytes()
        identity=dict(zip(native.IDENTITY_NAMES,(getattr(info,key) for key in native.IDENTITY)))
        identity.update(uid=f.registry['executor_uid'],gid=f.registry['executor_uid'],mode=stat.S_IFREG|0o600)
        entries.append(dict(kind='FILE',component='ordinary',relative=name+'.json',source_identity=identity,
            original_sha256=hashlib.sha256(raw).hexdigest(),stored_sha256=hashlib.sha256(raw).hexdigest(),
            logical_bytes=len(raw),snapshot_method='STABLE_FILE_READ',extents=native._extents(path)))
    indexes=native._write_index(tmp_path/'index',entries)
    root=f.paths['executor_registry'].parent.stat();uid=f.registry['executor_uid']
    inventory=dict(schema=native.SCHEMA,operation_sha256=f.args['operation_sha256'],cut_request_sha256=_digest(cut),
        namespace='executor',source_commit=f.args['source_commit'],registry_sha256=_digest(f.registry),
        account_ref_sha256=f.args['account_ref_sha256'],pilot_identity=None,
        source_root=dict(path=str(f.paths['executor_registry'].parent),device=root.st_dev,inode=root.st_ino,
            uid=uid,gid=uid,mode=stat.S_IFDIR|0o700),index=indexes,semantic_view={'schema':'ISOLATED_INDEX_CONTRACT'},
        component_bounds={},files=len(entries),directories=0,payload_bytes=sum(row['logical_bytes'] for row in entries),
        source_attachment_sha256='f'*64,source_dependencies={},submission_enabled=False,economic_authority=False)
    receipt={key:inventory[key] for key in ('operation_sha256','cut_request_sha256','namespace','source_commit',
        'registry_sha256','source_attachment_sha256')}
    receipt.update(schema=native.RECEIPT_SCHEMA,inventory_sha256=_digest(inventory),
        semantic_view_sha256=_digest(inventory['semantic_view']),before_sha256=native._source_digest(entries),
        after_sha256=native._source_digest(entries),started_at_ms=f.now,completed_at_ms=f.now,
        stable=True,worker_identity=dict(uid=uid,gid=uid,groups=[uid],pid=123,start_ticks=456),
        submission_enabled=False,economic_authority=False)
    return snapshot,dict(public_bundle=public,cut_request=cut,inventory=inventory,role_receipt=receipt,
        index_entries=entries,as_of_ms=f.now)


def test_archive_handle_never_reads_current_fixed_state(fixed_public,tmp_path,monkeypatch):
    f=fixed_public;snapshot,args=archive_materials(f,tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('archive read current protected state')
    monkeypatch.setattr(f.module,'capture',forbidden)
    handle=f.module.validate_archived(snapshot,**args)
    assert handle.partition_at(f.now)['record']==json.loads(f.paths['executor_partition'].read_bytes())
    args['public_bundle']['records'].clear()
    assert handle.recheck() is handle


@pytest.mark.parametrize('damage',['public','cut','inventory','receipt','index','index-body','missing-fixed',
    'role','numeric-alias','original-intent'])
def test_saved_chain_requires_actual_full_public_cut_receipt_and_index(fixed_public,tmp_path,damage):
    f=fixed_public;snapshot,args=archive_materials(f,tmp_path)
    if damage=='public':args['public_bundle']['records']['unrelated']={'changed':True}
    elif damage=='cut':args['cut_request']['writer_barrier_sha256']='0'*64
    elif damage=='inventory':args['inventory']['pilot_identity']={'changed':True}
    elif damage=='receipt':args['role_receipt']['inventory_sha256']='0'*64
    elif damage=='index':args['index_entries'].pop()
    elif damage=='index-body':args['index_entries'][0]['original_sha256']='0'*64
    elif damage=='missing-fixed':args['public_bundle']['records'].pop('fixed_public')
    elif damage=='role':args['role_receipt']['worker_identity']['uid']+=1
    elif damage=='numeric-alias':args['inventory']['directories']=False
    else:
        from base64 import b64decode,b64encode
        leaf=snapshot['files']['partition_intent'];value=json.loads(b64decode(leaf['body']))
        value['operator_approval_sha256']='0'*64;raw=operating.canonical(value)
        leaf['body']=b64encode(raw).decode();leaf['identity']['st_size']=len(raw)
    with pytest.raises(ValueError):f.module.validate_archived(snapshot,**args)


@pytest.mark.parametrize('damage',['root','source','guard','clock'])
def test_capture_retains_original_identity_source_guard_and_clock_gates(fixed_public,monkeypatch,damage):
    f=fixed_public
    if damage=='root':monkeypatch.setattr(f.module.os,'geteuid',lambda:1234)
    elif damage=='source':
        def deny(*args):raise ValueError('ISOLATED_RELEASE_NOT_ACCEPTED')
        monkeypatch.setattr(supervisor,'verify_source',deny)
    elif damage=='guard':
        calls=[]
        def guard():
            calls.append(1)
            if len(calls)==2:raise ValueError('ISOLATED_BARRIER_LOST')
        f.args['guard']=guard
    else:
        ticks=iter([f.now,f.now-1]);monkeypatch.setattr(supervisor,'now_ms',lambda:next(ticks))
    with pytest.raises(ValueError):f.module.capture(**f.args)


def test_same_snapshot_cannot_supply_handle_for_different_cut(fixed_public):
    f=fixed_public;snapshot=f.module.capture(**f.args);public,cut=public_cut(f,snapshot)
    handle=f.module.bind_captured(snapshot,public_bundle=public,cut_request=cut,guard=f.args['guard'])
    different=deepcopy(cut);different['writer_barrier_sha256']='e'*64
    assert handle.cut_request_sha256 != _digest(different)
    different['operation_sha256']='f'*64
    with pytest.raises(ValueError):f.module.bind_captured(snapshot,public_bundle=public,cut_request=different,
        guard=f.args['guard'])
