"""Venue IDs are learned after reservation without rewriting frozen intents."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from live.execution.demo_receipt_binding import bind_reserved_receipts
from live.execution.demo_receipt_binding import import_reserved_receipts
from live.execution import demo_prepared_execution as executor
from live.exchange import binance_demo_exact as transport
from services.research.demo_user_stream import UserStreamJournal, normalize_message
from tests.live.test_demo_prepared_execution import ready, NOW
from tests.research.test_binance_order_receipts import message


def fixture(tmp_path, monkeypatch, *, ack=True):
    batch, args = ready(tmp_path, monkeypatch)
    order = batch['orders'][0]
    def http(*_):
        if not ack: raise TimeoutError('fixture')
        return 200,json.dumps({'symbol':order['symbol'],'clientOrderId':order['newClientOrderId'],
                              'orderId':9007199254740993}).encode()
    monkeypatch.setattr(transport,'_http_once',http)
    executor.execute(batch, **args)
    stream=UserStreamJournal(tmp_path/'stream.db',account_ref_sha256=batch['context']['account_ref_sha256'])
    stream.start_session(source_sha256='a'*64,at_ms=NOW)
    p=message(NOW+10,'FILLED','0.002','0.002',trade_id=7,fee='0.054')
    p['o'].update(c=order['newClientOrderId'],i=9007199254740993,q=order['quantity'],p=order['price'],
                  L=order['price'],f=order['timeInForce'],m=order['timeInForce']=='GTX',R=order['reduceOnly'],er='0')
    return batch,args,stream,p


def bind(batch,args,stream):
    return bind_reserved_receipts(batch,dispatch=args['dispatch'],stream=stream,start_ms=NOW+1,end_ms=NOW+1000)


def close(args,stream):
    stream.db.close()
    for name in ('dispatch','recovery','oms'):args[name].close()


@pytest.mark.parametrize('ack',[False, True])
def test_stream_recovers_exact_venue_id_without_resending_or_rewriting_intent(tmp_path,monkeypatch,ack):
    batch,args,stream,p=fixture(tmp_path,monkeypatch,ack=ack)
    try:
        original=Path(batch['proposal_path']).read_bytes(); before=args['dispatch'].evidence_snapshot()
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)))
        r=bind(batch,args,stream)
        assert r['status']=='BOUND_RECEIPT_INPUTS' and r['issues']==[]
        assert r['native']['bindings'][0]['venue_order_id']=='9007199254740993'
        assert r['native']['bindings'][0]['client_order_id']==batch['orders'][0]['newClientOrderId']
        assert r['native']['bindings'][0]['time_in_force']==batch['orders'][0]['timeInForce']
        assert r['trades_count']==1 and r['filled_quantity']=='0.002'
        assert r['submission_enabled'] is False and r['coverage_verified'] is False
        assert r['source_verified'] is False and r['strategy_pnl'] is None
        assert args['dispatch'].evidence_snapshot()==before and Path(batch['proposal_path']).read_bytes()==original
        assert args['oms'].fills()==[] and len(args['recovery'].pending())==1
    finally:close(args,stream)


@pytest.mark.parametrize('failure',['venue_conflict','wrong_options','foreign_same_quote','namespace','before_send','gap','future_venue'])
def test_conflicting_or_unbound_stream_facts_remain_visible_without_relabeling(tmp_path,monkeypatch,failure):
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    try:
        if failure=='venue_conflict':p['o']['i']+=1
        elif failure=='wrong_options':p['o']['R']=True
        elif failure=='foreign_same_quote':p['o'].update(c='manual-order',i=123,s='ETHUSDC')
        elif failure=='namespace':
            stream.db.close();stream=UserStreamJournal(tmp_path/'other.db',account_ref_sha256='f'*64)
        elif failure=='before_send':p['E']=p['T']=p['o']['T']=NOW
        elif failure=='future_venue':p['E']=NOW+20
        else:stream.record('GAP',at_ms=NOW+5,reason='STREAM_DISCONNECTED')
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)))
        r=bind(batch,args,stream)
        assert r['status']=='UNRESOLVED' and r['issues']
        assert r['native'] is None and r['strategy_pnl'] is None
        assert stream.verify()['records']>=1 and len(args['dispatch'].wire_snapshot())==2
    finally:close(args,stream)


def test_ack_alone_never_becomes_a_fill_or_terminal_order(tmp_path,monkeypatch):
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    try:
        r=bind(batch,args,stream)
        assert r['status']=='UNRESOLVED' and 'PRIVATE_RECEIPTS_MISSING' in r['issues']
        assert r['trades_count']==0 and r['native'] is None
    finally:close(args,stream)


def test_other_quote_order_is_counted_as_excluded_not_imported(tmp_path,monkeypatch):
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    try:
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)))
        other=deepcopy(p);other['o'].update(c='manual-usdt',s='ETHUSDT',i=123)
        stream.record('MESSAGE',at_ms=NOW+13,message=normalize_message(json.dumps(other)))
        r=bind(batch,args,stream)
        assert r['status']=='BOUND_RECEIPT_INPUTS' and r['excluded_other_quote_messages']==1
        assert len(r['native']['messages'])==1 and len(r['excluded_message_sha256'])==1
    finally:close(args,stream)


def test_reserved_batch_imports_into_original_receipt_journal_with_actual_origin_terms(tmp_path,monkeypatch):
    from services.research.demo_execution_journal import DemoExecutionJournal, replay_report
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    try:
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)))
        kwargs=dict(dispatch=args['dispatch'],stream=stream,start_ms=NOW+1,end_ms=NOW+1000,
                    source_commit='a'*40,output_directory=tmp_path/'imported')
        result=import_reserved_receipts(batch,**kwargs)
        assert result['status']=='IMPORTED_TECHNICAL_RECEIPTS'
        assert result['replay']['position_quantity']=='0.002'
        assert result['replay']['reconciliation']['initial_equity']=='5000'
        assert result['replay']['reconciliation']['fee']=='0.054'
        assert result['contract_projection']['settlement_rounding_verified'] is False
        assert result['contract_projection']['symbol_config_cap_verified'] is False
        path=Path(result['journal_path']);before=path.read_bytes()
        second=import_reserved_receipts(batch,**kwargs)
        assert second['replay']==result['replay'] and path.read_bytes()==before
        with DemoExecutionJournal(path,read_only=True) as journal:
            assert replay_report(journal)==result['replay']
            assert journal.manifest['contract']['new_risk_notional_cap']=='10000'
            assert journal.manifest['native']['bindings'][0]['venue_order_id']=='9007199254740993'
        assert args['oms'].fills()==[] and len(args['recovery'].pending())==1
    finally:close(args,stream)


def test_unknown_binding_is_retained_but_does_not_create_cash_or_receipt_projection(tmp_path,monkeypatch):
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    try:
        result=import_reserved_receipts(batch,dispatch=args['dispatch'],stream=stream,start_ms=NOW+1,end_ms=NOW+1000,
            source_commit='a'*40,output_directory=tmp_path/'imported')
        assert result['status']=='UNRESOLVED' and result['replay'] is None
        assert Path(result['binding_path']).is_file()
        assert list((tmp_path/'imported').rglob('*.sqlite3'))==[]
    finally:close(args,stream)


def paired_cash_capture(ref):
    from tests.research.test_demo_cash_window import fixture as cash_fixture, NOW as CASH_NOW
    cash=cash_fixture(); shift=NOW-6200-CASH_NOW
    for k in ('observed_at_ms','completed_at_ms'):cash[k]+=shift
    cash['account_ref_sha256']=ref
    for r in cash['reads']:
        for k in ('requested_at_ms','received_at_ms'):r[k]+=shift
        if r['kind'].startswith('clock'):r['data']['serverTime']+=shift
        else:r['account_ref_sha256']=ref
        if r['kind'].startswith('wallet'):
            r['data']=[{'asset':'USDC','wallet':'5000' if r['kind']=='wallet_before' else '4999.946','updated_at_ms':NOW-1000 if r['kind']=='wallet_before' else NOW+10}]
        if r['kind']=='income':r['data']=[{'symbol':'BTCUSDC','incomeType':'COMMISSION','income':'-0.054',
            'asset':'USDC','time':NOW+10,'tranId':'9007199254740993','tradeId':'7'}]
    return cash


def test_origin_receipts_and_cash_window_form_one_reproducible_reported_projection(tmp_path,monkeypatch):
    from services.research.demo_cash_projection import project_cash_window
    from services.research.demo_execution_journal import DemoExecutionJournal
    batch,args,stream,p=fixture(tmp_path,monkeypatch)
    try:
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)))
        result=import_reserved_receipts(batch,dispatch=args['dispatch'],stream=stream,start_ms=NOW+1,end_ms=NOW+70000,
            source_commit='a'*40,output_directory=tmp_path/'imported')
        assert result['status']=='IMPORTED_TECHNICAL_RECEIPTS'
        cash=paired_cash_capture(batch['context']['account_ref_sha256'])
        with DemoExecutionJournal(result['journal_path'],read_only=True) as journal:
            report=project_cash_window(journal,cash)
            assert report['status']=='MATCHED_REPORTED_INPUTS'
            assert report['canonical_wallet']=='4999.946' and report['wallet_residual']=='0'
            assert report['reconciliation']['fee']=='0.054' and report['strategy_pnl'] is None
            # A funding/transfer before the bound origin cannot be posted again.
            for r in cash['reads']:
                if r['kind']=='income':r['data'].append({'symbol':'','incomeType':'TRANSFER','income':'1',
                    'asset':'USDC','time':NOW-1,'tranId':'42','tradeId':''})
            assert 'CASH_BEFORE_BOUND_OPENING' in project_cash_window(journal,cash)['issues']
    finally:close(args,stream)
