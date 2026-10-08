"""Scope-to-pilot handoff with original V2 and explicit virtual custody.

Keys, observations, accepted source and operator identity are isolated fixtures.
No real credential, account, service or order operation is performed.
"""
from copy import deepcopy
import hashlib
import json
import os

import pytest

from tests.live.test_demo_market_partition import book as book
from tests.live.test_demo_market_partition_runtime import installed as installed, shift
from tests.live.test_gold_first_enrollment_review import first as first
from tests.tools.test_gold_first_enrollment_materials import materials as materials
from tests.tools.test_gold_budget_scope_preparation import scope_inputs as scope_inputs
from tests.tools.test_gold_budget_scope_materials import day_materials as day_materials, checked
from tools import gold_bar_pilot_capacity as capacity, gold_pilot_retention as retention
from tools import gold_pilot_retention_store as store, gold_pilot_install as install
from tools.demo_resident_recovery import _check_path as native_path_check
from tools.gold_pilot_install import _read_target as native_target_read


def isolated_retention_custody(root,registry,tmp_path,monkeypatch):
    """Map signed fixture identities while retaining native file checks."""
    root.relative_to(tmp_path);root.chmod(0o700)
    controller_uid=os.geteuid();executor_uid=registry['executor_uid']
    def owner(path,uid):
        path.relative_to(tmp_path)
        expected=executor_uid if path==root or root in path.parents else controller_uid
        assert uid==expected
        return os.getuid()
    def check(path,uid,*,directory):
        native_path_check(path,owner(path,uid),directory=directory)
    def read(path,*,uid,maximum=65536):
        return native_target_read(path,uid=owner(path,uid),maximum=maximum)
    for module in (capacity,retention,store):monkeypatch.setattr(module,'_check_path',check)
    monkeypatch.setattr(install,'_read_target',read)


def scoped_request(day_materials):
    _,_,native=checked(day_materials)
    return dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V2',source_commit='c'*40,
        account_ref_sha256=native['policy']['account_ref_sha256'],duration_seconds=86400,
        max_loss_fraction='0.05',submission_enabled=False,
        budget_scope=native['policy'],scope_registry=native['registry'])


@pytest.fixture
def enrollment_case(day_materials,scope_inputs,monkeypatch):
    from live.execution import demo_authority
    value=scope_inputs[0];stores=value['stores'];root=stores['dispatch'].path.parent
    request=scoped_request(day_materials)
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:deepcopy(request['scope_registry']))
    (root/'gold-pilot-request.json').write_text(json.dumps(request))
    args=dict(root=root,source_commit='c'*40,origin=shift(day_materials['origin'],1000),
        **{name:stores[name] for name in ('dispatch','recovery','oms')})
    return args,request,day_materials['now_ms']+1000


@pytest.mark.parametrize('damage',['late_start','registry_revoked','signature'])
def test_invalid_scope_cannot_create_a_new_pilot_baseline(enrollment_case,monkeypatch,damage):
    from live.execution import demo_gold_pilot_enrollment as enrollment, demo_authority
    args,request,at=enrollment_case
    if damage=='late_start':
        at=request['budget_scope']['scope_enrollment']['installation_deadline_ms']+1
        args['origin']=shift(args['origin'],at-(enrollment_case[2]))
    elif damage=='registry_revoked':
        registry=deepcopy(request['scope_registry']);registry['keys'][-1]['status']='REVOKED'
        monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:registry)
    else:
        request['budget_scope']['max_total_notional']='749'
        (args['root']/'gold-pilot-request.json').write_text(json.dumps(request))
    before=enrollment._state(args['dispatch'],args['recovery'],args['oms'])
    with pytest.raises(ValueError):enrollment.enroll_requested(**args,now_ms=at)
    assert not (args['root']/'gold-pilot').exists()
    assert enrollment._state(args['dispatch'],args['recovery'],args['oms'])==before


@pytest.mark.parametrize('target',['configuration.json','enrollment.json'])
def test_partial_scope_pilot_resumes_after_start_deadline_without_renewal(enrollment_case,monkeypatch,target):
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from tests.live.test_gold_pilot_enrollment_recovery import interrupted
    args,request,at=enrollment_case;area=args['root']/'gold-pilot'
    interrupted(args,monkeypatch,at,target)
    existing={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}
    intent=json.loads((area/'enrollment-intent.json').read_bytes())
    current={**args,'origin':shift(args['origin'],301000)}
    config=enrollment.enroll_requested(**current,now_ms=at+301000)
    assert at+301000>request['budget_scope']['scope_enrollment']['installation_deadline_ms']
    assert config==intent['prepared']['configuration']
    assert config['started_at_ms']==at and config['ends_at_ms']==at+86400000
    assert config['initial_equity']=='4200'
    assert all(p.read_bytes()==raw for p,raw in existing.items())


