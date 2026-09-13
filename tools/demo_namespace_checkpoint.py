"""Encrypted Demo namespace checkpoints with isolated restore, never activation.

Run capture as the source owner so SQLite read-side files keep their ownership.
The caller supplies an existing backup passphrase; no credential is generated.
Namespaces are independent snapshots, not an atomic cross-process checkpoint.
"""
import argparse
from contextlib import closing,contextmanager
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import sqlite3
import stat
import tempfile
import threading
import time
import zipfile

from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.l2_backup import _snapshot_source_file as snapshot_file,_sqlite_integrity,resolve_backup_passphrase,_default_passphrase_file
from tools.live_backup import encrypt_file,decrypt_file,_check_passphrase_strength

SCHEMA='DEMO_NAMESPACE_CHECKPOINT_V1'
MAX_FILES=100000
MAX_BYTES=20*1024**3
MAX_JSON_BYTES=16*1024*1024
EXECUTOR_DATABASES={'dispatch.sqlite3','recovery.sqlite3','oms.sqlite3','order-events.sqlite3','user-stream.sqlite3'}
EXECUTOR_DIRECTORIES={'evidence','execution-origins','resident-recovery','receipt-reconciliation',
    'observation-views','stream-capture-results','market-inputs-BTCUSDC','market-inputs-BTCUSDT','request-preparation-results'}
CONTROLLER_DIRECTORIES={'requests','claims','results','tasks','task-claims','task-results'}
RESEARCH_DIRECTORIES={'binance_demo_account_captures','binance_demo_cash_windows','binance_demo_execution_origins',
    'binance_demo_executor_observation','binance_demo_private_checks','binance_demo_statements',
    'binance_demo_market_inputs','binance_demo_issuer_status','binance_demo_resident_status',
    'binance_demo_oms_status','binance_demo_cancel_status'}
EPHEMERAL={'observation.lock','origin-capture.lock','user-stream.lock','binance_demo_user_stream.lock'}
PROTECTED_ROOTS=tuple(Path(p) for p in ('/srv/quantos/state','/opt/quantos/releases',
    '/var/lib/quantos-demo-executor','/var/lib/quantos-demo-issuer','/var/lib/quantos-demo-controller'))


def require(value,reason):
    if not value:raise ValueError(reason)


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def safe_relative(value):
    require(type(value) is str and value and '\\' not in value and '\0' not in value,'CHECKPOINT_PATH')
    path=PurePosixPath(value)
    require(not path.is_absolute() and '..' not in path.parts and path.as_posix()==value and path.parts,'CHECKPOINT_PATH')
    return path


def scope(namespace):
    if namespace=='executor':return EXECUTOR_DATABASES|{'kill-switch.sqlite3','cancellations.sqlite3'},EXECUTOR_DIRECTORIES,EXECUTOR_DATABASES
    if namespace=='issuer':return {'issuance.sqlite3'},{'results','views'},{'issuance.sqlite3'}
    if namespace=='controller':return {'installation.json'},CONTROLLER_DIRECTORIES,set()
    if namespace=='research':return {'binance_demo_user_stream.db','binance_demo_readiness.json'},RESEARCH_DIRECTORIES,{'binance_demo_user_stream.db'}
    raise ValueError('CHECKPOINT_NAMESPACE')


def allowed(relative,namespace):
    path=safe_relative(relative);files,directories,_=scope(namespace)
    return (len(path.parts)==1 and relative in files or len(path.parts)>1 and path.parts[0] in directories) and path.suffix in ('.db','.sqlite3','.json')


def data_clock(path):
    result=[]
    for suffix in ('','-wal','-journal'):
        member=Path(str(path)+suffix)
        try:info=member.lstat()
        except FileNotFoundError:result.append(None);continue
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'CHECKPOINT_DATABASE_CUSTODY')
        # A SQLite RO connection can create an empty WAL beside a WAL-mode
        # database. Zero-byte sidecars carry no frames or committed data.
        if suffix and info.st_size==0:result.append(None);continue
        result.append((info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns))
    return result


