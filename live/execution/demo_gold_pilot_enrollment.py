"""Prepare a pilot baseline from existing observations and settled executor stores.

Read-only preparation, not installation or source authentication. The controlled
operator must retain and revalidate the captured origin at actual enrollment.
No supplied balance, credentials, approvals, reset, or order submission API.
"""
from decimal import Decimal
from copy import deepcopy
import json
import re

from live.execution import demo_gold_pilot as pilot
from live.execution import demo_position_context as position
from live.execution import demo_market_partition_runtime
from live.execution.demo_batch_risk import _digest
from live.execution.demo_pre_submit import _require_origin, _state
from services.research.demo_execution_origin import GOLD_ACCOUNT_TERMS_SCHEMA

INTENT_MAXIMUM=16_000_000
SCOPE_REQUEST_SCHEMA='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V2'


def _request_check(request,*,source_commit,account):
    scoped=type(request) is dict and request.get('schema')==SCOPE_REQUEST_SCHEMA
    fields=set('schema source_commit account_ref_sha256 duration_seconds max_loss_fraction submission_enabled'.split())
    pilot.require(type(request) is dict and set(request)==fields|({'budget_scope','scope_registry'} if scoped else set()),
        'ENROLLMENT_REQUEST_FIELDS')
    pilot.require(request['schema'] in ('GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',SCOPE_REQUEST_SCHEMA)
        and type(source_commit) is str and re.fullmatch('[a-f0-9]{40}',source_commit) is not None
        and request['source_commit']==source_commit and request['account_ref_sha256']==account
        and type(account) is str and re.fullmatch('[a-f0-9]{64}',account) is not None
        and type(request['duration_seconds']) is int and request['duration_seconds']==86400
        and request['max_loss_fraction']=='0.05' and request['submission_enabled'] is False,
        'ENROLLMENT_REQUEST_SCOPE')
    if scoped:
        from live.execution import demo_gold_budget_scope as scope
        retained=request['budget_scope'];scope.historical_scope_binding(retained,request['scope_registry'])
        pilot.require(retained['executor_release_commit']==source_commit
            and retained['account_ref_sha256']==account,'ENROLLMENT_SCOPE_IDENTITY')


def scope_request(*,scope,registry,source_commit,account_ref_sha256):
    """Retain signed public scope evidence; never execution permission."""
    request=dict(schema=SCOPE_REQUEST_SCHEMA,source_commit=source_commit,account_ref_sha256=account_ref_sha256,
        duration_seconds=86400,max_loss_fraction='0.05',submission_enabled=False,
        budget_scope=deepcopy(scope),scope_registry=deepcopy(registry))
    _request_check(request,source_commit=source_commit,account=account_ref_sha256)
    pilot.require(len((json.dumps(request,sort_keys=True,allow_nan=False)+'\n').encode())<=32768,
        'ENROLLMENT_REQUEST_BUDGET')
    return request


def _scope_current(request,*,now_ms,starting=False):
    if request['schema']!=SCOPE_REQUEST_SCHEMA:return
    from live.execution import demo_authority, demo_gold_budget_scope as scope
    registry=demo_authority.load_registry_for_executor()
    pilot.require(_digest(registry)==_digest(request['scope_registry']),'ENROLLMENT_SCOPE_REGISTRY_CHANGED')
    if starting:scope.pilot_window(request['budget_scope'],registry=registry,now_ms=now_ms)
    else:scope.current_scope_key(request['budget_scope'],registry,now_ms)


