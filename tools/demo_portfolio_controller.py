"""Finite root controller for an independently registered Demo portfolio.

Reads protected declarations and fixed research sources, delegates account
preparation to the executor UID, retains the first decision, and optionally
calls the original task executor. No enrollment, key access or claim reset.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require, historical_policy_binding
from live.execution.demo_portfolio_task import strategy_id
from live.execution.demo_request_inputs import profile_check, profile_declarations, task_check
from live.execution.demo_pre_submit import _retain
from quantos.contracts.killswitch import KillSwitchState
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools import demo_supervisor as supervisor, demo_task as task_tool
from tools import demo_portfolio_sources as sources, demo_portfolio_checkpoint as checkpoint
from tools.demo_cycle_checkpoint import cycle_lock
from tools.binance_demo_market_inputs import source_hash as market_source_hash

PROFILE=Path('/etc/quantos/demo-issuer/profile.json')
CLOSE_WINDOW_MS=300000


def closing_at(policy):
    # Current continuation supports flat UTC-day boundaries, not overnight marks.
    day_end=(policy['valid_from_ms']//86400000+1)*86400000
    return min(policy['valid_until_ms'],day_end)-CLOSE_WINDOW_MS


def configuration(source_commit):
    registry,policy=supervisor.configuration(source_commit)
    return registered_configuration(source_commit,registry,policy,read_only=False)


def reconciliation_configuration(source_commit):
    require(os.geteuid()==0,'SUPERVISOR_ROOT_REQUIRED');supervisor.verify_source(source_commit)
    registry=supervisor.validate_registry(load_json_without_duplicate_keys(
        supervisor._read_root_owned_json(supervisor.EXECUTOR_REGISTRY)))
    require(load_json_without_duplicate_keys(supervisor._read_root_owned_json(supervisor.ISSUER_REGISTRY))==registry,
        'SUPERVISOR_REGISTRY_MIRROR')
    policy=load_json_without_duplicate_keys(supervisor._read_root_owned_json(supervisor.ISSUER_POLICY))
    historical_policy_binding(policy,registry)
    supervisor._root_private_directory(supervisor.STATE)
    return registered_configuration(source_commit,registry,policy,read_only=True)


def registered_configuration(source_commit,registry,policy,*,read_only):
    require(policy['executor_release_commit']==source_commit,'SUPERVISOR_POLICY_RELEASE')
    config=load_json_without_duplicate_keys(supervisor._read_root_owned_json(supervisor.STATE/'portfolio-runtime.json'))
    profile=load_json_without_duplicate_keys(supervisor._read_root_owned_json(PROFILE))
    validate_runtime(config,profile,policy,source_commit=source_commit,now_ms=supervisor.now_ms(),read_only=read_only)
    return registry,policy,profile,config


def validate_runtime(config,profile,policy,*,source_commit,now_ms,read_only=False):
    _fields(config,'schema source_commit research_root allocation binding kill_switch')
    require(config['schema']=='DEMO_PORTFOLIO_RUNTIME_V1' and config['source_commit']==source_commit,
        'PORTFOLIO_CONTROLLER_CONFIG')
    allocation=config['allocation']
    require(allocation['schema']=='DEMO_PORTFOLIO_ALLOCATION_V2' and
        allocation['source_commit']==source_commit and
        allocation['account_ref_sha256']==policy['account_ref_sha256'] and
        allocation['source_binding_sha256']==_digest(config['binding']) and
        strategy_id(allocation)==policy['bindings']['strategy_id']==profile['strategy']['strategy_id'] and
        profile['strategy']['approved'] is True,'PORTFOLIO_CONTROLLER_ALLOCATION')
    root=config['research_root']
    require(type(root) is str and len(root)<=4096 and Path(root).is_absolute() and '..' not in Path(root).parts,
        'PORTFOLIO_CONTROLLER_RESEARCH_ROOT')
    market={'source_sha256':market_source_hash(),'symbol':allocation['symbol']}
    if read_only:profile_declarations(profile,policy,market)
    else:profile_check(profile,policy,market,now_ms)
    KillSwitchState.model_validate_json(json.dumps(config['kill_switch']),strict=True)


def prepare(bundle,*,source_commit,registry,operation='prepare'):
    require(operation in ('prepare','close','reconcile'),'PORTFOLIO_CONTROLLER_OPERATION')
    unit='quantos-demo-executor-'+os.urandom(8).hex()
    command=task_tool.preparation_command(source_commit=source_commit,registry=registry,unit=unit)
    command[command.index('tools.demo_request_prepare')]='tools.demo_portfolio_prepare'
    seconds=420 if operation=='reconcile' else 240 if operation=='close' else 180
    command[command.index('RuntimeMaxSec=155s')]=f'RuntimeMaxSec={seconds}s'
    command.extend(['--operation',operation])
    env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','SYSTEMD_COLORS':'0'}
    process=None
    try:
        process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,bufsize=0,env=env,cwd='/')
        deadline=time.monotonic()+seconds-5
        supervisor._send(process.stdin,json.dumps(bundle,separators=(',',':'),allow_nan=False).encode(),deadline)
        process.stdin.close()
        raw=supervisor.read_frame(process.stdout.fileno(),maximum=supervisor.MAX_INPUT_BYTES,deadline=deadline)
        value=load_json_without_duplicate_keys(raw)
        schema='DEMO_PORTFOLIO_RECONCILIATION_V1' if operation=='reconcile' else 'DEMO_PORTFOLIO_PREPARATION_V1'
        require(value.get('schema')==schema and
            value.get('source_commit')==source_commit and value.get('bundle_sha256')==_digest(bundle) and
            value.get('submission_enabled') is False and value.get('economic_authority') is False,
            'PORTFOLIO_CONTROLLER_CHILD_BINDING')
        code=process.wait(timeout=max(.01,deadline-time.monotonic()))
        successes=('MATCHED_REPORTED_INPUTS',) if operation=='reconcile' else (
            ('CLOSE_TASK_PREPARED','ALREADY_FLAT') if operation=='close' else ('TASK_PREPARED','REDUCE_BEFORE_REVERSE','NO_CHANGE'))
        failures=('INPUTS_UNRESOLVED','RECONCILIATION_BLOCKED') if operation=='reconcile' else ('INPUTS_UNRESOLVED',)
        require((code==0 and value['status'] in successes) or
            (code==3 and value['status'] in failures),'PORTFOLIO_CONTROLLER_CHILD_STATUS')
        return value
    finally:
        if process is not None:
            try:subprocess.run(['systemctl','stop',unit],env=env,stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,timeout=10,check=False)
            except (OSError,subprocess.TimeoutExpired):pass
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
            for channel in (process.stdin,process.stdout):
                if channel is not None:channel.close()


def close_step(*,source_commit,registry,policy,profile,config,execute):
    previous=None
    for index in (1,2):
        allocation=config['allocation']
        record=checkpoint.read_close(allocation=allocation,policy=policy,index=index)
        built=None
        if record is None:
            bundle={'allocation':allocation,'binding':config['binding'],'forecast':None,'profile':profile,
                'policy':policy,'kill_switch':config['kill_switch'],'close_index':index,'previous_close':previous}
            prepared=prepare(bundle,source_commit=source_commit,registry=registry,operation='close')
            if prepared['status']=='INPUTS_UNRESOLVED':
                return {'status':'CLOSE_RECOVERY_REQUIRED','worker_phase':prepared.get('phase')}
            built=prepared['build']
            if built['task'] is not None:task_tool.same_declarations(built['request_build']['request'],built['task'])
            require(configuration(source_commit)==(registry,policy,profile,config),'PORTFOLIO_CONTROLLER_CONFIG_CHANGED')
        retained=checkpoint.retain_close(allocation=allocation,policy=policy,index=index,built=built,now_ms=supervisor.now_ms())
        if retained['status']=='QUERY_ONLY':
            previous=record['task']
            continue
        if execute and retained['status']=='TASK_READY':
            execution=task_tool.run(source_commit=source_commit,task_sha256=retained['task_sha256'])
            retained.update(status='EXECUTION_ATTEMPT_RECORDED' if execution.get('status')=='EXECUTOR_RESULT_RECORDED'
                else 'EXECUTION_NOT_COMPLETED',execution=execution)
        return {**retained,'close_index':index}
    return {'status':'CLOSE_RECOVERY_REQUIRED','close_index':2}


def cash_result(recovered):
    result={'status':'CASH_RECONCILIATION_REQUIRED','reconciliation':recovered,'cash_reconciled':False}
    if recovered.get('cash_reconciled') is True:
        cash=recovered['cash']
        require(recovered['status']==cash['status']=='MATCHED_REPORTED_INPUTS' and
            cash['final_quantity']=='0' and cash['wallet_residual']=='0' and
            cash['economic_authority'] is False,'PORTFOLIO_CONTROLLER_CASH')
        result.update(status='CASH_RECONCILED',cash_reconciled=True)
    return result


def run(*,source_commit,execute=False,reconcile=False,close=False,tick=False,expected_policy_sha256=None):
    previous=os.umask(0o077);area=None;phase='CONFIGURATION'
    result={'schema':'DEMO_PORTFOLIO_CONTROLLER_V1','source_commit':source_commit,
        'status':'INPUTS_UNRESOLVED','task_sha256':None,'cash_reconciled':False,
        'economic_authority':False,'retry_submission':False}
    try:
        require(all(type(v) is bool for v in (execute,reconcile,close,tick)) and
            not (reconcile and (execute or close)) and not (tick and (execute or reconcile or close)),
            'PORTFOLIO_CONTROLLER_EXECUTION_MODE')
        if tick:
            _,policy,_,_=reconciliation_configuration(source_commit)
            reconcile=supervisor.now_ms()>=policy['valid_until_ms']
            execute=not reconcile
            close=not reconcile and supervisor.now_ms()>=closing_at(policy)
        read_config=reconciliation_configuration if reconcile else configuration
        registry,policy,profile,config=read_config(source_commit)
        require(expected_policy_sha256 is None or _digest(policy)==expected_policy_sha256,
            'PORTFOLIO_CYCLE_POLICY_CHANGED')
        area=supervisor.STATE/'portfolio-results';area.mkdir(mode=0o700,exist_ok=True)
        supervisor._root_private_directory(area)
        with cycle_lock(area):
            checkpoint._budget(area)
            closing_started=checkpoint.read_close(allocation=config['allocation'],policy=policy,index=1) is not None
            if tick and closing_started and not reconcile:close=True
            if reconcile:
                phase='ORIGINAL_ACCOUNT_RECONCILIATION'
                bundle={'allocation':config['allocation'],'binding':config['binding'],'forecast':None,
                    'profile':profile,'policy':policy,'kill_switch':config['kill_switch']}
                recovered=prepare(bundle,source_commit=source_commit,registry=registry,operation='reconcile')
                require(read_config(source_commit)==(registry,policy,profile,config),'PORTFOLIO_CONTROLLER_CONFIG_CHANGED')
                result.update(cash_result(recovered))
                return result
            if close:
                phase='PORTFOLIO_CLOSING'
                result.update(close_step(source_commit=source_commit,registry=registry,policy=policy,
                    profile=profile,config=config,execute=execute))
                if tick and (result['status']=='ALREADY_FLAT' or
                    result['status']=='CLOSE_RECOVERY_REQUIRED' and result.get('close_index')==2):
                    phase='ORIGINAL_ACCOUNT_RECONCILIATION'
                    bundle={'allocation':config['allocation'],'binding':config['binding'],'forecast':None,
                        'profile':profile,'policy':policy,'kill_switch':config['kill_switch']}
                    result.update(cash_result(prepare(bundle,source_commit=source_commit,registry=registry,operation='reconcile')))
                return result
            if closing_started or supervisor.now_ms()>=closing_at(policy):
                result['status']='CLOSING_REQUIRED';return result
            phase='FROZEN_SOURCES'
            forecast=sources.load_forecast(root=config['research_root'],binding=config['binding'],
                allocation=config['allocation'],now_ms=supervisor.now_ms())
            identity=_digest({'account':config['allocation']['account_ref_sha256'],
                'model':config['allocation']['model_hash'],'slot_ms':forecast['slot_ms']})
            result.update(decision_sha256=identity,forecast_sha256=forecast['result_hash'])
            # Reserve the remaining two grants for bounded closing attempts.
            decisions=supervisor.STATE/'portfolio-decisions'
            if decisions.exists():
                checkpoint._budget(decisions)
                for path in decisions.glob('*.json'):
                    old=load_json_without_duplicate_keys(supervisor._read_root_owned_json(path))
                    task=old.get('task')
                    if old['identity']!=identity and task is not None and task['issuer_policy_sha256']==_digest(policy):
                        result['status']='DECISION_BUDGET_RESERVED';return result
            built=None
            if not (supervisor.STATE/'portfolio-decisions'/(identity+'.json')).exists():
                phase='EXECUTOR_PREPARATION'
                bundle={'allocation':config['allocation'],'binding':config['binding'],'forecast':forecast,
                    'profile':profile,'policy':policy,'kill_switch':config['kill_switch']}
                prepared=prepare(bundle,source_commit=source_commit,registry=registry)
                if prepared['status']=='INPUTS_UNRESOLVED':
                    result['worker_phase']=prepared.get('phase')
                    return result
                require(prepared['bundle_sha256']==_digest(bundle),'PORTFOLIO_CONTROLLER_PREPARATION')
                built=prepared['build']
                if built['task'] is not None:
                    task_check(built['task'],policy=policy,source_commit=source_commit,now_ms=supervisor.now_ms())
                    task_tool.same_declarations(built['request_build']['request'],built['task'])
            phase='DECLARATION_RECHECK'
            require(configuration(source_commit)==(registry,policy,profile,config),'PORTFOLIO_CONTROLLER_CONFIG_CHANGED')
            phase='DECISION_RETENTION'
            retained=checkpoint.retain(allocation=config['allocation'],forecast=forecast,built=built,
                policy=policy,now_ms=supervisor.now_ms())
            result.update(status=retained['status'],task_sha256=retained['task_sha256'],
                checkpoint_sha256=retained['checkpoint_sha256'])
            if execute and retained['status']=='TASK_READY':
                if supervisor.now_ms()>=closing_at(policy):
                    result['status']='CLOSING_REQUIRED';return result
                phase='ORIGINAL_TASK_EXECUTOR'
                execution=task_tool.run(source_commit=source_commit,task_sha256=retained['task_sha256'])
                result.update(status='EXECUTION_ATTEMPT_RECORDED' if execution.get('status')=='EXECUTOR_RESULT_RECORDED'
                    else 'EXECUTION_NOT_COMPLETED',execution=execution)
            return result
    except Exception:
        result['status']='INPUTS_UNRESOLVED'
        return result
    finally:
        result.update(phase=phase,observed_at_ms=supervisor.now_ms())
        if area is not None:
            try:_retain(area,result)
            except Exception:
                result['status']='CONTROLLER_RESULT_NOT_RETAINED'
        os.umask(previous)


def run_cycle(*,source_commit):
    """Advance one installed policy only; never enroll or renew it."""
    try:
        _,policy,_,_=reconciliation_configuration(source_commit)
        require(0<policy['valid_until_ms']-policy['valid_from_ms']<=3600000 and
            policy['max_orders']==policy['max_grants']==3,'PORTFOLIO_CYCLE_BOUNDARY')
        deadline=time.monotonic()+max(0,(policy['valid_until_ms']-supervisor.now_ms())/1000)+420
        identity=_digest(policy)
        while time.monotonic()<deadline:
            require(_digest(reconciliation_configuration(source_commit)[1])==identity,'PORTFOLIO_CYCLE_POLICY_CHANGED')
            result=run(source_commit=source_commit,tick=True,expected_policy_sha256=identity)
            if result['status']=='CASH_RECONCILED' or result['status'] not in (
                'NO_CHANGE','QUERY_ONLY','DECISION_BUDGET_RESERVED','EXECUTION_ATTEMPT_RECORDED',
                'CLOSE_RECOVERY_REQUIRED','CLOSING_REQUIRED'):
                return result
            # After expiry the next tick can only recover. No implicit renewal.
            time.sleep(min(15,max(0,deadline-time.monotonic())))
        return {'schema':'DEMO_PORTFOLIO_CONTROLLER_V1','source_commit':source_commit,
            'status':'CYCLE_DEADLINE_REACHED','cash_reconciled':False,'economic_authority':False}
    except Exception:
        return {'schema':'DEMO_PORTFOLIO_CONTROLLER_V1','source_commit':source_commit,
            'status':'INPUTS_UNRESOLVED','cash_reconciled':False,'economic_authority':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--execute',action='store_true',help='Use the existing task executor within the installed approval.')
    mode.add_argument('--reconcile',action='store_true',help='Recover original orders and reconcile cash, including after approval expiry.')
    mode.add_argument('--tick',action='store_true',help='Advance a finite registered cycle; close in the last five minutes and recover after expiry.')
    mode.add_argument('--cycle',action='store_true',help='Run one installed finite policy through closing and reconciliation, without renewal.')
    parser.add_argument('--close',action='store_true',help='Prepare only a bounded exit; combine with --execute to use the original executor.')
    args=parser.parse_args(argv)
    if args.cycle and args.close:parser.error('--cycle cannot be combined with --close')
    result=(run_cycle(source_commit=args.source_commit) if args.cycle else
        run(source_commit=args.source_commit,execute=args.execute,reconcile=args.reconcile,close=args.close,tick=args.tick))
    print(json.dumps(result,sort_keys=True))
    return 3 if result['status'] in ('INPUTS_UNRESOLVED','CONTROLLER_RESULT_NOT_RETAINED','EXECUTION_NOT_COMPLETED',
        'CASH_RECONCILIATION_REQUIRED','CLOSE_RECOVERY_REQUIRED','CLOSING_REQUIRED','EXPIRED_QUERY_ONLY','CYCLE_DEADLINE_REACHED') else 0


if __name__=='__main__':raise SystemExit(main())
