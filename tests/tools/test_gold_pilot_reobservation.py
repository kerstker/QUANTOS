"""A real interrupted account observation can have only one fresh successor."""
import os

import pytest

from tests.tools.test_gold_pilot_closure import (
    case as case, original_evidence as original_evidence,
    completed_bar_feedback as completed_bar_feedback, closure_case as closure_case,
    opening_case as opening_case, idle_empty_case as idle_empty_case,
    legacy_case as legacy_case, completed_legacy_feedback as completed_legacy_feedback,
    configure_closure,
    run, wait_child,
)
from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _state
from tools.demo_executor_origin import run as original_origin_run


def stale_attempt(f, monkeypatch, req=None):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention_store import Retention
    req=closure.request(f.base_runtime,f.result) if req is None else req
    f.clock[0]=req['original_pilot_window']['ends_at_ms']+1000
    child=os.fork()
    if child==0:
        if req['runtime']['schema'] in closure.BAR_SCHEMAS:
            original=Retention.put
            def interrupted(self,claim,kind,value):
                result=original(self,claim,kind,value)
                if kind=='origin':os._exit(71)
                return result
            monkeypatch.setattr(Retention,'put',interrupted)
        else:
            from live.execution import demo_gold_pilot as pilot
            original=pilot.retain_once
            def interrupted(path,*args,**kwargs):
                result=original(path,*args,**kwargs)
                if path.name=='closure-origin.json':os._exit(71)
                return result
            monkeypatch.setattr(pilot,'retain_once',interrupted)
        try:run(f,req)
        except BaseException:os._exit(73)
        os._exit(72)
    assert wait_child(child)==71
    f.clock[0]+=31000
    with pytest.raises(ValueError,match='ORIGIN_NOT_READY'):run(f,req)
    assert not (f.root/'gold-pilot/stopped.json').exists()
    return req


@pytest.fixture
def reobservation_case(closure_case,monkeypatch):
    f=closure_case
    f.original_closure_request=stale_attempt(f,monkeypatch)
    return f


def execute(f,req):
    from tools import gold_pilot_reobservation as retry
    return retry.execute(f.root,req,source_commit=f.source_commit,account=f.account,
        executor_uid=f.executor_uid,clock=lambda:f.clock[0],
        sleep=lambda secs:f.clock.__setitem__(0,f.clock[0]+int(secs*1000)))


def build_request(f):
    from tools import gold_pilot_reobservation as retry
    from tools.gold_pilot_retention import executor_retention
    from contextlib import nullcontext
    context=executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid)
    with context if context is not None else nullcontext(None) as store:
        candidate=retry.inspect_stale_candidate(f.root,retention=store,base_runtime=f.base_runtime,
            runtime=f.base_runtime,source_commit=f.source_commit,account=f.account,
            executor_uid=f.executor_uid,now_ms=f.clock[0])
    return retry.request(f.base_runtime,f.original_closure_request,candidate)


def test_real_stale_attempt_has_one_new_observation_and_honest_old_diagnostic(reobservation_case):
    from tools import gold_pilot_closure as closure
    from tools.gold_pilot_retention import executor_retention
    f=reobservation_case;state=_state(**f.stores)
    path=f.root/'gold-pilot/closure-intent.json';old=(path.read_bytes(),path.stat().st_ino)
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        view=closure.read_intent(f.root,retention=store,runtime=f.base_runtime,source_commit=f.source_commit,
            account=f.account,executor_uid=f.executor_uid)
        failed=view['claim'];origin=store.read_claim(failed,'origin');head=store.metadata()
    req=build_request(f);result=execute(f,req)
    assert closure.validate_result(result,f.result)['status']=='PILOT_STOPPED'
    assert _state(**f.stores)==state and old==(path.read_bytes(),path.stat().st_ino)
    assert execute(f,req)==result
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata()['frames']==head['frames']+1
        assert store.read_claim(failed,'origin')==origin
        from services.governance.json_integrity import load_json_without_duplicate_keys as decode
        diagnostic=decode(store.read_claim(failed,'result'))
        assert diagnostic['status']=='EXPIRED_BEFORE_STOP'
        assert diagnostic['decision']['pilot_stopped'] is False
        assert store.read('result',_digest(f.result))==f.result_bytes


