"""Trusted isolated-executor coordinator for one exact, pre-reserved Demo batch.

No mainnet path and no signer. The fixed registry/credentials and controlled
state must be installed independently. Missing deployment custody rejects before
reading credentials. ACKs remain input facts; recovery/unique V2 cash work is
separate and may never infer fills from these acknowledgements.
"""
from dataclasses import asdict
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import stat
import time

from live.contracts import LiveOrderRequest
from live.execution import demo_authority
from live.execution.demo_batch_risk import _digest,_json_value,screen
from live.execution.demo_pre_submit import _preparation_lease,_risk_with_history,_venue_rules,_venue_account_cap,_retain
from live.execution.demo_wire_protocol import outcome_for_response,outcome_for_query
from live.exchange import binance_demo_exact
from live.governance.mandate import AUTO,Mandate,evaluate_mandate
from live.governance.validation_attestation import require_validation_attestation
from quantos.contracts.trading import OrderSide
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_execution_origin import assess

_STATE_ROOT=Path('/var/lib/quantos-demo-executor')


class DemoExecutionStopped(RuntimeError):
    pass


def now_ms():return time.time_ns()//1_000_000


def _require_state_custody(prepared,dispatch,recovery,oms):
    registry=demo_authority.load_registry_for_executor();uid=registry['executor_uid']
    for key in ('reservation_sha256','proposal_sha256'):
        if type(prepared.get(key)) is not str or re.fullmatch('[a-f0-9]{64}',prepared[key]) is None:
            raise DemoExecutionStopped('DEMO_EXECUTOR_PREPARED_HASH')
    if dispatch.path!=_STATE_ROOT/'dispatch.sqlite3':raise DemoExecutionStopped('DEMO_EXECUTOR_STATE_PATH')
    for store,name in ((recovery,'recovery.sqlite3'),(oms,'oms.sqlite3')):
        # sqlite reports the file actually open, rather than a caller label.
        db=getattr(store,'_conn',None)
        path=db.execute('PRAGMA database_list').fetchone()[2]
        if Path(path)!=_STATE_ROOT/name:raise DemoExecutionStopped('DEMO_EXECUTOR_STATE_PATH')
    proof=Path(prepared['proposal_path'])
    if proof!=_STATE_ROOT/'evidence'/(prepared['proposal_sha256']+'.json'):
        raise DemoExecutionStopped('DEMO_EXECUTOR_EVIDENCE_PATH')
    for parent in reversed(_STATE_ROOT.parents):
        demo_authority._require_protected(parent.lstat(),directory=True)
    for path,directory in ((_STATE_ROOT,True),(_STATE_ROOT/'evidence',True),
        *[(_STATE_ROOT/name,False) for name in ('dispatch.sqlite3','recovery.sqlite3','oms.sqlite3')],(proof,False)):
        info=path.lstat()
        if (info.st_uid!=uid or info.st_mode & 0o022 or
            not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) or
            not directory and info.st_nlink!=1):raise DemoExecutionStopped('DEMO_EXECUTOR_STATE_CUSTODY')


