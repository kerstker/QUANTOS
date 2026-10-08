"""Import supplied Demo operating materials into the existing protected stack.

Root only, accepted source only, initial provisioning only. This importer never
creates an approval, signing key, kill-switch state, order ID or broker secret.
The executor registry is published last. Partial installs remain inspectable;
they are never erased or treated as permission to reset original stores.
"""
import argparse
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from live.execution.demo_authority import _read_root_owned_json,_require_protected
from live.execution.demo_batch_risk import _digest,_fields
from live.execution.demo_issuer_policy import current_key,require,amount
from live.execution.demo_request_inputs import profile_check,task_check
from live.execution.demo_issuer_store import IssuerStore
from live.execution.demo_cancel_store import DemoCancelStore
from live.execution.demo_session_inputs import _limits,_mandate
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore,kill_switch_state_hash
from services.validation.demo_submission import validate_registry,registry_sha256,_decode
from tools.binance_demo_market_inputs import source_hash as market_source_hash
from tools.demo_issuer import verify_source

EXECUTOR=Path('/var/lib/quantos-demo-executor')
ISSUER=Path('/var/lib/quantos-demo-issuer')
CONFIG=Path('/etc/quantos/demo-issuer')
CONTROLLER=Path('/var/lib/quantos-demo-controller')
REGISTRY=Path('/etc/quantos/demo-submission/authority.json')
BASE_FILES={'registry.json','policy.json','profile.json','signing-key.json','kill-switch.sqlite3'}


def now_ms():return time.time_ns()//1000000
def sha(body):return hashlib.sha256(body).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def private_metadata(info,*,maximum):
    require(stat.S_ISREG(info.st_mode) and info.st_uid==info.st_gid==0 and not info.st_mode&0o077 and
        info.st_nlink==1 and 0<info.st_size<=maximum,'OPERATING_PRIVATE_MATERIAL_CUSTODY')


