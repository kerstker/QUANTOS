"""Native original ledger and compiler; explicitly virtual host/operator custody.

No actual sudo, credentials, exchange requests, deployment or service starts.
The retained bytes, fsync writes, leases, source review and activation are real
inside this isolated test tree; signing keys and observations are synthetic.
"""
from copy import deepcopy
import json
import os
import stat

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _state
from services.research.demo_execution_origin import save
from services.validation.demo_submission import registry_sha256
from tests.live.test_demo_market_partition import book as book, NOW
from tests.live.test_demo_market_partition_runtime import installed as installed,shift
from tests.live.test_gold_first_enrollment_review import first as first
from tools import demo_first_gold_install as tool,demo_successor_setup as successor


@pytest.fixture
def local(first,book,tmp_path,monkeypatch):
    from live.execution import demo_authority
    from tools import demo_functional_cycle as channel,demo_functional_worker as worker,demo_supervisor
    value=first;root=value['stores']['dispatch'].path.parent;clock=[NOW+140000];uid=os.getuid()
    setup=tmp_path/'operator-setup';config=tmp_path/'operator-config';controller=tmp_path/'operator-controller'
    for path in (setup,config,controller,controller/'tasks',controller/'functional-cycle'):
        path.mkdir(mode=0o700)
    registry_path=tmp_path/'public-registry.json';registry_path.write_text(json.dumps(value['registry']))
    monkeypatch.setattr(successor,'SETUP',setup)
    for name,path in (('CONFIG',config),('CONTROLLER',controller),('EXECUTOR',root),('REGISTRY',registry_path)):
        monkeypatch.setattr(tool.installer,name,path)
    monkeypatch.setattr(demo_supervisor,'STATE',controller)
    monkeypatch.setattr(demo_supervisor,'_root_private_directory',lambda path:None)
    monkeypatch.setattr(successor,'verify_source',lambda _:None)
    monkeypatch.setattr(tool,'local_operator',lambda:'isolated-operator')
    monkeypatch.setattr(tool.installer,'now_ms',lambda:clock[0])
    # Substitute only the host ownership checks; descriptor walking, bounds,
    # duplicate-key rejection, native writes and flock remain exercised.
    monkeypatch.setattr(tool.installer,'_require_protected',lambda *a,**k:None)
    def metadata(info,*,maximum):
        assert stat.S_ISREG(info.st_mode) and info.st_uid==uid and not info.st_mode&0o077
        assert info.st_nlink==1 and 0<info.st_size<=maximum
    monkeypatch.setattr(tool.installer,'private_metadata',metadata)
    monkeypatch.setattr(tool.installer.os,'fchown',lambda *a:None)
    monkeypatch.setattr(successor,'_read_root_owned_json',lambda path:path.read_bytes())
    monkeypatch.setattr(tool.installer,'_read_root_owned_json',lambda path:path.read_bytes())
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:json.loads(registry_path.read_bytes()))
    _,context,inputs,_,_=book
    old_spec=tool.setup.propose(origin=inputs['legacy_origin'],registry=value['legacy'],source_commit='c'*40,
        now_ms=inputs['now_ms'],position_context=context)
    old_result=dict(schema='DEMO_OPERATOR_SETUP_RESULT_V1',status='INSTALLED_PENDING_CYCLE',
        spec_sha256=_digest(old_spec),enrollment_sha256='a'*64,source_commit='c'*40,task_sha256='b'*64,
        orders_submitted=False,economic_authority=False)
    (setup/'spec.json').write_text(json.dumps(old_spec));(setup/'result.json').write_text(json.dumps(old_result))
    # Already expired fixture configuration. It cannot grant orders and its
    # exact bytes must be preserved in the original previous-files directory.
    old=value['legacy'];key=old['keys'][0]
    policy=dict(schema='DEMO_ISSUER_POLICY_V1',environment='BINANCE_USDM_DEMO',policy_id='expired-fixture',
        deployment_sha256=old['deployment_sha256'],registry_sha256=registry_sha256(old),executor_release_commit='c'*40,
        account_ref_sha256=value['origin']['account_ref_sha256'],authority_id=key['authority_id'],key_id=key['key_id'],
        valid_from_ms=NOW-1000,valid_until_ms=NOW+30000,grant_ttl_ms=10000,max_grants=2,max_orders=4,
        max_total_notional='300',max_cancel_grants=2,max_cancel_orders=4,
        scope=dict(symbol='BTCUSDC',sides=['BUY','SELL'],time_in_force=['GTX','IOC'],allow_opening=True,
            max_quantity_per_order='0.003',max_notional_per_order='150',max_notional_per_grant='250'),
        bindings=dict(strategy_id='expired-fixture',mandate_sha256='a'*64,validation_sha256='b'*64,
            native_limits_sha256='c'*64,strategy_approval_sha256='d'*64))
    documents={'policy.json':policy,'profile.json':{'fixture':'expired legacy profile'},
        'signing-key.json':{'fixture':'expired legacy signing material'},'authority.json':old}
    for name,data in documents.items():(config/name).write_text(json.dumps(data))
    captures=[];confirmations=[];exchanges=[]
    def capture(*a,**kw):
        assert kw['symbol']=='XAUUSDT';captures.append(kw['symbol'])
        origin=deepcopy(value['origin']);save(root/'execution-origins'/'XAUUSDT',origin)
        return json.loads(registry_path.read_bytes()),origin
    monkeypatch.setattr(tool.setup,'_runtime_inputs',capture)
    monkeypatch.setattr(worker,'_open',lambda stack,path:value['stores'])
    monkeypatch.setattr(worker,'_check_path',lambda *a,**k:None)
    monkeypatch.setattr(worker,'now_ms',lambda:clock[0])
    class Runtime:
        def __init__(self,source,registry,cycle):exchanges.append(('open',source,cycle))
        def invoke(self,operation,*,payload,timeout):
            assert operation=='context' and timeout==60
            function=worker.review_first_gold_activation if 'expected_new_registry' in payload else worker.review_first_gold
            return function(root,value['origin']['account_ref_sha256'],'c'*40,payload)
        def close(self):exchanges.append(('close',))
    monkeypatch.setattr(channel,'Runtime',Runtime)
    def confirm(spec):
        confirmations.append(deepcopy(spec))
        return dict(schema='DEMO_OPERATOR_ENROLLMENT_V1',spec_sha256=_digest(spec),
            operator_id='isolated-operator',approved_at_ms=clock[0],purpose='DEMO_FIRST_GOLD_FUNCTIONAL_TEST_ONLY')
    monkeypatch.setattr(tool.setup,'operator_confirmation',confirm)
    return dict(value=value,root=root,setup=setup,config=config,controller=controller,registry_path=registry_path,
        clock=clock,captures=captures,confirmations=confirmations,exchanges=exchanges,
        old_files={path.name:path.read_bytes() for path in config.iterdir()})