@pytest.fixture
def idle_retry_case(idle_empty_case,monkeypatch):
    from tools import gold_pilot_closure as closure
    f=idle_empty_case
    f.original_closure_request=stale_attempt(f,monkeypatch,closure.idle_request(f.base_runtime,f.window))
    return f


@pytest.mark.parametrize('phase',['intent','expired_decision','expired_result','claim','admission','capture','capture_witness',
    'origin','stop','packet','decision','result'])
def test_native_idle_recovers_each_actual_write_without_second_capture(idle_retry_case,monkeypatch,phase):
    from tools import gold_pilot_reobservation as retry, gold_pilot_closure as closure
    from tools.gold_pilot_retention_store import Retention
    from tools.gold_pilot_retention import executor_retention
    from live.execution import demo_gold_pilot as pilot
    f=idle_retry_case;req=build_request(f);before=_state(**f.stores)
    child=os.fork()
    if child==0:
        if phase in ('intent','admission','capture','capture_witness','packet'):
            original=retry._save
            name=dict(intent=retry.INTENT,admission=retry.ADMISSION,capture=retry.CAPTURE,
                capture_witness=retry.CAPTURE_WITNESS,packet='closure-result.json')[phase]
            def interrupted(root,key,value):
                original(root,key,value)
                if key==name:os._exit(71)
            monkeypatch.setattr(retry,'_save',interrupted)
        elif phase=='claim':
            original=Retention.claim
            def interrupted(*args,**kwargs):
                value=original(*args,**kwargs);os._exit(71)
            monkeypatch.setattr(Retention,'claim',interrupted)
        elif phase=='stop':
            original=pilot.observe
            def interrupted(*args,**kwargs):
                value=original(*args,**kwargs);os._exit(71)
            monkeypatch.setattr(pilot,'observe',interrupted)
        else:
            original=Retention.put
            def interrupted(self,claim,kind,value):
                answer=original(self,claim,kind,value)
                expired=value.get('status')=='EXPIRED_BEFORE_STOP'
                if phase==('expired_'+kind if expired else kind):os._exit(71)
                return answer
            monkeypatch.setattr(Retention,'put',interrupted)
        try:execute(f,req)
        except BaseException:os._exit(73)
        os._exit(72)
    assert wait_child(child)==71
    if phase=='stop':f.clock[0]+=910000
    calls=list(f.read_calls)
    if phase in ('capture','capture_witness'):
        with pytest.raises(ValueError,match='UNKNOWN_CAPTURE_OUTCOME'):execute(f,req)
        assert f.read_calls==calls
    else:
        result=execute(f,req)
        assert closure.validate_idle_result(result,f.original_closure_request)['status']=='PILOT_STOPPED'
        count=len(f.read_calls);assert execute(f,req)==result and len(f.read_calls)==count
    assert _state(**f.stores)==before
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata()['frames']==2
        if phase=='capture':
            with pytest.raises(ValueError,match='UNKNOWN_CAPTURE_OUTCOME'):
                retry.read_reobservation(f.root,retention=store,base_runtime=f.base_runtime,
                    source_commit=f.source_commit,account=f.account,executor_uid=f.executor_uid,expected_runtime=f.base_runtime)
        else:
            view=retry.read_reobservation(f.root,retention=store,base_runtime=f.base_runtime,
                source_commit=f.source_commit,account=f.account,executor_uid=f.executor_uid,expected_runtime=f.base_runtime)
            assert view['status']==('CAPTURE_STARTED' if phase=='capture_witness' else 'COMPLETE')


