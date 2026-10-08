"""One root-controlled Demo executor/issuer handoff, with no broker key reads.

Consumes an already prepared root-owned request by content hash. It does not
construct mandates, strategy approvals, attestations, risk inputs, keys or new
order IDs. A retained request claim forbids automatic replay after interruption.
"""
import argparse
from contextlib import contextmanager, ExitStack
import fcntl
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
from live.execution.demo_issuer_policy import validate_policy,current_key,handoff_proposal,require,GOLD_STRATEGY_POLICY_SCHEMAS,strategy_constraints_check
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


@contextmanager
def controller_lease(*, maintenance=False):
    """Coordinate installed controller work on the existing root directory inode.

    Exclusive maintenance never grants execution permission. Older processes and
    operator setup tools must separately be drained before production maintenance.
    """
    require(type(maintenance) is bool, 'CONTROLLER_LEASE_MODE')
    path=STATE;_root_private_directory(path)
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        opened=os.fstat(fd);current=path.lstat()
        require((opened.st_dev,opened.st_ino)==(current.st_dev,current.st_ino),'CONTROLLER_DIRECTORY_CHANGED')
        try:fcntl.flock(fd,(fcntl.LOCK_EX if maintenance else fcntl.LOCK_SH)|fcntl.LOCK_NB)
        except BlockingIOError:raise ValueError('CONTROLLER_MAINTENANCE_BUSY') from None
        current=path.lstat()
        require((opened.st_dev,opened.st_ino)==(current.st_dev,current.st_ino),'CONTROLLER_DIRECTORY_CHANGED')
        yield
    finally:os.close(fd)


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
    # Shared public activation is outside both roles' mutually hidden private
    # configuration roots. Missing activation must not prevent legacy startup.
    props['ReadOnlyPaths']+=' -/etc/quantos/demo-gold-delegation'
    if role=='issuer':props['ReadOnlyPaths']+=' -/etc/quantos/demo-gold-continuity'
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


def claim(directory,value,*,identity_field='request_sha256'):
    _root_private_directory(directory)
    require(identity_field in ('request_sha256','task_sha256'),'SUPERVISOR_CLAIM_KIND')
    identity=value[identity_field];require(re.fullmatch('[a-f0-9]{64}',identity) is not None,'SUPERVISOR_REQUEST_HASH')
    path=directory/(identity+'.json');body=(json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return path


def read_document(directory,identity):
    require(type(identity) is str and re.fullmatch('[a-f0-9]{64}',identity) is not None,'SUPERVISOR_REQUEST_HASH')
    _root_private_directory(directory)
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


def read_request(identity):return read_document(STATE/'requests',identity)


def configuration(source_commit):
    require(os.geteuid()==0,'SUPERVISOR_ROOT_REQUIRED');verify_source(source_commit)
    executor=_read_root_owned_json(EXECUTOR_REGISTRY);issuer=_read_root_owned_json(ISSUER_REGISTRY)
    registry=validate_registry(load_json_without_duplicate_keys(executor))
    require(load_json_without_duplicate_keys(issuer)==registry,'SUPERVISOR_REGISTRY_MIRROR')
    policy=load_json_without_duplicate_keys(_read_root_owned_json(ISSUER_POLICY))
    from live.execution.demo_gold_future_template import SCHEMA as FUTURE_SCHEMA, current_template_key
    if policy.get('schema')==FUTURE_SCHEMA:current_template_key(policy,registry,now_ms())
    else:current_key(validate_policy(policy),registry,now_ms())
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
    lease=ExitStack()
    try:
        lease.enter_context(controller_lease())
        require(purpose in ('submission','cancellation'),'SUPERVISOR_PURPOSE')
        registry,policy=configuration(source_commit);request=read_request(request_sha256)
        expected='DEMO_EXECUTION_SESSION_INPUT_V1' if purpose=='submission' else 'DEMO_CANCELLATION_SESSION_INPUT_V1'
        from live.execution.demo_gold_future_template import SCHEMA as FUTURE_SCHEMA
        future=policy['schema']==FUTURE_SCHEMA
        constrained=purpose=='submission' and (future or policy['schema'] in GOLD_STRATEGY_POLICY_SCHEMAS)
        if constrained:expected='DEMO_GOLD_STRATEGY_SESSION_INPUT_V1'
        document=load_json_without_duplicate_keys(request)
        delegated=purpose=='submission' and document.get('schema')=='DEMO_GOLD_DELEGATED_SESSION_INPUT_V1'
        require(not future or purpose=='cancellation' or delegated,'SUPERVISOR_FUTURE_TEMPLATE_PROOF_REQUIRED')
        effective=policy
        if delegated:
            from live.execution.demo_delegated_inputs import installed_context,resolve
            require(constrained,'SUPERVISOR_DELEGATED_POLICY_REQUIRED')
            expected='DEMO_GOLD_DELEGATED_SESSION_INPUT_V1'
            at=now_ms()
            admitted=resolve(document['delegated_admission'],policy=policy,orders=document['orders'],
                constraints=document['execution_constraints'],now_ms=at,
                context=installed_context(policy=policy,registry=registry,now_ms=at,role='controller'))
            effective=admitted['effective_policy']
        require(document.get('schema')==expected,'SUPERVISOR_REQUEST_SCOPE')
        last_admission_check=at if delegated else None
        def admission_time():
            nonlocal last_admission_check
            checked=now_ms()
            require(last_admission_check is not None and last_admission_check<=checked<admitted['valid_until_ms'],
                    'SUPERVISOR_DELEGATED_ADMISSION_EXPIRED_OR_CLOCK_ROLLBACK')
            last_admission_check=checked
            return checked
        if constrained:
            strategy_constraints_check(document.get('execution_constraints'),policy=effective,
                orders=document['orders'],now_ms=admission_time() if delegated else now_ms())
        nonce=os.urandom(8).hex();units={role:'quantos-demo-'+role+'-'+nonce for role in ('executor','issuer')}
        claim_at=admission_time() if delegated else now_ms()
        claim(STATE/'claims',{'schema':'DEMO_SUPERVISOR_CLAIM_V1','request_sha256':request_sha256,
            'purpose':purpose,'source_commit':source_commit,'units':units,'observed_at_ms':claim_at})
        claimed=True;phase='HANDOFF'
        def launch(role):
            require(role not in owned,'SUPERVISOR_NO_CHILD_RETRY')
            if delegated:admission_time()
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
        lease.close();os.umask(previous)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True);parser.add_argument('--request-sha256',required=True)
    parser.add_argument('--purpose',choices=('submission','cancellation'),default='submission')
    args=parser.parse_args(argv)
    result=run(source_commit=args.source_commit,request_sha256=args.request_sha256,purpose=args.purpose)
    print(json.dumps(result,sort_keys=True))
    return 0 if result['status']=='EXECUTOR_RESULT_RECORDED' else 3


if __name__=='__main__':raise SystemExit(main())
