"""Cancellation never mutates OMS completion or resends an uncertain request."""
import json
from urllib.parse import parse_qs
import pytest
from live.execution import demo_cancellation as executor
from live.execution.demo_cancel_store import DemoCancelStore
from live.execution import demo_authority
from live.exchange import binance_demo_exact as transport
from services.validation import demo_cancellation as auth
from tests.live.test_demo_receipt_binding import fixture,close,NOW
from tests.live.test_demo_oms_reconciliation import add_query
from tests.live.test_demo_cancel_verification import cancellation_material
from tests.live.test_demo_submission_verification import encoded


def ready(tmp_path,monkeypatch):
    batch,args,stream,_=fixture(tmp_path,monkeypatch)
    add_query(batch,args,quantity='0',status='NEW')
    private,registry,_,_,envelope,_=cancellation_material()
    # These receipt fixtures use a different historical instant from the
    # standalone signing tests. Bind test key validity to this same window.
    registry['keys'][0].update(valid_from_ms=NOW-10000,valid_until_ms=NOW+100000)
    envelope['payload'].update(issued_at_ms=NOW-1,expires_at_ms=NOW+30000)
    monkeypatch.setattr(demo_authority,'load_registry_for_executor',lambda:registry)
    monkeypatch.setattr(executor,'now_ms',lambda:NOW+50)
    monkeypatch.setattr(executor,'_require_cancel_custody',lambda *a:None)
    store=DemoCancelStore(tmp_path/'cancel.db',create=True)
    kwargs={k:args[k] for k in ('dispatch','recovery','oms')};kwargs['cancel_store']=store
    plan=executor.proposal(batch,**kwargs)
    envelope['payload']['context']=plan['context']
    envelope['signature_base64url']=encoded(private.sign(auth.signing_payload(envelope)))
    return batch,args,stream,store,kwargs,json.dumps(envelope).encode(),registry


@pytest.mark.parametrize('mode',['canceled','fill_race','timeout','503','mismatched','lost_result'])
def test_cancel_once_keeps_private_query_recovery_separate_from_response(tmp_path,monkeypatch,mode):
    batch,args,stream,store,kwargs,envelope,_=ready(tmp_path,monkeypatch);calls=[]
    order=batch['orders'][0]
    def http(query,headers):
        assert [r['kind'] for r in store.snapshot()]==['GRANT','CLAIM']
        params=parse_qs(query.decode());assert params['origClientOrderId']==[order['newClientOrderId']]
        assert params['orderId']==['9007199254740993'] and params['recvWindow']==['5000']
        assert 'quantity' not in params and 'side' not in params and 'signature' in params
        calls.append(params)
        if mode=='timeout':raise TimeoutError('fixture')
        if mode=='503':return 503,b'{}'
        return 200,json.dumps({'symbol':'BTCUSDC','clientOrderId':order['newClientOrderId'],
            'orderId':9007199254740994 if mode=='mismatched' else 9007199254740993,
            'status':'FILLED' if mode=='fill_race' else 'CANCELED','executedQty':'0.002' if mode=='fill_race' else '0'}).encode()
    monkeypatch.setattr(transport,'_http_delete_once',http)
    monkeypatch.setattr(transport,'_http_once',lambda *a:pytest.fail('new order forbidden'))
    if mode=='lost_result':monkeypatch.setattr(store,'result',lambda *a,**k:(_ for _ in ()).throw(OSError('disk fixture')))
    try:
        before=args['dispatch'].evidence_snapshot();recovery=args['recovery'].latest_records();oms=args['oms'].orders()
        result=executor.execute(batch,envelope,**kwargs)
        assert result['status']=='ORIGINAL_ID_QUERY_RECOVERY_REQUIRED' and len(calls)==1
        assert args['dispatch'].evidence_snapshot()==before and args['recovery'].latest_records()==recovery
        assert args['oms'].orders()==oms and args['oms'].fills()==[]
        from services.research.demo_cancel_status import load_latest
        from pathlib import Path
        view=load_latest(Path(batch['proposal_path']).parent/'cancellations/views')
        assert view['phase']=='ORIGINAL_ID_QUERY_RECOVERY_REQUIRED' and view['claimed_orders']==1
        assert view['unresolved_responses']==(1 if mode in ('timeout','503','mismatched','lost_result') else 0)
        if mode not in ('timeout','503','mismatched','lost_result'):
            assert result['orders'][0]['outcome']['venue_status']==('FILLED' if mode=='fill_race' else 'CANCELED')
        with pytest.raises(executor.DemoCancellationStopped):executor.execute(batch,envelope,**kwargs)
        assert len(calls)==1
    finally:store.close();close(args,stream)


