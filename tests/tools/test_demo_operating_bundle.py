"""Installed materials are imported exactly; provisioning never invents approval."""
from copy import deepcopy
import base64
import hashlib
import json

import pytest

from tools import demo_operating_bundle as tool
from live.execution.demo_batch_risk import _digest
from tests.live.test_demo_request_inputs import NOW
from tests.tools.test_demo_task import material


def bundle(tmp_path,monkeypatch):
    from tests.live.test_demo_issuer import fixture
    # Reuse the original issuer/strategy/kill fixtures, only in isolated tests.
    private,values,opened,_,_,registry,policy,log=fixture(tmp_path,monkeypatch)
    from tests.live.test_demo_request_inputs import profile
    from tests.tools.test_binance_demo_market_inputs import capture
    market,_=capture();p=profile(values,market)
    kwargs={'issuer_policy':policy,'profile':p,'orders':values['orders'],'kill_state':values['risk_inputs']['kill_switch'].state}
    task=material(kwargs)
    documents={'registry.json':registry,'policy.json':policy,'profile.json':p,
        'signing-key.json':{'schema':'DEMO_ISSUER_KEY_V1','authority_id':policy['authority_id'],'key_id':policy['key_id'],
            'private_key_base64url':base64.urlsafe_b64encode(private.private_bytes_raw()).rstrip(b'=').decode()}}
    raw={name:json.dumps(v,sort_keys=True,separators=(',',':')).encode() for name,v in documents.items()}
    raw['kill-switch.sqlite3']=(tmp_path/'kill-switch.sqlite3').read_bytes()
    raw[_digest(task)+'.json']=json.dumps(task,sort_keys=True,separators=(',',':')).encode()
    previous=deepcopy(registry);previous['keys']=[]
    manifest={'schema':'DEMO_OPERATING_BUNDLE_V1','source_commit':'c'*40,'expected_registry_sha256':tool.registry_sha256(previous),
        'files':{name:hashlib.sha256(body).hexdigest() for name,body in raw.items()},'tasks':[_digest(task)]}
    return manifest,raw,previous,values['origin']['account_ref_sha256'],opened,log


@pytest.mark.parametrize('change',[None,'old_registry','roles','source','account','key','profile','task_hash','kill','extra'])
def test_bundle_checks_all_supplied_bindings_before_installation(tmp_path,monkeypatch,change):
    manifest,raw,registry,account,opened,log=bundle(tmp_path,monkeypatch)
    if change=='old_registry':registry['deployment_sha256']='f'*64
    elif change=='roles':
        value=json.loads(raw['registry.json']);value['executor_uid']=2003;raw['registry.json']=json.dumps(value).encode()
    elif change=='source':manifest['source_commit']='d'*40
    elif change=='account':account='f'*64
    elif change=='key':
        value=json.loads(raw['signing-key.json']);value['private_key_base64url']=base64.urlsafe_b64encode(b'1'*32).rstrip(b'=').decode();raw['signing-key.json']=json.dumps(value).encode()
    elif change=='profile':
        value=json.loads(raw['profile.json']);value['mandate']['approved_by']=['AUTO'];raw['profile.json']=json.dumps(value).encode()
    elif change=='task_hash':manifest['tasks']=['f'*64]
    elif change=='kill':raw['kill-switch.sqlite3']=b'not sqlite'
    elif change=='extra':raw['network-override.json']=b'{}'
    # Coherent manifest tampering still cannot alter original policy/account/key.
    manifest['files']={name:hashlib.sha256(body).hexdigest() for name,body in raw.items()}
    before=deepcopy(raw)
    try:
        args=dict(manifest=manifest,files=raw,existing_registry=registry,source_commit='c'*40,account_ref=account,now_ms=NOW+10)
        if change:
            with pytest.raises(ValueError):tool.validate_materials(**args)
        else:
            result=tool.validate_materials(**args)
            assert result['policy']==json.loads(raw['policy.json']) and len(result['tasks'])==1
            assert result['kill_state'].model_dump(mode='json')==result['tasks'][0]['kill_switch']
            assert result['profile']['mandate']['approved_by']==['isolated-test-operator']
        assert raw==before and opened[0].snapshot()==[] and opened[2].orders()==[] and log.snapshot()==[]
    finally:
        log.close()
        for store in opened:store.close()


