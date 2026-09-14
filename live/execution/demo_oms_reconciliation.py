"""Trusted Demo executor projection of paired receipt/query facts into the OMS.

This is not a research mutation API or an independent authorization boundary.
The protected executor checks process/store custody before invoking projection.
Only the existing Decimal receipt ledger handles money; legacy float OMS rows
are explicitly order read models and never a new cash, fee or P&L authority.
"""
from decimal import Decimal
from pathlib import Path

from live.execution.demo_batch_risk import _digest
from live.execution.demo_prepared_execution import _load_proof
from live.execution.demo_pre_submit import _retain
from live.execution.demo_receipt_binding import import_reserved_receipts
from live.execution.oms import OmsAuthority
from live.execution.order_event_store import OrderEventSource
from live.execution.order_recovery import RecoveryState
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.research.demo_execution_journal import DemoExecutionJournal, observations
from quantos.contracts.trading import OrderSide
from quantos.contracts.binance_demo_clock import validate_query_clock,INTERIOR_DRIFT_MS,QUANTIZATION_MS

SCHEMA='DEMO_OMS_RECEIPT_RESOLUTION_V1'
TERMINAL={'FILLED':'FILLED','CANCELLED':'CANCELED','EXPIRED':'EXPIRED'}


@ledger_decimal_context()
def prepare_resolution(prepared,*,dispatch,stream,start_ms,end_ms,source_commit,output_directory,as_of_ms,
                       query_clock_sha256=None):
    """Retain a bounded resolution plan; never change OMS/recovery or cash."""
    if type(as_of_ms) is not int or as_of_ms<end_ms:raise ValueError('DEMO_RESOLUTION_FUTURE_WINDOW')
    directory=Path(output_directory)
    imported=import_reserved_receipts(prepared,dispatch=dispatch,stream=stream,start_ms=start_ms,end_ms=end_ms,
        source_commit=source_commit,output_directory=directory/'receipts',query_clock_sha256=query_clock_sha256)
    proof=_load_proof(prepared);snapshot=dispatch.evidence_snapshot()
    result={'schema':SCHEMA,'status':'UNRESOLVED','issues':[],'orders':[],
        'reservation_sha256':prepared['reservation_sha256'],'proposal_sha256':prepared['proposal_sha256'],
        'dispatch_evidence_sha256':_digest(snapshot),'import_sha256':_digest(imported),
        'journal_path':imported['journal_path'],'manifest_hash':imported['manifest_hash'],
        'journal_tail':None,'resolution_at_ms':None,'submission_enabled':False,'source_verified':False,
        'economic_authority':False,'cash_reconciled':False,
        'oms_policy':'FLOAT_ORDER_READ_MODEL_ONLY_NO_CASH_AUTHORITY_V1'}
    issues=result['issues']
    if imported['status']!='IMPORTED_TECHNICAL_RECEIPTS' or imported['replay']['projection_status']!='REPLAYED_TECHNICAL_ONLY':
        issues.append('RECEIPT_LEDGER_UNRESOLVED')
        # Preserve the concrete cause through OMS/resident display instead of
        # reducing every rejected stream or clock to the same generic failure.
        issues.extend(imported['issues'])
    else:
        with DemoExecutionJournal(imported['journal_path'],read_only=True) as journal:
            records,tail,_=journal.read()
            checked=observations(journal.manifest,records,journal.parsed)
            native=checked['native_observations']
            bindings={b['order_id']:b for b in journal.manifest['native']['bindings']}
            result['journal_tail']=tail
            if tail!=imported['replay']['journal_tail']:raise ValueError('DEMO_RESOLUTION_RECEIPTS_CHANGED')
        times=[]
        for observed in checked['observed']['orders']:
            binding=bindings[observed['order_id']];client=binding['client_order_id'];events=observed['events']
            state=events[-1]['state'] if events else 'UNKNOWN'
            if state not in TERMINAL:
                issues.append('PRIVATE_ORDER_NOT_TERMINAL');continue
            queries=[r for r in snapshot['wire'] if r['kind']=='QUERY_RESULT' and r['client_order_id']==client and
                     r['reservation_sha256']==prepared['reservation_sha256']]
            if not queries:
                issues.append('ORIGINAL_ID_QUERY_MISSING');continue
            query=queries[-1];outcome=query['payload']['outcome']
            if outcome['status']!='FOUND':
                issues.append('ORIGINAL_ID_QUERY_UNKNOWN');continue
            q=outcome['order'];last=events[-1]
            query_state='EXPIRED' if q['status']=='EXPIRED_IN_MATCH' else q['status']
            if (q['orderId']!=binding['venue_order_id'] or query_state!=TERMINAL[state] or
                Decimal(q['executedQty'])!=Decimal(last['cumulative_filled'])):
                issues.append('PRIVATE_QUERY_STATE_OR_QUANTITY_DIFFERENCE');continue
            query_venue_upper=query['at_ms']
            if 'clock' in native:
                try:
                    clock=validate_query_clock(query['payload'].get('clock'),
                        query_requested_at_ms=query['payload']['requested_at_ms'])
                    query_venue_upper+=clock['server_time_ms']-clock['requested_at_ms']+QUANTIZATION_MS+INTERIOR_DRIFT_MS
                except ValueError:
                    issues.append('PRIVATE_QUERY_CLOCK_UNRESOLVED');continue
            if (query['payload']['requested_at_ms']<last['received_ms'] or query['at_ms']>as_of_ms or
                q['updateTime']<last['venue_ms'] or q['updateTime']>query_venue_upper or
                native.get('clock',{}).get('available_at_ms',0)>as_of_ms):
                issues.append('PRIVATE_QUERY_CLOCK_UNRESOLVED');continue
            trades={t['response_sha256']:t for t in native['trade_rows'] if t['order_id']==observed['order_id']}
            quantity=Decimal(0);notional=Decimal(0);project=[]
            for index,event in enumerate(events,1):
                trade=trades.get(event['receipt_id'])
                if trade:
                    quantity+=Decimal(trade['quantity']);notional+=Decimal(trade['quantity'])*Decimal(trade['price'])
                if quantity!=Decimal(event['cumulative_filled']):raise ValueError('DEMO_RESOLUTION_EXACT_QUANTITY')
                project.append({'source_sequence':index,'receipt_sha256':event['receipt_id'],
                    'status':TERMINAL.get(event['state'],'NEW' if event['state']=='ACK' else event['state']),
                    'filled_quantity':canonical_decimal(quantity),
                    'average_price':canonical_decimal(notional/quantity) if quantity else '0',
                    'received_at_ms':event['received_ms']})
            # The OMS read model may use floats; the exact cumulative values
            # and every private receipt identity remain in this retained plan.
            result['orders'].append({'client_order_id':client,'venue_order_id':binding['venue_order_id'],
                'terminal_status':TERMINAL[state],'filled_quantity':last['cumulative_filled'],
                'query_native_status':q['status'],
                'query_record_sha256':query['record_hash'],'events':project})
            times.append(query['at_ms'])
        if len(result['orders'])!=len(proof['orders']):issues.append('BATCH_RESOLUTION_INCOMPLETE')
        if not issues:
            result['status']='READY_FOR_OMS_PROJECTION';result['resolution_at_ms']=max(times)
    result['issues']=sorted(set(issues))
    _retain(directory/'plans',result)
    return result