def inventory(root,namespace):
    root=Path(root).absolute();files,directories,required=scope(namespace)
    require(root.is_dir() and not root.is_symlink(),'CHECKPOINT_SOURCE_MISSING')
    require(not any(p.is_symlink() for p in root.parents),'CHECKPOINT_SOURCE_PARENT')
    uid=root.stat().st_uid;result={};pending=[root];count=0;directory_names=[]
    while pending:
        directory=pending.pop()
        for path in directory.iterdir():
            relative=path.relative_to(root).as_posix();first=PurePosixPath(relative).parts[0]
            if namespace=='research' and not first.startswith('binance_demo'):continue
            info=path.lstat();count+=1;require(count<=MAX_FILES+10000,'CHECKPOINT_INVENTORY_BUDGET')
            require(info.st_uid==uid and not info.st_mode&0o022 and not stat.S_ISLNK(info.st_mode),'CHECKPOINT_SOURCE_CUSTODY')
            if stat.S_ISDIR(info.st_mode):
                require(first in directories,'CHECKPOINT_UNDECLARED_DIRECTORY');pending.append(path);directory_names.append(relative);continue
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1,'CHECKPOINT_SOURCE_FILE')
            if relative in EPHEMERAL:continue
            if any(relative.endswith(suffix) for suffix in ('-wal','-shm','-journal')):
                suffix=next(s for s in ('-wal','-shm','-journal') if relative.endswith(s))
                require(allowed(relative[:-len(suffix)],namespace) and (root/relative[:-len(suffix)]).is_file(),'CHECKPOINT_ORPHAN_SIDECAR')
                continue
            require(allowed(relative,namespace),'CHECKPOINT_UNDECLARED_FILE')
            require(path.suffix!='.json' or info.st_size<=MAX_JSON_BYTES,'CHECKPOINT_JSON_SIZE')
            result[relative]={'size':info.st_size,'inode':info.st_ino,'device':info.st_dev,
                'mtime_ns':info.st_mtime_ns,'json_sha256':sha(path) if path.suffix=='.json' else None,
                'data_clock':None if path.suffix=='.json' or relative in ('user-stream.sqlite3','binance_demo_user_stream.db') else data_clock(path)}
    require(required<=set(result) and result and len(result)<=MAX_FILES and sum(v['size'] for v in result.values())<=MAX_BYTES,'CHECKPOINT_REQUIRED_OR_BUDGET')
    if namespace=='controller':require(all((root/name).is_dir() for name in CONTROLLER_DIRECTORIES),'CHECKPOINT_CONTROLLER_LAYOUT')
    return dict(sorted(result.items())),sorted(directory_names)


@contextmanager
def readonly_object(factory,path):
    # Original legacy constructors can migrate schemas. Bind their read-only
    # methods to an explicit SQLite RO connection instead; no migration runs.
    obj=object.__new__(factory);obj._conn=sqlite3.connect(Path(path).absolute().as_uri()+'?mode=ro',uri=True)
    obj._conn.row_factory=sqlite3.Row;obj._lock=threading.RLock()
    obj._conn.execute('PRAGMA query_only=ON')
    try:yield obj
    finally:obj._conn.close()