@pytest.mark.parametrize('failure',['query_stale','query_unknown','terminal','no_claim','venue_conflict','registry_revoked','expired','custody'])
def test_cancel_preconditions_fail_before_credentials_or_request(tmp_path,monkeypatch,failure):
    batch,args,stream,store,kwargs,envelope,registry=ready(tmp_path,monkeypatch);calls=[]
    monkeypatch.setattr(transport,'_credentials',lambda *a:calls.append('key'))
    monkeypatch.setattr(transport,'_http_delete_once',lambda *a:calls.append('http'))
    if failure=='query_stale':monkeypatch.setattr(executor,'now_ms',lambda:NOW+6000)
    elif failure=='query_unknown':
        from live.execution.demo_wire_protocol import outcome_for_query
        args['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],client_order_id=batch['orders'][0]['newClientOrderId'],
            requested_at_ms=NOW+40,outcome=outcome_for_query(batch['orders'][0],None,None),now_ms=NOW+41)
    elif failure=='terminal':add_query(batch,args,at=NOW+40)
    elif failure in ('no_claim','venue_conflict'):
        original=args['dispatch'].evidence_snapshot
        def altered():
            view=original()
            if failure=='no_claim':view['wire']=[r for r in view['wire'] if r['kind']!='SEND_CLAIM']
            else:next(r for r in view['wire'] if r['kind']=='QUERY_RESULT')['payload']['outcome']['order']['orderId']='5'
            return view
        monkeypatch.setattr(args['dispatch'],'evidence_snapshot',altered)
    elif failure=='registry_revoked':registry['keys'][0]['status']='REVOKED'
    elif failure=='expired':monkeypatch.setattr(executor,'now_ms',lambda:NOW+30000)
    else:monkeypatch.setattr(executor,'_require_cancel_custody',lambda *a:(_ for _ in ()).throw(ValueError('fixture custody')))
    try:
        with pytest.raises(executor.DemoCancellationStopped):executor.execute(batch,envelope,**kwargs)
        assert calls==[] and store.snapshot()==[]
    finally:store.close();close(args,stream)


def test_revocation_after_key_read_still_stops_cancel_at_final_network_check(tmp_path,monkeypatch):
    batch,args,stream,store,kwargs,envelope,registry=ready(tmp_path,monkeypatch);calls=[]
    def credentials(*a):
        registry['keys'][0]['status']='REVOKED';return 'isolated-fixture-key','isolated-fixture-secret'
    monkeypatch.setattr(transport,'_credentials',credentials)
    monkeypatch.setattr(transport,'_http_delete_once',lambda *a:calls.append('http'))
    try:
        assert executor.execute(batch,envelope,**kwargs)['status']=='ORIGINAL_ID_QUERY_RECOVERY_REQUIRED'
        assert calls==[] and [r['kind'] for r in store.snapshot()]==['GRANT','CLAIM','RESULT']
        assert store.snapshot()[-1]['payload']['outcome']['status']=='UNKNOWN'
    finally:store.close();close(args,stream)


def test_abrupt_cancel_interruption_keeps_claim_and_cannot_reissue_after_reopen(tmp_path,monkeypatch):
    batch,args,stream,store,kwargs,envelope,_=ready(tmp_path,monkeypatch);calls=[]
    def crash(*a):calls.append('attempt');raise KeyboardInterrupt('isolated process interruption')
    monkeypatch.setattr(transport,'_http_delete_once',crash)
    try:
        with pytest.raises(KeyboardInterrupt):executor.execute(batch,envelope,**kwargs)
        assert [r['kind'] for r in store.snapshot()]==['GRANT','CLAIM']
        path=store.path;store.close();store=DemoCancelStore(path);kwargs['cancel_store']=store
        with pytest.raises(executor.DemoCancellationStopped):executor.execute(batch,envelope,**kwargs)
        assert calls==['attempt']
    finally:store.close();close(args,stream)


