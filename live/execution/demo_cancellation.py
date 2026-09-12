"""Protected original-ID cancellation coordinator, never new risk or OMS fills.

Each independent cancellation grant is consumed before its single-use claims.
Unknown DELETE outcomes always lead to the existing original-ID query/private
receipt recovery. No transport retry, replacement order, signer or cash posting.
"""
from pathlib import Path
import stat
import time

from live.execution import demo_authority,demo_prepared_execution
from live.execution.demo_pre_submit import _preparation_lease,_digest,_retain
from live.execution.demo_wire_protocol import outcome_for_response
from live.execution.demo_cancel_store import validate_outcome
from live.exchange import binance_demo_exact
from services.validation import demo_cancellation as auth
from services.validation.demo_submission import batch_sha256,registry_sha256
from services.research.demo_cancel_status import build_status,save as save_status


class DemoCancellationStopped(RuntimeError):pass


def now_ms():return time.time_ns()//1_000_000


def _require_cancel_custody(prepared,dispatch,recovery,oms,cancel_store):
    demo_prepared_execution._require_state_custody(prepared,dispatch,recovery,oms)
    registry=demo_authority.load_registry_for_executor();path=demo_prepared_execution._STATE_ROOT/'cancellations.sqlite3'
    actual=cancel_store._conn.execute('PRAGMA database_list').fetchone()[2]
    if Path(actual)!=path or cancel_store.path!=path:raise DemoCancellationStopped('DEMO_CANCEL_STATE_PATH')
    info=path.lstat()
    if info.st_uid!=registry['executor_uid'] or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise DemoCancellationStopped('DEMO_CANCEL_STATE_CUSTODY')


def proposal(prepared,*,dispatch,recovery,oms,cancel_store,client_order_ids=None):
    """Build the exact reviewable cancellation scope from protected facts."""
    _require_cancel_custody(prepared,dispatch,recovery,oms,cancel_store)
    proof=demo_prepared_execution._load_proof(prepared);registry=demo_authority.load_registry_for_executor();at=now_ms()
    snapshot=dispatch.evidence_snapshot()
    reserved=next((r for r in snapshot['reservations'] if r['record_hash']==prepared['reservation_sha256']),None)
    if reserved is None or reserved['payload']['orders']!=proof['orders'] or reserved['payload']['context']!=proof['context']:
        raise DemoCancellationStopped('DEMO_CANCEL_ORIGINAL_RESERVATION')
    orders=proof['orders'];all_ids={o['newClientOrderId'] for o in orders}
    selected=[o['newClientOrderId'] for o in orders] if client_order_ids is None else client_order_ids
    if type(selected) is not list or not 1<=len(selected)<=20 or any(type(v) is not str for v in selected) or len(set(selected))!=len(selected) or not set(selected)<=all_ids:
        raise DemoCancellationStopped('DEMO_CANCEL_ORIGINAL_SCOPE')
    commands=[];queries=[]
    for order in orders:
        client=order['newClientOrderId']
        if client not in selected:continue
        wire=[r for r in snapshot['wire'] if r['reservation_sha256']==prepared['reservation_sha256'] and r['client_order_id']==client]
        if not any(r['kind']=='SEND_CLAIM' for r in wire):raise DemoCancellationStopped('DEMO_CANCEL_SEND_CLAIM_MISSING')
        observed=[r for r in wire if r['kind']=='QUERY_RESULT']
        if not observed:raise DemoCancellationStopped('DEMO_CANCEL_QUERY_MISSING')
        query=observed[-1];outcome=query['payload']['outcome']
        if outcome['status']!='FOUND':raise DemoCancellationStopped('DEMO_CANCEL_QUERY_UNKNOWN')
        q=outcome['order']
        if q['status'] not in ('NEW','PARTIALLY_FILLED'):raise DemoCancellationStopped('DEMO_CANCEL_ORDER_ALREADY_TERMINAL')
        if not at-5000<=query['payload']['requested_at_ms']<=query['at_ms']<=at:
            raise DemoCancellationStopped('DEMO_CANCEL_QUERY_STALE')
        identities={r['payload']['outcome']['order']['orderId'] for r in wire if r['kind'] in ('SEND_RESULT','QUERY_RESULT') and
                    r['payload']['outcome']['status'] in ('ACKNOWLEDGED','FOUND')}
        if identities!={q['orderId']}:raise DemoCancellationStopped('DEMO_CANCEL_VENUE_ID_CONFLICT')
        commands.append({'symbol':order['symbol'],'origClientOrderId':client,'orderId':q['orderId']});queries.append(query['record_hash'])
    context={'deployment_sha256':registry['deployment_sha256'],'executor_boot_sha256':demo_authority.executor_boot_sha256(),
        'registry_sha256':registry_sha256(registry),'account_ref_sha256':proof['context']['account_ref_sha256'],
        'reservation_sha256':prepared['reservation_sha256'],'original_batch_sha256':batch_sha256(orders),
        'cancel_batch_sha256':auth.batch_sha256(commands),'query_snapshot_sha256':_digest({'wire':snapshot['wire'],'queries':queries})}
    if context['deployment_sha256']!=proof['context']['deployment_sha256']:
        raise DemoCancellationStopped('DEMO_CANCEL_DEPLOYMENT_MISMATCH')
    return {'schema':'DEMO_CANCEL_PROPOSAL_V1','context':context,'commands':commands,'query_record_hashes':queries,
        'new_order_submission_enabled':False,'economic_authority':False}


