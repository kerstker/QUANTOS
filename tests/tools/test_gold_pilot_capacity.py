"""Original pilot baseline bounds all three observation stores for one day.

Protected roots, account and completed V1 enrollment are isolated fixtures. The
production rotation/history/FreshEpisode paths remain native; no network/order.
"""
from copy import deepcopy
import hashlib
import json
import os
import shutil
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_bar_rotation import bundle, outcome
from tests.tools.test_gold_bar_opening_origin import opening_case as opening_case


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True)+'\n')
    path.chmod(0o600)


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    from tools import demo_operating_bundle as installer, demo_supervisor as supervisor
    from tools import gold_strategy_controller as controller
    executor=tmp_path/'executor';executor.mkdir(mode=0o700)
    state=tmp_path/'controller';state.mkdir(mode=0o700)
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit='c'*40,
        account_ref_sha256='d'*64,duration_seconds=86400,max_loss_fraction='0.05',submission_enabled=False)
    config=dict(schema='GOLD_DEMO_PILOT_CONTROL_V1',environment='BINANCE_USDM_DEMO',symbol='XAUUSDT',
        account_ref_sha256='d'*64,opening_origin_sha256='f'*64,initial_equity='5000',
        started_at_ms=1_000_000,ends_at_ms=87_400_000,max_loss_fraction='0.05',submission_enabled=False)
    area=executor/'gold-pilot';area.mkdir(mode=0o700)
    write(executor/'gold-pilot-request.json',request)
    write(area/'configuration.json',config)
    write(area/'enrollment.json',dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_RECEIPT_V1',
        request_sha256=_digest(request),configuration_sha256=_digest(config),
        opening_origin_sha256=config['opening_origin_sha256'],execution_state_sha256='a'*64,submission_enabled=False))
    monkeypatch.setattr(installer,'EXECUTOR',executor)
    monkeypatch.setattr(controller,'observation_configuration',lambda _:
        ({'executor_uid':os.getuid()},{'account_ref_sha256':'d'*64}))
    monkeypatch.setattr(supervisor,'_root_private_directory',lambda p:
        p.relative_to(tmp_path))
    monkeypatch.setattr(supervisor,'_read_root_owned_json',lambda p:p.read_bytes())
    return dict(root=executor,state=state,request=request,config=config,base=bundle(),uid=os.getuid())


def args(pilot):
    return dict(root=pilot['state'],base=pilot['base'],account='d'*64)


def decision(pilot, current, ordinal=0):
    result=outcome(current)
    result['decision']['pilot_control']=dict(status='ACTIVE',configuration_sha256=_digest(pilot['config']),submission_enabled=False)
    result['decision']['observed_at_ms']=pilot['config']['started_at_ms']+ordinal*900_000
    result['decision_sha256']=_digest(result['decision'])
    return result


def test_original_rotation_can_cover_96_closes_and_reopens_without_reset(pilot):
    from tools import gold_bar_rotation as rotation
    current=rotation.load(**args(pilot))
    for index in range(96):
        current=rotation.advance(**args(pilot),current=current,result=decision(pilot,current,index))
        assert rotation.load(**args(pilot),read_only=True)==current
    assert current['episode_id']!=pilot['base']['episode_id']
    assert len(list(pilot['state'].rglob('*.json')))>=96


def history_capture(monkeypatch):
    from tools import gold_bar_auto_history as history
    from tests.research.test_gold_bar_dataset import raw_rows, STEP
    raw=json.dumps(raw_rows()).encode();digest=hashlib.sha256(raw).hexdigest();calls=[]
    def capture(path,*,protected_network):
        assert protected_network is True;calls.append(path);path.mkdir(mode=0o700)
        write(path/'receipt.json',dict(response_sha256=digest,wire_sha256=digest,
            requested_at_ms=1000*STEP+100,received_at_ms=1000*STEP+200,
            content_encoding='identity',network_mode='PROTECTED_DEMO_NETWORK_V1'))
        write(path/'summary.json',dict(schema='GOLD_BAR_HISTORY_CAPTURE_V1',status='CAPTURED_FOR_RESEARCH',
            source_sha256=digest,count=1000,submission_enabled=False))
        for name in ('response.json','response.wire'):
            (path/name).write_bytes(raw);(path/name).chmod(0o600)
    monkeypatch.setattr(history,'capture',capture)
    return calls