def test_missing_source_acceptance_prevents_material_read_and_writes(monkeypatch,capsys):
    monkeypatch.setattr(tool,'verify_source',lambda _:(_ for _ in ()).throw(ValueError('unaccepted source')))
    monkeypatch.setattr(tool,'read_materials',lambda *a,**k:pytest.fail('private materials read'))
    monkeypatch.setattr(tool,'install',lambda *a,**k:pytest.fail('installation reached'))
    assert tool.main(['--source-commit','c'*40,'--bundle-dir','/not/read','--manifest-sha256','a'*64,'--apply'])==3
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='NOT_INSTALLED' and result['phase']=='SOURCE'


def test_combined_tasks_cannot_overrun_the_total_issuer_notional_budget(tmp_path,monkeypatch):
    manifest,raw,registry,account,opened,log=bundle(tmp_path,monkeypatch)
    policy=json.loads(raw['policy.json']);policy['max_total_notional']='150';raw['policy.json']=tool.canonical(policy)
    first=json.loads(raw.pop(manifest['tasks'][0]+'.json'));first['issuer_policy_sha256']=_digest(policy)
    second=deepcopy(first);second['orders'][0]['newClientOrderId']='QDtest000002'
    manifest['tasks']=[_digest(first),_digest(second)]
    for task in (first,second):raw[_digest(task)+'.json']=tool.canonical(task)
    manifest['files']={name:tool.sha(body) for name,body in raw.items()}
    try:
        with pytest.raises(ValueError,match='OPERATING_TASK_NOTIONAL_BUDGET'):
            tool.validate_materials(manifest=manifest,files=raw,existing_registry=registry,
                source_commit='c'*40,account_ref=account,now_ms=NOW+10)
        assert log.snapshot()==[] and opened[0].snapshot()==[]
    finally:
        log.close()
        for store in opened:store.close()


@pytest.mark.parametrize('mode,uid,gid,nlink,size',[(0o100644,0,0,1,2),(0o100600,1000,0,1,2),
    (0o100640,0,1000,1,2),(0o120600,0,0,1,2),(0o100600,0,0,2,2),(0o100600,0,0,1,0)])
def test_private_material_custody_is_checked_before_bytes_are_consumed(mode,uid,gid,nlink,size):
    from types import SimpleNamespace
    with pytest.raises(ValueError):tool.private_metadata(SimpleNamespace(st_mode=mode,st_uid=uid,st_gid=gid,st_nlink=nlink,st_size=size),maximum=65536)


def filesystem(tmp_path,monkeypatch):
    """Real isolated files/SQLite; ownership syscalls recorded without root."""
    from pathlib import Path
    from types import SimpleNamespace
    manifest,raw,registry,account,opened,log=bundle(tmp_path,monkeypatch)
    reviewed=tool.validate_materials(manifest=manifest,files=raw,existing_registry=registry,
        source_commit='c'*40,account_ref=account,now_ms=NOW+10)
    log.close()
    for store in opened:store.close()
    base=tmp_path/'installation';base.mkdir()
    for name in ('EXECUTOR','ISSUER','CONFIG','CONTROLLER','REGISTRY'):
        monkeypatch.setattr(tool,name,base/(name.lower()+('.json' if name=='REGISTRY' else '')))
    tool.EXECUTOR.mkdir(mode=0o700);preimage=tool.canonical(registry);tool.REGISTRY.write_bytes(preimage)
    ownership={tool.EXECUTOR:(registry['executor_uid'],registry['executor_uid'])}
    original=Path.lstat
    def info(path,*a,**kw):
        value=original(path,*a,**kw)
        if path not in ownership:return value
        copied={k:getattr(value,k) for k in dir(value) if k.startswith('st_')}
        copied.update(st_uid=ownership[path][0],st_gid=ownership[path][1]);return SimpleNamespace(**copied)
    monkeypatch.setattr(Path,'lstat',info)
    monkeypatch.setattr(tool.os,'chown',lambda path,uid,gid:ownership.update({Path(path):(uid,gid)}))
    def fchown(fd,uid,gid):ownership[Path('/proc/self/fd/'+str(fd)).resolve()]=(uid,gid)
    monkeypatch.setattr(tool.os,'fchown',fchown)
    monkeypatch.setattr(tool,'_require_protected',lambda *a,**kw:None)
    monkeypatch.setattr(tool,'_read_root_owned_json',lambda path:path.read_bytes())
    return reviewed,raw,preimage,ownership


