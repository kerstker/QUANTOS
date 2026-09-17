"""Recover a manually flattened Demo account from fixed executor observations.

Only original-ID queries and existing read adapters are used. The protected
caller supplies no receipt file, account, order, risk setting or replacement
wallet. Old gap records and unsuccessful imports remain immutable.
"""
from contextlib import ExitStack
from decimal import Decimal
import hashlib
from pathlib import Path
import time

from live.execution import demo_prepared_execution as execution
from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from live.execution.demo_oms_reconciliation import _project_resolution
from live.execution.demo_pre_submit import _preparation_lease, _retain
from live.execution.demo_reported_recovery import prepare
from live.execution.demo_resident_recovery import discover, _cycle_lease
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_execution_origin import load as load_origin
from services.research.demo_oms_status import build_status, save as save_status
from tools import demo_executor_origin, demo_executor_statement
from tools.demo_executor_stream import fixed_account
from tools.demo_resident_recovery import require_custody, _open, _check_path


def now_ms(): return time.time_ns() // 1000000


def _finish_retained(root,stores):
    """Finish only a retained, revalidated plan after a post-OMS crash."""
    from live.execution.demo_resident_recovery import _records
    directory=root/'receipt-reconciliation/plans'
    _check_path(directory,root.stat().st_uid,directory=True)
    rows=_records(directory,count_limit=1000,byte_limit=64*1024*1024)
    plans=[(p,v) for p,v in rows if v.get('schema')=='DEMO_REPORTED_ACCOUNT_RESOLUTION_V1']
    require(plans,'ACCOUNT_CAPTURE_RETAINED_PLAN_REQUIRED')
    latest=max(v['resolution_at_ms'] for _,v in plans)
    choices=[(p,v) for p,v in plans if v['resolution_at_ms']==latest]
    require(len(choices)==1,'ACCOUNT_CAPTURE_RETAINED_PLAN_AMBIGUOUS')
    path,plan=choices[0];require(_read(path,root,16_000_000)==plan,'ACCOUNT_CAPTURE_RETAINED_PLAN_CHANGED')
    proof_path=root/'evidence'/(plan['proposal_sha256']+'.json');proof=_read(proof_path,root,16_000_000)
    prepared=dict(schema='DEMO_PREPARED_BATCH_V1',status='INTENTS_PERSISTED',proposal_path=str(proof_path),
        reservation_sha256=plan['reservation_sha256'],proposal_sha256=plan['proposal_sha256'],
        orders=proof['orders'],context=proof['context'],submission_enabled=False,economic_authority=False)
    execution._require_state_custody(prepared,stores['dispatch'],stores['recovery'],stores['oms'])
    execution._require_receipt_store_custody(stores['event_store'],stores['stream'])
    with _preparation_lease(stores['dispatch']):
        result=_project_resolution(plan,prepared=prepared,dispatch=stores['dispatch'],recovery=stores['recovery'],
            oms=stores['oms'],event_store=stores['event_store'],now_ms=now_ms())
        _retain(root/'receipt-reconciliation/outcomes',result)
        save_status(root/'receipt-reconciliation/views',build_status(prepared,plan,observed_at_ms=now_ms(),result=result))


def _read(path, root, maximum=12_000_000, *, raw_digest=False):
    path = Path(path)
    require(root in path.parents, 'ACCOUNT_CAPTURE_FIXED_PATH')
    for p in reversed(path.parents):
        if p == root or root in p.parents: _check_path(p, root.stat().st_uid, directory=True)
    _check_path(path, root.stat().st_uid, directory=False)
    require(path.stat().st_size <= maximum, 'ACCOUNT_CAPTURE_BYTE_BUDGET')
    body=path.read_bytes();value = load_json_without_duplicate_keys(body)
    require(path.stem == (hashlib.sha256(body).hexdigest() if raw_digest else _digest(value)), 'ACCOUNT_CAPTURE_FILE_IDENTITY')
    return value


def capture_inputs(root,account,source_commit,*,opening,system_orders,adv_notional,participation_rate,stream,day_replay=False):
    """Complete fixed-account reads; callers supply only their retained originals."""
    captured=demo_executor_origin.run(symbol='BTCUSDC',source_commit=source_commit)
    require(captured['status']=='CAPTURED','ACCOUNT_CAPTURE_CLOSING_UNAVAILABLE')
    closing=_read(captured['path'],root,raw_digest=True)
    require(closing['source_sha256']==demo_executor_origin.source_hash() and closing['account_ref_sha256']==account,
            'ACCOUNT_CAPTURE_CLOSING_BINDING')
    load_origin(Path(captured['path']))
    start,end=opening['observed_at_ms']-6000,closing['completed_at_ms']+6000
    require(type(day_replay) is bool and 0<end-start<(7 if day_replay else 1)*86400000,'ACCOUNT_CAPTURE_WINDOW_BUDGET')
    if now_ms()<=end:time.sleep((end-now_ms()+1)/1000)
    statements=[]
    for index in range(2):
        if index:time.sleep(max(0,(statements[-1]['completed_at_ms']+10001-now_ms())/1000))
        result=demo_executor_statement.run(symbol='BTCUSDC',start_ms=start,end_ms=end,source_commit=source_commit,
            **({'day_replay':True} if day_replay else {}))
        require(result['status']=='CAPTURED','ACCOUNT_CAPTURE_STATEMENT_UNAVAILABLE')
        wrapper=demo_executor_statement.validate(_read(result['path'],root))
        require(wrapper['schema']==(demo_executor_statement.DAY_SCHEMA if day_replay else demo_executor_statement.SCHEMA) and
            wrapper['account_ref_sha256']==account and wrapper['source_sha256']==demo_executor_statement.source_hash() and
            wrapper['statement']['source_sha256']==demo_executor_statement.original.source_hash(),'ACCOUNT_CAPTURE_STATEMENT_BINDING')
        statements.append(wrapper['statement'])
    trades=statements[0]['trades'];require(1<=len(trades)<=32,'ACCOUNT_CAPTURE_NATIVE_WINDOW_BUDGET')
    windows=[dict(stream.export_order_messages(start_ms=t['time']-5000,end_ms=t['time']+5000),
                  start_ms=t['time']-5000,end_ms=t['time']+5000) for t in trades]
    return dict(opening=opening,closing=closing,statements=statements,windows=windows,system_orders=system_orders,
                adv_notional=adv_notional,participation_rate=participation_rate)


