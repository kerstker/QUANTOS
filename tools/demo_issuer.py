"""One fixed-policy Demo grant over private root-owned supervisor pipes.

Runs only as the independently installed issuer UID. No broker credentials,
network listener, key generation, human actor, policy creation or store reset.
The supervisor must isolate this child from networking and the executor state.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from live.execution.demo_authority import _read_root_owned_json, _require_protected
from live.execution.demo_issuer import _issue
from live.execution.demo_issuer_policy import validate_policy,current_key,review_submission,review_cancellation,require
from live.execution.demo_issuer_store import IssuerStore
from live.execution.demo_pre_submit import _retain
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.validation import demo_submission
from tools.demo_execution_session import read_frame

REGISTRY=Path('/etc/quantos/demo-issuer/authority.json')
POLICY=Path('/etc/quantos/demo-issuer/policy.json')
KEY=Path('/etc/quantos/demo-issuer/signing-key.json')
STATE=Path('/var/lib/quantos-demo-issuer')
MAX_INPUT_BYTES=16_000_000


def now_ms():return time.time_ns()//1_000_000


def private_pipe(fd):
    info=os.fstat(fd)
    require(stat.S_ISFIFO(info.st_mode) and info.st_uid==0 and info.st_gid==0 and
            not info.st_mode&0o077,'ISSUER_ROOT_PRIVATE_PIPE_REQUIRED')


def private_key_metadata(info,issuer_uid):
    _require_protected(info,directory=False)
    require(info.st_gid==issuer_uid and not info.st_mode&0o037,'ISSUER_KEY_READERS')


def verify_source(source_commit):
    require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}',source_commit) is not None,'ISSUER_SOURCE_ID')
    root=Path(__file__).resolve().parents[1]
    require(root==Path('/opt/quantos/releases')/source_commit,'ISSUER_SOURCE_LOCATION')
    for parent in reversed((root,*root.parents)):_require_protected(parent.lstat(),directory=True)
    from tools.l2_schedule_entrypoint import _verify_release_identity,_verify_release_evidence
    _verify_release_identity(root,source_commit);_verify_release_evidence(root,source_commit)


def _state_custody(root,uid):
    for parent in reversed(root.parents):_require_protected(parent.lstat(),directory=True)
    for path,directory in ((root,True),(root/'results',True),(root/'issuance.sqlite3',False)):
        info=path.lstat()
        require(info.st_uid==uid and info.st_gid==uid and not info.st_mode&0o077 and
                (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink==1),'ISSUER_STATE_CUSTODY')
    for suffix in ('-wal','-shm','-journal'):
        path=root/('issuance.sqlite3'+suffix)
        if path.exists() or path.is_symlink():
            info=path.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==uid and
                    info.st_gid==uid and not info.st_mode&0o077,'ISSUER_STATE_SIDECAR')


def require_identity(issuer_uid):
    require(os.geteuid()==os.getegid()==issuer_uid and set(os.getgroups())<={issuer_uid},'ISSUER_IDENTITY')


def configuration(source_commit):
    verify_source(source_commit)
    registry=demo_submission.validate_registry(load_json_without_duplicate_keys(_read_root_owned_json(REGISTRY)))
    require_identity(registry['issuer_uid'])
    policy=validate_policy(load_json_without_duplicate_keys(_read_root_owned_json(POLICY)))
    require(policy['executor_release_commit']==source_commit,'ISSUER_POLICY_RELEASE_MISMATCH')
    current_key(policy,registry,now_ms());_state_custody(STATE,registry['issuer_uid'])
    return STATE,registry,policy


def load_key(registry,policy):
    raw=_read_root_owned_json(KEY,file_check=lambda info:private_key_metadata(info,registry['issuer_uid']))
    value=load_json_without_duplicate_keys(raw)
    demo_submission._fields(value,'schema authority_id key_id private_key_base64url')
    require(value['schema']=='DEMO_ISSUER_KEY_V1' and value['authority_id']==policy['authority_id'] and
            value['key_id']==policy['key_id'],'ISSUER_KEY_SCOPE')
    key=Ed25519PrivateKey.from_private_bytes(demo_submission._decode(value['private_key_base64url'],32))
    require(hashlib.sha256(key.public_key().public_bytes_raw()).hexdigest()==current_key(policy,registry,now_ms())['public_key_sha256'],
            'ISSUER_PRIVATE_KEY_MISMATCH')
    return key


def _result(root,registry,value):
    _state_custody(root,registry['issuer_uid']);directory=root/'results';count=size=0
    for path in directory.iterdir():
        info=path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid==registry['issuer_uid'] and
                info.st_nlink==1 and not info.st_mode&0o077,'ISSUER_RESULT_CUSTODY')
        count+=1;size+=info.st_size
        require(count<16000 and size<64*1024*1024,'ISSUER_RESULT_BUDGET')
    return _retain(directory,value)


def run(*,source_commit,input_fd=None,output=None,purpose='submission'):
    previous=os.umask(0o077);root=registry=None;request_hash=None;phase='CONFIGURATION'
    grant_recorded=False
    channel=sys.stdout if output is None else output
    try:
        require(purpose in ('submission','cancellation'),'ISSUER_PURPOSE')
        root,registry,policy=configuration(source_commit)
        phase='PRIVATE_HANDOFF';fd=sys.stdin.fileno() if input_fd is None else input_fd
        private_pipe(fd);private_pipe(channel.fileno())
        raw=read_frame(fd,maximum=MAX_INPUT_BYTES,deadline=time.monotonic()+15)
        request_hash=hashlib.sha256(raw).hexdigest();handoff=load_json_without_duplicate_keys(raw)
        phase='POLICY_REVIEW'
        with closing(IssuerStore(root/'issuance.sqlite3')) as store:
            at=now_ms()
            if purpose=='submission':plan=review_submission(handoff,policy=policy,registry=registry,now_ms=at)
            else:plan=review_cancellation(handoff,policy=policy,registry=registry,history=store.snapshot(),now_ms=at)
            phase='SIGNING';key=load_key(registry,policy)
            result=_issue(plan,policy=policy,registry=registry,key=key,store=store,now_ms=at,clock_ms=now_ms)
            grant_recorded=True
            del key
            phase='FINAL_CONFIGURATION'
            latest_root,latest_registry,latest_policy=configuration(source_commit)
            require((latest_root,latest_registry,latest_policy)==(root,registry,policy),'ISSUER_CONFIGURATION_CHANGED')
            phase='RESULT_PERSISTENCE'
            _result(root,registry,{'schema':'DEMO_ISSUER_ATTEMPT_V1','source_commit':source_commit,
                'observed_at_ms':now_ms(),'purpose':purpose,'request_sha256':request_hash,
                'proposal_sha256':plan['proposal_sha256'],'status':'GRANT_RECORDED','phase':phase,
                'approval_id':result['envelope']['payload']['approval_id'],'submission_enabled':False,'economic_authority':False})
            phase='GRANT_DELIVERY'
            print(json.dumps(result,sort_keys=True,separators=(',',':')),file=channel,flush=True)
        return 0
    except Exception:
        # Signing may have committed even if the helper failed on its return
        # path. Never infer an unspent reservation from an absent stdout frame.
        value={'schema':'DEMO_ISSUER_ATTEMPT_V1',
            'source_commit':source_commit if type(source_commit) is str and re.fullmatch('[a-f0-9]{40}',source_commit) else None,
            'observed_at_ms':now_ms(),'purpose':purpose if purpose in ('submission','cancellation') else None,
            'request_sha256':request_hash,'status':'GRANT_DELIVERY_UNCONFIRMED' if phase in
                ('SIGNING','FINAL_CONFIGURATION','RESULT_PERSISTENCE','GRANT_DELIVERY') else 'NOT_ISSUED',
            'phase':phase,'grant_recorded':True if grant_recorded else None if phase=='SIGNING' else False,
            'submission_enabled':False,'economic_authority':False}
        persisted=False
        if root is not None and registry is not None:
            try:_result(root,registry,value);persisted=True
            except Exception:pass
        try:print(json.dumps({**value,'record_persisted':persisted},sort_keys=True,separators=(',',':')),file=channel,flush=True)
        except (OSError,ValueError):pass
        return 3
    finally:os.umask(previous)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--purpose',choices=('submission','cancellation'),default='submission')
    args=parser.parse_args(argv)
    return run(source_commit=args.source_commit,purpose=args.purpose)


if __name__=='__main__':raise SystemExit(main())
