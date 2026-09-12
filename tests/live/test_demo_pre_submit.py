"""Independent verification -> exact risk/mandate -> durable intent barriers."""
import json
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading

import pytest

from live.execution import demo_authority as boundary, demo_pre_submit as submit
from live.execution.demo_dispatch_store import DemoDispatchStore
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
from live.governance.mandate import Mandate
from live.governance.validation_attestation import ValidationAttestation,VALIDATION_PASS
from services.validation.demo_submission import signing_payload
from tests.live.test_demo_submission_verification import material,encoded
from tests.live.test_demo_batch_risk import inputs
from tests.tools.test_binance_demo_execution_origin import collect,NOW


def setup(tmp_path,monkeypatch):
    private,registry,orders,_,envelope=material()
    registry['keys'][0].update(valid_from_ms=NOW-10000,valid_until_ms=NOW+100000)
    monkeypatch.setattr(boundary,'_read_root_owned_json',lambda path:json.dumps(registry).encode())
    monkeypatch.setattr(boundary.os,'geteuid',lambda:2001)
    origin,_=collect(tmp_path);risk=inputs();ref=origin['account_ref_sha256']
    # Explicit isolated stop history. Production fixed-path custody is covered
    # separately; no default state is synthesized by the execution boundary.
    from live.execution import demo_current_kill
    from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore
    stop_path=tmp_path/'kill-switch.sqlite3'
    if not stop_path.exists():
        stop_store=KillSwitchStateStore(str(stop_path),create=True)
        try:stop_store.bootstrap(risk['kill_switch'].state,recorded_at=0)
        finally:stop_store.close()
    monkeypatch.setattr(demo_current_kill,'_PATH',stop_path)
    monkeypatch.setattr(demo_current_kill,'_custody',lambda:None)
    risk.update(now_ms=NOW);risk['account']['account_ref_sha256']=ref;risk['market']['observed_at_ms']=NOW
    risk['native_limits']=replace(risk['native_limits'],allowed_accounts=frozenset({ref}))
    sid=risk['strategy'].strategy_id
    mandate=Mandate('isolated-fixture','testnet',frozenset({'BTCUSDC'}),exchange='binance',
        max_single_order_notional=200,max_gross_notional=200,max_net_notional=200,
        approved_by=('isolated-test-operator',),issued_at=NOW-1000,expires_at=NOW+30000)
    attestations={sid:ValidationAttestation(sid,VALIDATION_PASS,'isolated-test-authority',NOW-1000,validation_result_hash='e'*64)}
    kwargs=dict(orders=orders,origin=origin,risk_inputs=risk,mandate=mandate,attestations=attestations,now_ms=NOW)
    return private,envelope,kwargs


def signed(private,envelope,proposal):
    envelope['payload'].update(context=proposal['context'],issued_at_ms=NOW-1000,expires_at_ms=NOW+30000)
    envelope['signature_base64url']=encoded(private.sign(signing_payload(envelope)))
    return json.dumps(envelope).encode()


def stores(tmp_path):
    return (DemoDispatchStore(tmp_path/'dispatch.db',create=True),
            SqliteOrderRecoveryStore(str(tmp_path/'recovery.db'),create=True),
            LiveLedger(str(tmp_path/'oms.db'),create=True))


def test_prepared_batch_has_all_order_intents_before_any_dispatch_and_no_cash_writes(tmp_path,monkeypatch):
    private,envelope,kwargs=setup(tmp_path,monkeypatch)
    dispatch,recovery,oms=stores(tmp_path)
    try:
        proposal=submit.proposal(**kwargs,dispatch=dispatch,recovery=recovery,oms=oms)
        result=submit.prepare(signed(private,envelope,proposal),**kwargs,dispatch=dispatch,recovery=recovery,oms=oms,
                              evidence_directory=tmp_path/'evidence')
        assert result['status']=='INTENTS_PERSISTED' and result['submission_enabled'] is False
        assert len(dispatch.snapshot())==len(recovery.pending())==len(oms.orders())==1
        assert oms.orders()[0]['status']=='PENDING_NEW' and oms.fills()==[]
        assert len(list((tmp_path/'evidence').glob('*.json')))==1
        with pytest.raises(submit.DemoPreparationRejected):
            submit.prepare(signed(private,envelope,proposal),**kwargs,dispatch=dispatch,recovery=recovery,oms=oms,
                           evidence_directory=tmp_path/'evidence')
        assert len(dispatch.snapshot())==1
    finally:
        for store in (dispatch,recovery,oms):store.close()