def test_second_stale_origin_is_exhausted_without_recursive_attempt(idle_retry_case,monkeypatch):
    from tools.gold_pilot_retention_store import Retention
    from tools.gold_pilot_retention import executor_retention
    f=idle_retry_case;req=build_request(f);child=os.fork()
    if child==0:
        original=Retention.put
        def interrupted(self,claim,kind,value):
            result=original(self,claim,kind,value)
            if kind=='origin':os._exit(71)
            return result
        monkeypatch.setattr(Retention,'put',interrupted)
        try:execute(f,req)
        except BaseException:os._exit(73)
        os._exit(72)
    assert wait_child(child)==71;f.clock[0]+=31001;calls=list(f.read_calls)
    for _ in range(2):
        with pytest.raises(ValueError,match='REOBSERVATION_EXHAUSTED'):execute(f,req)
    assert f.read_calls==calls and not (f.root/'gold-pilot/stopped.json').exists()
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        assert store.metadata()['frames']==2


def test_true_nonbar_legacy_preserves_old_controller_provenance(completed_legacy_feedback,monkeypatch):
    from tools import gold_pilot_closure as closure, gold_pilot_reobservation as retry
    f=configure_closure(completed_legacy_feedback,monkeypatch)
    f.original_closure_request=stale_attempt(f,monkeypatch)
    req=build_request(f);before=_state(**f.stores);result=execute(f,req)
    assert result['original_result_provenance']=='CONTROLLER_ORIGINAL'
    assert closure.validate_result(result,f.result)['status']=='PILOT_STOPPED'
    assert execute(f,req)==result and _state(**f.stores)==before
    view=retry.read_reobservation(f.root,retention=None,base_runtime=f.base_runtime,
        source_commit=f.source_commit,account=f.account,executor_uid=f.executor_uid,expected_runtime=f.base_runtime)
    assert view['claim'] is view['failed_claim'] is None and view['status']=='COMPLETE'


@pytest.mark.parametrize('slot',['decision','result'])
def test_native_diagnostic_rejects_boolean_integer_alias_in_actual_slot(idle_retry_case,monkeypatch,slot):
    from tools import gold_pilot_reobservation as retry
    from tools.gold_pilot_retention import executor_retention
    from copy import deepcopy
    f=idle_retry_case;req=build_request(f);child=os.fork()
    if child==0:
        original=retry._save
        def interrupted(root,name,value):
            original(root,name,value)
            if name==retry.INTENT:os._exit(71)
        monkeypatch.setattr(retry,'_save',interrupted)
        try:execute(f,req)
        except BaseException:os._exit(73)
        os._exit(72)
    assert wait_child(child)==71
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        view=retry.read_reobservation(f.root,retention=store,base_runtime=f.base_runtime,
            source_commit=f.source_commit,account=f.account,executor_uid=f.executor_uid)
        old,_,origin=retry._old(f.root,store,f.base_runtime,f.source_commit,f.account,f.executor_uid)
        d,r=retry._diagnostic(view['intent']['subject'],origin)
        if slot=='decision':
            bad=deepcopy(d);bad['pilot_stopped']=0
            store.put(old['observation_claim'],'decision',bad)
        else:
            store.put(old['observation_claim'],'decision',d)
            bad=deepcopy(r);bad['submission_enabled']=0
            store.put(old['observation_claim'],'result',bad)
        with pytest.raises(ValueError):
            retry.read_reobservation(f.root,retention=store,base_runtime=f.base_runtime,
                source_commit=f.source_commit,account=f.account,executor_uid=f.executor_uid)


@pytest.mark.parametrize('field,value',[('attempt_index',True),('submission_enabled',0)])
def test_input_boolean_numeric_alias_cannot_begin_reobservation(idle_retry_case,field,value):
    f=idle_retry_case;req=build_request(f);req[field]=value;calls=list(f.read_calls)
    with pytest.raises(ValueError):execute(f,req)
    assert f.read_calls==calls and not (f.root/'gold-pilot/closure-reobservation-intent.json').exists()


