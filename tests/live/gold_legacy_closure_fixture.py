"""Native non-BAR IOC completion for legacy feedback and pilot closure tests.

This is the legitimate unfilled-entry branch: original research/forward replay,
exact issuer/signature, ExecutionSession, expired IOC receipt/OMS, and original
cash build/export all run. Filled entry/owned exit is independently covered by
the AUTO BAR fixture. Venue/LLM, source acceptance and role custody are isolated;
no completed state is manufactured by relabeling BAR or removing native markers.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
from types import SimpleNamespace

import pytest

# Import readers before the original identity fixture installs test aliases.
from tools import demo_reported_recovery as reported
from tests.live import test_gold_demo_request_session as original_session
from tests.live.test_gold_demo_request_session import request_inputs, NOW
from tests.live.gold_feedback_recovery_fixture import _reported_io
from tests.tools.test_gold_forward_review import captured
from tests.tools.test_gold_strategy_entry import origin_at
from tests.tools.test_binance_demo_cash_window import Clock
from tests.research.test_binance_order_receipts import message
from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _retain, _state

legacy_case = original_session.case


def _private(root):
    for path in (root,*root.rglob('*')):
        assert not path.is_symlink()
        path.chmod(0o700 if path.is_dir() else 0o600)


def _cash(root,runtime,account,monkeypatch):
    from tools import gold_strategy_cash as cash
    clock=Clock();clock.ms=NOW-6000;reads=[]
    def get(path,**kwargs):
        at=clock.ms;clock.ms+=100;reads.append(path)
        if path=='/fapi/v1/time':value={'serverTime':at+50}
        elif path=='/fapi/v3/account':value={'assets':[dict(asset='USDT',walletBalance='5000',updateTime=NOW-7000)]}
        elif path=='/fapi/v1/income':value=[]
        else:raise AssertionError(path)
        return json.dumps(value).encode()
    def collect(actual,**kwargs):
        assert actual==account
        return cash.collector.collect(credential_file=None,operator_read=True,observe_seconds=30,
            credential_reader=lambda _:('synthetic-key','synthetic-secret'),get=get,
            clock_ms=clock.now,sleep=clock.sleep,**kwargs)
    monkeypatch.setattr(cash,'_collect',collect)
    monkeypatch.setattr(cash,'time',SimpleNamespace(time_ns=lambda:clock.ms*1000000))
    args=dict(bundle=runtime,account=account,source_commit=runtime['source_commit'])
    opened=cash.open_window(root,**args,observation_exists=False)
    assert len(reads)==2
    return cash,clock,reads,args,opened


@contextmanager
def legacy_feedback(case,tmp_path,monkeypatch):
    from live.execution import demo_gold_pilot_enrollment as enrollment, demo_position_context as position
    from live.execution import demo_issuer, demo_prepared_execution as executor, demo_oms_reconciliation as reconcile
    from live.execution.demo_execution_session import ExecutionSession
    from live.execution.demo_wire_protocol import outcome_for_query
    from live.execution.order_event_store import SqliteOrderEventStore
    from live.exchange import binance_demo_exact as transport
    from services.research.demo_user_stream import UserStreamJournal,normalize_message
    from services.validation.demo_submission import GOLD_BATCH_SCHEMA
    from tools import gold_strategy_cash, gold_strategy_cycle as cycle, gold_strategy_request as strategy_request
    from tools import gold_strategy_feedback as feedback, gold_strategy_worker as worker
    from tools.demo_resident_recovery import _check_path
    from tools.gold_pilot_retention_store import canonical

    private,values,_,registry,policy,issuer=case
    root=tmp_path;root.chmod(0o700)
    source=policy['executor_release_commit'];assert source=='c'*40
    stores={k:values[k] for k in ('dispatch','recovery','oms')}
    assert stores['dispatch'].path.parent==root and _state(**stores)==dict(reservations=[],recovery=[],oms=[],wire=[])
    account=values['origin']['account_ref_sha256']
    monkeypatch.setattr(position,'_ROOT',root)
    def custody(path,*,directory):
        path.relative_to(root)
        _check_path(path,os.getuid(),directory=directory)
    monkeypatch.setattr(position,'_custody',custody)
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit=source,
        account_ref_sha256=account,duration_seconds=86400,max_loss_fraction='0.05',submission_enabled=False)
    request_path=root/'gold-pilot-request.json';request_path.write_bytes(canonical(request));request_path.chmod(0o600)
    config=enrollment.enroll_requested(root=root,source_commit=source,origin=origin_at(NOW-7000),
        **stores,now_ms=NOW-7000)
    # The original case contains an unused unsigned fixture proposal. No grant,
    # reservation or actual signal proposal exists before this enrollment.
    with monkeypatch.context() as clock_patch:
        clock_patch.setattr(time,'time_ns',lambda:(NOW-4011)*1000000)
        observation,trial,definition=captured(root/'research',monkeypatch,protected_network=True)
    identity=hashlib.sha256((trial/'manifest.json').read_bytes()).hexdigest()
    runtime=dict(schema='GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1',source_commit=source,
        trial_manifest_sha256=identity,plan=json.loads((observation/'registration.json').read_bytes())['plan'])
    target_trial=root/'strategy-trials'/identity;shutil.copytree(trial,target_trial)
    cash,cash_clock,cash_reads,cash_args,opening=_cash(root,runtime,account,monkeypatch)
    target_observation=root/'strategy-observation-work'/_digest(runtime)
    shutil.copytree(observation,target_observation);_private(root)
    args=request_inputs(case);args['origin']=origin_at(NOW)
    rules=next(r['data'] for r in args['origin']['reads'] if r['kind']=='rules')
    for group in (rules['terms']['filters'],rules['venue_filters']):
        for item in group:
            if item['filterType']=='PRICE_FILTER':item['maxPrice']='200'
    step=cycle.prepare(target_observation,strategy_trial=target_trial,origin=args['origin'],**stores,now_ms=NOW+10)
    assert step['status']=='DECISION_REQUIRES_APPROVAL' and step['pilot_control']['status']=='ACTIVE'
    draft=step['draft'];profile=args['profile'];sid=definition['research_identity']['strategy_id']
    previous=next(iter(profile['attestations'].values()))
    profile['attestations']={sid:dict(previous,strategy_id=sid)};profile['strategy']['strategy_id']=sid
    profile['native_limits'].update(default_max_position_qty=0.2,fat_finger_qty=0.2)
    for name,key in (('native_limits','native_limits_sha256'),('strategy','strategy_approval_sha256'),
        ('mandate','mandate_sha256'),('attestations','validation_sha256')):policy['bindings'][key]=_digest(profile[name])
    policy['bindings']['strategy_id']=sid;policy['scope']['max_quantity_per_order']='0.2'
    policy['schema']='DEMO_GOLD_STRATEGY_BUDGET_POLICY_V1'
    policy['budget_group']=dict(group_id='synthetic-legacy-closure-budget',strategy_definition_sha256=definition['result_hash'],
        valid_from_ms=policy['valid_from_ms'],valid_until_ms=policy['valid_until_ms'],max_grants=2,max_orders=2,
        max_total_notional='30',max_cancel_grants=2,max_cancel_orders=2)
    policy['bindings']['execution_constraints_sha256']=_digest(strategy_request.constraints_from_draft(draft,now_ms=NOW+10))
    task=strategy_request.task_from_draft(draft,profile=profile,issuer_policy=policy,
        kill_state=args['kill_state'],source_commit=source,now_ms=NOW+10)
    args.pop('orders');built=strategy_request.prepare(draft,**args)
    session=ExecutionSession(**stores,evidence_directory=root/'evidence')
    handoff=session.propose(json.dumps(built['request']).encode(),now_ms=NOW+10)
    grant=demo_issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=issuer,now_ms=NOW+11)
    monkeypatch.setattr(executor,'now_ms',lambda:NOW+12)
    monkeypatch.setattr(executor,'_require_state_custody',lambda *a,**kw:None)
    monkeypatch.setattr(transport,'_credentials',lambda _:('synthetic-key','synthetic-secret'))
    order=draft['orders'][0];posts=[]
    monkeypatch.setattr(transport,'_http_once',lambda *a:(posts.append(a[0]) or 200,
        json.dumps(dict(symbol='XAUUSDT',clientOrderId=order['newClientOrderId'],orderId=123)).encode()))
    submitted=session.submit(grant['proposal_sha256'],json.dumps(grant['envelope']).encode());batch=submitted['prepared']
    stream=UserStreamJournal(root/'gold-stream.db',account_ref_sha256=account)
    events=SqliteOrderEventStore(str(root/'gold-events.db'),create=True)
    try:
        stream.start_session(source_sha256='a'*64,at_ms=NOW)
        fill=message(NOW+13,'EXPIRED','0','0',trade_id=0,asset='USDT')
        fill['o'].update(s='XAUUSDT',c=order['newClientOrderId'],i=123,q='0.1',p='100.1',
            L='0',f='IOC',m=False,R=False,er='0',x='EXPIRED')
        stream.record('MESSAGE',at_ms=NOW+14,message=normalize_message(json.dumps(fill)))
        query=dict(order,clientOrderId=order['newClientOrderId'],orderId=123,origQty='0.1',
            status='EXPIRED',executedQty='0',time=NOW+12,updateTime=NOW+13)
        for key in ('newClientOrderId','newOrderRespType','quantity'):query.pop(key)
        stores['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
            client_order_id=order['newClientOrderId'],requested_at_ms=NOW+30,
            outcome=outcome_for_query(order,200,json.dumps(query).encode(),schema=GOLD_BATCH_SCHEMA),now_ms=NOW+31)
        plan=reconcile.prepare_resolution(batch,dispatch=stores['dispatch'],stream=stream,start_ms=NOW+1,
            end_ms=NOW+1000,source_commit=source,output_directory=root/'receipt-reconciliation',as_of_ms=NOW+1001)
        assert plan['status']=='READY_FOR_OMS_PROJECTION'
        outcome=reconcile._project_resolution(plan,prepared=batch,**stores,event_store=events,now_ms=NOW+1001)
        _retain(root/'receipt-reconciliation/outcomes',outcome);_private(root)
        context=position.load_context(_state(**stores),symbol='XAUUSDT',account_ref=account,as_of_ms=NOW+2000)
        assert context['quantity']=='0' and context['canonical_wallet']=='5000' and stores['oms'].fills()==[]
        closing=origin_at(NOW+2000)
        decision=cycle.prepare(target_observation,strategy_trial=target_trial,origin=closing,**stores,now_ms=NOW+2000)
        assert decision['status']=='STRATEGY_EPISODE_SETTLED' and len(posts)==1
        cash_clock.ms=NOW+4000;capture=gold_strategy_cash.close_window(root,**cash_args)
        assert capture['reads'][:2]==opening['reads'] and len(cash_reads)==6
        _private(root)
        exported=feedback.export_retained(root,observation_directory=target_observation,strategy_trial=target_trial,
            bundle_sha256=_digest(runtime),account=account,source_commit=source,stores=dict(stores,stream=stream),
            as_of_ms=capture['completed_at_ms'])
        artifact_bytes=Path(exported['path']).read_bytes();artifact=json.loads(artifact_bytes)
        decision.update(source_commit=source,bundle_sha256=_digest(runtime),account_ref_sha256=account,origin_sha256=_digest(closing))
        _retain(root/'strategy-decisions',decision)
        result=dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1',status='DECISION_RETAINED',source_commit=source,
            bundle_sha256=_digest(runtime),decision_sha256=_digest(decision),decision=decision,feedback=exported,
            original_pilot_window=worker.original_pilot_window(root,decision=decision,source_commit=source,account=account),
            submission_enabled=False,economic_authority=False)
        f=SimpleNamespace(root=root,base_runtime=runtime,runtime=runtime,source_commit=source,account=account,
            executor_uid=os.getuid(),registry=registry,base_policy=deepcopy(policy),result=result,result_bytes=canonical(result),
            artifact=artifact,artifact_bytes=artifact_bytes,exported=exported,configuration=config,request=request,
            stores=stores,stream=stream,posts=posts,issuer_rows=issuer.snapshot(),task=task,batch=batch,grant=grant,
            observation=target_observation,trial=target_trial,capture=capture,cash_reads=cash_reads,
            context=context,now_ms=NOW,closing_origin=closing)
        # Reuse only the synthetic GET/process boundary, not BAR state. The
        # native origin collector used by closure still validates its 16 reads.
        _reported_io(f,monkeypatch);f.clock[0]=capture['completed_at_ms']
        _private(root)
        yield f
    finally:
        events.close();stream.db.close();session.close()


@pytest.fixture
def completed_legacy_feedback(legacy_case,tmp_path,monkeypatch):
    with legacy_feedback(legacy_case,tmp_path,monkeypatch) as value:
        yield value
