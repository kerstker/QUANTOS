"""Original AUTO cash and ledgers close without a second settled strategy."""
from copy import deepcopy
import os
import select
import signal
import time

import pytest

from tests.live.test_gold_feedback_recovery_fixture import case as case
from tests.live.test_gold_feedback_recovery_fixture import original_evidence as original_evidence
from tests.live.test_gold_feedback_recovery_fixture import completed_bar_feedback as completed_bar_feedback
from tests.live.gold_legacy_closure_fixture import legacy_case as legacy_case
from tests.live.gold_legacy_closure_fixture import completed_legacy_feedback as completed_legacy_feedback
from tests.tools.test_gold_bar_opening_origin import opening_case as opening_case
from tests.tools.test_gold_pilot_idle_closure import (
    book as book, installed as installed, first as first, materials as materials,
    scope_inputs as scope_inputs, day_materials as day_materials, enrollment_case as enrollment_case,
    idle_case as idle_case,
)
from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _state


def configure_closure(f,monkeypatch):
    from tools import gold_pilot_closure as closure
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    # The pre-existing native account fixture uses .db filenames. Open the same
    # original files through actual read-only stores; never substitute snapshots.
    def stores(stack,root):
        assert root==f.root
        result={}
        for name,factory in (('dispatch',DemoDispatchStore),('recovery',SqliteOrderRecoveryStore),('oms',LiveLedger)):
            store=factory(str(root/(name+'.db')),read_only=True)
            stack.callback(store.close);result[name]=store
        return result
    monkeypatch.setattr(closure,'_stores',stores)
    return f


@pytest.fixture
def closure_case(completed_bar_feedback,monkeypatch):
    return configure_closure(completed_bar_feedback,monkeypatch)


def run(f, request):
    from tools import gold_pilot_closure as closure
    return closure.execute(f.root, request, source_commit=f.source_commit,
        account=f.account, executor_uid=f.executor_uid, clock=lambda:f.clock[0],
        sleep=lambda secs:f.clock.__setitem__(0,f.clock[0]+int(secs*1000)))


def wait_child(child):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        found,status=os.waitpid(child,os.WNOHANG)
        if found:return os.waitstatus_to_exitcode(status)
        select.select([],[],[],0.01)
    os.kill(child,signal.SIGKILL);os.waitpid(child,0)
    raise AssertionError('isolated closure child exceeded 15 seconds')