def read_private(path,*,maximum):
    """Check every opened descriptor, including secret metadata before reading."""
    path=Path(path);require(path.is_absolute() and '..' not in path.parts,'OPERATING_MATERIAL_PATH')
    current=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC);leaf=None
    try:
        _require_protected(os.fstat(current),directory=True)
        for name in path.parts[1:-1]:
            child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=current)
            os.close(current);current=child;_require_protected(os.fstat(current),directory=True)
        leaf=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=current)
        before=os.fstat(leaf);private_metadata(before,maximum=maximum);body=bytearray()
        while len(body)<=maximum:
            chunk=os.read(leaf,min(65536,maximum+1-len(body)))
            if not chunk:break
            body.extend(chunk)
        after=os.fstat(leaf);private_metadata(after,maximum=maximum)
        require(len(body)==before.st_size and all(getattr(before,k)==getattr(after,k) for k in
            ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')),'OPERATING_MATERIAL_CHANGED')
        return bytes(body)
    finally:
        if leaf is not None:os.close(leaf)
        os.close(current)


def manifest_check(manifest):
    _fields(manifest,'schema source_commit expected_registry_sha256 files tasks')
    portfolio=manifest['schema']=='DEMO_PORTFOLIO_OPERATING_BUNDLE_V1'
    from live.execution.demo_gold_budget_scope import BUNDLE_SCHEMA, BUNDLE_SCHEMA_V2
    from tools.gold_strategy_budget_materials import SCOPE_FILES
    scope_only=manifest['schema'] in (BUNDLE_SCHEMA,BUNDLE_SCHEMA_V2)
    require(portfolio or scope_only or manifest['schema']=='DEMO_OPERATING_BUNDLE_V1','OPERATING_SCHEMA')
    require(type(manifest['tasks']) is list and (len(manifest['tasks'])==0 if portfolio or scope_only else 1<=len(manifest['tasks'])<=20) and
        all(type(v) is str and re.fullmatch('[a-f0-9]{64}',v) for v in manifest['tasks']) and
        len(set(manifest['tasks']))==len(manifest['tasks']),'OPERATING_TASK_IDENTITIES')
    require(type(manifest['files']) is dict and set(manifest['files'])==BASE_FILES|{v+'.json' for v in manifest['tasks']}|
        ({'portfolio-runtime.json'} if portfolio else SCOPE_FILES if scope_only else set()),'OPERATING_FILE_SET')
    for digest in [manifest['expected_registry_sha256'],*manifest['files'].values()]:
        require(type(digest) is str and re.fullmatch('[a-f0-9]{64}',digest),'OPERATING_FILE_HASH')
    return manifest


def read_materials(directory,manifest_sha256):
    raw=read_private(directory/'manifest.json',maximum=65536)
    require(sha(raw)==manifest_sha256,'OPERATING_MANIFEST_DIGEST')
    manifest=manifest_check(load_json_without_duplicate_keys(raw));files={}
    for name,digest in manifest['files'].items():
        body=read_private(directory/name,maximum=64*1024*1024 if name=='kill-switch.sqlite3' else 65536)
        require(sha(body)==digest,'OPERATING_FILE_DIGEST');files[name]=body
    return manifest,files


@ledger_decimal_context()
def validate_materials(*,manifest,files,existing_registry,source_commit,account_ref,now_ms):
    manifest_check(manifest);validate_registry(existing_registry)
    require(manifest['source_commit']==source_commit and manifest['expected_registry_sha256']==registry_sha256(existing_registry),'OPERATING_SOURCE_OR_PREIMAGE')
    require(set(files)==set(manifest['files']) and all(sha(body)==manifest['files'][name] for name,body in files.items()),'OPERATING_FILE_DIGEST')
    documents={name:load_json_without_duplicate_keys(body) for name,body in files.items() if name!='kill-switch.sqlite3'}
    registry=validate_registry(documents['registry.json']);policy=documents['policy.json'];profile=documents['profile.json']
    require({k:v for k,v in registry.items() if k!='keys'}=={k:v for k,v in existing_registry.items() if k!='keys'} and
        registry['keys'][:-1]==existing_registry['keys'] and len(registry['keys'])==len(existing_registry['keys'])+1,'OPERATING_REGISTRY_EXTENSION')
    from live.execution.demo_gold_budget_scope import BUNDLE_SCHEMA, BUNDLE_SCHEMA_V2
    scope_only=manifest['schema'] in (BUNDLE_SCHEMA,BUNDLE_SCHEMA_V2)
    if scope_only:
        if manifest['schema']==BUNDLE_SCHEMA_V2:
            from tools.gold_first_scope_materials import validate_documents
        else:
            from tools.gold_strategy_budget_materials import validate_documents
        key=validate_documents(documents,registry=registry,existing_registry=existing_registry,
            source_commit=source_commit,account_ref=account_ref,now_ms=now_ms,stop_image=files['kill-switch.sqlite3'])
    else:key=current_key(policy,registry,now_ms)
    require(key==registry['keys'][-1] and policy['executor_release_commit']==source_commit and
        policy['account_ref_sha256']==account_ref,'OPERATING_POLICY_SCOPE')
    if not scope_only:
        profile_check(profile,policy,{'source_sha256':market_source_hash(),'symbol':policy['scope']['symbol']},now_ms)
    _limits(profile['native_limits']);_mandate(profile['mandate'])
    secret=documents['signing-key.json'];_fields(secret,'schema authority_id key_id private_key_base64url')
    require(secret['schema']=='DEMO_ISSUER_KEY_V1' and secret['authority_id']==key['authority_id'] and secret['key_id']==key['key_id'],'OPERATING_SIGNING_KEY_IDENTITY')
    private=Ed25519PrivateKey.from_private_bytes(_decode(secret['private_key_base64url'],32))
    require(sha(private.public_key().public_bytes_raw())==key['public_key_sha256'],'OPERATING_SIGNING_KEY_PAIR')
    del private,secret
    try:
        # Validate a private copy of the supplied SQLite image. No bootstrap,
        # sidecar import or source-store write is used to make it readable.
        with tempfile.TemporaryDirectory(prefix='quantos-demo-stop-inspection-') as directory:
            path=Path(directory)/'stop.sqlite3';path.write_bytes(files['kill-switch.sqlite3']);path.chmod(0o600)
            store=KillSwitchStateStore(str(path),read_only=True)
            try:snapshot=store.verified_snapshot()
            finally:store.close()
    except Exception:raise ValueError('OPERATING_STOP_SOURCE_INVALID') from None
    require(max(snapshot['recorded_at'],snapshot['state'].armed_at,snapshot['state'].reset_at)<=now_ms,'OPERATING_STOP_SOURCE_FUTURE')
    if scope_only:require(not snapshot['state'].tripped,'GOLD_SCOPE_STOP_ACTIVE')
    portfolio=documents.get('portfolio-runtime.json')
    if portfolio is not None:
        from tools.demo_portfolio_controller import validate_runtime
        validate_runtime(portfolio,profile,policy,source_commit=source_commit,now_ms=now_ms)
        require(portfolio['kill_switch']==snapshot['state'].model_dump(mode='json'),'OPERATING_PORTFOLIO_STOP_BINDING')
    tasks=[];ids=set()
    for identity in manifest['tasks']:
        task=documents[identity+'.json'];require(_digest(task)==identity,'OPERATING_TASK_DIGEST')
        task_check(task,policy=policy,source_commit=source_commit,now_ms=now_ms)
        require(task['profile']==profile and task['kill_switch']==snapshot['state'].model_dump(mode='json'),'OPERATING_TASK_BINDING')
        for order in task['orders']:
            require(order['newClientOrderId'] not in ids,'OPERATING_DUPLICATE_ORDER_ID');ids.add(order['newClientOrderId'])
        tasks.append(task)
    require(len(tasks)<=policy['max_grants'] and len(ids)<=policy['max_orders'],'OPERATING_TASK_BUDGET')
    notional=sum((amount(order['quantity'])*amount(order['price']) for task in tasks for order in task['orders']),Decimal(0))
    require(notional<=amount(policy['max_total_notional']),'OPERATING_TASK_NOTIONAL_BUDGET')
    return {'registry':registry,'policy':policy,'profile':profile,'tasks':tasks,**({'scope_only':True} if scope_only else {}),**({'portfolio':portfolio} if portfolio is not None else {}),'kill_state':snapshot['state'],
        'kill_record_sha256':snapshot['record_hash'],'public_key_sha256':key['public_key_sha256']}


def directory(path,uid,gid,*,mode=0o700):
    path.mkdir(mode=mode);os.chown(path,uid,gid);os.chmod(path,mode)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def read_account(registry,source_commit):
    # SQLite may create read-side WAL/SHM files. Read under the original executor
    # UID so initial provisioning cannot leave root-owned files in its journal.
    source=Path(__file__).resolve().parents[1];uid=registry['executor_uid']
    result=subprocess.run([sys.executable,'-P','-B','-m','tools.demo_operating_bundle',
        '--account-only','--source-commit',source_commit],user=uid,group=uid,extra_groups=[],
        cwd=source,env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONPATH':str(source),
            'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1'},
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=20,check=False)
    require(result.returncode==0 and re.fullmatch(b'[a-f0-9]{64}\n',result.stdout) is not None,'OPERATING_ACCOUNT_UNAVAILABLE')
    return result.stdout[:-1].decode('ascii')


def write_new(path,body,*,uid=0,gid=0,mode=0o600):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    with os.fdopen(fd,'wb') as handle:
        os.fchmod(handle.fileno(),mode);os.fchown(handle.fileno(),uid,gid)
        handle.write(body);handle.flush();os.fsync(handle.fileno())
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def install(materials,files,*,registry_preimage):
    registry=materials['registry'];executor,issuer=registry['executor_uid'],registry['issuer_uid']
    targets=[CONFIG,ISSUER,CONTROLLER,EXECUTOR/'cancellations.sqlite3',EXECUTOR/'kill-switch.sqlite3']
    # Existing state is a separate continuation/repair action, never reset by
    # repeating the first-install command after an interruption.
    require(all(not p.exists() and not p.is_symlink() for p in targets),'OPERATING_ALREADY_OR_PARTIALLY_INSTALLED')
    require(_read_root_owned_json(REGISTRY)==registry_preimage,'OPERATING_REGISTRY_CHANGED')
    for path in (CONFIG.parent,ISSUER.parent,CONTROLLER.parent):
        for parent in reversed((path,*path.parents)):_require_protected(parent.lstat(),directory=True)
    info=EXECUTOR.lstat();require(stat.S_ISDIR(info.st_mode) and info.st_uid==executor and not info.st_mode&0o077,'OPERATING_EXECUTOR_CUSTODY')
    directory(CONFIG,0,issuer,mode=0o750);directory(ISSUER,issuer,issuer);directory(CONTROLLER,0,0)
    for name in ('results','views'):directory(ISSUER/name,issuer,issuer)
    for name in ('requests','claims','results','tasks','task-claims','task-results'):directory(CONTROLLER/name,0,0)
    write_new(CONFIG/'signing-key.json',files['signing-key.json'],gid=issuer,mode=0o640)
    write_new(CONFIG/'policy.json',files['policy.json'],gid=issuer,mode=0o640)
    write_new(CONFIG/'profile.json',files['profile.json'],gid=issuer,mode=0o640)
    write_new(CONFIG/'authority.json',canonical(registry),gid=issuer,mode=0o640)
    store=IssuerStore(ISSUER/'issuance.sqlite3',create=True);store.close()
    os.chmod(ISSUER/'issuance.sqlite3',0o600);os.chown(ISSUER/'issuance.sqlite3',issuer,issuer)
    cancel=DemoCancelStore(EXECUTOR/'cancellations.sqlite3',create=True);cancel.close()
    os.chmod(EXECUTOR/'cancellations.sqlite3',0o600);os.chown(EXECUTOR/'cancellations.sqlite3',executor,executor)
    write_new(EXECUTOR/'kill-switch.sqlite3',files['kill-switch.sqlite3'],uid=executor,gid=executor)
    for task in materials['tasks']:write_new(CONTROLLER/'tasks'/(_digest(task)+'.json'),canonical(task))
    if 'portfolio' in materials:write_new(CONTROLLER/'portfolio-runtime.json',files['portfolio-runtime.json'])
    # Public registry publication is the final activation step. Other workers
    # see the old registry until every supplied file and empty append log exists.
    require(_read_root_owned_json(REGISTRY)==registry_preimage,'OPERATING_REGISTRY_CHANGED')
    temporary=REGISTRY.parent/('.operating-'+os.urandom(8).hex()+'.json')
    write_new(temporary,canonical(registry),gid=executor,mode=0o640);os.replace(temporary,REGISTRY)
    fd=os.open(REGISTRY.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    require(_read_root_owned_json(REGISTRY)==canonical(registry),'OPERATING_REGISTRY_PUBLICATION')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True);parser.add_argument('--bundle-dir',type=Path)
    parser.add_argument('--manifest-sha256');parser.add_argument('--apply',action='store_true')
    parser.add_argument('--account-only',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args(argv);phase='SOURCE';changed=False;previous=os.umask(0o077)
    try:
        verify_source(args.source_commit)
        if args.account_only:
            require(not args.apply and args.bundle_dir is args.manifest_sha256 is None,'OPERATING_ACCOUNT_ARGUMENTS')
            from live.execution.demo_authority import load_registry_for_executor
            from tools.demo_executor_stream import fixed_account
            registry=load_registry_for_executor();uid=registry['executor_uid']
            require(os.geteuid()==os.getegid()==uid and set(os.getgroups())<={uid},'OPERATING_ACCOUNT_IDENTITY')
            _,account=fixed_account();print(account);return 0
        require(args.bundle_dir is not None and args.manifest_sha256 is not None,'OPERATING_MATERIAL_ARGUMENTS')
        require(os.geteuid()==0,'OPERATING_ROOT_REQUIRED')
        phase='MATERIALS';manifest,files=read_materials(args.bundle_dir,args.manifest_sha256)
        preimage=_read_root_owned_json(REGISTRY);registry=validate_registry(load_json_without_duplicate_keys(preimage))
        account=read_account(registry,args.source_commit)
        materials=validate_materials(manifest=manifest,files=files,existing_registry=registry,
            source_commit=args.source_commit,account_ref=account,now_ms=now_ms())
        if args.apply:
            phase='INSTALLATION';changed=True;install(materials,files,registry_preimage=preimage)
        result={'schema':'DEMO_OPERATING_INSTALL_RESULT_V1','status':'INSTALLED' if args.apply else 'REVIEWED_ONLY',
            'source_commit':args.source_commit,'manifest_sha256':args.manifest_sha256,'observed_at_ms':now_ms(),
            'policy_id':materials['policy']['policy_id'],'public_key_sha256':materials['public_key_sha256'],
            'kill_state_sha256':kill_switch_state_hash(materials['kill_state']),'kill_record_sha256':materials['kill_record_sha256'],
            'task_count':len(materials['tasks']),'services_started':False,'orders_submitted':False,'economic_authority':False}
        if args.apply:write_new(CONTROLLER/'installation.json',canonical(result))
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        print(json.dumps({'schema':'DEMO_OPERATING_INSTALL_RESULT_V1','status':'INSPECT_PARTIAL_INSTALL' if changed else 'NOT_INSTALLED',
            'phase':phase,'services_started':False,'orders_submitted':False,'economic_authority':False}));return 3
    finally:os.umask(previous)


if __name__=='__main__':raise SystemExit(main())
