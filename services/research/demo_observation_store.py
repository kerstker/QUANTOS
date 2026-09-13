"""Lossless daily observation segments; append history before publishing a head.

This is storage for observations, never a trading or source-authority ledger.
Legacy immutable JSON remains readable and is never removed by this module.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import zlib

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research import demo_display_store

SCHEMA='DEMO_OBSERVATION_SEGMENT_V1'
POINTER_SCHEMA='DEMO_OBSERVATION_POINTER_V1'
MAX_RAW_BYTES=65536
MAX_RECORDS=10000
MAX_SEGMENTS=3660
MAX_SEGMENT_BYTES=512*1024**2
DAY_MS=86400000
APPLICATION_ID=0x514f4253
SEGMENT=re.compile(r'day-[0-9]{8}\.sqlite3')
HASH=re.compile(r'[a-f0-9]{64}')
TRIGGERS={
    'observations_no_update':'CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations BEGIN SELECT RAISE(ABORT, \'immutable observations\'); END',
    'observations_no_delete':'CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations BEGIN SELECT RAISE(ABORT, \'immutable observations\'); END',
    'metadata_no_update':'CREATE TRIGGER metadata_no_update BEFORE UPDATE ON metadata BEGIN SELECT RAISE(ABORT, \'immutable metadata\'); END',
    'metadata_no_delete':'CREATE TRIGGER metadata_no_delete BEFORE DELETE ON metadata BEGIN SELECT RAISE(ABORT, \'immutable metadata\'); END',
}


def require(value,reason):
    if not value:raise ValueError(reason)


def segment_name(observed_at_ms):
    require(type(observed_at_ms) is int and 0<=observed_at_ms<DAY_MS*100000000,'OBSERVATION_TIME')
    return f'day-{observed_at_ms//DAY_MS:08d}.sqlite3'


def _directory(path):
    path=Path(path).absolute()
    require(hasattr(os,'getuid'),'OBSERVATION_POSIX_CUSTODY_REQUIRED')
    require(not any(p.is_symlink() for p in (path,*path.parents)),'OBSERVATION_DIRECTORY_SYMLINK')
    info=path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid() and not info.st_mode&0o022,'OBSERVATION_DIRECTORY_CUSTODY')
    return path


def _file(path,maximum):
    info=path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_uid==os.getuid() and
            not info.st_mode&0o022 and info.st_size<=maximum,'OBSERVATION_FILE_CUSTODY')
    return info


def _read(path,maximum):
    before=_file(path,maximum)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as handle:
        opened=os.fstat(handle.fileno());data=handle.read(maximum+1);after=os.fstat(handle.fileno())
    fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
    require(len(data)==before.st_size and len(data)<=maximum and
            all(getattr(before,k)==getattr(opened,k)==getattr(after,k) for k in fields),'OBSERVATION_READ_CHANGED')
    return data


def validate_pointer(raw):
    require(type(raw) is dict and set(raw)=={'schema','segment','sha256'} and raw['schema']==POINTER_SCHEMA and
            type(raw['segment']) is str and SEGMENT.fullmatch(raw['segment']) and
            type(raw['sha256']) is str and HASH.fullmatch(raw['sha256']),'OBSERVATION_POINTER')
    return raw


def _schema(db,path,*,initialize):
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if initialize and not tables:
        require(db.execute('PRAGMA application_id').fetchone()[0]==0 and
                not db.execute('SELECT name FROM sqlite_master').fetchall(),'OBSERVATION_EMPTY_DATABASE')
        db.execute(f'PRAGMA application_id={APPLICATION_ID}')
        db.execute('PRAGMA user_version=1')
        db.execute('CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), schema TEXT NOT NULL, segment TEXT NOT NULL)')
        db.execute('CREATE TABLE observations (sequence INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, observed_at_ms INTEGER NOT NULL, raw_size INTEGER NOT NULL, payload_zlib BLOB NOT NULL)')
        db.execute('INSERT INTO metadata VALUES(1,?,?)',(SCHEMA,path.name))
        for sql in TRIGGERS.values():db.execute(sql)
        tables={'metadata','observations'}
    require(tables=={'metadata','observations'} and db.execute('PRAGMA application_id').fetchone()[0]==APPLICATION_ID and
            db.execute('PRAGMA user_version').fetchone()[0]==1,'OBSERVATION_DATABASE_SCHEMA')
    require(db.execute('SELECT id,schema,segment FROM metadata').fetchall()==[(1,SCHEMA,path.name)],'OBSERVATION_DATABASE_SCOPE')
    require(dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger'"))==TRIGGERS,'OBSERVATION_APPEND_ONLY_SCHEMA')


@contextmanager
def _connection(path,*,write=False):
    path=Path(path).absolute();_directory(path.parent)
    require(SEGMENT.fullmatch(path.name),'OBSERVATION_SEGMENT_NAME')
    if write:
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600);os.close(fd)
        except FileExistsError:pass
    before=_file(path,MAX_SEGMENT_BYTES)
    db=sqlite3.connect(path.as_uri()+('?mode=rw' if write else '?mode=ro'),uri=True,timeout=10 if write else 2)
    try:
        db.execute('PRAGMA trusted_schema=OFF')
        if write:
            db.execute('PRAGMA synchronous=FULL')
            # Rollback journals avoid read-side WAL creation and keep each day one file.
            require(db.execute('PRAGMA journal_mode').fetchone()[0]=='delete','OBSERVATION_JOURNAL_MODE')
            page=db.execute('PRAGMA page_size').fetchone()[0]
            db.execute(f'PRAGMA max_page_count={MAX_SEGMENT_BYTES//page}')
            db.execute('BEGIN IMMEDIATE')
        else:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            require(db.execute('PRAGMA journal_mode').fetchone()[0]=='delete','OBSERVATION_JOURNAL_MODE')
        _schema(db,path,initialize=write)
        current=_file(path,MAX_SEGMENT_BYTES)
        require((before.st_dev,before.st_ino)==(current.st_dev,current.st_ino),'OBSERVATION_DATABASE_REPLACED')
        yield db
        if write:db.commit()
    except BaseException:
        db.rollback();raise
    finally:
        db.close()


def _decode(row,segment,validator):
    digest,observed,size,packed=row
    require(type(digest) is str and HASH.fullmatch(digest) and segment_name(observed)==segment and
            type(size) is int and 0<size<=MAX_RAW_BYTES and type(packed) is bytes and
            0<len(packed)<=MAX_RAW_BYTES+1024,'OBSERVATION_RECORD_FIELDS')
    decoder=zlib.decompressobj()
    try:body=decoder.decompress(packed,MAX_RAW_BYTES+1)
    except zlib.error as error:raise ValueError('OBSERVATION_COMPRESSION') from error
    require(len(body)==size and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail and
            hashlib.sha256(body).hexdigest()==digest,'OBSERVATION_RECORD_DIGEST')
    raw=validator(load_json_without_duplicate_keys(body))
    require(type(raw) is dict and type(raw.get('observed_at_ms')) is int and raw['observed_at_ms']==observed,'OBSERVATION_RECORD_TIME')
    return raw


def _publish_pointer(directory,pointer):
    fd,name=tempfile.mkstemp(prefix='.observation-head-',dir=directory)
    try:
        with os.fdopen(fd,'w') as f:json.dump(pointer,f,sort_keys=True);f.flush();os.fsync(f.fileno())
        os.replace(name,directory/'latest.json')
        parent=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(parent)
        finally:os.close(parent)
    finally:
        if os.path.exists(name):os.unlink(name)


@contextmanager
def _lock(archive):
    # Optional Linux archive writing must not prevent legacy readers importing.
    import fcntl
    path=archive/'append.lock'
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        _file(path,0);deadline=time.monotonic()+10
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                require(time.monotonic()<deadline,'OBSERVATION_STORE_BUSY');time.sleep(.01)
        yield
    finally:os.close(fd)


def save(directory,raw,*,validator):
    require(os.name=='posix' and hasattr(os,'getuid'),'OBSERVATION_POSIX_CUSTODY_REQUIRED')
    validator(raw);name=segment_name(raw.get('observed_at_ms'))
    body=(json.dumps(raw,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    require(0<len(body)<=MAX_RAW_BYTES,'OBSERVATION_RAW_SIZE')
    digest=hashlib.sha256(body).hexdigest();directory=Path(directory).absolute()
    directory.mkdir(mode=0o700,parents=True,exist_ok=True);_directory(directory)
    archive=directory/'archive';archive.mkdir(mode=0o700,exist_ok=True);_directory(archive)
    with _lock(archive):
        return _save_locked(directory,archive,name,body,digest,raw,validator)


def _save_locked(directory,archive,name,body,digest,raw,validator):
    segments=[]
    for path in archive.iterdir():
        if path.name=='append.lock':continue
        if path.name.endswith('-journal'):continue
        require(SEGMENT.fullmatch(path.name),'OBSERVATION_ARCHIVE_LAYOUT');segments.append(path.name)
        require(len(segments)<=MAX_SEGMENTS,'OBSERVATION_SEGMENT_CAPACITY')
    require(name in segments or len(segments)<MAX_SEGMENTS,'OBSERVATION_SEGMENT_CAPACITY')
    require(shutil.disk_usage(archive).free>=8*1024**2,'OBSERVATION_DISK_RESERVE')
    path=archive/name
    with _connection(path,write=True) as db:
        old=db.execute('SELECT sha256,observed_at_ms,raw_size,payload_zlib FROM observations WHERE sha256=?',(digest,)).fetchone()
        if old is not None:
            require(_decode(old,name,validator)==raw,'OBSERVATION_COLLISION')
        else:
            count,sequence=db.execute('SELECT count(*),coalesce(max(sequence),0) FROM observations').fetchone()
            require(count==sequence and count<MAX_RECORDS,'OBSERVATION_RECORD_CAPACITY')
            db.execute('INSERT INTO observations VALUES(?,?,?,?,?)',(count+1,digest,raw['observed_at_ms'],len(body),zlib.compress(body)))
    parent=os.open(archive,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(parent)
    finally:os.close(parent)
    pointer=directory/'latest.json'
    if pointer.exists() or pointer.is_symlink():
        previous=load_latest(directory,validator=validator)
        if previous['observed_at_ms']>raw['observed_at_ms'] or previous==raw:return path
    _publish_pointer(directory,dict(schema=POINTER_SCHEMA,segment=name,sha256=digest))
    return path


def load_pointer(directory,pointer,*,validator):
    directory=_directory(directory);validate_pointer(pointer)
    path=directory/'archive'/pointer['segment']
    with _connection(path) as db:
        row=db.execute('SELECT sha256,observed_at_ms,raw_size,payload_zlib FROM observations WHERE sha256=?',(pointer['sha256'],)).fetchone()
        require(row is not None,'OBSERVATION_HEAD_MISSING')
        return _decode(row,path.name,validator)


def load_latest(directory,*,validator):
    directory=Path(directory)
    require(not (directory/'latest.json').is_symlink(),'OBSERVATION_POINTER_SYMLINK')
    with (directory/'latest.json').open('rb') as handle:body=handle.read(1025)
    require(len(body)<=1024,'OBSERVATION_POINTER_SIZE')
    pointer=load_json_without_duplicate_keys(body)
    if type(pointer) is dict and set(pointer)=={'sha256'}:
        return demo_display_store.load_latest(directory,validator=validator)
    # Archived pointers require the stricter source-owner custody contract.
    pointer=load_json_without_duplicate_keys(_read(_directory(directory)/'latest.json',1024))
    return load_pointer(directory,pointer,validator=validator)


def verify_segment(path,*,validator):
    path=Path(path);count=0;chain='0'*64;first=last=None
    with _connection(path) as db:
        require(db.execute('PRAGMA integrity_check').fetchall()==[('ok',)],'OBSERVATION_SQLITE_INTEGRITY')
        rows=db.execute('SELECT sequence,sha256,observed_at_ms,raw_size,payload_zlib FROM observations ORDER BY sequence')
        for row in rows:
            count+=1;require(row[0]==count and count<=MAX_RECORDS,'OBSERVATION_SEQUENCE')
            _decode(row[1:],path.name,validator)
            first=row[2] if first is None else min(first,row[2]);last=row[2] if last is None else max(last,row[2])
            chain=hashlib.sha256((chain+':'+str(count)+':'+row[1]).encode()).hexdigest()
    return dict(schema=SCHEMA,segment=path.name,records=count,content_chain_sha256=chain,
        first_observed_at_ms=first,last_observed_at_ms=last,source_verified=False,submission_enabled=False,economic_authority=False)