@pytest.mark.parametrize('previous', [False, True])
def test_native_cash_closes_original_pilot_once(closure_case, previous):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention import executor_retention
    f = closure_case
    runtime = dict(f.base_runtime, episode_id='f'*64) if previous else f.base_runtime
    req = closure.request(runtime, f.result)
    before = _state(**f.stores)
    old_artifact, old_result = f.artifact_bytes, f.result_bytes
    f.clock[0] = f.result['original_pilot_window']['ends_at_ms']+1000
    calls = len(f.read_calls)
    result = run(f, req)
    assert result['original_result_provenance']=='EXECUTOR_ORIGINAL'
    decision = closure.validate_result(result, f.result)
    assert decision['status'] == 'PILOT_STOPPED' and decision['position_flat'] is True
    assert decision['pilot_control']['reason'] == 'DURATION_COMPLETE'
    assert decision['bundle_sha256'] == _digest(runtime)
    assert decision['closure_of_result_sha256'] == _digest(f.result)
    assert decision['action'] is decision['draft'] is decision['execution_constraints'] is None
    assert len(f.read_calls) > calls and all(k == 'origin' for k, _ in f.read_calls[calls:])
    calls = len(f.read_calls)
    assert run(f, req) == result and len(f.read_calls) == calls
    assert _state(**f.stores) == before
    assert f.artifact_bytes == old_artifact and f.result_bytes == old_result
    with executor_retention(f.root,runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata()['frames'] == f.executor_checkpoint['frames']+1
        assert store.read('result',_digest(f.result)) == old_result
        view = closure.read_intent(f.root,retention=store,runtime=runtime,
            source_commit=f.source_commit,account=f.account,executor_uid=f.executor_uid)
        assert view['status'] == 'COMPLETE' and view['result'] == result
        assert store.read_claim(view['claim'],'result') is not None


@pytest.mark.parametrize('damage', ['source','decision','artifact','feedback','window','original','runtime'])
def test_request_drift_never_allocates_closure(closure_case, damage):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention import executor_retention
    f = closure_case
    req = closure.request(f.base_runtime, f.result)
    if damage == 'source':req['source_commit']='e'*40
    elif damage == 'decision':req['original_decision_sha256']='e'*64
    elif damage == 'artifact':req['artifact_sha256']='e'*64
    elif damage == 'feedback':req['feedback_sha256']='e'*64
    elif damage == 'window':req['original_pilot_window']['ends_at_ms']+=1
    elif damage == 'original':req['original_result']['decision']['observed_at_ms']+=1
    else:req['runtime']['seconds']+=1
    f.clock[0] = f.result['original_pilot_window']['ends_at_ms']+1000
    before = len(f.read_calls)
    with pytest.raises(ValueError):run(f,req)
    assert not (f.root/'gold-pilot/closure-intent.json').exists()
    assert len(f.read_calls) == before
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata() == f.executor_checkpoint


@pytest.mark.parametrize('phase', ['intent','claim','origin','stop','result'])
def test_real_fork_recovers_only_original_closure_attempt(closure_case, monkeypatch, phase):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention_store import Retention
    from live.execution import demo_gold_pilot as pilot
    f = closure_case
    req = closure.request(f.base_runtime,f.result)
    f.clock[0] = f.result['original_pilot_window']['ends_at_ms']+1000
    before = _state(**f.stores)
    child=os.fork()
    if child == 0:
        try:
            if phase == 'intent':
                original=closure._write_intent
                def interrupted(*args,**kwargs):
                    original(*args,**kwargs);os._exit(71)
                monkeypatch.setattr(closure,'_write_intent',interrupted)
            elif phase == 'claim':
                original=Retention.claim
                def interrupted(*args,**kwargs):
                    original(*args,**kwargs);os._exit(71)
                monkeypatch.setattr(Retention,'claim',interrupted)
            elif phase == 'stop':
                original=pilot.observe
                def interrupted(*args,**kwargs):
                    original(*args,**kwargs);os._exit(71)
                monkeypatch.setattr(pilot,'observe',interrupted)
            else:
                original=Retention.put
                def interrupted(self,claim,kind,value):
                    answer=original(self,claim,kind,value)
                    if kind == phase:os._exit(71)
                    return answer
                monkeypatch.setattr(Retention,'put',interrupted)
            run(f,req)
            os._exit(72)
        except BaseException:os._exit(73)
    assert wait_child(child) == 71
    result=run(f,req)
    assert closure.validate_result(result,f.result)['status'] == 'PILOT_STOPPED'
    assert _state(**f.stores) == before
    from tools.gold_pilot_retention import executor_retention
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata()['frames'] == f.executor_checkpoint['frames']+1


def test_request_is_pure_and_never_relabels_original(closure_case):
    from tools import gold_pilot_closure as closure
    f=closure_case
    original=deepcopy(f.result);runtime=deepcopy(f.base_runtime)
    req=closure.request(runtime,original)
    assert req['original_result_sha256']==_digest(original)
    assert original==f.result and runtime==f.base_runtime
    assert req['original_result']==original and req['original_result'] is not original


def test_missing_completed_intent_cannot_reopen_observation_budget(closure_case):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention import executor_retention
    f=closure_case;req=closure.request(f.base_runtime,f.result)
    f.clock[0]=f.result['original_pilot_window']['ends_at_ms']+1000
    run(f,req)
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        before=store.metadata()
    (f.root/'gold-pilot/closure-intent.json').unlink()
    with pytest.raises(ValueError,match='INTENT_MISSING'):run(f,req)
    assert not (f.root/'gold-pilot/closure-intent.json').exists()
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata()==before


def test_retained_origin_cannot_be_renewed_after_its_original_freshness(closure_case,monkeypatch):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention_store import Retention
    f=closure_case;req=closure.request(f.base_runtime,f.result)
    f.clock[0]=f.result['original_pilot_window']['ends_at_ms']+1000
    original=Retention.put
    child=os.fork()
    if child==0:
        def interrupted(self,claim,kind,value):
            answer=original(self,claim,kind,value)
            if kind=='origin':os._exit(71)
            return answer
        monkeypatch.setattr(Retention,'put',interrupted)
        try:run(f,req)
        except BaseException:os._exit(73)
        os._exit(72)
    assert wait_child(child)==71
    f.clock[0]+=31000
    calls=len(f.read_calls)
    with pytest.raises(ValueError,match='ORIGIN_NOT_READY'):run(f,req)
    assert len(f.read_calls)==calls and not (f.root/'gold-pilot/stopped.json').exists()
    from tools.gold_pilot_retention import executor_retention
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata()['frames']==f.executor_checkpoint['frames']+1


@pytest.mark.parametrize('boundary',['before_end','after_closing','capture_late','capture_rollback'])
def test_original_observation_window_never_renews(closure_case,monkeypatch,boundary):
    from tools import gold_pilot_closure as closure, demo_executor_origin as capture
    f=closure_case;req=closure.request(f.base_runtime,f.result)
    end=f.result['original_pilot_window']['ends_at_ms']
    f.clock[0]=end+1000
    if boundary=='before_end':f.clock[0]=end-1000
    elif boundary=='after_closing':f.clock[0]=end+900000
    else:
        original=capture._report
        def moved(**kwargs):
            value=original(**kwargs)
            f.clock[0]=end+900000 if boundary=='capture_late' else end
            return value
        monkeypatch.setattr(capture,'_report',moved)
    with pytest.raises(ValueError):run(f,req)
    assert not (f.root/'gold-pilot/stopped.json').exists()
    if boundary in ('before_end','after_closing'):
        assert not (f.root/'gold-pilot/closure-intent.json').exists()


@pytest.mark.parametrize('damage',['wallet','position','source'])
def test_current_account_drift_never_claims_closure(closure_case,monkeypatch,damage):
    from tools import gold_pilot_closure as closure, demo_executor_origin as capture
    f=closure_case;req=closure.request(f.base_runtime,f.result)
    f.clock[0]=f.result['original_pilot_window']['ends_at_ms']+1000
    before=_state(**f.stores)
    if damage=='wallet':
        # This is the explicit venue response fixture, not the retained cash.
        f.context['canonical_wallet']='4900'
    else:
        original=capture._report
        def changed(**kwargs):
            value=original(**kwargs)
            if damage=='source':value['source_sha256']='0'*64
            else:
                from tests.live.test_gold_strategy_roundtrip import held_origin
                value=held_origin(value,wallet=f.context['canonical_wallet'])
                f.clock[0]+=2000
            return value
        monkeypatch.setattr(capture,'_report',changed)
    with pytest.raises(ValueError):run(f,req)
    assert not (f.root/'gold-pilot/stopped.json').exists()
    assert _state(**f.stores)==before


def test_completed_closure_is_historical_without_new_capture_after_deadline(closure_case):
    from tools import gold_pilot_closure as closure
    f=closure_case;req=closure.request(f.base_runtime,f.result)
    f.clock[0]=f.result['original_pilot_window']['ends_at_ms']+1000
    result=run(f,req);calls=len(f.read_calls)
    f.clock[0]+=900000
    assert run(f,req)==result and len(f.read_calls)==calls


@pytest.mark.parametrize('phase',[None,'decision','result'])
def test_native_legacy_closure_has_explicit_original_and_exact_partial(completed_legacy_feedback,monkeypatch,phase):
    from tools import gold_pilot_closure as closure
    from live.execution import demo_gold_pilot as pilot
    from tools.gold_pilot_retention_store import canonical
    f=configure_closure(completed_legacy_feedback,monkeypatch)
    before=_state(**f.stores);old_result=f.result_bytes;old_artifact=f.artifact_bytes
    req=closure.request(f.base_runtime,f.result)
    f.clock[0]=f.configuration['ends_at_ms']+1000
    if phase is not None:
        child=os.fork()
        if child==0:
            if phase=='decision':
                original=closure._retain
                def interrupted(*args,**kwargs):
                    answer=original(*args,**kwargs);os._exit(71)
                    return answer
                monkeypatch.setattr(closure,'_retain',interrupted)
            else:
                original=pilot.retain_once
                def interrupted(path,*args,**kwargs):
                    answer=original(path,*args,**kwargs)
                    if path.name=='closure-result.json':os._exit(71)
                    return answer
                monkeypatch.setattr(pilot,'retain_once',interrupted)
            try:run(f,req)
            except BaseException:os._exit(73)
            os._exit(72)
        assert wait_child(child)==71
        view=closure.read_intent(f.root,retention=None,runtime=f.base_runtime,
            source_commit=f.source_commit,account=f.account,executor_uid=f.executor_uid)
        assert view['status']==('PENDING' if phase=='decision' else 'COMPLETE')
        assert view['decision'] is not None
        assert (f.root/'strategy-decisions'/(_digest(view['decision'])+'.json')).read_bytes()==canonical(view['decision'])
    result=run(f,req)
    assert result['original_result_provenance']=='CONTROLLER_ORIGINAL'
    assert closure.validate_result(result,f.result)['status']=='PILOT_STOPPED'
    calls=list(f.read_calls)
    assert run(f,req)==result and f.read_calls==calls
    assert _state(**f.stores)==before and f.result_bytes==old_result and f.artifact_bytes==old_artifact
    assert not (f.root/'gold-pilot-retention').exists()


@pytest.fixture
def idle_empty_case(opening_case,monkeypatch):
    from types import SimpleNamespace
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from tests.tools.test_gold_strategy_entry import origin_at
    from tests.tools.test_gold_pilot_capacity import write
    from tests.live.gold_feedback_recovery_fixture import _reported_io
    from tools import demo_executor_origin as origin
    f=opening_case
    original=origin_at(f.clock[0]);original['source_sha256']=origin.source_hash()
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit='c'*40,
        account_ref_sha256=f.account,duration_seconds=86400,max_loss_fraction='0.05',submission_enabled=False)
    write(f.root/'gold-pilot-request.json',request)
    config=enrollment.enroll_requested(root=f.root,source_commit='c'*40,origin=original,
        now_ms=f.clock[0],**dict(zip(('dispatch','recovery','oms'),f.native)))
    value=SimpleNamespace(root=f.root,base_runtime=f.bundle,source_commit='c'*40,account=f.account,
        executor_uid=os.getuid(),configuration=config,stores=dict(zip(('dispatch','recovery','oms'),f.native)),
        context={'canonical_wallet':'5000'},now_ms=config['ends_at_ms']-3000)
    value.window=dict(schema='GOLD_PILOT_ORIGINAL_WINDOW_V1',source_commit='c'*40,
        account_ref_sha256=f.account,request_sha256=_digest(request),configuration_sha256=_digest(config),
        started_at_ms=config['started_at_ms'],ends_at_ms=config['ends_at_ms'],submission_enabled=False)
    monkeypatch.setattr(origin.transport,'_credentials',lambda _:('synthetic-key','synthetic-secret'))
    _reported_io(value,monkeypatch)
    return configure_closure(value,monkeypatch)


