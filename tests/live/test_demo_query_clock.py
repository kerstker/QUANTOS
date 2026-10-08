"""The protected original-ID reader keeps the actual clock bytes in its journal."""
from copy import deepcopy
import hashlib
import json

import pytest

from live.execution import demo_prepared_execution as executor
from live.exchange import binance_demo_exact as transport
from live.execution.demo_dispatch_store import DemoDispatchStore
from tests.live.test_demo_prepared_execution import ready,NOW
from tests.live.test_demo_reserved_queries import snapshot


def read_with_clock(tmp_path,monkeypatch,*,raw_clock=None):
    batch,args=ready(tmp_path,monkeypatch)
    now=NOW+60000
    monkeypatch.setattr(executor,'now_ms',lambda:now)
    raw_clock=raw_clock if raw_clock is not None else ('{ "serverTime" : '+str(now+1000)+' }').encode()
    calls=[]
    def read(path,query,headers):
        calls.append(path)
        if path=='/fapi/v1/time':
            assert not query and not headers
            return 200,raw_clock
        assert path=='/fapi/v1/order'
        return 200,json.dumps(snapshot(batch['orders'][0])).encode()
    monkeypatch.setattr(transport,'_http_get_once',read)
    monkeypatch.setattr(transport,'_http_once',lambda *a:pytest.fail('clock recovery never POSTs'))
    return batch,args,raw_clock,calls


def close(args):
    for name in ('dispatch','recovery','oms'):args[name].close()


def test_query_clock_raw_bytes_survive_dispatch_restart_without_order_side_effects(tmp_path,monkeypatch):
    batch,args,raw,calls=read_with_clock(tmp_path,monkeypatch)
    try:
        result=executor.query_reserved(batch,dispatch=args['dispatch'],recovery=args['recovery'],oms=args['oms'])
        assert result['status']=='ORDER_SNAPSHOTS_OBSERVED'
        row=args['dispatch'].wire_snapshot()[-1]; clock=row['payload']['clock']
        assert clock['schema']=='DEMO_ORIGINAL_QUERY_CLOCK_V1'
        assert clock['raw_utf8'].encode()==raw
        assert clock['response_sha256']==hashlib.sha256(raw).hexdigest()
        assert len(clock['source_sha256'])==64
        assert clock['server_time_ms']==NOW+61000
        with DemoDispatchStore(args['dispatch'].path,read_only=True) as restored:
            assert restored.wire_snapshot()[-1]==row
        assert calls==['/fapi/v1/time','/fapi/v1/order']
        assert args['oms'].fills()==[] and len(args['recovery'].pending())==1
    finally:close(args)


@pytest.mark.parametrize('raw',[b'{"serverTime":1,"serverTime":2}',b'{"serverTime":true}',
    b'{"serverTime":1789180061000,"unexpected":"untrusted"}',b'{"serverTime":1e12}'])
def test_nonexact_public_clock_is_retained_as_failure_before_private_order_query(tmp_path,monkeypatch,raw):
    if b'unexpected' in raw:
        raw=json.dumps({'serverTime':NOW+61000,'unexpected':'untrusted'}).encode()
    batch,args,_,calls=read_with_clock(tmp_path,monkeypatch,raw_clock=raw)
    try:
        r=executor.query_reserved(batch,dispatch=args['dispatch'],recovery=args['recovery'],oms=args['oms'])
        assert r['reason']=='QUERY_CLOCK_UNRELIABLE' and calls==['/fapi/v1/time']
        assert args['dispatch'].wire_snapshot()==[] and args['oms'].fills()==[]
    finally:close(args)


@pytest.mark.parametrize('change',['hash','raw','server','future_receive','source','extra','stale'])
def test_clock_provenance_cannot_be_detached_from_retained_query(tmp_path,monkeypatch,change):
    batch,args,_,_=read_with_clock(tmp_path,monkeypatch)
    try:
        executor.query_reserved(batch,dispatch=args['dispatch'],recovery=args['recovery'],oms=args['oms'])
        row=args['dispatch'].wire_snapshot()[-1]; before=deepcopy(row); clock=deepcopy(row['payload']['clock'])
        if change=='hash':clock['response_sha256']='a'*64
        elif change=='raw':clock['raw_utf8']='{}'
        elif change=='server':clock['server_time_ms']+=1
        elif change=='future_receive':clock['received_at_ms']=row['at_ms']+1
        elif change=='source':clock['source_sha256']=''
        elif change=='extra':clock['accepted']=True
        else:
            for key in ('requested_at_ms','received_at_ms','server_time_ms'):clock[key]-=60001
            clock['raw_utf8']=json.dumps({'serverTime':clock['server_time_ms']})
            clock['response_sha256']=hashlib.sha256(clock['raw_utf8'].encode()).hexdigest()
        with pytest.raises((ValueError,RuntimeError)):
            args['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
                client_order_id=batch['orders'][0]['newClientOrderId'],requested_at_ms=row['at_ms'],
                outcome=row['payload']['outcome'],clock=clock,now_ms=row['at_ms'])
        assert args['dispatch'].wire_snapshot()[-1]==before
    finally:close(args)


