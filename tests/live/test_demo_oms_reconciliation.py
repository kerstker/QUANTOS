"""Only agreeing private receipts and original-ID queries can close recovery."""
import json
from pathlib import Path

import pytest

from live.execution import demo_oms_reconciliation as reconciliation
from live.execution.order_event_store import SqliteOrderEventStore
from live.execution.order_recovery import RecoveryState
from live.execution.demo_wire_protocol import outcome_for_query
from services.research.demo_user_stream import normalize_message
from tests.live.test_demo_receipt_binding import fixture,close,NOW


def add_query(batch,args,*,quantity='0.002',status='FILLED',at=NOW+30):
    o=batch['orders'][0]
    raw={'symbol':o['symbol'],'clientOrderId':o['newClientOrderId'],'orderId':9007199254740993,
        'side':o['side'],'origQty':o['quantity'],'price':o['price'],'positionSide':o['positionSide'],
        'reduceOnly':o['reduceOnly'],'timeInForce':o['timeInForce'],'type':o['type'],
        'status':status,'executedQty':quantity,'time':NOW+1,'updateTime':NOW+10}
    return args['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
        client_order_id=o['newClientOrderId'],requested_at_ms=at,
        outcome=outcome_for_query(o,200,json.dumps(raw).encode()),now_ms=at+1)


def plan(batch,args,stream,tmp_path):
    return reconciliation.prepare_resolution(batch,dispatch=args['dispatch'],stream=stream,
        start_ms=NOW+1,end_ms=NOW+1000,source_commit='a'*40,output_directory=tmp_path/'output',as_of_ms=NOW+1001)


def test_receipt_and_original_query_prepare_exact_resolution_without_mutating_oms(tmp_path,monkeypatch):
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    try:
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)))
        q=add_query(batch,args)
        result=plan(batch,args,stream,tmp_path)
        assert result['status']=='READY_FOR_OMS_PROJECTION' and result['issues']==[]
        row=result['orders'][0]
        assert row['query_record_sha256']==q['record_hash']
        assert row['filled_quantity']=='0.002' and row['venue_order_id']=='9007199254740993'
        assert row['events'][0]['average_price']=='60000' and row['terminal_status']=='FILLED'
        assert result['cash_reconciled'] is False and result['source_verified'] is False
        assert args['oms'].fills()==[] and len(args['recovery'].pending())==1
    finally:close(args,stream)


@pytest.mark.parametrize('failure',['query_missing','query_quantity','query_before_receipt','query_unknown','private_missing'])
def test_incomplete_or_conflicting_query_cannot_clear_recovery(tmp_path,monkeypatch,failure):
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    try:
        if failure!='private_missing':stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)))
        if failure!='query_missing':
            add_query(batch,args,quantity='0.001' if failure=='query_quantity' else '0.002',
                status='EXPIRED' if failure=='query_quantity' else 'FILLED',at=NOW+5 if failure=='query_before_receipt' else NOW+30)
        if failure=='query_unknown':
            args['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
                client_order_id=batch['orders'][0]['newClientOrderId'],requested_at_ms=NOW+40,
                outcome=outcome_for_query(batch['orders'][0],None,None),now_ms=NOW+41)
        r=plan(batch,args,stream,tmp_path)
        assert r['status']=='UNRESOLVED' and r['issues']
        assert args['oms'].fills()==[] and len(args['recovery'].pending())==1
    finally:close(args,stream)


def test_projected_order_uses_existing_oms_events_and_repeated_replay_has_one_fill(tmp_path,monkeypatch):
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    events=SqliteOrderEventStore(str(tmp_path/'events.sqlite3'),create=True)
    try:
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)));add_query(batch,args)
        r=plan(batch,args,stream,tmp_path)
        original=Path(batch['proposal_path']).read_bytes()
        result=reconciliation._project_resolution(r,prepared=batch,dispatch=args['dispatch'],recovery=args['recovery'],
            oms=args['oms'],event_store=events,now_ms=NOW+1001)
        assert result['status']=='OMS_TERMINAL_PROJECTED' and result['cash_reconciled'] is False
        assert args['oms'].orders()[0]['status']=='FILLED' and len(args['oms'].fills())==1
        assert args['oms'].position('BTCUSDC')==.002
        assert args['recovery'].latest(batch['orders'][0]['newClientOrderId']).state is RecoveryState.RESOLVED
        count=events.row_count();chain=args['recovery'].latest_records()
        second=reconciliation._project_resolution(r,prepared=batch,dispatch=args['dispatch'],recovery=args['recovery'],
            oms=args['oms'],event_store=events,now_ms=NOW+2000)
        assert second['status']=='OMS_TERMINAL_PROJECTED' and events.row_count()==count
        assert args['recovery'].latest_records()==chain and len(args['oms'].fills())==1
        assert Path(batch['proposal_path']).read_bytes()==original
    finally:events.close();close(args,stream)