def test_idle_zero_frame_observes_original_stop_without_opening_or_feedback(idle_empty_case):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention import executor_retention
    f=idle_empty_case;before=_state(**f.stores)
    req=closure.idle_request(f.base_runtime,f.window)
    result=run(f,req)
    decision=closure.validate_idle_result(result,req)
    assert decision['status']=='PILOT_STOPPED' and decision['closure_mode']=='NO_EXECUTION_SINCE_ENROLLMENT'
    assert decision['execution_history_empty'] is True and result['idle_evidence']['observed_equity']=='5000'
    assert 'feedback' not in result and 'closure_of_result_sha256' not in decision
    assert len(f.read_calls)==16 and _state(**f.stores)==before
    calls=list(f.read_calls)
    assert run(f,req)==result and f.read_calls==calls
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata()['frames']==1
        view=closure.read_intent(f.root,retention=store,runtime=f.base_runtime,
            source_commit=f.source_commit,account=f.account,executor_uid=f.executor_uid)
        assert view['status']=='COMPLETE' and view['intent']['original_claim'] is None
        assert view['intent']['original_checkpoint']['frames']==0


def test_persisted_stop_recovers_original_time_after_freshness_expires(closure_case,monkeypatch):
    from tools import gold_pilot_closure as closure
    from live.execution import demo_gold_pilot as pilot
    from tools.gold_pilot_retention import executor_retention
    f=closure_case;req=closure.request(f.base_runtime,f.result)
    f.clock[0]=f.result['original_pilot_window']['ends_at_ms']+1000
    before=_state(**f.stores)
    child=os.fork()
    if child==0:
        original=pilot.observe
        def interrupted(*args,**kwargs):
            original(*args,**kwargs);os._exit(71)
        monkeypatch.setattr(pilot,'observe',interrupted)
        try:run(f,req)
        except BaseException:os._exit(73)
        os._exit(72)
    assert wait_child(child)==71
    stop=pilot.read(f.root/'gold-pilot/stopped.json')
    f.clock[0]+=910000
    monkeypatch.setattr(pilot,'observe',lambda *a,**k:pytest.fail('historical recovery observed again'))
    calls=list(f.read_calls);result=run(f,req)
    assert result['decision']['observed_at_ms']==stop['observed_at_ms']
    assert result['decision']['origin_sha256']==stop['origin_sha256'] and f.read_calls==calls
    assert _state(**f.stores)==before and pilot.read(f.root/'gold-pilot/stopped.json')==stop
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata()['frames']==f.executor_checkpoint['frames']+1