@pytest.mark.parametrize('failure',['signature','risk','mandate','attestation','wallet_binding','market_binding'])
def test_rejected_inputs_do_not_consume_or_write_order_intents(tmp_path,monkeypatch,failure):
    private,envelope,kwargs=setup(tmp_path,monkeypatch);dispatch,recovery,oms=stores(tmp_path)
    try:
        proposal=submit.proposal(**kwargs,dispatch=dispatch,recovery=recovery,oms=oms)
        grant=signed(private,envelope,proposal)
        if failure=='signature':grant=grant.replace(envelope['signature_base64url'].encode(),encoded(b'\0'*64).encode())
        elif failure=='risk':kwargs['risk_inputs']['account']['day_pnl']='-20'
        elif failure=='mandate':kwargs['mandate']=None
        elif failure=='attestation':kwargs['attestations']={}
        elif failure=='wallet_binding':kwargs['risk_inputs']['account']['cash']='6000'
        else:kwargs['risk_inputs']['market']['reference_price']='65000'
        with pytest.raises((submit.DemoPreparationRejected,ValueError)):
            submit.prepare(grant,**kwargs,dispatch=dispatch,recovery=recovery,oms=oms,evidence_directory=tmp_path/'evidence')
        assert dispatch.snapshot()==[] and recovery.latest_records()==() and oms.orders()==[]
    finally:
        for store in (dispatch,recovery,oms):store.close()


def test_crash_after_consumption_keeps_all_client_ids_spent_for_query_recovery(tmp_path,monkeypatch):
    private,envelope,kwargs=setup(tmp_path,monkeypatch);dispatch,recovery,oms=stores(tmp_path)
    try:
        proposal=submit.proposal(**kwargs,dispatch=dispatch,recovery=recovery,oms=oms)
        grant=signed(private,envelope,proposal)
        monkeypatch.setattr(oms,'record_submission',lambda *a,**k:(_ for _ in ()).throw(OSError('simulated disk failure')))
        with pytest.raises(submit.DemoPreparationRejected):
            submit.prepare(grant,**kwargs,dispatch=dispatch,recovery=recovery,oms=oms,evidence_directory=tmp_path/'evidence')
        assert len(dispatch.snapshot())==1 and len(recovery.pending())==1
        assert dispatch.snapshot()[0]['payload']['orders']==kwargs['orders']
        assert oms.orders()==[]
    finally:
        for store in (dispatch,recovery,oms):store.close()


def test_two_batches_cannot_prepare_against_the_same_pre_reservation_risk_state(tmp_path,monkeypatch):
    private,envelope,first=setup(tmp_path,monkeypatch);second=deepcopy(first)
    second['orders'][0]['newClientOrderId']='QDtest000002'
    dispatch,recovery,oms=stores(tmp_path)
    try:
        first_grant=signed(private,envelope,submit.proposal(**first,dispatch=dispatch,recovery=recovery,oms=oms))
        envelope['payload']['approval_id']='d'*64
        second_grant=signed(private,envelope,submit.proposal(**second,dispatch=dispatch,recovery=recovery,oms=oms))
    finally:
        for store in (dispatch,recovery,oms):store.close()
    entered,release=threading.Event(),threading.Event();retain=submit._retain
    def hold(directory,proposal):
        if proposal['orders'][0]['newClientOrderId']=='QDtest000001':
            entered.set();assert release.wait(5)
        return retain(directory,proposal)
    monkeypatch.setattr(submit,'_retain',hold)
    def prepare(grant,values):
        ds=DemoDispatchStore(tmp_path/'dispatch.db');rs=SqliteOrderRecoveryStore(str(tmp_path/'recovery.db'));ls=LiveLedger(str(tmp_path/'oms.db'))
        try:
            submit.prepare(grant,**values,dispatch=ds,recovery=rs,oms=ls,evidence_directory=tmp_path/'evidence')
            return 'PREPARED'
        except submit.DemoPreparationRejected:return 'BLOCKED'
        finally:
            for store in (ds,rs,ls):store.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(prepare,first_grant,first)
        assert entered.wait(5)
        try:other=pool.submit(prepare,second_grant,second).result(timeout=3)
        finally:release.set()
        result=pending.result(timeout=3)
    assert (result,other)==('PREPARED','BLOCKED')


