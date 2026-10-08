"""Fixed Demo executor operations for the finite functional cycle.

Cash capture uses the original credential boundary; recovery uses original IDs.
Only the independently signed original task path can submit an order.
"""
import argparse
import hashlib
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal, ROUND_FLOOR
import json
import os
from pathlib import Path
import re
import sys
import time

from live.execution.demo_issuer_policy import require
from live.execution.demo_pre_submit import _state, _retain, _venue_rules
from live.execution.demo_position_context import load_context, validate_origin
from live.execution.demo_resident_recovery import RecoveryLoop
from services.execution.perpetual_ledger_v2 import canonical_decimal, ledger_decimal_context
from services.research.demo_cash_chain import project_cash_chain
from services.research.demo_cash_chain_status import build_status as cash_status, save as save_cash_status
from services.research.demo_execution_journal import DemoExecutionJournal
from services.research.demo_execution_origin import load as load_origin
from services.research.demo_cash_window import save_capture, load_capture, validate as validate_cash, GUARD_MS
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_resident_status import build_status, save as save_status
from live.execution import demo_authority
from live.exchange import demo_network, binance_demo_exact
from tools import binance_demo_cash_window, demo_executor_origin, demo_operator_setup
from tools.demo_resident_recovery import require_custody, _open, _check_path
from tools.demo_executor_stream import fixed_account
from tools.demo_issuer import private_pipe, verify_source
from tools.demo_execution_session import read_frame
from tools.cache_io import atomic_write_json


def now_ms():return time.time_ns()//1000000


def area_for(root,cycle):
    require(type(cycle) is str and re.fullmatch('[a-f0-9]{64}',cycle),'FUNCTIONAL_CYCLE_ID')
    area=root/'resident-recovery'/'functional-cycles'/cycle
    for path in (area.parent,area):
        path.mkdir(mode=0o700,exist_ok=True);_check_path(path,root.stat().st_uid,directory=True)
    return area


def position(root,account,*,strategy_id=None,symbol='BTCUSDC'):
    with ExitStack() as stack:
        stores=_open(stack,root)
        state=_state(stores['dispatch'],stores['recovery'],stores['oms'])
        context=load_context(state,symbol=symbol,account_ref=account,as_of_ms=now_ms())
        return context,len(stores['oms'].fills(strategy_id=strategy_id))


def _partition_stop(path,uid,*,as_of_ms):
    _check_path(path,uid,directory=False)
    image=demo_operator_setup._stop_image(path=path)
    try:snapshot=demo_operator_setup.verified_stop_snapshot(image,now_ms=as_of_ms)
    except Exception:raise ValueError('PARTITION_STOP_SOURCE_INVALID') from None
    _check_path(path,uid,directory=False)
    return image,snapshot


def review_first_gold(root,account,source_commit,payload):
    return _review_first_gold(root,account,source_commit,payload,activation=False)


def review_first_gold_activation(root,account,source_commit,payload):
    return _review_first_gold(root,account,source_commit,payload,activation=True)


def review_gold_budget_scope(root,account,source_commit,payload):
    return _review_first_gold(root,account,source_commit,payload,activation=False,budget=True)