def test_idle_changed_execution_returns_only_readonly_not_applicable(closure_case):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention import executor_retention
    f=closure_case;window=f.result['original_pilot_window'];f.clock[0]=window['ends_at_ms']+1000
    req=closure.idle_request(f.base_runtime,window)
    before=_state(**f.stores);calls=list(f.read_calls)
    result=run(f,req)
    assert result==dict(schema='GOLD_PILOT_IDLE_CLOSURE_RESULT_V1',status='NOT_APPLICABLE',
        reason='EXECUTION_SINCE_ENROLLMENT',source_commit=f.source_commit,bundle_sha256=_digest(f.base_runtime),
        request_sha256=_digest(req),original_pilot_window=window,submission_enabled=False,economic_authority=False)
    assert _state(**f.stores)==before and f.read_calls==calls
    assert not (f.root/'gold-pilot/closure-intent.json').exists() and not (f.root/'gold-pilot/stopped.json').exists()
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata()==f.executor_checkpoint


@pytest.fixture
def idle_btc_case(idle_case,scope_inputs,day_materials,monkeypatch):
    import hashlib,json
    from types import SimpleNamespace
    from tools import demo_executor_origin as capture
    from tests.live.test_demo_market_partition_runtime import shift
    values,root=idle_case;config=values['configuration'];clock=[values['now_ms']]
    for path in (root,*root.rglob('*')):
        assert not path.is_symlink()
        path.chmod(0o700 if path.is_dir() else 0o600)
    f=SimpleNamespace(root=root,base_runtime=day_materials['runtime'],source_commit='c'*40,
        account=config['account_ref_sha256'],executor_uid=os.getuid(),configuration=config,
        stores={k:scope_inputs[0]['stores'][k] for k in ('dispatch','recovery','oms')},clock=clock,read_calls=[])
    f.window=dict(schema='GOLD_PILOT_ORIGINAL_WINDOW_V1',source_commit='c'*40,account_ref_sha256=f.account,
        request_sha256=_digest(values['request']),configuration_sha256=_digest(config),
        started_at_ms=config['started_at_ms'],ends_at_ms=config['ends_at_ms'],submission_enabled=False)
    source=values['intent']['prepared']['opening_origin']['source_sha256']
    # Fixed source/role and network boundaries are isolated. Original signed
    # enrollment, partition, all assets and native ledger bytes stay unchanged.
    monkeypatch.setattr(capture,'fixed_account',lambda:(root,f.account))
    monkeypatch.setattr(capture,'os',SimpleNamespace(geteuid=lambda:f.executor_uid))
    monkeypatch.setattr(capture,'source_hash',lambda:source)
    monkeypatch.setattr(capture,'now_ms',lambda:clock[0])
    key=next(k for k in ('synthetic-key','a'*64) if hashlib.sha256(('BINANCE_USDM_DEMO:'+k).encode()).hexdigest()==f.account)
    monkeypatch.setattr(capture.transport,'_credentials',lambda _:(key,'synthetic-secret'))
    monkeypatch.setattr(capture.demo_network,'opener',lambda:object())
    def child(command,**kwargs):
        assert command[3]=='tools.demo_executor_origin'
        rows=iter(shift(values['origin'],clock[0]-values['now_ms'])['reads'])
        def get(path,**kwargs):
            row=next(rows);kind=row['kind'];assert path==capture.original.PATHS[kind]
            f.read_calls.append(path);data=deepcopy(row['data'])
            if kind=='rules':
                data={'symbols':[dict(data['terms'],**data['precision'],baseAsset=data['base_asset'],
                    filters=data['venue_filters'],orderTypes=data['order_types'],timeInForce=data['time_in_forces'])]}
            elif kind.startswith('symbols'):
                data=[dict(item,**(row['target_config'] if item['symbol']=='XAUUSDT' else
                    {'isAutoAddMargin':False,'maxNotionalValue':'10000'})) for item in data]
            return json.dumps(data).encode()
        with monkeypatch.context() as patch:
            patch.setattr(capture.original,'get_response',get)
            value=capture.capture_worker('XAUUSDT')
        return SimpleNamespace(stdout=json.dumps(value).encode())
    monkeypatch.setattr(capture,'subprocess',SimpleNamespace(run=child,TimeoutExpired=TimeoutError))
    return configure_closure(f,monkeypatch)


