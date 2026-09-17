"""Synthetic next-day intents, exact transport, receipts and cash share one book."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from tests.live.test_demo_day_checkpoint import recovered_context, later_close, NOW, DAY
from tests.live.test_demo_reported_recovery import close


def observation(template, at, wallet, quantity='0'):
    from services.research.demo_account_baseline import normalize_response
    delta=at-template['observed_at_ms']
    def shift(value):
        if isinstance(value,dict):return {k:shift(v) for k,v in value.items()}
        if isinstance(value,list):return [shift(v) for v in value]
        return value+delta if type(value) is int and value>=NOW else value
    origin=shift(template)
    for read in origin['reads']:
        if read['kind'].startswith('account'):
            asset=next(a for a in read['data']['assets'] if a['asset']=='USDC')
            asset.update(walletBalance=wallet,crossWalletBalance=wallet,marginBalance=wallet,
                availableBalance=str(Decimal(wallet)-(Decimal('6') if quantity!='0' else 0)),unrealizedProfit='0')
            read['data']['positions']=[p for p in read['data']['positions'] if p['symbol']!='BTCUSDC']
            if quantity!='0':read['data']['positions'].append(dict(symbol='BTCUSDC',positionSide='BOTH',positionAmt=quantity))
            read['data']=normalize_response(read['kind'],read['data'])
        elif read['kind']=='positions':
            read['data']=[p for p in read['data'] if p['symbol']!='BTCUSDC']
            if quantity!='0':read['data'].append(dict(symbol='BTCUSDC',positionSide='BOTH',positionAmt=quantity,
                entryPrice='60000',markPrice='60000',unRealizedProfit='0',isolatedWallet='0',marginAsset='USDC',updateTime=at))
            read['data']=normalize_response(read['kind'],read['data'])
    return origin


@pytest.mark.parametrize('portfolio',[False,True])
def test_next_day_round_trip_keeps_original_loss_and_matches_full_account(tmp_path,monkeypatch,portfolio):
    from tests.live import test_demo_prepared_execution as fixture
    from tests.live.test_demo_submission_verification import encoded
    from tests.research.test_binance_order_receipts import message,receipt
    from services.validation.demo_submission import signing_payload
    from services.research.demo_user_stream import UserStreamJournal,normalize_message
    from live.execution import demo_day_checkpoint as checkpoint, demo_position_context as position
    from live.execution import demo_pre_submit as prep, demo_prepared_execution as execute
    from live.execution import demo_authority, demo_oms_reconciliation as reconciliation
    from live.exchange import binance_demo_exact as transport
    from live.execution.demo_pre_submit import _retain,_state
    from live.execution.demo_wire_protocol import outcome_for_query
    from tools.demo_account_cash import compare
    captured={};original_setup=fixture.setup
    def setup(*a,**kw):
        private,envelope,values=original_setup(*a,**kw)
        captured.update(private=private,envelope=envelope)
        return private,envelope,values
    monkeypatch.setattr(fixture,'setup',setup)
    batch,stores,events,value,state,context=recovered_context(tmp_path,monkeypatch)
    stream=UserStreamJournal(tmp_path/'next-day-stream.db',account_ref_sha256=context['account_ref_sha256'])
    try:
        proof=json.loads(Path(batch['proposal_path']).read_text())
        record=checkpoint.prepare(context=context,proofs=[proof],inputs=later_close(value),
            created_at_ms=NOW+DAY+100000,source_commit='c'*40)
        _retain(tmp_path/'receipt-reconciliation/day-checkpoints',record)
        # Independent synthetic signing material is valid only in this test.
        registry=demo_authority.load_registry_for_executor()
        registry['keys'][0]['valid_until_ms']=NOW+DAY+500000
        monkeypatch.setattr(demo_authority,'_read_root_owned_json',lambda *_:json.dumps(registry).encode())
        stores['mandate']=replace(stores['mandate'],expires_at=NOW+DAY+500000)
        stream.start_session(source_sha256='a'*64,at_ms=NOW+DAY+190000)
        original_risk=deepcopy(stores['risk_inputs']);all_orders=[];all_trades=[];all_income=[];windows=[];calls=[]
        strategy_id='demo-functional-isolated-next-day'
        allocation={'schema':'DEMO_PORTFOLIO_ALLOCATION_V2','source_commit':'c'*40,'symbol':'BTCUSDC',
            'account_ref_sha256':context['account_ref_sha256'],'model_hash':'a'*64,'capital':'500',
            'fee_bps':4,'slip_bps':5,'ioc_offset_bps':5,'source_binding_sha256':'b'*64}
        if portfolio:
            from live.execution.demo_portfolio_task import strategy_id as portfolio_strategy_id
            strategy_id=portfolio_strategy_id(allocation)
        previous_id=original_risk['strategy'].strategy_id
        original_risk['strategy']=original_risk['strategy'].model_copy(update={'strategy_id':strategy_id})
        stores['attestations']={strategy_id:replace(stores['attestations'][previous_id],strategy_id=strategy_id)}
        for index,(side,wallet,quantity) in enumerate([('BUY','4999.6801','0'),('SELL','4999.6201','0.002')]):
            at=NOW+DAY+200000+index*10000;venue=31+index;trade_id=11+index
            current=position.load_context(_state(stores['dispatch'],stores['recovery'],stores['oms']),
                symbol='BTCUSDC',account_ref=context['account_ref_sha256'],as_of_ms=at)
            origin=observation(value['closing'],at,wallet,quantity)
            risk=deepcopy(original_risk);risk['now_ms']=at;risk['market']['observed_at_ms']=at
            risk['account'].update(position.validate_origin(origin,current,as_of_ms=at))
            order=dict(batch['orders'][0],newClientOrderId='QDnextday'+str(index),side=side,
                reduceOnly=bool(index),timeInForce='IOC')
            kwargs=dict(orders=[order],origin=origin,risk_inputs=risk,mandate=stores['mandate'],
                attestations=stores['attestations'],now_ms=at,**{k:stores[k] for k in ('dispatch','recovery','oms')})
            proposed=prep.proposal(**kwargs)
            assert proposed['state']['position_context']['initial_equity']=='5000'
            envelope=deepcopy(captured['envelope'])
            envelope['payload'].update(context=proposed['context'],approval_id=str(index+7)*64,
                issued_at_ms=at-1,expires_at_ms=at+9000)
            envelope['signature_base64url']=encoded(captured['private'].sign(signing_payload(envelope)))
            prepared=prep.prepare(json.dumps(envelope).encode(),**kwargs,evidence_directory=tmp_path/'evidence')
            monkeypatch.setattr(execute,'now_ms',lambda:at+1)
            def http(*_):
                calls.append(order['newClientOrderId'])
                return 200,json.dumps(dict(symbol='BTCUSDC',clientOrderId=order['newClientOrderId'],orderId=venue)).encode()
            monkeypatch.setattr(transport,'_http_once',http)
            stores['risk_inputs']=risk
            execute.execute(prepared,**stores)
            event=message(at+10,'FILLED','0.002','0.002',trade_id=trade_id,fee='0.06',asset='USDC')
            event['o'].update(c=order['newClientOrderId'],i=venue,S=side,q='0.002',p='60000',L='60000',
                ap='60000',f='IOC',m=False,R=bool(index),er='0',rp='0')
            stream.record('MESSAGE',at_ms=at+12,message=normalize_message(json.dumps(event)))
            query=dict(symbol='BTCUSDC',clientOrderId=order['newClientOrderId'],orderId=venue,side=side,
                origQty='0.002',price='60000',positionSide='BOTH',reduceOnly=bool(index),timeInForce='IOC',
                type='LIMIT',status='FILLED',executedQty='0.002',time=at+1,updateTime=at+10)
            stores['dispatch'].record_query_result(reservation_sha256=prepared['reservation_sha256'],
                client_order_id=order['newClientOrderId'],requested_at_ms=at+30,
                outcome=outcome_for_query(order,200,json.dumps(query).encode()),now_ms=at+31)
            plan=reconciliation.prepare_resolution(prepared,dispatch=stores['dispatch'],stream=stream,
                start_ms=at+1,end_ms=at+1000,source_commit='c'*40,
                output_directory=tmp_path/'receipt-reconciliation',as_of_ms=at+1001)
            assert plan['status']=='READY_FOR_OMS_PROJECTION',plan
            outcome=reconciliation._project_resolution(plan,prepared=prepared,dispatch=stores['dispatch'],
                recovery=stores['recovery'],oms=stores['oms'],event_store=events,now_ms=at+1001)
            _retain(tmp_path/'receipt-reconciliation/outcomes',outcome)
            all_orders.append(order)
            all_trades.append(dict(symbol='BTCUSDC',id=str(trade_id),orderId=str(venue),side=side,positionSide='BOTH',
                price='60000',qty='0.002',quoteQty='120',realizedPnl='0',commission='0.06',commissionAsset='USDC',time=at+10,maker=False))
            all_income.append(dict(symbol='BTCUSDC',incomeType='COMMISSION',income='-0.06',asset='USDC',time=at+10,
                tranId=str(100+index),tradeId=str(trade_id)))
            windows.append(dict(account_ref_sha256=context['account_ref_sha256'],start_ms=at+1,end_ms=at+1000,
                messages=[receipt(event,at+12)],gaps=[]))
        final=position.load_context(_state(stores['dispatch'],stores['recovery'],stores['oms']),symbol='BTCUSDC',
            account_ref=context['account_ref_sha256'],as_of_ms=NOW+DAY+220000)
        assert final['canonical_wallet']=='4999.5601' and final['quantity']=='0' and len(final['sources'])==3
        assert sum(map(Decimal,final['capacity_consumed'].values()))==Decimal('479.8')
        cash=later_close(value);cash['closing']=observation(value['closing'],NOW+DAY+220000,'4999.5601')
        cash['system_orders']+=all_orders;cash['windows']+=windows
        for sweep in cash['statements']:
            sweep['trades']+=all_trades;sweep['income']+=all_income
            sweep['end_ms']=NOW+DAY+230000
            sweep['completed_at_ms']+=200000;sweep['observed_at_ms']+=200000
            for read in sweep['reads']:
                read['requested_at_ms']+=200000;read['received_at_ms']+=200000
                if read['kind'] in ('trades','income'):
                    read['end_ms']=sweep['end_ms'];read['data']=deepcopy(sweep[read['kind']])
        report,ledger,count=compare(cash,final,cycle_order_ids=['31','32'])
        assert count==2 and report['wallet_residual']=='0' and report['manual_order_ids']==['22']
        assert report['daily_cash'][-1]['net_after_costs']=='-0.12'
        assert position.validate_origin(cash['closing'],final,as_of_ms=cash['closing']['completed_at_ms'])['day_pnl']=='-0.4399'
        assert len(calls)==2 and not report['economic_authority']
        # Exercise the production cash completion/persistence path with frozen
        # synthetic GET results, retaining original manual attribution.
        from tools import demo_account_cash as cash_tool
        from contextlib import nullcontext
        monkeypatch.setattr(cash_tool,'require_custody',lambda:tmp_path)
        monkeypatch.setattr(cash_tool,'fixed_account',lambda:(tmp_path,context['account_ref_sha256']))
        monkeypatch.setattr(cash_tool,'_cycle_lease',lambda *a:nullcontext())
        monkeypatch.setattr(cash_tool,'_open',lambda *a:dict(stores,stream=stream))
        monkeypatch.setattr(cash_tool,'_read',lambda p,*a,**kw:json.loads(Path(p).read_text()))
        monkeypatch.setattr(cash_tool,'_check_path',lambda *a,**kw:None)
        monkeypatch.setattr(cash_tool,'now_ms',lambda:NOW+DAY+300000)
        def capture(*a,**kw):
            assert kw['day_replay'] is True and kw['system_orders']==cash['system_orders']
            return cash
        monkeypatch.setattr(cash_tool,'capture_inputs',capture)
        if portfolio:
            completed=cash_tool.run_portfolio(tmp_path,tmp_path/'portfolio-cycle'/('f'*64),context['account_ref_sha256'],
                'c'*40,allocation=allocation)
            with pytest.raises(ValueError,match='ACCOUNT_CASH_PORTFOLIO_SCOPE'):
                cash_tool.run_portfolio(tmp_path,tmp_path/'portfolio-cycle'/('f'*64),context['account_ref_sha256'],
                    'c'*40,allocation={**allocation,'account_ref_sha256':'f'*64})
            with pytest.raises(ValueError,match='ACCOUNT_CASH_CYCLE_SCOPE'):
                cash_tool.run(tmp_path,tmp_path/'functional-cycle'/('f'*64),context['account_ref_sha256'],
                    'c'*40,strategy_id=strategy_id)
        else:
            completed=cash_tool.run(tmp_path,tmp_path/'functional-cycle'/('f'*64),context['account_ref_sha256'],
                'c'*40,strategy_id=strategy_id)
        assert completed['status']=='MATCHED_REPORTED_INPUTS' and completed['fill_count']==2
        assert len(calls)==2
        # Removing the checkpoint breaks reconstruction even with valid receipts.
        for path in (tmp_path/'receipt-reconciliation/day-checkpoints').glob('*.json'):path.unlink()
        with pytest.raises(position.PositionContextRejected):
            position.load_context(_state(stores['dispatch'],stores['recovery'],stores['oms']),symbol='BTCUSDC',
                account_ref=context['account_ref_sha256'],as_of_ms=NOW+DAY+220000)
    finally:stream.db.close();events.close();close(stores)