def _review_first_gold(root,account,source_commit,payload,*,activation,budget=False):
    from live.execution.demo_market_partition_runtime import first_gold_enrollment_review,first_gold_activation_review,gold_budget_scope_review
    from live.execution.demo_pre_submit import _preparation_lease
    key='gold_budget_scope_origin_sha256' if budget else 'first_gold_activation_origin_sha256' if activation else 'first_gold_enrollment_origin_sha256'
    fields={key,'expected_new_registry'} if activation else {key}
    require(type(payload) is dict and set(payload)==fields,
        'FIRST_GOLD_WORKER_FIELDS')
    identity=payload[key]
    require(type(identity) is str and re.fullmatch('[a-f0-9]{64}',identity),'FIRST_GOLD_WORKER_ORIGIN')
    registry=demo_authority.load_registry_for_executor();uid=registry['executor_uid']
    directory=root/'execution-origins'/'XAUUSDT';path=directory/(identity+'.json')
    for parent in (root,directory.parent,directory):_check_path(parent,uid,directory=True)
    _check_path(path,uid,directory=False);origin=load_origin(path)
    require(origin['symbol']=='XAUUSDT' and origin['account_ref_sha256']==account,'FIRST_GOLD_WORKER_ACCOUNT')
    with ExitStack() as stack:
        stores=_open(stack,root);stack.enter_context(_preparation_lease(stores['dispatch']))
        state=_state(stores['dispatch'],stores['recovery'],stores['oms']);at=now_ms()
        stop,snapshot=_partition_stop(root/'kill-switch.sqlite3',uid,as_of_ms=at)
        report=(gold_budget_scope_review(state=state,origin=origin,as_of_ms=at) if budget else first_gold_activation_review(state=state,origin=origin,
            expected_registry=payload['expected_new_registry'],as_of_ms=at) if activation
            else first_gold_enrollment_review(state=state,origin=origin,as_of_ms=at))
        require(report['source_commit']==source_commit,'FIRST_GOLD_WORKER_SOURCE')
        current_stop,current_snapshot=_partition_stop(root/'kill-switch.sqlite3',uid,as_of_ms=at)
        _check_path(path,uid,directory=False)
        require(state==_state(stores['dispatch'],stores['recovery'],stores['oms'])
            and current_stop==stop and current_snapshot==snapshot
            and demo_authority.load_registry_for_executor()==registry
            and load_origin(path)==origin,'FIRST_GOLD_WORKER_INPUTS_CHANGED')
    result=dict(status='GOLD_BUDGET_SCOPE_REVIEW' if budget else 'FIRST_GOLD_ACTIVATION_REVIEW' if activation else 'FIRST_GOLD_ENROLLMENT_REVIEW',review=report,
        submission_enabled=False,economic_authority=False)
    require(len(demo_operator_setup.canonical(result))<=65000,'FIRST_GOLD_WORKER_RESPONSE_BUDGET')
    return result


def prepare_market_partition(root,account,source_commit,payload):
    """Original executor only; fixed retained hashes, no caller cash or paths."""
    from live.execution.demo_market_partition import prepare,revalidate_pre_activation
    from live.execution.demo_pre_submit import _preparation_lease
    require(type(payload) is dict and set(payload) in ({'market_partition_origins'},
        {'market_partition_origins','retained_market_partition'}),'PARTITION_WORKER_FIELDS')
    hashes=payload['market_partition_origins']
    require(type(hashes) is dict and set(hashes)=={'BTCUSDC','XAUUSDT'}
        and all(type(value) is str and re.fullmatch('[a-f0-9]{64}',value) for value in hashes.values()),
        'PARTITION_WORKER_ORIGINS')
    registry=demo_authority.load_registry_for_executor();uid=registry['executor_uid']
    def read_origin(path):
        try:return load_origin(path)
        except OSError:raise ValueError('PARTITION_WORKER_ORIGIN_UNAVAILABLE') from None
    origins={};paths={}
    for symbol,value in hashes.items():
        directory=root/'execution-origins'/symbol;path=directory/(value+'.json')
        for parent in (root,directory.parent,directory):_check_path(parent,uid,directory=True)
        _check_path(path,uid,directory=False)
        origins[symbol]=read_origin(path);paths[symbol]=path
        require(origins[symbol]['symbol']==symbol and origins[symbol]['account_ref_sha256']==account,
                'PARTITION_WORKER_ACCOUNT')
    stop_path=root/'kill-switch.sqlite3'
    with ExitStack() as stack:
        stores=_open(stack,root);stack.enter_context(_preparation_lease(stores['dispatch']))
        state=_state(stores['dispatch'],stores['recovery'],stores['oms']);at=now_ms()
        stop,snapshot=_partition_stop(stop_path,uid,as_of_ms=at)
        args=dict(state=state,legacy_origin=origins['BTCUSDC'],gold_origin=origins['XAUUSDT'],
            registry=registry,source_commit=source_commit,kill_switch_sha256=hashlib.sha256(stop).hexdigest(),now_ms=at)
        if 'retained_market_partition' in payload:
            report=revalidate_pre_activation(candidate=payload['retained_market_partition'],**args)
            result=dict(status='PARTITION_REVALIDATED',revalidation=report,submission_enabled=False,economic_authority=False)
        else:
            candidate=prepare(**args)
            result=dict(status='PARTITION_CANDIDATE',candidate=candidate,submission_enabled=False,economic_authority=False)
        current_stop,current_snapshot=_partition_stop(stop_path,uid,as_of_ms=at)
        require(_state(stores['dispatch'],stores['recovery'],stores['oms'])==state
            and current_stop==stop and current_snapshot==snapshot
            and demo_authority.load_registry_for_executor()==registry
            and all(read_origin(paths[symbol])==origins[symbol] for symbol in hashes),
            'PARTITION_WORKER_INPUTS_CHANGED')
    # Leave room for the existing source/cycle/time envelope within the pipe cap.
    require(len(demo_operator_setup.canonical(result))<=65000,'PARTITION_WORKER_RESPONSE_BUDGET')
    return result