def test_real_cancel_custody_check_rejects_research_store_path_before_credentials(tmp_path,monkeypatch):
    custody=executor._require_cancel_custody
    batch,args,stream,store,kwargs,envelope,_=ready(tmp_path,monkeypatch)
    monkeypatch.setattr(executor,'_require_cancel_custody',custody)
    monkeypatch.setattr(transport,'_credentials',lambda *a:pytest.fail('credentials unavailable to this path'))
    try:
        with pytest.raises(executor.DemoCancellationStopped,match='DEMO_CANCEL_STATE_PATH'):
            executor.execute(batch,envelope,**kwargs)
        assert store.snapshot()==[]
    finally:store.close();close(args,stream)


def test_partial_fill_cancel_then_private_and_query_recovery_posts_fee_once(tmp_path,monkeypatch):
    from live.execution import demo_oms_reconciliation as reconciliation
    from live.execution.order_event_store import SqliteOrderEventStore
    from services.research.demo_user_stream import normalize_message
    from services.research.demo_execution_journal import DemoExecutionJournal,replay_report
    from tests.research.test_binance_order_receipts import message
    batch,args,stream,store,kwargs,envelope,_=ready(tmp_path,monkeypatch);order=batch['orders'][0]
    events=SqliteOrderEventStore(str(tmp_path/'order-events.db'),create=True)
    monkeypatch.setattr(transport,'_http_delete_once',lambda *a:(200,json.dumps({'symbol':'BTCUSDC',
        'clientOrderId':order['newClientOrderId'],'orderId':9007199254740993,'status':'CANCELED','executedQty':'0.001'}).encode()))
    try:
        executor.execute(batch,envelope,**kwargs)
        assert args['oms'].fills()==[] and args['recovery'].pending()
        p=message(NOW+49,'PARTIALLY_FILLED','0.001','0.001',trade_id=7,fee='0.027')
        p['o'].update(c=order['newClientOrderId'],i=9007199254740993,q=order['quantity'],p=order['price'],
            L=order['price'],f=order['timeInForce'],m=order['timeInForce']=='GTX',R=False,er='0')
        stream.record('MESSAGE',at_ms=NOW+53,message=normalize_message(json.dumps(p)))
        p['E']=p['T']=p['o']['T']=NOW+54;p['o'].update(X='CANCELED',x='CANCELED',l='0',L='0',n='0',t=0)
        stream.record('MESSAGE',at_ms=NOW+55,message=normalize_message(json.dumps(p)))
        query=add_query(batch,args,quantity='0.001',status='CANCELED',at=NOW+60)
        outcome=query['payload']['outcome'];outcome['order']['updateTime']=NOW+54
        args['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],client_order_id=order['newClientOrderId'],
            requested_at_ms=NOW+70,outcome=outcome,now_ms=NOW+71)
        plan=reconciliation.prepare_resolution(batch,dispatch=args['dispatch'],stream=stream,start_ms=NOW+1,end_ms=NOW+1000,
            source_commit='a'*40,output_directory=tmp_path/'reconciled',as_of_ms=NOW+1001)
        assert plan['status']=='READY_FOR_OMS_PROJECTION'
        for at in (NOW+1001,NOW+1002):
            reconciliation._project_resolution(plan,prepared=batch,dispatch=args['dispatch'],recovery=args['recovery'],oms=args['oms'],event_store=events,now_ms=at)
        assert len(args['oms'].fills())==1 and args['oms'].position('BTCUSDC')==.001 and not args['recovery'].pending()
        with DemoExecutionJournal(plan['journal_path'],read_only=True) as journal:
            report=replay_report(journal)
            assert report['reconciliation']['fee']=='0.027'
        assert len([r for r in store.snapshot() if r['kind']=='CLAIM'])==1
    finally:events.close();store.close();close(args,stream)