def _project_resolution(plan,*,prepared,dispatch,recovery,oms,event_store,now_ms):
    """Internal trusted-code step; caller must own protected executor stores.

    Append existing OMS events before projection. Recovery is resolved last;
    crashes replay those same event identities and cannot append another fill.
    There is no network, retry authorization, credential or cash operation.
    """
    if (plan.get('schema')!=SCHEMA or plan.get('status')!='READY_FOR_OMS_PROJECTION' or plan.get('issues') or
        plan.get('reservation_sha256')!=prepared['reservation_sha256'] or
        plan.get('proposal_sha256')!=prepared['proposal_sha256'] or type(now_ms) is not int or
        type(plan.get('resolution_at_ms')) is not int or now_ms<plan['resolution_at_ms']):
        raise ValueError('DEMO_RESOLUTION_PLAN_NOT_READY')
    snapshot=dispatch.evidence_snapshot()
    if _digest(snapshot)!=plan['dispatch_evidence_sha256']:raise ValueError('DEMO_RESOLUTION_WIRE_CHANGED')
    proof=_load_proof(prepared)
    reserved=next(r for r in snapshot['reservations'] if r['record_hash']==prepared['reservation_sha256'])
    fixed={o['newClientOrderId']:o for o in proof['orders']}
    if len(plan['orders'])!=len(fixed) or {o['client_order_id'] for o in plan['orders']}!=set(fixed):
        raise ValueError('DEMO_RESOLUTION_ORDER_SET')
    for row in plan['orders']:
        r=recovery.latest(row['client_order_id']);order=fixed[row['client_order_id']]
        if (r is None or r.deployment_id!=proof['context']['deployment_sha256'] or r.instrument_id!=order['symbol'] or
            r.side!=order['side'] or r.quantity!=float(Decimal(order['quantity'])) or
            r.state not in (RecoveryState.ORDER_SUBMISSION_UNKNOWN,RecoveryState.INDETERMINATE,RecoveryState.FOUND,RecoveryState.RESOLVED) or
            r.exchange_order_id not in ('',row['venue_order_id']) or
            r.state in (RecoveryState.FOUND,RecoveryState.RESOLVED) and r.exchange_order_id!=row['venue_order_id'] or
            r.state is RecoveryState.RESOLVED and r.observed_status!=row['terminal_status']):
            raise ValueError('DEMO_RESOLUTION_RECOVERY_CONFLICT')
    authority=OmsAuthority(event_store,oms,recovery_store=recovery)
    # Repair prior committed projections before replaying the same first event.
    authority.replay()
    for row in plan['orders']:
        client=row['client_order_id'];order=fixed[client]
        authority.register_submission(client_order_id=client,instrument_id=order['symbol'],side=OrderSide(order['side']),
            order_quantity=float(Decimal(order['quantity'])),strategy_id=proof['strategy_id'],order_type=order['type'],
            limit_price=float(Decimal(order['price'])),observed_at_ms=reserved['payload']['reserved_at_ms'],
            command_event_id='demo-reservation:'+prepared['reservation_sha256']+':'+client)
        for event in row['events']:
            authority.ingest_venue_snapshot(client_order_id=client,source=OrderEventSource.USER_STREAM,
                source_sequence=event['source_sequence'],venue_event_id='demo-receipt:'+event['receipt_sha256'],
                venue_order_id=row['venue_order_id'],venue_status=event['status'],
                cumulative_filled_qty=float(Decimal(event['filled_quantity'])),avg_fill_price=float(Decimal(event['average_price'])),
                observed_at_ms=event['received_at_ms'],venue_payload_hash=event['receipt_sha256'])
    models=authority.replay()
    fills=oms.fills()
    for row in plan['orders']:
        model=models[row['client_order_id']]
        projected_quantity=sum((f['quantity'] for f in fills if f['client_order_id']==row['client_order_id']),0.0)
        if (model.state.value!={'CANCELED':'CANCELLED'}.get(row['terminal_status'],row['terminal_status']) or
            Decimal(str(model.cumulative_filled_qty))!=Decimal(row['filled_quantity']) or
            Decimal(str(projected_quantity))!=Decimal(row['filled_quantity']) or model.venue_order_id!=row['venue_order_id']):
            raise ValueError('DEMO_RESOLUTION_OMS_DIFFERENCE')
    for row in plan['orders']:
        client=row['client_order_id'];prior=recovery.latest(client)
        if prior.state is RecoveryState.RESOLVED:continue
        if prior.state is not RecoveryState.FOUND:
            recovery.transition(client,RecoveryState.FOUND,now_ms=now_ms,reason='DEMO_PRIVATE_QUERY_MATCHED',
                exchange_order_id=row['venue_order_id'],observed_status=row['terminal_status'])
        recovery.mark_resolved(client,now_ms=now_ms,observed_status=row['terminal_status'])
    return {'schema':SCHEMA,'status':'OMS_TERMINAL_PROJECTED','plan_sha256':_digest(plan),
        'orders':len(plan['orders']),'cash_reconciled':False,'economic_authority':False,'submission_enabled':False}