def invoke():return successor.main(['--source-commit','c'*40,'--enroll-first-gold'])


def test_first_gold_human_path_installs_only_original_new_scope_without_orders(local,capsys):
    value=local['value'];before=deepcopy(value['state']),value['events'].row_count()
    stop=(local['root']/'kill-switch.sqlite3').read_bytes()
    assert invoke()==0
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='FIRST_GOLD_ENROLLMENT_INSTALLED' and result['gold_enrollment_installed'] is True
    assert result['authorization_window_seconds']==3600
    assert all(result[name] is False for name in ('orders_submitted','services_started','economic_authority'))
    assert len(local['confirmations'])==1 and local['exchanges'][-1]==('close',)
    stage=successor.active_setup();spec=successor.read(stage/'spec.json')
    policy=successor.read(local['config']/'policy.json')
    assert spec['symbol']=='XAUUSDT' and spec['first_gold_enrollment']['gold_position_context'] is None
    assert policy['scope']['symbol']=='XAUUSDT' and policy['max_orders']==3 and policy['max_total_notional']=='750'
    assert policy['valid_until_ms']-policy['valid_from_ms']==3600000
    registry=json.loads(local['registry_path'].read_bytes())
    assert registry['keys'][:-1]==value['legacy']['keys'] and len(registry['keys'])==len(value['legacy']['keys'])+1
    assert all((stage/'previous-files'/name).read_bytes()==body for name,body in local['old_files'].items())
    assert _state(**{name:value['stores'][name] for name in ('dispatch','recovery','oms')})==before[0]
    assert value['events'].row_count()==before[1] and (local['root']/'kill-switch.sqlite3').read_bytes()==stop
    assert not (local['setup']/'pending.json').exists() and (stage/'activation.json').is_file()
    assert invoke()==0
    repeated=json.loads(capsys.readouterr().out)
    assert repeated['status']=='FIRST_GOLD_ALREADY_ENROLLED' and repeated['authorization_current_verified'] is False
    assert len(local['confirmations'])==1


