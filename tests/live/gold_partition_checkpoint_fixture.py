"""Original BTC installation followed by real Gold entry/exit and receipt replay.

Only synthetic host identities and external venue bytes are isolated. Both
markets use the original installed stores; no signed history is replaced.
"""
from contextlib import closing
from copy import deepcopy
from decimal import Decimal
import json
import hashlib
import os
from pathlib import Path
import shutil
import stat
from types import SimpleNamespace

import pytest

from live.execution.demo_batch_risk import _digest
from live.execution import demo_authority
_ROOT_READER=demo_authority._read_root_owned_json
# Import native checkpoint custody aliases before any synthetic installer
# patches exist; bind_checkpoint_public remaps them separately for each case.
from tools import demo_gold_namespace_checkpoint as inventory
from tools import demo_gold_historical_position as historical
from tools import demo_gold_checkpoint_components as components
from tools import demo_gold_checkpoint_transport as checkpoint_transport
from tools import gold_bar_auto_history as auto_history
from tools import gold_pilot_closure as closure
from tests.live.test_gold_first_scope_delivery import (
    first_scope_archive as first_scope_archive,
    cold_scope_local as original_cold_scope_local,
    installed_first_scope as installed_first_scope,
    native_scope as native_scope,
)


@pytest.fixture
def cold_scope_local(tmp_path, monkeypatch, first_scope_archive, request):
    from live.execution.order_event_store import SqliteOrderEventStore
    from live.execution.demo_execution_session import ExecutionSession
    from tests.live import test_gold_first_scope_original_history as history_fixture
    from tests.live import test_demo_reported_recovery as account_fixture
    # Original constructors also create synthetic account/credential inputs.
    # Keep those external fixture inputs intact outside the executor inventory;
    # the native retained proof/plan already contains their full public bodies.
    original_factory=history_fixture.native_old_operator_materials
    def factory(**kwargs):
        result=original_factory(**kwargs)
        shutil.move(kwargs['root']/'input-materials',tmp_path/'external-operator-inputs')
        return result
    monkeypatch.setattr(history_fixture,'native_old_operator_materials',factory)
    original_inputs=account_fixture.inputs
    def account_inputs(directory):
        value=original_inputs(directory)
        shutil.move(directory,tmp_path/'external-account-inputs')
        return value
    monkeypatch.setattr(account_fixture,'inputs',account_inputs)
    original_session=ExecutionSession.__init__
    def session(self,*,evidence_directory,**kwargs):
        directory=Path(evidence_directory)
        if directory.name=='unsigned-evidence' and tmp_path in directory.parents:
            directory=tmp_path/'external-unsigned-proposals'
        original_session(self,evidence_directory=directory,**kwargs)
    monkeypatch.setattr(ExecutionSession,'__init__',session)
    original = SqliteOrderEventStore.__init__
    def fixed_name(self, path, *args, **kwargs):
        path=Path(path)
        if tmp_path in path.parents and path.name=='events.sqlite3':
            path=path.with_name('order-events.sqlite3')
        return original(self,str(path),*args,**kwargs)
    monkeypatch.setattr(SqliteOrderEventStore,'__init__',fixed_name)
    yield from original_cold_scope_local.__wrapped__(tmp_path,monkeypatch,first_scope_archive,request)


