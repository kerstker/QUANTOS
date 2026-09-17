"""A later UTC day requires immutable full-account evidence, never a reset."""
from copy import deepcopy
from decimal import Decimal
import pytest

from tests.research.test_demo_account_recovery import inputs
from tests.research.test_demo_account_days import later_close, DAY
from tests.tools.test_binance_demo_execution_origin import NOW
from services.research.demo_account_recovery import reconcile
from services.research.demo_origin_contract import contract_from_origin
from live.execution.demo_batch_risk import _digest


def fixture(tmp_path):
    value = inputs(tmp_path); report, _ = reconcile(**value)
    sources = [dict(reservation_sha256='a'*64, plan_sha256='b'*64, manifest_hash=None, journal_tail=None)]
    context = dict(symbol='BTCUSDC',account_ref_sha256=value['opening']['account_ref_sha256'],
        sources=sources,ledger_state_hash=_digest(report['ledger_state']),canonical_wallet=report['canonical_wallet'],
        quantity='0',day_id=NOW//DAY,available_at_ms=NOW+100000,
        contract=contract_from_origin(value['opening'])[0],adv_notional=value['adv_notional'],
        participation_rate=value['participation_rate'],reported_account_prefix={'manual_order_ids':['22']})
    proofs = [dict(origin=value['opening'],orders=value['system_orders'])]
    return context, proofs, later_close(value)


def test_day_checkpoint_binds_original_book_and_revalidates_without_effects(tmp_path):
    from live.execution.demo_day_checkpoint import prepare, validate
    context,proofs,value = fixture(tmp_path); before=deepcopy((context,proofs,value))
    checkpoint=prepare(context=context,proofs=proofs,inputs=value,created_at_ms=NOW+DAY+100000,source_commit='c'*40)
    report=validate(checkpoint,context=context,proofs=proofs,as_of_ms=NOW+DAY+100001)
    assert report['canonical_wallet']=='4999.6801' and report['daily_cash'][-1]['net_after_costs']=='0'
    assert checkpoint['day_id']==context['day_id']+1
    assert not checkpoint['submission_enabled'] and not checkpoint['risk_budget_reset']
    assert (context,proofs,value)==before


@pytest.mark.parametrize('damage',['wallet','ledger','sources','manual','future','same_day','opening','orders','terms','report'])
def test_missing_or_rebound_prefix_is_rejected(tmp_path,damage):
    from live.execution.demo_day_checkpoint import prepare,validate
    context,proofs,value=fixture(tmp_path)
    record=prepare(context=context,proofs=proofs,inputs=value,created_at_ms=NOW+DAY+100000,source_commit='c'*40)
    if damage=='wallet':context['canonical_wallet']='5000'
    elif damage=='ledger':context['ledger_state_hash']='e'*64
    elif damage=='sources':context['sources'][0]['reservation_sha256']='e'*64
    elif damage=='manual':context['reported_account_prefix']['manual_order_ids']=[]
    elif damage=='future':record['created_at_ms']+=DAY
    elif damage=='same_day':context['day_id']+=1
    elif damage=='opening':proofs[0]['origin']=value['closing']
    elif damage=='orders':proofs[0]['orders'][0]['quantity']='0.003'
    elif damage=='terms':context['adv_notional']='2000000'
    else:record['account_report']['daily_cash'][-1]['opening_wallet']='5000'
    with pytest.raises(ValueError):validate(record,context=context,proofs=proofs,as_of_ms=NOW+DAY+100001)


def recovered_context(tmp_path, monkeypatch):
    from tests.live.test_demo_reported_recovery import fixture as recovered
    from live.execution import demo_reported_recovery, demo_oms_reconciliation, demo_position_context as position
    from live.execution.demo_pre_submit import _state, _retain
    from live.execution.order_event_store import SqliteOrderEventStore
    batch, stores, value = recovered(tmp_path, monkeypatch)
    events = SqliteOrderEventStore(str(tmp_path/'events.sqlite3'), create=True)
    plan = demo_reported_recovery.prepare(batch, dispatch=stores['dispatch'], inputs=value, as_of_ms=NOW+100000)
    outcome = demo_oms_reconciliation._project_resolution(plan, prepared=batch, dispatch=stores['dispatch'],
        recovery=stores['recovery'], oms=stores['oms'], event_store=events, now_ms=NOW+100000)
    _retain(tmp_path/'receipt-reconciliation/plans', plan)
    _retain(tmp_path/'receipt-reconciliation/outcomes', outcome)
    (tmp_path/'receipt-reconciliation/receipts/imports').mkdir(parents=True)
    monkeypatch.setattr(position, '_ROOT', tmp_path)
    monkeypatch.setattr(position, '_custody', lambda *a, **kw: None)
    state = _state(stores['dispatch'],stores['recovery'],stores['oms'])
    context = position.load_context(state,symbol='BTCUSDC',account_ref=batch['context']['account_ref_sha256'],as_of_ms=NOW+100001)
    return batch,stores,events,value,state,context


def test_protected_context_loads_day_evidence_without_resetting_original_risk(tmp_path,monkeypatch):
    import json
    from live.execution import demo_day_checkpoint, demo_position_context as position
    from live.execution.demo_pre_submit import _retain, _state
    from tests.live.test_demo_reported_recovery import close
    batch,stores,events,value,state,original = recovered_context(tmp_path,monkeypatch)
    try:
        proof=json.loads(__import__('pathlib').Path(batch['proposal_path']).read_text())
        day_inputs=later_close(value)
        record=demo_day_checkpoint.prepare(context=original,proofs=[proof],inputs=day_inputs,
            created_at_ms=NOW+DAY+100000,source_commit='c'*40)
        _retain(tmp_path/'receipt-reconciliation/day-checkpoints',record)
        current=position.load_context(state,symbol='BTCUSDC',account_ref=original['account_ref_sha256'],as_of_ms=NOW+DAY+100001)
        assert current['day_id']==original['day_id']+1
        assert current['initial_equity']==original['initial_equity']=='5000'
        assert current['ledger_state_hash']==original['ledger_state_hash']
        assert current['capacity_consumed']==original['capacity_consumed']
        assert current['day_open_equity']=='4999.6801'
        assert current['available_at_ms']==record['created_at_ms']
        assert _state(stores['dispatch'],stores['recovery'],stores['oms'])==state
        # A fresh same-day flat observation still carries the original loss.
        origin=deepcopy(day_inputs['closing'])
        def shift(v):
            if isinstance(v,dict):return {k:shift(x) for k,x in v.items()}
            if isinstance(v,list):return [shift(x) for x in v]
            return v+60000 if type(v) is int and v>=NOW+DAY else v
        origin=shift(origin)
        account=position.validate_origin(origin,current,as_of_ms=origin['completed_at_ms'])
        assert account['day_pnl']=='-0.3199'
        with pytest.raises(ValueError):position.validate_origin(origin,original,as_of_ms=origin['completed_at_ms'])
    finally:events.close();close(stores)
