"""Bar decisions consume the original installed approval and task barrier.

Observations, authority and custody are explicit isolated fixtures. No exchange
or credentials are used; native rotation and task materialization remain real.
"""
from copy import deepcopy
import json
import os
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import budget_group_binding
from tests.live.test_gold_strategy_constraints import case as case,request_inputs,NOW
from tools.gold_strategy_request import constraints_from_draft


@pytest.fixture
def bar_dispatch(case,tmp_path,monkeypatch):
    from tools import gold_strategy_dispatch as tool,gold_strategy_approval_request as approval
    from tools import gold_bar_worker_inputs as retained, demo_operating_bundle as installer
    executor=tmp_path/'executor';executor.mkdir(mode=0o700)
    registry={'executor_uid':os.getuid()}
    monkeypatch.setattr(installer,'EXECUTOR',executor)
    args=request_inputs(case);policy=args['issuer_policy']
    policy['schema']='DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'
    policy['budget_group']=dict(group_id='isolated-bar-dispatch',strategy_definition_sha256='a'*64,
        **{k:policy[k] for k in ('valid_from_ms','valid_until_ms','max_grants','max_orders',
            'max_total_notional','max_cancel_grants','max_cancel_orders')})
    draft=dict(schema='GOLD_BAR_DEMO_ENTRY_DRAFT_V1',status='BAR_ENTRY_DRAFT',
        environment='BINANCE_USDM_DEMO',observed_at_ms=NOW,valid_until_ms=NOW+100,
        signal_valid_until_ms=NOW+100,definition_hash='a'*64,origin_sha256=_digest(args['origin']),
        orders=deepcopy(args['orders']),pilot_control=dict(status='ACTIVE',configuration_sha256='e'*64,
            submission_enabled=False),submission_enabled=False,economic_authority=False,risk_status='NOT_RUN')
    draft['result_hash']=_digest(draft)
    constraints=constraints_from_draft(draft,now_ms=NOW+10)
    policy['bindings']['execution_constraints_sha256']=_digest(constraints)
    for name in ('strategy-decisions','tasks','task-claims','task-results'):(tmp_path/name).mkdir(mode=0o700)
    profile=tmp_path/'profile.json';profile.write_text(json.dumps(args['profile']))
    monkeypatch.setattr(tool,'PROFILE',profile)
    monkeypatch.setattr(tool.supervisor,'STATE',tmp_path)
    monkeypatch.setattr(tool.supervisor,'configuration',lambda _:(registry,policy))
    monkeypatch.setattr(tool.supervisor,'_root_private_directory',lambda _:None)
    monkeypatch.setattr(tool.supervisor,'_read_root_owned_json',lambda path:path.read_bytes())
    clock=[NOW+10];monkeypatch.setattr(tool.supervisor,'now_ms',lambda:clock[0])
    monkeypatch.setattr(tool.checkpoint.bundle,'write_new',lambda path,body:path.write_bytes(body))
    prefix_calls=[];native_prefix=approval.review_bar_prefix
    def prefix(**kwargs):
        assert kwargs['root']==executor and kwargs['source_commit']=='c'*40
        assert kwargs['runtime']['schema'] in ('GOLD_BAR_FRESH_EPISODE_INPUT_V1','GOLD_BAR_AUTO_EPISODE_INPUT_V1')
        assert kwargs['decision']['bar_continuation_sha256']=='f'*64
        prefix_calls.append(deepcopy(kwargs))
        return {'isolated_original_prefix':'f'*64}
    monkeypatch.setattr(approval,'review_bar_prefix',prefix)
    signal={'isolated_original_signal':True}
    engine=SimpleNamespace(verified_definition=lambda:{'definition_hash':'a'*64},signal_at=lambda at:signal,
        _cursor=SimpleNamespace(last_received=NOW,bars=[]))
    monkeypatch.setattr(retained,'load',lambda *a,**k:(engine,{'isolated_original_archive':True}))
    config=dict(schema='GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1',source_commit='c'*40,
        account_ref_sha256=policy['account_ref_sha256'],budget_group_binding_sha256=budget_group_binding(policy),
        kill_switch=args['kill_state'].model_dump(mode='json'))
    (tmp_path/'strategy-dispatch.json').write_text(json.dumps(config))
    def write_runtime(schema,*,rotated=False):
        base=dict(schema=schema,source_commit='c'*40,trial_manifest_sha256='b'*64,archive_source_commit='d'*40)
        if schema=='GOLD_BAR_RETAINED_EPISODE_INPUT_V1':base.update(observation_sha256='e'*64,entry_decision_ms=NOW)
        else:
            base.update(candidate='channel',seconds=30)
            base['episode_id' if schema=='GOLD_BAR_AUTO_EPISODE_INPUT_V1' else 'history_sha256']='e'*64
        (tmp_path/'strategy-runtime.json').write_text(json.dumps(base));runtime=deepcopy(base)
        if rotated:
            from tools import gold_bar_rotation as rotation
            from tests.tools.test_gold_bar_rotation import outcome
            prior=outcome(base);prior['decision']['account_ref_sha256']=policy['account_ref_sha256']
            prior['decision_sha256']=_digest(prior['decision'])
            runtime=rotation.advance(root=tmp_path,base=base,account=policy['account_ref_sha256'],current=base,result=prior)
        decision=dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1',source_commit='c'*40,
            bundle_sha256=_digest(runtime),account_ref_sha256=policy['account_ref_sha256'],observed_at_ms=NOW,
            strategy_definition_sha256='a'*64,bar_signal_sha256=_digest(signal),
            status='DECISION_REQUIRES_APPROVAL',draft=deepcopy(draft),execution_constraints=deepcopy(constraints),
            submission_enabled=False,economic_authority=False)
        if schema!='GOLD_BAR_RETAINED_EPISODE_INPUT_V1':decision['bar_continuation_sha256']='f'*64
        result=dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1',status='DECISION_RETAINED',source_commit='c'*40,
            bundle_sha256=_digest(runtime),decision_sha256=_digest(decision),decision=decision,
            submission_enabled=False,economic_authority=False)
        identity=_digest(result);path=tmp_path/'strategy-decisions'/(identity+'.json')
        path.write_text(json.dumps(result))
        return runtime,result,identity,path
    return dict(tool=tool,args=args,policy=policy,draft=draft,constraints=constraints,
        root=tmp_path,clock=clock,prefix_calls=prefix_calls,write_runtime=write_runtime,approval=approval,
        native=case,native_prefix=native_prefix,executor=executor,registry=registry)


