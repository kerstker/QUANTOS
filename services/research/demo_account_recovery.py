"""Reconcile a bounded reported quote account in the existing Decimal ledger.

Two complete REST observations, native fills, commissions and actual flat wallet
checkpoints are separate inputs. Private-stream gaps remain gaps. This pure
projection does not authenticate the inputs, project OMS events or grant orders.
"""
from decimal import Decimal

from services.execution.perpetual_ledger_v2 import (
    LinearPerpetualLedger,ReportedFillReplayEvent,ReportedCashFlowEvent,ReportedCashReplayPolicy,
    CapacityPolicy,CapacityAction,MarginMode,BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,
    canonical_decimal,ledger_decimal_context,
)
from services.research.ai_factory_store import digest
from services.research.demo_execution_origin import assess
from services.research.demo_origin_contract import contract_from_origin
from services.research.demo_opening_projection import contract_spec
from services.research.demo_statement import validate as validate_statement
from services.research.demo_trade_crosscheck import compare_order
from services.research.demo_user_stream import normalize_message
from services.research.demo_income_time import association, POLICY as INCOME_TIME_POLICY


class AccountRecoveryRejected(ValueError):pass


def require(ok,reason):
    if not ok:raise AccountRecoveryRejected(reason)


@ledger_decimal_context()
def reconcile(*,opening,closing,statements,windows,system_orders,adv_notional,participation_rate):
    """Return the verified reported projection and its one ledger, or unknown.

    Input custody and original intent/query binding belong to the protected
    execution boundary, not to this imported/offline calculation.
    """
    result={'schema':'DEMO_REPORTED_ACCOUNT_RECONCILIATION_V1','status':'UNRESOLVED','issues':[],
        'account_ref_sha256':opening.get('account_ref_sha256'),'symbol':opening.get('symbol'),
        'opening_sha256':digest(opening),'closing_sha256':digest(closing),
        'statement_sha256':[digest(s) for s in statements],'windows_sha256':digest(windows),
        'system_orders_sha256':digest(system_orders),'system_order_ids':[],'manual_order_ids':[],
        'stream_gaps':[],'stream_coverage_repaired':False,'canonical_wallet':None,'reported_wallet':None,
        'wallet_residual':None,'final_quantity':None,'reported_fee':None,'reported_realized_pnl':None,
        'net_after_fees':None,'ledger_state':None,'ledger_state_hash':None,'fill_count':None,
        'source_verified':False,'economic_authority':False,'automatic_cycle_completed':False,
        'submission_enabled':False,'strategy_pnl':None,'income_time_policy':INCOME_TIME_POLICY}
    try:
        before=assess(opening,as_of_ms=opening['completed_at_ms'])
        after=assess(closing,as_of_ms=closing['completed_at_ms'])
        require(before['status']==after['status']=='OBSERVED_FLAT_QUOTE','ACCOUNT_RECOVERY_FLAT_CHECKPOINTS_REQUIRED')
        symbol=opening['symbol'];quote=symbol[3:];account=opening['account_ref_sha256']
        require(symbol==closing['symbol'] and account==closing['account_ref_sha256'],'ACCOUNT_RECOVERY_ACCOUNT_BINDING')
        start=opening['completed_at_ms'];end=closing['observed_at_ms']
        require(0<end-start<86400000 and start//86400000==closing['completed_at_ms']//86400000,'ACCOUNT_RECOVERY_SAME_DAY_WINDOW')
        terms,_=contract_from_origin(opening);current_terms,_=contract_from_origin(closing)
        require(terms==current_terms,'ACCOUNT_RECOVERY_CONTRACT_CHANGED')
        require(type(statements) is list and len(statements)==2,'ACCOUNT_RECOVERY_TWO_REST_OBSERVATIONS')
        for statement in statements:
            validate_statement(statement)
            require(statement['status']=='CAPTURED' and statement['symbols']==[symbol] and not statement['gaps'] and
                len(statement['trades'])<1000 and len(statement['income'])<1000,'ACCOUNT_RECOVERY_REST_INCOMPLETE')
            require(statement['start_ms']<=opening['observed_at_ms']-5000 and
                statement['end_ms']>=closing['completed_at_ms']+5000,'ACCOUNT_RECOVERY_REST_WINDOW')
        first,last=statements
        require(last['observed_at_ms']>=first['completed_at_ms']+10000 and
            all(first[k]==last[k] for k in ('start_ms','end_ms','symbols','trades','income')),'ACCOUNT_RECOVERY_REST_CHANGED_OR_TOO_EARLY')
        trades=first['trades'];flows=[r for r in first['income'] if r['asset']==quote]
        require(1<=len(trades)<=1000 and len({t['id'] for t in trades})==len(trades),'ACCOUNT_RECOVERY_TRADE_IDENTITIES')
        require(all(t['symbol']==symbol and t['positionSide']=='BOTH' and t['commissionAsset']==quote for t in trades),
            'ACCOUNT_RECOVERY_SINGLE_QUOTE_TRADES')
        require(all(r['incomeType'] in ('COMMISSION','REALIZED_PNL','FUNDING_FEE','TRANSFER') and
            (r['symbol']==symbol or r['incomeType']=='TRANSFER' and r['symbol']=='') for r in flows),'ACCOUNT_RECOVERY_UNBOUND_QUOTE_FLOW')
        require(all(start+5000<t['time']<end-5000 for t in trades+flows),'ACCOUNT_RECOVERY_CASH_BOUNDARY')
        require(type(windows) is list and 1<=len(windows)<=32,'ACCOUNT_RECOVERY_NATIVE_WINDOW_BUDGET')
        messages={};gaps={}
        for window in windows:
            require(window['account_ref_sha256']==account and 0<window['end_ms']-window['start_ms']<=150000,
                'ACCOUNT_RECOVERY_STREAM_BINDING')
            for row in window['messages']:
                require(window['start_ms']<=row['received_at_ms']<=window['end_ms'],'ACCOUNT_RECOVERY_MESSAGE_WINDOW')
                parsed=normalize_message(row['raw_utf8'])
                require(parsed['response_sha256']==row['response_sha256'],'ACCOUNT_RECOVERY_NATIVE_DIGEST')
                previous=messages.get(row['response_sha256'])
                require(previous is None or previous['raw_utf8']==row['raw_utf8'],'ACCOUNT_RECOVERY_NATIVE_CONFLICT')
                if previous is None or row['received_at_ms']<previous['received_at_ms']:messages[row['response_sha256']]=row
            for gap in window['gaps']:
                require(window['start_ms']<=gap['at_ms']<=window['end_ms'],'ACCOUNT_RECOVERY_GAP_WINDOW')
                gaps[digest(gap)]=gap
        result['stream_gaps']=sorted(gaps.values(),key=lambda g:(g['at_ms'],digest(g)))
        native=list(messages.values());order_ids=sorted({t['orderId'] for t in trades},key=int)
        require(len(order_ids)<=32,'ACCOUNT_RECOVERY_ORDER_BUDGET')
        for order in order_ids:
            comparison=compare_order(first,messages=native,symbol=symbol,venue_order_id=order,
                stream_gaps=result['stream_gaps'],as_of_ms=last['completed_at_ms'])
            require(comparison['status'] in ('OBSERVATIONS_MATCHED','MATCHED_WITH_STREAM_GAPS'),
                'ACCOUNT_RECOVERY_NATIVE_REST_DIFFERENCE')
        fixed={o['newClientOrderId']:o for o in system_orders}
        require(1<=len(fixed)==len(system_orders)<=32 and all(o['symbol']==symbol for o in system_orders),
            'ACCOUNT_RECOVERY_ORIGINAL_ORDER_SCOPE')
        observed={};fill_receipts={}
        for row in sorted(native,key=lambda r:(r['received_at_ms'],r['response_sha256'])):
            value=normalize_message(row['raw_utf8'])['payload'];o=value['o']
            if o['s']!=symbol:continue
            identity=str(o['i']);require(identity in order_ids,'ACCOUNT_RECOVERY_UNBOUND_NATIVE_ORDER')
            previous=observed.get(identity)
            if previous is not None:
                require(all(o[k]==previous[k] for k in ('c','s','S','o','f','q','p','R','ps')),'ACCOUNT_RECOVERY_ORDER_TERMS_CHANGED')
            observed[identity]=o
            if o['x']=='TRADE' and Decimal(o['l'])>0:fill_receipts[str(o['t'])]=row['response_sha256']
        owned=[];external=[]
        for identity,o in observed.items():
            if o['c'] in fixed:
                planned=fixed[o['c']]
                require(o['S']==planned['side'] and o['o']==planned['type'] and o['f']==planned['timeInForce'] and
                    Decimal(o['q'])==Decimal(planned['quantity']) and Decimal(o['p'])==Decimal(planned['price']) and
                    o['R'] is planned['reduceOnly'] and o['ps']==planned['positionSide'],'ACCOUNT_RECOVERY_INTENT_DIFFERENCE')
                owned.append(identity)
            else:
                require(o['R'] is True and o['S']=='SELL' and o['ps']=='BOTH','ACCOUNT_RECOVERY_EXTERNAL_OPENING_UNSUPPORTED')
                external.append(identity)
        require({observed[i]['c'] for i in owned}==set(fixed),'ACCOUNT_RECOVERY_SYSTEM_ORDER_MISSING')
        fee_rows=[r for r in flows if r['incomeType']=='COMMISSION']
        pnl_rows=[r for r in flows if r['incomeType']=='REALIZED_PNL']
        by_trade={t['id']:t for t in trades}
        require(len(fee_rows)==len(trades) and {r['tradeId'] for r in fee_rows}==set(by_trade),
            'ACCOUNT_RECOVERY_COMMISSION_SCOPE')
        require(len({r['tradeId'] for r in pnl_rows})==len(pnl_rows) and all(r['tradeId'] in by_trade for r in pnl_rows),
            'ACCOUNT_RECOVERY_PRICE_PNL_SCOPE')
        for rows,field,sign in ((fee_rows,'commission',-1),(pnl_rows,'realizedPnl',1)):
            require(all(Decimal(r['income'])==sign*Decimal(by_trade[r['tradeId']][field]) and
                association(r['time'],by_trade[r['tradeId']]['time']) is not None for r in rows),'ACCOUNT_RECOVERY_FLOW_TRADE_DIFFERENCE')
        fee=sum((Decimal(t['commission']) for t in trades),Decimal(0))
        pnl=sum((Decimal(t['realizedPnl']) for t in trades),Decimal(0))
        require(pnl==sum((Decimal(r['income']) for r in pnl_rows),Decimal(0)),'ACCOUNT_RECOVERY_REALIZED_PNL_MISSING')
        ledger=LinearPerpetualLedger((contract_spec(terms),),initial_collateral=Decimal(before['wallet']),
            reported_cash_policy=ReportedCashReplayPolicy(account_ref_sha256=account,asset=quote))
        events=[]
        for trade in trades:
            capacity=CapacityPolicy(venue_id='BINANCE_USDM',window_id=digest({'opening':digest(opening)}),
                period_rule_id=BINANCE_USDM_UTC_DAY_CAPACITY_RULE_ID,period_index=trade['time']//86400000,
                adv_notional=Decimal(adv_notional),max_participation_rate=Decimal(participation_rate),action=CapacityAction.REJECT)
            delta=Decimal(trade['qty'])*(1 if trade['side']=='BUY' else -1)
            events.append(ReportedFillReplayEvent(timestamp=trade['time'],instrument_id=symbol,quantity_delta=delta,
                execution_price=Decimal(trade['price']),margin_mode=MarginMode.CROSS,capacity=capacity,
                fee=Decimal(trade['commission']),order_id='reported.'+trade['orderId'],receipt_sha256=fill_receipts[trade['id']]))
        for row in flows:
            if row['incomeType'] not in ('FUNDING_FEE','TRANSFER'):continue
            events.append(ReportedCashFlowEvent(timestamp=row['time'],account_ref_sha256=account,asset=quote,
                income_type=row['incomeType'],transaction_id=row['tranId'],amount=Decimal(row['income']),
                instrument_id=symbol if row['incomeType']=='FUNDING_FEE' else None,source_sha256=digest(first)))
        for event in sorted(events,key=lambda e:e.timestamp):
            if isinstance(event,ReportedFillReplayEvent) and event.quantity_delta<0:
                position=ledger.position(symbol)
                require(position is not None and position.quantity>=-event.quantity_delta,'ACCOUNT_RECOVERY_REDUCTION_EXCEEDS_POSITION')
            ledger.apply_event(event)
        state=ledger.state_as_canonical_dict();position=ledger.position(symbol)
        require(position is None or position.quantity==0,'ACCOUNT_RECOVERY_POSITION_DIFFERENCE')
        residual=Decimal(after['wallet'])-Decimal(state['wallet_balance'])
        require(residual==0,'ACCOUNT_RECOVERY_WALLET_DIFFERENCE')
        cash=sum((Decimal(r['income']) for r in flows if r['incomeType'] in ('FUNDING_FEE','TRANSFER')),Decimal(0))
        require(Decimal(state['wallet_balance'])==Decimal(before['wallet'])+pnl-fee+cash,'ACCOUNT_RECOVERY_V2_PRICE_PNL_DIFFERENCE')
        result.update(status='REPORTED_ACCOUNT_MATCHED',system_order_ids=sorted(owned,key=int),manual_order_ids=sorted(external,key=int),
            canonical_wallet=state['wallet_balance'],reported_wallet=after['wallet'],wallet_residual='0',final_quantity='0',
            reported_fee=canonical_decimal(fee),reported_realized_pnl=canonical_decimal(pnl),net_after_fees=canonical_decimal(pnl-fee),
            ledger_state=state,ledger_state_hash=digest(state),fill_count=len(trades))
        return result,ledger
    except AccountRecoveryRejected as error:
        result['issues']=[str(error)];return result,None
