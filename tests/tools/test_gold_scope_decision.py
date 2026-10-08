"""First exact approval consumes genuine signed Scope and the original V2.

Source/operator/custody, market signal/prefix and observations are explicit
isolated fixtures. Keys are synthetic; no installed state, credential or send.
"""
from copy import deepcopy
import hashlib
import json
import os

import pytest

from live.execution.demo_batch_risk import _digest
from tests.tools.test_gold_scope_pilot import (
    book as book, installed as installed, first as first, materials as materials,
    scope_inputs as scope_inputs, day_materials as day_materials, scoped_request,
    isolated_retention_custody,
)
from tests.live.test_demo_market_partition_runtime import shift


def isolated_executor_custody(f, tmp_path, monkeypatch):
    """Map the signed synthetic executor identity to this isolated test owner."""
    from tools import demo_resident_recovery as recovery, gold_pilot_closure as closure
    f['root'].relative_to(tmp_path)
    f['root'].chmod(0o700)
    original = recovery._check_path
    def check(path, uid, *, directory):
        path.relative_to(tmp_path)
        assert uid == f['registry']['executor_uid']
        original(path, os.getuid(), directory=directory)
    monkeypatch.setattr(recovery, '_check_path', check)
    monkeypatch.setattr(closure, '_check_path', check)


def with_original_window(f,result):
    from tools.gold_strategy_worker import original_pilot_window
    value=deepcopy(result)
    value['decision']['pilot_control']=deepcopy(value['decision']['draft']['pilot_control'])
    value['decision_sha256']=_digest(value['decision'])
    value['original_pilot_window']=original_pilot_window(f['root'],decision=value['decision'],
        source_commit='c'*40,account=f['scope']['account_ref_sha256'])
    return value


def retain_worker_original(f,result,*,tmp_path,monkeypatch):
    """The pipe substitute retains its own native immutable frame first."""
    from tools import demo_operating_bundle as installer, gold_pilot_retention as retention
    monkeypatch.setattr(installer,'EXECUTOR',f['root'])
    isolated_retention_custody(f['root'],f['registry'],tmp_path,monkeypatch)
    with retention.executor_retention(f['root'],f['runtime'],source_commit='c'*40,
            account=f['scope']['account_ref_sha256'],uid=f['registry']['executor_uid']) as store:
        claim=store.claim(bundle=f['runtime'],now_ms=f['at'])
        store.put(claim,'decision',result['decision']);store.put(claim,'result',result)


@pytest.fixture
def decision(day_materials,scope_inputs,monkeypatch):
    from live.execution import demo_authority, demo_gold_pilot_enrollment as enrollment, demo_gold_pilot as pilot
    from tools.gold_strategy_request import constraints_from_draft
    value=scope_inputs[0];stores=value['stores'];root=stores['dispatch'].path.parent
    retained=scoped_request(day_materials);scope=retained['budget_scope'];registry=retained['scope_registry']
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:deepcopy(registry))
    (root/'gold-pilot-request.json').write_text(json.dumps(retained))
    (root/'gold-pilot-request.json').chmod(0o600)
    at=day_materials['now_ms']+1000;origin=shift(day_materials['origin'],1000)
    config=enrollment.enroll_requested(root=root,source_commit='c'*40,origin=origin,
        **{key:stores[key] for key in ('dispatch','recovery','oms')},now_ms=at)
    control=pilot.observe(dict(account_ref_sha256=scope['account_ref_sha256'],equity='4200'),
        now_ms=at,origin_sha256=_digest(origin))
    runtime=deepcopy(day_materials['runtime']);definition=day_materials['definition']['definition_hash']
    orders=[dict(symbol='XAUUSDT',side='BUY',type='LIMIT',timeInForce='IOC',quantity='0.002',
        price='60000.1',reduceOnly=False,positionSide='BOTH',newOrderRespType='ACK',
        newClientOrderId='QDscopeDecisionfixture')]
    until=at+day_materials['definition']['size_and_timing']['entry_timeout_ms']
    draft=dict(schema='GOLD_BAR_DEMO_ENTRY_DRAFT_V1',status='BAR_ENTRY_DRAFT',environment='BINANCE_USDM_DEMO',
        observed_at_ms=at,valid_until_ms=until,signal_valid_until_ms=until,definition_hash=definition,
        origin_sha256=_digest(origin),orders=orders,pilot_control=control,
        submission_enabled=False,economic_authority=False,risk_status='NOT_RUN')
    draft['result_hash']=_digest(draft);constraints=constraints_from_draft(draft,now_ms=at+1)
    body=dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1',source_commit='c'*40,bundle_sha256=_digest(runtime),
        account_ref_sha256=scope['account_ref_sha256'],observed_at_ms=at,status='DECISION_REQUIRES_APPROVAL',
        strategy_definition_sha256=definition,bar_continuation_sha256='f'*64,bar_signal_sha256='e'*64,
        draft=draft,execution_constraints=constraints,submission_enabled=False,economic_authority=False)
    result=dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1',status='DECISION_RETAINED',source_commit='c'*40,
        bundle_sha256=_digest(runtime),decision_sha256=_digest(body),decision=body,
        submission_enabled=False,economic_authority=False)
    return dict(scope=scope,registry=registry,runtime=runtime,result=result,at=at+1,
        origin=origin,root=root,stores=stores,config=config,parts=day_materials,value=value)