def _intent_check(intent,*,request):
    pilot.require(type(intent) is dict and set(intent)=={'schema','source_commit','request_sha256','prepared'}
        and intent['schema']=='GOLD_DEMO_PILOT_ENROLLMENT_INTENT_V1'
        and intent['source_commit']==request['source_commit'] and intent['request_sha256']==_digest(request),
        'ENROLLMENT_INTENT_BINDING')
    prepared=intent['prepared']
    pilot.require(type(prepared) is dict and set(prepared)==set('schema status configuration '
        'configuration_sha256 opening_origin execution_state_sha256 source_authenticated installed submission_enabled'.split())
        and prepared['schema']=='GOLD_DEMO_PILOT_ENROLLMENT_PREPARATION_V1'
        and prepared['status']=='ENROLLMENT_REVIEW_REQUIRED'
        and all(prepared[key] is False for key in ('source_authenticated','installed','submission_enabled')),
        'ENROLLMENT_INTENT_PREPARATION')
    config=prepared['configuration'];pilot.config_check(config)
    if request['schema']==SCOPE_REQUEST_SCHEMA:
        from live.execution.demo_gold_budget_scope import pilot_window
        window=pilot_window(request['budget_scope'],registry=request['scope_registry'],now_ms=config['started_at_ms'])
        pilot.require(config['ends_at_ms']==window['ends_at_ms']
            and config['ends_at_ms']<=window['scope_valid_until_ms'],'ENROLLMENT_SCOPE_WINDOW')
    origin=prepared['opening_origin']
    pilot.require(type(origin) is dict and origin.get('schema')==GOLD_ACCOUNT_TERMS_SCHEMA
        and origin.get('symbol')=='XAUUSDT' and origin.get('account_ref_sha256')==request['account_ref_sha256'],
        'ENROLLMENT_INTENT_ORIGIN')
    report=_require_origin(origin,now_ms=config['started_at_ms'])
    pilot.require(config['account_ref_sha256']==request['account_ref_sha256']
        and config['opening_origin_sha256']==_digest(origin)
        and config['initial_equity']==report['wallet']
        and prepared['configuration_sha256']==_digest(config)
        and type(prepared['execution_state_sha256']) is str
        and re.fullmatch('[a-f0-9]{64}',prepared['execution_state_sha256']) is not None,
        'ENROLLMENT_INTENT_MATERIALS')
    return config


def _receipt(config,request,state_hash,*,intent=None):
    value=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_RECEIPT_V1',request_sha256=_digest(request),
        configuration_sha256=_digest(config),opening_origin_sha256=config['opening_origin_sha256'],
        execution_state_sha256=state_hash,submission_enabled=False)
    if intent is not None:value.update(schema='GOLD_DEMO_PILOT_ENROLLMENT_RECEIPT_V2',intent_sha256=_digest(intent))
    return value


def completed_configuration(area,*,request):
    """Verify completed V1/V2 records; never reconstruct absent legacy material."""
    config=pilot.read(area/'configuration.json');pilot.config_check(config)
    pilot.require(type(request) is dict,'ENROLLMENT_REQUEST_FIELDS')
    _request_check(request,source_commit=request.get('source_commit'),account=config['account_ref_sha256'])
    receipt=pilot.read(area/'enrollment.json');path=area/'enrollment-intent.json'
    intent=None
    if path.exists() or path.is_symlink():
        intent=pilot.read(path,maximum=INTENT_MAXIMUM)
        pilot.require(type(intent) is dict,'ENROLLMENT_INTENT_BINDING')
    return completed_configuration_records(configuration=config,receipt=receipt,intent=intent,request=request)


def completed_configuration_records(*,configuration,receipt,intent,request):
    """Validate already custody-checked originals without executor identity I/O.

    Root reviewers must first read these records using the installed executor
    UID and recheck those bytes. This pure function grants no source custody.
    """
    config=configuration;pilot.config_check(config)
    pilot.require(type(request) is dict,'ENROLLMENT_REQUEST_FIELDS')
    _request_check(request,source_commit=request.get('source_commit'),account=config['account_ref_sha256'])
    if intent is not None:
        pilot.require(_intent_check(intent,request=request)==config,'ENROLLMENT_INTENT_CONFIGURATION')
    state_hash=receipt.get('execution_state_sha256') if type(receipt) is dict else None
    pilot.require(type(state_hash) is str and re.fullmatch('[a-f0-9]{64}',state_hash) is not None
        and receipt.get('submission_enabled') is False
        and _digest(receipt)==_digest(_receipt(config,request,state_hash,intent=intent))
        and (intent is None or state_hash==intent['prepared']['execution_state_sha256']),
        'ENROLLMENT_BINDING_CHANGED')
    return config


