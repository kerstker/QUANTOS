"""Functional work retains real original journals in isolated fixtures only."""
import json
from pathlib import Path

import pytest

from tools import demo_functional_worker as tool
from tools import demo_operator_setup as setup


def test_actual_historical_gap_exits_worker_without_retries_or_oms_projection(tmp_path,monkeypatch):
    from live.execution import demo_resident_recovery as resident
    from live.execution.order_event_store import SqliteOrderEventStore
    from live.exchange import binance_demo_exact as transport
    from services.research.demo_user_stream import normalize_message
    from tests.live.test_demo_receipt_binding import fixture,close,NOW
    from tests.live.test_demo_reserved_queries import snapshot
    batch,args,stream,message=fixture(tmp_path,monkeypatch,ack=False)
    events=SqliteOrderEventStore(str(tmp_path/'events.sqlite3'),create=True)
    now=[NOW+1020];calls=[]
    monkeypatch.setattr(resident.executor,'now_ms',lambda:now[0])
    monkeypatch.setattr(resident.executor,'_STATE_ROOT',tmp_path)
    monkeypatch.setattr(resident.executor,'_require_receipt_store_custody',lambda *a:None)
    def get(path,query,headers):
        calls.append(path)
        value={'serverTime':now[0]+400} if path=='/fapi/v1/time' else snapshot(batch['orders'][0],
            status='FILLED',executedQty='0.002',time=NOW+1000,updateTime=NOW+1400)
        return 200,json.dumps(value).encode()
    monkeypatch.setattr(transport,'_http_get_once',get)
    monkeypatch.setattr(transport,'_http_once',lambda *a:pytest.fail('new order'))
    stores={k:args[k] for k in ('dispatch','recovery','oms')}
    try:
        message['E']=message['T']=message['o']['T']=NOW+1400
        stream.record('MESSAGE',at_ms=NOW+1012,message=normalize_message(json.dumps(message)))
        stream.record('GAP',at_ms=NOW+1015,reason='MESSAGE_REJECTED',response_sha256='c'*64)
        resident.executor.query_reserved(batch,**stores);before=stream.verify();calls.clear();now[0]=NOW+300000
        monkeypatch.setattr(tool,'_open',lambda *a:dict(stores,stream=stream,event_store=events))
        monkeypatch.setattr(tool,'RecoveryLoop',lambda **kwargs:resident.RecoveryLoop(**kwargs,clock=lambda:now[0]))
        monkeypatch.setattr(tool.time,'sleep',lambda _:pytest.fail('retry of permanent evidence gap'))
        result=tool.settle(tmp_path,batch['context']['account_ref_sha256'],'a'*40)
        assert result['status']=='EVIDENCE_REPAIR_REQUIRED'
        assert calls==['/fapi/v1/time','/fapi/v1/order'] and stream.verify()==before
        assert args['oms'].fills()==[] and len(args['recovery'].pending())==1
    finally:events.close();close(args,stream)


def test_reduction_uses_original_confirmed_basis_and_fresh_venue_grid(tmp_path,monkeypatch):
    from tests.live.test_demo_position_context import completed,held_inputs,NOW,close
    from services.research.demo_execution_origin import save
    batch,args,stream,events,_=completed(tmp_path,monkeypatch)
    try:
        held=held_inputs(batch,args);origin=held['origin'];path=save(tmp_path/'origins',origin)
        context=tool.load_context(tool._state(args['dispatch'],args['recovery'],args['oms']),
            symbol='BTCUSDC',account_ref=origin['account_ref_sha256'],as_of_ms=NOW+2000)
        monkeypatch.setattr(tool,'position',lambda *a:(context,1))
        monkeypatch.setattr(tool,'now_ms',lambda:NOW+2000)
        monkeypatch.setattr(tool.demo_executor_origin,'run',lambda **k:{'status':'CAPTURED','path':str(path)})
        spec={**setup.FIXED,'source_commit':'c'*40,'account_ref_sha256':origin['account_ref_sha256'],
            'registry_sha256':'a'*64,'observed_origin_sha256':'b'*64}
        template={'source_commit':'c'*40,'orders':[batch['orders'][0]]}
        result=tool.reduce_task(tmp_path,origin['account_ref_sha256'],'c'*40,{'template':template,'spec':spec,'index':1})
        order=result['task']['orders'][0]
        assert order['side']=='SELL' and order['reduceOnly'] and order['quantity']=='0.002'
        assert order['newClientOrderId']!=template['orders'][0]['newClientOrderId']
        assert result['context_sha256']==setup.digest(context)
        origin['account_ref_sha256']='f'*64
        with pytest.raises(ValueError):tool.reduce_task(tmp_path,spec['account_ref_sha256'],'c'*40,
            {'template':template,'spec':dict(spec,account_ref_sha256='f'*64),'index':1})
    finally:events.close();close(args,stream)


