"""Read current sealed observation reports independently of completed job snapshots."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import time
from fastapi import APIRouter
from quantos.runtime_paths import runtime_state_root
from services.governance.json_integrity import load_json_without_duplicate_keys

router=APIRouter()


def _read(path: Path,*,max_bytes: int=4_000_000) -> dict:
    if type(max_bytes) is not int or not 1<=max_bytes<=16_000_000:
        raise ValueError('bounded observation read required')
    if path.is_symlink() or path.stat().st_size>max_bytes:
        raise ValueError('unsafe observation artifact')
    with path.open('rb') as stream:raw=stream.read(max_bytes+1)
    if len(raw)>max_bytes:raise ValueError('observation artifact grew beyond limit')
    data=load_json_without_duplicate_keys(raw)
    if not isinstance(data,dict):
        raise ValueError('invalid observation artifact')
    expected=hashlib.sha256(json.dumps({k:v for k,v in data.items() if k!='result_hash'},sort_keys=True,allow_nan=False).encode()).hexdigest()
    if data.get('result_hash')!=expected:
        raise ValueError('observation artifact hash mismatch')
    return data


def forward_payload(root: Path, *, now_ms: int) -> dict:
    observations=[];errors=[];schedule=None
    base=root/'data/cache/btc_forward'
    try:
        if base.is_symlink():
            raise ValueError('unsafe observation directory')
        for attempt in range(1,1001):
            folder=base/str(attempt)
            if not (folder/'status.json').exists():
                continue
            try:
                if folder.is_symlink():
                    raise ValueError('unsafe attempt directory')
                result=_read(folder/'status.json');freeze=_read(folder/'freeze.json')
                if (result.get('schema')!='QUANTOS_BTC_FORWARD_OBSERVATION_V1'
                    or result.get('attempt')!=attempt or result.get('freeze_hash')!=freeze['result_hash']
                    or result.get('authoritative') is not False or result.get('account_pnl_computed') is not False
                    or type(result.get('observed_at_ms')) is not int or not 0<=result['observed_at_ms']<=now_ms):
                    raise ValueError('observation identity or clock mismatch')
                observations.append(result)
            except (OSError,ValueError,KeyError,TypeError):
                errors.append({'attempt':attempt,'reason':'CORRUPT_OR_UNREADABLE'})
        path=root/'data/cache/btc_forward_schedule/status.json'
        if path.exists():
            schedule=_read(path)
            if schedule.get('schema')!='QUANTOS_BTC_OBSERVATION_SCHEDULE_V1' or type(schedule.get('observed_at_ms')) is not int or not 0<=schedule['observed_at_ms']<=now_ms:
                raise ValueError('invalid schedule report')
            schedule={**schedule,'fresh':now_ms-schedule['observed_at_ms']<=35*60000}
    except (OSError,ValueError,TypeError,KeyError):
        schedule=None;errors.append({'attempt':None,'reason':'SCHEDULE_UNREADABLE'})
    return dict(source='BACKEND_COMPUTED_SNAPSHOT',read_only=True,api_version='0.1.0',
                data_provenance='REAL_FORWARD_OBSERVATION_DIAGNOSTIC',snapshot_status='REAL' if observations or schedule else 'NO_SNAPSHOT',
                authoritative=False,as_of_ms=now_ms,observations=observations,schedule=schedule,errors=errors)


@router.get('/api/v1/console/forward-dashboard')
def forward_dashboard():
    return forward_payload(runtime_state_root(),now_ms=time.time_ns()//1000000)