def _retain_exact(path,value,*,maximum=32768):
    retained=pilot.retain_once(path,value,maximum=maximum)
    pilot.require(_digest(retained)==_digest(value),'ENROLLMENT_RETAINED_RECORD_CHANGED')


def _finish_intent(area,intent,*,request,request_path,origin,dispatch,recovery,oms,now_ms):
    config=_intent_check(intent,request=request)
    pilot.require(type(now_ms) is int and config['started_at_ms']<=now_ms<config['ends_at_ms'],
        'ENROLLMENT_ORIGINAL_WINDOW_EXPIRED')
    receipt=_receipt(config,request,intent['prepared']['execution_state_sha256'],intent=intent)
    # Check every existing target before retaining any new recovery evidence.
    for name,value in (('configuration.json',config),('enrollment.json',receipt)):
        path=area/name
        if path.exists() or path.is_symlink():
            pilot.require(_digest(pilot.read(path))==_digest(value),'ENROLLMENT_RETAINED_RECORD_CHANGED')
    def current():
        _scope_current(request,now_ms=now_ms)
        checked=prepare(origin=origin,dispatch=dispatch,recovery=recovery,oms=oms,now_ms=now_ms)
        pilot.require(checked['execution_state_sha256']==intent['prepared']['execution_state_sha256']
            and checked['configuration']['initial_equity']==config['initial_equity']
            and pilot.read(request_path)==request,'ENROLLMENT_CURRENT_BASELINE_CHANGED')
        return checked
    checked=current()
    review=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_RESUME_REVIEW_V1',intent_sha256=_digest(intent),
        request_sha256=_digest(request),configuration_sha256=_digest(config),reviewed_at_ms=now_ms,
        current_origin_sha256=_digest(checked['opening_origin']),
        execution_state_sha256=checked['execution_state_sha256'],submission_enabled=False)
    reviews=area/'enrollment-reviews'
    if not reviews.exists():reviews.mkdir(mode=0o700)
    position._custody(reviews,directory=True)
    paths=[];target=reviews/(_digest(review)+'.json')
    for path in reviews.iterdir():
        pilot.require(len(paths)<32 and re.fullmatch('[a-f0-9]{64}\\.json',path.name),
            'ENROLLMENT_RESUME_REVIEW_BUDGET')
        old=pilot.read(path)
        pilot.require(type(old) is dict and set(old)==set(review)
            and old['schema']==review['schema'] and _digest(old)+'.json'==path.name
            and all(old[key]==review[key] for key in ('intent_sha256','request_sha256',
                'configuration_sha256','execution_state_sha256','submission_enabled'))
            and old['submission_enabled'] is False
            and type(old['reviewed_at_ms']) is int
            and config['started_at_ms']<=old['reviewed_at_ms']<=now_ms
            and type(old['current_origin_sha256']) is str
            and re.fullmatch('[a-f0-9]{64}',old['current_origin_sha256']),
            'ENROLLMENT_RESUME_HISTORY_CHANGED')
        paths.append(path)
    pilot.require(target in paths or len(paths)<32,'ENROLLMENT_RESUME_REVIEW_BUDGET')
    _retain_exact(target,review)
    _retain_exact(area/'configuration.json',config)
    current()
    _retain_exact(area/'enrollment.json',receipt)
    pilot.require(pilot.read(request_path)==request,'ENROLLMENT_REQUEST_CHANGED')
    return completed_configuration(area,request=request)