def _project(f, batch, *, at, price, venue_id, trade_id):
    from live.execution import demo_oms_reconciliation as reconciliation, demo_position_context as position
    from live.execution.demo_pre_submit import _retain, _state
    from live.execution.demo_wire_protocol import outcome_for_query
    from services.research.demo_user_stream import normalize_message
    from tests.research.test_binance_order_receipts import message
    order,=batch['orders'];quantity=order['quantity']
    fee=str(Decimal(price)*Decimal(quantity)*Decimal('.00045'))
    fill=message(at+13,'FILLED',quantity,quantity,trade_id=trade_id,fee=fee,asset='USDT')
    pnl='0' if order['side']=='BUY' else str((Decimal(price)-Decimal(f.context['average_entry_price']))*Decimal(quantity))
    fill['o'].update(s='XAUUSDT',c=order['newClientOrderId'],i=venue_id,q=quantity,p=order['price'],
        L=price,S=order['side'],f=order['timeInForce'],m=False,R=order['reduceOnly'],er='0',rp=pnl)
    f.stream.record('MESSAGE',at_ms=at+14,message=normalize_message(json.dumps(fill)))
    query=dict(order,clientOrderId=order['newClientOrderId'],orderId=venue_id,origQty=quantity,
        status='FILLED',executedQty=quantity,time=at+2,updateTime=at+13)
    for key in ('newClientOrderId','newOrderRespType','quantity'):query.pop(key)
    f.stores['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
        client_order_id=order['newClientOrderId'],requested_at_ms=at+30,
        outcome=outcome_for_query(order,200,json.dumps(query).encode(),schema='DEMO_GOLD_EXACT_ORDER_BATCH_V1'),now_ms=at+31)
    plan=reconciliation.prepare_resolution(batch,dispatch=f.stores['dispatch'],stream=f.stream,
        start_ms=at,end_ms=at+1000,source_commit=f.source_commit,
        output_directory=f.root/'receipt-reconciliation',as_of_ms=at+1001)
    assert plan['status']=='READY_FOR_OMS_PROJECTION',plan
    outcome=reconciliation._project_resolution(plan,prepared=batch,**f.stores,event_store=f.events,now_ms=at+1001)
    _retain(f.root/'receipt-reconciliation/outcomes',outcome)
    f.clock[0]=at+1002
    f.context=position.load_context(_state(**f.stores),symbol='XAUUSDT',account_ref=f.account,as_of_ms=f.clock[0])
    return plan


def _origin(f, at, price):
    from tests.live.test_demo_market_partition_runtime import shift
    from services.research.demo_account_baseline import normalize_response
    origin=shift(f.entry_origin,at-1-f.entry_origin['completed_at_ms'])
    wallet=f.context['canonical_wallet'];qty=Decimal(f.context['quantity'])
    pnl=qty*(Decimal(price)-Decimal(f.context['average_entry_price']))
    for row in origin['reads']:
        kind,data=row['kind'],row['data']
        if kind=='book':data.update(time=at-1,bidPrice=price,askPrice=str(Decimal(price)+Decimal('.1')))
        elif kind=='mark':data.update(time=at-1,markPrice=price,indexPrice=price)
        elif kind.startswith('account'):
            asset=next(a for a in data['assets'] if a['asset']=='USDT')
            asset.update(walletBalance=wallet,crossWalletBalance=wallet,availableBalance=wallet,
                unrealizedProfit=str(pnl),marginBalance=str(Decimal(wallet)+pnl))
            data['positions'].append(dict(symbol='XAUUSDT',positionSide='BOTH',positionAmt=str(qty)))
            row['data']=normalize_response(kind,data)
        elif kind=='positions':
            data.append(dict(symbol='XAUUSDT',positionSide='BOTH',positionAmt=str(qty),
                entryPrice=f.context['average_entry_price'],markPrice=price,unRealizedProfit=str(pnl),
                isolatedWallet='0',marginAsset='USDT',updateTime=f.context['position_episode']['opened_venue_at_ms']))
            row['data']=normalize_response(kind,data)
    return origin