def settle(root,account,source_commit,*,strategy_id=None,symbol='BTCUSDC'):
    deadline=time.monotonic()+45
    with ExitStack() as stack:
        stores=_open(stack,root)
        loop=RecoveryLoop(**stores,evidence_directory=root/'evidence',output_directory=root/'resident-recovery',
            source_commit=source_commit)
        def publish(report):
            save_status(root/'resident-recovery/views',build_status(report,
                executor_boot_sha256=demo_authority.executor_boot_sha256(),source_commit=source_commit))
        while time.monotonic()<deadline:
            report=loop.tick(publish=publish)
            resolution=report.get('resolution') or {}
            if 'STREAM_GAPS_IN_WINDOW' in resolution.get('issues',[]):
                return {'status':'EVIDENCE_REPAIR_REQUIRED','quantity':None,'fill_count':None,
                    'reason':'STREAM_GAPS_IN_WINDOW'}
            try:context,count=position(root,account,strategy_id=strategy_id,symbol=symbol)
            except ValueError:pass
            else:
                return {'status':'SETTLED','quantity':context['quantity'],'fill_count':count,
                    'ledger_state_hash':context['ledger_state_hash']}
            time.sleep(1)
    return {'status':'UNRESOLVED','quantity':None,'fill_count':None}


def capture_cash(root,area,account,source_commit):
    opener=demo_network.opener()
    def get(path,*,params,headers):
        return binance_demo_cash_window.get_response(path,params=params,headers=headers,opener=opener)
    def opened(report):
        require(report['account_ref_sha256']==account and
            [r['kind'] for r in report['reads']]==['clock_before','wallet_before'],'FUNCTIONAL_CASH_ORIGIN')
        _retain(area/'cash-prefix',report)
        marker={'schema':'DEMO_FUNCTIONAL_CASH_OPENING_V1','account_ref_sha256':account,
            'source_commit':source_commit,'opened_at_ms':report['reads'][-1]['received_at_ms'],
            'capture_prefix_sha256':demo_operator_setup.digest(report)}
        _retain(area/'markers',marker);atomic_write_json(area/'cash-opening.json',marker)
    report=binance_demo_cash_window.collect(credential_file=None,observe_seconds=600,operator_read=True,
        credential_reader=lambda _:binance_demo_exact._credentials(account),get=get,window_opened=opened,
        observe_wait=lambda maximum:wait_cash_close(area,account,source_commit,maximum))
    require(report['account_ref_sha256'] in (None,account),'FUNCTIONAL_CASH_ACCOUNT')
    save_capture(area/'cash',report)
    return {'status':report['status'],'capture_sha256':demo_operator_setup.digest(report)}


def opening_prefix(root,area,account,source_commit):
    """Load the actual pre-order observation; never replace it with today's wallet."""
    for path in (area/'cash-opening.json',):_check_path(path,root.stat().st_uid,directory=False)
    marker=load_json_without_duplicate_keys((area/'cash-opening.json').read_bytes())
    require(marker['schema']=='DEMO_FUNCTIONAL_CASH_OPENING_V1' and marker['account_ref_sha256']==account and
        marker['source_commit']==source_commit,'FUNCTIONAL_RESUME_OPENING_BINDING')
    identity=marker['capture_prefix_sha256']
    require(type(identity) is str and re.fullmatch('[a-f0-9]{64}',identity),'FUNCTIONAL_RESUME_PREFIX_ID')
    directory=area/'cash-prefix';_check_path(directory,root.stat().st_uid,directory=True)
    path=directory/(identity+'.json');_check_path(path,root.stat().st_uid,directory=False)
    require(path.stat().st_size<=65536,'FUNCTIONAL_RESUME_PREFIX_SIZE')
    prefix=validate_cash(load_json_without_duplicate_keys(path.read_bytes()))
    require(demo_operator_setup.digest(prefix)==identity and prefix['status']=='OPENING_CAPTURED' and
        prefix['account_ref_sha256']==account and prefix['source_sha256']==binance_demo_cash_window.source_hash() and
        prefix['completed_at_ms']==marker['opened_at_ms'] and
        0<=now_ms()-prefix['completed_at_ms']<=3000_000,'FUNCTIONAL_RESUME_CASH_WINDOW')
    return prefix