@pytest.mark.parametrize('schema',['GOLD_BAR_RETAINED_EPISODE_INPUT_V1',
    'GOLD_BAR_FRESH_EPISODE_INPUT_V1','GOLD_BAR_AUTO_EPISODE_INPUT_V1'])
def test_approved_bar_runtime_reaches_original_materializer(bar_dispatch,schema):
    f=bar_dispatch;runtime,result,identity,_=f['write_runtime'](schema)
    before=deepcopy(f['policy']);config=(f['root']/'strategy-dispatch.json').read_bytes()
    status=f['tool'].run(source_commit='c'*40,use_approved_policy=True,result_sha256=identity)
    assert status['status']=='TASK_READY',status
    task=json.loads(next((f['root']/'tasks').iterdir()).read_bytes())
    assert task['execution_constraints']==f['constraints'] and task['orders']==f['draft']['orders']
    assert f['policy']==before and (f['root']/'strategy-dispatch.json').read_bytes()==config
    assert not list((f['root']/'task-claims').iterdir())
    assert f['args']['dispatch'].snapshot()==[] and f['args']['oms'].orders()==[]


def test_current_rotated_bar_is_selected_without_rewriting_observation_or_rotation(bar_dispatch):
    f=bar_dispatch;runtime,result,identity,_=f['write_runtime']('GOLD_BAR_AUTO_EPISODE_INPUT_V1',rotated=True)
    before={p:p.read_bytes() for p in f['root'].rglob('*') if p.is_file()}
    assert runtime['episode_id']!='e'*64
    status=f['tool'].run(source_commit='c'*40,use_approved_policy=True,result_sha256=identity)
    assert status['status']=='TASK_READY',status
    assert all(p.read_bytes()==body for p,body in before.items())
    assert all(call['runtime']==runtime for call in f['prefix_calls'])


def test_stale_episode_result_is_rejected_even_with_exact_policy_approval(bar_dispatch):
    f=bar_dispatch;runtime,result,_,_=f['write_runtime']('GOLD_BAR_AUTO_EPISODE_INPUT_V1',rotated=True)
    base=json.loads((f['root']/'strategy-runtime.json').read_bytes())
    assert base!=runtime
    result['bundle_sha256']=result['decision']['bundle_sha256']=_digest(base)
    result['decision_sha256']=_digest(result['decision']);identity=_digest(result)
    (f['root']/'strategy-decisions'/(identity+'.json')).write_text(json.dumps(result))
    status=f['tool'].run(source_commit='c'*40,use_approved_policy=True,result_sha256=identity)
    assert status['status']=='INPUTS_UNRESOLVED' and status['phase']=='RETAINED_DECISION'
    assert not list((f['root']/'tasks').iterdir()) and f['prefix_calls']==[]


@pytest.mark.parametrize('schema',['GOLD_BAR_RETAINED_EPISODE_INPUT_V1',
    'GOLD_BAR_FRESH_EPISODE_INPUT_V1','GOLD_BAR_AUTO_EPISODE_INPUT_V1'])