def test_history_budget_covers_same_96_episode_pilot(pilot,monkeypatch):
    from tools import gold_bar_auto_history as history
    calls=history_capture(monkeypatch)
    for index in range(96):
        current=pilot['base'] if index==0 else dict(pilot['base'],episode_id=_digest({'ordinal':index}))
        path=history.load(pilot['root'],current,uid=pilot['uid'])
        assert history.load(pilot['root'],current,uid=pilot['uid'],read_only=True)==path
    assert len(calls)==96


def test_changed_baseline_is_not_a_new_capacity_namespace(pilot):
    from tools import gold_bar_rotation as rotation
    current=rotation.advance(**args(pilot),current=pilot['base'],result=decision(pilot,pilot['base']))
    area=pilot['root']/'gold-pilot'
    config=deepcopy(pilot['config']);config['initial_equity']='6000'
    write(area/'configuration.json',config)
    receipt=json.loads((area/'enrollment.json').read_bytes());receipt['configuration_sha256']=_digest(config)
    write(area/'enrollment.json',receipt)
    before={p:p.read_bytes() for p in pilot['state'].rglob('*') if p.is_file()}
    with pytest.raises(ValueError):rotation.load(**args(pilot))
    assert before=={p:p.read_bytes() for p in pilot['state'].rglob('*') if p.is_file()}
    assert current!=pilot['base']


def test_rotation_rejects_different_pilot_control(pilot):
    from tools import gold_bar_rotation as rotation
    result=decision(pilot,pilot['base']);result['decision']['pilot_control']['configuration_sha256']='e'*64
    result['decision_sha256']=_digest(result['decision'])
    with pytest.raises(ValueError):rotation.advance(**args(pilot),current=pilot['base'],result=result)


def test_partial_original_enrollment_never_expands_capacity(pilot):
    from tools import gold_bar_rotation as rotation
    (pilot['root']/'gold-pilot'/'enrollment.json').unlink()
    with pytest.raises((ValueError,FileNotFoundError)):rotation.load(**args(pilot))
    assert list(pilot['state'].iterdir())==[]


def test_legacy_active_episode_is_not_skipped_by_new_namespace(pilot,monkeypatch):
    from tools import gold_bar_rotation as rotation, demo_operating_bundle as installer
    empty=pilot['root'].parent/'no-pilot';empty.mkdir(mode=0o700)
    monkeypatch.setattr(installer,'EXECUTOR',empty)
    current=rotation.advance(**args(pilot),current=pilot['base'],result=outcome(pilot['base']))
    monkeypatch.setattr(installer,'EXECUTOR',pilot['root'])
    before={p:p.read_bytes() for p in pilot['state'].rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='LEGACY'):rotation.load(**args(pilot))
    assert before=={p:p.read_bytes() for p in pilot['state'].rglob('*') if p.is_file()}
    assert current!=pilot['base']


