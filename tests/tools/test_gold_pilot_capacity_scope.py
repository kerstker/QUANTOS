"""Capacity follows original signed Scope and completed native enrollment.

Account, signing key and operator materials are isolated fixture objects. The
signature validator, completed enrollment intent/receipt and storage are native.
"""
from copy import deepcopy
import json
import os
import shutil

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.test_demo_market_partition import book as book
from tests.live.test_demo_market_partition_runtime import installed as installed, shift
from tests.live.test_gold_first_enrollment_review import first as first
from tests.tools.test_gold_first_enrollment_materials import materials as materials
from tests.tools.test_gold_budget_scope_preparation import scope_inputs as scope_inputs
from tests.tools.test_gold_budget_scope_materials import day_materials, checked
from tests.tools.test_gold_pilot_capacity import write


@pytest.fixture
def signed(scope_inputs,materials,tmp_path,monkeypatch):
    from live.execution import demo_authority, demo_gold_pilot_enrollment as enrollment
    from tools import demo_operating_bundle as installer, demo_supervisor as supervisor
    from tools import gold_strategy_controller as controller
    value,_,_,_,runtime=scope_inputs
    runtime['schema']='GOLD_BAR_AUTO_EPISODE_INPUT_V1';runtime['episode_id']=runtime.pop('history_sha256')
    parts=day_materials.__wrapped__(scope_inputs,materials)
    _,_,native=checked(parts)
    request=enrollment.scope_request(scope=native['policy'],registry=native['registry'],
        source_commit='c'*40,account_ref_sha256=native['policy']['account_ref_sha256'])
    stores=value['stores'];root=stores['dispatch'].path.parent;root.chmod(0o700)
    write(root/'gold-pilot-request.json',request)
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:deepcopy(request['scope_registry']))
    enrollment_args=dict(root=root,source_commit='c'*40,origin=shift(parts['origin'],1000),
        **{key:stores[key] for key in ('dispatch','recovery','oms')},now_ms=parts['now_ms']+1000)
    config=enrollment.enroll_requested(**enrollment_args)
    state=tmp_path/'capacity-controller';state.mkdir(mode=0o700)
    monkeypatch.setattr(installer,'EXECUTOR',root)
    monkeypatch.setattr(controller,'observation_configuration',lambda _:
        ({'executor_uid':os.getuid()},native['policy']))
    monkeypatch.setattr(supervisor,'_root_private_directory',lambda p:p.relative_to(tmp_path))
    monkeypatch.setattr(supervisor,'_read_root_owned_json',lambda p:p.read_bytes())
    return dict(root=root,state=state,request=request,config=config,base=runtime,native=native,
        enrollment_args=enrollment_args)


def test_signed_original_baseline_and_runtime_bind_capacity(signed,monkeypatch):
    from tools import gold_bar_rotation as rotation, gold_bar_pilot_capacity as capacity
    from live.execution import demo_authority
    original={p:p.read_bytes() for p in (signed['root']/'gold-pilot').rglob('*') if p.is_file()}
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:pytest.fail('root crossed identity'))
    args=dict(root=signed['state'],base=signed['base'],account=signed['request']['account_ref_sha256'])
    assert rotation.load(**args)==signed['base']
    saved=json.loads((signed['state']/capacity.MARKER).read_bytes())
    assert saved['base_runtime_sha256']==signed['request']['budget_scope']['scope_enrollment']['runtime_sha256']
    assert saved['configuration_sha256']==_digest(signed['config']) and saved['max_episodes']==98
    assert original=={p:p.read_bytes() for p in (signed['root']/'gold-pilot').rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage',['runtime','signature','intent','receipt','request_uid'])
def test_changed_signed_originals_cannot_allocate_storage(signed,monkeypatch,damage):
    from tools import gold_bar_rotation as rotation, gold_strategy_controller as controller
    base=deepcopy(signed['base']);root=signed['root'];area=root/'gold-pilot'
    if damage=='runtime':base['candidate']='trend'
    elif damage=='signature':
        request=deepcopy(signed['request']);request['budget_scope']['max_orders']+=1
        write(root/'gold-pilot-request.json',request)
    elif damage=='intent':
        value=json.loads((area/'enrollment-intent.json').read_bytes())
        value['prepared']['configuration']['initial_equity']='6000';write(area/'enrollment-intent.json',value)
    elif damage=='receipt':
        value=json.loads((area/'enrollment.json').read_bytes());value['configuration_sha256']='a'*64
        write(area/'enrollment.json',value)
    else:
        monkeypatch.setattr(controller,'observation_configuration',lambda _:
            ({'executor_uid':os.getuid()+1},signed['native']['policy']))
    with pytest.raises(ValueError):
        rotation.load(root=signed['state'],base=base,account=signed['request']['account_ref_sha256'])
    assert list(signed['state'].iterdir())==[]


@pytest.mark.parametrize('malformed',[None,[],{},True])
def test_original_completed_reader_rejects_present_malformed_intent(signed,malformed):
    from live.execution import demo_gold_pilot_enrollment as enrollment
    area=signed['root']/'gold-pilot';write(area/'enrollment-intent.json',malformed)
    with pytest.raises(ValueError):enrollment.completed_configuration(area,request=signed['request'])


def test_pending_controller_enrollment_first_worker_capture_and_retained_result_rotate(signed,monkeypatch):
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from live.execution.demo_pre_submit import _retain
    from tools import gold_bar_rotation as rotation, gold_bar_auto_history as history, gold_bar_pilot_capacity as capacity
    from tools.demo_request_prepare import destination
    from tests.tools.test_gold_bar_rotation import outcome
    from tests.tools.test_gold_pilot_capacity import history_capture
    root,state,base=signed['root'],signed['state'],signed['base']
    # Isolated baseline is moved intact to model the moment immediately before
    # the native executor's first enrollment; no production state is involved.
    shutil.move(root/'gold-pilot',root.parent/'prior-fixture-enrollment')
    arguments=dict(root=state,base=base,account=signed['request']['account_ref_sha256'])
    assert rotation.load(**arguments)==base and list(state.iterdir())==[]
    config=enrollment.enroll_requested(**signed['enrollment_args'])
    assert config==signed['config']
    calls=history_capture(monkeypatch)
    history.load(root,base,uid=os.getuid())
    result=outcome(base)
    result['decision'].update(account_ref_sha256=arguments['account'],observed_at_ms=config['started_at_ms']+100,
        pilot_control=dict(status='ACTIVE',configuration_sha256=_digest(config),submission_enabled=False))
    result['decision_sha256']=_digest(result['decision'])
    retained=_retain(destination(state,'strategy-decisions'),result)
    next_bundle=rotation.advance(**arguments,current=base,result=result)
    assert next_bundle!=base and rotation.load(**arguments)==next_bundle
    assert len(calls)==1 and json.loads(retained.read_bytes())==result
    assert (root/capacity.MARKER).read_bytes()==(state/capacity.MARKER).read_bytes()