def test_idle_original_btc_prefix_remains_nonempty_and_unmodified(idle_btc_case):
    from tools import gold_pilot_closure as closure
    f=idle_btc_case;before=_state(**f.stores)
    assert all(before.values())
    req=closure.idle_request(f.base_runtime,f.window)
    result=run(f,req);d=closure.validate_idle_result(result,req)
    assert d['execution_history_empty'] is False and result['idle_evidence']['market_partition_sha256'] is not None
    assert result['idle_evidence']['observed_equity']=='4200' and len(f.read_calls)==16
    assert _state(**f.stores)==before
    assert run(f,req)==result and len(f.read_calls)==16


@pytest.mark.parametrize('phase',['intent','claim','origin','stop','saved_result','decision','result'])
def test_idle_real_fork_restores_only_its_original_observation(idle_empty_case,monkeypatch,phase):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention_store import Retention
    from tools.gold_pilot_retention import executor_retention
    from live.execution import demo_gold_pilot as pilot
    f=idle_empty_case;req=closure.idle_request(f.base_runtime,f.window);before=_state(**f.stores)
    child=os.fork()
    if child==0:
        if phase=='intent':
            original=closure._write_intent
            def interrupted(*args,**kwargs):original(*args,**kwargs);os._exit(71)
            monkeypatch.setattr(closure,'_write_intent',interrupted)
        elif phase=='claim':
            original=Retention.claim
            def interrupted(*args,**kwargs):original(*args,**kwargs);os._exit(71)
            monkeypatch.setattr(Retention,'claim',interrupted)
        elif phase=='stop':
            original=pilot.observe
            def interrupted(*args,**kwargs):original(*args,**kwargs);os._exit(71)
            monkeypatch.setattr(pilot,'observe',interrupted)
        elif phase=='saved_result':
            original=pilot.retain_once
            def interrupted(path,*args,**kwargs):
                result=original(path,*args,**kwargs)
                if path.name=='closure-result.json':os._exit(71)
                return result
            monkeypatch.setattr(pilot,'retain_once',interrupted)
        else:
            original=Retention.put
            def interrupted(self,claim,kind,value):
                result=original(self,claim,kind,value)
                if kind==phase:os._exit(71)
                return result
            monkeypatch.setattr(Retention,'put',interrupted)
        try:run(f,req)
        except BaseException:os._exit(73)
        os._exit(72)
    assert wait_child(child)==71
    if phase in ('stop','saved_result','decision','result'):f.clock[0]+=910000
    result=run(f,req);assert closure.validate_idle_result(result,req)['status']=='PILOT_STOPPED'
    assert _state(**f.stores)==before
    calls=list(f.read_calls);assert run(f,req)==result and f.read_calls==calls
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata()['frames']==1


