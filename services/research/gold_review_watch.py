"""Change identity for retained public review evidence, not a receipt verifier."""
import hashlib
import os
from pathlib import Path
import stat

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.ai_factory_store import digest

REGISTRY=Path(__file__).resolve().parents[2]/'config/artifact_receipt_authority_registry.json'


def _read(path):
    if path.resolve()!=path.absolute():raise ValueError('unsafe review input')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as handle:
        before=os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=1_000_000:
            raise ValueError('invalid review input')
        raw=handle.read(before.st_size+1)
        after=os.fstat(handle.fileno())
        if len(raw)!=before.st_size or (before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_ino,after.st_size,after.st_mtime_ns):
            raise ValueError('review input changed')
        return raw


def observation(trial,registry,*,now):
    if type(now) is not int or now<0:raise ValueError('invalid review time')
    inputs={};phases=[]
    for name,path in (('registry',registry),('envelope',trial/'independent-envelope.json')):
        try:
            raw=_read(path);inputs[name]=hashlib.sha256(raw).hexdigest()
        except FileNotFoundError:
            inputs[name]='MISSING';continue
        except (OSError,ValueError):
            inputs[name]='UNAVAILABLE';continue
        if name=='envelope':
            try:
                receipt=load_json_without_duplicate_keys(raw)['receipt']
                for field in ('verified_at_ms','transparency_checkpoint_at_ms','revocation_checked_at_ms'):
                    at=receipt[field]
                    if type(at) is not int:raise ValueError('invalid receipt clock')
                    phases.append('FUTURE' if now<at else 'STALE' if now-at>86400000 else 'CURRENT')
            except (ValueError,TypeError,KeyError):phases=['INVALID']
    return digest({'inputs':inputs,'clock_phases':phases})
