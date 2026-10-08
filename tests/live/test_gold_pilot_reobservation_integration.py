"""A stale retained closure origin may consume only one finite new observation."""
import json
import os

import pytest

from live.execution.demo_batch_risk import _digest
from tests.live.gold_reobservation_fixture import (
    reobservation_case as reobservation_case, interrupt_origin, assert_preserved, native_snapshot,
)
from tests.live.test_gold_feedback_recovery_fixture import (
    case as case, original_evidence as original_evidence, completed_bar_feedback as completed_bar_feedback,
)
from tests.live.gold_legacy_closure_fixture import (
    legacy_case as legacy_case, completed_legacy_feedback as completed_legacy_feedback,
)
from tests.tools.test_gold_pilot_closure import (
    idle_empty_case as idle_empty_case, opening_case as opening_case,
    idle_btc_case as idle_btc_case, idle_case as idle_case, book as book, installed as installed,
    first as first, materials as materials, scope_inputs as scope_inputs,
    day_materials as day_materials, enrollment_case as enrollment_case,
)


@pytest.mark.parametrize('reobservation_case',['bar','historical','btc_idle','legacy','legacy_idle'],indirect=True)
def test_stale_origin_without_stop_gets_one_native_successor_and_closes(reobservation_case,monkeypatch):
    from tools import gold_pilot_closure as closure, gold_pilot_run as runner
    from tools.gold_feedback_recovery import FeedbackOwner
    f=reobservation_case
    old=interrupt_origin(f,monkeypatch)
    if f.kind=='legacy_idle':
        assert not (f.root/'strategy-decisions').exists()
        assert not (f.root/'gold-pilot-retention').exists()
    assert f.capture_count()==1
    result=f.close_tick()
    assert result['status']==('PILOT_CLOSED_WITHOUT_EXECUTION' if f.idle else 'PILOT_CLOSED_WITH_FEEDBACK'),(
        result,f.diagnostics)
    observation=result['ticks'][0]['observation']
    final=observation if f.idle else observation['pilot_closure']
    if f.idle:closure.validate_idle_result(final,old['intent']['request'])
    else:
        closure.validate_result(final,f.result)
        assert observation['decision']==f.result['decision'] and observation['research_feedback']==f.receipt
    assert final['closure_request']==old['intent']['request']
    assert final['closure_intent_sha256']!=_digest(old['intent'])
    assert final['decision']['origin_sha256']!=_digest(old['origin'])
    assert final['decision']['observed_at_ms']>old['origin']['completed_at_ms']+30000
    stop=json.loads((f.root/'gold-pilot/stopped.json').read_bytes())
    assert stop==final['decision']['pilot_control'] and stop['origin_sha256']==final['decision']['origin_sha256']
    assert f.capture_count()==2
    assert_preserved(f,old)
    with FeedbackOwner() as owner:
        owner.bind(root=f.controller_root,base_runtime=f.base_runtime,source_commit=f.source_commit,
            account=f.account,registry=f.registry)
        view=owner.discover(current_runtime=f.current_runtime,terminal=True)
        assert view['closure']['status']=='COMPLETE' and view['closure']['result']==final
        if not f.idle:assert view['result_bytes']==f.result_bytes and view['delivery']==f.receipt
        if not f.legacy:
            assert view['executor_checkpoint']['frames']==view['controller_checkpoint']['frames']==old['claim']['ordinal']+1
            expired_raw=owner.retention._feedback_source.read_claim(old['claim'],'result')
            expired=json.loads(expired_raw)
            assert expired['schema']=='GOLD_PILOT_CLOSURE_EXPIRED_RESULT_V1'
            assert expired['status']=='EXPIRED_BEFORE_STOP'
            assert expired['decision']['current_account_verified'] is expired['decision']['pilot_stopped'] is False
            assert not any(k in expired['decision'] for k in ('pilot_control','position_flat','cash_reconciled'))
            assert owner.retention.store.read_claim(old['claim'],'result')==expired_raw
    checkpoint=None if f.legacy else native_snapshot(f)
    completed=runner.run(source_commit=f.source_commit,max_seconds=60)
    assert completed['pilot_complete'] is True and completed['status']=='PILOT_STOPPED',completed
    assert f.capture_count()==2
    assert_preserved(f,old)
    if checkpoint is not None:assert native_snapshot(f)==checkpoint


