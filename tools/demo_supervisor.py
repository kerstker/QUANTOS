"""One root-controlled Demo executor/issuer handoff, with no broker key reads.

Consumes an already prepared root-owned request by content hash. It does not
construct mandates, strategy approvals, attestations, risk inputs, keys or new
order IDs. A retained request claim forbids automatic replay after interruption.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import stat
import subprocess
import time

from live.execution.demo_authority import _read_root_owned_json,_require_protected
from live.execution.demo_issuer_policy import validate_policy,current_key,handoff_proposal,require
from live.execution.demo_pre_submit import _retain
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.validation.demo_submission import validate_registry
from tools.demo_execution_session import read_frame,parse_grant
from tools.demo_issuer import verify_source,MAX_INPUT_BYTES

STATE=Path('/var/lib/quantos-demo-controller')
EXECUTOR_REGISTRY=Path('/etc/quantos/demo-submission/authority.json')
ISSUER_REGISTRY=Path('/etc/quantos/demo-issuer/authority.json')
ISSUER_POLICY=Path('/etc/quantos/demo-issuer/policy.json')


def now_ms():return time.time_ns()//1_000_000


def _root_private_directory(path):
    for parent in reversed((path,*path.parents)):_require_protected(parent.lstat(),directory=True)
    require(not path.lstat().st_mode&0o077,'SUPERVISOR_PRIVATE_DIRECTORY')


def child_command(role,*,source_commit,registry,purpose,unit):
    require(role in ('executor','issuer') and purpose in ('submission','cancellation'),'SUPERVISOR_ROLE')
    require(re.fullmatch('[a-f0-9]{40}',source_commit) is not None and
            re.fullmatch('quantos-demo-'+role+'-[a-z0-9]{12,32}',unit) is not None,'SUPERVISOR_CHILD_IDENTITY')
    uid=registry[role+'_uid'];require(type(uid) is int and uid>0,'SUPERVISOR_CHILD_UID')
    root='/opt/quantos/releases/'+source_commit;venv='/opt/quantos/venvs/'+source_commit
    state='/var/lib/quantos-demo-'+role
    props={'User':str(uid),'Group':str(uid),'UMask':'0077','NoNewPrivileges':'yes','ProtectSystem':'strict',
        'ProtectHome':'yes','PrivateTmp':'yes','PrivateDevices':'yes','ProtectKernelTunables':'yes',
        'ProtectKernelModules':'yes','ProtectKernelLogs':'yes','ProtectControlGroups':'yes','ProtectClock':'yes',
        'ProtectHostname':'yes','RestrictSUIDSGID':'yes','LockPersonality':'yes','RestrictRealtime':'yes',
        'RestrictNamespaces':'yes','SystemCallArchitectures':'native','CapabilityBoundingSet':'',
        'AmbientCapabilities':'','WorkingDirectory':state,'ReadWritePaths':state,
        'RuntimeMaxSec':'90s','TimeoutStopSec':'5s','KillMode':'control-group','StandardError':'null'}
    if role=='issuer':
        props.update(PrivateNetwork='yes',RestrictAddressFamilies='AF_UNIX',
            ReadOnlyPaths=root+' /etc/quantos/demo-issuer',
            InaccessiblePaths='/etc/quantos/demo-submission /var/lib/quantos-demo-executor')
    else:
        props.update(RestrictAddressFamilies='AF_UNIX AF_INET AF_INET6',
            ReadOnlyPaths=root+' /etc/quantos/demo-submission',
            InaccessiblePaths='/etc/quantos/demo-issuer /var/lib/quantos-demo-issuer')
    command=['systemd-run','--quiet','--collect','--wait','--pipe','--unit='+unit]
    for key,value in props.items():command.extend(['-p',key+'='+value])
    module='tools.demo_issuer' if role=='issuer' else 'tools.demo_execution_session'
    command+=['--','/usr/bin/env','-i','PATH='+venv+'/bin:/usr/bin:/bin','LANG=C.UTF-8',
        'PYTHONPATH='+root,'PYTHONNOUSERSITE=1','PYTHONDONTWRITEBYTECODE=1',
        'LIVE_TRADING_ENABLED=false','REAL_BROKER_ENABLED=false','ALLOW_ORDER_SUBMISSION=false',
        'ALLOW_AI_DIRECT_TRADING=false','REQUIRE_HUMAN_APPROVAL=true',
        venv+'/bin/python','-P','-m',module,'--source-commit',source_commit,'--purpose',purpose]
    return command


def _send(channel,body,deadline):
    require(type(body) is bytes and 0<len(body)<=MAX_INPUT_BYTES and b'\n' not in body,'SUPERVISOR_SINGLE_FRAME')
    fd=channel.fileno();os.set_blocking(fd,False);remaining=memoryview(body+b'\n')
    while remaining:
        wait=deadline-time.monotonic()
        require(wait>0 and bool(select.select([],[fd],[],wait)[1]),'SUPERVISOR_SEND_TIMEOUT')
        try:count=os.write(fd,remaining[:4096])
        except BlockingIOError:continue
        require(count>0,'SUPERVISOR_SEND_CLOSED');remaining=remaining[count:]


def _finish(process,deadline):
    remaining=deadline-time.monotonic();require(remaining>0,'SUPERVISOR_CHILD_TIMEOUT')
    try:code=process.wait(timeout=remaining)
    except subprocess.TimeoutExpired:raise ValueError('SUPERVISOR_CHILD_TIMEOUT') from None
    require(code==0,'SUPERVISOR_CHILD_FAILED')
    fd=process.stdout.fileno()
    if select.select([fd],[],[],0)[0]:require(os.read(fd,1)==b'','SUPERVISOR_EXTRA_OUTPUT')


def exchange(executable,issuer_factory,request,*,purpose,deadline):
    require(purpose in ('submission','cancellation'),'SUPERVISOR_PURPOSE')
    prefix='DEMO_EXECUTION_SESSION' if purpose=='submission' else 'DEMO_CANCELLATION_SESSION'
    _send(executable.stdin,request,deadline)
    proposal_raw=read_frame(executable.stdout.fileno(),maximum=MAX_INPUT_BYTES,deadline=deadline)
    proposal=load_json_without_duplicate_keys(proposal_raw);handoff_proposal(proposal,purpose)
    signing=issuer_factory()
    _send(signing.stdin,proposal_raw,deadline);signing.stdin.close()
    grant_raw=read_frame(signing.stdout.fileno(),maximum=65536,deadline=deadline)
    identity,_=parse_grant(grant_raw,purpose=purpose)
    require(identity==proposal['proposal_sha256'],'SUPERVISOR_GRANT_BINDING')
    _finish(signing,deadline)
    _send(executable.stdin,grant_raw,deadline);executable.stdin.close()
    result_raw=read_frame(executable.stdout.fileno(),maximum=MAX_INPUT_BYTES,deadline=deadline)
    result=load_json_without_duplicate_keys(result_raw)
    require(type(result) is dict and result.get('schema')==prefix+'_RESULT_V1' and
            result.get('cash_reconciled') is False and result.get('economic_authority') is False,'SUPERVISOR_EXECUTOR_RESULT')
    _finish(executable,deadline)
    return {'proposal_sha256':identity,'grant_frame_sha256':hashlib.sha256(grant_raw).hexdigest(),
            'result_sha256':hashlib.sha256(result_raw).hexdigest(),'result':result}


def claim(directory,value):
    _root_private_directory(directory)
    identity=value['request_sha256'];require(re.fullmatch('[a-f0-9]{64}',identity) is not None,'SUPERVISOR_REQUEST_HASH')
    path=directory/(identity+'.json');body=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return path


def read_request(identity):
    require(type(identity) is str and re.fullmatch('[a-f0-9]{64}',identity) is not None,'SUPERVISOR_REQUEST_HASH')
    directory=STATE/'requests';_root_private_directory(directory)
    fd=os.open(directory/(identity+'.json'),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    def check(info):
        require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and
                not info.st_mode&0o077 and 0<info.st_size<=MAX_INPUT_BYTES,'SUPERVISOR_REQUEST_CUSTODY')
    try:
        before=os.fstat(fd);check(before);pieces=[];size=0
        while size<=MAX_INPUT_BYTES:
            piece=os.read(fd,min(65536,MAX_INPUT_BYTES+1-size))
            if not piece:break
            pieces.append(piece);size+=len(piece)
        after=os.fstat(fd);check(after)
        require(size==before.st_size and all(getattr(before,k)==getattr(after,k) for k in
            ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')),'SUPERVISOR_REQUEST_CHANGED')
        raw=b''.join(pieces);require(hashlib.sha256(raw).hexdigest()==identity,'SUPERVISOR_REQUEST_DIGEST')
        value=load_json_without_duplicate_keys(raw)
        return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    finally:os.close(fd)


def configuration(source_commit):
    require(os.geteuid()==0,'SUPERVISOR_ROOT_REQUIRED');verify_source(source_commit)
    executor=_read_root_owned_json(EXECUTOR_REGISTRY);issuer=_read_root_owned_json(ISSUER_REGISTRY)
    registry=validate_registry(load_json_without_duplicate_keys(executor))
    require(load_json_without_duplicate_keys(issuer)==registry,'SUPERVISOR_REGISTRY_MIRROR')
    policy=validate_policy(load_json_without_duplicate_keys(_read_root_owned_json(ISSUER_POLICY)))
    current_key(policy,registry,now_ms())
    require(policy['executor_release_commit']==source_commit,'SUPERVISOR_POLICY_RELEASE')
    for path in (STATE,STATE/'requests',STATE/'claims',STATE/'results'):_root_private_directory(path)
    for directory in (STATE/'claims',STATE/'results'):
        count=size=0
        for path in directory.iterdir():
            info=path.lstat();require(stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and
                not info.st_mode&0o077,'SUPERVISOR_HISTORY_CUSTODY')
            count+=1;size+=info.st_size
            require(count<16000 and size<256*1024*1024,'SUPERVISOR_HISTORY_BUDGET')
    return registry,policy


def run(*,source_commit,request_sha256,purpose):
    previous=os.umask(0o077);owned={};units={};claimed=False;phase='CONFIGURATION';registry=policy=None
    env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','SYSTEMD_COLORS':'0'}
    try:
        require(purpose in ('submission','cancellation'),'SUPERVISOR_PURPOSE')
        registry,policy=configuration(source_commit);request=read_request(request_sha256)
        expected='DEMO_EXECUTION_SESSION_INPUT_V1' if purpose=='submission' else 'DEMO_CANCELLATION_SESSION_INPUT_V1'
        require(json.loads(request).get('schema')==expected,'SUPERVISOR_REQUEST_SCOPE')
        nonce=os.urandom(8).hex();units={role:'quantos-demo-'+role+'-'+nonce for role in ('executor','issuer')}
        claim(STATE/'claims',{'schema':'DEMO_SUPERVISOR_CLAIM_V1','request_sha256':request_sha256,
            'purpose':purpose,'source_commit':source_commit,'units':units,'observed_at_ms':now_ms()})
        claimed=True;phase='HANDOFF'
        def launch(role):
            require(role not in owned,'SUPERVISOR_NO_CHILD_RETRY')
            command=child_command(role,source_commit=source_commit,registry=registry,purpose=purpose,unit=units[role])
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                bufsize=0,env=env,cwd='/');owned[role]=process;return process
        response=exchange(launch('executor'),lambda:launch('issuer'),request,purpose=purpose,deadline=time.monotonic()+60)
        phase='RESULT_PERSISTENCE'
        value={'schema':'DEMO_SUPERVISOR_RESULT_V1','request_sha256':request_sha256,'source_commit':source_commit,
            'observed_at_ms':now_ms(),'purpose':purpose,'units':units,'status':'EXECUTOR_RESULT_RECORDED',
            **response,'cash_reconciled':False,'economic_authority':False}
        _retain(STATE/'results',value)
        return {key:value[key] for key in ('schema','request_sha256','source_commit','observed_at_ms','purpose','status','result_sha256','cash_reconciled','economic_authority')}
    except Exception:
        value={'schema':'DEMO_SUPERVISOR_RESULT_V1','request_sha256':request_sha256,'source_commit':source_commit,
            'observed_at_ms':now_ms(),'purpose':purpose,'units':units,'status':'INSPECT_ORIGINAL_ATTEMPT',
            'phase':phase,'claim_retained':claimed,'retry_submission':False,'cash_reconciled':False,'economic_authority':False}
        if claimed:
            try:_root_private_directory(STATE/'results');_retain(STATE/'results',value)
            except Exception:pass
        return value
    finally:
        # Only our random transient units are stopped. No resident service,
        # pending order, risk state or database is reset by supervisor cleanup.
        for role,process in owned.items():
            try:subprocess.run(['systemctl','stop',units[role]],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
            except (OSError,subprocess.TimeoutExpired):pass
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=2)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
            for stream in (process.stdin,process.stdout):
                if stream is not None:stream.close()
        os.umask(previous)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True);parser.add_argument('--request-sha256',required=True)
    parser.add_argument('--purpose',choices=('submission','cancellation'),default='submission')
    args=parser.parse_args(argv)
    result=run(source_commit=args.source_commit,request_sha256=args.request_sha256,purpose=args.purpose)
    print(json.dumps(result,sort_keys=True))
    return 0 if result['status']=='EXECUTOR_RESULT_RECORDED' else 3


if __name__=='__main__':raise SystemExit(main())
