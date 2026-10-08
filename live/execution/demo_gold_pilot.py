"""Additional, executor-owned pilot stop state; never order authorization.

An operator-installed configuration pins the opening equity and finite window.
There is no enrollment, reset, credentials, sender, or alternative cash ledger.
Callers must validate account/origin provenance through the original paths first.
"""
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import stat

from live.execution import demo_position_context as position
from live.execution.demo_batch_risk import _digest
from services.execution.perpetual_ledger_v2 import ledger_decimal_context
from services.governance.json_integrity import load_json_without_duplicate_keys as decode


def require(value,reason):
    if not value:raise ValueError('GOLD_PILOT_'+reason)


def read(path,*,maximum=32768):
    require(type(maximum) is int and 0<maximum<=16_000_000,'RECORD_BUDGET')
    position._custody(path,directory=False)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno());raw=stream.read(maximum+1);after=os.fstat(stream.fileno())
    require(stat.S_ISREG(before.st_mode) and len(raw)==before.st_size and len(raw)<=maximum
        and all(getattr(before,k)==getattr(after,k) for k in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')),
        'RECORD_CHANGED')
    position._custody(path,directory=False)
    return decode(raw)


def retain_once(path,value,*,maximum=32768):
    raw=(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
    require(type(maximum) is int and 0<len(raw)<=maximum<=16_000_000,'RECORD_BUDGET')
    try:fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    except FileExistsError:return read(path,maximum=maximum)
    with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return read(path,maximum=maximum)


def config_check(value):
    require(type(value) is dict and set(value)==set('schema environment symbol account_ref_sha256 '
        'opening_origin_sha256 initial_equity started_at_ms ends_at_ms max_loss_fraction submission_enabled'.split()),'CONFIGURATION_FIELDS')
    require(value['schema']=='GOLD_DEMO_PILOT_CONTROL_V1' and value['environment']=='BINANCE_USDM_DEMO'
        and value['symbol']=='XAUUSDT' and value['max_loss_fraction']=='0.05'
        and value['submission_enabled'] is False,'CONFIGURATION_SCOPE')
    for key in ('account_ref_sha256','opening_origin_sha256'):
        require(type(value[key]) is str and re.fullmatch('[a-f0-9]{64}',value[key]) is not None,'CONFIGURATION_DIGEST')
    require(type(value['started_at_ms']) is int and type(value['ends_at_ms']) is int
        and 0<value['started_at_ms']<value['ends_at_ms']<=2**63-1
        and value['ends_at_ms']-value['started_at_ms']==86400000,'CONFIGURATION_WINDOW')
    require(type(value['initial_equity']) is str and len(value['initial_equity'])<=100,'CONFIGURATION_EQUITY')
    initial=Decimal(value['initial_equity'])
    require(initial.is_finite() and initial>0,'CONFIGURATION_EQUITY')
    return initial


@ledger_decimal_context()
def observe(account,*,now_ms,origin_sha256,state_root=None):
    root=position._ROOT if state_root is None else Path(state_root)
    area=root/'gold-pilot'
    if not area.exists() and not area.is_symlink():return None
    require(root==position._ROOT,'STATE_ROOT')
    position._custody(area,directory=True)
    config=read(area/'configuration.json');initial=config_check(config)
    request_path=root/'gold-pilot-request.json'
    if request_path.exists() or request_path.is_symlink():
        from live.execution.demo_gold_pilot_enrollment import completed_configuration
        require(completed_configuration(area,request=read(request_path))==config,'ENROLLMENT_BINDING_CHANGED')
    else:
        intent=area/'enrollment-intent.json'
        require(not intent.exists() and not intent.is_symlink(),'ENROLLMENT_REQUEST_REQUIRED')
    require(type(now_ms) is int and config['started_at_ms']<=now_ms<=2**63-1,'OBSERVATION_TIME')
    require(account['account_ref_sha256']==config['account_ref_sha256'],'ACCOUNT_CHANGED')
    require(type(account['equity']) is str and len(account['equity'])<=100,'OBSERVATION_EQUITY')
    equity=Decimal(account['equity']);require(equity.is_finite(),'OBSERVATION_EQUITY')
    require(type(origin_sha256) is str and re.fullmatch('[a-f0-9]{64}',origin_sha256) is not None,'OBSERVATION_SOURCE')
    binding=dict(schema='GOLD_DEMO_PILOT_BINDING_V1',configuration_sha256=_digest(config))
    require(retain_once(area/'binding.json',binding)==binding,'CONFIGURATION_CHANGED')
    path=area/'stopped.json'
    if path.exists() or path.is_symlink():
        result=read(path)
    else:
        reason='LOSS_LIMIT' if equity<=initial*Decimal('0.95') else 'DURATION_COMPLETE' if now_ms>=config['ends_at_ms'] else None
        if reason is None:
            return dict(status='ACTIVE',configuration_sha256=binding['configuration_sha256'],submission_enabled=False)
        result=retain_once(path,dict(schema='GOLD_DEMO_PILOT_STOP_V1',status='STOPPED',reason=reason,
            configuration_sha256=binding['configuration_sha256'],account_ref_sha256=config['account_ref_sha256'],
            observed_at_ms=now_ms,observed_equity=account['equity'],origin_sha256=origin_sha256,submission_enabled=False))
    return validate_stop(result,config=config,now_ms=now_ms)


@ledger_decimal_context()
def validate_stop(result,*,config,now_ms):
    """Validate an original stop without observing, persisting, or resetting it."""
    initial=config_check(config)
    require(type(now_ms) is int and config['started_at_ms']<=now_ms<=2**63-1,'OBSERVATION_TIME')
    require(type(result) is dict and set(result)==set('schema status reason configuration_sha256 account_ref_sha256 '
        'observed_at_ms observed_equity origin_sha256 submission_enabled'.split()),'STOP_RECORD')
    require(result['schema']=='GOLD_DEMO_PILOT_STOP_V1' and result['status']=='STOPPED'
        and result['reason'] in ('LOSS_LIMIT','DURATION_COMPLETE') and result['submission_enabled'] is False
        and result['configuration_sha256']==_digest(config)
        and result['account_ref_sha256']==config['account_ref_sha256']
        and type(result['observed_at_ms']) is int and config['started_at_ms']<=result['observed_at_ms']<=now_ms,'STOP_RECORD')
    require(type(result['observed_equity']) is str and len(result['observed_equity'])<=100
        and type(result['origin_sha256']) is str and re.fullmatch('[a-f0-9]{64}',result['origin_sha256']) is not None,'STOP_RECORD')
    stopped_equity=Decimal(result['observed_equity'])
    require(stopped_equity.is_finite() and (stopped_equity<=initial*Decimal('0.95')
        if result['reason']=='LOSS_LIMIT' else result['observed_at_ms']>=config['ends_at_ms']),'STOP_RECORD')
    return result


def require_opening_allowed(orders,observation):
    if observation is None:return
    require(type(observation) is dict and observation.get('status') in ('ACTIVE','STOPPED')
        and observation.get('submission_enabled') is False
        and type(observation.get('configuration_sha256')) is str
        and re.fullmatch('[a-f0-9]{64}',observation['configuration_sha256']) is not None,'OBSERVATION_BINDING')
    if observation['status']=='STOPPED':
        require(all(order['reduceOnly'] is True for order in orders),'STOPPED')
