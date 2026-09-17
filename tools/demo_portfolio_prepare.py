"""Executor-only portfolio preparation over private controller pipes.

The controller verifies registered frozen research sources. This worker checks
the active policy, captures original account/market inputs, and runs original
risk preparation. It never signs, submits, bootstraps or resets any account.
"""
import argparse
from contextlib import ExitStack
import json
import os
import sys
import time

from live.execution import demo_portfolio_task, demo_pre_submit
from live.execution.demo_issuer_policy import require, current_key, historical_policy_binding
from live.execution.demo_batch_risk import _digest, _fields
from quantos.contracts.killswitch import KillSwitchState
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory_store import digest
from tools import demo_request_prepare as common, demo_portfolio_sources as sources
from tools import demo_account_cash as cash_tool, demo_functional_worker as functional


def check_bundle(bundle,*,registry,account,source_commit,now_ms):
    _fields(bundle,'allocation binding forecast profile policy kill_switch')
    allocation,profile,policy=bundle['allocation'],bundle['profile'],bundle['policy']
    current_key(policy,registry,now_ms)
    sid=demo_portfolio_task.strategy_id(allocation)
    require(allocation['schema']=='DEMO_PORTFOLIO_ALLOCATION_V2' and
        allocation['source_commit']==source_commit==policy['executor_release_commit'] and
        allocation['account_ref_sha256']==account==policy['account_ref_sha256'] and
        allocation['source_binding_sha256']==_digest(bundle['binding']) and
        profile['strategy']['strategy_id']==sid,'PORTFOLIO_WORKER_SCOPE')
    common.demo_request_inputs.profile_check(profile,policy,
        {'source_sha256':common.binance_demo_market_inputs.source_hash(),'symbol':allocation['symbol']},now_ms)
    forecast=sources._sealed(bundle['forecast'])
    require(forecast['model_hash']==allocation['model_hash'] and
        type(forecast['entry_ms']) is int and forecast['entry_ms']<=now_ms<forecast['entry_ms']+sources.HOUR,
        'PORTFOLIO_WORKER_FORECAST')
    kill=KillSwitchState.model_validate_json(json.dumps(bundle['kill_switch']),strict=True)
    common.demo_current_kill.require_expected(kill,now_ms=now_ms)
    return kill


def check_reconciliation_bundle(bundle,*,registry,account,source_commit):
    # Same declarations may be inspected after expiry; no clock is backdated.
    _fields(bundle,'allocation binding forecast profile policy kill_switch')
    allocation,profile,policy=bundle['allocation'],bundle['profile'],bundle['policy']
    historical_policy_binding(policy,registry)
    require(allocation['schema']=='DEMO_PORTFOLIO_ALLOCATION_V2' and
        allocation['source_commit']==source_commit==policy['executor_release_commit'] and
        allocation['account_ref_sha256']==account==policy['account_ref_sha256'] and
        allocation['source_binding_sha256']==_digest(bundle['binding']) and
        profile['strategy']['strategy_id']==demo_portfolio_task.strategy_id(allocation),'PORTFOLIO_WORKER_SCOPE')
    common.demo_request_inputs.profile_declarations(profile,policy,
        {'source_sha256':profile['market_source_sha256'],'symbol':allocation['symbol']})


def reconcile(root,account,source_commit,bundle):
    allocation=bundle['allocation'];sid=demo_portfolio_task.strategy_id(allocation)
    settled=functional.settle(root,account,source_commit,strategy_id=sid)
    if settled.get('status')!='SETTLED' or settled.get('quantity')!='0':
        return {'status':'RECONCILIATION_BLOCKED','settlement':settled,'cash_reconciled':False}
    area=root/'resident-recovery'/'portfolio-cycles'/_digest(allocation)
    for path in (root/'resident-recovery',area.parent,area):
        path.mkdir(mode=0o700,exist_ok=True)
        common._check_path(path,root.stat().st_uid,directory=True)
    cash=cash_tool.run_portfolio(root,area,account,source_commit,allocation=allocation)
    require(cash['status']=='MATCHED_REPORTED_INPUTS' and cash['final_quantity']=='0' and
        cash['wallet_residual']=='0' and cash['economic_authority'] is False,'PORTFOLIO_CASH_RESULT')
    return {'status':'MATCHED_REPORTED_INPUTS','cash':cash,'cash_reconciled':True}