def test_actual_failed_capture_consumes_its_slot_without_false_completion(idle_retry_case,monkeypatch):
    from tools import demo_executor_origin as capture, gold_pilot_reobservation as retry
    from tools.gold_pilot_retention import executor_retention
    f=idle_retry_case;req=build_request(f)
    attempts=[]
    def unavailable(*args,**kwargs):
        attempts.append(True);raise TimeoutError('synthetic original capture child timeout')
    monkeypatch.setattr(capture.subprocess,'run',unavailable)
    with pytest.raises(ValueError,match='REOBSERVATION_CAPTURE_FAILED'):execute(f,req)
    calls=list(f.read_calls)
    with pytest.raises(ValueError,match='REOBSERVATION_CAPTURE_FAILED'):execute(f,req)
    assert attempts==[True] and f.read_calls==calls and not (f.root/'gold-pilot/stopped.json').exists()
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        view=retry.read_reobservation(f.root,retention=store,base_runtime=f.base_runtime,
            source_commit=f.source_commit,account=f.account,executor_uid=f.executor_uid)
        assert view['status']=='ORIGIN_FAILED' and view['origin_status']=='FAILED'
        assert view['decision'] is view['result'] is None
        assert store.metadata()['frames']==2


@pytest.mark.parametrize('damage',['missing_intent','partial_admission','wrong_capture','original_changed'])
def test_fixed_lineage_damage_never_reopens_completed_attempt(idle_retry_case,damage):
    from tools import gold_pilot_reobservation as retry
    from tools.gold_pilot_retention_store import canonical
    from services.governance.json_integrity import load_json_without_duplicate_keys as decode
    f=idle_retry_case;req=build_request(f);execute(f,req);calls=list(f.read_calls)
    if damage=='missing_intent':(f.root/'gold-pilot'/retry.INTENT).unlink()
    elif damage=='partial_admission':(f.root/'gold-pilot'/retry.ADMISSION).write_bytes(b'{')
    else:
        path=f.root/'gold-pilot'/('closure-intent.json' if damage=='original_changed' else retry.CAPTURE)
        value=decode(path.read_bytes())
        if damage=='original_changed':value['closing_deadline_ms']+=1
        else:value['maximum_capture_attempts']=2
        path.write_bytes(canonical(value))
    with pytest.raises(ValueError):execute(f,req)
    assert f.read_calls==calls


def test_candidate_rejects_damaged_stop_instead_of_classifying_not_applicable(idle_retry_case):
    from tools import gold_pilot_reobservation as retry
    from tools.gold_pilot_retention import executor_retention
    f=idle_retry_case;(f.root/'gold-pilot/stopped.json').write_bytes(b'{')
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        with pytest.raises(ValueError):
            retry.inspect_stale_candidate(f.root,retention=store,base_runtime=f.base_runtime,runtime=f.base_runtime,
                source_commit=f.source_commit,account=f.account,executor_uid=f.executor_uid,now_ms=f.clock[0])


@pytest.mark.parametrize('limit',['max_frames','max_total_bytes','max_audit_events','max_audit_bytes','max_database_bytes','max_audit_allocated_bytes'])
def test_remaining_quota_guard_rejects_without_writes(idle_retry_case,limit):
    """The original audited store is real; only the proposed ceiling is lowered."""
    from tools import gold_pilot_reobservation as retry
    from tools.gold_pilot_retention import executor_retention
    f=idle_retry_case
    with executor_retention(f.root,f.base_runtime,source_commit=f.source_commit,account=f.account,uid=f.executor_uid) as store:
        before=store.metadata()
        class ProposedCeiling:
            binding=dict(store.binding,**{limit:0})
            def __getattr__(self,name):return getattr(store,name)
        with pytest.raises(ValueError,match='REOBSERVATION_.*CAPACITY'):
            retry._executor_budget(ProposedCeiling(),f.original_closure_request)
        assert store.metadata()==before
    assert not (f.root/'gold-pilot'/retry.INTENT).exists()


