"""One installed task: observed inputs -> original supervisor -> original IDs.

Tasks are finite root-private documents with existing declarations and exact
orders. A durable task claim precedes capture. No implicit retries, fabricated
approvals, model decisions, state bootstrap or mainnet path are provided here.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import time

from live.execution.demo_batch_risk import _digest
from live.execution.demo_issuer_policy import require
from live.execution.demo_pre_submit import _retain
from live.execution.demo_request_inputs import task_check
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools import demo_supervisor as supervisor
from tools.demo_execution_session import read_frame


def preparation_command(*,source_commit,registry,unit):
    command=supervisor.child_command('executor',source_commit=source_commit,registry=registry,purpose='submission',unit=unit)
    command[command.index('RuntimeMaxSec=90s')]='RuntimeMaxSec=155s'
    command[command.index('tools.demo_execution_session')]='tools.demo_request_prepare'
    del command[-2:]
    return command


def prepare_exchange(process,bundle,*,deadline):
    supervisor._send(process.stdin,json.dumps(bundle,sort_keys=True,separators=(',',':'),allow_nan=False).encode(),deadline)
    raw=read_frame(process.stdout.fileno(),maximum=supervisor.MAX_INPUT_BYTES,deadline=deadline)
    result=load_json_without_duplicate_keys(raw)
    require(type(result) is dict and result.get('schema')=='DEMO_REQUEST_PREPARATION_V1' and
        result.get('status')=='PREPARED_INPUT_ONLY' and result.get('task_sha256')==_digest(bundle['task']) and
        result.get('submission_enabled') is False and result.get('economic_authority') is False,'TASK_PREPARATION_RESULT')
    require(type(result.get('request')) is dict and result['request'].get('schema')=='DEMO_EXECUTION_SESSION_INPUT_V1' and
        result.get('request_sha256')==_digest(result['request']),'TASK_PREPARED_REQUEST_DIGEST')
    supervisor._finish(process,deadline)
    return result


def task_directories():
    for name in ('tasks','task-claims','task-results','requests'):
        path=supervisor.STATE/name;supervisor._root_private_directory(path)
        count=size=0
        for entry in path.iterdir():
            info=entry.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and
                not info.st_mode&0o077,'TASK_HISTORY_CUSTODY')
            count+=1;size+=info.st_size
            require(count<16000 and size<256*1024*1024,'TASK_HISTORY_BUDGET')


def save_request(request):
    """The original supervisor hashes exact file bytes, without a trailing LF."""
    directory=supervisor.STATE/'requests';supervisor._root_private_directory(directory)
    body=json.dumps(request,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    require(0<len(body)<=supervisor.MAX_INPUT_BYTES,'TASK_REQUEST_SIZE')
    identity=hashlib.sha256(body).hexdigest();path=directory/(identity+'.json')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    require(supervisor.read_request(identity)==body,'TASK_REQUEST_PERSISTENCE')
    return identity


def same_declarations(request,task):
    profile=task['profile'];risk=request['risk']
    require(request['orders']==task['orders'] and request['mandate']==profile['mandate'] and
        request['attestations']==profile['attestations'] and risk['kill_switch']==task['kill_switch'] and
        risk['native_limits']==profile['native_limits'] and risk['strategy']==profile['strategy'],
        'TASK_DECLARATIONS_CHANGED')


def run(*,source_commit,task_sha256):
    previous=os.umask(0o077);phase='CONFIGURATION';claimed=False;process=None;unit=None;request_hash=None
    env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','SYSTEMD_COLORS':'0'}
    try:
        registry,policy=supervisor.configuration(source_commit);task_directories()
        raw=supervisor.read_document(supervisor.STATE/'tasks',task_sha256)
        task=load_json_without_duplicate_keys(raw)
        # Canonical task bytes give one stable task identity regardless of UI.
        require(_digest(task)==task_sha256,'TASK_CANONICAL_IDENTITY')
        task_check(task,policy=policy,source_commit=source_commit,now_ms=supervisor.now_ms())
        phase='TASK_CLAIM';unit='quantos-demo-executor-'+os.urandom(8).hex()
        supervisor.claim(supervisor.STATE/'task-claims',{'schema':'DEMO_TASK_CLAIM_V1',
            'task_sha256':task_sha256,'source_commit':source_commit,'unit':unit,'observed_at_ms':supervisor.now_ms()},
            identity_field='task_sha256')
        claimed=True;phase='OBSERVED_PREPARATION'
        process=subprocess.Popen(preparation_command(source_commit=source_commit,registry=registry,unit=unit),
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,env=env,cwd='/')
        prepared=prepare_exchange(process,{'task':task,'policy':policy},deadline=time.monotonic()+150)
        task_check(task,policy=policy,source_commit=source_commit,now_ms=supervisor.now_ms())
        same_declarations(prepared['request'],task)
        phase='REQUEST_PERSISTENCE';request_hash=save_request(prepared['request'])
        _retain(supervisor.STATE/'task-results',{'schema':'DEMO_TASK_PREPARED_V1','task_sha256':task_sha256,
            'request_sha256':request_hash,'source_commit':source_commit,'observed_at_ms':supervisor.now_ms(),
            'preparation_sha256':_digest(prepared),'submission_enabled':False,'economic_authority':False})
        phase='ORIGINAL_SUPERVISOR'
        execution=supervisor.run(source_commit=source_commit,request_sha256=request_hash,purpose='submission')
        result={'schema':'DEMO_TASK_RESULT_V1','status':execution['status'],'phase':phase,'task_sha256':task_sha256,
            'request_sha256':request_hash,'source_commit':source_commit,'observed_at_ms':supervisor.now_ms(),
            'task_claim_retained':True,'execution':execution,'retry_submission':False,'cash_reconciled':False,'economic_authority':False}
        _retain(supervisor.STATE/'task-results',result)
        return result
    except Exception:
        result={'schema':'DEMO_TASK_RESULT_V1','status':'TASK_NOT_COMPLETED','phase':phase,
            'task_sha256':task_sha256 if type(task_sha256) is str and re.fullmatch('[a-f0-9]{64}',task_sha256) else None,
            'request_sha256':request_hash,'source_commit':source_commit,'observed_at_ms':supervisor.now_ms(),
            'task_claim_retained':True if claimed else None if phase=='TASK_CLAIM' else False,
            'retry_submission':False,'cash_reconciled':False,'economic_authority':False}
        if claimed:
            try:_retain(supervisor.STATE/'task-results',result)
            except Exception:pass
        return result
    finally:
        if process is not None:
            try:subprocess.run(['systemctl','stop',unit],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            except (OSError,subprocess.TimeoutExpired):pass
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
            for channel in (process.stdin,process.stdout):
                if channel is not None:channel.close()
        os.umask(previous)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True);parser.add_argument('--task-sha256',required=True)
    args=parser.parse_args(argv);result=run(source_commit=args.source_commit,task_sha256=args.task_sha256)
    print(json.dumps(result,sort_keys=True))
    return 0 if result['status']=='EXECUTOR_RESULT_RECORDED' else 3


if __name__=='__main__':raise SystemExit(main())
