"""Fixed root-owned public administration material, separate from ENABLE.

This reader establishes custody and exact bytes, never current authorization.
The issuer independently rebuilds the subject against its original database.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from services.governance.json_integrity import load_json_without_duplicate_keys as decode

PUBLIC_ROOT = Path('/etc/quantos/demo-gold-continuity')
MANIFEST_SCHEMA = 'DEMO_GOLD_CONTINUITY_PUBLIC_MANIFEST_V1'
COMPLETE_SCHEMA = 'DEMO_GOLD_CONTINUITY_PUBLIC_COMPLETE_V1'
DOCUMENTS = {
    'GROUP_CONTINUITY': frozenset(('previous_authorization','template','registry','profile','pilot',
        'ancestry','runtime','definition','dispatcher')),
    'DELEGATION_V2_SIGN': frozenset(('template','registry','profile','pilot','runtime','definition',
        'dispatcher','continuity_event','delegation_intent')),
}
DOCUMENT_MAXIMUM = 131072
OPERATION_MAXIMUM = 1048576
MAX_OPERATIONS = 4096


def require(condition, reason):
    if not condition: raise ValueError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def digest(value): return hashlib.sha256(canonical(value)).hexdigest()


def _parent_metadata(info):
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022
        and info.st_mode&stat.S_IXOTH,
        'CONTINUITY_PUBLIC_PARENT_CUSTODY')


def _metadata(info, *, directory, maximum=DOCUMENT_MAXIMUM):
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
        and info.st_uid==info.st_gid==0 and stat.S_IMODE(info.st_mode)==(0o755 if directory else 0o444),
        'CONTINUITY_PUBLIC_CUSTODY')
    if not directory:
        require(info.st_nlink==1 and 0<info.st_size<=maximum, 'CONTINUITY_PUBLIC_FILE_BUDGET')


def _same(before, after):
    require(all(getattr(before,n)==getattr(after,n) for n in
        ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode','st_uid','st_gid','st_nlink')),
        'CONTINUITY_PUBLIC_CHANGED')


def _same_directory(before, after):
    require(all(getattr(before,n)==getattr(after,n) for n in
        ('st_dev','st_ino','st_mode','st_uid','st_gid')), 'CONTINUITY_PUBLIC_CHANGED')


def _open_root():
    require(PUBLIC_ROOT.is_absolute() and '..' not in PUBLIC_ROOT.parts, 'CONTINUITY_PUBLIC_PATH')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_CLOEXEC)
    try:
        _parent_metadata(os.fstat(fd))
        for part in PUBLIC_ROOT.parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=fd)
            os.close(fd);fd=child;_parent_metadata(os.fstat(fd))
        _metadata(os.fstat(fd),directory=True)
        return fd
    except BaseException:
        os.close(fd);raise


@contextmanager
def public_lease(*, exclusive=False):
    fd=_open_root()
    try:
        before=os.fstat(fd)
        try: fcntl.flock(fd,(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('CONTINUITY_PUBLIC_BUSY') from None
        _same_directory(before,PUBLIC_ROOT.lstat())
        yield fd
        _same_directory(before,os.fstat(fd));_same_directory(before,PUBLIC_ROOT.lstat())
        _metadata(os.fstat(fd),directory=True)
    finally: os.close(fd)


def _read_at(fd, name, *, maximum):
    child=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,dir_fd=fd)
    try:
        before=os.fstat(child);_metadata(before,directory=False,maximum=maximum)
        chunks=[];size=0
        while size<=maximum:
            block=os.read(child,min(8192,maximum+1-size))
            if not block: break
            chunks.append(block);size+=len(block)
        after=os.fstat(child);_metadata(after,directory=False,maximum=maximum);_same(before,after)
        require(size==before.st_size, 'CONTINUITY_PUBLIC_CHANGED')
        _same(after,os.stat(name,dir_fd=fd,follow_symlinks=False))
        raw=b''.join(chunks);value=decode(raw)
        require(canonical(value)==raw, 'CONTINUITY_PUBLIC_CANONICAL')
        return raw,value
    finally: os.close(child)


def _fields(value, names):
    require(type(value) is dict and set(value)==set(names.split()), 'CONTINUITY_PUBLIC_FIELDS')


def _load_locked(root_fd, operation_sha256, *, source_commit, purpose):
    names=os.listdir(root_fd)
    require(len(names)<=MAX_OPERATIONS and all(re.fullmatch('[a-f0-9]{64}',name) for name in names),
        'CONTINUITY_PUBLIC_OPERATION_SCOPE')
    fd=os.open(operation_sha256,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=root_fd)
    try:
        before=os.fstat(fd);_metadata(before,directory=True)
        filenames={'operation.json',*(name.replace('_','-')+'.json' for name in DOCUMENTS[purpose])}
        expected=filenames|{'manifest.json','complete.json'}
        require(set(os.listdir(fd))==expected, 'CONTINUITY_PUBLIC_INCOMPLETE')
        original={}
        for name in sorted(expected):
            maximum=4096 if name=='complete.json' else 65536 if name=='manifest.json' else DOCUMENT_MAXIMUM
            original[name]=_read_at(fd,name,maximum=maximum)
        require(sum(len(pair[0]) for pair in original.values())<=OPERATION_MAXIMUM, 'CONTINUITY_PUBLIC_TOTAL_BUDGET')
        manifest=original['manifest.json'][1];complete=original['complete.json'][1]
        _fields(manifest,'schema source_commit purpose operation_sha256 files submission_enabled economic_authority')
        require(manifest==dict(schema=MANIFEST_SCHEMA,source_commit=source_commit,purpose=purpose,
            operation_sha256=operation_sha256,files={name:hashlib.sha256(original[name][0]).hexdigest() for name in filenames},
            submission_enabled=False,economic_authority=False), 'CONTINUITY_PUBLIC_MANIFEST')
        require(complete==dict(schema=COMPLETE_SCHEMA,source_commit=source_commit,purpose=purpose,
            operation_sha256=operation_sha256,manifest_sha256=digest(manifest),
            submission_enabled=False,economic_authority=False), 'CONTINUITY_PUBLIC_INCOMPLETE')
        operation=original['operation.json'][1];_fields(operation,'subject confirmation')
        documents={name:original[name.replace('_','-')+'.json'][1] for name in DOCUMENTS[purpose]}
        require(digest(operation)==operation_sha256 and operation['subject']['source_commit']==source_commit
            and operation['subject']['purpose']==purpose
            and operation['subject']['documents_sha256']=={name:digest(value) for name,value in documents.items()},
            'CONTINUITY_PUBLIC_OPERATION_BINDING')
        for name,(raw,_) in original.items():
            maximum=4096 if name=='complete.json' else 65536 if name=='manifest.json' else DOCUMENT_MAXIMUM
            require(_read_at(fd,name,maximum=maximum)[0]==raw, 'CONTINUITY_PUBLIC_CHANGED')
        require(set(os.listdir(fd))==expected, 'CONTINUITY_PUBLIC_CHANGED')
        _same(before,os.fstat(fd));_same(before,os.stat(operation_sha256,dir_fd=root_fd,follow_symlinks=False))
        return dict(operation=operation,documents=documents,manifest_sha256=digest(manifest))
    finally: os.close(fd)


def load_operation(operation_sha256, *, source_commit, purpose, registry, role):
    require(type(operation_sha256) is str and re.fullmatch('[a-f0-9]{64}',operation_sha256)
        and type(source_commit) is str and re.fullmatch('[a-f0-9]{40}',source_commit)
        and purpose in DOCUMENTS, 'CONTINUITY_PUBLIC_INPUT')
    require(role in ('controller','issuer'), 'CONTINUITY_PUBLIC_ROLE')
    if role=='controller': require(os.geteuid()==0,'CONTINUITY_PUBLIC_ROLE_IDENTITY')
    else:
        uid=registry['issuer_uid']
        require(os.geteuid()==os.getegid()==uid and set(os.getgroups())<={uid},'CONTINUITY_PUBLIC_ROLE_IDENTITY')
    with public_lease() as fd:
        return _load_locked(fd,operation_sha256,source_commit=source_commit,purpose=purpose)