def _outcome(order,command,http,raw):
    observed=outcome_for_response(order,http,raw)
    value={'status':'UNKNOWN','http_status':observed['http_status'],'response_sha256':observed['response_sha256'],
        'venue_status':None,'executed_quantity':None}
    q=observed['order']
    if observed['status']=='ACKNOWLEDGED' and q['orderId']==command['orderId'] and {'status','executedQty'}<=set(q):
        value.update(status='ORDER_STATE_OBSERVED',venue_status=q['status'],executed_quantity=q['executedQty'])
    return validate_outcome(value)


def execute(prepared,envelope,*,dispatch,recovery,oms,cancel_store,client_order_ids=None):
    try:
        _require_cancel_custody(prepared,dispatch,recovery,oms,cancel_store)
        kwargs=dict(dispatch=dispatch,recovery=recovery,oms=oms,cancel_store=cancel_store,client_order_ids=client_order_ids)
        with _preparation_lease(dispatch):
            plan=proposal(prepared,**kwargs)
            grant=cancel_store.reserve(envelope,context=plan['context'],commands=plan['commands'],now_ms=now_ms())
            proof=demo_prepared_execution._load_proof(prepared);fixed={o['newClientOrderId']:o for o in proof['orders']}
            def refresh_view():
                view=build_status(cancel_store.snapshot(),observed_at_ms=now_ms())
                save_status(Path(prepared['proposal_path']).parent/'cancellations/views',view)
            refresh_view()
            def final_check():
                current=proposal(prepared,**kwargs)
                if current!=plan:raise DemoCancellationStopped('DEMO_CANCEL_CONTEXT_CHANGED')
                auth.verify(auth.parse_envelope(envelope),registry=demo_authority.load_registry_for_executor(),
                    expected_context=current['context'],commands=current['commands'],now_ms=now_ms())
            report={'schema':'DEMO_CANCELLATION_RESULT_V1','status':'ORIGINAL_ID_QUERY_RECOVERY_REQUIRED',
                'grant_sha256':grant['record_hash'],'orders':[],'unattempted_client_order_ids':[],'reason':None,
                'new_order_submission_enabled':False,'economic_authority':False,'cash_reconciled':False}
            for command in plan['commands']:
                try:final_check()
                except Exception:report['reason']='DEMO_CANCEL_FINAL_CHECK_STOPPED';break
                at=now_ms();client=command['origClientOrderId']
                claim=cancel_store.claim(grant['record_hash'],client,request_sha256=binance_demo_exact.cancellation_sha256(command,at),now_ms=at)
                refresh_view()
                try:
                    http,raw=binance_demo_exact._cancel_claimed(command,timestamp_ms=at,account_ref=plan['context']['account_ref_sha256'],final_check=final_check)
                except Exception:http,raw=None,None
                outcome=_outcome(fixed[client],command,http,raw)
                report['orders'].append({'client_order_id':client,'claim_sha256':claim['record_hash'],'outcome':outcome})
                try:cancel_store.result(claim['record_hash'],outcome=outcome,now_ms=now_ms())
                except Exception:report['reason']='DEMO_CANCEL_RESPONSE_PERSISTENCE_FAILED';break
                try:refresh_view()
                except Exception:report['reason']='DEMO_CANCEL_DISPLAY_PERSISTENCE_FAILED';break
                if outcome['status']=='UNKNOWN':report['reason']='DEMO_CANCEL_RESPONSE_UNKNOWN';break
            attempted={r['client_order_id'] for r in report['orders']}
            report['unattempted_client_order_ids']=[c['origClientOrderId'] for c in plan['commands'] if c['origClientOrderId'] not in attempted]
            _retain(Path(prepared['proposal_path']).parent/'cancellations',{'plan':plan,'result':report})
            return report
    except DemoCancellationStopped:raise
    except Exception:raise DemoCancellationStopped('DEMO_CANCELLATION_STOPPED') from None