def _retain(f, origin, decision, at):
    from tools.gold_bar_pilot_capacity import original_context
    from tools.gold_pilot_retention_store import Retention
    from tools.gold_strategy_worker import original_pilot_window
    decision=deepcopy(decision)
    decision.update(source_commit=f.source_commit,bundle_sha256=_digest(f.base_runtime),
        account_ref_sha256=f.account,origin_sha256=_digest(origin))
    result=dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1',status='DECISION_RETAINED',source_commit=f.source_commit,
        bundle_sha256=_digest(f.base_runtime),decision_sha256=_digest(decision),decision=decision,
        submission_enabled=False,economic_authority=False,original_pilot_window=original_pilot_window(
            f.root,decision=decision,source_commit=f.source_commit,account=f.account))
    original=original_context(f.root,f.base_runtime,uid=f.executor_uid,account=f.account)
    with Retention(f.root,original=original,base_runtime=f.base_runtime,uid=f.executor_uid,role='executor') as store:
        claim=store.claim(bundle=f.base_runtime,now_ms=at)
        for kind,value in (('origin',origin),('decision',decision),('result',result)):store.put(claim,kind,value)
    return result


def bind_checkpoint_public(f, tmp_path, monkeypatch):
    """Capture the actual installed fixed originals, with isolated UID metadata."""
    from tools import demo_gold_checkpoint_public as fixed, demo_supervisor as supervisor
    from tools import demo_market_partition_install as installed, gold_bar_definition as definition
    from tools import perpetual_replay_research as replay, demo_operating_bundle as operating
    from tests.live.gold_checkpoint_fixture import git
    from tools import demo_resident_recovery as custody
    from tools import gold_strategy_controller as controller
    # The original installed-scope custody fixture permits only this isolated
    # test tree. Keep native checkpoint scratch directories in that same tree.
    monkeypatch.setattr(inventory.tempfile,'tempdir',str(tmp_path))
    # Both modules import the original custody callable. Refresh aliases for
    # this test's synthetic root instead of keeping an earlier fixture's root.
    for module in (inventory,historical,components,auto_history,closure):
        monkeypatch.setattr(module,'_check_path',custody._check_path)
    source=tmp_path/'checkpoint-original-source'
    git('clone','--no-local',str(f.archive.source),str(source),cwd=tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(replay,'ROOT',source)
        identity=replay.source_identity()
    bundle=tmp_path/'partition-current-source.bundle'
    git('bundle','create',str(bundle),'HEAD',cwd=source)
    f.source_attachment=dict(schema='GOLD_CHECKPOINT_VERIFIED_SOURCE_V1',source_root=str(source),
        bundle_path=str(bundle),bundle_sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),identity=identity)
    paths=dict(executor_registry=f.local['public'],issuer_registry=f.local['cfg']/'authority.json',
        policy=f.local['cfg']/'policy.json',executor_partition=installed.EXECUTOR_RECORD,
        issuer_partition=installed.ISSUER_RECORD,partition_intent=installed.INTENT,activation=installed.ACTIVATION)
    releases=tmp_path/'checkpoint-releases';evidence=releases/f.source_commit/'evidence/l2_release_evidence_manifest.json'
    evidence.parent.mkdir(parents=True,mode=0o700)
    evidence.write_text(json.dumps(dict(schema_version=1,readiness='L2_DRY_RUN_ONLY',source_commit=f.source_commit)))
    evidence.chmod(0o644)
    monkeypatch.setattr(fixed,'PATHS',paths);monkeypatch.setattr(fixed,'RELEASES',releases)
    monkeypatch.setattr(fixed,'PARTITION_HISTORY',f.local['ctl']/'market-partition-installations')
    proxy=SimpleNamespace(**dict(vars(os),geteuid=lambda:0))
    monkeypatch.setattr(fixed,'os',proxy);monkeypatch.setattr(controller,'os',proxy)
    monkeypatch.setattr(supervisor,'now_ms',lambda:f.clock[0])
    monkeypatch.setattr(supervisor,'verify_source',lambda commit:definition.verify_source(f.archive.source,identity)
        if commit==f.source_commit else pytest.fail('wrong source'))
    parents={p.stat().st_ino for leaf in (*paths.values(),evidence) for p in leaf.parents}
    gids={path.stat().st_ino:(f.registry['executor_uid'] if name.startswith('executor') else
        0 if name in ('partition_intent','activation') else f.registry['issuer_uid']) for name,path in paths.items()}
    gids[evidence.stat().st_ino]=0
    def virtual(info):
        data={name:getattr(info,name) for name in fixed._STAT};data.update(st_uid=0,st_gid=gids.get(info.st_ino,0))
        if info.st_ino in parents:data['st_mode'] &= ~0o022
        return SimpleNamespace(**data)
    native_identity=fixed._identity
    monkeypatch.setattr(fixed,'_identity',lambda info:native_identity(virtual(info)))
    def protected(info,*,directory):
        assert stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        assert not info.st_mode&0o022 or directory and info.st_ino in parents
        if not directory:assert info.st_nlink==1
    for module in (fixed,demo_authority,supervisor):monkeypatch.setattr(module,'_require_protected',protected)
    def reader(path,*,file_check=None):
        return _ROOT_READER(path,file_check=None if file_check is None else lambda info:file_check(virtual(info)))
    for module in (fixed,supervisor):monkeypatch.setattr(module,'_read_root_owned_json',reader)
    # Keep the original private decoder/size/mode checks; only root ownership
    # and configured group identities are mapped to this synthetic installation.
    metadata=operating.private_metadata
    monkeypatch.setattr(operating,'private_metadata',lambda info,maximum:metadata(virtual(info),maximum=maximum))
    public=dict(schema='GOLD_CHECKPOINT_PUBLIC_CONTEXT_V1',source_commit=f.source_commit,
        registry=f.registry,account_ref_sha256=f.account,base_runtime=f.base_runtime,
        current_runtime=f.base_runtime,records=dict(rotation=dict(base=f.base_runtime,current=f.base_runtime,records=[],capacity=None)))
    public['records']['pilot']={name:json.loads((f.root/path).read_bytes()) for name,path in (
        ('request','gold-pilot-request.json'),('configuration','gold-pilot/configuration.json'),
        ('intent','gold-pilot/enrollment-intent.json'),('receipt','gold-pilot/enrollment.json'))}
    public['records']['pilot']['stop']=None
    cut=dict(schema='GOLD_CHECKPOINT_CUT_REQUEST_V1',operation_sha256='e'*64,source_commit=f.source_commit,
        registry_sha256=_digest(f.registry),public_bundle_sha256=_digest(public),roles=['executor','controller','issuer'],
        writer_barrier_sha256='f'*64,opening_at_ms=f.clock[0],submission_enabled=False,economic_authority=False)
    monkeypatch.setitem(inventory.ROLE_ROOTS,'executor',f.root)
    monkeypatch.setattr(inventory,'_role_uid',lambda registry,role:f.executor_uid)
    monkeypatch.setattr(inventory,'os',SimpleNamespace(**dict(vars(os),
        geteuid=lambda:f.executor_uid,getegid=lambda:f.executor_uid,
        getgroups=lambda:[f.executor_uid])))
    monkeypatch.setattr(checkpoint_transport,'os',inventory.os)
    monkeypatch.setattr(inventory,'time',SimpleNamespace(**dict(vars(inventory.time),
        time_ns=lambda:f.clock[0]*1_000_000)))
    args=dict(namespace='executor',source_commit=f.source_commit,operation_sha256='e'*64,
        cut_request=cut,public_bundle=public,source_attachment=f.source_attachment,guard=lambda:None)
    snapshot=fixed.capture(source_commit=f.source_commit,operation_sha256='e'*64,account_ref_sha256=f.account,guard=lambda:None)
    assert snapshot['partition_status']=='INSTALLED'
    args['public_bundle']['records']['fixed_public']=snapshot
    args['cut_request']['public_bundle_sha256']=_digest(args['public_bundle'])
    f.verified_public=fixed.bind_captured(snapshot,public_bundle=args['public_bundle'],cut_request=args['cut_request'],guard=lambda:None)
    f.history_public=deepcopy(args['public_bundle']);f.history_arguments=dict(args,verified_public=f.verified_public)
    return f


