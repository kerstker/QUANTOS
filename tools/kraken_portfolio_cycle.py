"""Bounded, restartable public-data/account-export allocation review cycle.

Consumes human-produced account snapshots. No credential or order endpoints.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import time

from services.data.kraken_public import require
from services.portfolio.kraken_allocation import validate_policy
from tools import kraken_inventory_report as inventory
from tools import kraken_portfolio_observer as observer
from tools import kraken_public_capture as capture


def private_directory(path):
    path=Path(path).absolute()
    require(path.resolve()==path,'KRAKEN_CYCLE_SYMLINK')
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    stat=path.stat()
    require(stat.st_uid==os.getuid() and stat.st_mode & 0o077==0,'KRAKEN_CYCLE_PRIVATE_DIRECTORY')
    return path


def read(path):
    require(not path.is_symlink(),'KRAKEN_CYCLE_SYMLINK')
    return inventory.load(path)


def bind(path,value):
    require(not path.is_symlink(),'KRAKEN_CYCLE_SYMLINK')
    if path.exists():require(read(path)==value,'KRAKEN_CYCLE_BINDING_CHANGED')
    else:capture.atomic_json(path,value)


def recover(root):
    latest=root/'status.json';previous=None;generation=0
    if latest.exists():
        previous=inventory.sealed(read(latest));generation=previous['generation']
        require(type(generation) is int and generation>0,'KRAKEN_CYCLE_GENERATION')
        require(read(root/'cycles'/f'{generation:08d}.json')==previous,'KRAKEN_CYCLE_ARCHIVE_MISMATCH')
    while (root/'cycles'/f'{generation+1:08d}.json').exists():
        candidate=inventory.sealed(read(root/'cycles'/f'{generation+1:08d}.json'))
        require(candidate.get('schema')=='KRAKEN_REVIEW_CYCLE_V1' and candidate['generation']==generation+1 and
                candidate['previous_hash']==(previous['result_hash'] if previous else None),'KRAKEN_CYCLE_CHAIN')
        require(previous is None or candidate['completed_at_ms']>=previous['completed_at_ms'],'KRAKEN_CYCLE_CLOCK')
        previous=candidate;generation+=1
    if previous is not None:capture.atomic_json(latest,previous)
    return previous


def run_once(root, config, *, account_path, policy_path=None, valuation_quote=None, observe=None, clock_ms=None):
    observer.validate_config(config)
    observe=observe or observer.run_once;clock_ms=clock_ms or (lambda:time.time_ns()//1_000_000)
    root=private_directory(root)
    for name in ('cycles','accounts','policies'):
        private_directory(root/name)
    for name in ('status.json','config.json','account-reference.json','policy-binding.json','market'):
        require(not (root/name).is_symlink(),'KRAKEN_CYCLE_SYMLINK')
    account_path=Path(account_path).absolute();policy_path=Path(policy_path).absolute() if policy_path else None
    fd=os.open(root/'.cycle.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        binding={'market':config,'account_path':str(account_path),'policy_path':str(policy_path) if policy_path else None,
                 'valuation_quote':valuation_quote}
        bind(root/'config.json',binding)
        previous=recover(root);started=clock_ms()
        require(type(started) is int and started>0 and (previous is None or started>=previous['completed_at_ms']),
                'KRAKEN_CYCLE_CLOCK')
        report={'schema':'KRAKEN_REVIEW_CYCLE_V1','generation':previous['generation']+1 if previous else 1,
                'previous_hash':previous['result_hash'] if previous else None,'started_at_ms':started,
                'stage':'MARKET','status':'MARKET_FAILED','market_status':'FAILED','market_hash':None,'inventory':None,'failure':None,
                'config_sha256':capture.digest(binding),'execution_status':'DISABLED',
                'portfolio_management_ready':False,'submission_enabled':False,'authoritative':False,
                'order_recovery_status':'NOT_CONNECTED','trade_reconciliation_status':'NOT_CONNECTED','cash_reconciliation_status':'NOT_CONNECTED','blockers':[]}
        try:
            market=inventory.sealed(observe(root/'market',config))
            require(read(root/'market'/'status.json')==market,'KRAKEN_CYCLE_MARKET_PUBLICATION')
            report.update(market_status=market['status'],market_hash=market['result_hash'])
            report['status']='INPUT_REJECTED';report['stage']='ACCOUNT'
            if not account_path.exists():report['status']='WAITING_ACCOUNT'
            else:
                account=inventory.sealed(read(account_path))
                require(account.get('schema')=='KRAKEN_ACCOUNT_OBSERVATION_V1','KRAKEN_CYCLE_ACCOUNT_SCHEMA')
                require(isinstance(account.get('account_ref_sha256'),str) and
                        re.fullmatch('[a-f0-9]{64}',account['account_ref_sha256']),'KRAKEN_CYCLE_ACCOUNT_REFERENCE')
                bind(root/'account-reference.json',{'account_ref_sha256':account['account_ref_sha256']})
                snapshot=root/'accounts'/(account['result_hash']+'.json')
                bind(snapshot,account)
                policy=None;report['stage']='POLICY'
                if policy_path and policy_path.exists():
                    policy=validate_policy(read(policy_path))
                    require(policy['account_ref_sha256']==account['account_ref_sha256'],'KRAKEN_CYCLE_POLICY_ACCOUNT')
                    bind(root/'policy-binding.json',{'sha256':capture.digest(policy)})
                    bind(root/'policies'/(capture.digest(policy)+'.json'),policy)
                cash_path=account_path.parent/'cash-reconciliation.json'
                if cash_path.exists():
                    cash=inventory.sealed(read(cash_path));now=clock_ms()
                    require(cash.get('schema')=='KRAKEN_BALANCE_BRIDGE_V1' and
                            cash['account_ref_sha256']==account['account_ref_sha256'],'KRAKEN_CYCLE_CASH_ACCOUNT')
                    report['cash_reconciliation_status']=cash['status']
                    if cash.get('closing_account_hash')!=account['result_hash'] or type(cash.get('as_of_ms')) is not int or not 0<=now-cash['as_of_ms']<=120000:
                        report['cash_reconciliation_status']='STALE_OR_DIFFERENT_ACCOUNT_SNAPSHOT'
                    # Matching observed balance changes alone does not prove trade linkage,
                    # full wallet scope, or execution permission. Existing blockers remain.
                recovery_path=account_path.parent/'order-recovery.json'
                required_path=root/'order-recovery-required.json'
                require(not required_path.is_symlink(),'KRAKEN_CYCLE_SYMLINK')
                if recovery_path.exists():
                    bind(required_path,{'required':True})
                    recovery=inventory.sealed(read(recovery_path));now=clock_ms()
                    require(recovery.get('schema')=='KRAKEN_ORDER_RECOVERY_V1' and
                            recovery['account_ref_sha256']==account['account_ref_sha256'],'KRAKEN_CYCLE_ORDER_ACCOUNT')
                    report['order_recovery_status']=recovery['status']
                    report['trade_reconciliation_status']=recovery.get('trade_status','NOT_OBSERVED')
                    if type(recovery.get('observed_at_ms')) is not int or not 0<=now-recovery['observed_at_ms']<=120000:
                        report['blockers'].append('ORDER_RECOVERY_STALE')
                    if recovery['status']=='UNRESOLVED':report['blockers'].append('ORDER_QUERY_UNRESOLVED')
                    if recovery['order_ids']:
                        report['blockers'].append('CASH_RECONCILIATION_REQUIRED' if recovery.get('trade_status')=='TRADE_TOTALS_MATCHED' else 'TRADE_AND_CASH_RECONCILIATION_REQUIRED')
                elif required_path.exists():
                    require(read(required_path)=={'required':True},'KRAKEN_CYCLE_RECOVERY_BINDING')
                    report['blockers'].append('ORDER_RECOVERY_MISSING')
                report['stage']='INVENTORY'
                result=inventory.build(snapshot,root/'market',as_of_ms=clock_ms(),
                    valuation_quote=valuation_quote or (policy['quote_asset'] if policy else None),
                    policy=None if report['blockers'] else policy)
                report['inventory']=result
                blocked=result['status']!='INVENTORY_MAPPED' or result.get('indicative_valuation',{}).get('status','INDICATIVE')!='INDICATIVE'
                if blocked or report['blockers']:report['status']='BLOCKED'
                elif policy is None:report['status']='WAITING_POLICY'
                else:report['status']=result['allocation_review']['status']
                report['stage']='COMPLETE' if report['status']=='REVIEW_ONLY' else 'POLICY' if report['status']=='WAITING_POLICY' else 'INVENTORY'
        except (OSError,ValueError,KeyError,TypeError) as exc:
            report['failure']={'error_type':type(exc).__name__}
        completed=clock_ms();require(type(completed) is int and completed>=started,'KRAKEN_CYCLE_CLOCK')
        report['completed_at_ms']=completed;report['result_hash']=capture.digest(report)
        target=root/'cycles'/f"{report['generation']:08d}.json"
        require(not target.exists() and not target.is_symlink(),'KRAKEN_CYCLE_ARCHIVE_EXISTS')
        capture.atomic_json(target,report)
        capture.atomic_json(root/'status.json',report)
        return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--account',type=Path,required=True)
    parser.add_argument('--policy',type=Path)
    parser.add_argument('--valuation-quote')
    parser.add_argument('--state-root',type=Path,required=True)
    parser.add_argument('--cycles',type=int,default=1)
    args=parser.parse_args(argv);require(1<=args.cycles<=1440,'KRAKEN_CYCLE_LIMIT')
    config=observer.validate_config(read(args.config));failures=0
    for index in range(args.cycles):
        if index:time.sleep(config['poll_seconds'])
        report=run_once(args.state_root,config,account_path=args.account,policy_path=args.policy,valuation_quote=args.valuation_quote)
        failures+=report['status']!='REVIEW_ONLY'
        print(json.dumps({k:report[k] for k in ('generation','status','market_status','execution_status','result_hash')}),flush=True)
    return 3 if failures else 0


if __name__=='__main__':raise SystemExit(main())