def test_cash_worker_replays_original_multi_batch_journals_without_mutating_them(tmp_path,monkeypatch):
    from tests.research.test_demo_cash_chain import fixture
    from services.research.demo_execution_journal import DemoExecutionJournal
    from services.research.demo_cash_window import save_capture
    from services.research.demo_cash_chain_status import load_latest
    batches,cash=fixture(tmp_path);area=tmp_path/'cycle';area.mkdir()
    save_capture(area/'cash',cash)
    directory=tmp_path/'receipt-reconciliation/receipts/journals';directory.mkdir(parents=True)
    sources=[]
    for manifest,messages in batches:
        identity=setup.digest(manifest);sources.append({'manifest_hash':identity})
        with DemoExecutionJournal(directory/(identity+'.sqlite3'),manifest=manifest) as journal:journal.append(messages)
    before={p.name:p.read_bytes() for p in directory.glob('*.sqlite3')}
    monkeypatch.setattr(tool,'position',lambda *a:({'sources':sources,'quantity':'0.6'},2))
    report=tool.reconcile(tmp_path,area,cash['account_ref_sha256'])
    assert report['status']=='MATCHED_REPORTED_INPUTS' and report['wallet_residual']=='0'
    assert report['final_quantity']=='0.6' and report['economic_authority'] is False
    view=load_latest(tmp_path/'resident-recovery/functional-cash/views')
    assert view['fee']=='0.0286' and view['canonical_wallet']=='1104.1714'
    assert {p.name:p.read_bytes() for p in directory.glob('*.sqlite3')}==before


def test_cash_close_marker_is_bound_and_wait_is_finite(tmp_path,monkeypatch):
    area=tmp_path/('c'*64);area.mkdir();account='a'*64;source='b'*40
    ticks=[100.0]
    monkeypatch.setattr(tool.time,'monotonic',lambda:ticks[0])
    monkeypatch.setattr(tool.time,'sleep',lambda seconds:ticks.__setitem__(0,ticks[0]+seconds))
    tool.close_cash(area,account,source)
    assert tool.wait_cash_close(area,account,source,600)==30
    with pytest.raises(ValueError):tool.wait_cash_close(area,'d'*64,source,600)
    (area/'cash-close.json').unlink()
    assert tool.wait_cash_close(area,account,source,30)==30


def test_executor_children_keep_original_credential_and_signer_isolation():
    from tools.demo_functional_cycle import child_command
    for operation in ('cash','close-cash','settle','reduce','reconcile'):
        command=child_command(operation,source_commit='c'*40,registry={'executor_uid':2001,'issuer_uid':2002},
            cycle='a'*64,unit='quantos-demo-executor-'+'1'*16)
        assert 'tools.demo_functional_worker' in command and 'User=2001' in command
        assert 'InaccessiblePaths=/etc/quantos/demo-issuer /var/lib/quantos-demo-issuer' in command
        assert 'LIVE_TRADING_ENABLED=false' in command and '--purpose' not in command