def resume_cash(root,area,account,source_commit):
    prefix=opening_prefix(root,area,account,source_commit)
    opener=demo_network.opener()
    report=binance_demo_cash_window.collect(credential_file=None,observe_seconds=30,operator_read=True,
        credential_reader=lambda _:binance_demo_exact._credentials(account),opening_prefix=prefix,
        get=lambda path,**kwargs:binance_demo_cash_window.get_response(path,opener=opener,**kwargs))
    require(report['account_ref_sha256']==account,'FUNCTIONAL_CASH_ACCOUNT')
    save_capture(area/'cash',report)
    return {'status':report['status'],'capture_sha256':demo_operator_setup.digest(report)}


def wait_cash_close(area,account,source_commit,maximum):
    start=time.monotonic();path=area/'cash-close.json'
    while time.monotonic()-start<maximum:
        if path.exists() or path.is_symlink():
            _check_path(path,area.stat().st_uid,directory=False)
            require(path.stat().st_size<=4096,'FUNCTIONAL_CASH_CLOSE_SIZE')
            value=load_json_without_duplicate_keys(path.read_bytes())
            require(value=={'schema':'DEMO_FUNCTIONAL_CASH_CLOSE_V1','account_ref_sha256':account,
                'source_commit':source_commit,'cycle_sha256':area.name},'FUNCTIONAL_CASH_CLOSE_BINDING')
            # Retain a real post-execution wallet, with the existing collector's
            # minimum window and settlement guard. No fixed 300-second wait.
            time.sleep(max(GUARD_MS/1000+1,30-(time.monotonic()-start)))
            return min(maximum,int(time.monotonic()-start))
        time.sleep(.2)
    return maximum


def close_cash(area,account,source_commit):
    value={'schema':'DEMO_FUNCTIONAL_CASH_CLOSE_V1','account_ref_sha256':account,
        'source_commit':source_commit,'cycle_sha256':area.name}
    _retain(area/'markers',value);atomic_write_json(area/'cash-close.json',value)
    return {'status':'CLOSE_REQUESTED'}


@ledger_decimal_context()
def reduce_task(root,account,source_commit,payload):
    template=payload['template'];spec=demo_operator_setup.spec_check(payload['spec']);index=payload['index']
    require(type(index) is int and 1<=index<=2 and template['source_commit']==source_commit and
        spec['account_ref_sha256']==account and len(template['orders'])==1 and
        template['orders'][0]['symbol']==spec['symbol'],'FUNCTIONAL_REDUCTION_SCOPE')
    captured=demo_executor_origin.run(symbol=spec['symbol'],source_commit=source_commit)
    require(captured['status']=='CAPTURED','FUNCTIONAL_REDUCTION_ORIGIN')
    origin=load_origin(Path(captured['path']))
    context,_=position(root,account,symbol=spec['symbol'])
    validate_origin(origin,context,as_of_ms=now_ms())
    quantity=Decimal(context['quantity']);require(0<quantity<=Decimal(spec['max_quantity']),'FUNCTIONAL_CONFIRMED_LONG_REQUIRED')
    data={r['kind']:r['data'] for r in origin['reads']}
    tick=next(f for f in data['rules']['terms']['filters'] if f['filterType']=='PRICE_FILTER')
    minimum,step=Decimal(tick['minPrice']),Decimal(tick['tickSize'])
    target=Decimal(data['book']['bidPrice'])*(1-Decimal(spec['ioc_price_offset_bps'])/10000)
    price=minimum+((target-minimum)/step).to_integral_value(rounding=ROUND_FLOOR)*step
    require(price>0 and quantity*max(price,Decimal(data['mark']['markPrice']))<=Decimal(spec['max_order_notional']),
        'FUNCTIONAL_REDUCTION_BUDGET')
    task=deepcopy(template)
    task['orders']=[dict(template['orders'][0],side='SELL',reduceOnly=True,quantity=canonical_decimal(quantity),
        price=canonical_decimal(price),newClientOrderId=template['orders'][0]['newClientOrderId'][:-4]+'close'+str(index))]
    _venue_rules(task['orders'],origin)
    return {'status':'REDUCTION_TASK_PREPARED','task':task,'context_sha256':demo_operator_setup.digest(context)}


