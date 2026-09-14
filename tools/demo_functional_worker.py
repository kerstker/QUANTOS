"""Fixed Demo executor operations for the finite functional cycle.

Cash capture uses the original credential boundary; recovery uses original IDs.
Only the independently signed original task path can submit an order.
"""
import argparse
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


def position(root,account,*,strategy_id=None):
    with ExitStack() as stack:
        stores=_open(stack,root)
        state=_state(stores['dispatch'],stores['recovery'],stores['oms'])
        context=load_context(state,symbol='BTCUSDC',account_ref=account,as_of_ms=now_ms())
        return context,len(stores['oms'].fills(strategy_id=strategy_id))


def settle(root,account,source_commit,*,strategy_id=None):
    deadline=time.monotonic()+45
    with ExitStack() as stack:
        stores=_open(stack,root)
        loop=RecoveryLoop(**stores,evidence_directory=root/'evidence',output_directory=root/'resident-recovery',
            source_commit=source_commit)
        while time.monotonic()<deadline:
            report=loop.tick()
            save_status(root/'resident-recovery/views',build_status(report,
                executor_boot_sha256=demo_authority.executor_boot_sha256(),source_commit=source_commit))
            resolution=report.get('resolution') or {}
            if 'STREAM_GAPS_IN_WINDOW' in resolution.get('issues',[]):
                return {'status':'EVIDENCE_REPAIR_REQUIRED','quantity':None,'fill_count':None,
                    'reason':'STREAM_GAPS_IN_WINDOW'}
            try:context,count=position(root,account,strategy_id=strategy_id)
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
        spec['account_ref_sha256']==account,'FUNCTIONAL_REDUCTION_SCOPE')
    captured=demo_executor_origin.run(symbol='BTCUSDC',source_commit=source_commit)
    require(captured['status']=='CAPTURED','FUNCTIONAL_REDUCTION_ORIGIN')
    origin=load_origin(Path(captured['path']))
    context,_=position(root,account);validate_origin(origin,context,as_of_ms=now_ms())
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
    capture=load_capture(area/'cash');context,count=position(root,account,strategy_id=cycle_strategy(root,area))
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


def cycle_strategy(root,area):
    path=area/'cycle-scope.json'
    if not path.exists() and not path.is_symlink():return None
    _check_path(path,root.stat().st_uid,directory=False)
    require(path.stat().st_size<=1024,'FUNCTIONAL_SCOPE_SIZE')
    value=load_json_without_duplicate_keys(path.read_bytes())
    require(set(value)=={'schema','cycle_sha256','strategy_id'} and value['schema']=='DEMO_CYCLE_SCOPE_V1' and
        value['cycle_sha256']==area.name and type(value['strategy_id']) is str and
        re.fullmatch('demo-functional-[a-f0-9]{16}',value['strategy_id']),'FUNCTIONAL_SCOPE_BINDING')
    return value['strategy_id']


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--cycle-sha256',required=True)
    parser.add_argument('--operation',choices=('cash','close-cash','settle','reduce','reconcile','resume-check','resume-cash','account-recover','account-cash','context'),required=True)
    args=parser.parse_args(argv);os.umask(0o077)
    try:
        verify_source(args.source_commit);root=require_custody();_,account=fixed_account()
        private_pipe(sys.stdin.fileno());private_pipe(sys.stdout.fileno())
        raw=read_frame(sys.stdin.fileno(),maximum=65536,deadline=time.monotonic()+10)
        payload=load_json_without_duplicate_keys(raw);require(type(payload) is dict,'FUNCTIONAL_INPUT')
        area=area_for(root,args.cycle_sha256)
        if args.operation=='account-cash':
            from tools.demo_account_cash import run
            result=run(root,area,account,args.source_commit,strategy_id=cycle_strategy(root,area))
        elif args.operation=='context':
            context,_=position(root,account)
            result={'status':'CANONICAL_CONTEXT','context':context}
        elif args.operation=='account-recover':
            from tools.demo_reported_recovery import run
            result=run(source_commit=args.source_commit)
        elif args.operation=='cash':
            if payload.get('strategy_id') is not None:
                from tools.demo_operating_bundle import write_new
                sid=payload['strategy_id']
                require(type(sid) is str and re.fullmatch('demo-functional-[a-f0-9]{16}',sid),'FUNCTIONAL_SCOPE_BINDING')
                write_new(area/'cycle-scope.json',demo_operator_setup.canonical(dict(schema='DEMO_CYCLE_SCOPE_V1',
                    cycle_sha256=area.name,strategy_id=sid)),uid=root.stat().st_uid,gid=root.stat().st_gid)
            result=capture_cash(root,area,account,args.source_commit)
        elif args.operation=='resume-check':
            prefix=opening_prefix(root,area,account,args.source_commit)
            result={'status':'ORIGINAL_OPENING_RETAINED','capture_prefix_sha256':demo_operator_setup.digest(prefix)}
        elif args.operation=='resume-cash':result=resume_cash(root,area,account,args.source_commit)
        elif args.operation=='close-cash':result=close_cash(area,account,args.source_commit)
        elif args.operation=='settle':result=settle(root,account,args.source_commit,strategy_id=cycle_strategy(root,area))
        elif args.operation=='reduce':result=reduce_task(root,account,args.source_commit,payload)
        else:result=reconcile(root,area,account)
        result.update(source_commit=args.source_commit,cycle_sha256=args.cycle_sha256,observed_at_ms=now_ms())
        _retain(area/'results',result)
        print(json.dumps(result,sort_keys=True,separators=(',',':')),flush=True);return 0
    except Exception:
        print(json.dumps({'status':'UNRESOLVED','operation':args.operation,'cycle_sha256':args.cycle_sha256,
            'source_commit':args.source_commit,'economic_authority':False}),flush=True);return 3


if __name__=='__main__':raise SystemExit(main())
