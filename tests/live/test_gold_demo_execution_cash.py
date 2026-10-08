"""One signed gold batch supplies the original journal and cash comparison."""
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from live.execution import demo_issuer as issuer,demo_pre_submit as prep,demo_prepared_execution as sender
from live.execution.demo_receipt_binding import import_reserved_receipts,origin_wallet
from live.exchange import binance_demo_exact as transport
from services.research.demo_user_stream import UserStreamJournal,normalize_message
from services.research.demo_execution_journal import DemoExecutionJournal
from services.research.demo_cash_chain import project_cash_chain
from tests.live.test_gold_demo_issuer import case as case
from tests.live.test_demo_pre_submit import NOW
from tests.research.test_binance_order_receipts import message
from tests.research.test_demo_cash_window import fixture as cash_fixture,NOW as CASH_NOW


@pytest.mark.parametrize('damage',[None,'account','trade_id','fee','wallet','research_link'])
def test_signed_gold_batch_cash_uses_its_original_private_trade(case,tmp_path,monkeypatch,damage):
    private,args,handoff,registry,policy,log=case
    identity=None
    if damage=='research_link':
        from dataclasses import replace
        from tests.research.test_gold_ai_research import setup
        from tools.gold_execution_identity import from_archive
        from tests.live.test_demo_issuer import policy_for
        from live.execution.demo_batch_risk import _digest
        research_root=tmp_path/'research'
        tool,_,_=setup(research_root,monkeypatch)
        from services.research.ai_factory_store import FactoryStore
        from services.research_runner.factory_campaign import campaign_tick
        from services.research.run_registry.store import ResearchRunStore
        from services.research.run_registry.registry import RunRegistry
        from services.research_runner.runner import JobQueue
        from tests.research.test_gold_factory_campaign import policy as campaign_policy
        from tests.research.test_ai_factory_provider import LOCAL,Transport
        from tools.ai_factory_provider import propose as model_propose
        raw=(research_root/'data/cache/gold_ai_research/requests/1/input.json').read_bytes()
        factory_root=research_root/'data/cache/ai_factory'
        (factory_root/'gold-inputs').mkdir(parents=True)
        (factory_root/'gold-inputs/1.json').write_bytes(raw)
        choices=[]
        def select(objective,remaining,history,**kwargs):
            choices.append(remaining[0])
            return model_propose(objective,remaining,history,**kwargs,environ=LOCAL,
                transport=Transport({'candidate_id':remaining[0],'reason':'synthetic closed-loop hypothesis'}))
        monkeypatch.setattr(tool,'propose',select)
        initial_policy=dict(campaign_policy(raw),max_cycles=1)
        with FactoryStore(factory_root/'factory.db') as factory:
            factory.create_campaign(initial_policy,operator='test',now=0)
        runs=RunRegistry(ResearchRunStore(str(research_root/'runs.db')))
        queue=JobQueue(runs)
        assert campaign_tick(research_root,queue,runs,now=0)['status']=='SUBMITTED'
        queue.join();campaign_tick(research_root,queue,runs,now=1)
        identity=from_archive(factory_root/'gold/results/1/trial')
        assert choices==[0]

        # Configure only the already synthetic independent issuer fixture before
        # its first reservation. Production approvals are never rewritten.
        sid=identity['strategy_id']
        args['risk_inputs']['strategy']=args['risk_inputs']['strategy'].model_copy(update={'strategy_id':sid})
        attestation=next(iter(args['attestations'].values()))
        args['attestations']={sid:replace(attestation,strategy_id=sid)}
        proposal=prep.proposal(**args)
        handoff=dict(handoff,proposal=proposal,proposal_sha256=_digest(proposal))
        policy=policy_for(proposal,registry)
        policy.update(schema='DEMO_GOLD_ISSUER_POLICY_V1')
        policy['scope']['symbol']='XAUUSDT'
    grant=issuer.issue_submission(handoff,policy=policy,registry=registry,key=private,store=log,now_ms=NOW+15001)
    batch=prep.prepare(json.dumps(grant['envelope']).encode(),verification_now_ms=NOW+15001,
                       evidence_directory=tmp_path/'evidence',**args)
    monkeypatch.setattr(sender,'_require_state_custody',lambda *a,**k:None)
    monkeypatch.setattr(sender,'now_ms',lambda:NOW+15002)
    monkeypatch.setattr(transport,'_credentials',lambda _:('synthetic-key','synthetic-secret'))
    order=args['orders'][0]
    monkeypatch.setattr(transport,'_http_once',lambda *a:(200,json.dumps({'symbol':'XAUUSDT',
        'clientOrderId':order['newClientOrderId'],'orderId':123}).encode()))
    sent=sender.execute(batch,**{k:args[k] for k in ('dispatch','recovery','oms','risk_inputs','mandate','attestations')})
    assert sent['status']=='WIRE_ATTEMPTS_RECORDED'
    stream=UserStreamJournal(tmp_path/'stream.db',account_ref_sha256=batch['context']['account_ref_sha256'])
    try:
        stream.start_session(source_sha256='a'*64,at_ms=NOW)
        payload=message(NOW+15010,'FILLED',order['quantity'],order['quantity'],trade_id=7,fee='0.01',asset='USDT')
        payload['o'].update(s='XAUUSDT',c=order['newClientOrderId'],i=123,q=order['quantity'],p=order['price'],
            L=order['price'],S=order['side'],f=order['timeInForce'],m=order['timeInForce']=='GTX',R=order['reduceOnly'],er='0')
        stream.record('MESSAGE',at_ms=NOW+15012,message=normalize_message(json.dumps(payload)))
        imported=import_reserved_receipts(batch,dispatch=args['dispatch'],stream=stream,start_ms=NOW+1,
            end_ms=NOW+75000,source_commit='a'*40,output_directory=tmp_path/'imports')
        assert imported['status']=='IMPORTED_TECHNICAL_RECEIPTS',imported['issues']
        cash=cash_fixture();offset=NOW+6000-CASH_NOW
        cash['observed_at_ms']+=offset;cash['completed_at_ms']+=offset
        ref=batch['context']['account_ref_sha256'];cash['account_ref_sha256']=ref
        opening=origin_wallet(args['origin']);closing=str(Decimal(opening)-Decimal('0.01'))
        for read in cash['reads']:
            read['requested_at_ms']+=offset;read['received_at_ms']+=offset
            if read['kind'].startswith('clock'):read['data']['serverTime']+=offset
            else:read['account_ref_sha256']=ref
            if read['kind'].startswith('wallet'):
                read['data']=[{'asset':'USDT','wallet':opening if read['kind']=='wallet_before' else closing,
                    'updated_at_ms':NOW-1000 if read['kind']=='wallet_before' else NOW+15010}]
            if read['kind']=='income':
                read['data']=[{'symbol':'XAUUSDT','incomeType':'COMMISSION','income':'-0.01','asset':'USDT',
                    'time':NOW+15010,'tranId':'101','tradeId':'7'}]
        if damage=='account':
            cash['account_ref_sha256']='f'*64
            for read in cash['reads']:
                if read['account_ref_sha256'] is not None:read['account_ref_sha256']='f'*64
        elif damage in ('trade_id','fee'):
            for read in cash['reads']:
                if read['kind']=='income':read['data'][0]['tradeId' if damage=='trade_id' else 'income']='8' if damage=='trade_id' else '-0.02'
        elif damage=='wallet':
            next(r for r in cash['reads'] if r['kind']=='wallet_after')['data'][0]['wallet']=opening
        before=(args['dispatch'].evidence_snapshot(),deepcopy(cash))
        with DemoExecutionJournal(imported['journal_path'],read_only=True) as journal:
            report=project_cash_chain([journal],cash)
        assert before==(args['dispatch'].evidence_snapshot(),cash)
        if damage in (None,'research_link'):
            assert report['status']=='MATCHED_REPORTED_INPUTS',report['issues']
            assert report['canonical_wallet']==closing and report['wallet_residual']=='0'
            assert report['reconciliation']['fee']=='0.01'
            assert report['batches'][0]['manifest_hash']==imported['manifest_hash']
            from live.execution.demo_wire_protocol import outcome_for_query
            from services.validation.demo_submission import GOLD_BATCH_SCHEMA
            from live.execution.order_event_store import SqliteOrderEventStore
            native={'symbol':'XAUUSDT','clientOrderId':order['newClientOrderId'],'orderId':123,
                'side':order['side'],'origQty':order['quantity'],'price':order['price'],'positionSide':'BOTH',
                'reduceOnly':order['reduceOnly'],'timeInForce':order['timeInForce'],'type':'LIMIT',
                'status':'FILLED','executedQty':order['quantity'],'time':NOW+15002,'updateTime':NOW+15010}
            args['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
                client_order_id=order['newClientOrderId'],requested_at_ms=NOW+75001,now_ms=NOW+75002,
                outcome=outcome_for_query(order,200,json.dumps(native).encode(),schema=GOLD_BATCH_SCHEMA))
            monkeypatch.setattr(sender,'_require_receipt_store_custody',lambda *a:None)
            monkeypatch.setattr(sender,'_STATE_ROOT',tmp_path/'executor')
            monkeypatch.setattr(sender,'now_ms',lambda:NOW+75003)
            events=SqliteOrderEventStore(str(tmp_path/'events.db'),create=True)
            try:
                result=sender.reconcile_reserved(batch,**{k:args[k] for k in ('dispatch','recovery','oms')},
                    stream=stream,event_store=events,start_ms=NOW+1,end_ms=NOW+75000,source_commit='a'*40)
                assert result['status']=='OMS_TERMINAL_PROJECTED' and not result['cash_reconciled']
                assert len(args['oms'].fills())==1 and not args['recovery'].pending()
                assert Decimal(str(args['oms'].position('XAUUSDT')))==Decimal(order['quantity'])
                with DemoExecutionJournal(imported['journal_path'],read_only=True) as journal:
                    assert project_cash_chain([journal],cash)==report
                from live.execution.demo_research_feedback import build_feedback
                feedback_args=dict(dispatch=args['dispatch'],stream=stream,capture=cash,start_ms=NOW+1,
                    end_ms=NOW+75000,source_commit='a'*40,output_directory=tmp_path/'feedback')
                with pytest.raises(ValueError,match='NOT_YET_AVAILABLE'):
                    build_feedback(batch,**feedback_args,as_of_ms=NOW+75003)
                feedback=build_feedback(batch,**feedback_args,as_of_ms=cash['completed_at_ms'])
                assert feedback['reservation_sha256']==batch['reservation_sha256']
                assert feedback['manifest_hash']==imported['manifest_hash']
                assert feedback['available_at_ms']==cash['completed_at_ms']
                assert feedback['strategy_id']==args['risk_inputs']['strategy'].strategy_id
                from services.research.gold_execution_identity import identify,SCHEMA
                claimed=identify({'schema':SCHEMA,'attempt':1,'candidate_id':0,'trial_id':'a'*64,
                    'definition_id':'b'*64,'base_input_sha256':'2'*64,'input_sha256':'c'*64,'report_sha256':'d'*64,
                    'research_origin_sha256':'e'*64,'manifest_sha256':'f'*64,'review_request_sha256':'1'*64,
                    'available_at_ms':NOW,'scope':'TECHNICAL_RESEARCH_IDENTITY_ONLY',
                    'economic_eligible':False,'submission_enabled':False})
                with pytest.raises(ValueError,match='STRATEGY_MISMATCH'):
                    build_feedback(batch,**feedback_args,as_of_ms=cash['completed_at_ms'],research_identity=claimed)

                assert feedback['cash_reconciliation']['fee']=='0.01'
                assert not feedback['position_flat'] and feedback['candidate_attribution']=='UNRESOLVED'
                assert not feedback['research_trigger_enabled'] and feedback['strategy_pnl'] is None
                assert build_feedback(batch,**feedback_args,as_of_ms=cash['completed_at_ms'])==feedback
                if identity is not None:
                    linked=build_feedback(batch,**feedback_args,as_of_ms=cash['completed_at_ms'],research_identity=identity)
                    assert linked['candidate_attribution']=='CONTENT_BOUND_TO_SIGNED_STRATEGY'
                    assert linked['research_identity']==identity and not linked['research_trigger_enabled']
                    from types import SimpleNamespace
                    from tools import gold_factory_research,gold_cost_research
                    from services.research.demo_oms_status import resolution_digest as digest
                    review_directory=factory_root/'execution-feedback';review_directory.mkdir()
                    feedback_raw=json.dumps(linked).encode()
                    (review_directory/(linked['result_hash']+'.json')).write_bytes(feedback_raw)
                    with FactoryStore(factory_root/'factory.db') as factory:
                        factory.create_campaign(dict(initial_policy,campaign_id=2,
                            execution_feedback_sha256=linked['result_hash']),operator='test',now=linked['available_at_ms'])
                    monkeypatch.setattr(gold_factory_research,'time',SimpleNamespace(time_ns=lambda:(linked['available_at_ms']+1)*1000000))
                    monkeypatch.setattr(gold_cost_research,'time',SimpleNamespace(time_ns=lambda:(linked['available_at_ms']+1)*1000000))
                    next_round=campaign_tick(research_root,queue,runs,now=linked['available_at_ms']+1)
                    assert next_round['status']=='SUBMITTED' and next_round['attempt']==2
                    queue.join();campaign_tick(research_root,queue,runs,now=linked['available_at_ms']+2)
                    assert choices==[0,1]
                    with FactoryStore(factory_root/'factory.db') as factory:
                        factory.verify_campaigns()
                        new_trial=factory.trials(2)[0]
                        assert factory.get(2)['state']=='COMPLETED'
                        assert new_trial['trial_id']!=identity['trial_id']
                        records=[e for e in factory.events(2) if e['kind']=='EXECUTION_REVIEW_INPUT']
                        assert records and records[0]['payload']['feedback_sha256']==linked['result_hash']
                    assert (factory_root/'gold/requests/2/execution-feedback.json').read_bytes()==feedback_raw
                    new_manifest=json.loads((factory_root/'gold/results/2/trial/manifest.json').read_bytes())
                    assert new_manifest['trial_id']==new_trial['trial_id']
                    from services.research.trial_ledger import TrialLedger
                    with TrialLedger(str(factory_root/'gold/results/2/trial/trials.db'),create=False,read_only=True) as ledger:
                        assert all(e.at>=linked['available_at_ms'] for e in ledger.load(new_trial['trial_id']).events)
                    assert digest({k:v for k,v in linked.items() if k!='result_hash'})==linked['result_hash']


                for change in ('account','trade','wallet'):
                    altered=deepcopy(cash)
                    if change=='account':
                        altered['account_ref_sha256']='f'*64
                        for read in altered['reads']:
                            if read['account_ref_sha256'] is not None:read['account_ref_sha256']='f'*64
                    elif change=='trade':
                        for read in altered['reads']:
                            if read['kind']=='income':read['data'][0]['tradeId']='8'
                    else:next(r for r in altered['reads'] if r['kind']=='wallet_after')['data'][0]['wallet']=opening
                    with pytest.raises(ValueError,match='FEEDBACK_CASH_EVIDENCE_UNRESOLVED'):
                        build_feedback(batch,**dict(feedback_args,capture=altered),as_of_ms=cash['completed_at_ms'])
                args['dispatch'].record_query_result(reservation_sha256=batch['reservation_sha256'],
                    client_order_id=order['newClientOrderId'],requested_at_ms=NOW+75004,now_ms=NOW+75005,
                    outcome=outcome_for_query(order,None,None,schema=GOLD_BATCH_SCHEMA))
                with pytest.raises(ValueError,match='FEEDBACK_ORDER_EVIDENCE_UNRESOLVED'):
                    build_feedback(batch,**feedback_args,as_of_ms=cash['completed_at_ms'])


            finally:events.close()

        else:assert report['status']!='MATCHED_REPORTED_INPUTS'
        assert before[1]==cash
        assert not report['source_verified'] and not report['authoritative'] and report['strategy_pnl'] is None
    finally:stream.db.close()