def test_signed_scope_can_prepare_exact_request_without_approval(decision):
    from tools.gold_strategy_approval_request import prepare,policy_candidate
    from live.execution.demo_gold_budget_scope import budget_binding
    from live.execution.demo_issuer_policy import budget_group_binding,current_key
    f=decision
    request=prepare(f['result'],runtime=f['runtime'],policy=f['scope'],registry=f['registry'],now_ms=f['at'])
    candidate=policy_candidate(request,result=f['result'],runtime=f['runtime'],policy=f['scope'],
        registry=f['registry'],now_ms=f['at'])
    assert request['approval_granted'] is False and candidate['approval_granted'] is False
    proposed=candidate['policy_candidate']
    assert proposed['bindings']['execution_constraints_sha256']==_digest(f['result']['decision']['execution_constraints'])
    assert budget_group_binding(proposed)==budget_binding(f['scope'])
    assert current_key(proposed,f['registry'],f['at'])==f['registry']['keys'][-1]
    with pytest.raises(ValueError):current_key(f['scope'],f['registry'],f['at'])


def test_controller_retains_scope_first_decision_for_independent_review(decision,tmp_path,monkeypatch):
    from tools import gold_strategy_controller as controller
    f=decision;monkeypatch.setattr(controller.supervisor,'STATE',tmp_path)
    monkeypatch.setattr(controller.supervisor,'now_ms',lambda:f['at'])
    monkeypatch.setattr(controller.supervisor,'_root_private_directory',lambda _:None)
    request=controller.retain_approval_request(f['result'],bundle=f['runtime'],policy=f['scope'],registry=f['registry'])
    assert request is not None and request['approval_granted'] is False
    assert json.loads((tmp_path/'strategy-approval-requests'/(_digest(request)+'.json')).read_bytes())==request
    assert not (tmp_path/'tasks').exists()


