"""Immutable private connection summaries; no credentials or venue operations."""
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from quantos.contracts.binance_demo_private import DemoPrivateCheck


def save_report(directory: Path, report: DemoPrivateCheck):
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    raw = (report.model_dump_json(indent=2) + '\n').encode()
    digest = hashlib.sha256(raw).hexdigest()
    target = directory / (digest + '.json')
    try:
        with target.open('xb') as f:
            f.write(raw); f.flush(); os.fsync(f.fileno())
    except FileExistsError:
        if target.is_symlink() or target.read_bytes() != raw:
            raise ValueError('existing private report mismatch') from None
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=directory, delete=False) as f:
            temporary = Path(f.name)
            json.dump({'sha256': digest}, f)
            f.flush(); os.fsync(f.fileno())
        os.replace(temporary, directory/'latest.json')
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def load_report(directory: Path):
    pointer = directory/'latest.json'
    if pointer.is_symlink(): raise ValueError('invalid report pointer')
    with pointer.open('rb') as f: raw_pointer = f.read(1025)
    if len(raw_pointer) > 1024: raise ValueError('oversized report pointer')
    value = json.loads(raw_pointer)
    if (not isinstance(value, dict) or set(value) != {'sha256'} or
            not isinstance(value['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', value['sha256'])):
        raise ValueError('invalid report digest')
    target = directory/(value['sha256']+'.json')
    if target.is_symlink(): raise ValueError('invalid report source')
    with target.open('rb') as f: raw = f.read(16385)
    if len(raw) > 16384 or hashlib.sha256(raw).hexdigest() != value['sha256']:
        raise ValueError('private report content mismatch')
    return DemoPrivateCheck.model_validate_json(raw)