def reconcile(root,area,account):
    capture=load_capture(area/'cash');context,count=position(root,account,strategy_id=cycle_strategy(root,area),
        symbol=cycle_symbol(root,area))
    require(capture['account_ref_sha256']==account,'FUNCTIONAL_CASH_ACCOUNT')
    with ExitStack() as stack:
        journals=[];account_prefix=None
        for source in context['sources']:
            if source['manifest_hash'] is None:
                from tools.demo_reported_recovery import _read
                plan=_read(root/'receipt-reconciliation/plans'/(source['plan_sha256']+'.json'),root,maximum=16_000_000)
                require(account_prefix is None and plan['schema']=='DEMO_REPORTED_ACCOUNT_RESOLUTION_V1',
                        'FUNCTIONAL_ACCOUNT_PREFIX')
                account_prefix=plan['account_inputs'];continue
            path=root/'receipt-reconciliation/receipts/journals'/(source['manifest_hash']+'.sqlite3')
            _check_path(path,root.stat().st_uid,directory=False)
            journals.append(stack.enter_context(DemoExecutionJournal(path,read_only=True)))
        report=project_cash_chain(journals,capture,account_prefix=account_prefix)
    _retain(area/'cash-chain-reports',report)
    save_cash_status(root/'resident-recovery/functional-cash/views',cash_status(report))
    return {'status':report['status'],'issues':report['issues'],'final_quantity':context['quantity'],
        'fill_count':count,'report_sha256':demo_operator_setup.digest(report),
        'wallet_residual':report['wallet_residual'],'economic_authority':False}


