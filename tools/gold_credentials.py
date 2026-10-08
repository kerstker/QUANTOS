"""Operator-only private-file storage, explicitly chosen by the owner.

Plaintext, protected by Unix ownership/mode, outside the repository. Never run
store or load on real credentials as an agent; tests use a temporary directory.
"""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
import warnings

DEFAULT_ROOT=Path.home()/'.local/share/quantos/credentials'
SCHEMA='GOLD_READONLY_CREDENTIALS_V1'


def validate(key,secret):
    if any(not isinstance(v,str) or re.fullmatch('[A-Za-z0-9]{16,256}',v) is None for v in (key,secret)):
        raise ValueError('INVALID_CREDENTIAL_FORMAT')
    return key,secret


def prompt_credentials():
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        return validate(getpass.getpass('Binance read-only API key (hidden): '),
                        getpass.getpass('Binance API secret (hidden): '))


def _root(root,*,create=False):
    root=Path(root).absolute()
    if root.resolve()!=root:raise ValueError('LINKED_CREDENTIAL_DIRECTORY')
    if create:root.mkdir(mode=0o700,parents=True,exist_ok=True)
    if root.exists():
        info=root.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700:
            raise ValueError('PRIVATE_CREDENTIAL_DIRECTORY_REQUIRED')
    return root


def _read(path):
    try:fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except OSError:raise ValueError('CREDENTIAL_FILE_UNAVAILABLE') from None
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600
                or info.st_nlink!=1 or info.st_size>4096):raise ValueError('PRIVATE_CREDENTIAL_FILE_REQUIRED')
        raw=os.read(fd,4097)
    finally:os.close(fd)
    try:
        def pairs(items):
            result={}
            for k,v in items:
                if k in result:raise ValueError('DUPLICATE_KEY')
                result[k]=v
            return result
        value=json.loads(raw,object_pairs_hook=pairs)
        if set(value)!={'schema','api_key','api_secret'} or value['schema']!=SCHEMA:raise ValueError('INVALID_SCHEMA')
        return validate(value['api_key'],value['api_secret'])
    except (ValueError,TypeError,KeyError,UnicodeError):raise ValueError('INVALID_STORED_CREDENTIALS') from None


def save_credentials(key,secret,*,root=None,replace=False):
    validate(key,secret);root=_root(root or DEFAULT_ROOT,create=True)
    path=root/'credentials.json'
    if path.is_symlink():raise ValueError('LINKED_CREDENTIAL_FILE')
    if path.exists() and not replace:raise ValueError('CREDENTIALS_ALREADY_SAVED')
    if path.exists():_read(path)  # Do not silently replace an unsafe file.
    temporary=root/('.credentials-'+uuid.uuid4().hex)
    raw=json.dumps({'schema':SCHEMA,'api_key':key,'api_secret':secret},separators=(',',':')).encode()
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        if replace:os.replace(temporary,path)
        else:os.link(temporary,path)  # Atomic no-clobber publication.
    finally:
        if temporary.exists():temporary.unlink()
    directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)


def get_credentials(*,root=None,source='auto',prompt=None):
    if source not in ('auto','prompt'):raise ValueError('INVALID_CREDENTIAL_SOURCE')
    prompt=prompt or prompt_credentials
    if source=='prompt':return validate(*prompt())
    root=_root(root or DEFAULT_ROOT)
    path=root/'credentials.json'
    if path.exists() or path.is_symlink():return _read(path)
    return validate(*prompt())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operator-store',action='store_true')
    parser.add_argument('--replace',action='store_true')
    args=parser.parse_args(argv)
    try:
        if not args.operator_store:
            if args.replace:raise ValueError('STORE_REQUIRED')
            report={'status':'READY_FOR_OPERATOR_STORE','credential_access':False,'network_used':False}
        else:
            if not sys.stdin.isatty() or not sys.stderr.isatty():raise ValueError('INTERACTIVE_TERMINAL_REQUIRED')
            # Resolve path and reject existing storage before asking for new keys.
            root=_root(DEFAULT_ROOT)
            if (root/'credentials.json').exists() and not args.replace:raise ValueError('CREDENTIALS_ALREADY_SAVED')
            key,secret=prompt_credentials()
            try:save_credentials(key,secret,root=root,replace=args.replace)
            finally:del key,secret
            report={'status':'CREDENTIALS_SAVED','storage':'PRIVATE_PLAINTEXT_FILE','network_used':False,
                    'exchange_permissions_verified':False,'submission_enabled':False}
        print(json.dumps(report,sort_keys=True));return 0
    except (Exception,KeyboardInterrupt) as exc:
        allowed={'CREDENTIALS_ALREADY_SAVED','PRIVATE_CREDENTIAL_DIRECTORY_REQUIRED','INTERACTIVE_TERMINAL_REQUIRED',
                 'INVALID_CREDENTIAL_FORMAT','LINKED_CREDENTIAL_DIRECTORY','STORE_REQUIRED'}
        reason=str(exc) if type(exc) is ValueError and str(exc) in allowed else 'CREDENTIAL_STORE_FAILED'
        print(json.dumps({'status':'FAILED','reason':reason,'submission_enabled':False}));return 2


if __name__=='__main__':raise SystemExit(main())