def test_bar_lost_response_consumes_only_original_claim_once(bar_dispatch,monkeypatch,schema):
    f=bar_dispatch;_,_,identity,_=f['write_runtime'](schema);calls=[]
    def lost(**kwargs):
        calls.append(kwargs)
        f['tool'].supervisor.claim(f['root']/'task-claims',{'task_sha256':kwargs['task_sha256']},
            identity_field='task_sha256')
        raise OSError('isolated lost response after original claim')
    monkeypatch.setattr(f['tool'].demo_task,'run',lost)
    first=f['tool'].run(source_commit='c'*40,use_approved_policy=True,result_sha256=identity,execute=True)
    assert first['status']=='INPUTS_UNRESOLVED' and first['phase']=='ORIGINAL_TASK'
    again=f['tool'].run(source_commit='c'*40,use_approved_policy=True,result_sha256=identity,execute=True)
    assert again['status']=='QUERY_ONLY' and len(calls)==1
    assert len(list((f['root']/'tasks').iterdir()))==len(list((f['root']/'task-claims').iterdir()))==1


def test_bar_exact_approval_mismatch_waits_without_reading_prefix_or_creating_task(bar_dispatch):
    f=bar_dispatch;_,_,identity,_=f['write_runtime']('GOLD_BAR_AUTO_EPISODE_INPUT_V1')
    f['policy']['bindings']['execution_constraints_sha256']='0'*64
    status=f['tool'].run(source_commit='c'*40,use_approved_policy=True,result_sha256=identity,execute=True)
    assert status['status']=='WAITING_INDEPENDENT_APPROVAL'
    assert f['prefix_calls']==[] and not list((f['root']/'tasks').iterdir())


@pytest.mark.parametrize('damage',['prefix_changed','prefix_unavailable','deadline','result','runtime','profile','rotation'])
def test_bar_review_conflict_or_expiry_stops_before_materialization(bar_dispatch,monkeypatch,damage):
    f=bar_dispatch;runtime,result,identity,path=f['write_runtime']('GOLD_BAR_AUTO_EPISODE_INPUT_V1')
    counter=[]
    def prefix(**kwargs):
        counter.append(1)
        if damage=='prefix_unavailable':raise ValueError('isolated missing original prefix')
        if len(counter)==2:
            if damage=='deadline':f['clock'][0]=NOW+100
            elif damage=='result':path.write_text('{}')
            elif damage=='runtime':(f['root']/'strategy-runtime.json').write_text('{}')
            elif damage=='profile':f['tool'].PROFILE.write_text('{}')
            elif damage=='rotation':
                from tools import gold_bar_rotation as rotation
                from tests.tools.test_gold_bar_rotation import outcome
                prior=outcome(runtime);prior['decision']['account_ref_sha256']=f['policy']['account_ref_sha256']
                prior['decision_sha256']=_digest(prior['decision'])
                rotation.advance(root=f['root'],base=runtime,account=f['policy']['account_ref_sha256'],
                    current=runtime,result=prior)
        return {'isolated_original_prefix':len(counter) if damage=='prefix_changed' else True}
    monkeypatch.setattr(f['approval'],'review_bar_prefix',prefix)
    status=f['tool'].run(source_commit='c'*40,use_approved_policy=True,result_sha256=identity,execute=True)
    assert status['status']=='INPUTS_UNRESOLVED',status
    assert not list((f['root']/'tasks').iterdir()) and not list((f['root']/'task-claims').iterdir())


@pytest.mark.parametrize('damage',['unknown_schema','extra_path','seconds_bool','candidate','archive_id','future_entry'])
def test_invalid_bar_runtime_never_reaches_original_materializer(bar_dispatch,damage):
    f=bar_dispatch;_,_,identity,_=f['write_runtime']('GOLD_BAR_AUTO_EPISODE_INPUT_V1')
    path=f['root']/'strategy-runtime.json';runtime=json.loads(path.read_bytes())
    if damage=='unknown_schema':runtime['schema']='GOLD_BAR_ARBITRARY_INPUT'
    elif damage=='extra_path':runtime['script_path']='/isolated/unsafe.py'
    elif damage=='seconds_bool':runtime['seconds']=True
    elif damage=='candidate':runtime['candidate']='unregistered-model'
    elif damage=='archive_id':runtime['archive_source_commit']='../arbitrary'
    else:
        runtime=dict(schema='GOLD_BAR_RETAINED_EPISODE_INPUT_V1',source_commit='c'*40,
            trial_manifest_sha256='b'*64,archive_source_commit='d'*40,observation_sha256='e'*64,entry_decision_ms=True)
    path.write_text(json.dumps(runtime))
    status=f['tool'].run(source_commit='c'*40,use_approved_policy=True,result_sha256=identity,execute=True)
    assert status['status']=='INPUTS_UNRESOLVED'
    assert not list((f['root']/'tasks').iterdir()) and f['prefix_calls']==[]