def export_feedback(root,area,account,source_commit,payload):
    """Rebuild one completed gold cycle from protected originals, without GETs."""
    from live.execution.demo_pre_submit import _preparation_lease
    from live.execution.demo_resident_recovery import _cycle_lease
    from live.execution.demo_research_feedback import build_roundtrip_feedback
    from services.research.demo_cash_window import assess
    from tools.demo_reported_recovery import _read
    require(require_custody()==root and fixed_account()==(root,account),'FEEDBACK_FIXED_ACCOUNT')
    require(type(payload) is dict and set(payload)=={'research_identity'},'FEEDBACK_IDENTITY_INPUT')
    require(area==root/'resident-recovery/functional-cycles'/area.name and
        re.fullmatch('[a-f0-9]{64}',area.name),'FEEDBACK_CYCLE_PATH')
    for directory in (root,root/'resident-recovery',area.parent,area,area/'cash'):
        _check_path(directory,root.stat().st_uid,directory=True)
    scope=cycle_scope(root,area)
    require(scope['symbol']=='XAUUSDT' and scope['strategy_id'] is not None,'FEEDBACK_GOLD_SCOPE')
    with _cycle_lease(root/'resident-recovery'),ExitStack() as stack:
        stores=_open(stack,root)
        stack.enter_context(_preparation_lease(stores['dispatch']))
        at=now_ms();state=_state(stores['dispatch'],stores['recovery'],stores['oms'])
        context=load_context(state,symbol=scope['symbol'],account_ref=account,as_of_ms=at)
        require(context['quantity']=='0','FEEDBACK_COMPLETED_POSITION_REQUIRED')
        capture=load_capture(area/'cash');cash=assess(capture)
        require(capture['account_ref_sha256']==account and cash['status']=='MATCHED_REPORTED_WINDOW',
            'FEEDBACK_CASH_WINDOW_REQUIRED')
        stream_before=stores['stream'].verify()
        batches=[]
        from live.execution.demo_market_partition_runtime import context_reservations
        for reservation,source in context_reservations(state=state,context=context,as_of_ms=at):
            require(source['reservation_sha256']==reservation['record_hash'],'FEEDBACK_RESERVATION_BINDING')
            plan=_read(root/'receipt-reconciliation/plans'/(source['plan_sha256']+'.json'),root,16_000_000)
            path=root/'evidence'/(plan['proposal_sha256']+'.json')
            proof=_read(path,root,16_000_000)
            require(proof['orders']==reservation['payload']['orders'] and
                proof['context']==reservation['payload']['context'],'FEEDBACK_ORIGINAL_BINDING')
            if proof['strategy_id']!=scope['strategy_id']:continue
            require(source['manifest_hash'] is not None,'FEEDBACK_NATIVE_CYCLE_REQUIRED')
            prepared=dict(schema='DEMO_PREPARED_BATCH_V1',status='INTENTS_PERSISTED',
                reservation_sha256=reservation['record_hash'],proposal_sha256=plan['proposal_sha256'],
                proposal_path=str(path),orders=proof['orders'],context=proof['context'],
                submission_enabled=False,economic_authority=False)
            batches.append(dict(prepared=prepared,start_ms=proof['origin']['completed_at_ms']+1,end_ms=0))
        require(2<=len(batches)<=3,'FEEDBACK_FUNCTIONAL_BATCH_COUNT')
        for index,item in enumerate(batches):
            item['end_ms']=batches[index+1]['start_ms'] if index+1<len(batches) else cash['bounds']['query_end_ms']
            require(2<=item['end_ms']-item['start_ms']<=150000,'FEEDBACK_RECEIPT_WINDOW_BUDGET')
        for directory in (area/'feedback-reconciliation',area/'research-feedback'):
            directory.mkdir(mode=0o700,exist_ok=True)
            _check_path(directory,root.stat().st_uid,directory=True)
        value=build_roundtrip_feedback(batches,dispatch=stores['dispatch'],stream=stores['stream'],capture=capture,
            source_commit=source_commit,output_directory=area/'feedback-reconciliation',as_of_ms=at,
            research_identity=payload['research_identity'])
        require(value['candidate_attribution']=='CONTENT_BOUND_TO_SIGNED_STRATEGY','FEEDBACK_TRIAL_BINDING_REQUIRED')
        require(_state(stores['dispatch'],stores['recovery'],stores['oms'])==state and
            stores['stream'].verify()==stream_before and load_capture(area/'cash')==capture and
            cycle_scope(root,area)==scope,'FEEDBACK_ORIGINALS_CHANGED')
        path=_retain(area/'research-feedback',value)
        return dict(status='ROUNDTRIP_FEEDBACK_RETAINED',feedback_sha256=value['result_hash'],
            artifact_sha256=demo_operator_setup.digest(value),path=str(path),economic_authority=False,
            submission_enabled=False)


def scope_record(cycle,sid,*,symbol='BTCUSDC'):
    require(type(cycle) is str and re.fullmatch('[a-f0-9]{64}',cycle) and
        symbol in ('BTCUSDC','XAUUSDT') and type(sid) is str and
        (re.fullmatch('demo-functional-[a-f0-9]{16}',sid) or
         symbol=='XAUUSDT' and re.fullmatch('gold-research-[a-f0-9]{64}',sid)), 'FUNCTIONAL_SCOPE_BINDING')
    value=dict(schema='DEMO_CYCLE_SCOPE_V1',cycle_sha256=cycle,strategy_id=sid)
    if symbol=='XAUUSDT':value.update(schema='DEMO_GOLD_CYCLE_SCOPE_V1',symbol=symbol)
    return value


def cycle_scope(root,area):
    path=area/'cycle-scope.json'
    if not path.exists() and not path.is_symlink():return {'strategy_id':None,'symbol':'BTCUSDC'}
    _check_path(path,root.stat().st_uid,directory=False)
    require(path.stat().st_size<=1024,'FUNCTIONAL_SCOPE_SIZE')
    value=load_json_without_duplicate_keys(path.read_bytes())
    symbol='XAUUSDT' if value.get('schema')=='DEMO_GOLD_CYCLE_SCOPE_V1' else 'BTCUSDC'
    require(value==scope_record(area.name,value.get('strategy_id'),symbol=symbol),'FUNCTIONAL_SCOPE_BINDING')
    return dict(value,symbol=symbol)


def cycle_strategy(root,area):return cycle_scope(root,area)['strategy_id']


