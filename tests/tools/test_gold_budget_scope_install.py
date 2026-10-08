"""Scope activation uses original files/worker/ledger and explicit virtual custody.

Local operator, accepted source and ownership are isolated substitutes. The
archive, native SQLite/V2, fsync writes and partial-activation recovery are real.
No real credentials, orders, systemd calls or deployed state are used.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from live.execution.demo_batch_risk import _digest
from services.research.demo_execution_origin import save
from tests.live.test_demo_market_partition import book as book
from tests.live.test_demo_market_partition_runtime import installed as installed, shift
from tests.live.test_gold_first_enrollment_review import first as first
from tests.tools.test_gold_first_enrollment_materials import materials as materials, assemble
from tests.tools.test_gold_budget_scope_preparation import scope_inputs as scope_inputs
from tests.tools.test_gold_budget_scope_materials import day_materials as day_materials, checked
from tools import demo_successor_setup as successor, demo_operator_setup as setup, demo_operating_bundle as installer
from tools import gold_strategy_controller as controller, gold_strategy_budget_enrollment as preparation


def copy_definition_source(repo, source):
    """Copy the native source-identity inputs, excluding developer state/keys."""
    subprocess.run(['git','clone','--shared','--local','--no-checkout',str(repo),str(source)],
        check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    paths=sorted(p for folder in ('services','quantos','tools') for p in (repo/folder).rglob('*.py'))
    for path in [*paths,repo/'requirements-ci-linux-aarch64.lock']:
        assert not path.is_symlink() and path.is_file()
        target=source/path.relative_to(repo);target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path,target)
    for path in (source,*source.rglob('*')):
        assert not path.is_symlink()
        path.chmod(0o700 if path.is_dir() else 0o600)


@pytest.fixture
def local(day_materials, scope_inputs, materials, tmp_path, monkeypatch):
    from live.execution import demo_authority
    from tools import demo_functional_cycle as channel, demo_functional_worker as worker, demo_supervisor
    value = scope_inputs[0];root = value['stores']['dispatch'].path.parent;uid = os.getuid()
    clock = [day_materials['now_ms']]
    area = tmp_path/'setup';cfg = tmp_path/'config';ctl = tmp_path/'controller'
    for path in (area,cfg,ctl,ctl/'tasks',ctl/'functional-cycle'):path.mkdir(mode=0o700)
    public = tmp_path/'public-registry.json'
    public.write_bytes(setup.canonical(day_materials['registry']))
    monkeypatch.setattr(successor,'SETUP',area)
    for name,path in (('CONFIG',cfg),('CONTROLLER',ctl),('EXECUTOR',root),('REGISTRY',public)):
        monkeypatch.setattr(installer,name,path)
    monkeypatch.setattr(demo_supervisor,'STATE',ctl)
    monkeypatch.setattr(demo_supervisor,'EXECUTOR_REGISTRY',public)
    monkeypatch.setattr(demo_supervisor,'ISSUER_REGISTRY',cfg/'authority.json')
    monkeypatch.setattr(demo_supervisor,'ISSUER_POLICY',cfg/'policy.json')
    monkeypatch.setattr(demo_supervisor,'verify_source',lambda _:None)
    monkeypatch.setattr(demo_supervisor,'_root_private_directory',lambda _:None)
    monkeypatch.setattr(successor,'verify_source',lambda _:None)
    monkeypatch.setattr(installer,'now_ms',lambda:clock[0])
    monkeypatch.setattr(demo_supervisor,'now_ms',lambda:clock[0])
    monkeypatch.setattr(controller.os,'geteuid',lambda:0)
    monkeypatch.setattr(installer,'_require_protected',lambda *a,**kw:None)
    def metadata(info,*,maximum):
        assert stat.S_ISREG(info.st_mode) and info.st_uid==uid and not info.st_mode&0o077
        assert info.st_nlink==1 and 0<info.st_size<=maximum
    monkeypatch.setattr(installer,'private_metadata',metadata)
    monkeypatch.setattr(installer.os,'fchown',lambda *a:None)
    for module in (successor,installer,demo_supervisor):
        monkeypatch.setattr(module,'_read_root_owned_json',lambda path:path.read_bytes())
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:json.loads(public.read_bytes()))
    _, original_files = assemble(materials,registry=value['legacy'])
    for name,body in [('authority.json',setup.canonical(day_materials['registry'])),
        ('policy.json',setup.canonical(day_materials['policy'])),('profile.json',setup.canonical(day_materials['profile'])),
        ('signing-key.json',original_files['signing-key.json'])]:
        (cfg/name).write_bytes(body);(cfg/name).chmod(0o600)
    result = dict(schema='DEMO_OPERATOR_SETUP_RESULT_V1',status='INSTALLED_PENDING_CYCLE',
        spec_sha256=_digest(materials[2]),enrollment_sha256=_digest(materials[3]),source_commit='c'*40,
        task_sha256='b'*64,orders_submitted=False,economic_authority=False)
    for name,doc in [('result.json',result),('spec.json',materials[2])]:
        (area/name).write_bytes(setup.canonical(doc));(area/name).chmod(0o600)
    (root/'kill-switch.sqlite3').write_bytes(day_materials['stop_image']);(root/'kill-switch.sqlite3').chmod(0o600)
    runtime = day_materials['runtime']
    (ctl/'strategy-runtime.json').write_bytes(setup.canonical(runtime));(ctl/'strategy-runtime.json').chmod(0o600)
    trial = root/'strategy-bar-trials'/runtime['trial_manifest_sha256']
    source = root/'strategy-bar-sources'/runtime['archive_source_commit']
    shutil.copytree(value['scope_archive'],trial)
    repo = Path(worker.__file__).resolve().parents[1]
    copy_definition_source(repo,source)
    for tree in (root,trial.parent,source.parent):
        tree.chmod(0o700)
    for tree in (trial,source):
        for path in (tree,*tree.rglob('*')):path.chmod(0o700 if path.is_dir() else 0o600)
    native_load = preparation.load_definition
    monkeypatch.setattr(preparation,'load_definition',lambda *a,**kw:native_load(*a,**(kw|{'uid':uid})))
    calls=[];confirmations=[]
    def capture(*a,**kw):
        assert kw['symbol']=='XAUUSDT'
        origin = shift(day_materials['origin'],clock[0]-day_materials['now_ms'])
        save(root/'execution-origins'/'XAUUSDT',origin)
        return json.loads(public.read_bytes()),origin
    monkeypatch.setattr(setup,'_runtime_inputs',capture)
    monkeypatch.setattr(worker,'_open',lambda stack,path:value['stores'])
    monkeypatch.setattr(worker,'_check_path',lambda *a,**kw:None)
    monkeypatch.setattr(worker,'now_ms',lambda:clock[0])
    class Runtime:
        def __init__(self,source,registry,cycle):calls.append(('open',source,cycle))
        def invoke(self,operation,*,payload,timeout):
            assert operation=='context' and timeout==60
            return worker.review_gold_budget_scope(root,day_materials['policy']['account_ref_sha256'],'c'*40,payload)
        def close(self):calls.append(('close',))
    monkeypatch.setattr(channel,'Runtime',Runtime)
    return dict(area=area,cfg=cfg,ctl=ctl,root=root,public=public,clock=clock,parts=day_materials,
        original=result,calls=calls,confirmations=confirmations,value=value,
        old_files={p.name:p.read_bytes() for p in cfg.iterdir()})


def operator(tool, local, monkeypatch):
    monkeypatch.setattr(tool,'local_operator',lambda:'isolated-operator')
    def confirm(request,cycle,who):
        local['confirmations'].append(deepcopy(request))
        return dict(schema='DEMO_GOLD_BUDGET_SCOPE_OPERATOR_V1',purpose=request['purpose'],
            request_sha256=request['request_sha256'],original_cycle_sha256=cycle,operator_id=who,
            approved_at_ms=local['clock'][0],duration_seconds=86400,installation_allowance_seconds=300,
            execution_approval_granted=False,economic_authority=False)
    monkeypatch.setattr(tool,'operator_confirmation',confirm)


def test_scope_can_be_installed_and_observed_without_becoming_executable(local,monkeypatch,capsys):
    from tools import gold_strategy_budget_install as tool
    from live.execution.demo_issuer_policy import validate_policy
    operator(tool,local,monkeypatch)
    assert tool.main(['--source-commit','c'*40,'--enroll-approved'])==0,capsys.readouterr().out
    stage = successor.active_setup();scope = json.loads((local['cfg']/'policy.json').read_bytes())
    assert len(local['confirmations'])==1 and (stage/'activation.json').exists()
    assert (stage/'previous-files/policy.json').read_bytes()==local['old_files']['policy.json']
    assert list((local['ctl']/'tasks').iterdir())==[]
    assert scope['schema']=='DEMO_GOLD_STRATEGY_BUDGET_SCOPE_V1'
    registry,observed = controller.observation_configuration('c'*40)
    assert observed==scope and registry['keys'][:-1]==local['parts']['registry']['keys']
    with pytest.raises(ValueError):validate_policy(scope)
    with pytest.raises(ValueError):tool.supervisor.configuration('c'*40)
    local['clock'][0] = scope['valid_until_ms']+1
    assert controller.observation_configuration('c'*40)==(registry,scope)


def test_observation_scope_signature_is_checked_after_expiry(day_materials,monkeypatch,tmp_path):
    from tools import demo_supervisor as supervisor
    manifest,files,native = checked(day_materials)
    monkeypatch.setattr(controller.os,'geteuid',lambda:0)
    monkeypatch.setattr(supervisor,'verify_source',lambda _:None)
    monkeypatch.setattr(supervisor,'_root_private_directory',lambda _:None)
    contents={supervisor.EXECUTOR_REGISTRY:files['registry.json'],supervisor.ISSUER_REGISTRY:files['registry.json'],
        supervisor.ISSUER_POLICY:files['policy.json']}
    monkeypatch.setattr(supervisor,'_read_root_owned_json',lambda p:contents[p])
    monkeypatch.setattr(supervisor,'now_ms',lambda:native['policy']['valid_until_ms']+1)
    assert controller.observation_configuration('c'*40)==(native['registry'],native['policy'])
    changed = deepcopy(native['policy']);changed['scope']['max_quantity_per_order']='1'
    contents[supervisor.ISSUER_POLICY]=setup.canonical(changed)
    with pytest.raises(ValueError):controller.observation_configuration('c'*40)


@pytest.mark.parametrize('target',['profile.json','registry','active.json'])
def test_partial_scope_activation_keeps_one_key_and_window(local,monkeypatch,capsys,target):
    from tools import gold_strategy_budget_install as tool
    operator(tool,local,monkeypatch);native=successor._replace
    def interrupt(path,body,**kw):
        native(path,body,**kw)
        if (target=='registry' and path==local['public']) or (target!='registry' and path.name==target):
            raise OSError('isolated interruption after durable '+target)
    with monkeypatch.context() as stopped:
        stopped.setattr(successor,'_replace',interrupt)
        assert tool.main(['--source-commit','c'*40,'--enroll-approved'])==3
    result=json.loads(capsys.readouterr().out);assert result['budget_scope_installed'] is None
    stage=tool.pending_stage();key=(stage/'seed-signing-key.json').read_bytes()
    policy=(stage/'bundle/policy.json').read_bytes();state=deepcopy(local['value']['state'])
    local['clock'][0]+=1000
    assert tool.main(['--source-commit','c'*40,'--enroll-approved'])==0,capsys.readouterr().out
    assert (stage/'seed-signing-key.json').read_bytes()==key
    assert (stage/'bundle/policy.json').read_bytes()==policy
    assert len(local['confirmations'])==1 and local['value']['state']==state
    assert (stage/'previous-files/policy.json').read_bytes()==local['old_files']['policy.json']
    assert not (local['area']/'pending.json').exists() and (stage/'activation.json').exists()


@pytest.mark.parametrize('damage',['expired','operator','seed','bundle','intent','old_wallet','new_wallet',
    'registry','stop','audit','runtime','archive'])
def test_scope_resume_rejects_changes_without_new_confirmation(local,monkeypatch,capsys,damage):
    from tools import gold_strategy_budget_install as tool
    operator(tool,local,monkeypatch)
    with monkeypatch.context() as stopped:
        stopped.setattr(successor,'install_successor',lambda *a,**kw:(_ for _ in ()).throw(OSError('isolated interruption')))
        assert tool.main(['--source-commit','c'*40,'--enroll-approved'])==3
    capsys.readouterr();stage=tool.pending_stage();intent=(stage/'scope-intent.json').read_bytes()
    if damage=='expired':local['clock'][0]+=300001
    elif damage=='operator':monkeypatch.setattr(tool,'local_operator',lambda:'another-isolated-operator')
    elif damage=='seed':(stage/'seed-signing-key.json').write_text('{}')
    elif damage=='bundle':(stage/'bundle/profile.json').write_text('{}')
    elif damage=='intent':(stage/'scope-intent.json').write_text('{}')
    elif damage in ('old_wallet','new_wallet'):
        for row in local['parts']['origin']['reads']:
            if row['kind'].startswith('account'):
                asset=next(item for item in row['data']['assets'] if item['asset']==('USDC' if damage=='old_wallet' else 'USDT'))
                for name in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):asset[name]='4199'
    elif damage=='registry':
        registry=json.loads(local['public'].read_bytes());registry['keys'][0]['status']='REVOKED'
        local['public'].write_bytes(setup.canonical(registry))
    elif damage=='stop':(local['root']/'kill-switch.sqlite3').write_bytes(b'isolated changed stop')
    elif damage=='audit':next(stage.glob('scope-review-*.json')).write_text('{}')
    elif damage=='runtime':(local['ctl']/'strategy-runtime.json').write_text('{}')
    else:
        mid=local['parts']['runtime']['trial_manifest_sha256']
        (local['root']/'strategy-bar-trials'/mid/'manifest.json').write_text('{}')
    before={p.name:p.read_bytes() for p in local['cfg'].iterdir()},local['public'].read_bytes()
    pending=(local['area']/'pending.json').read_bytes()
    assert tool.main(['--source-commit','c'*40,'--enroll-approved'])==3
    result=json.loads(capsys.readouterr().out);assert result['budget_scope_installed'] is None
    assert len(local['confirmations'])==1
    assert before==({p.name:p.read_bytes() for p in local['cfg'].iterdir()},local['public'].read_bytes())
    assert pending==(local['area']/'pending.json').read_bytes()
    if damage!='intent':assert intent==(stage/'scope-intent.json').read_bytes()


def test_operator_gate_and_exclusive_lease_precede_scope_preparation(local,monkeypatch,capsys):
    from tools import gold_strategy_budget_install as tool
    operator(tool,local,monkeypatch)
    with supervisor_lease():assert tool.main(['--source-commit','c'*40,'--enroll-approved'])==3
    assert json.loads(capsys.readouterr().out)['phase']=='PREFLIGHT'
    with monkeypatch.context() as denied:
        denied.setattr(tool,'local_operator',lambda:(_ for _ in ()).throw(ValueError('isolated operator required')))
        assert tool.main(['--source-commit','c'*40,'--enroll-approved'])==3
    assert local['confirmations']==[] and local['calls']==[] and not (local['area']/'cycles').exists()


def supervisor_lease():
    from tools.demo_supervisor import controller_lease
    return controller_lease()


def test_installation_parameter_check_rejects_sub_ulp_notional_over_the_original_cap(day_materials):
    from services.execution.backtest_authority import exact_identity_hash
    args=day_materials;definition=deepcopy(args['definition']);origin=deepcopy(args['origin'])
    scale=10**18;q=1999999999999999
    p=124999*scale+pow(q,-1,scale)
    cap=(q*p)//scale
    amount=lambda n:f'{n//scale}.{n%scale:018d}'
    definition['size_and_timing']['quantity']=amount(q)
    body={k:v for k,v in definition.items() if k!='definition_hash'}
    definition['definition_hash']=exact_identity_hash(body,name='gold_bar_research_definition')
    for row in origin['reads']:
        if row['kind']=='book':row['data'].update(bidPrice='124999',askPrice=amount(p))
        elif row['kind']=='rules':
            lot=next(item for item in row['data']['terms']['filters'] if item['filterType']=='LOT_SIZE')
            lot['stepSize']='0.000000000000000001'
    scope=deepcopy(args['policy']['scope']);scope['max_notional_per_order']=scope['max_notional_per_grant']=amount(cap)
    # Exact integer arithmetic proves the quote exceeds the cap by 1e-36.
    assert q*p-cap*scale==1
    with pytest.raises(ValueError,match='BUDGET_SCOPE_NOTIONAL_EXCEEDS_ORIGINAL_LIMIT'):
        preparation.verify_strategy_parameters(origin=origin,profile=args['profile'],definition=definition,
            runtime=args['runtime'],source_commit='c'*40,scope=scope)


def test_completed_scope_after_expiry_is_integrity_only(local,monkeypatch,capsys):
    from tools import gold_strategy_budget_install as tool
    operator(tool,local,monkeypatch)
    assert tool.main(['--source-commit','c'*40,'--enroll-approved'])==0,capsys.readouterr().out
    capsys.readouterr();stage=successor.active_setup()
    before={p.name:p.read_bytes() for p in local['cfg'].iterdir()}
    local['clock'][0]=json.loads(before['policy.json'])['valid_until_ms']+1
    calls=len(local['calls'])
    assert tool.main(['--source-commit','c'*40,'--enroll-approved'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='GOLD_BUDGET_SCOPE_ALREADY_INSTALLED'
    assert result['authorization_current_verified'] is False
    assert successor.active_setup()==stage and len(local['confirmations'])==1
    assert calls==len(local['calls']) and before=={p.name:p.read_bytes() for p in local['cfg'].iterdir()}


def test_incomplete_bundle_uses_original_complete_seed(local,monkeypatch,capsys):
    from tools import gold_strategy_budget_install as tool
    operator(tool,local,monkeypatch);native=installer.write_new
    def interrupt(path,body,**kw):
        if path.parent.name=='bundle' and path.name=='policy.json':raise OSError('isolated bundle interruption')
        return native(path,body,**kw)
    with monkeypatch.context() as stopped:
        stopped.setattr(installer,'write_new',interrupt)
        assert tool.main(['--source-commit','c'*40,'--enroll-approved'])==3
    assert json.loads(capsys.readouterr().out)['budget_scope_installed'] is None
    stage=tool.pending_stage();key=(stage/'seed-signing-key.json').read_bytes()
    assert not (local['area']/'pending.json').exists() and not (stage/'bundle/policy.json').exists()
    local['clock'][0]+=1000
    assert tool.main(['--source-commit','c'*40,'--enroll-approved'])==0,capsys.readouterr().out
    assert (stage/'seed-signing-key.json').read_bytes()==key and len(local['confirmations'])==1
    scope=json.loads((stage/'bundle/policy.json').read_bytes())
    assert scope['valid_from_ms']==local['parts']['now_ms']


def test_scope_operator_process_exit_and_restart_use_original_worker_and_sqlite(local,tmp_path):
    import subprocess
    import sys
    from live.execution.demo_pre_submit import _state
    from tools import demo_functional_worker as worker
    value=local['value'];stores=value['stores'];root=local['root']
    config=dict(source_root=str(Path(worker.__file__).resolve().parents[1]),root=str(root),
        setup=str(local['area']),config=str(local['cfg']),controller=str(local['ctl']),
        registry=str(local['public']),origin=local['parts']['origin'],account=local['parts']['origin']['account_ref_sha256'],
        at=local['clock'][0],executor_record=str(value['executor']),issuer_record=str(value['issuer']),
        activation_record=str(value['activation']),dispatch=str(stores['dispatch'].path),
        recovery=stores['recovery'].db_path,oms=stores['oms']._conn.execute('PRAGMA database_list').fetchone()[2])
    cfg=tmp_path/'scope-restart-config.json';cfg.write_text(json.dumps(config))
    script=tmp_path/'scope-restart-operator.py'
    script.write_text('''import json,os,stat,subprocess,sys,time
from pathlib import Path
cfg=json.loads(Path(sys.argv[1]).read_text());sys.path.insert(0,cfg['source_root'])
from live.execution import demo_authority,demo_position_context as position,demo_market_partition_runtime as partition
from live.execution.demo_dispatch_store import DemoDispatchStore
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
from services.research.demo_execution_origin import save
from tests.live.test_demo_market_partition_runtime import shift
from tools import gold_strategy_budget_install as tool,gold_strategy_budget_enrollment as preparation
from tools import demo_successor_setup as successor,demo_supervisor,demo_functional_cycle as channel,demo_functional_worker as worker
mode=sys.argv[2];root=Path(cfg['root'])
at=int(sys.argv[3]) if mode=='worker' else cfg['at']+(1000 if mode=='resume' else 0)
cfg['origin']=shift(cfg['origin'],at-cfg['at'])
# Source acceptance, local operator and role ownership are explicit isolated substitutes.
successor.SETUP=Path(cfg['setup'])
for name,key in (('CONFIG','config'),('CONTROLLER','controller'),('REGISTRY','registry')):
 setattr(tool.installer,name,Path(cfg[key]))
tool.installer.EXECUTOR=root;tool.installer.now_ms=lambda:at
demo_supervisor.STATE=Path(cfg['controller']);demo_supervisor.EXECUTOR_REGISTRY=Path(cfg['registry'])
demo_supervisor.ISSUER_REGISTRY=Path(cfg['config'])/'authority.json'
demo_supervisor.ISSUER_POLICY=Path(cfg['config'])/'policy.json'
demo_supervisor.now_ms=lambda:at;demo_supervisor._root_private_directory=lambda _:None
demo_supervisor.verify_source=lambda _:None;os.geteuid=lambda:0
successor.verify_source=lambda _:None;tool.local_operator=lambda:'isolated-operator'
tool.installer._require_protected=lambda *a,**k:None
def metadata(info,maximum):
 assert stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077
 assert info.st_nlink==1 and 0<info.st_size<=maximum
tool.installer.private_metadata=lambda info,maximum:metadata(info,maximum)
tool.installer.os.fchown=lambda *a:None
for module in (successor,tool.installer,demo_supervisor):module._read_root_owned_json=lambda p:p.read_bytes()
native_definition=preparation.load_definition
preparation.load_definition=lambda *a,**k:native_definition(*a,**(k|{'uid':os.getuid()}))
def registry():return json.loads(Path(cfg['registry']).read_bytes())
demo_authority.load_registry_for_executor=registry
position._ROOT=root;position._custody=lambda *a,**k:None
partition.EXECUTOR_RECORD=Path(cfg['executor_record']);partition.ISSUER_RECORD=Path(cfg['issuer_record'])
partition.ACTIVATION=Path(cfg['activation_record']);partition._read_record=lambda p,**kw:p.read_bytes()
partition.executing_source_commit=lambda:'c'*40;partition._role_identity=lambda *a:None
def capture(*a,**kw):
 assert kw['symbol']=='XAUUSDT';save(root/'execution-origins'/'XAUUSDT',cfg['origin'])
 return registry(),cfg['origin']
tool.setup._runtime_inputs=capture
worker._check_path=lambda *a,**k:None;worker.now_ms=lambda:at
worker.verify_source=lambda _:None;worker.private_pipe=lambda _:None
worker.require_custody=lambda:root;worker.fixed_account=lambda:({},cfg['account'])
def opened(stack,ignored):
 return dict(dispatch=DemoDispatchStore(cfg['dispatch']),recovery=SqliteOrderRecoveryStore(cfg['recovery']),oms=LiveLedger(cfg['oms']))
worker._open=opened
def area(ignored,cycle):
 p=root/'scope-restart-views'/cycle;p.mkdir(parents=True,mode=0o700,exist_ok=True);return p
worker.area_for=area
if mode=='worker':raise SystemExit(worker.main(sys.argv[4:]))
processes=[]
class Runtime(channel.Runtime):
 def start(self,operation,payload):
  p=subprocess.Popen([sys.executable,__file__,sys.argv[1],'worker',str(at),'--source-commit',self.source_commit,
      '--cycle-sha256',self.cycle,'--operation',operation],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,bufsize=0,cwd=cfg['source_root'])
  processes.append(p)
  demo_supervisor._send(p.stdin,tool.setup.canonical(payload),time.monotonic()+10)
  p.stdin.close();return p
 def close(self):
  assert all(p.poll()==0 for p in processes)
channel.Runtime=Runtime
def confirmation(request,cycle,who):
 return dict(schema='DEMO_GOLD_BUDGET_SCOPE_OPERATOR_V1',purpose=request['purpose'],request_sha256=request['request_sha256'],
     original_cycle_sha256=cycle,operator_id=who,approved_at_ms=at,duration_seconds=86400,installation_allowance_seconds=300,
     execution_approval_granted=False,economic_authority=False)
tool.operator_confirmation=confirmation if mode=='interrupt' else lambda *a:(_ for _ in ()).throw(AssertionError('no new confirmation'))
if mode=='interrupt':
 original=successor._replace
 def replace(path,body,**kw):
  original(path,body,**kw)
  if path==Path(cfg['registry']):os._exit(73)
 successor._replace=replace
raise SystemExit(tool.main(['--source-commit','c'*40,'--enroll-approved']))
''')
    before=deepcopy(value['state']),value['events'].row_count(),(root/'kill-switch.sqlite3').read_bytes()
    first_run=subprocess.run([sys.executable,str(script),str(cfg),'interrupt'],capture_output=True,timeout=120)
    (tmp_path/'scope-interrupted.stdout').write_bytes(first_run.stdout);(tmp_path/'scope-interrupted.stderr').write_bytes(first_run.stderr)
    assert first_run.returncode==73,(first_run.returncode,first_run.stdout,first_run.stderr)
    stage=successor.active_setup() if not (local['area']/'pending.json').exists() else local['area']/'cycles'/successor.read(local['area']/'pending.json')['enrollment_sha256']
    policy=(stage/'bundle/policy.json').read_bytes();key=(stage/'bundle/signing-key.json').read_bytes()
    second=subprocess.run([sys.executable,str(script),str(cfg),'resume'],capture_output=True,timeout=120)
    (tmp_path/'scope-resumed.stdout').write_bytes(second.stdout);(tmp_path/'scope-resumed.stderr').write_bytes(second.stderr)
    assert second.returncode==0,(second.returncode,second.stdout,second.stderr)
    assert json.loads(second.stdout)['status']=='GOLD_BUDGET_SCOPE_INSTALLED'
    assert (stage/'bundle/policy.json').read_bytes()==policy and (stage/'bundle/signing-key.json').read_bytes()==key
    assert json.loads(policy)['valid_from_ms']==config['at']
    assert json.loads(policy)['valid_until_ms']==config['at']+86700000
    assert not (local['area']/'pending.json').exists() and list((local['ctl']/'tasks').iterdir())==[]
    assert _state(**{name:stores[name] for name in ('dispatch','recovery','oms')})==before[0]
    assert value['events'].row_count()==before[1] and (root/'kill-switch.sqlite3').read_bytes()==before[2]


def test_archive_source_fixture_excludes_developer_state_and_key_files(tmp_path,monkeypatch):
    from tools.gold_bar_definition import verify_source
    from tools.perpetual_replay_research import source_identity
    from tools.gold_bar_worker_inputs import protected_tree
    repo=Path(__file__).resolve().parents[2];original=tmp_path/'original';snapshot=tmp_path/'snapshot'
    expected=source_identity();copy_definition_source(repo,original)
    for name in ('.venv','.mypy_cache','key'):
        area=original/name;area.mkdir(mode=0o700)
        (area/'synthetic-excluded-file').write_text('isolated fixture only')
    native=shutil.copyfile
    def restricted_copy(path,target,*args,**kw):
        assert not any(name in path.relative_to(original).parts for name in ('.venv','.mypy_cache','key'))
        return native(path,target,*args,**kw)
    monkeypatch.setattr(shutil,'copyfile',restricted_copy)
    copy_definition_source(original,snapshot)
    assert all(not (snapshot/name).exists() for name in ('.venv','.mypy_cache','key'))
    assert verify_source(snapshot,expected)
    assert len(protected_tree(snapshot,os.getuid()))<10000
