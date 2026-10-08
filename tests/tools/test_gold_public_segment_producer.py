"""Periodic public snapshots must keep shared original receipts and failures."""
import io
import json

import pytest
from tests.tools.test_registered_research_run import technical_arguments as technical_arguments

from tools import perpetual_public_capture as tool

BASE = 1790730000000


def transport(monkeypatch,*,funding_after_ms=None,funding_delay_seconds=0,hold_forecast=False):
    clock,urls = [0.0],[]
    class Response(io.BytesIO):
        def geturl(self):
            return urls[-1]
    class Opener:
        def open(self,request,timeout):
            urls.append(request.full_url)
            clock[0] += .01
            payload = {'fixture':True}
            if '/premiumIndex?' in request.full_url:
                now=BASE+int(clock[0]*1000)
                nominal=BASE+funding_after_ms if funding_after_ms is not None else BASE+1_000_000
                payload = {'time':now,'nextFundingTime':nominal if hold_forecast or now<nominal else nominal+1_000_000}
            elif '/fundingRate?' in request.full_url:
                payload = []
                if funding_after_ms is not None and '&startTime=' in request.full_url:
                    clock[0]+=funding_delay_seconds
                    payload=[dict(symbol='XAUUSDT',fundingTime=BASE+funding_after_ms,
                        fundingRate='0.001',markPrice='100')]
            return Response(json.dumps(payload).encode())
    monkeypatch.setattr(tool.urllib.request,'build_opener',lambda *args:Opener())
    monkeypatch.setattr(tool.time,'time',lambda:BASE/1000+clock[0])
    monkeypatch.setattr(tool.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(tool.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    return clock,urls


def test_original_collector_emits_fresh_snapshots_and_exact_shared_boundary(monkeypatch):
    _,urls = transport(monkeypatch)
    records,segments = [],[]
    result = tool.capture_gold_session(seconds=30,interval_seconds=1,on_record=records.append,
        segment_samples=8,on_segment=segments.append)
    assert len(segments)>=3
    assert result['segment_count']==len(segments)
    assert result['schema_version']=='GOLD_PUBLIC_SEGMENTED_QUOTE_SESSION_V1'
    assert result['replay_ready'] is result['submission_enabled'] is False
    assert len(result['samples'])==1
    for index,segment in enumerate(segments):
        assert segment['schema']=='GOLD_PUBLIC_QUOTE_SEGMENT_V1' and segment['index']==index
        assert 3<=len(segment['samples'])<=240
        assert segment['exchange_after']['received_at']>=segment['samples'][-1]['depth']['received_at']
        assert max(row['received_at'] for row in segment['bootstrap'].values())<=segment['samples'][0]['mark']['requested_at']
        assert all(row in records for row in segment['bootstrap'].values())
        if index:
            assert segments[index-1]['samples'][-1]==segment['samples'][0]
            assert segments[index-1]['bootstrap']!=segment['bootstrap']
    unique={sample['depth']['received_at'] for segment in segments for sample in segment['samples']}
    assert len(unique)==result['sample_count']
    assert sum('/exchangeInfo' in url for url in urls)>=2*len(segments)


def test_durable_segment_reader_rejects_changed_root_receipt(tmp_path,monkeypatch):
    from tools import gold_quote_session as journal
    transport(monkeypatch)
    monkeypatch.setattr(journal,'source_identity',lambda:{'fixture':True})
    journal.run(tmp_path/'session',seconds=30,interval_seconds=1,segment_samples=8)
    saved=journal.read_segment(tmp_path/'session',0)
    binding=saved['receipts'][0]
    path=tmp_path/'session'/f"receipt-{binding['index']:06d}.json"
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='ORIGINAL_CHANGED'):
        journal.read_segment(tmp_path/'session',0)


def test_segmented_root_requires_explicit_segment_in_original_prefix_reader(tmp_path,monkeypatch):
    from tests.tools.test_gold_native_prefix import frozen
    from tools.gold_session_prefix import snapshot
    from tools import gold_quote_session as journal
    from tests.execution.test_perpetual_public_replay import START
    import sys
    monkeypatch.setattr(sys.modules[__name__],'BASE',START)
    _,bars,_,source=frozen(tmp_path,monkeypatch)
    transport(monkeypatch)
    monkeypatch.setattr(journal,'source_identity',lambda:source)
    journal.run(tmp_path/'session',seconds=30,interval_seconds=1,segment_samples=8)
    with pytest.raises(ValueError,match='SEGMENT'):
        snapshot(tmp_path/'session',bars,tmp_path/'unspecified')
    report=snapshot(tmp_path/'session',bars,tmp_path/'first',quote_segment=0)
    saved=journal.read_segment(tmp_path/'session',0)
    inputs=json.loads((tmp_path/'first/inputs.json').read_bytes())
    assert report['sample_count']==len(saved['segment']['samples'])
    assert inputs['bootstrap']==saved['segment']['bootstrap']
    assert inputs['quote_declared_interval_ms']==1000


def test_segment_sink_failure_keeps_every_original_and_does_not_restart(monkeypatch):
    transport(monkeypatch)
    originals,calls = [],[]
    def fail(segment):
        calls.append(segment)
        raise ValueError('DURABLE_SEGMENT_SINK_FAILED')
    with pytest.raises(ValueError,match='DURABLE_SEGMENT_SINK_FAILED'):
        tool.capture_gold_session(seconds=30,interval_seconds=1,on_record=originals.append,
            segment_samples=8,on_segment=fail)
    assert len(calls)==1
    assert calls[0]['samples'][-1]['depth'] in originals
    assert calls[0]['exchange_after'] in originals


@pytest.mark.parametrize('size',[True,0,2,121])
def test_segment_budgets_reject_before_network(monkeypatch,size):
    _,urls = transport(monkeypatch)
    with pytest.raises(ValueError):
        tool.capture_gold_session(seconds=30,on_record=lambda row:None,
            segment_samples=size,on_segment=lambda segment:None)
    assert not urls


def test_original_session_persists_segment_receipts_and_source_bindings(tmp_path,monkeypatch):
    from tools import gold_quote_session as journal
    transport(monkeypatch)
    monkeypatch.setattr(journal,'source_identity',lambda:{'fixture':True})
    result=journal.run(tmp_path/'session',seconds=30,interval_seconds=1,segment_samples=8)
    segments=sorted((tmp_path/'session').glob('segment-*.json'))
    assert len(segments)==result['segment_count']>=3
    plan=json.loads((tmp_path/'session/plan.json').read_bytes())
    assert plan['schema']=='GOLD_PUBLIC_SEGMENTED_QUOTE_SESSION_PLAN_V1'
    for index,path in enumerate(segments):
        saved=json.loads(path.read_bytes())
        assert saved['source']=={'fixture':True} and saved['segment']['index']==index
        for binding in saved['receipts']:
            raw=(tmp_path/'session'/f"receipt-{binding['index']:06d}.json").read_bytes()
            import hashlib
            assert hashlib.sha256(raw).hexdigest()==binding['sha256']
    assert result['replay_ready'] is result['submission_enabled'] is False


def native_session(tmp_path,monkeypatch,source=None,*,funding_after_ms=None):
    from copy import deepcopy
    from tests.execution.test_perpetual_public_replay import START
    from tests.tools.test_gold_native_prefix import frozen
    from tools import gold_quote_session as journal, gold_native_prefix as bridge
    _,bars,capture,source=frozen(tmp_path,monkeypatch,source)
    clock,urls=[0.0],[]
    class Response(io.BytesIO):
        def geturl(self):
            return urls[-1]
    class Opener:
        def open(self,request,timeout):
            urls.append(request.full_url)
            clock[0]+=.01
            now=START+int(clock[0]*1000)
            if '/exchangeInfo' in request.full_url:
                payload=capture['bootstrap']['exchange']['payload']
            elif '/fundingRate?' in request.full_url:
                payload=deepcopy(capture['bootstrap']['funding']['payload'])
                if funding_after_ms is not None and now>=START+funding_after_ms:
                    if '&startTime=' in request.full_url:
                        clock[0]+=1.5
                    payload=[dict(symbol='XAUUSDT',fundingTime=START+funding_after_ms,
                        fundingRate='0.001',markPrice='100')]
            elif '/klines?' in request.full_url:
                payload=capture['bootstrap']['candles']['payload']
            elif '/premiumIndex?' in request.full_url:
                payload=deepcopy(capture['samples'][0]['mark']['payload'])
                payload['time']=now
                if funding_after_ms is not None:
                    nominal=START+funding_after_ms
                    payload['nextFundingTime']=nominal if now<nominal else nominal+1_000_000
            else:
                payload=deepcopy(capture['samples'][0]['depth']['payload'])
                payload.update(T=now,E=now,lastUpdateId=len(urls))
            return Response(json.dumps(payload).encode())
    with monkeypatch.context() as transport_patch:
        transport_patch.setattr(tool.urllib.request,'build_opener',lambda *args:Opener())
        transport_patch.setattr(tool.time,'time',lambda:START/1000+clock[0])
        transport_patch.setattr(tool.time,'monotonic',lambda:clock[0])
        transport_patch.setattr(tool.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
        transport_patch.setattr(journal,'source_identity',lambda:source)
        summary=journal.run(tmp_path/'session',seconds=1200,interval_seconds=10,segment_samples=40)
    # A synthetic original OPEN heartbeat covers the final quote watermark;
    # it adds no new closed bar or trading decision.
    from tools.gold_bar_session import _cursor
    cursor=_cursor((bars/'warmup.json').read_bytes(),json.loads((bars/'warmup-receipt.json').read_bytes()))
    rows=[json.loads(line) for line in (bars/'messages-0000.jsonl').read_text().splitlines()]
    for row in rows:
        cursor.ingest(row['raw'],received_at_ms=row['received_at_ms'])
    last=json.loads(rows[-1]['raw'])
    received=max(START+int(clock[0]*1000)+1,rows[-1]['received_at_ms']+1)
    last['data']['E']=received-1
    raw=json.dumps(last)
    row=dict(raw=raw,received_at_ms=received,decision=cursor.ingest(raw,received_at_ms=received))
    with (bars/'messages-0000.jsonl').open('a') as handle:
        handle.write(json.dumps(row)+'\n')
    monkeypatch.setattr(bridge,'source_identity',lambda:source)
    return bars,source,summary


def test_original_collected_segments_run_one_account_and_fixed_native_request(tmp_path,monkeypatch):
    from decimal import Decimal
    from tools import gold_native_prefix as bridge
    from tools.gold_bar_experiment import _execute,_encode
    from tools.gold_bar_research import read_arguments
    bars,source,summary=native_session(tmp_path,monkeypatch)
    arguments=bridge.from_session(tmp_path/'session',bars,tmp_path/'pieces',
        segment_count=summary['segment_count'],release_identity=source,quantity=Decimal('.1'),
        initial_collateral=Decimal('1000'),fee_rate=Decimal('.001'))
    result=_execute(arguments)
    channel=result['trials'][0]['simulation']
    filled=[order for order in channel['orders'] if order['status']=='FILLED']
    assert [order['filled_quantity'] for order in filled]==['0.1','-0.1']
    assert filled[0]['filled_at'] < filled[1]['created_at'] < filled[1]['filled_at']
    cancelled=[order for order in channel['orders'] if order['status']=='CANCELLED']
    assert len(cancelled)==1 and cancelled[0]['filled_quantity']=='0'
    assert cancelled[0]['reason']=='ENVIRONMENT_EPOCH_CHANGED'
    assert len(channel['environment_segments'])==summary['segment_count']>=3
    assert channel['reconciliation']['initial_equity']=='1000'
    assert channel['ending_state']['cashflow_identity']['residual']=='0'
    assert channel['ending_state']['cashflow_identity']['fee']=='0.02'
    bridge.write_request(arguments,tmp_path/'request')
    assert _encode(read_arguments(tmp_path/'request',original_source=source))==_encode(arguments)
    assert result['economic_eligible'] is result['submission_enabled'] is False


def test_original_cold_prefix_rejects_funding_receipt_replacement(tmp_path,monkeypatch):
    from decimal import Decimal
    from tools import gold_native_prefix as bridge
    from tools.gold_bar_experiment import _execute
    bars,source,summary=native_session(tmp_path,monkeypatch)
    arguments=bridge.from_session(tmp_path/'session',bars,tmp_path/'pieces',
        segment_count=summary['segment_count'],release_identity=source,quantity=Decimal('.1'),
        initial_collateral=Decimal('1000'),fee_rate=Decimal('.001'))
    arguments['observed_funding_records']=[arguments['capture']['bootstrap']['funding']]
    arguments['settlement_window_ms']=1000
    with pytest.raises(ValueError,match='BAR_FROZEN_PREFIX_FUNDING_CHANGED'):
        _execute(arguments)


def test_original_segmented_collector_reaches_queue_and_recovery_once(tmp_path,monkeypatch,technical_arguments):
    from decimal import Decimal
    import time
    from tests.research.test_registered_research_job import queued
    from services.research.project_research_evidence_reader import ProjectResearchEvidenceReader
    from tools import gold_native_prefix as bridge,gold_bar_experiment,gold_bar_research
    from tools import registered_gold_collection as collected,registered_research_job as job
    from tools import registered_research_run as native
    bars,source,summary=native_session(tmp_path,monkeypatch,technical_arguments['release_identity'])
    arguments=bridge.from_session(tmp_path/'session',bars,tmp_path/'pieces',
        segment_count=summary['segment_count'],release_identity=source,quantity=Decimal('.1'),
        initial_collateral=Decimal('1000'),fee_rate=Decimal('.001'))
    for module in (gold_bar_experiment,gold_bar_research):
        monkeypatch.setattr(module,'source_identity',lambda:source)
    last=arguments['continuation_segments'][-1]['capture']['exchange_after']['received_at']
    monkeypatch.setattr(time,'time_ns',lambda:(last+1000)*1_000_000)
    monkeypatch.setenv('QUANTOS_STATE_ROOT',str(tmp_path))
    bridge.write_request(arguments,tmp_path/'data/cache/gold_bar_research/requests/1')
    original,calls=native._native_run,[]
    def observed(**kwargs):
        producer=kwargs['execute']
        def execute():
            calls.append(1)
            return producer()
        return original(**{**kwargs,'execute':execute})
    monkeypatch.setattr(native,'_native_run',observed)
    registry,queue=queued(tmp_path)
    uid=queue.submit('gold_bar_research',{'attempt':1},1,invocation_id='collected-prefix')
    queue.join()
    assert registry.get(uid).status=='done',registry.get(uid).error
    project,database=job.workspace_paths()
    cut=ProjectResearchEvidenceReader(project,registry_database=database)()
    assert cut['attempt_count']==3 and {row['status'] for row in cut['attempts']}=={'PASSED'}
    report=project/'runs'/collected.invocation_id(1)/'archive/report.json'
    before=report.read_bytes(),database.read_bytes()
    uid=queue.submit('gold_bar_recover',{'attempt':1},2,invocation_id='collected-prefix-recover')
    queue.join()
    assert registry.get(uid).status=='done',registry.get(uid).error
    assert calls==[1] and before==(report.read_bytes(),database.read_bytes())


def test_funding_receipt_waits_for_a_later_original_quote_before_segment_cut(monkeypatch):
    transport(monkeypatch,funding_after_ms=6000,funding_delay_seconds=1.5)
    records,segments=[],[]
    tool.capture_gold_session(seconds=30,interval_seconds=1,on_record=records.append,
        segment_samples=8,on_segment=segments.append)
    funded=[segment for segment in segments if segment['observed_funding_records']]
    assert len(funded)==1
    segment=funded[0]
    receipt=segment['observed_funding_records'][0]
    assert receipt in records
    assert any(sample['depth']['received_at'] < receipt['received_at'] for sample in segment['samples'])
    assert receipt['received_at'] <= segment['samples'][-1]['depth']['received_at']
    assert segment['samples'][-1]==segments[segment['index']+1]['samples'][0]


def test_original_collected_funding_reaches_original_account_once(tmp_path,monkeypatch):
    from decimal import Decimal
    from tools import gold_native_prefix as bridge
    from tools.gold_bar_experiment import _execute
    bars,source,summary=native_session(tmp_path,monkeypatch,funding_after_ms=500_000)
    arguments=bridge.from_session(tmp_path/'session',bars,tmp_path/'pieces',
        segment_count=summary['segment_count'],release_identity=source,quantity=Decimal('.1'),
        initial_collateral=Decimal('1000'),fee_rate=Decimal('.001'),settlement_window_ms=1000)
    result=_execute(arguments)
    channel=result['trials'][0]['simulation']
    cash=channel['ending_state']['cashflow_identity']
    assert Decimal(cash['funding'])==Decimal('-.01')
    assert cash['fee']=='0.02' and cash['residual']=='0' and cash['ending_equity']=='999.95'
    assert len(channel['environment_segments'])==summary['segment_count']


def test_utc_rollover_uses_a_new_original_capacity_receipt(tmp_path,monkeypatch):
    import sys
    from tools import gold_quote_session as journal
    monkeypatch.setattr(sys.modules[__name__],'BASE',(BASE//tool.DAY_MS+1)*tool.DAY_MS-5000)
    _,urls=transport(monkeypatch)
    monkeypatch.setattr(journal,'source_identity',lambda:{'fixture':True})
    summary=journal.run(tmp_path/'session',seconds=30,interval_seconds=1,segment_samples=20)
    first=journal.read_segment(tmp_path/'session',0)['segment']
    assert len(first['capacity_observations'])==1
    receipt=first['capacity_observations'][0]
    midnight=(BASE//tool.DAY_MS+1)*tool.DAY_MS
    assert f'endTime={midnight-1}' in receipt['endpoint']
    assert receipt['received_at']<=next(sample['mark']['requested_at'] for sample in first['samples']
        if sample['depth']['received_at']>=midnight)
    assert any(f'endTime={midnight-1}' in url for url in urls)
    assert summary['replay_ready'] is False


def test_failure_after_sealed_segments_keeps_originals_and_no_completion(tmp_path,monkeypatch):
    from tools import gold_quote_session as journal
    transport(monkeypatch)
    identities=[]
    def source():
        identities.append(1)
        return {'fixture':len(identities)>2}
    monkeypatch.setattr(journal,'source_identity',source)
    with pytest.raises(ValueError,match='SOURCE_OR_SEQUENCE_CHANGED'):
        journal.run(tmp_path/'session',seconds=30,interval_seconds=1,segment_samples=8)
    assert (tmp_path/'session/segment-000000.json').exists()
    assert not (tmp_path/'session/segment-000001.json').exists()
    failure=json.loads((tmp_path/'session/failure.json').read_bytes())
    assert failure['status']=='INCOMPLETE' and failure['receipts']>20
    assert not (tmp_path/'session/summary.json').exists()


@pytest.mark.parametrize('damage',['interval','exchange'])
def test_original_cold_prefix_rejects_bound_quote_metadata_changes(tmp_path,monkeypatch,damage):
    from decimal import Decimal
    from tests.execution.test_perpetual_public_replay import sealed
    from tools import gold_native_prefix as bridge
    from tools.gold_bar_experiment import _execute
    bars,source,summary=native_session(tmp_path,monkeypatch)
    arguments=bridge.from_session(tmp_path/'session',bars,tmp_path/'pieces',
        segment_count=summary['segment_count'],release_identity=source,quantity=Decimal('.1'),
        initial_collateral=Decimal('1000'),fee_rate=Decimal('.001'))
    capture=arguments['capture']
    if damage=='interval':
        capture['declared_interval_ms']=1000
    else:
        capture['exchange_after']['received_at']+=1
    arguments['capture']=sealed(capture)
    with pytest.raises(ValueError,match='BAR_FROZEN_PREFIX_CAPTURE_CHANGED'):
        _execute(arguments)


def test_segment_tail_is_not_dropped_when_bar_watermark_is_behind(tmp_path,monkeypatch):
    from decimal import Decimal
    from tools import gold_native_prefix as bridge
    bars,source,summary=native_session(tmp_path,monkeypatch)
    path=bars/'messages-0000.jsonl'
    path.write_bytes(b''.join(path.read_bytes().splitlines(keepends=True)[:2]))
    with pytest.raises(ValueError,match='BAR_WATERMARK_INCOMPLETE'):
        bridge.from_session(tmp_path/'session',bars,tmp_path/'pieces',
            segment_count=summary['segment_count'],release_identity=source,quantity=Decimal('.1'),
            initial_collateral=Decimal('1000'),fee_rate=Decimal('.001'))


def test_terminal_tail_budget_preserves_failure_without_inventing_future_quotes(tmp_path,monkeypatch):
    from tools import gold_quote_session as journal
    transport(monkeypatch,funding_after_ms=6000,hold_forecast=True)
    monkeypatch.setattr(journal,'source_identity',lambda:{'fixture':True})
    with pytest.raises(ValueError,match='SEGMENT_TERMINAL_TAIL_INCOMPLETE'):
        journal.run(tmp_path/'session',seconds=30,interval_seconds=1,segment_samples=8)
    failure=json.loads((tmp_path/'session/failure.json').read_bytes())
    assert failure['segment_count']==0 and failure['segments_replay_validated'] is False
    assert not (tmp_path/'session/summary.json').exists()
    assert len(list((tmp_path/'session').glob('receipt-*.json')))==failure['receipts']