@pytest.mark.parametrize('phase',['intent','expired_decision','expired_result','admission','capture','capture_witness','origin','stop','decision','result'])
def test_true_legacy_recovers_its_fixed_file_write_boundaries(completed_legacy_feedback,monkeypatch,phase):
    from tools import gold_pilot_reobservation as retry, gold_pilot_closure as closure
    from live.execution import demo_gold_pilot as pilot
    f=configure_closure(completed_legacy_feedback,monkeypatch)
    f.original_closure_request=stale_attempt(f,monkeypatch)
    req=build_request(f);before=_state(**f.stores);child=os.fork()
    if child==0:
        if phase in ('expired_decision','decision'):
            original=retry._retain
            def interrupted(path,value):
                result=original(path,value)
                if (value['status']=='EXPIRED_BEFORE_STOP')==(phase=='expired_decision'):os._exit(71)
                return result
            monkeypatch.setattr(retry,'_retain',interrupted)
        elif phase=='stop':
            original=pilot.observe
            def interrupted(*args,**kwargs):
                original(*args,**kwargs);os._exit(71)
            monkeypatch.setattr(pilot,'observe',interrupted)
        else:
            original=retry._save
            wanted=dict(intent=retry.INTENT,expired_result=retry.EXPIRED,admission=retry.ADMISSION,
                capture=retry.CAPTURE,capture_witness=retry.CAPTURE_WITNESS,origin=retry.ORIGIN,result='closure-result.json')[phase]
            def interrupted(root,name,value):
                original(root,name,value)
                if name==wanted:os._exit(71)
            monkeypatch.setattr(retry,'_save',interrupted)
        try:execute(f,req)
        except BaseException:os._exit(73)
        os._exit(72)
    assert wait_child(child)==71
    if phase=='stop':f.clock[0]+=910000
    calls=list(f.read_calls)
    if phase in ('capture','capture_witness'):
        with pytest.raises(ValueError,match='UNKNOWN_CAPTURE_OUTCOME'):execute(f,req)
        assert f.read_calls==calls
    else:
        result=execute(f,req)
        assert closure.validate_result(result,f.result)['status']=='PILOT_STOPPED'
        assert execute(f,req)==result and result['original_result_provenance']=='CONTROLLER_ORIGINAL'
    assert _state(**f.stores)==before
    if phase=='capture':
        with pytest.raises(ValueError,match='UNKNOWN_CAPTURE_OUTCOME'):
            retry.read_reobservation(f.root,retention=None,base_runtime=f.base_runtime,
                source_commit=f.source_commit,account=f.account,executor_uid=f.executor_uid)
    else:
        view=retry.read_reobservation(f.root,retention=None,base_runtime=f.base_runtime,
            source_commit=f.source_commit,account=f.account,executor_uid=f.executor_uid)
        assert view['claim'] is view['failed_claim'] is None


@pytest.mark.parametrize('witness',['primary','secondary'])
def test_actual_capture_witness_fsync_overrun_starts_no_get(idle_retry_case,monkeypatch,witness):
    from tools import gold_pilot_reobservation as retry
    f=idle_retry_case;req=build_request(f);original=retry._save;calls=list(f.read_calls)
    def delayed(root,name,value):
        result=original(root,name,value)
        if name==(retry.CAPTURE if witness=='primary' else retry.CAPTURE_WITNESS):
            f.clock[0]=req['original_pilot_window']['ends_at_ms']+900000
        return result
    monkeypatch.setattr(retry,'_save',delayed)
    with pytest.raises(ValueError,match='REOBSERVATION_CLOCK'):execute(f,req)
    assert (f.root/'gold-pilot'/retry.CAPTURE).is_file() and f.read_calls==calls


@pytest.mark.parametrize('damage',['primary','secondary','mismatch','hardlink','symlink'])
def test_capture_witness_loss_after_actual_gets_never_repeats_them(idle_retry_case,monkeypatch,damage):
    from tools import gold_pilot_reobservation as retry, demo_executor_origin as capture
    from tools.gold_pilot_retention_store import canonical
    from services.governance.json_integrity import load_json_without_duplicate_keys as decode
    f=idle_retry_case;req=build_request(f);child=os.fork()
    if child==0:
        native=capture._report;before=len(f.read_calls)
        def interrupted(*args,**kwargs):
            report=native(*args,**kwargs)
            assert report['status']=='CAPTURED' and len(f.read_calls)==before+16
            os._exit(71)
        monkeypatch.setattr(capture,'_report',interrupted)
        try:execute(f,req)
        except BaseException:os._exit(73)
        os._exit(72)
    assert wait_child(child)==71;calls=list(f.read_calls)
    primary=f.root/'gold-pilot'/retry.CAPTURE;secondary=f.root/'gold-pilot'/retry.CAPTURE_WITNESS
    assert primary.read_bytes()==secondary.read_bytes() and primary.stat().st_ino!=secondary.stat().st_ino
    if damage=='primary':primary.unlink()
    elif damage=='secondary':secondary.unlink()
    elif damage=='mismatch':
        value=decode(secondary.read_bytes());value['started_at_ms']+=1;secondary.write_bytes(canonical(value))
    else:
        secondary.unlink()
        if damage=='hardlink':os.link(primary,secondary)
        else:secondary.symlink_to(primary)
    for _ in range(2):
        with pytest.raises((ValueError,OSError)):execute(f,req)
    assert f.read_calls==calls and not (f.root/'gold-pilot/stopped.json').exists()