def test_completed_scope_pilot_records_expiry_without_current_authorization(enrollment_case,monkeypatch):
    from live.execution import demo_gold_pilot_enrollment as enrollment, demo_gold_pilot as pilot, demo_authority
    from live.execution.demo_batch_risk import _digest
    args,request,at=enrollment_case
    config=enrollment.enroll_requested(**args,now_ms=at)
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:pytest.fail('historical read renewed authority'))
    assert enrollment.enroll_requested(**args,now_ms=request['budget_scope']['valid_until_ms']+1)==config
    account=dict(account_ref_sha256=request['account_ref_sha256'],equity='4200')
    stopped=pilot.observe(account,now_ms=request['budget_scope']['valid_until_ms']+1,
        origin_sha256=_digest(args['origin']))
    assert stopped['status']=='STOPPED' and stopped['reason']=='DURATION_COMPLETE'
    assert config['initial_equity']=='4200' and config['started_at_ms']==at


def test_partial_scope_pilot_cannot_resume_after_public_key_revocation(enrollment_case,monkeypatch):
    from live.execution import demo_gold_pilot_enrollment as enrollment, demo_authority
    from tests.live.test_gold_pilot_enrollment_recovery import interrupted
    args,request,at=enrollment_case;area=args['root']/'gold-pilot'
    interrupted(args,monkeypatch,at,'enrollment.json')
    before={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}
    registry=deepcopy(request['scope_registry']);registry['keys'][-1]['status']='REVOKED'
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:registry)
    current={**args,'origin':shift(args['origin'],301000)}
    with pytest.raises(ValueError,match='SCOPE_REGISTRY_CHANGED'):
        enrollment.enroll_requested(**current,now_ms=at+301000)
    assert before=={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}
    assert not (area/'enrollment.json').exists()


def test_renderer_retains_signed_scope_without_order_approval(day_materials):
    from scripts.render_gold_pilot_unit import render
    request=scoped_request(day_materials)
    result=render(release_commit='c'*40,account_ref_sha256=request['account_ref_sha256'],
        research_state_root='/home/operator/gold-research/state',execute_approved=True,
        budget_scope=request['budget_scope'],scope_registry=request['scope_registry'])
    assert result['enrollment_request']==request
    assert request['budget_scope']['scope_enrollment']['execution_approval_granted'] is False


def test_original_enrollment_binds_actual_start_to_signed_scope(day_materials,scope_inputs,monkeypatch):
    from live.execution import demo_gold_pilot_enrollment as enrollment, demo_authority
    from live.execution.demo_pre_submit import _state
    value=scope_inputs[0];stores=value['stores'];root=stores['dispatch'].path.parent
    request=scoped_request(day_materials);at=day_materials['now_ms']+1000
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:deepcopy(request['scope_registry']))
    (root/'gold-pilot-request.json').write_text(json.dumps(request))
    origin=shift(day_materials['origin'],1000);before=deepcopy(value['state'])
    config=enrollment.enroll_requested(root=root,source_commit='c'*40,origin=origin,
        **{name:stores[name] for name in ('dispatch','recovery','oms')},now_ms=at)
    assert config['started_at_ms']==at and config['ends_at_ms']==at+86400000
    assert config['initial_equity']=='4200' and config['ends_at_ms']<=request['budget_scope']['valid_until_ms']
    intent=json.loads((root/'gold-pilot/enrollment-intent.json').read_bytes())
    assert enrollment.completed_configuration(root/'gold-pilot',request=request)==config
    assert intent['prepared']['configuration']==config
    assert _state(**{name:stores[name] for name in ('dispatch','recovery','oms')})==before


