from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('pending', [False, True])
def test_strategy_reuses_original_recovery_before_account_snapshot(tmp_path, monkeypatch, pending):
    from tools import gold_strategy_worker as worker
    from live.execution import demo_resident_recovery as recovery
    calls=[]
    stores=dict(recovery=SimpleNamespace(pending=lambda:[object()] if pending else []),
                dispatch=object(),oms=object(),event_store=object(),stream=object())
    report=dict(status='WAITING_QUERY_INTERVAL',new_order_submission_enabled=False)
    class Loop:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def tick(self): return report
    monkeypatch.setattr(recovery,'RecoveryLoop',Loop)
    result=worker.reconcile_pending(tmp_path,stores=stores,source_commit='c'*40)
    if pending:
        assert result == report and len(calls)==1
        assert calls[0]['recovery'] is stores['recovery']
        assert calls[0]['output_directory']==tmp_path/'resident-recovery'
    else:
        assert result is None and calls==[]


def test_worker_reconciliation_preserves_original_durable_query_pacing(tmp_path, monkeypatch):
    from tools import gold_strategy_worker as worker
    from live.execution import demo_resident_recovery as recovery
    from tests.live.test_demo_prepared_execution import ready, NOW
    from tests.live.test_demo_resident_recovery import close
    prepared, args = ready(tmp_path, monkeypatch)
    calls=[]
    original_loop=recovery.RecoveryLoop
    monkeypatch.setattr(recovery,'RecoveryLoop',lambda **kw:original_loop(**kw,clock=lambda:NOW+1000))
    def query(batch, **kwargs):
        assert batch==prepared
        calls.append(batch['reservation_sha256'])
        return dict(status='QUERY_RECOVERY_REQUIRED',reason='synthetic query not yet resolved')
    monkeypatch.setattr(recovery.executor,'query_reserved',query)
    stores={k:args[k] for k in ('dispatch','recovery','oms')}
    stores.update(stream=None,event_store=None)
    try:
        first=worker.reconcile_pending(tmp_path,stores=stores,source_commit='c'*40)
        second=worker.reconcile_pending(tmp_path,stores=stores,source_commit='c'*40)
        assert first['status']=='QUERY_RECOVERY_REQUIRED'
        assert second['status']=='WAITING_QUERY_INTERVAL' and len(calls)==1
        assert len(args['recovery'].pending())==1 and args['dispatch'].wire_snapshot()==[]
    finally:
        close(args)