def test_install_is_usable_by_issuer_and_preserves_exact_original_stop_history(tmp_path,monkeypatch):
    from contextlib import closing
    reviewed,raw,preimage,ownership=filesystem(tmp_path,monkeypatch)
    original_replace=tool.os.replace
    def publish(source,target):
        assert target==tool.REGISTRY and target.read_bytes()==preimage
        assert (tool.EXECUTOR/'kill-switch.sqlite3').read_bytes()==raw['kill-switch.sqlite3']
        assert len(list((tool.CONTROLLER/'tasks').iterdir()))==1
        with closing(tool.IssuerStore(tool.ISSUER/'issuance.sqlite3',read_only=True)) as store:assert store.snapshot()==[]
        with closing(tool.DemoCancelStore(tool.EXECUTOR/'cancellations.sqlite3',read_only=True)) as store:assert store.snapshot()==[]
        original_replace(source,target)
    monkeypatch.setattr(tool.os,'replace',publish)
    tool.install(reviewed,raw,registry_preimage=preimage)
    assert tool.CONFIG.stat().st_mode&0o777==0o750
    assert ownership[tool.CONFIG]==(0,reviewed['registry']['issuer_uid'])
    assert (tool.CONFIG/'signing-key.json').stat().st_mode&0o777==0o640
    assert tool.REGISTRY.read_bytes()==tool.canonical(reviewed['registry'])
    assert not list(tool.ISSUER.glob('*.sqlite3-*'))
    assert not list(tool.EXECUTOR.glob('*.sqlite3-*'))
    before={p:p.read_bytes() for root in (tool.CONFIG,tool.ISSUER,tool.EXECUTOR,tool.CONTROLLER) for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='ALREADY_OR_PARTIALLY_INSTALLED'):tool.install(reviewed,raw,registry_preimage=preimage)
    assert all(path.read_bytes()==body for path,body in before.items())


@pytest.mark.parametrize('failure',['policy.json','kill-switch.sqlite3','task'])
def test_interrupted_install_retains_partial_files_without_activating_registry(tmp_path,monkeypatch,failure):
    reviewed,raw,preimage,_=filesystem(tmp_path,monkeypatch)
    original=tool.write_new
    def disk_failure(path,*a,**kw):
        if path.name==failure or failure=='task' and path.parent.name=='tasks':raise OSError('isolated disk failure')
        return original(path,*a,**kw)
    monkeypatch.setattr(tool,'write_new',disk_failure)
    with pytest.raises(OSError):tool.install(reviewed,raw,registry_preimage=preimage)
    assert tool.REGISTRY.read_bytes()==preimage and (tool.CONFIG/'signing-key.json').exists()
    before=(tool.CONFIG/'signing-key.json').read_bytes()
    with pytest.raises(ValueError,match='ALREADY_OR_PARTIALLY_INSTALLED'):tool.install(reviewed,raw,registry_preimage=preimage)
    assert (tool.CONFIG/'signing-key.json').read_bytes()==before


@pytest.mark.parametrize('name',['kill-switch.sqlite3','cancellations.sqlite3'])
def test_existing_executor_history_is_never_overwritten(tmp_path,monkeypatch,name):
    reviewed,raw,preimage,_=filesystem(tmp_path,monkeypatch)
    old=tool.EXECUTOR/name;old.write_bytes(b'existing historical state')
    with pytest.raises(ValueError,match='ALREADY_OR_PARTIALLY_INSTALLED'):tool.install(reviewed,raw,registry_preimage=preimage)
    assert old.read_bytes()==b'existing historical state' and tool.REGISTRY.read_bytes()==preimage
    assert not tool.CONFIG.exists()


def test_account_read_runs_only_as_executor_with_clean_environment(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('BINANCE_API_SECRET','must-not-inherit')
    calls=[]
    def run(argv,**kwargs):calls.append((argv,kwargs));return SimpleNamespace(returncode=0,stdout=b'a'*64+b'\n')
    monkeypatch.setattr(tool.subprocess,'run',run)
    assert tool.read_account({'executor_uid':2001},'c'*40)=='a'*64
    argv,kwargs=calls[0]
    assert argv[-3:]==['--account-only','--source-commit','c'*40]
    assert kwargs['user']==kwargs['group']==2001 and kwargs['extra_groups']==[]
    assert 'BINANCE_API_SECRET' not in kwargs['env'] and kwargs['timeout']==20


@pytest.mark.parametrize('returncode,body',[(3,b'a'*64+b'\n'),(0,b'sensitive failure text'),(0,b'a'*64+b'\nextra')])
def test_failed_account_child_does_not_echo_output_or_reach_install(monkeypatch,returncode,body):
    from types import SimpleNamespace
    monkeypatch.setattr(tool.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=returncode,stdout=body))
    with pytest.raises(ValueError,match='OPERATING_ACCOUNT_UNAVAILABLE') as error:tool.read_account({'executor_uid':2001},'c'*40)
    assert 'sensitive' not in str(error.value)
