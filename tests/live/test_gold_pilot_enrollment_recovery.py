"""A durable original pilot intent can resume missing writes without resetting."""
from copy import deepcopy
import json

import pytest

from live.execution import demo_gold_pilot as pilot, demo_gold_pilot_enrollment as enrollment
from live.execution import demo_position_context as position
from live.execution.demo_batch_risk import _digest
from tests.live.test_demo_pre_submit import NOW, stores
from tests.live.test_gold_demo_preparation import setup
from tests.live.test_demo_market_partition import book as book, NOW as PARTITION_NOW
from tests.live.test_demo_market_partition_runtime import installed as installed


@pytest.fixture
def local(tmp_path,monkeypatch):
    _, _, values=setup(tmp_path,monkeypatch)
    dispatch,recovery,oms=stores(tmp_path)
    monkeypatch.setattr(position,'_ROOT',tmp_path)
    monkeypatch.setattr(position,'_custody',lambda *a,**k:None)
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit='c'*40,
        account_ref_sha256=values['origin']['account_ref_sha256'],duration_seconds=86400,
        max_loss_fraction='0.05',submission_enabled=False)
    path=tmp_path/'gold-pilot-request.json';path.write_text(json.dumps(request))
    args=dict(root=tmp_path,source_commit='c'*40,origin=values['origin'],dispatch=dispatch,
        recovery=recovery,oms=oms)
    try:yield args,path,values['risk_inputs']['account']
    finally:
        for store in (dispatch,recovery,oms):store.close()


def fresh(origin,delta):
    def move(value):
        if type(value) is int and value>=NOW:return value+delta
        if type(value) is dict:return {key:move(item) for key,item in value.items()}
        if type(value) is list:return [move(item) for item in value]
        return value
    return move(origin)


def interrupted(args,monkeypatch,at,target):
    original=pilot.retain_once
    def fail(path,value,**kwargs):
        if path.name==target:raise OSError('isolated loss before '+target)
        return original(path,value,**kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(pilot,'retain_once',fail)
        with pytest.raises(OSError,match='isolated loss'):
            enrollment.enroll_requested(**args,now_ms=at)


@pytest.mark.parametrize('target',['configuration.json','enrollment.json'])
def test_complete_original_intent_resumes_without_new_wallet_or_start(local,monkeypatch,target):
    args,_,_=local;area=args['root']/'gold-pilot'
    interrupted(args,monkeypatch,NOW,target)
    existing={p.name:p.read_bytes() for p in area.iterdir() if p.is_file()}
    current={**args,'origin':fresh(args['origin'],1000)}
    config=enrollment.enroll_requested(**current,now_ms=NOW+1000)
    assert config['started_at_ms']==NOW and config['ends_at_ms']==NOW+86400000
    assert config['initial_equity']=='5000' and config['opening_origin_sha256']==_digest(args['origin'])
    assert all((area/name).read_bytes()==body for name,body in existing.items())
    receipt=json.loads((area/'enrollment.json').read_bytes())
    assert receipt['schema']=='GOLD_DEMO_PILOT_ENROLLMENT_RECEIPT_V2'
    assert receipt['intent_sha256']==_digest(json.loads((area/'enrollment-intent.json').read_bytes()))
    assert config==enrollment.enroll_requested(**current,now_ms=NOW+1001)
    assert args['dispatch'].snapshot()==[] and args['oms'].orders()==[]


@pytest.mark.parametrize('damage',['request','source','account','stale','future','expired',
    'wallet','state','intent','origin','equity','start','configuration','truncated_intent',
    'legacy_without_intent'])
def test_missing_material_or_changed_scope_remains_partial_and_keeps_original_bytes(local,monkeypatch,damage):
    args,path,_=local;area=args['root']/'gold-pilot'
    interrupted(args,monkeypatch,NOW,'enrollment.json')
    at=NOW+1000;current=deepcopy(args['origin']);kw=dict(args)
    if damage=='request':
        request=json.loads(path.read_bytes());request['duration_seconds']=3600;path.write_text(json.dumps(request))
    elif damage=='source':kw['source_commit']='d'*40
    elif damage=='account':current['account_ref_sha256']='e'*64
    elif damage=='stale':at+=40000
    elif damage=='future':at=NOW-1
    elif damage=='expired':at=NOW+86400000;current=fresh(current,86400000)
    elif damage=='wallet':
        for row in current['reads']:
            if row['kind'].startswith('account'):
                for asset in row['data']['assets']:
                    if asset['asset']=='USDT':
                        for key in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):
                            asset[key]='6000'
    elif damage=='state':
        changed=enrollment._state(args['dispatch'],args['recovery'],args['oms'])
        changed['wire']=[{'isolated_change':True}]
        monkeypatch.setattr(enrollment,'_state',lambda *a:deepcopy(changed))
    elif damage in ('intent','origin','equity','start'):
        p=area/'enrollment-intent.json';value=json.loads(p.read_bytes())
        if damage=='intent':value['source_commit']='d'*40
        elif damage=='origin':value['prepared']['opening_origin']['observed_at_ms']+=1
        else:
            config=value['prepared']['configuration']
            if damage=='equity':config['initial_equity']='6000'
            else:config['started_at_ms']+=1;config['ends_at_ms']+=1
            value['prepared']['configuration_sha256']=_digest(config)
        p.write_text(json.dumps(value))
    elif damage=='configuration':
        p=area/'configuration.json';value=json.loads(p.read_bytes());value['initial_equity']='6000';p.write_text(json.dumps(value))
    elif damage=='truncated_intent':(area/'enrollment-intent.json').write_bytes(b'{')
    else:(area/'enrollment-intent.json').unlink()
    kw['origin']=current
    before={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}
    with pytest.raises((ValueError,FileNotFoundError)):
        enrollment.enroll_requested(**kw,now_ms=at)
    assert before=={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}
    assert not (area/'enrollment.json').exists()