def prepare(*, origin, dispatch, recovery, oms, now_ms):
    origin=json.loads(json.dumps(origin,allow_nan=False))
    pilot.require(origin.get('schema')==GOLD_ACCOUNT_TERMS_SCHEMA
        and origin.get('symbol')=='XAUUSDT','ENROLLMENT_SCOPE')
    report=_require_origin(origin,now_ms=now_ms)
    state=_state(dispatch,recovery,oms)
    context,partition=demo_market_partition_runtime.resolve_context(state=state,origin=origin,as_of_ms=now_ms)
    if partition is not None:state['market_partition']=partition
    if context is not None:
        position.validate_origin(origin,context,as_of_ms=now_ms)
        pilot.require(Decimal(context['quantity'])==0,'ENROLLMENT_POSITION_NOT_FLAT')
    config=dict(schema='GOLD_DEMO_PILOT_CONTROL_V1',environment='BINANCE_USDM_DEMO',
        symbol='XAUUSDT',account_ref_sha256=origin['account_ref_sha256'],
        opening_origin_sha256=_digest(origin),initial_equity=report['wallet'],
        started_at_ms=now_ms,ends_at_ms=now_ms+86400000,
        max_loss_fraction='0.05',submission_enabled=False)
    pilot.config_check(config)
    return dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_PREPARATION_V1',
        status='ENROLLMENT_REVIEW_REQUIRED',configuration=config,
        configuration_sha256=_digest(config),opening_origin=origin,
        execution_state_sha256=_digest(state),source_authenticated=False,
        installed=False,submission_enabled=False)


def enroll_requested(*, root, source_commit, origin, dispatch, recovery, oms, now_ms):
    """Executor worker only, under the original preparation lease.

    The worker authenticates its identity/source and captures the origin through
    the original executor boundary before calling. An operator-installed request
    is required. This creates control records, never trading authorization.
    A complete durable intent resumes only missing writes from its original
    baseline/window. Older incomplete material and conflicting bytes stay rejected.
    """
    from pathlib import Path
    import os

    root=Path(root)
    request_path=root/'gold-pilot-request.json'
    if not request_path.exists() and not request_path.is_symlink():
        intent=root/'gold-pilot'/'enrollment-intent.json'
        pilot.require(not intent.exists() and not intent.is_symlink(),'ENROLLMENT_REQUEST_REQUIRED')
        return None
    pilot.require(root==position._ROOT and dispatch.path.parent==root,'ENROLLMENT_ROOT')
    request=pilot.read(request_path)
    _request_check(request,source_commit=source_commit,account=origin['account_ref_sha256'])
    area=root/'gold-pilot'
    if area.exists() or area.is_symlink():
        position._custody(area,directory=True)
        receipt=area/'enrollment.json'
        if receipt.exists() or receipt.is_symlink():return completed_configuration(area,request=request)
        intent=pilot.read(area/'enrollment-intent.json',maximum=INTENT_MAXIMUM)
        return _finish_intent(area,intent,request=request,request_path=request_path,
            origin=origin,dispatch=dispatch,recovery=recovery,oms=oms,now_ms=now_ms)
    _scope_current(request,now_ms=now_ms,starting=True)
    prepared=prepare(origin=origin,dispatch=dispatch,recovery=recovery,oms=oms,now_ms=now_ms)
    pilot.require(pilot.read(request_path)==request,'ENROLLMENT_REQUEST_CHANGED')
    intent=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_INTENT_V1',source_commit=source_commit,
        request_sha256=_digest(request),prepared=prepared)
    _intent_check(intent,request=request)
    pilot.require(len(json.dumps(intent,sort_keys=True,separators=(',',':'),allow_nan=False).encode())+1<=INTENT_MAXIMUM,
        'ENROLLMENT_INTENT_BUDGET')
    # Durable complete intent comes first. Missing writes may be resumed, never
    # overwritten; an empty directory/truncated intent still needs human repair.
    area.mkdir(mode=0o700)
    position._custody(area,directory=True)
    _retain_exact(area/'enrollment-intent.json',intent,maximum=INTENT_MAXIMUM)
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
    return _finish_intent(area,intent,request=request,request_path=request_path,
        origin=origin,dispatch=dispatch,recovery=recovery,oms=oms,now_ms=now_ms)