def verify_state(root,namespace):
    from services.research.demo_user_stream import UserStreamJournal
    result={'namespace':namespace}
    if namespace in ('executor','research'):
        path=root/('user-stream.sqlite3' if namespace=='executor' else 'binance_demo_user_stream.db')
        with UserStreamJournal(path,read_only=True) as stream:
            checked=stream.verify();account=stream.meta['account_ref_sha256']
            sessions=stream.db.execute("SELECT count(*) FROM stream_records WHERE kind='SESSION_START'").fetchone()[0]
        # Backup includes empty logs and records with a bad/future observation
        # clock. Chain preservation never admits those records as current data.
        result.update(account_ref_sha256=account,stream_records=checked['records'],stream_chain_verified=True,
            stream_head_sha256=checked['head_sha256'],stream_session_present=sessions>0,stream_current_observation_verified=False)
    if namespace=='executor':
        from live.execution.demo_dispatch_store import DemoDispatchStore
        from live.execution.order_recovery import SqliteOrderRecoveryStore
        from live.execution.order_event_store import SqliteOrderEventStore
        from live.execution.position_ledger import LiveLedger
        with closing(DemoDispatchStore(root/'dispatch.sqlite3',read_only=True)) as store:
            result.update(reservations=len(store.snapshot()),wire_records=len(store.wire_snapshot()))
        with readonly_object(SqliteOrderRecoveryStore,root/'recovery.sqlite3') as store:
            store._verify_locked();records=store.latest_records()
            result['pending_recoveries']=sum(r.state.value!='RESOLVED' for r in records)
        with readonly_object(SqliteOrderEventStore,root/'order-events.sqlite3') as store:
            store._verify_locked();result['order_events']=len(store.events())
        with readonly_object(LiveLedger,root/'oms.sqlite3') as store:
            store._require_schema();result.update(orders=len(store.orders()),fills=len(store.fills()))
        result['kill_history_verified']=False
        if (root/'kill-switch.sqlite3').exists():
            from services.risk.pre_trade.kill_switch_store import KillSwitchStateStore
            with closing(KillSwitchStateStore(str(root/'kill-switch.sqlite3'),read_only=True)) as store:
                checked=store.verified_snapshot();result.update(kill_history_verified=True,kill_record_sha256=checked['record_hash'])
        if (root/'cancellations.sqlite3').exists():
            from live.execution.demo_cancel_store import DemoCancelStore
            with closing(DemoCancelStore(root/'cancellations.sqlite3',read_only=True)) as store:result['cancellation_records']=len(store.snapshot())
    if namespace=='issuer':
        from live.execution.demo_issuer_store import IssuerStore
        with closing(IssuerStore(root/'issuance.sqlite3',read_only=True)) as store:result['issuer_records']=len(store.snapshot())
    if namespace=='controller':
        for folder,field in (('tasks',None),('requests',None),('claims','request_sha256'),('task-claims','task_sha256')):
            paths=list((root/folder).glob('*.json'))
            for path in paths:
                require(re.fullmatch('[a-f0-9]{64}',path.stem) is not None,'CHECKPOINT_CONTROLLER_ID')
                if field is None:require(sha(path)==path.stem,'CHECKPOINT_CONTROLLER_BYTES')
                else:require(load_json_without_duplicate_keys(path.read_bytes()).get(field)==path.stem,'CHECKPOINT_CONTROLLER_CLAIM')
            result[folder.replace('-','_')]=len(paths)
    return result


def verify_files(root,entries,namespace):
    require(type(entries) is dict and 0<len(entries)<=MAX_FILES,'CHECKPOINT_ENTRIES')
    for name,entry in entries.items():
        require(allowed(name,namespace) and type(entry) is dict and set(entry)=={'sha256','size','snapshot_method'},'CHECKPOINT_ENTRY')
        require(type(entry['size']) is int and 0<=entry['size']<=MAX_BYTES and
            type(entry['sha256']) is str and re.fullmatch('[a-f0-9]{64}',entry['sha256']) is not None,'CHECKPOINT_ENTRY_SIZE_OR_HASH')
        path=root/name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size==entry['size'] and sha(path)==entry['sha256'],'CHECKPOINT_CONTENT')
        if path.suffix in ('.db','.sqlite3'):
            require(entry['snapshot_method']=='SQLITE_ONLINE_BACKUP','CHECKPOINT_SQLITE_SNAPSHOT_REQUIRED');_sqlite_integrity(path)
        else:
            require(entry['snapshot_method']=='STABLE_FILE_READ','CHECKPOINT_JSON_SNAPSHOT_REQUIRED')
            require(entry['size']<=MAX_JSON_BYTES,'CHECKPOINT_JSON_SIZE')
            load_json_without_duplicate_keys(path.read_bytes())
    require(sum(v['size'] for v in entries.values())<=MAX_BYTES,'CHECKPOINT_TOTAL_SIZE')
    _,_,required=scope(namespace);require(required<=set(entries),'CHECKPOINT_REQUIRED_MISSING')
    return verify_state(root,namespace)