def test_installed_first_gold_materials_reach_original_risk_and_issuer_without_dispatch(local,tmp_path,capsys):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from live.execution import demo_request_inputs,demo_issuer
    from live.execution.demo_execution_session import ExecutionSession
    from live.execution.demo_issuer_store import IssuerStore
    from quantos.contracts.killswitch import KillSwitchState
    from services.validation import demo_submission
    from tools import binance_demo_market_inputs
    from tests.tools.test_binance_demo_market_inputs import material
    assert invoke()==0;capsys.readouterr()
    stage=successor.active_setup();bundle=stage/'bundle'
    manifest=successor.read(bundle/'manifest.json');task=successor.read(bundle/(manifest['tasks'][0]+'.json'))
    policy=successor.read(local['config']/'policy.json');registry=successor.read(local['registry_path'])
    secret=successor.read(local['config']/'signing-key.json')
    private=Ed25519PrivateKey.from_private_bytes(demo_submission._decode(secret['private_key_base64url'],32))
    del secret
    at=NOW+139990;data=material();data['ticker'].update(symbol='XAUUSDT',openTime=at-86400000,closeTime=at)
    delta=at//60000*60000-1-data['klines'][-1][6]
    for row in data['klines']:row[0]+=delta;row[6]+=delta
    ticks=iter(range(at,at+100))
    def get(kind,**kwargs):
        body={'serverTime':at+(6 if kind=='clock_after' else 0)} if kind.startswith('clock') else data[kind]
        return json.dumps(body,separators=(',',':')).encode()
    market=binance_demo_market_inputs.collect(symbol='XAUUSDT',get=get,clock_ms=lambda:next(ticks))
    value=local['value'];stores=value['stores']
    before=_state(**{name:stores[name] for name in ('dispatch','recovery','oms')}),value['events'].row_count()
    built=demo_request_inputs.build(orders=task['orders'],origin=value['origin'],market_inputs=market,
        profile=task['profile'],issuer_policy=policy,kill_state=KillSwitchState(),
        dispatch=stores['dispatch'],recovery=stores['recovery'],oms=stores['oms'],now_ms=NOW+140000)
    session=ExecutionSession(dispatch=stores['dispatch'],recovery=stores['recovery'],oms=stores['oms'],
        evidence_directory=tmp_path/'original-proposals')
    handoff=session.propose(tool.setup.canonical(built['request']),now_ms=NOW+140000)
    log=IssuerStore(tmp_path/'isolated-issuer.sqlite3',create=True)
    try:
        grant=demo_issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,
            store=log,now_ms=NOW+140001)
    finally:log.close()
    del private
    proof=demo_submission.verify(grant['envelope'],registry=registry,
        expected_context=handoff['proposal']['context'],orders=task['orders'],now_ms=NOW+140001)
    assert proof['authority_id']==registry['keys'][-1]['authority_id']
    assert handoff['proposal']['state']['reservations']==before[0]['reservations']
    assert 'position_context' not in handoff['proposal']['state']
    assert before==(_state(**{name:stores[name] for name in ('dispatch','recovery','oms')}),value['events'].row_count())


@pytest.mark.parametrize('target',['profile.json','registry','active.json'])
def test_exact_partial_activation_resumes_original_key_window_and_risk(local,monkeypatch,capsys,target):
    original=successor._replace
    def fault(path,body,**kw):
        original(path,body,**kw)
        if (target=='registry' and path==local['registry_path']) or (target!='registry' and path.name==target):
            raise OSError('isolated interruption after durable '+target)
    with monkeypatch.context() as interruption:
        interruption.setattr(successor,'_replace',fault)
        assert invoke()==3
    failed=json.loads(capsys.readouterr().out)
    assert failed['phase']=='ACTIVATION' and failed['gold_enrollment_installed'] is None
    pending=successor.read(local['setup']/'pending.json');stage=local['setup']/'cycles'/pending['enrollment_sha256']
    bundle={p.name:p.read_bytes() for p in (stage/'bundle').iterdir()}
    original_policy=json.loads(bundle['policy.json']);original_registry=json.loads(bundle['registry.json'])
    local['clock'][0]+=1000
    local['value']['origin']=shift(local['value']['origin'],1000)
    assert invoke()==0
    assert json.loads(capsys.readouterr().out)['status']=='FIRST_GOLD_ENROLLMENT_INSTALLED'
    assert successor.read(local['config']/'policy.json')==original_policy
    assert json.loads(local['registry_path'].read_bytes())==original_registry
    assert {p.name:p.read_bytes() for p in (stage/'bundle').iterdir()}==bundle
    assert len(local['confirmations'])==1 and len(original_registry['keys'])==len(local['value']['legacy']['keys'])+1
    assert original_policy['valid_from_ms']==NOW+140000 and original_policy['valid_until_ms']==NOW+3740000