def append_exit_query(f):
    """Append a real later original-ID query and replay the same receipt IDs."""
    from live.execution import demo_oms_reconciliation as reconciliation, demo_position_context as position
    from live.execution.demo_pre_submit import _retain, _state
    row=next(row for row in reversed(f.stores['dispatch'].evidence_snapshot()['wire'])
        if row['kind']=='QUERY_RESULT' and row['reservation_sha256']==f.exit_batch['reservation_sha256'])
    f.stores['dispatch'].record_query_result(reservation_sha256=row['reservation_sha256'],
        client_order_id=row['client_order_id'],requested_at_ms=f.clock[0]+1,
        outcome=deepcopy(row['payload']['outcome']),now_ms=f.clock[0]+2)
    f.clock[0]+=3
    at=f.now_ms+2000
    plan=reconciliation.prepare_resolution(f.exit_batch,dispatch=f.stores['dispatch'],stream=f.stream,
        start_ms=at,end_ms=at+1000,source_commit=f.source_commit,output_directory=f.root/'receipt-reconciliation',as_of_ms=f.clock[0])
    assert plan['status']=='READY_FOR_OMS_PROJECTION',plan
    outcome=reconciliation._project_resolution(plan,prepared=f.exit_batch,**f.stores,event_store=f.events,now_ms=f.clock[0])
    _retain(f.root/'receipt-reconciliation/outcomes',outcome)
    f.context=position.load_context(_state(**f.stores),symbol='XAUUSDT',account_ref=f.account,as_of_ms=f.clock[0])
    return plan