def manifest_check(manifest):
    require(type(manifest) is dict and set(manifest)==set('schema namespace source_commit source_uid captured_from_ms captured_until_ms entries directories state'.split()) and manifest['schema']==SCHEMA,'CHECKPOINT_MANIFEST')
    _,directories,_=scope(manifest['namespace'])
    require(type(manifest['source_commit']) is str and re.fullmatch('[a-f0-9]{40}',manifest['source_commit']) is not None,'CHECKPOINT_SOURCE_COMMIT')
    require(type(manifest['source_uid']) is int and 0<=manifest['source_uid']<2**31,'CHECKPOINT_SOURCE_UID')
    require(all(type(manifest[k]) is int and 0<manifest[k]<=2**53-1 for k in ('captured_from_ms','captured_until_ms')) and
        manifest['captured_from_ms']<=manifest['captured_until_ms'],'CHECKPOINT_CAPTURE_CLOCK')
    names=manifest['directories']
    require(type(names) is list and len(names)<=10000 and all(type(n) is str for n in names) and names==sorted(set(names)),'CHECKPOINT_DIRECTORIES')
    require(all(safe_relative(n).parts[0] in directories for n in names),'CHECKPOINT_DIRECTORY_SCOPE')
    require(type(manifest['entries']) is dict and set(names).isdisjoint(manifest['entries']) and type(manifest['state']) is dict,'CHECKPOINT_LAYOUT')
    if manifest['namespace']=='controller':require(CONTROLLER_DIRECTORIES<=set(names),'CHECKPOINT_CONTROLLER_LAYOUT')
    return manifest


def restore_checkpoint(archive,destination,*,passphrase,expected_sha256):
    archive=Path(archive);destination=Path(destination).absolute()
    require(not destination.exists() and not destination.is_symlink() and not any(p.is_symlink() for p in destination.parents),'CHECKPOINT_NEW_RESTORE_DESTINATION')
    require(not any(destination==p or p in destination.parents for p in PROTECTED_ROOTS),'CHECKPOINT_ISOLATED_RESTORE_ONLY')
    require(not archive.is_symlink() and sha(archive)==expected_sha256,'CHECKPOINT_ENCRYPTED_DIGEST')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.demo-restore-',dir=destination.parent) as temporary:
        stage=Path(temporary);plain=stage/'archive.zip';tree=stage/'tree';tree.mkdir(mode=0o700)
        decrypt_file(str(archive),str(plain),passphrase)
        with zipfile.ZipFile(plain) as z:
            names=z.namelist();require(len(names)==len(set(names)) and len(names)<=MAX_FILES+1,'CHECKPOINT_ZIP_INVENTORY')
            require('checkpoint-manifest.json' in names and z.getinfo('checkpoint-manifest.json').file_size<=32*1024*1024,'CHECKPOINT_MANIFEST_SIZE')
            manifest=manifest_check(load_json_without_duplicate_keys(z.read('checkpoint-manifest.json')))
            namespace=manifest['namespace'];scope(namespace);entries=manifest['entries']
            require(type(entries) is dict and set(names)==set(entries)|{'checkpoint-manifest.json'},'CHECKPOINT_MEMBER_SET')
            require(sum(z.getinfo(n).file_size for n in names)<=MAX_BYTES+32*1024*1024,'CHECKPOINT_ZIP_SIZE')
            for name in manifest['directories']:(tree/name).mkdir(parents=True,mode=0o700,exist_ok=True)
            for name in entries:
                require(allowed(name,namespace),'CHECKPOINT_MEMBER_PATH');info=z.getinfo(name)
                require(not name.endswith('.json') or info.file_size<=MAX_JSON_BYTES,'CHECKPOINT_JSON_SIZE')
                require(not info.is_dir() and stat.S_IFMT(info.external_attr>>16) in (0,stat.S_IFREG),'CHECKPOINT_MEMBER_TYPE')
                target=tree/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with z.open(name) as src,target.open('xb') as dst:
                    target.chmod(0o600)
                    for block in iter(lambda:src.read(1024*1024),b''):dst.write(block)
                    dst.flush();os.fsync(dst.fileno())
            checked=verify_files(tree,entries,namespace)
            require(checked==manifest['state'],'CHECKPOINT_STATE_CHANGED')
            # Verification cannot silently migrate restored records.
            require(all(sha(tree/name)==entry['sha256'] for name,entry in entries.items()),'CHECKPOINT_RESTORE_MUTATED')
        os.rename(tree,destination)
    return {'status':'RESTORED_ISOLATED','namespace':namespace,'files':len(entries),'state':checked,
        'source_commit':manifest['source_commit'],'source_uid':manifest['source_uid'],'source_verified':False,
        'cross_namespace_atomic':False,'submission_enabled':False,'economic_authority':False,'production_restore_performed':False}