def test_fresh_episode_capture_and_restart_cover_96_closes(opening_case,monkeypatch):
    from live.execution.demo_gold_pilot_enrollment import enroll_requested
    from live.execution.demo_pre_submit import _state
    from tools import gold_bar_fresh_inputs as fresh, gold_bar_cash
    from tests.tools.test_gold_bar_worker_inputs import setup
    from tests.tools.test_gold_strategy_entry import origin_at
    # Reuse real native stores and the complete synthetic account-origin
    # contract. Only account transport/host identity and the old long cash
    # window are isolated; account openings and their shared budget run natively.
    f=opening_case;root=f.root;started=f.clock[0]
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit='c'*40,
        account_ref_sha256=f.account,duration_seconds=86400,max_loss_fraction='0.05',submission_enabled=False)
    write(root/'gold-pilot-request.json',request)
    stores=dict(zip(('dispatch','recovery','oms'),f.native))
    state=_state(**stores)
    enroll_requested(root=root,source_commit='c'*40,origin=origin_at(started),now_ms=started,**stores)
    original={p:p.read_bytes() for p in (root/'gold-pilot').rglob('*') if p.is_file()}
    config=json.loads((root/'gold-pilot/configuration.json').read_bytes())
    assert config['started_at_ms']==started and config['ends_at_ms']==started+86400000
    _,retained,template,_,_,_=setup(root,monkeypatch)
    current=dict(bundle(),trial_manifest_sha256=retained['trial_manifest_sha256'],
        archive_source_commit=retained['archive_source_commit'])
    definition=dict(definition_hash='d'*64,archive_files={'manifest.json':current['trial_manifest_sha256']},
        source={'commit':current['archive_source_commit']})
    monkeypatch.setattr(fresh,'PreparedBarDefinition',lambda *a,**k:
        SimpleNamespace(definition=lambda *a,**k:definition))
    monkeypatch.setattr(fresh,'restore_engine',lambda *a,**k:object())
    calls=history_capture(monkeypatch);observations=[];cash=[]
    monkeypatch.setattr(gold_bar_cash,'open_window',lambda *a,**k:cash.append(k['observation_exists']))
    async def capture(*a,**k):
        output=k['output'];observations.append(output);shutil.copytree(template,output)
        write(output/'registration.json',dict(first_close=True,network_mode='PROTECTED_DEMO_NETWORK_V1',seconds=30))
        write(output/'selected-decisions.jsonl',{'received_at_ms':f.clock[0]})
        for source,name in (('receipt.json','warmup-receipt.json'),('response.json','warmup.json')):
            (output/'journal'/name).write_bytes((k['history']/source).read_bytes())
    monkeypatch.setattr(fresh,'observe',capture)
    for index in range(96):
        f.clock[0]=started+index*900000
        if index:current=dict(current,episode_id=_digest({'ordinal':index}))
        episode=fresh.FreshEpisode(root,current,source_commit='c'*40,uid=os.getuid(),cash_account=f.account)
        opening=deepcopy(episode.account_opening)
        assert opening['origin']['completed_at_ms']+5000<episode.entry_decision_ms<config['ends_at_ms']
        assert opening['state_sha256']==_digest(state)
        assert opening['pilot_request_sha256']==_digest(request)
        assert opening['pilot_configuration_sha256']==_digest(config)
        checked=fresh.FreshEpisode(root,current,source_commit='c'*40,uid=os.getuid(),read_only=True)
        assert episode.recheck()==checked.recheck()
        assert gold_bar_cash.load_account_opening(root,bundle=current,account=f.account,
            source_commit='c'*40)==opening and len(f.calls)==index+1
    assert len(calls)==len(observations)==96 and cash==[False]*96
    assert len(list((root/'strategy-cash-work').glob('*/account-opening/record.json')))==96
    assert _state(**stores)==state
    assert original=={p:p.read_bytes() for p in (root/'gold-pilot').rglob('*') if p.is_file()}


def test_capacity_contract_is_finite_and_same_for_all_three_stores(pilot,monkeypatch):
    from tools import gold_bar_pilot_capacity as capacity, gold_bar_auto_history as history, gold_bar_rotation as rotation
    history_capture(monkeypatch)
    history.load(pilot['root'],pilot['base'],uid=pilot['uid'])
    rotation.load(**args(pilot))
    executor=json.loads((pilot['root']/capacity.MARKER).read_bytes())
    controller=json.loads((pilot['state']/capacity.MARKER).read_bytes())
    assert executor==controller
    assert executor['max_episodes']==98 and executor['max_rotations']==97
    assert executor['history_total_bytes']==98*executor['history_episode_bytes']
    assert executor['observation_total_bytes']==98*executor['observation_episode_bytes']
    assert executor['request_sha256']==_digest(pilot['request'])
    assert executor['configuration_sha256']==_digest(pilot['config'])
    assert executor['ends_at_ms']==pilot['config']['ends_at_ms']
    assert executor['submission_enabled'] is executor['economic_authority'] is False


@pytest.mark.parametrize('damage',['marker_missing','directory_missing','marker_changed','request_missing','null_intent'])
def test_missing_or_changed_capacity_never_reinitializes_pilot(pilot,damage):
    from tools import gold_bar_rotation as rotation, gold_bar_pilot_capacity as capacity
    rotation.advance(**args(pilot),current=pilot['base'],result=decision(pilot,pilot['base']))
    marker=pilot['state']/capacity.MARKER
    if damage=='marker_missing':marker.unlink()
    elif damage=='directory_missing':
        (pilot['state']/capacity.PARENT/_digest(pilot['request'])/'history').rmdir()
    elif damage=='marker_changed':
        value=json.loads(marker.read_bytes());value['max_episodes']+=1;write(marker,value)
    elif damage=='request_missing':(pilot['root']/'gold-pilot-request.json').unlink()
    else:write(pilot['root']/'gold-pilot'/'enrollment-intent.json',None)
    before={p:p.read_bytes() for p in pilot['state'].rglob('*') if p.is_file()}
    with pytest.raises((ValueError,FileNotFoundError)):rotation.load(**args(pilot))
    assert before=={p:p.read_bytes() for p in pilot['state'].rglob('*') if p.is_file()}