def cycle_symbol(root,area):return cycle_scope(root,area)['symbol']


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--cycle-sha256',required=True)
    parser.add_argument('--operation',choices=('cash','close-cash','settle','reduce','reconcile','resume-check','resume-cash','account-recover','account-cash','context','day-checkpoint','feedback'),required=True)
    args=parser.parse_args(argv);os.umask(0o077)
    try:
        verify_source(args.source_commit);root=require_custody();_,account=fixed_account()
        private_pipe(sys.stdin.fileno());private_pipe(sys.stdout.fileno())
        raw=read_frame(sys.stdin.fileno(),maximum=65536,deadline=time.monotonic()+10)
        payload=load_json_without_duplicate_keys(raw);require(type(payload) is dict,'FUNCTIONAL_INPUT')
        area=area_for(root,args.cycle_sha256)
        if args.operation=='feedback':
            result=export_feedback(root,area,account,args.source_commit,payload)
        elif args.operation=='day-checkpoint':
            require(not payload,'DAY_CAPTURE_NO_CALLER_INPUTS')
            from tools.demo_day_checkpoint import run
            result=run(source_commit=args.source_commit,symbol=cycle_symbol(root,area))
        elif args.operation=='account-cash':
            from tools.demo_account_cash import run
            result=run(root,area,account,args.source_commit,strategy_id=cycle_strategy(root,area),symbol=cycle_symbol(root,area))
        elif args.operation=='context':
            if payload:
                if set(payload)=={'gold_budget_scope_origin_sha256'}:
                    if (area/'cycle-scope.json').exists() or (area/'cycle-scope.json').is_symlink():
                        require(cycle_symbol(root,area)=='XAUUSDT','BUDGET_WORKER_GOLD_SCOPE')
                    result=review_gold_budget_scope(root,account,args.source_commit,payload)
                elif set(payload)=={'first_gold_activation_origin_sha256','expected_new_registry'}:
                    result=review_first_gold_activation(root,account,args.source_commit,payload)
                else:
                    require(cycle_symbol(root,area)=='BTCUSDC','PARTITION_WORKER_LEGACY_SCOPE')
                    if set(payload)=={'first_gold_enrollment_origin_sha256'}:
                        result=review_first_gold(root,account,args.source_commit,payload)
                    else:result=prepare_market_partition(root,account,args.source_commit,payload)
            else:
                context,_=position(root,account,symbol=cycle_symbol(root,area))
                result={'status':'CANONICAL_CONTEXT','context':context}
        elif args.operation=='account-recover':
            from tools.demo_reported_recovery import run
            result=run(source_commit=args.source_commit,symbol=cycle_symbol(root,area))
        elif args.operation=='cash':
            if payload.get('strategy_id') is not None:
                from tools.demo_operating_bundle import write_new
                sid=payload['strategy_id']
                scope=scope_record(area.name,sid,symbol=payload.get('symbol','BTCUSDC'))
                write_new(area/'cycle-scope.json',demo_operator_setup.canonical(scope),uid=root.stat().st_uid,gid=root.stat().st_gid)
            result=capture_cash(root,area,account,args.source_commit)
        elif args.operation=='resume-check':
            prefix=opening_prefix(root,area,account,args.source_commit)
            result={'status':'ORIGINAL_OPENING_RETAINED','capture_prefix_sha256':demo_operator_setup.digest(prefix)}
        elif args.operation=='resume-cash':result=resume_cash(root,area,account,args.source_commit)
        elif args.operation=='close-cash':result=close_cash(area,account,args.source_commit)
        elif args.operation=='settle':result=settle(root,account,args.source_commit,
            strategy_id=cycle_strategy(root,area),symbol=cycle_symbol(root,area))
        elif args.operation=='reduce':result=reduce_task(root,account,args.source_commit,payload)
        else:result=reconcile(root,area,account)
        result.update(source_commit=args.source_commit,cycle_sha256=args.cycle_sha256,observed_at_ms=now_ms())
        _retain(area/'results',result)
        print(json.dumps(result,sort_keys=True,separators=(',',':')),flush=True);return 0
    except Exception:
        print(json.dumps({'status':'UNRESOLVED','operation':args.operation,'cycle_sha256':args.cycle_sha256,
            'source_commit':args.source_commit,'economic_authority':False}),flush=True);return 3


if __name__=='__main__':raise SystemExit(main())