@pytest.fixture
def scope_installation(decision,tmp_path,monkeypatch):
    from tools import gold_strategy_policy_install as tool, gold_strategy_controller as controller
    from tools import gold_strategy_approval_request as approval
    from live.execution.demo_issuer_policy import current_key
    from live.execution.demo_gold_budget_scope import historical_scope_binding
    f=decision;ctl=tmp_path/'controller';cfg=tmp_path/'issuer'
    ctl.mkdir(mode=0o700);cfg.mkdir(mode=0o700);policy_path=cfg/'policy.json'
    policy_path.write_bytes(tool.installer.canonical(f['scope']))
    (f['root']/'kill-switch.sqlite3').write_bytes(f['parts']['stop_image'])
    request=approval.prepare(f['result'],runtime=f['runtime'],policy=f['scope'],registry=f['registry'],now_ms=f['at'])
    for name,value in [('strategy-runtime.json',f['runtime']),
        ('strategy-decisions/'+_digest(f['result'])+'.json',f['result']),
        ('strategy-approval-requests/'+_digest(request)+'.json',request)]:
        path=ctl/name;path.parent.mkdir(mode=0o700,exist_ok=True);path.write_bytes(tool.installer.canonical(value))
    def read(path):
        path.relative_to(tmp_path);assert not path.is_symlink();return path.read_bytes()
    def configuration(source):
        assert source=='c'*40
        policy=json.loads(read(policy_path));current_key(policy,f['registry'],f['at'])
        return deepcopy(f['registry']),policy
    def observation(source):
        assert source=='c'*40
        policy=json.loads(read(policy_path));historical_scope_binding(policy,f['registry'])
        return deepcopy(f['registry']),policy
    def directory(path,uid,gid,*,mode=0o700):
        path.relative_to(tmp_path);assert uid==gid==0;path.mkdir(mode=mode)
    def write(path,body,*,uid=0,gid=0,mode=0o600):
        import os
        path.relative_to(tmp_path);assert uid==0
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
        with os.fdopen(fd,'wb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
    monkeypatch.setattr(tool.supervisor,'STATE',ctl)
    monkeypatch.setattr(tool.supervisor,'ISSUER_POLICY',policy_path)
    monkeypatch.setattr(tool.supervisor,'configuration',configuration)
    monkeypatch.setattr(controller,'observation_configuration',observation)
    monkeypatch.setattr(tool.supervisor,'_read_root_owned_json',read)
    monkeypatch.setattr(tool.supervisor,'_root_private_directory',lambda path:path.relative_to(tmp_path))
    monkeypatch.setattr(tool.supervisor,'now_ms',lambda:f['at'])
    monkeypatch.setattr(tool.installer,'EXECUTOR',f['root'])
    isolated_executor_custody(f, tmp_path, monkeypatch)
    monkeypatch.setattr(tool.installer,'directory',directory)
    monkeypatch.setattr(tool.installer,'write_new',write)
    monkeypatch.setattr(tool,'local_operator',lambda:'isolated-operator')
    monkeypatch.setattr('builtins.input',lambda prompt:prompt.removeprefix('请输入 ').strip())
    # The synthetic signal's prefix is explicitly substituted; originals are retained and re-read.
    monkeypatch.setattr(approval,'review_bar_prefix',lambda **kw:{'isolated_prefix_sha256':'f'*64})
    return dict(f=f,tool=tool,request=request,controller=ctl,policy_path=policy_path,cfg=cfg)


def test_scope_human_install_retains_original_budget_and_creates_fixed_dispatch(scope_installation):
    from tools.gold_strategy_approval_request import review_retained
    from live.execution.demo_issuer_policy import budget_group_binding
    from live.execution.demo_pre_submit import _state
    local=scope_installation;f=local['f'];tool=local['tool'];before=_state(f['stores']['dispatch'],f['stores']['recovery'],f['stores']['oms'])
    candidate=review_retained(source_commit='c'*40,stored_request_sha256=_digest(local['request']),prepare_candidate=True)
    result=tool.install_approved(source_commit='c'*40,stored_request_sha256=_digest(local['request']))
    assert result['installed'] is True and result['orders_submitted'] is False
    policy=json.loads(local['policy_path'].read_bytes());dispatch=json.loads((local['controller']/'strategy-dispatch.json').read_bytes())
    assert policy==candidate['policy_candidate'] and dispatch==candidate['dispatcher_configuration']
    assert dispatch['budget_group_binding_sha256']==budget_group_binding(policy)
    assert dispatch['kill_switch']['tripped'] is False
    stage=local['cfg']/'strategy-approvals'/_digest(candidate)
    assert json.loads((stage/'previous-policy.json').read_bytes())==f['scope']
    assert (stage/'previous-dispatch.absent').is_file()
    assert _state(f['stores']['dispatch'],f['stores']['recovery'],f['stores']['oms'])==before
    assert not (local['controller']/'tasks').exists()


def test_scope_targeted_dispatch_waits_without_materializing_or_reading_profile(scope_installation,monkeypatch):
    from tools import gold_strategy_dispatch as dispatch
    local=scope_installation;f=local['f']
    monkeypatch.setattr(dispatch.checkpoint,'materialize',lambda *a,**kw:pytest.fail('Scope created a task'))
    monkeypatch.setattr(dispatch,'review_bar_runtime',lambda **kw:pytest.fail('waiting Scope rebuilt prefix'))
    result=dispatch.run(source_commit='c'*40,use_approved_policy=True,result_sha256=_digest(f['result']),execute=True)
    assert result['status']=='WAITING_INDEPENDENT_APPROVAL',result
    assert result['task_sha256'] is None and result['submission_enabled'] is False
    assert not (local['controller']/'strategy-dispatch.json').exists()


@pytest.fixture
def installation(decision,tmp_path,monkeypatch):
    from tools import gold_strategy_controller as controller, gold_strategy_policy_install as tool
    from tools import gold_strategy_approval_request as approval
    from live.execution import demo_gold_budget_scope as scope_contract
    from live.execution.demo_issuer_policy import current_key
    f=decision;state=tmp_path/'controller';state.mkdir(mode=0o700)
    issuer=tmp_path/'issuer';issuer.mkdir(mode=0o700);path=issuer/'policy.json'
    path.write_bytes(tool.installer.canonical(f['scope']))
    (f['root']/'kill-switch.sqlite3').write_bytes(f['parts']['stop_image'])
    def read(path):
        path.relative_to(tmp_path);assert not path.is_symlink();return path.read_bytes()
    def configuration(source):
        assert source=='c'*40
        policy=json.loads(read(path));current_key(policy,f['registry'],f['at'])
        return deepcopy(f['registry']),policy
    def observation(source):
        assert source=='c'*40
        policy=json.loads(read(path))
        if policy.get('schema')==scope_contract.SCHEMA:scope_contract.historical_scope_binding(policy,f['registry'])
        else:current_key(policy,f['registry'],f['at'])
        return deepcopy(f['registry']),policy
    monkeypatch.setattr(tool.supervisor,'STATE',state)
    monkeypatch.setattr(tool.supervisor,'ISSUER_POLICY',path)
    monkeypatch.setattr(tool.supervisor,'configuration',configuration)
    monkeypatch.setattr(controller,'observation_configuration',observation)
    monkeypatch.setattr(tool.supervisor,'_read_root_owned_json',read)
    monkeypatch.setattr(tool.supervisor,'_root_private_directory',lambda p:p.relative_to(tmp_path))
    monkeypatch.setattr(tool.supervisor,'now_ms',lambda:f['at'])
    monkeypatch.setattr(tool.installer,'EXECUTOR',f['root'])
    isolated_executor_custody(f, tmp_path, monkeypatch)
    monkeypatch.setattr(tool.installer.os,'chown',lambda *a:None)
    monkeypatch.setattr(tool.installer.os,'fchown',lambda *a:None)
    monkeypatch.setattr(tool,'local_operator',lambda:'isolated-operator')
    monkeypatch.setattr('builtins.input',lambda prompt:prompt.removeprefix('请输入 ').strip())
    monkeypatch.setattr(approval,'review_bar_prefix',lambda **kw:
        dict(snapshot_sha256='f'*64,definition_hash=f['scope']['budget_group']['strategy_definition_sha256'],
            submission_enabled=False,approval_granted=False))
    request=approval.prepare(f['result'],runtime=f['runtime'],policy=f['scope'],registry=f['registry'],now_ms=f['at'])
    for name,value in [('strategy-runtime.json',f['runtime']),
        ('strategy-decisions/'+_digest(f['result'])+'.json',f['result']),
        ('strategy-approval-requests/'+_digest(request)+'.json',request)]:
        target=state/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if '/' in name:
            from live.execution.demo_pre_submit import _retain
            assert _retain(target.parent,value)==target
        else:
            target.write_bytes(tool.installer.canonical(value));target.chmod(0o600)
    return f|dict(tool=tool,request=request,state=state,policy_path=path)


def test_first_human_scope_policy_installs_exact_dispatch_without_rewriting_stop(installation):
    from live.execution.demo_issuer_policy import budget_group_binding,current_key
    from live.execution.demo_gold_budget_scope import budget_binding
    f=installation;before=f['policy_path'].read_bytes();stop=(f['root']/'kill-switch.sqlite3').read_bytes()
    state=f['value']['state'];stores=f['stores'];tool=f['tool']
    result=tool.install_approved(source_commit='c'*40,stored_request_sha256=_digest(f['request']))
    assert result['installed'] is True and result['orders_submitted'] is False
    proposed=json.loads(f['policy_path'].read_bytes())
    assert current_key(proposed,f['registry'],f['at'])==f['registry']['keys'][-1]
    assert budget_group_binding(proposed)==budget_binding(f['scope'])
    dispatch=json.loads((f['state']/'strategy-dispatch.json').read_bytes())
    assert dispatch['budget_group_binding_sha256']==budget_binding(f['scope'])
    assert dispatch['kill_switch']['tripped'] is False
    assert (f['root']/'kill-switch.sqlite3').read_bytes()==stop
    stage=f['policy_path'].parent/'strategy-approvals'/result['candidate_sha256']
    assert (stage/'previous-policy.json').read_bytes()==before
    from live.execution.demo_pre_submit import _state
    assert _state(stores['dispatch'],stores['recovery'],stores['oms'])==state


def test_scope_dispatch_waits_without_task_or_prefix_replay(installation,monkeypatch):
    from tools import gold_strategy_dispatch as dispatcher,gold_strategy_approval_request as approval
    f=installation
    monkeypatch.setattr(approval,'review_bar_prefix',lambda **kw:pytest.fail('waiting replayed a prefix'))
    monkeypatch.setattr(dispatcher.checkpoint,'materialize',lambda *a,**kw:pytest.fail('unapproved Scope created a task'))
    result=dispatcher.run(source_commit='c'*40,use_approved_policy=True,result_sha256=_digest(f['result']),execute=True)
    assert result['status']=='WAITING_INDEPENDENT_APPROVAL',result
    assert result['task_sha256'] is None and result['submission_enabled'] is False
    assert not (f['state']/'strategy-dispatch.json').exists()


@pytest.mark.parametrize('damage',['registry','signed_runtime','effective_runtime','definition','expired','no_registry',
    'order_scope','order_quantity'])
def test_first_scope_request_rejects_substitution_without_writes(decision,damage):
    from tools.gold_strategy_approval_request import prepare
    from tools.gold_strategy_request import constraints_from_draft
    f=decision;runtime=deepcopy(f['runtime']);result=deepcopy(f['result']);registry=deepcopy(f['registry']);at=f['at']
    if damage=='registry':registry['keys'][-1]['status']='REVOKED'
    elif damage in ('signed_runtime','effective_runtime'):
        runtime['seconds']-=1
        result['bundle_sha256']=result['decision']['bundle_sha256']=_digest(runtime)
    elif damage in ('definition','order_scope','order_quantity'):
        draft=result['decision']['draft']
        if damage=='definition':draft['definition_hash']='a'*64
        elif damage=='order_scope':draft['orders'][0]['timeInForce']='GTX'
        else:draft['orders'][0]['quantity']='0.02'
        draft['result_hash']=_digest({k:v for k,v in draft.items() if k!='result_hash'})
        result['decision']['execution_constraints']=constraints_from_draft(draft,now_ms=at)
    elif damage=='expired':at=result['decision']['execution_constraints']['valid_until_ms']
    else:registry=None
    result['decision_sha256']=_digest(result['decision'])
    before={p:p.read_bytes() for p in f['root'].rglob('*') if p.is_file()}
    with pytest.raises(ValueError):prepare(result,runtime=runtime,policy=f['scope'],registry=registry,now_ms=at,
        base_runtime=f['runtime'] if damage=='effective_runtime' else None)
    assert before=={p:p.read_bytes() for p in f['root'].rglob('*') if p.is_file()}


@pytest.mark.parametrize('damage',['declined','stop_image','dispatch_conflict'])
def test_scope_install_failure_preserves_scope_and_original_stop(installation,monkeypatch,damage):
    f=installation;tool=f['tool'];old=f['policy_path'].read_bytes()
    if damage=='declined':monkeypatch.setattr('builtins.input',lambda _:'decline')
    elif damage=='stop_image':
        path=f['root']/'kill-switch.sqlite3';path.write_bytes(path.read_bytes()+b'isolated changed source')
    else:(f['state']/'strategy-dispatch.json').write_text(json.dumps({'isolated_conflicting_dispatch':True}))
    stop=(f['root']/'kill-switch.sqlite3').read_bytes()
    with pytest.raises(ValueError):tool.install_approved(source_commit='c'*40,stored_request_sha256=_digest(f['request']))
    assert f['policy_path'].read_bytes()==old and (f['root']/'kill-switch.sqlite3').read_bytes()==stop
    assert not (f['policy_path'].parent/'strategy-approvals').exists()


def test_scope_human_policy_reaches_original_dispatch_risk_issuer_without_send(installation,tmp_path,monkeypatch):
    from tools import gold_strategy_dispatch as dispatcher,binance_demo_market_inputs as capture
    from tests.tools.test_gold_budget_scope_materials import checked
    from tests.tools.test_binance_demo_market_inputs import material
    from live.execution import demo_request_inputs as builder,demo_issuer as issuer
    from live.execution.demo_execution_session import ExecutionSession
    from live.execution.demo_issuer_store import IssuerStore
    from live.execution.demo_pre_submit import _state
    from services.validation import demo_submission
    from quantos.contracts.killswitch import KillSwitchState
    f=installation;tool=f['tool'];stores=f['stores']
    tool.install_approved(source_commit='c'*40,stored_request_sha256=_digest(f['request']))
    policy=json.loads(f['policy_path'].read_bytes());_,_,native=checked(f['parts'])
    assert native['policy']==f['scope']
    profile=f['policy_path'].parent/'profile.json';profile.write_bytes(tool.installer.canonical(native['profile']))
    monkeypatch.setattr(dispatcher,'PROFILE',profile)
    status=dispatcher.run(source_commit='c'*40,use_approved_policy=True,result_sha256=_digest(f['result']))
    assert status['status']=='TASK_READY',status
    task=json.loads((f['state']/'tasks'/(status['task_sha256']+'.json')).read_bytes())
    assert task['execution_constraints']==f['result']['decision']['execution_constraints']
    assert task['orders']==f['result']['decision']['draft']['orders']
    at=f['at']-10;data=material();data['ticker'].update(symbol='XAUUSDT',openTime=at-86400000,closeTime=at)
    delta=at//60000*60000-1-data['klines'][-1][6]
    for row in data['klines']:row[0]+=delta;row[6]+=delta
    ticks=iter(range(at,at+100))
    def get(kind,**kw):
        body={'serverTime':at+(6 if kind=='clock_after' else 0)} if kind.startswith('clock') else data[kind]
        return json.dumps(body,separators=(',',':')).encode()
    market=capture.collect(symbol='XAUUSDT',get=get,clock_ms=lambda:next(ticks))
    before=_state(stores['dispatch'],stores['recovery'],stores['oms'])
    built=builder.build(orders=task['orders'],origin=f['origin'],market_inputs=market,profile=task['profile'],
        issuer_policy=policy,kill_state=KillSwitchState.model_validate_json(json.dumps(task['kill_switch']),strict=True),
        **{name:stores[name] for name in ('dispatch','recovery','oms')},now_ms=f['at'],
        execution_constraints=task['execution_constraints'])
    session=ExecutionSession(**{name:stores[name] for name in ('dispatch','recovery','oms')},
        evidence_directory=tmp_path/'proposal')
    handoff=session.propose(tool.installer.canonical(built['request']),now_ms=f['at'])
    log=IssuerStore(tmp_path/'isolated-issuer.sqlite3',create=True)
    try:grant=issuer.issue_submission(handoff,policy=policy,registry=f['registry'],key=f['parts']['private_key'],
        store=log,now_ms=f['at']+1)
    finally:log.close()
    proof=demo_submission.verify(grant['envelope'],registry=f['registry'],
        expected_context=handoff['proposal']['context'],orders=task['orders'],now_ms=f['at']+2)
    assert proof['signature_verified'] is True
    assert before==_state(stores['dispatch'],stores['recovery'],stores['oms'])


@pytest.mark.parametrize('worker_exit',[0,73])
def test_finished_scope_controller_frame_allows_original_exclusive_human_approval(installation,tmp_path,monkeypatch,worker_exit):
    from tools import gold_strategy_controller as controller
    from tests.tools.test_demo_supervisor import child
    f=installation;tool=f['tool']
    # Keep the older isolated legacy review evidence intact. This controller
    # invocation starts from an independent root and a native executor frame.
    state=tmp_path/'native-controller';state.mkdir(mode=0o700)
    (state/'strategy-runtime.json').write_bytes((f['state']/'strategy-runtime.json').read_bytes())
    (state/'strategy-runtime.json').chmod(0o600)
    f['state']=state;monkeypatch.setattr(controller.supervisor,'STATE',state)
    f['result']=with_original_window(f,f['result'])
    from tools.gold_strategy_approval_request import prepare
    f['request']=prepare(f['result'],runtime=f['runtime'],policy=f['scope'],registry=f['registry'],now_ms=f['at'])
    retain_worker_original(f,f['result'],tmp_path=tmp_path,monkeypatch=monkeypatch)
    program='import json,sys\n'+f'assert json.loads(sys.stdin.buffer.readline())=={f["runtime"]!r}\n'
    program+=f'print({json.dumps(json.dumps(f["result"]))},flush=True)\n'
    program+=f'sys.exit({worker_exit})\n'
    process=child(program);confirmed=[];errors=[]
    monkeypatch.setattr(controller.demo_task,'preparation_command',lambda **kw:
        ['tools.demo_request_prepare','RuntimeMaxSec=155s'])
    monkeypatch.setattr(controller.subprocess,'Popen',lambda *args,**kw:process)
    monkeypatch.setattr(controller.subprocess,'run',lambda *args,**kw:None)
    def handoff(value):
        assert value==f['result'] and process.poll()==0
        try:result=tool.install_approved(source_commit='c'*40,stored_request_sha256=_digest(f['request']))
        except ValueError as exc:
            errors.append(str(exc));raise
        confirmed.append(result)
        return False
    result=controller.run(source_commit='c'*40,ticks=1,on_decision=handoff)
    if worker_exit:
        assert result['status']=='INPUTS_UNRESOLVED' and result['phase']=='STRATEGY_OBSERVATION'
        assert confirmed==[] and errors==[]
        assert json.loads(f['policy_path'].read_bytes())==f['scope']
        assert not (f['state']/'strategy-dispatch.json').exists()
        return
    assert result['status']=='DECISION_RETAINED',(result,errors)
    assert len(confirmed)==1 and confirmed[0]['installed'] is True
    assert json.loads(f['policy_path'].read_bytes())['schema']=='DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'


@pytest.mark.parametrize('field',['bar_continuation_sha256','bar_signal_sha256'])
@pytest.mark.parametrize('replacement',[None,'not-a-digest'])
def test_scope_missing_market_evidence_cannot_reach_human_confirmation(installation,monkeypatch,field,replacement):
    from live.execution.demo_pre_submit import _retain
    f=installation;result=deepcopy(f['result']);request=deepcopy(f['request']);tool=f['tool']
    if replacement is None:result['decision'].pop(field)
    else:result['decision'][field]=replacement
    result['decision_sha256']=_digest(result['decision'])
    request.update(result_sha256=_digest(result),decision=deepcopy(result['decision']))
    request['request_sha256']=_digest({k:v for k,v in request.items() if k!='request_sha256'})
    _retain(f['state']/'strategy-decisions',result)
    _retain(f['state']/'strategy-approval-requests',request)
    original=f['policy_path'].read_bytes()
    monkeypatch.setattr(tool,'operator_confirmation',lambda *a:pytest.fail('missing evidence reached human approval'))
    with pytest.raises(ValueError,match='APPROVAL_SCOPE_BAR_EVIDENCE'):
        tool.install_approved(source_commit='c'*40,stored_request_sha256=_digest(request))
    assert f['policy_path'].read_bytes()==original
    assert not (f['state']/'strategy-dispatch.json').exists()
    assert not (f['policy_path'].parent/'strategy-approvals').exists()


def test_expiry_after_first_dispatch_write_keeps_scope_inert_and_audit(installation,monkeypatch):
    from live.execution.demo_issuer_policy import current_key
    f=installation;tool=f['tool'];old=f['policy_path'].read_bytes();clock=[f['at']]
    original_write=tool.installer.write_new
    def write(path,body,**kwargs):
        original_write(path,body,**kwargs)
        if path==f['state']/'strategy-dispatch.json':clock[0]=f['request']['valid_until_ms']
    monkeypatch.setattr(tool.installer,'write_new',write)
    monkeypatch.setattr(tool.supervisor,'now_ms',lambda:clock[0])
    with pytest.raises(ValueError):
        tool.install_approved(source_commit='c'*40,stored_request_sha256=_digest(f['request']))
    assert f['policy_path'].read_bytes()==old
    with pytest.raises(ValueError):current_key(json.loads(old),f['registry'],clock[0])
    assert (f['state']/'strategy-dispatch.json').is_file()
    stages=list((f['policy_path'].parent/'strategy-approvals').iterdir())
    assert len(stages)==1 and (stages[0]/'previous-policy.json').read_bytes()==old
    assert (stages[0]/'operator-approval.json').is_file() and not (stages[0]/'result.json').exists()
    assert not (f['state']/'tasks').exists()
