"""Original ID, account evidence and OMS recovery stay independently bound."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json

import pytest

from live.execution import demo_reported_recovery as recovery
from live.execution import demo_prepared_execution as execution
from live.execution import demo_oms_reconciliation as oms_recovery
from live.execution.demo_wire_protocol import outcome_for_query
from live.execution.order_event_store import SqliteOrderEventStore
from tests.live.test_demo_prepared_execution import ready
from tests.research.test_demo_account_recovery import inputs
from tests.research.test_binance_order_receipts import receipt
from tests.tools.test_binance_demo_execution_origin import NOW


def fixture(tmp_path, monkeypatch,*,gold=False):
    if gold:
        from tests.live import test_demo_prepared_execution as prepared_fixture
        from tests.live.test_gold_demo_preparation import setup as gold_setup
        monkeypatch.setattr(prepared_fixture,'setup',gold_setup)
    batch, stores = ready(tmp_path, monkeypatch)
    folder = tmp_path / 'account'; folder.mkdir()
    if gold:
        from tests.research.test_gold_demo_account_recovery import inputs as gold_inputs
    value = (gold_inputs if gold else inputs)(folder)
    proof = execution._load_proof(batch)
    value['opening'] = proof['origin']
    if gold:
        # Match the independently generated synthetic gold account fixture to
        # the synthetic signed account; never transform production evidence.
        value['closing']['account_ref_sha256']=proof['context']['account_ref_sha256']
        for read in value['closing']['reads']:
            if read['account_ref_sha256'] is not None:
                read['account_ref_sha256']=proof['context']['account_ref_sha256']
        for window in value['windows']:
            window['account_ref_sha256']=proof['context']['account_ref_sha256']
    value['system_orders'] = proof['orders']
    risk = proof['risk']['input_snapshot']
    from decimal import Decimal
    value['adv_notional'] = str(Decimal(risk['market']['daily_volume_base']) * Decimal(risk['market']['reference_price']))
    value['participation_rate'] = str(risk['native_limits']['max_participation_rate'])
    o = batch['orders'][0]
    event = json.loads(value['windows'][0]['messages'][0]['raw_utf8'])
    event['o'].update(c=o['newClientOrderId'], f=o['timeInForce'])
    value['windows'][0]['messages'][0] = receipt(event, NOW + 10005)
    monkeypatch.setattr(execution.binance_demo_exact if hasattr(execution,'binance_demo_exact') else execution.transport,
                        '_http_once', lambda *a: (503, b'{}'))
    execution.execute(batch, **stores)
    raw = {'symbol':o['symbol'], 'clientOrderId':o['newClientOrderId'], 'orderId':21,
           'side':o['side'], 'origQty':o['quantity'], 'price':o['price'], 'positionSide':o['positionSide'],
           'reduceOnly':o['reduceOnly'], 'timeInForce':o['timeInForce'], 'type':o['type'],
           'status':'FILLED', 'executedQty':'0.002', 'time':NOW+1, 'updateTime':NOW+10000}
    clock_raw = json.dumps({'serverTime':NOW+89999})
    clock = dict(schema='DEMO_ORIGINAL_QUERY_CLOCK_V1', requested_at_ms=NOW+89999,received_at_ms=NOW+89999,
                 server_time_ms=NOW+89999, response_sha256=hashlib.sha256(clock_raw.encode()).hexdigest(),
                 source_sha256='a'*64, raw_utf8=clock_raw)
    stores['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
        client_order_id=o['newClientOrderId'], requested_at_ms=NOW+90000,
        outcome=outcome_for_query(o, 200, json.dumps(raw).encode(),
            **({'schema':'DEMO_GOLD_EXACT_ORDER_BATCH_V1'} if gold else {})), now_ms=NOW+90001, clock=clock)
    return batch, stores, value


def close(stores):
    for k in ('dispatch','recovery','oms'): stores[k].close()


def test_complete_account_evidence_projects_original_system_order_once(tmp_path,monkeypatch):
    batch, stores, value = fixture(tmp_path,monkeypatch)
    events = SqliteOrderEventStore(str(tmp_path/'events.sqlite3'), create=True)
    try:
        plan = recovery.prepare(batch, dispatch=stores['dispatch'], inputs=value, as_of_ms=NOW+100000)
        assert plan['account_report']['manual_order_ids']==['22'] and plan['cash_reconciled']
        assert len(plan['account_report']['stream_gaps'])==2 and not plan['automatic_cycle_completed']
        assert stores['oms'].fills()==[]
        args = dict(prepared=batch, dispatch=stores['dispatch'], recovery=stores['recovery'],
                    oms=stores['oms'], event_store=events, now_ms=NOW+100000)
        oms_recovery._project_resolution(plan, **args)
        before=events.row_count()
        oms_recovery._project_resolution(plan, **args)
        assert events.row_count()==before and len(stores['oms'].fills())==1
        assert stores['oms'].orders()[0]['status']=='FILLED' and not stores['recovery'].pending()
        # Manual close belongs to account cash, not a fabricated system intent.
        assert len(stores['oms'].orders())==1
        from live.execution import demo_position_context as position
        from live.execution.demo_pre_submit import _retain, _state
        _retain(tmp_path/'receipt-reconciliation/plans', plan)
        _retain(tmp_path/'receipt-reconciliation/outcomes', oms_recovery._project_resolution(plan, **args))
        (tmp_path/'receipt-reconciliation/receipts/imports').mkdir(parents=True)
        monkeypatch.setattr(position, '_ROOT', tmp_path)
        monkeypatch.setattr(position, '_custody', lambda *a, **kw: None)
        state=_state(stores['dispatch'],stores['recovery'],stores['oms'])
        context=position.load_context(state, symbol='BTCUSDC',account_ref=batch['context']['account_ref_sha256'],as_of_ms=NOW+100000)
        assert context['quantity']=='0' and context['canonical_wallet']=='4999.6801'
        assert context['initial_equity']=='5000'
        assert sum(map(Decimal,context['capacity_consumed'].values()))==Decimal('239.8')
        assert context['reported_account_prefix']['manual_order_ids']==['22']
    finally: events.close();close(stores)


@pytest.mark.parametrize('damage',['original_origin','capacity','unknown_query','future','missing_native','intent'])
def test_account_resolution_never_waives_original_binding(tmp_path,monkeypatch,damage):
    batch, stores, value=fixture(tmp_path,monkeypatch)
    try:
        at=NOW+100000
        if damage=='original_origin':value['opening']=deepcopy(value['closing'])
        elif damage=='capacity':value['adv_notional']='99999999'
        elif damage=='unknown_query':
            stores['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
                client_order_id=batch['orders'][0]['newClientOrderId'],requested_at_ms=NOW+91000,
                outcome=outcome_for_query(batch['orders'][0],None,None),now_ms=NOW+91001)
        elif damage=='future':at=NOW+80000
        elif damage=='missing_native':value['windows'][0]['messages']=[]
        else:value['system_orders']=deepcopy(value['system_orders']);value['system_orders'][0]['quantity']='0.003'
        with pytest.raises(ValueError):recovery.prepare(batch,dispatch=stores['dispatch'],inputs=value,as_of_ms=at)
        assert stores['oms'].fills()==[] and len(stores['recovery'].pending())==1
    finally:close(stores)