def test_crash_after_event_commit_replays_projection_before_resolving(tmp_path,monkeypatch):
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    events=SqliteOrderEventStore(str(tmp_path/'events.sqlite3'),create=True)
    try:
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)));add_query(batch,args)
        r=plan(batch,args,stream,tmp_path);original=args['oms'].apply_oms_projection
        def crash(record,**kw):
            if record.event.venue_status=='FILLED':raise OSError('simulated crash after event append')
            return original(record,**kw)
        monkeypatch.setattr(args['oms'],'apply_oms_projection',crash)
        with pytest.raises(RuntimeError):
            reconciliation._project_resolution(r,prepared=batch,dispatch=args['dispatch'],recovery=args['recovery'],
                oms=args['oms'],event_store=events,now_ms=NOW+1001)
        assert len(args['recovery'].pending())==1 and args['oms'].fills()==[]
        monkeypatch.setattr(args['oms'],'apply_oms_projection',original)
        reconciliation._project_resolution(r,prepared=batch,dispatch=args['dispatch'],recovery=args['recovery'],
            oms=args['oms'],event_store=events,now_ms=NOW+2000)
        assert len(args['oms'].fills())==1 and not args['recovery'].pending()
    finally:events.close();close(args,stream)


def test_partial_fill_then_expiry_keeps_one_partial_fill_and_exact_cash_inputs(tmp_path,monkeypatch):
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    events=SqliteOrderEventStore(str(tmp_path/'events.sqlite3'),create=True)
    try:
        p['o'].update(X='PARTIALLY_FILLED',l='0.001',z='0.001',n='0.027')
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)))
        p['E']=p['T']=p['o']['T']=NOW+13
        p['o'].update(X='EXPIRED_IN_MATCH',x='EXPIRED',l='0',L='0',n='0',t=0)
        stream.record('MESSAGE',at_ms=NOW+14,message=normalize_message(json.dumps(p)))
        # Native query records retain the venue's precise expiry subtype.
        q=add_query(batch,args,quantity='0.001',status='EXPIRED_IN_MATCH')
        # Correct the fixture query's last-update instant by appending a new
        # query observation; existing records are not edited in place.
        value=q['payload']['outcome'];value['order']['updateTime']=NOW+13
        args['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
            client_order_id=batch['orders'][0]['newClientOrderId'],requested_at_ms=NOW+40,outcome=value,now_ms=NOW+41)
        r=plan(batch,args,stream,tmp_path)
        assert r['status']=='READY_FOR_OMS_PROJECTION'
        journal=Path(r['journal_path']);before=journal.read_bytes()
        reconciliation._project_resolution(r,prepared=batch,dispatch=args['dispatch'],recovery=args['recovery'],
            oms=args['oms'],event_store=events,now_ms=NOW+1001)
        assert args['oms'].orders()[0]['status']=='EXPIRED' and args['oms'].position('BTCUSDC')==.001
        assert len(args['oms'].fills())==1 and journal.read_bytes()==before
        assert r['orders'][0]['query_native_status']=='EXPIRED_IN_MATCH'
    finally:events.close();close(args,stream)


