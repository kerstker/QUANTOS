"""The fixed-account boundary retains captures before an idempotent projection."""
import json
from pathlib import Path
import pytest

from tools import demo_reported_recovery as tool, demo_executor_statement, demo_executor_origin
from live.execution import demo_position_context
from live.execution.demo_pre_submit import _retain
from live.execution.order_event_store import SqliteOrderEventStore
from services.research.demo_user_stream import UserStreamJournal, normalize_message
from tests.live.test_demo_reported_recovery import fixture, close
from tests.tools.test_binance_demo_execution_origin import NOW


@pytest.mark.parametrize('damage',[None,'account','closing_source','second_sweep','after_projection'])
def test_fixed_parent_recovery_uses_complete_captured_inputs_and_no_new_send(tmp_path,monkeypatch,damage):
    batch,stores,value=fixture(tmp_path,monkeypatch)
    stream=UserStreamJournal(tmp_path/'stream.sqlite3',account_ref_sha256=batch['context']['account_ref_sha256'])
    events=SqliteOrderEventStore(str(tmp_path/'events.sqlite3'),create=True)
    stream.start_session(source_sha256='a'*64,at_ms=NOW)
    for window in value['windows']:
        row=window['messages'][0]
        stream.record('MESSAGE',at_ms=row['received_at_ms'],message=normalize_message(row['raw_utf8']))
        stream.record('GAP',at_ms=row['received_at_ms']+1,reason='MESSAGE_REJECTED',response_sha256=window['gaps'][0]['response_sha256'])
    original_stream=stream.verify();wire=stores['dispatch'].evidence_snapshot()
    monkeypatch.setattr(tool,'require_custody',lambda:tmp_path)
    monkeypatch.setattr(tool,'fixed_account',lambda:(tmp_path,batch['context']['account_ref_sha256']))
    monkeypatch.setattr(tool,'_check_path',lambda *a,**kw:None)
    monkeypatch.setattr(tool.execution,'_require_state_custody',lambda *a:None)
    monkeypatch.setattr(tool.execution,'_require_receipt_store_custody',lambda *a:None)
    monkeypatch.setattr(tool.execution,'query_reserved',lambda *a,**kw:None)
    monkeypatch.setattr(tool,'_open',lambda stack,root:dict(dispatch=stores['dispatch'],recovery=stores['recovery'],
        oms=stores['oms'],stream=stream,event_store=events))
    monkeypatch.setattr(tool,'now_ms',lambda:NOW+100000)
    monkeypatch.setattr(tool.time,'sleep',lambda _:None)
    monkeypatch.setattr(demo_position_context,'_ROOT',tmp_path)
    monkeypatch.setattr(demo_position_context,'_custody',lambda *a,**kw:None)
    (tmp_path/'receipt-reconciliation/receipts/imports').mkdir(parents=True)
    value['closing']['source_sha256']=demo_executor_origin.source_hash() if damage!='closing_source' else 'f'*64
    from services.research.demo_execution_origin import save
    closing=save(tmp_path/'execution-origins/BTCUSDC',value['closing'])
    monkeypatch.setattr(demo_executor_origin,'run',lambda **kw:dict(status='CAPTURED',path=str(closing)))
    observations=[]
    for i,s in enumerate(value['statements']):
        if damage=='second_sweep' and i==1:s=value['statements'][0]
        account='f'*64 if damage=='account' else batch['context']['account_ref_sha256']
        wrapper=demo_executor_statement._report(account,'BTCUSDC',s['start_ms'],s['end_ms'],s['observed_at_ms'],s['completed_at_ms'],s)
        observations.append(_retain(tmp_path/'receipt-reconciliation/statements',wrapper))
    calls=[]
    def capture(**kw):
        path=observations[len(calls)];calls.append(kw)
        return dict(status='CAPTURED',path=str(path))
    monkeypatch.setattr(demo_executor_statement,'run',capture)
    try:
        if damage=='after_projection':
            original_retain=tool._retain
            def crash(path,value):
                if Path(path).name=='outcomes':raise OSError('isolated post-projection crash')
                return original_retain(path,value)
            monkeypatch.setattr(tool,'_retain',crash)
            with pytest.raises(OSError):tool.run(source_commit='a'*40)
            assert len(stores['oms'].fills())==1 and not stores['recovery'].pending()
            monkeypatch.setattr(tool,'_retain',original_retain)
            assert tool.run(source_commit='a'*40)['status']=='MANUAL_ACCOUNT_RECONCILED'
            assert len(calls)==2 and len(stores['oms'].fills())==1
        elif damage:
            with pytest.raises(ValueError):tool.run(source_commit='a'*40)
            assert stores['oms'].fills()==[] and len(stores['recovery'].pending())==1
        else:
            result=tool.run(source_commit='a'*40)
            assert result['status']=='MANUAL_ACCOUNT_RECONCILED' and result['cash_reconciled']
            assert len(calls)==2 and result['fill_count']==2
            assert tool.run(source_commit='a'*40)==result
            assert len(calls)==2 and len(stores['oms'].fills())==1
            from services.research.demo_oms_status import load_latest
            view=load_latest(tmp_path/'receipt-reconciliation/views')
            assert view['phase']=='OMS_ACCOUNT_RECONCILED' and view['projected_orders']==1
            assert view['manifest_hash'] is None and view['journal_tail'] is None
            assert view['cash_reconciled'] is False
        assert stores['dispatch'].evidence_snapshot()==wire and stream.verify()==original_stream
    finally:events.close();stream.db.close();close(stores)


def test_fixed_day_capture_propagates_bounded_schema_without_changing_default(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from tests.research.test_demo_account_recovery import inputs
    from tests.research.test_demo_account_days import later_close,DAY
    from services.research.demo_execution_origin import save
    from services.research.demo_account_recovery import reconcile_days
    value=later_close(inputs(tmp_path));account=value['opening']['account_ref_sha256']
    monkeypatch.setattr(tool,'_check_path',lambda *a,**kw:None)
    monkeypatch.setattr(tool,'now_ms',lambda:NOW+DAY+100000)
    monkeypatch.setattr(tool.time,'sleep',lambda _:None)
    value['closing']['source_sha256']=demo_executor_origin.source_hash()
    closing=save(tmp_path/'execution-origins/BTCUSDC',value['closing'])
    monkeypatch.setattr(demo_executor_origin,'run',lambda **kw:dict(status='CAPTURED',path=closing))
    calls=[]
    def capture(**kw):
        assert kw['day_replay'] is True
        s=value['statements'][len(calls)];calls.append(kw)
        wrapper=demo_executor_statement._report(account,'BTCUSDC',s['start_ms'],s['end_ms'],
            s['observed_at_ms'],s['completed_at_ms'],s,day_replay=True)
        return dict(status='CAPTURED',path=_retain(tmp_path/'statements',wrapper))
    monkeypatch.setattr(demo_executor_statement,'run',capture)
    stream=SimpleNamespace(export_order_messages=lambda **kw:next(w for w in value['windows'] if w['start_ms']==kw['start_ms']))
    args=dict(opening=value['opening'],system_orders=value['system_orders'],adv_notional=value['adv_notional'],
        participation_rate=value['participation_rate'],stream=stream)
    with pytest.raises(ValueError,match='ACCOUNT_CAPTURE_WINDOW_BUDGET'):
        tool.capture_inputs(tmp_path,account,'c'*40,**args)
    assert calls==[]
    captured=tool.capture_inputs(tmp_path,account,'c'*40,**args,day_replay=True)
    assert len(calls)==2 and reconcile_days(**captured)[0]['status']=='REPORTED_ACCOUNT_MATCHED'