def test_venue_grid_is_anchored_at_its_minimum_and_unknown_caps_remain_blocked(tmp_path,monkeypatch):
    _,_,kwargs=setup(tmp_path,monkeypatch)
    origin=kwargs['origin'];orders=kwargs['orders']
    rules=next(r for r in origin['reads'] if r['kind']=='rules')['data']
    lot=next(f for f in rules['terms']['filters'] if f['filterType']=='LOT_SIZE')
    lot.update(minQty='0.002',stepSize='0.003');orders[0]['quantity']='0.003'
    with pytest.raises(submit.DemoPreparationRejected):submit._venue_rules(orders,origin)
    orders[0]['quantity']='0.005'
    submit._venue_rules(orders,origin)
    rules['filter_types'].append('MAX_NUM_ORDERS')
    with pytest.raises(submit.DemoPreparationRejected):submit._venue_rules(orders,origin)


def test_venue_maximum_open_orders_is_checked_from_the_retained_term(tmp_path,monkeypatch):
    _,_,kwargs=setup(tmp_path,monkeypatch)
    rules=next(r for r in kwargs['origin']['reads'] if r['kind']=='rules')['data']
    rules['filter_types'].append('MAX_NUM_ORDERS');rules['order_count_limits']={'MAX_NUM_ORDERS':1}
    submit._venue_rules(kwargs['orders'],kwargs['origin'])
    kwargs['orders'].append(dict(kwargs['orders'][0],newClientOrderId='QDtest000002'))
    with pytest.raises(submit.DemoPreparationRejected,match='DEMO_VENUE_ORDER_COUNT'):
        submit._venue_rules(kwargs['orders'],kwargs['origin'])


def test_existing_oms_orders_are_used_for_rate_checks_even_if_caller_omits_history(tmp_path,monkeypatch):
    from quantos.contracts.trading import OrderSide
    _,_,kwargs=setup(tmp_path,monkeypatch);dispatch,recovery,oms=stores(tmp_path)
    kwargs['risk_inputs']['native_limits']=replace(kwargs['risk_inputs']['native_limits'],max_orders_per_window=1)
    try:
        oms.record_submission('QDprior00001','BTCUSDC',OrderSide.BUY,.001,strategy_id='prior-fixture',
                              submitted_at=NOW-1000,status='CANCELED',order_type='LIMIT',limit_price=60000)
        with pytest.raises(submit.DemoPreparationRejected,match='DEMO_BATCH_RISK_REJECTED'):
            submit.proposal(**kwargs,dispatch=dispatch,recovery=recovery,oms=oms)
    finally:
        for store in (dispatch,recovery,oms):store.close()


def test_position_control_requires_retained_exact_none_and_other_values_block(tmp_path,monkeypatch):
    _,_,values=setup(tmp_path,monkeypatch)
    origin=values['origin'];rules=next(r['data'] for r in origin['reads'] if r['kind']=='rules')
    rules['filter_types'].append('POSITION_RISK_CONTROL')
    control={'filterType':'POSITION_RISK_CONTROL','positionControlSide':'NONE'}
    rules['venue_filters'].append(control)
    submit._venue_rules(values['orders'],origin)
    for value in ('LONG','SHORT','BOTH','UNKNOWN'):
        control['positionControlSide']=value
        with pytest.raises(submit.DemoPreparationRejected):submit._venue_rules(values['orders'],origin)
    control['positionControlSide']='NONE';control['newConstraint']='1'
    with pytest.raises(submit.DemoPreparationRejected):submit._venue_rules(values['orders'],origin)


def test_extra_fields_in_known_filters_are_not_silently_discarded(tmp_path,monkeypatch):
    _,_,values=setup(tmp_path,monkeypatch)
    rules=next(r['data'] for r in values['origin']['reads'] if r['kind']=='rules')
    rules['venue_filters'][0]['futureConstraint']='1'
    with pytest.raises(submit.DemoPreparationRejected):submit._venue_rules(values['orders'],values['origin'])