def test_recovered_receipt_is_consumed_by_original_stop_and_keeps_5_percent_floor(local,monkeypatch):
    args,_,account=local
    interrupted(args,monkeypatch,NOW,'enrollment.json')
    config=enrollment.enroll_requested(**args,now_ms=NOW+1)
    assert pilot.observe(account,now_ms=NOW+1,origin_sha256=_digest(args['origin']))['status']=='ACTIVE'
    account=dict(account,equity='4750')
    stopped=pilot.observe(account,now_ms=NOW+2,origin_sha256=_digest(args['origin']))
    assert stopped['reason']=='LOSS_LIMIT' and stopped['observed_equity']=='4750'
    assert stopped==pilot.observe(dict(account,equity='5000'),now_ms=NOW+3,origin_sha256=_digest(args['origin']))
    assert config['initial_equity']=='5000' and config['started_at_ms']==NOW


def test_completed_v2_cannot_bypass_intent_or_receipt_by_removing_the_request(local):
    args,path,account=local
    enrollment.enroll_requested(**args,now_ms=NOW)
    path.unlink()
    with pytest.raises(ValueError):enrollment.enroll_requested(**args,now_ms=NOW+1)
    with pytest.raises(ValueError):pilot.observe(account,now_ms=NOW+1,origin_sha256=_digest(args['origin']))


@pytest.mark.parametrize('damage',['intent','receipt','configuration'])
def test_completed_v2_detects_binding_corruption_before_any_stop_record(local,damage):
    args,_,account=local;area=args['root']/'gold-pilot'
    enrollment.enroll_requested(**args,now_ms=NOW)
    filename={'intent':'enrollment-intent.json','receipt':'enrollment.json','configuration':'configuration.json'}[damage]
    p=area/filename;value=json.loads(p.read_bytes())
    if damage=='intent':value['prepared']['execution_state_sha256']='e'*64
    elif damage=='receipt':value['intent_sha256']='e'*64
    else:value['initial_equity']='6000'
    p.write_text(json.dumps(value));before={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):pilot.observe(account,now_ms=NOW+1,origin_sha256=_digest(args['origin']))
    assert before=={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}


def test_complete_legacy_receipt_remains_readable_without_inventing_an_intent(local):
    args,_,_=local;area=args['root']/'gold-pilot';area.mkdir()
    prepared=enrollment.prepare(origin=args['origin'],dispatch=args['dispatch'],recovery=args['recovery'],
        oms=args['oms'],now_ms=NOW)
    config=prepared['configuration']
    receipt=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_RECEIPT_V1',request_sha256=_digest(json.loads((args['root']/'gold-pilot-request.json').read_bytes())),
        configuration_sha256=_digest(config),opening_origin_sha256=config['opening_origin_sha256'],
        execution_state_sha256=prepared['execution_state_sha256'],submission_enabled=False)
    pilot.retain_once(area/'configuration.json',config);pilot.retain_once(area/'enrollment.json',receipt)
    before={p:p.read_bytes() for p in area.iterdir()}
    assert enrollment.enroll_requested(**args,now_ms=NOW+1)==config
    assert before=={p:p.read_bytes() for p in area.iterdir()}