@pytest.fixture
def scope_installation(day_materials,scope_inputs,tmp_path,monkeypatch):
    from tools import gold_scope_pilot_materials as materials_tool, gold_pilot_install as tool
    from tools import gold_strategy_controller as controller
    request=scoped_request(day_materials);bundle=tmp_path/'bundle';bundle.mkdir(mode=0o700)
    manifest,files=materials_tool.assemble(scope=request['budget_scope'],registry=request['scope_registry'],
        source_commit='c'*40,research_state_root='/home/operator/gold-research/state',execute_approved=True,
        now_ms=day_materials['now_ms'])
    for name,raw in files.items():(bundle/name).write_bytes(raw)
    raw=json.dumps(manifest,sort_keys=True).encode();(bundle/'review.json').write_bytes(raw)
    state=tmp_path/'controller';state.mkdir(mode=0o700)
    units=tmp_path/'units';units.mkdir(mode=0o700)
    root=scope_inputs[0]['stores']['dispatch'].path.parent
    (state/'strategy-runtime.json').write_text(json.dumps(day_materials['runtime']))
    monkeypatch.setattr(tool.supervisor,'STATE',state)
    monkeypatch.setattr(tool.installer,'EXECUTOR',root)
    monkeypatch.setattr(tool,'UNIT',units/'quantos-gold-pilot.service')
    monkeypatch.setattr(tool.supervisor,'now_ms',lambda:day_materials['now_ms'])
    monkeypatch.setattr(tool.supervisor,'_read_root_owned_json',lambda path:path.read_bytes())
    monkeypatch.setattr(tool.installer,'read_private',lambda path,**kw:path.read_bytes())
    monkeypatch.setattr(controller,'observation_configuration',lambda _:deepcopy((request['scope_registry'],request['budget_scope'])))
    monkeypatch.setattr(tool.supervisor,'configuration',lambda _:pytest.fail('Scope entered execution configuration'))
    monkeypatch.setattr(tool,'_protected_targets',lambda _:None)
    monkeypatch.setattr(tool,'_unit_absent',lambda:None)
    # Future HUMAN gate/custody are virtual; writes, fsync, lease and readback stay real.
    monkeypatch.setattr(tool,'local_operator',lambda:'isolated-operator')
    monkeypatch.setattr('builtins.input',lambda prompt:prompt.removeprefix('请输入 ').strip())
    monkeypatch.setattr(tool.supervisor,'_root_private_directory',lambda _:None)
    monkeypatch.setattr(tool.installer.os,'chown',lambda *args:None)
    monkeypatch.setattr(tool.installer.os,'fchown',lambda *args:None)
    monkeypatch.setattr(tool,'_check_path',lambda *args,**kwargs:None)
    original=tool._target_metadata
    monkeypatch.setattr(tool,'_target_metadata',lambda info,*,uid,maximum:
        original(info,uid=info.st_uid,maximum=maximum))
    args=dict(source_commit='c'*40,bundle_dir=bundle,review_sha256=hashlib.sha256(raw).hexdigest(),
        research_state_root='/home/operator/gold-research/state')
    from tests.tools.test_gold_pilot_install import bind_feedback
    bind_feedback(tool,args,request['scope_registry'],tmp_path,monkeypatch)
    return tool,args,request,root


def test_scope_bundle_is_reviewed_by_original_pilot_installer(scope_installation):
    tool,args,_,root=scope_installation
    result=tool.review(**args)
    assert result['scope_only'] is True and result['execution_approval_granted'] is False
    assert result['installed'] is False and result['started'] is False
    assert not tool.UNIT.exists() and not (root/'gold-pilot-request.json').exists()


def test_scope_installer_to_original_native_enrollment_preserves_account(scope_installation,day_materials,scope_inputs,monkeypatch):
    from live.execution import demo_gold_pilot_enrollment as enrollment, demo_authority
    from live.execution.demo_issuer_policy import validate_policy, current_key
    tool,args,request,root=scope_installation
    stores=scope_inputs[0]['stores'];before=enrollment._state(stores['dispatch'],stores['recovery'],stores['oms'])
    installed=tool.install_approved(**args)
    assert installed['installed'] is True and installed['started'] is False
    assert installed['orders_submitted'] is False and installed['economic_authority'] is False
    assert tool.UNIT.read_bytes()==(args['bundle_dir']/tool.UNIT.name).read_bytes()
    assert json.loads((root/'gold-pilot-request.json').read_bytes())==request
    assert not (root/'gold-pilot').exists()
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:deepcopy(request['scope_registry']))
    at=day_materials['now_ms']+1000
    native=dict(root=root,source_commit='c'*40,origin=shift(day_materials['origin'],1000),
        **{name:stores[name] for name in ('dispatch','recovery','oms')})
    config=enrollment.enroll_requested(**native,now_ms=at)
    assert config['initial_equity']=='4200' and config['started_at_ms']==at
    assert config['ends_at_ms']==at+86400000 and config['submission_enabled'] is False
    assert enrollment.completed_configuration(root/'gold-pilot',request=request)==config
    assert enrollment._state(stores['dispatch'],stores['recovery'],stores['oms'])==before
    with pytest.raises(ValueError,match='EXISTING_OR_PARTIAL'):tool.install_approved(**args)
    with pytest.raises(ValueError):validate_policy(request['budget_scope'])
    with pytest.raises(ValueError):current_key(request['budget_scope'],request['scope_registry'],at)