@pytest.mark.parametrize('offset',[-400,400])
def test_original_query_clock_automatically_binds_receipts_and_terminal_oms(tmp_path,monkeypatch,offset):
    from live.execution.demo_oms_reconciliation import prepare_resolution,_project_resolution
    from live.execution.order_event_store import SqliteOrderEventStore
    from services.research.demo_user_stream import normalize_message
    from tests.live.test_demo_receipt_binding import fixture,close as close_stream
    batch,args,stream,p=fixture(tmp_path,monkeypatch,ack=False)
    events=SqliteOrderEventStore(str(tmp_path/'events.sqlite3'),create=True)
    try:
        p['E']=p['T']=p['o']['T']=NOW+1000+offset
        stream.record('MESSAGE',at_ms=NOW+1012,message=normalize_message(json.dumps(p)))
        monkeypatch.setattr(executor,'now_ms',lambda:NOW+1020)
        def get(path,query,headers):
            if path=='/fapi/v1/time':return 200,json.dumps({'serverTime':NOW+1020+offset}).encode()
            return 200,json.dumps(snapshot(batch['orders'][0],status='FILLED',executedQty='0.002',
                time=NOW+600+offset,updateTime=NOW+1000+offset)).encode()
        monkeypatch.setattr(transport,'_http_get_once',get)
        q=executor.query_reserved(batch,dispatch=args['dispatch'],recovery=args['recovery'],oms=args['oms'])
        assert q['status']=='ORDER_SNAPSHOTS_OBSERVED'
        before=args['dispatch'].evidence_snapshot()
        plan=prepare_resolution(batch,dispatch=args['dispatch'],stream=stream,start_ms=NOW+1,end_ms=NOW+1013,
            source_commit='a'*40,output_directory=tmp_path/'result',as_of_ms=NOW+1030)
        assert plan['status']=='READY_FOR_OMS_PROJECTION' and plan['issues']==[]
        from services.research.demo_execution_journal import DemoExecutionJournal,replay_report
        with DemoExecutionJournal(plan['journal_path'],read_only=True) as journal:
            assert journal.manifest['native']['schema']=='BINANCE_USDM_ORDER_RECEIPTS_V3'
            r=replay_report(journal)
            assert r['reconciliation']['fee']=='0.054' and r['clock']['exact_latency_ms'] is None
        assert args['dispatch'].evidence_snapshot()==before and args['oms'].fills()==[]
        result=_project_resolution(plan,prepared=batch,dispatch=args['dispatch'],recovery=args['recovery'],
            oms=args['oms'],event_store=events,now_ms=NOW+1030)
        assert result['status']=='OMS_TERMINAL_PROJECTED' and len(args['oms'].fills())==1
        assert args['oms'].position('BTCUSDC')==.002 and args['recovery'].pending()==()
    finally:events.close();close_stream(args,stream)


@pytest.mark.parametrize('change',['before_receipt_end','offset_drift','missing_peer','mixed_peer'])
def test_new_clock_records_never_fall_back_to_common_epoch_on_incomplete_bracket(tmp_path,monkeypatch,change):
    from live.execution.demo_receipt_binding import _retained_clock_bracket
    from live.execution.demo_prepared_execution import _load_proof
    batch,args,_,_=read_with_clock(tmp_path,monkeypatch)
    try:
        executor.query_reserved(batch,dispatch=args['dispatch'],recovery=args['recovery'],oms=args['oms'])
        wire=args['dispatch'].wire_snapshot(); clients=[batch['orders'][0]['newClientOrderId']]
        # The actual fixture's 1000 ms endpoint change is already outside the
        # fixed 500 ms bracket assumption. Other cases use a coherent 0 offset.
        if change!='offset_drift':
            c=wire[-1]['payload']['clock'];c['server_time_ms']=NOW+60000
            c['raw_utf8']=json.dumps({'serverTime':c['server_time_ms']});c['response_sha256']=hashlib.sha256(c['raw_utf8'].encode()).hexdigest()
        end=NOW+60001 if change=='before_receipt_end' else NOW+1000
        if change in ('missing_peer','mixed_peer'):
            clients.append('QDsecond0002');peer=deepcopy(wire[-1]);peer['client_order_id']=clients[-1]
            if change=='missing_peer':del peer['payload']['clock']
            else:
                c=peer['payload']['clock'];c['source_sha256']='e'*64
            wire.append(peer)
        with pytest.raises(ValueError):_retained_clock_bracket(_load_proof(batch),wire,clients,start_ms=NOW+1,end_ms=end)
        assert args['oms'].fills()==[] and len(args['recovery'].pending())==1
    finally:close(args)