@pytest.fixture
def installed_partition_checkpoint(installed_first_scope, first_scope_archive, native_scope, tmp_path, monkeypatch):
    from tests.live.test_gold_first_scope_exact_continuity import native_exact_case
    from live.execution.demo_execution_session import ExecutionSession
    from live.execution import demo_issuer, demo_prepared_execution as executor, demo_session_inputs
    from live.execution.demo_issuer_store import IssuerStore
    from live.exchange import binance_demo_exact as transport
    from services.research.demo_user_stream import UserStreamJournal
    from tools.gold_bar_fresh_inputs import FreshEpisode
    from tools.gold_bar_episode_tail import EpisodeTail
    from tools.gold_bar_demo_cycle import prepare
    from tools import gold_strategy_approval_request as approval
    from tests.live.test_demo_execution_session import request
    captured=[];native_propose=ExecutionSession.propose
    def capture(self,raw,**kw):
        value=native_propose(self,raw,**kw);captured.append(raw);return value
    with monkeypatch.context() as patch:
        patch.setattr(ExecutionSession,'propose',capture)
        initial=native_exact_case(installed_first_scope,native_scope,tmp_path,patch)
    local=installed_first_scope['local'];root=local['root'];runtime=deepcopy(local['parts']['runtime'])
    f=SimpleNamespace(root=root,local=local,installed=installed_first_scope,registry=initial.registry,
        base_policy=initial.policy,private=initial.key,ledger=installed_first_scope['history']['issuer_path'],
        stores=local['value']['stores'],events=local['value']['events'],clock=local['clock'],
        base_runtime=runtime,current_runtime=runtime,source_commit=runtime['source_commit'],
        account=initial.scope['account_ref_sha256'],executor_uid=os.stat(root).st_uid,
        archive=first_scope_archive,entry_origin=deepcopy(initial.handoff['proposal']['origin']))
    raw=captured[-1];values=demo_session_inputs.decode(raw,now_ms=initial.now_ms)
    f.proposal_values=values;f.now_ms=initial.now_ms
    f.episode=FreshEpisode(root,runtime,source_commit=f.source_commit,uid=f.executor_uid,read_only=True)
    f.engine=f.episode.engine;f.entry_decision_ms=f.episode.entry_decision_ms
    f.entry_result=_retain(f,f.entry_origin,initial.result['decision'],initial.result['decision']['observed_at_ms'])
    f.stream=UserStreamJournal(root/'user-stream.sqlite3',account_ref_sha256=f.account)
    f.stream.start_session(source_sha256='a'*64,at_ms=f.now_ms)
    posts=[]
    monkeypatch.setattr(executor,'_require_state_custody',lambda *args,**kw:None)
    monkeypatch.setattr(executor,'now_ms',lambda:f.clock[0])
    monkeypatch.setattr(transport,'_credentials',lambda _:('isolated-key','isolated-secret'))
    def post(body,headers):
        from urllib.parse import parse_qs
        args=parse_qs(body);posts.append(args)
        return 200,json.dumps(dict(symbol='XAUUSDT',clientOrderId=args['newClientOrderId'][0],orderId=122+len(posts))).encode()
    monkeypatch.setattr(transport,'_http_once',post)
    def send(raw,policy,at):
        f.clock[0]=at
        with closing(ExecutionSession(**f.stores,evidence_directory=root/'evidence')) as session:
            handoff=session.propose(raw,now_ms=at)
            with closing(IssuerStore(f.ledger)) as issuer:
                grant=demo_issuer.issue_submission(handoff,policy=policy,registry=f.registry,key=f.private,
                    store=issuer,now_ms=at,clock_ms=lambda:at)
            return session.submit(grant['proposal_sha256'],json.dumps(grant['envelope']).encode())['prepared']
    try:
        f.entry_batch=send(raw,f.base_policy,f.now_ms)
        f.entry_plan=_project(f,f.entry_batch,at=f.now_ms,price=f.entry_batch['orders'][0]['price'],venue_id=123,trade_id=71)
        assert Decimal(f.context['quantity'])>0
        f.held_context=deepcopy(f.context)
        at=f.now_ms+2000;price=str((Decimal(f.context['average_entry_price'])*Decimal('.99')).quantize(Decimal('.1')))
        origin=_origin(f,at,price)
        f.clock[0]=at
        engine,continuation,_=EpisodeTail(root,bundle_sha256=_digest(runtime),engine=f.engine,
            entry_decision_ms=f.entry_decision_ms).snapshot()
        decision=prepare(engine=engine,entry_decision_ms=f.entry_decision_ms,origin=origin,**f.stores,now_ms=at)
        assert decision['status']=='DECISION_REQUIRES_APPROVAL' and decision['action']=='REDUCE_LONG',decision
        decision['bar_continuation_sha256']=continuation
        f.exit_result=_retain(f,origin,decision,at)
        approved=approval.prepare(f.exit_result,runtime=runtime,policy=f.base_policy,registry=f.registry,now_ms=at)
        policy=approval.policy_candidate(approved,result=f.exit_result,runtime=runtime,
            policy=f.base_policy,registry=f.registry,now_ms=at)['policy_candidate']
        from live.execution import demo_position_context as position
        values=deepcopy(f.proposal_values);risk=values['risk_inputs'];risk['now_ms']=at
        risk['account'].update(position.validate_origin(origin,f.context,as_of_ms=at))
        risk['market'].update(reference_price=price,observed_at_ms=at-1)
        values.update(orders=decision['draft']['orders'],origin=origin,now_ms=at,execution_constraints=decision['execution_constraints'])
        raw_exit=json.loads(request(values));raw_exit.update(schema='DEMO_GOLD_STRATEGY_SESSION_INPUT_V1',execution_constraints=decision['execution_constraints'])
        f.exit_batch=send(json.dumps(raw_exit).encode(),policy,at)
        f.exit_plan=_project(f,f.exit_batch,at=at,price=price,venue_id=124,trade_id=72)
        assert f.context['quantity']=='0' and len(posts)==2
        f.trigger=json.loads(next(root.glob('bar-exit-trigger-*.json')).read_bytes())
        yield f
    finally:f.stream.__exit__(None,None,None)