def run(*, source_commit):
    root = require_custody(); fixed_root, account = fixed_account()
    require(root == fixed_root, 'ACCOUNT_CAPTURE_FIXED_ROOT')
    with _cycle_lease(root/'resident-recovery'), ExitStack() as stack:
        stores = _open(stack, root)
        discovered = discover(dispatch=stores['dispatch'], recovery=stores['recovery'], oms=stores['oms'],
                              evidence_directory=root/'evidence')
        if discovered['reservations'] == 1 and not discovered['pending']:
            from live.execution.demo_position_context import load_context
            from live.execution.demo_pre_submit import _state
            _finish_retained(root,stores)
            context = load_context(_state(stores['dispatch'],stores['recovery'],stores['oms']),
                symbol='BTCUSDC',account_ref=account,as_of_ms=now_ms())
            require(context.get('reported_account_prefix') and context['quantity']=='0', 'ACCOUNT_CAPTURE_PRIOR_RECOVERY_REQUIRED')
            prefix = context['reported_account_prefix']
            return dict(status='MANUAL_ACCOUNT_RECONCILED',plan_sha256=prefix['plan_sha256'],cash_reconciled=True,
                cash_status='MATCHED_REPORTED_ACCOUNT',final_quantity='0',fill_count=prefix['fill_count'],
                automatic_cycle_completed=False,stream_coverage_repaired=False,orders_submitted=False,economic_authority=False)
        # First incident only. No earlier unresolved account is silently cut
        # away and no caller picks a favorable subset of original reservations.
        require(discovered['reservations'] == 1 and len(discovered['pending']) == 1,
                'ACCOUNT_CAPTURE_SINGLE_ORIGINAL_INCIDENT')
        prepared = discovered['pending'][0]['prepared']
        require(prepared is not None, 'ACCOUNT_CAPTURE_ORIGINAL_MISSING')
        execution._require_state_custody(prepared, stores['dispatch'], stores['recovery'], stores['oms'])
        execution._require_receipt_store_custody(stores['event_store'], stores['stream'])
        proof = execution._load_proof(prepared)
        require(proof['context']['account_ref_sha256'] == account and
                all(o['symbol'] == 'BTCUSDC' for o in proof['orders']), 'ACCOUNT_CAPTURE_ORIGINAL_SCOPE')
        risk = proof['risk']['input_snapshot']
        inputs=capture_inputs(root,account,source_commit,opening=proof['origin'],system_orders=proof['orders'],
            adv_notional=str(Decimal(str(risk['market']['daily_volume_base']))*Decimal(str(risk['market']['reference_price']))),
            participation_rate=str(risk['native_limits']['max_participation_rate']),stream=stores['stream'])
        # Retain the capture before projection, including failures and all GAPs.
        output = root/'receipt-reconciliation'
        _retain(output/'account-captures', dict(schema='DEMO_PROTECTED_ACCOUNT_CAPTURE_V1',
            source_commit=source_commit, account_ref_sha256=account, inputs=inputs,
            reservation_sha256=prepared['reservation_sha256'], observed_at_ms=now_ms(),
            orders_submitted=False, economic_authority=False))
        execution.query_reserved(prepared, dispatch=stores['dispatch'], recovery=stores['recovery'], oms=stores['oms'])
        with _preparation_lease(stores['dispatch']):
            plan = prepare(prepared, dispatch=stores['dispatch'], inputs=inputs, as_of_ms=now_ms())
            _retain(output/'plans', plan)
            result = _project_resolution(plan, prepared=prepared, dispatch=stores['dispatch'],
                recovery=stores['recovery'], oms=stores['oms'], event_store=stores['event_store'], now_ms=now_ms())
            _retain(output/'outcomes', result)
            save_status(output/'views',build_status(prepared,plan,observed_at_ms=now_ms(),result=result))
        return {'status': 'MANUAL_ACCOUNT_RECONCILED', 'plan_sha256': _digest(plan),
                'cash_reconciled': True, 'cash_status': 'MATCHED_REPORTED_ACCOUNT',
                'final_quantity': plan['account_report']['final_quantity'],
                'fill_count': plan['account_report']['fill_count'],
                'automatic_cycle_completed': False, 'stream_coverage_repaired': False,
                'orders_submitted': False, 'economic_authority': False}