def test_first_installer_requires_the_real_local_operator_gate_before_source_reads(monkeypatch,capsys):
    monkeypatch.setattr(tool,'local_operator',lambda:(_ for _ in ()).throw(ValueError('no fixture operator')))
    monkeypatch.setattr(successor,'verify_source',lambda *a:pytest.fail('identity denied before protected inputs'))
    assert invoke()==3
    assert json.loads(capsys.readouterr().out)['phase']=='PREFLIGHT'


def test_rejected_resume_keeps_unknown_activation_after_a_durable_partial_write(local,monkeypatch,capsys):
    original=successor._replace
    def interrupt(path,body,**kw):
        original(path,body,**kw)
        if path==local['config']/'profile.json':raise OSError('isolated durable partial write')
    with monkeypatch.context() as failure:
        failure.setattr(successor,'_replace',interrupt)
        assert invoke()==3
    capsys.readouterr()
    (local['root']/'kill-switch.sqlite3').write_bytes(b'isolated changed stop')
    before={p.name:p.read_bytes() for p in local['config'].iterdir()}
    assert invoke()==3
    result=json.loads(capsys.readouterr().out)
    assert result['phase']=='STAGED_REVIEW' and result['gold_enrollment_installed'] is None
    assert before=={p.name:p.read_bytes() for p in local['config'].iterdir()}
    assert len(local['confirmations'])==1


@pytest.mark.parametrize('damage',['expired','operator','seed','bundle','intent','old_wallet','new_wallet',
    'registry','stop','audit'])
def test_partial_first_install_keeps_conflicts_and_never_reissues_scope(local,monkeypatch,capsys,damage):
    original=successor.install_successor
    with monkeypatch.context() as interrupted:
        interrupted.setattr(successor,'install_successor',lambda *a,**k:
            (_ for _ in ()).throw(OSError('isolated before activation')))
        assert invoke()==3
    capsys.readouterr()
    stage=tool.pending_stage(successor.active_setup())
    if damage=='expired':local['clock'][0]=NOW+3740000
    elif damage=='operator':monkeypatch.setattr(tool,'local_operator',lambda:'different-isolated-operator')
    elif damage=='seed':(stage/'seed-signing-key.json').write_text('{}')
    elif damage=='bundle':(stage/'bundle/profile.json').write_text('{}')
    elif damage=='intent':(stage/'first-gold-intent.json').write_text('{}')
    elif damage in ('old_wallet','new_wallet'):
        for read in local['value']['origin']['reads']:
            if read['kind'].startswith('account'):
                asset=next(a for a in read['data']['assets'] if a['asset']==('USDC' if damage=='old_wallet' else 'USDT'))
                for name in ('walletBalance','crossWalletBalance','availableBalance','marginBalance'):asset[name]='4199'
    elif damage=='registry':
        value=json.loads(local['registry_path'].read_bytes());value['keys'][0]['status']='REVOKED'
        local['registry_path'].write_text(json.dumps(value))
    elif damage=='stop':(local['root']/'kill-switch.sqlite3').write_bytes(b'isolated invalid stop')
    else:
        path=next(stage.glob('activation-review-*.json'));path.write_text('{}')
    before={path.name:path.read_bytes() for path in local['config'].iterdir()},local['registry_path'].read_bytes()
    pending=(local['setup']/'pending.json').read_bytes()
    assert invoke()==3
    assert len(local['confirmations'])==1
    assert before==({path.name:path.read_bytes() for path in local['config'].iterdir()},local['registry_path'].read_bytes())
    assert (local['setup']/'pending.json').read_bytes()==pending
    assert successor.install_successor is original