def run(*,source_commit,input_fd=None,output=None,operation='prepare'):
    channel=sys.stdout if output is None else output
    root=None;bundle_hash=None;phase='CONFIGURATION';previous=os.umask(0o077)
    schema='DEMO_PORTFOLIO_RECONCILIATION_V1' if operation=='reconcile' else 'DEMO_PORTFOLIO_PREPARATION_V1'
    try:
        require(operation in ('prepare','reconcile'),'PORTFOLIO_WORKER_OPERATION')
        common.verify_source(source_commit);root,account=common.fixed_account()
        registry=common.demo_authority.load_registry_for_executor()
        require(os.geteuid()==os.getegid()==registry['executor_uid'] and
            set(os.getgroups())<={registry['executor_uid']},'PORTFOLIO_WORKER_IDENTITY')
        fd=sys.stdin.fileno() if input_fd is None else input_fd
        common.private_pipe(fd);common.private_pipe(channel.fileno())
        phase='REGISTERED_INPUT'
        raw=common.read_frame(fd,maximum=common.MAX_INPUT_BYTES,deadline=time.monotonic()+15)
        bundle=load_json_without_duplicate_keys(raw);bundle_hash=_digest(bundle)
        if operation=='reconcile':
            check_reconciliation_bundle(bundle,registry=registry,account=account,source_commit=source_commit)
            phase='ORIGINAL_ACCOUNT_RECONCILIATION'
            result={'schema':schema,'source_commit':source_commit,'bundle_sha256':bundle_hash,
                **reconcile(root,account,source_commit,bundle),'observed_at_ms':common.now_ms(),
                'submission_enabled':False,'economic_authority':False}
            common._retain(common.destination(root,'request-preparation-results'),result)
            print(json.dumps(result,sort_keys=True,separators=(',',':')),file=channel,flush=True)
            return 0 if result['cash_reconciled'] else 3
        check_bundle(bundle,registry=registry,account=account,source_commit=source_commit,now_ms=common.now_ms())
        target=common.destination(root,'request-preparation-results')
        phase='OBSERVATIONS'
        origin,market=common.capture_inputs(root=root,symbol=bundle['allocation']['symbol'],source_commit=source_commit)
        funding_receipt=sources.capture_funding()
        at=common.now_ms()
        funds=sources.funding_rows(funding_receipt,now_ms=at)
        # Retain the source response as well as the derived rows before success.
        common._retain(target,{'schema':'DEMO_PORTFOLIO_FUNDING_INPUT_V1','source_commit':source_commit,
            'bundle_sha256':bundle_hash,'receipt':funding_receipt,'economic_authority':False})
        phase='ORIGINAL_RISK'
        with ExitStack() as stack:
            stores=common._open(stack,root)
            with demo_pre_submit._preparation_lease(stores['dispatch']):
                at=common.now_ms()
                kill=check_bundle(bundle,registry=registry,account=account,source_commit=source_commit,now_ms=at)
                built=demo_portfolio_task.build(allocation=bundle['allocation'],forecast=bundle['forecast'],funding=funds,
                    origin=origin,market_inputs=market,profile=bundle['profile'],issuer_policy=bundle['policy'],
                    kill_state=kill,**{k:stores[k] for k in ('dispatch','recovery','oms')},now_ms=at)
        result={'schema':schema,'status':built['status'],'source_commit':source_commit,
            'bundle_sha256':bundle_hash,'observed_at_ms':common.now_ms(),'build':built,
            'funding_receipt_sha256':digest(funding_receipt),'submission_enabled':False,'economic_authority':False}
        body=json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False)
        require(len(body.encode())<=common.MAX_INPUT_BYTES,'PORTFOLIO_WORKER_RESULT_SIZE')
        phase='RESULT_PERSISTENCE';common._retain(target,result)
        print(body,file=channel,flush=True)
        return 0
    except Exception:
        result={'schema':schema,'status':'INPUTS_UNRESOLVED','phase':phase,'cash_reconciled':False,
            'source_commit':source_commit,'bundle_sha256':bundle_hash,'observed_at_ms':common.now_ms(),
            'submission_enabled':False,'economic_authority':False}
        if root is not None:
            try:common._retain(common.destination(root,'request-preparation-results'),result)
            except Exception:pass
        try:print(json.dumps(result,sort_keys=True,separators=(',',':')),file=channel,flush=True)
        except (OSError,ValueError):pass
        return 3
    finally:os.umask(previous)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--operation',choices=('prepare','reconcile'),default='prepare')
    args=parser.parse_args(argv)
    return run(source_commit=args.source_commit,operation=args.operation)


if __name__=='__main__':raise SystemExit(main())