@pytest.mark.parametrize('phase',['origin','stop'])
def test_resume_clock_cannot_precede_original_observation_or_stop(idle_retry_case,monkeypatch,phase):
    from tools import demo_executor_origin as capture
    from tools.gold_pilot_retention_store import Retention
    from live.execution import demo_gold_pilot as pilot
    f=idle_retry_case;req=build_request(f);initial=f.clock[0];child=os.fork()
    if child==0:
        native_capture=capture._report
        def later(*args,**kwargs):
            f.clock[0]+=1000
            return native_capture(*args,**kwargs)
        monkeypatch.setattr(capture,'_report',later)
        if phase=='origin':
            native=Retention.put
            def interrupted(self,claim,kind,value):
                result=native(self,claim,kind,value)
                if kind=='origin':os._exit(71)
                return result
            monkeypatch.setattr(Retention,'put',interrupted)
        else:
            native=pilot.observe
            def interrupted(*args,**kwargs):
                native(*args,**kwargs);os._exit(71)
            monkeypatch.setattr(pilot,'observe',interrupted)
        try:execute(f,req)
        except BaseException:os._exit(73)
        os._exit(72)
    assert wait_child(child)==71;f.clock[0]=initial+999;calls=list(f.read_calls)
    with pytest.raises(ValueError):execute(f,req)
    assert f.read_calls==calls and not (f.root/'gold-pilot/closure-result.json').exists()


@pytest.mark.parametrize('original_directory',[False,True])
def test_legacy_idle_allows_proved_absence_but_rejects_original_directory_replacement(idle_empty_case,monkeypatch,original_directory):
    from tools import gold_pilot_closure as closure, gold_pilot_reobservation as retry, demo_executor_origin as capture
    f=idle_empty_case
    # No strategy ran and no native marker exists. Choose the independent
    # non-BAR closure input before the first observation of this enrollment.
    assert not (f.root/'gold-pilot-retention').exists()
    f.base_runtime=dict(schema='GOLD_STRATEGY_WORKER_INPUT_V1',source_commit=f.source_commit,
        trial_manifest_sha256=f.base_runtime['trial_manifest_sha256'],observation_sha256='e'*64)
    monkeypatch.setattr(capture,'run',original_origin_run)
    f.original_closure_request=stale_attempt(f,monkeypatch,closure.idle_request(f.base_runtime,f.window))
    directory=f.root/'strategy-decisions'
    assert not directory.exists()
    if original_directory:directory.mkdir(mode=0o700)
    req=build_request(f);child=os.fork()
    if child==0:
        native=retry._save
        def interrupted(root,name,value):
            native(root,name,value)
            if name==retry.INTENT:os._exit(71)
        monkeypatch.setattr(retry,'_save',interrupted)
        try:execute(f,req)
        except BaseException:os._exit(73)
        os._exit(72)
    assert wait_child(child)==71;calls=list(f.read_calls)
    if original_directory:
        old_inode=directory.stat().st_ino
        directory.rename(f.root/'original-empty-strategy-decisions');directory.mkdir(mode=0o700)
        assert directory.stat().st_ino!=old_inode
        with pytest.raises(ValueError,match='REOBSERVATION_LEGACY_CHANGED'):execute(f,req)
        assert f.read_calls==calls
    else:
        result=execute(f,req)
        assert closure.validate_idle_result(result,f.original_closure_request)['status']=='PILOT_STOPPED'
        assert execute(f,req)==result