def test_materialized_bar_task_traverses_native_risk_issuer_and_signature_without_send(bar_dispatch,tmp_path):
    from live.execution import demo_request_inputs as builder,demo_issuer as issuer
    from live.execution.demo_execution_session import ExecutionSession
    from services.validation import demo_submission
    f=bar_dispatch;_,_,identity,_=f['write_runtime']('GOLD_BAR_AUTO_EPISODE_INPUT_V1',rotated=True)
    status=f['tool'].run(source_commit='c'*40,use_approved_policy=True,result_sha256=identity)
    assert status['status']=='TASK_READY'
    task=json.loads(next((f['root']/'tasks').iterdir()).read_bytes());args=dict(f['args'])
    args.update(issuer_policy=f['policy'],profile=task['profile'],orders=task['orders'],
        execution_constraints=task['execution_constraints'],now_ms=NOW+10)
    built=builder.build(**args)
    session=ExecutionSession(**{k:args[k] for k in ('dispatch','recovery','oms')},
        evidence_directory=tmp_path/'original-proposals')
    handoff=session.propose(json.dumps(built['request']).encode(),now_ms=NOW+10)
    private,_,_,registry,_,log=f['native']
    grant=issuer.issue_submission(handoff,policy=f['policy'],registry=registry,key=private,store=log,now_ms=NOW+11)
    proof=demo_submission.verify(grant['envelope'],registry=registry,
        expected_context=handoff['proposal']['context'],orders=task['orders'],now_ms=NOW+11)
    assert proof['signature_verified'] is True
    assert grant['envelope']['payload']['expires_at_ms']==NOW+100
    assert args['dispatch'].snapshot()==[] and args['oms'].orders()==[]


def test_dispatch_reads_native_frozen_tail_without_recapture_or_observation_mutation(bar_dispatch,monkeypatch):
    from tests.tools.test_gold_bar_episode_tail import engine as original_engine,message,STEP
    from tools import gold_bar_fresh_inputs as fresh
    from tools.gold_bar_episode_tail import EpisodeTail
    f=bar_dispatch;runtime,result,_,_=f['write_runtime']('GOLD_BAR_AUTO_EPISODE_INPUT_V1')
    engine,entry=original_engine(monkeypatch)
    tail=EpisodeTail(f['executor'],bundle_sha256=_digest(runtime),engine=engine,entry_decision_ms=entry)
    raw,at=message(1001*STEP,'90');tail.append(raw,received_at_ms=at)
    _,snapshot,_=tail.snapshot();calls=[]
    def episode(root,bundle,**kwargs):
        assert kwargs['read_only'] is True and 'cash_account' not in kwargs
        assert root==f['executor'] and bundle==runtime
        calls.append(1)
        return SimpleNamespace(engine=engine,entry_decision_ms=entry,recheck=lambda:{'isolated_archive_custody':True})
    monkeypatch.setattr(fresh,'FreshEpisode',episode)
    monkeypatch.setattr(f['approval'],'review_bar_prefix',f['native_prefix'])
    decision=result['decision'];draft=decision['draft'];draft['definition_hash']='d'*64
    draft['result_hash']=_digest({k:v for k,v in draft.items() if k!='result_hash'})
    constraints=constraints_from_draft(draft,now_ms=NOW+10)
    decision.update(strategy_definition_sha256='d'*64,bar_signal_sha256=_digest(engine.signal_at(entry)),
        bar_continuation_sha256=snapshot,execution_constraints=constraints)
    result['decision_sha256']=_digest(decision);identity=_digest(result)
    f['policy']['budget_group']['strategy_definition_sha256']='d'*64
    f['policy']['bindings']['execution_constraints_sha256']=_digest(constraints)
    config=json.loads((f['root']/'strategy-dispatch.json').read_bytes())
    config['budget_group_binding_sha256']=budget_group_binding(f['policy'])
    (f['root']/'strategy-dispatch.json').write_text(json.dumps(config))
    (f['root']/'strategy-decisions'/(identity+'.json')).write_text(json.dumps(result))
    # Later public messages do not replace the signed original decision prefix.
    raw,later=message(1002*STEP);tail.append(raw,received_at_ms=later)
    before={p:p.read_bytes() for p in f['root'].rglob('*') if p.is_file()}
    status=f['tool'].run(source_commit='c'*40,use_approved_policy=True,result_sha256=identity)
    assert status['status']=='TASK_READY',status
    assert len(calls)==2 and all(p.read_bytes()==body for p,body in before.items())
    task=json.loads(next((f['root']/'tasks').iterdir()).read_bytes())
    assert task['execution_constraints']==constraints