def create_checkpoint(root,destination,*,namespace,source_commit,passphrase,capture_only=False):
    root=Path(root).absolute();destination=Path(destination).absolute()
    require(type(capture_only) is bool,'CHECKPOINT_CAPTURE_MODE')
    if not capture_only:_check_passphrase_strength(passphrase)
    require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}',source_commit) is not None,'CHECKPOINT_SOURCE_COMMIT')
    require(not destination.exists() and not destination.is_symlink() and root!=destination and root not in destination.parents,'CHECKPOINT_NEW_ARCHIVE_DESTINATION')
    require(not any(p.is_symlink() for p in destination.parents),'CHECKPOINT_DESTINATION_PARENT')
    started=time.time_ns()//1000000
    require(root.is_dir() and not root.is_symlink(),'CHECKPOINT_SOURCE_MISSING')
    require(root.stat().st_uid==os.getuid(),'CHECKPOINT_SOURCE_OWNER_REQUIRED')
    before,directories=inventory(root,namespace)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.demo-checkpoint-',dir=destination.parent) as temporary:
        stage=Path(temporary);copies=stage/'files';scratch=stage/'scratch';copies.mkdir(mode=0o700);scratch.mkdir(mode=0o700);entries={}
        for name in before:
            path=root/name;snapshot,method,_=snapshot_file(path,scratch)
            target=copies/name;target.parent.mkdir(parents=True,exist_ok=True,mode=0o700);os.rename(snapshot,target);target.chmod(0o600)
            entries[name]={'sha256':sha(target),'size':target.stat().st_size,'snapshot_method':method}
        after,later_directories=inventory(root,namespace)
        require(directories==later_directories and set(before)==set(after) and all(before[n]['inode']==after[n]['inode'] and before[n]['device']==after[n]['device'] and
            before[n]['json_sha256']==after[n]['json_sha256'] and before[n]['data_clock']==after[n]['data_clock'] for n in before),'CHECKPOINT_SOURCE_CHANGED')
        for name in directories:(copies/name).mkdir(parents=True,mode=0o700,exist_ok=True)
        checked=verify_files(copies,entries,namespace)
        require(all(sha(copies/n)==v['sha256'] for n,v in entries.items()),'CHECKPOINT_VERIFIER_MUTATED')
        manifest={'schema':SCHEMA,'namespace':namespace,'source_commit':source_commit,'source_uid':root.stat().st_uid,
            'captured_from_ms':started,'captured_until_ms':time.time_ns()//1000000,'entries':entries,'directories':directories,'state':checked}
        manifest_check(manifest)
        archive=stage/'checkpoint.zip'
        with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
            for name in entries:z.write(copies/name,name)
            z.writestr('checkpoint-manifest.json',canonical(manifest))
        if capture_only:
            archive.chmod(0o600)
            with archive.open('rb') as f:os.fsync(f.fileno())
            os.link(archive,destination)
            fd=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)
            return {'status':'CAPTURED_PRIVATE_NAMESPACE_ARCHIVE','namespace':namespace,'files':len(entries),
                'sha256':sha(archive),'source_commit':source_commit,'source_verified':False,'state':checked,
                'encrypted':False,'cross_namespace_atomic':False,'submission_enabled':False,
                'economic_authority':False,'production_restore_performed':False}
        encrypted=stage/'checkpoint.enc';encrypt_file(str(archive),str(encrypted),passphrase);encrypted.chmod(0o600)
        digest=sha(encrypted);restored=restore_checkpoint(encrypted,stage/'verified',passphrase=passphrase,expected_sha256=digest)
        require(restored['state']==checked,'CHECKPOINT_ROUNDTRIP_STATE')
        # Publish new bytes only. No old checkpoint or original log is deleted.
        os.link(encrypted,destination)
        fd=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    return {'status':'VERIFIED_ENCRYPTED_NAMESPACE_CHECKPOINT','namespace':namespace,'files':len(entries),'sha256':digest,
        'source_commit':source_commit,'source_verified':False,'state':checked,'encrypted':True,
        'cross_namespace_atomic':False,'submission_enabled':False,'economic_authority':False,'production_restore_performed':False}