def test_native_partition_receipts_and_legacy_risk_survive_pilot_resume(installed,monkeypatch):
    value=installed;root=value['stores']['dispatch'].path.parent
    monkeypatch.setattr(position,'_ROOT',root)
    monkeypatch.setattr(position,'_custody',lambda *a,**k:None)
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit='c'*40,
        account_ref_sha256=value['origin']['account_ref_sha256'],duration_seconds=86400,
        max_loss_fraction='0.05',submission_enabled=False)
    (root/'gold-pilot-request.json').write_text(json.dumps(request))
    args=dict(root=root,source_commit='c'*40,origin=value['origin'],
        **{key:value['stores'][key] for key in ('dispatch','recovery','oms')})
    before=deepcopy(value['state']),value['events'].row_count(),deepcopy(value['registry'])
    interrupted(args,monkeypatch,PARTITION_NOW+140000,'enrollment.json')
    config=enrollment.enroll_requested(**{**args,'origin':fresh(args['origin'],1000)},
        now_ms=PARTITION_NOW+141000)
    assert config['initial_equity']=='4200' and config['started_at_ms']==PARTITION_NOW+140000
    assert config['ends_at_ms']==PARTITION_NOW+140000+86400000
    used,old=value['runtime'].installed_context(state=value['state'],symbol='BTCUSDC',
        account_ref=value['origin']['account_ref_sha256'],as_of_ms=PARTITION_NOW+141000)
    assert used and old['canonical_wallet']=='4999.6801' and old['initial_equity']=='5000'
    assert old['capacity_consumed']==value['context']['capacity_consumed']
    assert before==(enrollment._state(args['dispatch'],args['recovery'],args['oms']),
        value['events'].row_count(),value['registry'])


def test_completed_record_can_record_expiry_without_renewing_the_window(local,monkeypatch):
    args,_,account=local
    interrupted(args,monkeypatch,NOW,'enrollment.json')
    original=enrollment.enroll_requested(**args,now_ms=NOW+1)
    assert enrollment.enroll_requested(**args,now_ms=NOW+86400001)==original
    result=pilot.observe(account,now_ms=NOW+86400001,origin_sha256=_digest(args['origin']))
    assert result['reason']=='DURATION_COMPLETE'
    assert original['started_at_ms']==NOW and original['ends_at_ms']==NOW+86400000


@pytest.mark.parametrize('damage',['malformed_request','malformed_review','review_directory'])
def test_partial_recovery_never_hides_invalid_request_or_prior_review(local,monkeypatch,damage):
    args,path,_=local;area=args['root']/'gold-pilot'
    interrupted(args,monkeypatch,NOW,'enrollment.json')
    if damage=='malformed_request':path.write_text('null')
    elif damage=='malformed_review':next((area/'enrollment-reviews').iterdir()).write_bytes(b'{')
    else:(area/'enrollment-reviews'/('e'*64+'.json')).mkdir()
    before={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}
    with pytest.raises((ValueError,FileNotFoundError,IsADirectoryError)):
        enrollment.enroll_requested(**args,now_ms=NOW+1)
    assert before=={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}


@pytest.mark.parametrize('maximum',[True,0,-1,16000001])
def test_record_budget_rejects_invalid_sizes_before_creating_a_file(tmp_path,maximum):
    path=tmp_path/'budget.json'
    with pytest.raises(ValueError):pilot.retain_once(path,{'value':1},maximum=maximum)
    assert not path.exists()