@pytest.mark.parametrize('damage',['missing_intent','saved_result','request','wallet','window'])
def test_idle_missing_or_changed_originals_never_complete(idle_empty_case,monkeypatch,damage):
    import json
    from tools import gold_pilot_closure as closure
    f=idle_empty_case;req=closure.idle_request(f.base_runtime,f.window)
    if damage in ('missing_intent','saved_result'):
        run(f,req);path=f.root/'gold-pilot'/('closure-intent.json' if damage=='missing_intent' else 'closure-result.json')
        path.unlink()
    elif damage=='request':
        path=f.root/'gold-pilot/enrollment-intent.json';value=json.loads(path.read_bytes())
        value['request_sha256']='e'*64;path.write_text(json.dumps(value))
    elif damage=='wallet':f.context['canonical_wallet']='5001'
    else:req['original_pilot_window']['ends_at_ms']+=1
    before=_state(**f.stores)
    with pytest.raises(ValueError):run(f,req)
    assert _state(**f.stores)==before


def test_idle_existing_original_stop_keeps_its_time_and_captures_current_account(idle_empty_case):
    from tools import gold_pilot_closure as closure
    from live.execution import demo_gold_pilot as pilot
    f=idle_empty_case;req=closure.idle_request(f.base_runtime,f.window)
    stop=pilot.observe(dict(account_ref_sha256=f.account,equity='5000'),now_ms=f.configuration['ends_at_ms'],
        origin_sha256='e'*64)
    result=run(f,req)
    assert result['decision']['pilot_control']==stop and result['decision']['origin_sha256']!='e'*64
    assert len(f.read_calls)==16 and closure.validate_idle_result(result,req)['status']=='PILOT_STOPPED'


