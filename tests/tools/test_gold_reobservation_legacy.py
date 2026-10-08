"""Actual legacy origins and custody readers; no new capture during preflight."""
from copy import deepcopy

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.gold_reobservation_fixture import reobservation_case as reobservation_case, interrupt_origin
from tests.live.test_gold_feedback_recovery_fixture import (
    case as case, original_evidence as original_evidence, completed_bar_feedback as completed_bar_feedback,
)
from tests.live.gold_legacy_closure_fixture import (
    legacy_case as legacy_case, completed_legacy_feedback as completed_legacy_feedback,
)
from tests.tools.test_gold_pilot_closure import (
    idle_btc_case as idle_btc_case, idle_case as idle_case, book as book, installed as installed,
    first as first, materials as materials, scope_inputs as scope_inputs,
    day_materials as day_materials, enrollment_case as enrollment_case,
)
from tests.tools.test_gold_feedback_legacy import files


@pytest.mark.parametrize('reobservation_case',['legacy'],indirect=True)
@pytest.mark.parametrize('limit',[None,'records','bytes','record','request_bool'])
def test_legacy_retry_preflight_preserves_sources_and_checks_remaining_mirrors(reobservation_case,monkeypatch,limit):
    from tools import gold_feedback_legacy as legacy, gold_pilot_reobservation as retry
    f=reobservation_case
    old=interrupt_origin(f,monkeypatch)
    args=dict(root=f.controller_root,executor_root=f.root,base_runtime=f.base_runtime,
        current_runtime=f.current_runtime,source_commit=f.source_commit,account=f.account,
        executor_uid=f.registry['executor_uid'])
    before=files(args)
    view=legacy.inspect_reobservation(**args,now_ms=f.clock[0])
    assert view['candidate']['status']=='ORIGIN_ONLY_STALE' and view['reobservation'] is None
    req=retry.request(f.current_runtime,old['intent']['request'],view['candidate'])
    current=legacy._scan(f.controller_root,'strategy-decisions',f.registry['executor_uid'])
    current_count=len(current['records']);current_bytes=sum(len(row['raw']) for row in current['records'].values())
    bounds=retry.budget_bounds(old['intent']['request'])
    if limit=='records':monkeypatch.setattr(legacy,'MAX_RECORDS',current_count+1)
    elif limit=='bytes':monkeypatch.setattr(legacy,'MAX_TOTAL_BYTES',current_bytes+bounds['expired_result']+bounds['result']-1)
    elif limit=='record':monkeypatch.setattr(legacy,'MAX_RECORD_BYTES',min(bounds['expired_result'],bounds['result'])-1)
    elif limit=='request_bool':req=deepcopy(req);req['submission_enabled']=0
    if limit is None:
        budget=legacy.preflight_reobservation(**args,now_ms=f.clock[0],request=req)
        assert budget['status']=='CAPACITY_AVAILABLE' and budget['request_sha256']==_digest(req)
        assert budget['required']['records']==2 and budget['required']['claims']==0
        assert budget['required']['payload_bytes']==bounds['expired_result']+bounds['result']
        assert budget['controller_checkpoint'] is None
    else:
        with pytest.raises(ValueError):legacy.preflight_reobservation(**args,now_ms=f.clock[0],request=req)
    assert files(args)==before and f.capture_count()==1


@pytest.mark.parametrize('reobservation_case',['legacy'],indirect=True)
def test_completed_legacy_chain_uses_no_future_capacity_model(reobservation_case,monkeypatch):
    from tools import gold_feedback_legacy as legacy, gold_pilot_reobservation as retry
    f=reobservation_case;old=interrupt_origin(f,monkeypatch)
    assert f.close_tick()['status']=='PILOT_CLOSED_WITH_FEEDBACK'
    args=dict(root=f.controller_root,executor_root=f.root,base_runtime=f.base_runtime,
        current_runtime=f.current_runtime,source_commit=f.source_commit,account=f.account,
        executor_uid=f.registry['executor_uid'])
    f.clock[0]=f.window['ends_at_ms']+900001
    view=legacy.inspect_reobservation(**args,now_ms=f.clock[0])
    assert view['reobservation']['status']=='COMPLETE'
    req=retry.request(f.current_runtime,old['intent']['request'],view['reobservation'])
    before=files(args);captures=f.capture_count()
    monkeypatch.setattr(retry,'budget_bounds',lambda *a,**kw:pytest.fail('completed chain requested future budget'))
    budget=legacy.preflight_reobservation(**args,now_ms=f.clock[0],request=req)
    assert all(value==0 for value in budget['required'].values())
    assert files(args)==before and f.capture_count()==captures