def _load_proof(prepared):
    fd=os.open(prepared['proposal_path'],os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as handle:raw=handle.read(16_000_001)
    if len(raw)>16_000_000:raise DemoExecutionStopped('DEMO_EXECUTOR_PROOF_BUDGET')
    proof=load_json_without_duplicate_keys(raw)
    if _digest(proof)!=prepared['proposal_sha256'] or proof['schema']!='DEMO_PRE_SUBMISSION_PROPOSAL_V1':
        raise DemoExecutionStopped('DEMO_EXECUTOR_PROOF_BINDING')
    if (prepared['schema']!='DEMO_PREPARED_BATCH_V1' or prepared['status']!='INTENTS_PERSISTED' or
        prepared['orders']!=proof['orders'] or prepared['context']!=proof['context'] or
        prepared['submission_enabled'] is not False or prepared['economic_authority'] is not False):
        raise DemoExecutionStopped('DEMO_EXECUTOR_PREPARED_BINDING')
    return proof


def _check_intents(proof,reservation,dispatch,recovery,oms):
    before=proof['state'];planned={o['newClientOrderId']:o for o in proof['orders']}
    if dispatch.snapshot()!=before['reservations']+[reservation]:raise DemoExecutionStopped('DEMO_EXECUTOR_RESERVATION_CHANGED')
    records=[_json_value(asdict(r)) for r in recovery.latest_records()]
    if [r for r in records if r['client_order_id'] not in planned]!=before['recovery']:
        raise DemoExecutionStopped('DEMO_EXECUTOR_RECOVERY_CHANGED')
    own=[r for r in records if r['client_order_id'] in planned]
    if len(own)!=len(planned):raise DemoExecutionStopped('DEMO_EXECUTOR_INTENT_MISSING')
    for row in own:
        order=planned[row['client_order_id']]
        expected={'deployment_id':proof['context']['deployment_sha256'],'instrument_id':order['symbol'],
            'side':order['side'],'quantity':float(Decimal(order['quantity'])),'exchange_order_id':'',
            'state':'ORDER_SUBMISSION_UNKNOWN','reason':'DEMO_INTENT_PERSISTED_BEFORE_NETWORK',
            'recorded_at_ms':reservation['payload']['reserved_at_ms']}
        if any(row[k]!=v for k,v in expected.items()):raise DemoExecutionStopped('DEMO_EXECUTOR_INTENT_CHANGED')
    orders=oms.orders()
    if [o for o in orders if o['client_order_id'] not in planned]!=before['oms']:
        raise DemoExecutionStopped('DEMO_EXECUTOR_OMS_CHANGED')
    own=[o for o in orders if o['client_order_id'] in planned]
    if len(own)!=len(planned):raise DemoExecutionStopped('DEMO_EXECUTOR_INTENT_MISSING')
    for row in own:
        order=planned[row['client_order_id']]
        expected={'instrument_id':order['symbol'],'side':order['side'],'quantity':float(Decimal(order['quantity'])),
            'status':'PENDING_NEW','venue_order_id':'','strategy_id':proof['strategy_id'],'order_type':'LIMIT',
            'limit_price':float(Decimal(order['price'])),'submitted_at':reservation['payload']['reserved_at_ms']}
        if any(row[k]!=v for k,v in expected.items()):raise DemoExecutionStopped('DEMO_EXECUTOR_OMS_CHANGED')
    wire=dispatch.wire_snapshot()
    if [w for w in wire if w['reservation_sha256']!=reservation['record_hash']]!=before['wire']:
        raise DemoExecutionStopped('DEMO_EXECUTOR_WIRE_CHANGED')


def _preflight(proof,reservation, *,dispatch,recovery,oms,risk_inputs,mandate,attestations):
    at=now_ms();context=proof['context'];orders=proof['orders'];sid=proof['strategy_id']
    if not risk_inputs['now_ms']<=at:raise DemoExecutionStopped('DEMO_EXECUTOR_CLOCK')
    if (reservation['payload']['context']!=context or reservation['payload']['orders']!=orders or
        _digest(proof['origin'])!=context['origin_sha256'] or _digest(proof['state'])!=context['recovery_sha256'] or
        _digest(asdict(mandate))!=context['mandate_sha256'] or _digest({sid:asdict(attestations[sid])})!=context['validation_sha256']):
        raise DemoExecutionStopped('DEMO_EXECUTOR_CONTEXT_CHANGED')
    demo_authority.verify_for_executor(json.dumps(reservation['payload']['envelope']).encode(),expected_context=context,orders=orders,now_ms=at)
    if assess(proof['origin'],as_of_ms=at)['status']!='OBSERVED_FLAT_QUOTE':raise DemoExecutionStopped('DEMO_EXECUTOR_ORIGIN_STALE')
    _venue_account_cap(orders,proof['origin'])
    _venue_rules(orders,proof['origin']);_check_intents(proof,reservation,dispatch,recovery,oms)
    original=_risk_with_history(risk_inputs,proof['state']['oms'],risk_inputs['now_ms'])
    checked=screen(orders,**original)
    if checked['status']!='RISK_CHECKS_PASSED' or checked['risk_sha256']!=context['risk_sha256']:
        raise DemoExecutionStopped('DEMO_EXECUTOR_RISK_CHANGED')
    current=screen(orders,**dict(original,now_ms=at))
    if current['status']!='RISK_CHECKS_PASSED':raise DemoExecutionStopped('DEMO_EXECUTOR_RISK_REJECTED')
    if (type(mandate) is not Mandate or not mandate.issued_at<=at<mandate.expires_at or
        require_validation_attestation(sid,attestations) or attestations[sid].attested_at>at):
        raise DemoExecutionStopped('DEMO_EXECUTOR_APPROVAL_EXPIRED')
    requests=[LiveOrderRequest(instrument_id=o['symbol'],side=OrderSide(o['side']),quantity=float(Decimal(o['quantity'])),
        client_order_id=o['newClientOrderId'],order_type='LIMIT',limit_price=float(Decimal(o['price']))) for o in orders]
    decision=evaluate_mandate(requests,{orders[0]['symbol']:float(Decimal(risk_inputs['market']['reference_price']))},mandate,
        venue='testnet',exchange='binance',now=at,current_positions={})
    if decision.tier!=AUTO:raise DemoExecutionStopped('DEMO_EXECUTOR_MANDATE_REJECTED')
    return {'at_ms':at,'context':context,'current_risk':current}


def execute(prepared, *,dispatch,recovery,oms,risk_inputs,mandate,attestations):
    """Internal executor API. No research runner/console network route is added."""
    try:
        _require_state_custody(prepared,dispatch,recovery,oms)
        with _preparation_lease(dispatch):
            proof=_load_proof(prepared)
            matches=[r for r in dispatch.snapshot() if r['record_hash']==prepared['reservation_sha256']]
            if len(matches)!=1:raise DemoExecutionStopped('DEMO_EXECUTOR_RESERVATION_MISSING')
            reservation=matches[0]
            if any(w['reservation_sha256']==reservation['record_hash'] for w in dispatch.wire_snapshot()):
                raise DemoExecutionStopped('DEMO_EXECUTOR_ALREADY_ATTEMPTED_QUERY_ONLY')
            values=dict(dispatch=dispatch,recovery=recovery,oms=oms,risk_inputs=risk_inputs,mandate=mandate,attestations=attestations)
            report={'schema':'DEMO_EXACT_BATCH_EXECUTION_V1','status':'WIRE_ATTEMPTS_RECORDED','orders':[],
                'reservation_sha256':reservation['record_hash'],'economic_authority':False,'strategy_pnl':None}
            clock=proof['origin']['reads'][-1]
            offset=clock['data']['serverTime']-(clock['requested_at_ms']+clock['received_at_ms'])//2
            for order in proof['orders']:
                check=_preflight(proof,reservation,**values);timestamp=check['at_ms']+offset
                claim=dispatch.claim_send(reservation_sha256=reservation['record_hash'],client_order_id=order['newClientOrderId'],
                    request_sha256=binance_demo_exact.request_sha256(order,timestamp),preflight_sha256=_digest(check),now_ms=check['at_ms'])
                def final_check():
                    latest=_preflight(proof,reservation,**values)
                    if not 0<=latest['at_ms']-check['at_ms']<=1000:raise DemoExecutionStopped('DEMO_EXECUTOR_SEND_DELAY')
                try:
                    status,raw=binance_demo_exact._submit_claimed(order,timestamp_ms=timestamp,account_ref=proof['context']['account_ref_sha256'],final_check=final_check)
                    outcome=outcome_for_response(order,status,raw)
                except Exception:
                    outcome=outcome_for_response(order,None,None)
                recorded=dispatch.record_wire_result(claim_sha256=claim['record_hash'],outcome=outcome,now_ms=now_ms())
                report['orders'].append({'client_order_id':order['newClientOrderId'],'claim_sha256':claim['record_hash'],
                                        'result_sha256':recorded['record_hash'],'outcome':outcome})
                if outcome['status']!='ACKNOWLEDGED':
                    report['status']='QUERY_RECOVERY_REQUIRED';break
            return report
    except DemoExecutionStopped:
        raise
    except Exception:
        raise DemoExecutionStopped('DEMO_EXECUTION_STOPPED_QUERY_RESERVED_IDS') from None


def query_reserved(prepared, *,dispatch,recovery,oms):
    """Read original client IDs, even after expiry/restart. Never resubmits.

    Query observations append to the same input journal. Missing orders remain
    unknown; this function does not resolve OMS or synthesize incremental fills.
    """
    try:
        _require_state_custody(prepared,dispatch,recovery,oms)
        with _preparation_lease(dispatch):
            proof=_load_proof(prepared)
            found=[r for r in dispatch.snapshot() if r['record_hash']==prepared['reservation_sha256']]
            if len(found)!=1 or found[0]['payload']['orders']!=proof['orders'] or found[0]['payload']['context']!=proof['context']:
                raise DemoExecutionStopped('DEMO_QUERY_RESERVATION_BINDING')
            # Integrity verification precedes all reads; recovery need not have
            # reached its initial intent after a crash during preparation.
            dispatch.wire_snapshot();recovery.latest_records();oms.orders()
            start=now_ms();deadline=time.monotonic()+60
            report={'schema':'DEMO_RESERVED_QUERY_BATCH_V1','status':'QUERY_RECOVERY_REQUIRED',
                'reservation_sha256':prepared['reservation_sha256'],'started_at_ms':start,'completed_at_ms':start,
                'clock':None,'orders':[],'reason':None,'economic_authority':False,'strategy_pnl':None}
            try:
                status,raw=binance_demo_exact._http_get_once('/fapi/v1/time',b'',{})
                received=now_ms()
                if not 200<=status<300 or len(raw)>65536:raise ValueError()
                clock=load_json_without_duplicate_keys(raw);server=clock['serverTime']
                if type(server) is not int:raise ValueError()
                offset=server-(start+received)//2
                report['clock']={'requested_at_ms':start,'received_at_ms':received,'server_time_ms':server,'accepted':False}
                if not 0<=received-start<=5000 or abs(offset)>2000:raise ValueError()
                report['clock']['accepted']=True
            except Exception:
                report['reason']='QUERY_CLOCK_UNRELIABLE'
            if report['reason'] is None:
                for order in proof['orders']:
                    if time.monotonic()+10>deadline:
                        report['reason']='QUERY_BUDGET_EXHAUSTED';break
                    requested=now_ms()
                    try:
                        status,raw=binance_demo_exact._query_reserved(order,timestamp_ms=requested+offset,account_ref=proof['context']['account_ref_sha256'])
                        outcome=outcome_for_query(order,status,raw)
                    except Exception:
                        outcome=outcome_for_query(order,None,None)
                    received=now_ms()
                    if outcome['status']=='FOUND':
                        original_clock=proof['origin']['reads'][-1]
                        origin_offset=original_clock['data']['serverTime']-(original_clock['requested_at_ms']+original_clock['received_at_ms'])//2
                        origin_uncertainty=(original_clock['received_at_ms']-original_clock['requested_at_ms']+1)//2
                        current_uncertainty=(report['clock']['received_at_ms']-report['clock']['requested_at_ms']+1)//2
                        observed=outcome['order']
                        if (observed['time']<found[0]['payload']['reserved_at_ms']+origin_offset-origin_uncertainty or
                            observed['updateTime']>received+offset+current_uncertainty):
                            outcome.update(status='UNKNOWN',order=None,reason='QUERY_CLOCK_INCONSISTENT')
                    record=dispatch.record_query_result(reservation_sha256=prepared['reservation_sha256'],client_order_id=order['newClientOrderId'],
                        requested_at_ms=requested,outcome=outcome,now_ms=received)
                    report['orders'].append({'client_order_id':order['newClientOrderId'],'outcome':outcome,'record_sha256':record['record_hash']})
                    if outcome['status']!='FOUND':
                        report['reason']='QUERY_UNRESOLVED';break
            report['unqueried_client_order_ids']=[o['newClientOrderId'] for o in proof['orders'][len(report['orders']):]]
            report['completed_at_ms']=now_ms()
            if report['reason'] is None and not report['unqueried_client_order_ids']:report['status']='ORDER_SNAPSHOTS_OBSERVED'
            _retain(Path(prepared['proposal_path']).parent/'queries',report)
            return report
    except DemoExecutionStopped:
        raise
    except Exception:
        raise DemoExecutionStopped('DEMO_QUERY_STOPPED_ORIGINAL_IDS_RETAINED') from None


def _require_receipt_store_custody(event_store,stream):
    registry=demo_authority.load_registry_for_executor()
    for store,attribute,name in ((event_store,'_conn','order-events.sqlite3'),(stream,'db','user-stream.sqlite3')):
        db=getattr(store,attribute,None)
        opened=db.execute('PRAGMA database_list').fetchone()[2]
        path=_STATE_ROOT/name
        if Path(opened)!=path:raise DemoExecutionStopped('DEMO_RECEIPT_STATE_PATH')
        info=path.lstat()
        if info.st_uid!=registry['executor_uid'] or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise DemoExecutionStopped('DEMO_RECEIPT_STATE_CUSTODY')


def reconcile_reserved(prepared,*,dispatch,recovery,oms,event_store,stream,start_ms,end_ms,source_commit):
    """Protected internal receipt->OMS recovery step, with no network or cash.

    The resident executor must own both input journals; a user-writable research
    import cannot resolve this state. No caller-supplied resolution plan is used.
    Actual private data collection and independent deployment remain required.
    """
    from live.execution.demo_oms_reconciliation import prepare_resolution,_project_resolution
    try:
        _require_state_custody(prepared,dispatch,recovery,oms)
        _require_receipt_store_custody(event_store,stream)
        with _preparation_lease(dispatch):
            output=_STATE_ROOT/'receipt-reconciliation'
            plan=prepare_resolution(prepared,dispatch=dispatch,stream=stream,start_ms=start_ms,end_ms=end_ms,
                source_commit=source_commit,output_directory=output,as_of_ms=now_ms())
            if plan['status']!='READY_FOR_OMS_PROJECTION':return plan
            result=_project_resolution(plan,prepared=prepared,dispatch=dispatch,recovery=recovery,oms=oms,
                                       event_store=event_store,now_ms=now_ms())
            _retain(output/'outcomes',result)
            return result
    except DemoExecutionStopped:
        raise
    except Exception:
        raise DemoExecutionStopped('DEMO_RECEIPT_RESOLUTION_STOPPED') from None