@pytest.mark.parametrize('witness',['origin_only','stop_stale','stop_other_origin'])
def test_idle_partial_requires_original_freshness_or_matching_timely_stop(idle_empty_case,monkeypatch,witness):
    import json
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention_store import Retention,canonical
    from tools.gold_pilot_retention import executor_retention
    from live.execution import demo_gold_pilot as pilot
    f=idle_empty_case;req=closure.idle_request(f.base_runtime,f.window)
    child=os.fork()
    if child==0:
        if witness=='origin_only':
            original=Retention.put
            def interrupted(self,claim,kind,value):
                answer=original(self,claim,kind,value)
                if kind=='origin':os._exit(71)
                return answer
            monkeypatch.setattr(Retention,'put',interrupted)
        else:
            original=pilot.observe
            def interrupted(*args,**kwargs):original(*args,**kwargs);os._exit(71)
            monkeypatch.setattr(pilot,'observe',interrupted)
        try:run(f,req)
        except BaseException:os._exit(73)
        os._exit(72)
    assert wait_child(child)==71
    if witness!='origin_only':
        path=f.root/'gold-pilot/stopped.json';value=json.loads(path.read_bytes())
        if witness=='stop_stale':value['observed_at_ms']+=31000
        else:value['origin_sha256']='e'*64
        path.write_bytes(canonical(value))
    f.clock[0]+=910000;calls=list(f.read_calls)
    with pytest.raises(ValueError):run(f,req)
    assert f.read_calls==calls and not (f.root/'gold-pilot/closure-result.json').exists()
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata()['frames']==1


def test_idle_damaged_enrollment_is_never_not_applicable(closure_case):
    import json
    from tools import gold_pilot_closure as closure
    f=closure_case;window=f.result['original_pilot_window'];req=closure.idle_request(f.base_runtime,window)
    f.clock[0]=window['ends_at_ms']+1000
    path=f.root/'gold-pilot/enrollment-intent.json';value=json.loads(path.read_bytes())
    value['prepared']['execution_state_sha256']='e'*64;path.write_text(json.dumps(value))
    calls=list(f.read_calls)
    with pytest.raises(ValueError):run(f,req)
    assert f.read_calls==calls and not (f.root/'gold-pilot/closure-intent.json').exists()


@pytest.mark.parametrize('clock_change',['past_closing','backward'])
def test_idle_readonly_classification_checks_completion_clock(closure_case,monkeypatch,clock_change):
    from tools import gold_pilot_closure as closure
    f=closure_case;window=f.result['original_pilot_window'];req=closure.idle_request(f.base_runtime,window)
    f.clock[0]=window['ends_at_ms']+1000;original=closure._idle_inputs;calls=[0]
    def slow(*args,**kwargs):
        value=original(*args,**kwargs);calls[0]+=1
        if calls[0]==2:f.clock[0]=window['ends_at_ms']+(900000 if clock_change=='past_closing' else 0)
        return value
    monkeypatch.setattr(closure,'_idle_inputs',slow)
    with pytest.raises(ValueError,match='CLOCK'):run(f,req)
    assert not (f.root/'gold-pilot/closure-intent.json').exists()
