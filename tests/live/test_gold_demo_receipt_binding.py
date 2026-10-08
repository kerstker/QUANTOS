"""Gold private receipts bind the original signed intent without OMS mutation."""
from copy import deepcopy
import json
from decimal import Decimal

import pytest

from live.execution import demo_issuer as issuer, demo_pre_submit as prep, demo_prepared_execution as sender
from live.execution.demo_receipt_binding import bind_reserved_receipts,import_reserved_receipts
from live.exchange import binance_demo_exact as transport
from services.research.demo_user_stream import UserStreamJournal, normalize_message
from tests.live.test_gold_demo_issuer import case as case
from tests.live.test_demo_pre_submit import NOW
from tests.research.test_binance_order_receipts import message


@pytest.mark.parametrize('damage', [None, 'retry', 'automatic', 'venue', 'symbol', 'gap', 'quantity'])
def test_gold_private_fill_preserves_identity_and_unknowns(case,tmp_path,monkeypatch,damage):
    retry=damage=='retry';automatic=damage=='automatic'
    if retry or automatic:damage=None
    private,args,handoff,registry,policy,log=case
    from dataclasses import replace
    from services.research.gold_execution_identity import identify,SCHEMA
    from tests.live.test_demo_issuer import policy_for
    from live.execution.demo_batch_risk import _digest
    identity=identify({'schema':SCHEMA,'attempt':1,'candidate_id':0,'trial_id':'a'*64,
        'definition_id':'b'*64,'base_input_sha256':'2'*64,'input_sha256':'c'*64,'report_sha256':'d'*64,
        'research_origin_sha256':'e'*64,'manifest_sha256':'f'*64,'review_request_sha256':'1'*64,
        'available_at_ms':NOW-1,'scope':'TECHNICAL_RESEARCH_IDENTITY_ONLY',
        'economic_eligible':False,'submission_enabled':False})
    if damage is None:
        from tests.live.gold_feedback_cycle import start,resume
        research=start(tmp_path/'research',monkeypatch,automatic=automatic)
        identity=research['identity']
    sid=identity['strategy_id']
    args['risk_inputs']['strategy']=args['risk_inputs']['strategy'].model_copy(update={'strategy_id':sid})
    args['attestations']={sid:replace(next(iter(args['attestations'].values())),strategy_id=sid)}
    proposal=prep.proposal(**args)
    handoff=dict(handoff,proposal=proposal,proposal_sha256=_digest(proposal))
    policy=policy_for(proposal,registry)
    policy.update(schema='DEMO_GOLD_ISSUER_POLICY_V1');policy['scope']['symbol']='XAUUSDT'
    # Isolated three-attempt test budget, fixed before any fixture grant.
    if retry:policy.update(max_grants=3,max_total_notional='400')
    grant=issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+1)
    batch=prep.prepare(json.dumps(grant['envelope']).encode(),verification_now_ms=NOW+1,
                       evidence_directory=tmp_path/'executor/evidence',**args)
    monkeypatch.setattr(sender,'_require_state_custody',lambda *a,**k:None)
    monkeypatch.setattr(sender,'now_ms',lambda:NOW+2)
    monkeypatch.setattr(transport,'_credentials',lambda _:('synthetic-key','synthetic-secret'))
    order=args['orders'][0]
    monkeypatch.setattr(transport,'_http_once',lambda *a:(200,json.dumps({'symbol':'XAUUSDT',
        'clientOrderId':order['newClientOrderId'],'orderId':123}).encode()))
    sender.execute(batch,**{k:args[k] for k in ('dispatch','recovery','oms','risk_inputs','mandate','attestations')})
    stream=UserStreamJournal(tmp_path/'stream.db',account_ref_sha256=batch['context']['account_ref_sha256'])
    try:
        stream.start_session(source_sha256='a'*64,at_ms=NOW)
        p=message(NOW+10,'FILLED',order['quantity'],order['quantity'],trade_id=7,fee='0.01')
        p['o'].update(s='XAUUSDT',c=order['newClientOrderId'],i=123,q=order['quantity'],p=order['price'],
                      L=order['price'],S=order['side'],N='USDT',f=order['timeInForce'],
                      m=order['timeInForce']=='GTX',R=order['reduceOnly'],er='0')
        if damage=='venue':p['o']['i']=124
        elif damage=='symbol':p['o']['s']='BTCUSDT'
        elif damage=='quantity':p['o']['z']=str(Decimal(order['quantity'])*2)
        elif damage=='gap':stream.record('GAP',at_ms=NOW+5,reason='STREAM_DISCONNECTED')
        stream.record('MESSAGE',at_ms=NOW+12,message=normalize_message(json.dumps(p)))
        before=args['dispatch'].evidence_snapshot()
        result=bind_reserved_receipts(batch,dispatch=args['dispatch'],stream=stream,start_ms=NOW+1,end_ms=NOW+1000)
        if damage is None:
            assert result['status']=='BOUND_RECEIPT_INPUTS',result['issues']
            assert result['native']['schema']=='BINANCE_USDM_GOLD_ORDER_RECEIPTS_V2'
            assert result['filled_quantity']==order['quantity'] and result['trades_count']==1
            imported=import_reserved_receipts(batch,dispatch=args['dispatch'],stream=stream,
                start_ms=NOW+1,end_ms=NOW+1000,source_commit='a'*40,output_directory=tmp_path/'imports')
            assert imported['status']=='IMPORTED_TECHNICAL_RECEIPTS',imported['issues']
            assert imported['replay']['projection_status']=='REPLAYED_TECHNICAL_ONLY'
            assert imported['replay']['symbol']=='XAUUSDT'
            report=imported['replay']
            assert report['reported_commission_by_asset']=={'USDT':'0.01'}
            assert Decimal(report['position_quantity'])==Decimal(order['quantity'])
            assert report['venue_cash_reconciliation']=='UNKNOWN' and not report['authoritative']
            from services.research.demo_execution_journal import DemoExecutionJournal,replay_report,prepare
            with DemoExecutionJournal(imported['journal_path'],read_only=True) as journal:
                assert replay_report(journal)==report
                from services.research.demo_order_recovery import build_query_plan
                plan=build_query_plan(journal)
                assert plan.symbol=='XAUUSDT'
                assert plan.schema_version=='BINANCE_DEMO_GOLD_QUERY_PLAN_V1'
                from tools.binance_demo_order_query import collect
                from services.research.demo_order_recovery import compare_orders,save_capture,load_capture
                from quantos.contracts.binance_demo_orders import QueryCapture,QueryPlan
                native={'symbol':'XAUUSDT','clientOrderId':order['newClientOrderId'],'orderId':123,
                    'side':order['side'],'type':'LIMIT','timeInForce':order['timeInForce'],
                    'positionSide':'BOTH','reduceOnly':order['reduceOnly'],'origQty':order['quantity'],
                    'executedQty':order['quantity'],'price':order['price'],'status':'FILLED',
                    'time':NOW+2,'updateTime':NOW+10}
                def get(path,**kwargs):
                    return {'serverTime':NOW+1100} if path.endswith('time') else native
                capture=collect(plan,get=get,clock_ms=lambda:NOW+1100,account_read=True,
                    environ={'BINANCE_TESTNET_API_KEY':'fixture','BINANCE_TESTNET_API_SECRET':'fixture'},
                    provenance='SYNTHETIC_TEST_FIXTURE')
                assert capture.schema_version=='BINANCE_DEMO_GOLD_ORDER_QUERIES_V1'
                assert compare_orders(report,capture,as_of_ms=NOW+1100)['status']=='MATCHED_OBSERVATIONS'
                save_capture(tmp_path/'queries',capture)
                assert load_capture(tmp_path/'queries')==capture
                wrong_capture=capture.model_dump();wrong_capture['schema_version']='BINANCE_DEMO_ORDER_QUERIES_V1'
                with pytest.raises(ValueError):QueryCapture.model_validate(wrong_capture)
                with pytest.raises(ValueError):QueryPlan.model_validate(plan.model_dump())

                assert journal.manifest['schema']=='DEMO_GOLD_EXECUTION_REPLAY_V2'
                wrong=deepcopy(journal.manifest);wrong['schema']='DEMO_EXECUTION_REPLAY_V2'
                with pytest.raises(ValueError):prepare(wrong)
                wrong=deepcopy(journal.manifest);wrong['native']['schema']='BINANCE_USDM_ORDER_RECEIPTS_V2'
                with pytest.raises(ValueError):prepare(wrong)
            again=import_reserved_receipts(batch,dispatch=args['dispatch'],stream=stream,
                start_ms=NOW+1,end_ms=NOW+1000,source_commit='a'*40,output_directory=tmp_path/'imports')
            assert again['replay']==imported['replay']
        else:
            assert result['status']=='UNRESOLVED' and result['issues'] and result['native'] is None
        assert args['dispatch'].evidence_snapshot()==before
        assert args['oms'].fills()==[] and len(args['recovery'].pending())==1
        assert result['source_verified'] is False
        if damage is None:
            from live.execution.demo_wire_protocol import outcome_for_query
            from services.validation.demo_submission import GOLD_BATCH_SCHEMA
            from live.execution.demo_oms_reconciliation import prepare_resolution
            missing=prepare_resolution(batch,dispatch=args['dispatch'],stream=stream,
                start_ms=NOW+1,end_ms=NOW+1000,source_commit='a'*40,
                output_directory=tmp_path/'resolution',as_of_ms=NOW+1101)
            assert missing['status']=='UNRESOLVED' and 'ORIGINAL_ID_QUERY_MISSING' in missing['issues']
            args['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
                client_order_id=order['newClientOrderId'],requested_at_ms=NOW+1100,now_ms=NOW+1101,
                outcome=outcome_for_query(order,200,json.dumps(native).encode(),schema=GOLD_BATCH_SCHEMA))
            resolution=prepare_resolution(batch,dispatch=args['dispatch'],stream=stream,
                start_ms=NOW+1,end_ms=NOW+1000,source_commit='a'*40,
                output_directory=tmp_path/'resolution',as_of_ms=NOW+1101)
            assert resolution['status']=='READY_FOR_OMS_PROJECTION',resolution['issues']
            assert resolution['orders'][0]['filled_quantity']==order['quantity']
            assert resolution['cash_reconciled'] is False
            assert args['oms'].fills()==[] and len(args['recovery'].pending())==1
            from live.execution.order_event_store import SqliteOrderEventStore
            from live.execution.order_recovery import RecoveryState
            from services.research.demo_oms_status import load_latest
            event_path=tmp_path/'events.db'
            events=SqliteOrderEventStore(str(event_path),create=True)
            monkeypatch.setattr(sender,'_require_receipt_store_custody',lambda *a:None)
            monkeypatch.setattr(sender,'_STATE_ROOT',tmp_path/'executor')
            monkeypatch.setattr(sender,'now_ms',lambda:NOW+1102)
            projection=args['oms'].apply_oms_projection
            def crash(record,**kw):
                if record.event.venue_status=='FILLED':raise OSError('synthetic crash after event append')
                return projection(record,**kw)
            def reconcile():
                return sender.reconcile_reserved(batch,**{k:args[k] for k in ('dispatch','recovery','oms')},
                    event_store=events,stream=stream,start_ms=NOW+1,end_ms=NOW+1000,source_commit='a'*40)
            try:
                monkeypatch.setattr(args['oms'],'apply_oms_projection',crash)
                with pytest.raises(sender.DemoExecutionStopped):reconcile()
                assert args['oms'].fills()==[] and len(args['recovery'].pending())==1
                view_dir=tmp_path/'executor/receipt-reconciliation/views'
                assert load_latest(view_dir)['phase']=='PROJECTION_INTERRUPTED'
                events.close()
                events=SqliteOrderEventStore(str(event_path))
                monkeypatch.setattr(args['oms'],'apply_oms_projection',projection)
                projected=reconcile()
                assert projected['status']=='OMS_TERMINAL_PROJECTED' and not projected['cash_reconciled']
                assert args['oms'].orders()[0]['status']=='FILLED'
                assert len(args['oms'].fills())==1
                assert Decimal(str(args['oms'].position('XAUUSDT')))==Decimal(order['quantity'])
                assert args['recovery'].latest(order['newClientOrderId']).state is RecoveryState.RESOLVED
                count=events.row_count();recovery_rows=args['recovery'].latest_records()
                assert reconcile()==projected
                assert events.row_count()==count and args['recovery'].latest_records()==recovery_rows
                assert len(args['oms'].fills())==1 and not args['recovery'].pending()
                assert load_latest(view_dir)['phase']=='OMS_TERMINAL_PROJECTED'
                assert not load_latest(view_dir)['cash_reconciled']
                from live.execution import demo_position_context as position
                monkeypatch.setattr(position,'_ROOT',tmp_path/'executor')
                monkeypatch.setattr(position,'_custody',lambda *a,**k:None)
                state=prep._state(args['dispatch'],args['recovery'],args['oms'])
                context=position.load_context(state,symbol='XAUUSDT',
                    account_ref=batch['context']['account_ref_sha256'],as_of_ms=NOW+2000)
                assert context['quantity']==order['quantity']
                assert Decimal(context['entry_notional'])==Decimal(order['quantity'])*Decimal(order['price'])
                assert context['canonical_wallet']=='4999.99'
                from tools import demo_functional_worker as worker,demo_operator_setup as setup
                from services.research.demo_account_baseline import normalize_response
                from services.research.demo_execution_origin import save
                held=deepcopy(args['origin'])
                held['observed_at_ms']+=2000;held['completed_at_ms']+=2000
                for read in held['reads']:
                    read['requested_at_ms']+=2000;read['received_at_ms']+=2000
                    kind=read['kind'];data=read['data']
                    if kind.startswith('clock'):data['serverTime']+=2000
                    elif kind in ('book','mark'):data['time']+=2000
                    elif kind.startswith('account'):
                        asset=next(a for a in data['assets'] if a['asset']=='USDT')
                        asset.update(walletBalance='4999.99',crossWalletBalance='4999.99',availableBalance='4993.99',
                            unrealizedProfit='0',marginBalance='4999.99')
                        data['positions']=[{'symbol':'XAUUSDT','positionSide':'BOTH','positionAmt':order['quantity']}]
                        read['data']=normalize_response(kind,data)
                    elif kind=='positions':
                        read['data']=normalize_response(kind,[{'symbol':'XAUUSDT','positionSide':'BOTH',
                            'positionAmt':order['quantity'],'entryPrice':order['price'],'markPrice':order['price'],
                            'unRealizedProfit':'0','isolatedWallet':'0','marginAsset':'USDT','updateTime':NOW+10}])
                path=save(tmp_path/'held',held)
                def owned_position(*a,**kwargs):
                    assert kwargs['symbol']=='XAUUSDT'
                    return context,1
                monkeypatch.setattr(worker,'position',owned_position)
                monkeypatch.setattr(worker,'now_ms',lambda:NOW+2000)
                calls=[]
                def capture(**kwargs):
                    calls.append(kwargs)
                    assert kwargs['symbol']=='XAUUSDT'
                    return {'status':'CAPTURED','path':str(path)}
                monkeypatch.setattr(worker.demo_executor_origin,'run',capture)
                spec={**setup.FIXED,'schema':'DEMO_GOLD_FUNCTIONAL_SETUP_SPEC_V1','symbol':'XAUUSDT',
                    'source_commit':'c'*40,'account_ref_sha256':held['account_ref_sha256'],
                    'registry_sha256':'a'*64,'observed_origin_sha256':'b'*64}
                template={'source_commit':'c'*40,'orders':[dict(order,timeInForce='IOC')]}
                reduced=worker.reduce_task(tmp_path,held['account_ref_sha256'],'c'*40,
                    {'spec':spec,'template':template,'index':1})
                close_order=reduced['task']['orders'][0]
                assert close_order['symbol']=='XAUUSDT' and close_order['side']=='SELL' and close_order['reduceOnly']
                assert close_order['quantity']==context['quantity'] and len(calls)==1
                assert close_order['newClientOrderId']!=order['newClientOrderId']
                wrong=deepcopy(template);wrong['orders'][0]['symbol']='BTCUSDC'
                with pytest.raises(ValueError,match='FUNCTIONAL_REDUCTION_SCOPE'):
                    worker.reduce_task(tmp_path,held['account_ref_sha256'],'c'*40,
                        {'spec':spec,'template':wrong,'index':1})
                assert len(calls)==1
                # Continue the actual original stores, issuer budget and basis;
                # a generated task alone does not prove a reduction can execute.
                from live.execution.demo_batch_risk import _digest
                risk=deepcopy(args['risk_inputs'])
                risk['now_ms']=NOW+2000;risk['market']['observed_at_ms']=NOW+2000
                risk['account'].update(cash='4993.99',equity='4999.99',day_pnl='-0.01',
                    positions=[{'symbol':'XAUUSDT','quantity':order['quantity'],'mark_price':order['price']}])
                empty=None
                if retry:
                    from tests.live.gold_empty_reduction import expire
                    empty=expire(tmp_path=tmp_path,monkeypatch=monkeypatch,args=args,held=held,order=close_order,
                        risk=risk,handoff=handoff,policy=policy,registry=registry,private=private,log=log,
                        stream=stream,events=events)
                    context=position.load_context(prep._state(args['dispatch'],args['recovery'],args['oms']),
                        symbol='XAUUSDT',account_ref=held['account_ref_sha256'],as_of_ms=NOW+2000)
                    assert context['quantity']==order['quantity'] and context['canonical_wallet']=='4999.99'
                values=dict(args,origin=held,risk_inputs=risk,orders=[close_order],now_ms=NOW+2000)
                if retry:
                    duplicate=dict(empty['orders'][0],newClientOrderId=close_order['newClientOrderId'])
                    with pytest.raises(prep.DemoPreparationRejected,match='DEMO_BATCH_RISK_REJECTED'):
                        prep.proposal(**dict(values,orders=[duplicate]))
                proposal=prep.proposal(**values)
                assert proposal['state']['position_context']==context
                close_handoff=dict(handoff,proposal=proposal,proposal_sha256=_digest(proposal))
                close_grant=issuer.issue_submission(close_handoff,policy=policy,registry=registry,key=private,
                    store=log,now_ms=NOW+2001)
                closing=prep.prepare(json.dumps(close_grant['envelope']).encode(),verification_now_ms=NOW+2001,
                    evidence_directory=tmp_path/'executor/evidence',**values)
                monkeypatch.setattr(sender,'now_ms',lambda:NOW+2002)
                sent=[]
                def close_http(body,headers):
                    from urllib.parse import parse_qs
                    request=parse_qs(body.decode());sent.append(request)
                    assert request['reduceOnly']==['true'] and request['symbol']==['XAUUSDT']
                    return 200,json.dumps({'symbol':'XAUUSDT','clientOrderId':close_order['newClientOrderId'],
                        'orderId':124}).encode()
                monkeypatch.setattr(transport,'_http_once',close_http)
                execution={k:values[k] for k in ('dispatch','recovery','oms','risk_inputs','mandate','attestations')}
                assert sender.execute(closing,**execution)['status']=='WIRE_ATTEMPTS_RECORDED'
                with pytest.raises(sender.DemoExecutionStopped):sender.execute(closing,**execution)
                assert len(sent)==1 and len(args['oms'].fills())==1
                event=message(NOW+2010,'FILLED',close_order['quantity'],close_order['quantity'],trade_id=8,fee='0.01')
                event['o'].update(s='XAUUSDT',c=close_order['newClientOrderId'],i=124,q=close_order['quantity'],
                    p=close_order['price'],L=order['price'],ap=order['price'],S='SELL',N='USDT',f='IOC',m=False,R=True,er='0',rp='0')
                stream.record('MESSAGE',at_ms=NOW+2012,message=normalize_message(json.dumps(event)))
                query=dict(native,clientOrderId=close_order['newClientOrderId'],orderId=124,side='SELL',
                    price=close_order['price'],reduceOnly=True,timeInForce='IOC',time=NOW+2002,updateTime=NOW+2010)
                args['dispatch'].record_query_result(reservation_sha256=closing['reservation_sha256'],
                    client_order_id=close_order['newClientOrderId'],requested_at_ms=NOW+2030,now_ms=NOW+2031,
                    outcome=outcome_for_query(close_order,200,json.dumps(query).encode(),schema=GOLD_BATCH_SCHEMA))
                monkeypatch.setattr(sender,'now_ms',lambda:NOW+3102)
                closed=sender.reconcile_reserved(closing,**{k:args[k] for k in ('dispatch','recovery','oms')},
                    event_store=events,stream=stream,start_ms=NOW+2001,end_ms=NOW+3000,source_commit='a'*40)
                assert closed['status']=='OMS_TERMINAL_PROJECTED',closed
                final=position.load_context(prep._state(args['dispatch'],args['recovery'],args['oms']),
                    symbol='XAUUSDT',account_ref=held['account_ref_sha256'],as_of_ms=NOW+4000)
                assert final['quantity']=='0' and final['entry_notional']=='0'
                assert final['canonical_wallet']=='4999.98' and final['initial_equity']=='5000'
                assert len(final['sources'])==2+int(retry) and len(args['oms'].fills())==2
                assert args['oms'].position('XAUUSDT')==0 and not args['recovery'].pending()
                assert not final['cash_reconciled']
                from contextlib import ExitStack
                from services.research.demo_execution_journal import DemoExecutionJournal
                from services.research.demo_cash_chain import project_cash_chain
                from tests.research.test_demo_cash_window import fixture as cash_fixture,NOW as CASH_NOW
                cash=cash_fixture();offset=NOW-6000-CASH_NOW
                cash['observed_at_ms']+=offset;cash['completed_at_ms']+=offset
                cash['account_ref_sha256']=held['account_ref_sha256']
                for read in cash['reads']:
                    read['requested_at_ms']+=offset;read['received_at_ms']+=offset
                    if read['kind'].startswith('clock'):read['data']['serverTime']+=offset
                    else:read['account_ref_sha256']=held['account_ref_sha256']
                    if read['kind'].startswith('wallet'):
                        read['data']=[{'asset':'USDT','wallet':'5000' if read['kind']=='wallet_before' else '4999.98',
                            'updated_at_ms':NOW-7000 if read['kind']=='wallet_before' else NOW+2010}]
                    elif read['kind']=='income':
                        read['data']=[{'symbol':'XAUUSDT','incomeType':'COMMISSION','income':'-0.01','asset':'USDT',
                            'time':at,'tranId':str(100+trade),'tradeId':str(trade)}
                            for trade,at in ((7,NOW+10),(8,NOW+2010))]
                batch_windows=[(batch,NOW+1,NOW+1501 if retry else NOW+2001)]
                if retry:batch_windows.append((empty,NOW+1501,NOW+2001))
                batch_windows.append((closing,NOW+2001,NOW+75000))
                with ExitStack() as journals:
                    chain=[]
                    for prepared,start,end in batch_windows:
                        imported=import_reserved_receipts(prepared,dispatch=args['dispatch'],stream=stream,
                            start_ms=start,end_ms=end,source_commit='a'*40,output_directory=tmp_path/'cash-chain')
                        assert imported['status']=='IMPORTED_TECHNICAL_RECEIPTS',imported['issues']
                        chain.append(journals.enter_context(DemoExecutionJournal(imported['journal_path'],read_only=True)))
                    report=project_cash_chain(chain,cash)
                    assert report['status']=='MATCHED_REPORTED_INPUTS',report['issues']
                    assert report['canonical_wallet']=='4999.98' and report['wallet_residual']=='0'
                    assert report['reconciliation']['fee']=='0.02' and len(report['batches'])==2+int(retry)
                    assert not report['authoritative'] and report['strategy_pnl'] is None
                    changed=deepcopy(cash)
                    for read in changed['reads']:
                        if read['kind']=='income':read['data'][1]['tradeId']='9'
                    rejected=project_cash_chain(chain,changed)
                    assert rejected['status']=='UNRESOLVED'
                    assert 'COMMISSION_TRADE_SCOPE_MISMATCH' in rejected['issues']
                from live.execution.demo_research_feedback import build_roundtrip_feedback
                windows=[dict(prepared=p,start_ms=start,end_ms=end) for p,start,end in batch_windows]
                feedback_args=dict(dispatch=args['dispatch'],stream=stream,capture=cash,
                    source_commit='a'*40,output_directory=tmp_path/'roundtrip-feedback',as_of_ms=cash['completed_at_ms'])
                feedback=build_roundtrip_feedback(windows,**feedback_args)
                assert feedback['schema']=='DEMO_ROUNDTRIP_RESEARCH_FEEDBACK_V1'
                assert feedback['position_flat'] and feedback['canonical_wallet']=='4999.98'
                assert feedback['cash_reconciliation']['fee']=='0.02' and feedback['wallet_residual']=='0'
                assert [b['reservation_sha256'] for b in feedback['batches']]==[
                    p['reservation_sha256'] for p,_,_ in batch_windows]
                assert len(feedback['orders'])==2+int(retry) and feedback['candidate_attribution']=='UNRESOLVED'
                assert not feedback['research_trigger_enabled'] and not feedback['economic_eligible']
                assert build_roundtrip_feedback(windows,**feedback_args)==feedback
                with pytest.raises(ValueError):build_roundtrip_feedback(windows[::-1],**feedback_args)
                with pytest.raises(ValueError):build_roundtrip_feedback([windows[0],windows[0]],**feedback_args)
                with pytest.raises(ValueError):build_roundtrip_feedback(windows[1:],**feedback_args)
                with pytest.raises(ValueError,match='CASH_EVIDENCE_UNRESOLVED'):
                    build_roundtrip_feedback(windows,**dict(feedback_args,capture=changed))
                with pytest.raises(ValueError,match='NOT_YET_AVAILABLE'):
                    build_roundtrip_feedback(windows,**dict(feedback_args,as_of_ms=NOW+75000))
                linked=build_roundtrip_feedback(windows,**feedback_args,research_identity=identity)
                assert linked['candidate_attribution']=='CONTENT_BOUND_TO_SIGNED_STRATEGY'
                from services.research.gold_execution_feedback import research_trigger
                config={'input_sha256':identity['base_input_sha256'],'execution_feedback_sha256':linked['result_hash']}
                assert not research_trigger(config,linked,now=linked['available_at_ms']-1)['ready']
                assert research_trigger(config,linked,now=linked['available_at_ms'])['ready']
                for damage in ('batch','hash','flat','position','residual'):
                    malformed=deepcopy(linked)
                    if damage=='batch':malformed['batches'][1]=deepcopy(malformed['batches'][0])
                    elif damage=='hash':malformed['batches'][1]['journal_tail']='invalid'
                    elif damage=='flat':malformed['position_flat']=False
                    elif damage=='position':malformed['positions']['XAUUSDT']={'quantity':'0.1'}
                    else:malformed['wallet_residual']='0.01'
                    malformed['result_hash']=_digest({k:v for k,v in malformed.items() if k!='result_hash'})
                    with pytest.raises(ValueError,match='GOLD_FEEDBACK_ROUNDTRIP'):
                        research_trigger(dict(config,execution_feedback_sha256=malformed['result_hash']),malformed,
                            now=linked['available_at_ms'])
                from services.research.demo_cash_window import save_capture
                root=tmp_path/'executor'
                monkeypatch.setattr(worker,'require_custody',lambda:root)
                monkeypatch.setattr(worker,'fixed_account',lambda:(root,held['account_ref_sha256']))
                monkeypatch.setattr(worker,'_open',lambda *a:dict(
                    **{k:args[k] for k in ('dispatch','recovery','oms')},stream=stream,event_store=events))
                monkeypatch.setattr(worker,'now_ms',lambda:cash['completed_at_ms'])
                (root/'resident-recovery').mkdir(mode=0o700,exist_ok=True)
                area=worker.area_for(root,'e'*64)
                (area/'cycle-scope.json').write_text(json.dumps(worker.scope_record(area.name,identity['strategy_id'],symbol='XAUUSDT')))
                (area/'cycle-scope.json').chmod(0o600)
                save_capture(area/'cash',cash)
                for member in (root,*root.rglob('*')):
                    member.chmod(0o700 if member.is_dir() else 0o600)
                export_args=(root,area,held['account_ref_sha256'],'a'*40,{'research_identity':identity})
                state_before=prep._state(args['dispatch'],args['recovery'],args['oms'])
                monkeypatch.setattr(transport,'_credentials',lambda *a:pytest.fail('feedback must not read credentials'))
                monkeypatch.setattr(transport,'_http_once',lambda *a:pytest.fail('feedback must not send HTTP'))
                exported=worker.export_feedback(*export_args)
                from pathlib import Path
                retained=json.loads(Path(exported['path']).read_bytes())
                assert exported['status']=='ROUNDTRIP_FEEDBACK_RETAINED'
                assert retained['position_flat'] and retained['cash_reconciliation']['fee']=='0.02'
                if retry:
                    assert len(retained['batches'])==3 and len(retained['orders'])==3
                    assert retained['orders'][1]['terminal_status']=='EXPIRED'
                    assert retained['orders'][1]['filled_quantity']=='0'
                    assert retained['batches'][1]['reservation_sha256']==empty['reservation_sha256']
                assert exported['feedback_sha256']==retained['result_hash']
                assert worker.export_feedback(*export_args)==exported
                retained_before={p.name:p.read_bytes() for p in (area/'research-feedback').iterdir()}
                with pytest.raises(ValueError):
                    worker.export_feedback(root,area,'f'*64,'a'*40,{'research_identity':identity})
                with pytest.raises(ValueError):
                    worker.export_feedback(root,area,held['account_ref_sha256'],'a'*40,{'research_identity':None})
                assert {p.name:p.read_bytes() for p in (area/'research-feedback').iterdir()}==retained_before
                assert prep._state(args['dispatch'],args['recovery'],args['oms'])==state_before
                from tools import demo_feedback_publish as delivery
                with monkeypatch.context() as source_custody:
                    source_custody.setattr(delivery,'EXECUTOR',root)
                    source_custody.setattr(delivery.custody,'_require_protected',lambda *a,**kw:None)
                    import os
                    delivered=delivery.read_export(cycle=area.name,
                        artifact_sha256=exported['artifact_sha256'],uid=os.getuid())
                assert delivered==retained
                for mutation in ('source','extra','digest','future'):
                    bad=deepcopy(delivered);pin=bad['result_hash'];commit='a'*40;at=bad['available_at_ms']
                    if mutation=='source':commit='b'*40
                    elif mutation=='extra':bad['unexpected_private_field']='not for publication'
                    elif mutation=='digest':pin='f'*64
                    else:at-=1
                    with pytest.raises(ValueError):
                        delivery.validate_export(bad,source_commit=commit,feedback_sha256=pin,now=at)
                resume(research,delivered,monkeypatch)
                unknown_batch=empty if retry else batch
                unknown_order=unknown_batch['orders'][0]
                args['dispatch'].record_query_result(reservation_sha256=unknown_batch['reservation_sha256'],
                    client_order_id=unknown_order['newClientOrderId'],requested_at_ms=NOW+75001,now_ms=NOW+75002,
                    outcome=outcome_for_query(unknown_order,None,None,schema=GOLD_BATCH_SCHEMA))
                with pytest.raises(ValueError,match='ORDER_EVIDENCE_UNRESOLVED'):
                    build_roundtrip_feedback(windows,**feedback_args,research_identity=identity)
                with pytest.raises(ValueError):worker.export_feedback(*export_args)

            finally:
                events.close()

    finally:
        stream.db.close()