def test_pending_first_baseline_lookup_does_not_create_capacity(pilot):
    from tools import gold_bar_rotation as rotation
    shutil.rmtree(pilot['root']/'gold-pilot')
    assert rotation.load(**args(pilot))==pilot['base']
    assert list(pilot['state'].iterdir())==[]
    with pytest.raises(ValueError,match='ENROLLMENT_REQUIRED'):
        rotation.advance(**args(pilot),current=pilot['base'],result=decision(pilot,pilot['base']))
    assert list(pilot['state'].iterdir())==[]


def test_controller_capacity_never_uses_executor_identity_api(pilot,monkeypatch):
    from tools import gold_bar_rotation as rotation
    from live.execution import demo_authority
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:
        pytest.fail('controller used executor identity'))
    assert rotation.load(**args(pilot))==pilot['base']


def test_finite_rotation_limit_and_original_end_do_not_extend(pilot):
    from tools import gold_bar_rotation as rotation
    current=pilot['base']
    for _ in range(97):current=rotation.advance(**args(pilot),current=current,result=decision(pilot,current))
    before={p:p.read_bytes() for p in pilot['state'].rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='STORAGE_BUDGET'):
        rotation.advance(**args(pilot),current=current,result=decision(pilot,current))
    assert before=={p:p.read_bytes() for p in pilot['state'].rglob('*') if p.is_file()}
    result=decision(pilot,current);result['decision']['observed_at_ms']=pilot['config']['ends_at_ms']
    result['decision_sha256']=_digest(result['decision'])
    with pytest.raises(ValueError,match='RESULT_WINDOW'):
        rotation.advance(**args(pilot),current=current,result=result)


def test_history_limit_does_not_capture_99th_episode(pilot,monkeypatch):
    from tools import gold_bar_pilot_capacity as capacity, gold_bar_auto_history as history
    calls=history_capture(monkeypatch)
    history.load(pilot['root'],pilot['base'],uid=pilot['uid'])
    area=pilot['root']/capacity.PARENT/_digest(pilot['request'])/'history'
    saved=json.loads((pilot['root']/capacity.MARKER).read_bytes())
    for index in range(97):
        current=dict(pilot['base'],episode_id=_digest({'retained':index}))
        capacity.allocate_capture(area,saved,'history',pilot['uid'],current)
        (area/_digest(current)).mkdir(mode=0o700)
    before={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='EPISODE_BUDGET'):
        history.load(pilot['root'],dict(pilot['base'],episode_id='e'*64),uid=pilot['uid'])
    assert len(calls)==1 and before=={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}


def test_capture_growth_over_budget_is_retained_and_rejected(pilot,monkeypatch):
    from tools import gold_bar_pilot_capacity as capacity, gold_bar_auto_history as history
    calls=history_capture(monkeypatch);native=history.capture
    def oversized(path,**kwargs):
        native(path,**kwargs)
        with (path/'oversized.bin').open('wb') as file:file.truncate(capacity.HISTORY_EPISODE_BYTES)
        (path/'oversized.bin').chmod(0o600)
    monkeypatch.setattr(history,'capture',oversized)
    with pytest.raises(ValueError,match='EPISODE_BYTES'):
        history.load(pilot['root'],pilot['base'],uid=pilot['uid'])
    assert len(calls)==1 and (calls[0]/'oversized.bin').exists()
    with pytest.raises(ValueError,match='EPISODE_BYTES'):
        history.load(pilot['root'],pilot['base'],uid=pilot['uid'])
    assert len(calls)==1