@pytest.mark.parametrize('missing',['profile.json','manifest.json'])
def test_complete_seed_restores_only_missing_material_without_new_key(local,monkeypatch,capsys,missing):
    with monkeypatch.context() as interrupted:
        interrupted.setattr(successor,'install_successor',lambda *a,**k:
            (_ for _ in ()).throw(OSError('isolated before activation')))
        assert invoke()==3
    capsys.readouterr();stage=tool.pending_stage(successor.active_setup());path=stage/'bundle'/missing
    original=path.read_bytes();path.unlink()
    key=(stage/'bundle/signing-key.json').read_bytes()
    assert invoke()==0
    assert path.read_bytes()==original and (stage/'bundle/signing-key.json').read_bytes()==key
    assert len(local['confirmations'])==1


def test_exclusive_controller_lease_blocks_install_before_any_capture(local,capsys):
    from tools import demo_supervisor
    with demo_supervisor.controller_lease():assert invoke()==3
    assert json.loads(capsys.readouterr().out)['phase']=='PREFLIGHT'
    assert not local['captures'] and not local['confirmations']


@pytest.mark.parametrize('extra',[['--enroll'],['--run-cycle'],['--symbol','XAUUSDT'],
    ['--prepare-first-gold-enrollment'],['--review-first-gold-enrollment'],['--portfolio-config','/isolated.json']])
def test_explicit_first_install_rejects_other_modes_before_operator_gate(monkeypatch,extra):
    monkeypatch.setattr(tool,'local_operator',lambda:pytest.fail('invalid mode reached operator gate'))
    assert successor.main(['--source-commit','c'*40,'--enroll-first-gold',*extra])==3