def test_native_restart_releases_original_lease_and_resumes_same_full_book(installed,tmp_path):
    from pathlib import Path
    import subprocess
    import sys
    value=installed;root=value['stores']['dispatch'].path.parent
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit='c'*40,
        account_ref_sha256=value['origin']['account_ref_sha256'],duration_seconds=86400,
        max_loss_fraction='0.05',submission_enabled=False)
    (root/'gold-pilot-request.json').write_text(json.dumps(request))
    cfg=dict(root=str(root),source_root=str(Path(enrollment.__file__).resolve().parents[2]),
        registry=value['registry'],origin=value['origin'],at=PARTITION_NOW+140000,
        executor=str(value['executor']),issuer=str(value['issuer']),activation=str(value['activation']),
        dispatch=str(value['stores']['dispatch'].path),recovery=value['stores']['recovery'].db_path,
        oms=value['stores']['oms']._conn.execute('PRAGMA database_list').fetchone()[2])
    path=tmp_path/'restart-input.json';path.write_text(json.dumps(cfg))
    script=tmp_path/'local-enrollment-worker.py'
    script.write_text('''import sys,json,os
from pathlib import Path
cfg=json.loads(Path(sys.argv[1]).read_text());sys.path.insert(0,cfg['source_root'])
from live.execution import demo_gold_pilot_enrollment as e,demo_gold_pilot as p
from live.execution import demo_position_context as pos,demo_market_partition_runtime as part,demo_authority
from live.execution.demo_dispatch_store import DemoDispatchStore
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
from live.execution.demo_pre_submit import _preparation_lease
root=Path(cfg['root']);pos._ROOT=root;pos._custody=lambda *a,**k:None
# Isolated identity/source/custody replacements; stores, lease and V2 are real.
demo_authority.load_registry_for_executor=lambda:cfg['registry']
part.EXECUTOR_RECORD=Path(cfg['executor']);part.ISSUER_RECORD=Path(cfg['issuer']);part.ACTIVATION=Path(cfg['activation'])
part._read_record=lambda path,**kwargs:path.read_bytes()
part.executing_source_commit=lambda:'c'*40;part._role_identity=lambda *a:None
stores=dict(dispatch=DemoDispatchStore(cfg['dispatch']),recovery=SqliteOrderRecoveryStore(cfg['recovery']),oms=LiveLedger(cfg['oms']))
try:
 with _preparation_lease(stores['dispatch']):
  if sys.argv[2]=='interrupt':
   original=p.retain_once
   def fail(path,value,**kwargs):
    if path.name=='enrollment.json':os._exit(73)
    return original(path,value,**kwargs)
   p.retain_once=fail
  result=e.enroll_requested(root=root,source_commit='c'*40,origin=cfg['origin'],**stores,
   now_ms=cfg['at']+(1 if sys.argv[2]=='resume' else 0))
  print(json.dumps({key:result[key] for key in ('started_at_ms','ends_at_ms','initial_equity')}))
finally:
 for store in stores.values():store.close()
''')
    before=deepcopy(value['state']),value['events'].row_count(),deepcopy(value['registry'])
    first=subprocess.run([sys.executable,str(script),str(path),'interrupt'],capture_output=True,text=True,timeout=30)
    assert first.returncode==73,first.stderr
    area=root/'gold-pilot';original=(area/'configuration.json').read_bytes()
    assert not (area/'enrollment.json').exists()
    second=subprocess.run([sys.executable,str(script),str(path),'resume'],capture_output=True,text=True,timeout=30)
    assert second.returncode==0,second.stderr
    assert json.loads(second.stdout)==dict(started_at_ms=cfg['at'],ends_at_ms=cfg['at']+86400000,initial_equity='4200')
    assert (area/'configuration.json').read_bytes()==original
    assert before==(enrollment._state(value['stores']['dispatch'],value['stores']['recovery'],value['stores']['oms']),
        value['events'].row_count(),value['registry'])


def test_explicit_large_record_budget_keeps_the_default_limit(tmp_path,monkeypatch):
    monkeypatch.setattr(position,'_custody',lambda *a,**k:None)
    path=tmp_path/'large.json';value={'synthetic_padding':'x'*40000}
    with pytest.raises(ValueError):pilot.retain_once(path,value)
    assert not path.exists()
    assert pilot.retain_once(path,value,maximum=65536)==value
    with pytest.raises(ValueError):pilot.read(path)
    assert pilot.read(path,maximum=65536)==value


def test_partial_integer_config_substitution_rejects_without_completing(local,monkeypatch):
    args,_,_=local;area=args['root']/'gold-pilot'
    interrupted(args,monkeypatch,NOW,'enrollment.json')
    path=area/'configuration.json';value=json.loads(path.read_bytes())
    for key in ('started_at_ms','ends_at_ms'):value[key]=float(value[key])
    path.write_text(json.dumps(value))
    before={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):enrollment.enroll_requested(**args,now_ms=NOW+1)
    assert before=={p:p.read_bytes() for p in area.rglob('*') if p.is_file()}


def test_numeric_zero_cannot_replace_false_in_completed_receipt(local):
    args,_,account=local;area=args['root']/'gold-pilot'
    enrollment.enroll_requested(**args,now_ms=NOW)
    path=area/'enrollment.json';value=json.loads(path.read_bytes());value['submission_enabled']=0
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):pilot.observe(account,now_ms=NOW+1,origin_sha256=_digest(args['origin']))
    assert not (area/'binding.json').exists()


def test_missing_receipt_field_is_a_structured_fail_closed_rejection(local):
    args,_,account=local;area=args['root']/'gold-pilot'
    enrollment.enroll_requested(**args,now_ms=NOW)
    path=area/'enrollment.json';value=json.loads(path.read_bytes());value.pop('submission_enabled')
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='ENROLLMENT_BINDING_CHANGED'):
        pilot.observe(account,now_ms=NOW+1,origin_sha256=_digest(args['origin']))
    assert not (area/'binding.json').exists()