@pytest.mark.parametrize('damage',['expired','runtime','service'])
def test_scope_installation_rejects_expiry_or_rehashed_substitution(scope_installation,day_materials,monkeypatch,damage):
    tool,args,request,root=scope_installation
    if damage=='expired':
        monkeypatch.setattr(tool.supervisor,'now_ms',lambda:
            request['budget_scope']['scope_enrollment']['installation_deadline_ms']+1)
    elif damage=='runtime':
        runtime=deepcopy(day_materials['runtime']);runtime['seconds']=299
        (tool.supervisor.STATE/'strategy-runtime.json').write_text(json.dumps(runtime))
    else:
        path=args['bundle_dir']/'quantos-gold-pilot.service'
        path.write_bytes(path.read_bytes()+b'\nExecStartPost=/bin/unsafe\n')
        manifest=json.loads((args['bundle_dir']/'review.json').read_bytes())
        manifest['files'][path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
        raw=json.dumps(manifest).encode();(args['bundle_dir']/'review.json').write_bytes(raw)
        args['review_sha256']=hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError):tool.review(**args)
    assert not tool.UNIT.exists() and not (root/'gold-pilot-request.json').exists()
    assert not (root/'gold-pilot').exists()


def test_worker_starts_scope_pilot_before_a_long_fresh_bar_capture(day_materials,scope_inputs,monkeypatch,tmp_path):
    from tools import gold_strategy_worker as worker
    from live.execution.demo_pre_submit import _state
    value=scope_inputs[0];stores=value['stores'];root=stores['dispatch'].path.parent
    request=scoped_request(day_materials);at=day_materials['now_ms']+1000
    (root/'gold-pilot-request.json').write_text(json.dumps(request))
    (root/'gold-pilot-request.json').chmod(0o600)
    uid=request['scope_registry']['executor_uid'];account=request['account_ref_sha256']
    monkeypatch.setattr(worker,'verify_source',lambda _:None)
    monkeypatch.setattr(worker,'fixed_account',lambda:(root,account))
    monkeypatch.setattr(worker.demo_authority,'load_registry_for_executor',lambda:deepcopy(request['scope_registry']))
    monkeypatch.setattr(worker.os,'geteuid',lambda:uid)
    monkeypatch.setattr(worker.os,'getegid',lambda:uid)
    monkeypatch.setattr(worker.os,'getgroups',lambda:[uid])
    isolated_retention_custody(root,request['scope_registry'],tmp_path,monkeypatch)
    monkeypatch.setattr(worker,'private_pipe',lambda _:None)
    monkeypatch.setattr(worker,'require_custody',lambda:root)
    monkeypatch.setattr(worker,'_open',lambda *args:stores)
    monkeypatch.setattr(worker,'now_ms',lambda:at)
    monkeypatch.setattr(worker,'reconcile_pending',lambda *args,**kw:{'status':'NO_PENDING'})
    monkeypatch.setattr(worker,'capture_origin',lambda **kw:shift(day_materials['origin'],1000))
    before=deepcopy(value['state']);observed=[]
    class Frames:
        def frame(self,*args,**kw):
            config=json.loads((root/'gold-pilot/configuration.json').read_bytes())
            observed.append(config)
            assert config['started_at_ms']==at and config['initial_equity']=='4200'
            raise ValueError('isolated stop at start of long bar collection')
    read,write=os.pipe()
    with os.fdopen(write,'wb') as stream:stream.write(json.dumps(day_materials['runtime']).encode()+b'\n')
    output=tmp_path/'worker-output.json'
    try:
        with output.open('w') as out:
            assert worker.run(source_commit='c'*40,input_fd=read,output=out,bar_sessions=Frames())==3
    finally:os.close(read)
    assert len(observed)==1
    assert _state(**{name:stores[name] for name in ('dispatch','recovery','oms')})==before