def test_real_operator_process_exit_and_restart_use_original_pipe_and_sqlite(local,tmp_path):
    from pathlib import Path
    import subprocess
    import sys
    from tools import demo_functional_worker as worker
    value=local['value'];stores=value['stores'];root=local['root']
    config=dict(source_root=str(Path(worker.__file__).resolve().parents[1]),root=str(root),
        setup=str(local['setup']),config=str(local['config']),controller=str(local['controller']),
        registry=str(local['registry_path']),origin=value['origin'],account=value['origin']['account_ref_sha256'],
        at=NOW+140000,executor_record=str(value['executor']),issuer_record=str(value['issuer']),
        activation_record=str(value['activation']),dispatch=str(stores['dispatch'].path),
        recovery=stores['recovery'].db_path,oms=stores['oms']._conn.execute('PRAGMA database_list').fetchone()[2])
    cfg=tmp_path/'restart-config.json';cfg.write_text(json.dumps(config))
    script=tmp_path/'restart-operator.py'
    script.write_text('''import json,os,stat,subprocess,sys,time
from pathlib import Path
cfg=json.loads(Path(sys.argv[1]).read_text());sys.path.insert(0,cfg['source_root'])
from live.execution import demo_authority,demo_position_context as position,demo_market_partition_runtime as partition
from live.execution.demo_dispatch_store import DemoDispatchStore
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
from services.research.demo_execution_origin import save
from tools import demo_first_gold_install as tool,demo_successor_setup as successor
from tools import demo_supervisor,demo_functional_cycle as channel,demo_functional_worker as worker
mode=sys.argv[2]
# Source, local-human and UID ownership are explicit fixture substitutes.
root=Path(cfg['root']);at=int(sys.argv[3]) if mode=='worker' else cfg['at']+(1000 if mode=='resume' else 0)
if mode=='resume':
 def shift(v):
  if type(v) is dict:return {k:shift(x) for k,x in v.items()}
  if type(v) is list:return [shift(x) for x in v]
  return v+1000 if type(v) is int and v>=1800000000000 else v
 cfg['origin']=shift(cfg['origin'])
successor.SETUP=Path(cfg['setup'])
for name,key in (('CONFIG','config'),('CONTROLLER','controller'),('REGISTRY','registry')):
 setattr(tool.installer,name,Path(cfg[key]))
tool.installer.EXECUTOR=root
demo_supervisor.STATE=Path(cfg['controller']);demo_supervisor._root_private_directory=lambda p:None
successor.verify_source=lambda _:None;tool.local_operator=lambda:'isolated-operator'
tool.installer._require_protected=lambda *a,**k:None
def metadata(info,maximum):
 assert stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o077
 assert info.st_nlink==1 and 0<info.st_size<=maximum
tool.installer.private_metadata=lambda info,maximum:metadata(info,maximum)
tool.installer.os.fchown=lambda *a:None
successor._read_root_owned_json=lambda p:p.read_bytes()
tool.installer._read_root_owned_json=lambda p:p.read_bytes()
tool.installer.now_ms=lambda:at
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
 return dict(dispatch=DemoDispatchStore(cfg['dispatch']),recovery=SqliteOrderRecoveryStore(cfg['recovery']),
             oms=LiveLedger(cfg['oms']))
worker._open=opened
def area(ignored,cycle):
 p=root/'restart-views'/cycle;p.mkdir(parents=True,mode=0o700,exist_ok=True);return p
worker.area_for=area
if mode=='worker':raise SystemExit(worker.main(sys.argv[4:]))
processes=[]
class Runtime(channel.Runtime):
 def start(self,operation,payload):
  p=subprocess.Popen([sys.executable,__file__,sys.argv[1],'worker',str(at),'--source-commit',self.source_commit,
      '--cycle-sha256',self.cycle,'--operation',operation],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
      stderr=subprocess.DEVNULL,bufsize=0,cwd=cfg['source_root'])
  processes.append(p)
  demo_supervisor._send(p.stdin,tool.setup.canonical(payload),time.monotonic()+10)
  p.stdin.close();return p
 def close(self):
  assert all(p.poll()==0 for p in processes)
channel.Runtime=Runtime
def confirmation(spec):
 return dict(schema='DEMO_OPERATOR_ENROLLMENT_V1',spec_sha256=tool.setup.digest(spec),
     operator_id='isolated-operator',approved_at_ms=at,purpose='DEMO_FIRST_GOLD_FUNCTIONAL_TEST_ONLY')
tool.setup.operator_confirmation=confirmation if mode=='interrupt' else lambda _:(_ for _ in ()).throw(AssertionError('no new confirmation'))
if mode=='interrupt':
 original=successor._replace
 def replace(path,body,**kw):
  original(path,body,**kw)
  if path==Path(cfg['registry']):os._exit(73)
 successor._replace=replace
raise SystemExit(successor.main(['--source-commit','c'*40,'--enroll-first-gold']))
''')
    before=deepcopy(value['state']),value['events'].row_count(),(root/'kill-switch.sqlite3').read_bytes()
    first_run=subprocess.run([sys.executable,str(script),str(cfg),'interrupt'],capture_output=True,timeout=90)
    (tmp_path/'interrupted.stdout').write_bytes(first_run.stdout);(tmp_path/'interrupted.stderr').write_bytes(first_run.stderr)
    assert first_run.returncode==73,(first_run.returncode,first_run.stdout,first_run.stderr)
    pending=successor.read(local['setup']/'pending.json');stage=local['setup']/'cycles'/pending['enrollment_sha256']
    policy=(stage/'bundle/policy.json').read_bytes();key=(stage/'bundle/signing-key.json').read_bytes()
    second=subprocess.run([sys.executable,str(script),str(cfg),'resume'],capture_output=True,timeout=90)
    (tmp_path/'resumed.stdout').write_bytes(second.stdout);(tmp_path/'resumed.stderr').write_bytes(second.stderr)
    assert second.returncode==0,(second.returncode,second.stdout,second.stderr)
    result=json.loads(second.stdout);assert result['status']=='FIRST_GOLD_ENROLLMENT_INSTALLED'
    assert (stage/'bundle/policy.json').read_bytes()==policy and (stage/'bundle/signing-key.json').read_bytes()==key
    actual=json.loads(local['registry_path'].read_bytes());assert len(actual['keys'])==len(value['legacy']['keys'])+1
    assert json.loads(policy)['valid_from_ms']==NOW+140000 and json.loads(policy)['valid_until_ms']==NOW+3740000
    assert not (local['setup']/'pending.json').exists()
    assert _state(**{name:stores[name] for name in ('dispatch','recovery','oms')})==before[0]
    assert value['events'].row_count()==before[1] and (root/'kill-switch.sqlite3').read_bytes()==before[2]