def test_readonly_initial_lookup_creates_no_namespace(pilot,monkeypatch):
    from tools import gold_bar_auto_history as history, gold_bar_rotation as rotation
    monkeypatch.setattr(history,'capture',lambda *a,**k:pytest.fail('read-only captured'))
    before={p:p.read_bytes() for p in pilot['root'].rglob('*') if p.is_file()}
    assert rotation.load(**args(pilot),read_only=True)==pilot['base']
    with pytest.raises(FileNotFoundError):history.load(pilot['root'],pilot['base'],uid=pilot['uid'],read_only=True)
    assert before=={p:p.read_bytes() for p in pilot['root'].rglob('*') if p.is_file()}
    assert list(pilot['state'].iterdir())==[]


def test_missing_captured_history_is_not_recaptured(pilot,monkeypatch):
    from tools import gold_bar_auto_history as history
    calls=history_capture(monkeypatch)
    path=history.load(pilot['root'],pilot['base'],uid=pilot['uid'])
    shutil.rmtree(path)
    with pytest.raises(ValueError,match='HISTORY|ALLOCATION'):
        history.load(pilot['root'],pilot['base'],uid=pilot['uid'])
    assert len(calls)==1


def test_lost_complete_rotation_chain_cannot_restart_original_episode(pilot):
    from tools import gold_bar_rotation as rotation, gold_bar_pilot_capacity as capacity
    current=rotation.advance(**args(pilot),current=pilot['base'],result=decision(pilot,pilot['base']))
    area=pilot['state']/capacity.PARENT/_digest(pilot['request'])/'rotations'
    for path in area.iterdir():path.unlink()
    with pytest.raises(ValueError,match='HISTORY|ALLOCATION'):
        rotation.load(**args(pilot))
    assert current!=pilot['base']


@pytest.mark.parametrize('damage',['namespace_and_marker','payload_and_allocation'])
def test_separate_allocation_audit_detects_paired_storage_loss(pilot,monkeypatch,damage):
    from tools import gold_bar_auto_history as history, gold_bar_pilot_capacity as capacity
    calls=history_capture(monkeypatch)
    path=history.load(pilot['root'],pilot['base'],uid=pilot['uid'])
    if damage=='namespace_and_marker':
        shutil.rmtree(pilot['root']/capacity.PARENT)
        (pilot['root']/capacity.MARKER).unlink()
    else:
        (path.parent.parent/'history-allocations'/(_digest(pilot['base'])+'.json')).unlink()
        shutil.rmtree(path)
    before={p:p.read_bytes() for p in (pilot['root']/capacity.AUDIT).rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='MISSING'):
        history.load(pilot['root'],pilot['base'],uid=pilot['uid'])
    assert len(calls)==1 and before=={p:p.read_bytes() for p in (pilot['root']/capacity.AUDIT).rglob('*') if p.is_file()}


def test_missing_executor_pilot_never_downgrades_controller_capacity(pilot):
    from tools import gold_bar_rotation as rotation
    rotation.advance(**args(pilot),current=pilot['base'],result=decision(pilot,pilot['base']))
    shutil.rmtree(pilot['root']/'gold-pilot');(pilot['root']/'gold-pilot-request.json').unlink()
    before={p:p.read_bytes() for p in pilot['state'].rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='REQUEST_MISSING'):rotation.load(**args(pilot))
    assert before=={p:p.read_bytes() for p in pilot['state'].rglob('*') if p.is_file()}
    assert not (pilot['state']/'strategy-bar-rotation').exists()


def test_pending_pilot_request_cannot_hide_old_active_rotation(pilot,monkeypatch):
    from tools import gold_bar_rotation as rotation, demo_operating_bundle as installer
    empty=pilot['root'].parent/'before-pilot';empty.mkdir(mode=0o700)
    monkeypatch.setattr(installer,'EXECUTOR',empty)
    current=rotation.advance(**args(pilot),current=pilot['base'],result=outcome(pilot['base']))
    monkeypatch.setattr(installer,'EXECUTOR',pilot['root']);shutil.rmtree(pilot['root']/'gold-pilot')
    with pytest.raises(ValueError,match='LEGACY'):rotation.load(**args(pilot))
    assert current!=pilot['base']


def test_changed_original_base_episode_cannot_use_an_empty_existing_namespace(pilot):
    from tools import gold_bar_rotation as rotation
    rotation.load(**args(pilot))
    changed=dict(pilot['base'],episode_id='9'*64)
    with pytest.raises(ValueError,match='BASE_RUNTIME_CHANGED'):
        rotation.load(**(args(pilot)|{'base':changed}))
