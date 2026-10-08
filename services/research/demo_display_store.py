"""Bounded content-addressed display files; schemas supply their own validator."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from services.governance.json_integrity import load_json_without_duplicate_keys
MAX_BYTES=65536

def _hash(value):
    if type(value) is not str or re.fullmatch('[a-f0-9]{64}',value) is None:raise ValueError('DISPLAY_STATUS_HASH')

def save(directory,raw,*,validator):
    validator(raw);directory=Path(directory);directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    data=(json.dumps(raw,sort_keys=True,indent=2)+'\n').encode()
    if len(data)>MAX_BYTES:raise ValueError('DISPLAY_STATUS_SIZE')
    path=directory/(hashlib.sha256(data).hexdigest()+'.json')
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
    except FileExistsError:
        if path.is_symlink() or path.read_bytes()!=data:raise ValueError('DISPLAY_STATUS_COLLISION') from None
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=directory,delete=False) as f:
            temporary=Path(f.name);json.dump({'sha256':path.stem},f);f.flush();os.fsync(f.fileno())
        os.replace(temporary,directory/'latest.json')
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return path


def load_latest(directory,*,validator):
    directory=Path(directory);pointer=directory/'latest.json'
    if pointer.is_symlink():raise ValueError('DISPLAY_STATUS_SYMLINK')
    with pointer.open('rb') as f:body=f.read(1025)
    if len(body)>1024:raise ValueError('DISPLAY_STATUS_POINTER_SIZE')
    value=load_json_without_duplicate_keys(body)
    if type(value) is not dict or set(value)!={'sha256'}:raise ValueError('DISPLAY_STATUS_POINTER')
    _hash(value['sha256']);path=directory/(value['sha256']+'.json')
    if path.is_symlink():raise ValueError('DISPLAY_STATUS_SYMLINK')
    with path.open('rb') as f:body=f.read(MAX_BYTES+1)
    if len(body)>MAX_BYTES or hashlib.sha256(body).hexdigest()!=value['sha256']:raise ValueError('DISPLAY_STATUS_DIGEST')
    return validator(load_json_without_duplicate_keys(body))