@pytest.mark.parametrize('reobservation_case',['bar'],indirect=True)
def test_started_reobservation_with_no_origin_never_repeats_external_capture(reobservation_case,monkeypatch):
    from tools import demo_executor_origin as origin
    f=reobservation_case;old=interrupt_origin(f,monkeypatch)
    with monkeypatch.context() as patch:
        def interrupted(**kwargs):
            witness=json.loads((f.root/'gold-pilot/closure-reobservation-capture.json').read_bytes())
            assert witness['schema']=='GOLD_PILOT_CLOSURE_REOBSERVATION_CAPTURE_V1'
            assert witness['maximum_capture_attempts']==1
            os._exit(72)
        patch.setattr(origin,'_report',interrupted)
        outcome=f.close_tick()
    assert f.children[-1].returncode==72,(outcome,f.diagnostics)
    assert f.capture_count()==1  # The original capture only; no successor GET.
    before=native_snapshot(f)
    assert before['metadata']['frames']==old['claim']['ordinal']+1
    for _ in range(2):
        f.clock[0]+=1000
        found=f.close_tick()
        assert found['status'] not in ('PILOT_CLOSED_WITH_FEEDBACK','PILOT_CLOSED_WITHOUT_EXECUTION','PILOT_STOPPED')
        assert f.capture_count()==1 and native_snapshot(f)==before
        assert_preserved(f,old)
        assert not (f.root/'gold-pilot/stopped.json').exists()


@pytest.mark.parametrize('reobservation_case',['bar'],indirect=True)
def test_unique_successor_becoming_stale_is_exhausted_without_third_claim(reobservation_case,monkeypatch):
    from tools.gold_pilot_retention import executor_retention
    from tools.gold_pilot_retention_store import Retention
    f=reobservation_case;old=interrupt_origin(f,monkeypatch)
    native=Retention.put
    with monkeypatch.context() as patch:
        def interrupted(self,claim,kind,value):
            result=native(self,claim,kind,value)
            if kind=='origin':
                assert claim['ordinal']==old['claim']['ordinal']+1
                assert (f.root/'gold-pilot/closure-reobservation-capture.json').is_file()
                os._exit(73)
            return result
        patch.setattr(Retention,'put',interrupted)
        outcome=f.close_tick()
    assert f.children[-1].returncode==73,(outcome,f.diagnostics)
    assert f.capture_count()==2
    with executor_retention(f.root,f.current_runtime,source_commit=f.source_commit,
            account=f.account,uid=f.executor_uid) as store:
        claim=json.loads(store.conn.execute('SELECT claim FROM frames ORDER BY ordinal DESC LIMIT 1').fetchone()[0])
        raw=store.read_claim(claim,'origin')
        assert raw is not None and store.read_claim(claim,'decision') is store.read_claim(claim,'result') is None
    successor=json.loads(raw)
    f.clock[0]=successor['completed_at_ms']+31001
    assert f.clock[0]<f.window['ends_at_ms']+900000
    before=native_snapshot(f)
    for _ in range(2):
        found=f.close_tick()
        assert found['status'] not in ('PILOT_CLOSED_WITH_FEEDBACK','PILOT_CLOSED_WITHOUT_EXECUTION','PILOT_STOPPED')
        assert f.capture_count()==2 and native_snapshot(f)==before
        assert_preserved(f,old)
        assert not (f.root/'gold-pilot/stopped.json').exists()