def test_protected_internal_entry_rejects_unowned_stream_before_any_projection(tmp_path,monkeypatch):
    from live.execution import demo_prepared_execution as executor
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    events=SqliteOrderEventStore(str(tmp_path/'events.sqlite3'),create=True)
    try:
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)));add_query(batch,args)
        monkeypatch.setattr(executor,'now_ms',lambda:NOW+1001)
        with pytest.raises(executor.DemoExecutionStopped,match='DEMO_RECEIPT_STATE_PATH'):
            executor.reconcile_reserved(batch,dispatch=args['dispatch'],recovery=args['recovery'],oms=args['oms'],
                event_store=events,stream=stream,start_ms=NOW+1,end_ms=NOW+1000,source_commit='a'*40)
        assert events.row_count()==0 and args['oms'].fills()==[] and len(args['recovery'].pending())==1
        monkeypatch.setattr(executor,'_STATE_ROOT',tmp_path)
        # Custody is intentionally simulated only in this offline fixture.
        monkeypatch.setattr(executor,'_require_receipt_store_custody',lambda *a:None)
        result=executor.reconcile_reserved(batch,dispatch=args['dispatch'],recovery=args['recovery'],oms=args['oms'],
            event_store=events,stream=stream,start_ms=NOW+1,end_ms=NOW+1000,source_commit='a'*40)
        assert result['status']=='OMS_TERMINAL_PROJECTED' and result['cash_reconciled'] is False
    finally:events.close();close(args,stream)


@pytest.mark.parametrize('failure',['kill_switched','wire_changed','missing_fee','off_grid_fee'])
def test_recovery_controls_and_missing_economics_cannot_be_cleared_by_projection(tmp_path,monkeypatch,failure):
    batch,args,stream,p=fixture(tmp_path,monkeypatch);events=SqliteOrderEventStore(str(tmp_path/'events.sqlite3'),create=True)
    try:
        if failure=='missing_fee':p['o'].pop('n');p['o'].pop('N')
        elif failure=='off_grid_fee':p['o']['n']='0.026999955'
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)));add_query(batch,args)
        r=plan(batch,args,stream,tmp_path)
        if failure=='kill_switched':
            args['recovery'].mark_kill_switched(batch['orders'][0]['newClientOrderId'],now_ms=NOW+50)
        elif failure=='wire_changed':add_query(batch,args,at=NOW+50)
        else:assert r['status']=='UNRESOLVED'
        with pytest.raises(ValueError):reconciliation._project_resolution(r,prepared=batch,dispatch=args['dispatch'],recovery=args['recovery'],
            oms=args['oms'],event_store=events,now_ms=NOW+1001)
        assert events.row_count()==0 and args['oms'].fills()==[] and len(args['recovery'].pending())==1
    finally:events.close();close(args,stream)


def test_two_distinct_fills_retain_both_receipts_while_oms_replay_stays_idempotent(tmp_path,monkeypatch):
    batch,args,stream,p=fixture(tmp_path,monkeypatch);events=SqliteOrderEventStore(str(tmp_path/'events.sqlite3'),create=True)
    try:
        # This fixture declares 1e-8 quote settlement; retain a reported fee
        # on that grid rather than an unrounded rate multiplication.
        p['o'].update(X='PARTIALLY_FILLED',l='0.001',z='0.001',L='59999.9',n='0.02699995')
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)))
        p['E']=p['T']=p['o']['T']=NOW+13
        p['o'].update(X='FILLED',l='0.001',z='0.002',L='60000',n='0.027',t=8)
        stream.record('MESSAGE',at_ms=NOW+14,message=normalize_message(json.dumps(p)))
        q=add_query(batch,args);value=q['payload']['outcome'];value['order']['updateTime']=NOW+13
        args['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
            client_order_id=batch['orders'][0]['newClientOrderId'],requested_at_ms=NOW+40,outcome=value,now_ms=NOW+41)
        r=plan(batch,args,stream,tmp_path);assert r['status']=='READY_FOR_OMS_PROJECTION'
        assert [e['average_price'] for e in r['orders'][0]['events']]==['59999.9','59999.95']
        before=Path(r['journal_path']).read_bytes()
        for at in (NOW+1001,NOW+2000):
            reconciliation._project_resolution(r,prepared=batch,dispatch=args['dispatch'],recovery=args['recovery'],
                oms=args['oms'],event_store=events,now_ms=at)
        assert len(args['oms'].fills())==2 and args['oms'].position('BTCUSDC')==.002
        assert Path(r['journal_path']).read_bytes()==before
    finally:events.close();close(args,stream)