def seal_checkpoint(archive,destination,*,passphrase,expected_sha256):
    """Separate secret-holding process seals an exact private capture.

    No source database is opened here. A new private copy is checked before
    encryption; the original capture is never rewritten or deleted.
    """
    _check_passphrase_strength(passphrase);archive=Path(archive);destination=Path(destination).absolute()
    require(type(expected_sha256) is str and re.fullmatch('[a-f0-9]{64}',expected_sha256) is not None,'CHECKPOINT_CAPTURE_DIGEST')
    require(not destination.exists() and not destination.is_symlink() and not any(p.is_symlink() for p in destination.parents),'CHECKPOINT_NEW_ARCHIVE_DESTINATION')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.demo-seal-',dir=destination.parent) as temporary:
        stage=Path(temporary);plain=stage/'capture.zip';fd=os.open(archive,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        with os.fdopen(fd,'rb') as src,plain.open('xb') as dst:
            plain.chmod(0o600);before=os.fstat(src.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<before.st_size<=MAX_BYTES+64*1024*1024,'CHECKPOINT_CAPTURE_FILE')
            size=0
            for block in iter(lambda:src.read(1024*1024),b''):
                size+=len(block);require(size<=before.st_size,'CHECKPOINT_CAPTURE_CHANGED');dst.write(block)
            after=os.fstat(src.fileno())
            require(size==before.st_size and all(getattr(before,k)==getattr(after,k) for k in
                ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')),'CHECKPOINT_CAPTURE_CHANGED')
            dst.flush();os.fsync(dst.fileno())
        require(sha(plain)==expected_sha256,'CHECKPOINT_CAPTURE_DIGEST')
        encrypted=stage/'checkpoint.enc';encrypt_file(str(plain),str(encrypted),passphrase);encrypted.chmod(0o600)
        digest=sha(encrypted);checked=restore_checkpoint(encrypted,stage/'verified',passphrase=passphrase,expected_sha256=digest)
        os.link(encrypted,destination)
        fd=os.open(destination.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    return dict(checked,status='VERIFIED_ENCRYPTED_NAMESPACE_CHECKPOINT',encrypted=True,sha256=digest,
        capture_sha256=expected_sha256,source_verified=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    for command in ('create','capture'):
        create=commands.add_parser(command);create.add_argument('--namespace',required=True,choices=('executor','issuer','controller','research'))
        create.add_argument('--source-dir',type=Path,required=True);create.add_argument('--source-commit',required=True);create.add_argument('--output',type=Path,required=True)
    for command in ('restore','seal'):
        restore=commands.add_parser(command);restore.add_argument('--archive',type=Path,required=True)
        restore.add_argument('--expected-sha256',required=True);restore.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv);previous=os.umask(0o077)
    try:
        passphrase=None if args.command=='capture' else resolve_backup_passphrase(environment_passphrase=os.environ.get('QUANTOS_BACKUP_PASSPHRASE'),passphrase_file=_default_passphrase_file())
        if args.command in ('create','capture'):result=create_checkpoint(args.source_dir,args.output,namespace=args.namespace,source_commit=args.source_commit,
            passphrase=passphrase,capture_only=args.command=='capture')
        elif args.command=='seal':result=seal_checkpoint(args.archive,args.output,passphrase=passphrase,expected_sha256=args.expected_sha256)
        else:result=restore_checkpoint(args.archive,args.output,passphrase=passphrase,expected_sha256=args.expected_sha256)
        del passphrase
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        print(json.dumps({'status':'CHECKPOINT_NOT_COMPLETED','command':args.command,
            'production_restore_performed':False,'submission_enabled':False,'economic_authority':False}));return 3
    finally:os.umask(previous)


if __name__=='__main__':raise SystemExit(main())
