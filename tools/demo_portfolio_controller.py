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
    else:profile_check(profile,policy,market,supervisor.now_ms())
    KillSwitchState.model_validate_json(json.dumps(config['kill_switch']),strict=True)
    return registry,policy,profile,config


def prepare(bundle,*,source_commit,registry,operation='prepare'):
    require(operation in ('prepare','reconcile'),'PORTFOLIO_CONTROLLER_OPERATION')
    unit='quantos-demo-executor-'+os.urandom(8).hex()
    command=task_tool.preparation_command(source_commit=source_commit,registry=registry,unit=unit)
    command[command.index('tools.demo_request_prepare')]='tools.demo_portfolio_prepare'
    seconds=420 if operation=='reconcile' else 180
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
        successes=('MATCHED_REPORTED_INPUTS',) if operation=='reconcile' else ('TASK_PREPARED','REDUCE_BEFORE_REVERSE','NO_CHANGE')
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


def run(*,source_commit,execute=False,reconcile=False):
    previous=os.umask(0o077);area=None;phase='CONFIGURATION'
    result={'schema':'DEMO_PORTFOLIO_CONTROLLER_V1','source_commit':source_commit,
        'status':'INPUTS_UNRESOLVED','task_sha256':None,'cash_reconciled':False,
        'economic_authority':False,'retry_submission':False}
    try:
        require(type(execute) is bool and type(reconcile) is bool and not (execute and reconcile),
            'PORTFOLIO_CONTROLLER_EXECUTION_MODE')
        read_config=reconciliation_configuration if reconcile else configuration
        registry,policy,profile,config=read_config(source_commit)
        area=supervisor.STATE/'portfolio-results';area.mkdir(mode=0o700,exist_ok=True)
        supervisor._root_private_directory(area)
        with cycle_lock(area):
            checkpoint._budget(area)
            if reconcile:
                phase='ORIGINAL_ACCOUNT_RECONCILIATION'
                bundle={'allocation':config['allocation'],'binding':config['binding'],'forecast':None,
                    'profile':profile,'policy':policy,'kill_switch':config['kill_switch']}
                recovered=prepare(bundle,source_commit=source_commit,registry=registry,operation='reconcile')
                require(read_config(source_commit)==(registry,policy,profile,config),'PORTFOLIO_CONTROLLER_CONFIG_CHANGED')
                result.update(status='CASH_RECONCILIATION_REQUIRED',reconciliation=recovered)
                if recovered.get('cash_reconciled') is True:
                    cash=recovered['cash']
                    require(recovered['status']==cash['status']=='MATCHED_REPORTED_INPUTS' and
                        cash['final_quantity']=='0' and cash['wallet_residual']=='0' and
                        cash['economic_authority'] is False,'PORTFOLIO_CONTROLLER_CASH')
                    result.update(status='CASH_RECONCILED',cash_reconciled=True)
                return result
            phase='FROZEN_SOURCES'
            forecast=sources.load_forecast(root=config['research_root'],binding=config['binding'],
                allocation=config['allocation'],now_ms=supervisor.now_ms())
            identity=_digest({'account':config['allocation']['account_ref_sha256'],
                'model':config['allocation']['model_hash'],'slot_ms':forecast['slot_ms']})
            result.update(decision_sha256=identity,forecast_sha256=forecast['result_hash'])
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


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--execute',action='store_true',help='Use the existing task executor within the installed approval.')
    mode.add_argument('--reconcile',action='store_true',help='Recover original orders and reconcile cash, including after approval expiry.')
    args=parser.parse_args(argv)
    result=run(source_commit=args.source_commit,execute=args.execute,reconcile=args.reconcile)
    print(json.dumps(result,sort_keys=True))
    return 3 if result['status'] in ('INPUTS_UNRESOLVED','CONTROLLER_RESULT_NOT_RETAINED','EXECUTION_NOT_COMPLETED',
        'CASH_RECONCILIATION_REQUIRED') else 0


if __name__=='__main__':raise SystemExit(main())
