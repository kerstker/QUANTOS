"""Observe one original pilot's stop without another strategy or cash episode.

The fixed intent identifies one observation in the existing retention budget.
It grants no trading, feedback publication, renewal or account authority.
"""
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
import os
from pathlib import Path
import re

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from live.execution.demo_pre_submit import _state, _require_origin, _preparation_lease, _retain
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.demo_issuer import MAX_INPUT_BYTES
from tools.demo_resident_recovery import _check_path
from tools.gold_bar_pilot_capacity import _read
from tools.gold_pilot_retention_store import canonical, CLOSING_MS, active

SCHEMA='GOLD_PILOT_CLOSURE_INPUT_V1'
IDLE_SCHEMA='GOLD_PILOT_IDLE_CLOSURE_INPUT_V1'
INTENT_SCHEMA='GOLD_PILOT_CLOSURE_INTENT_V1'
INTENT='closure-intent.json'
BAR_SCHEMAS=('GOLD_BAR_AUTO_EPISODE_INPUT_V1','GOLD_BAR_FRESH_EPISODE_INPUT_V1','GOLD_BAR_RETAINED_EPISODE_INPUT_V1')
DECISION_FIELDS=('schema status source_commit bundle_sha256 account_ref_sha256 observed_at_ms origin_sha256 '
    'execution_state_sha256 pilot_control position_flat execution_history_empty action draft execution_constraints '
    'cash_reconciled submission_enabled economic_authority closure_of_result_sha256 closure_artifact_sha256 '
    'closure_feedback_sha256')


def _hash(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}',value),'PILOT_CLOSURE_HASH')


def _runtime(runtime,source):
    require(type(source) is str and re.fullmatch('[a-f0-9]{40}',source)
        and type(runtime) is dict and runtime.get('source_commit')==source,'PILOT_CLOSURE_RUNTIME')
    if runtime.get('schema') in BAR_SCHEMAS:
        from tools.gold_strategy_budget_enrollment import runtime_check
        runtime_check(runtime,source)
    else:
        extras={'GOLD_STRATEGY_WORKER_INPUT_V1':'observation_sha256',
            'GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1':'plan'}
        require(runtime.get('schema') in extras,'PILOT_CLOSURE_RUNTIME')
        _fields(runtime,'schema source_commit trial_manifest_sha256 '+extras[runtime['schema']])
        _hash(runtime['trial_manifest_sha256'])


def request(runtime,original_result):
    """Build a bounded request from the original, unaugmented worker result."""
    from tools.gold_strategy_controller import validate_result as worker_result
    require(type(original_result) is dict and not any(k in original_result for k in
        ('research_feedback','feedback_delivery_pending','feedback_recovery_control','closure_request')),
        'PILOT_CLOSURE_ORIGINAL_RESULT')
    source=original_result.get('source_commit')
    require(type(source) is str and re.fullmatch('[a-f0-9]{40}',source),'PILOT_CLOSURE_SOURCE')
    _runtime(runtime,source)
    decision=original_result.get('decision',{})
    worker_result(original_result,source_commit=source,bundle_sha256=original_result.get('bundle_sha256'),
        account=decision.get('account_ref_sha256'))
    require(decision.get('status')=='STRATEGY_EPISODE_SETTLED' and decision.get('position_flat') is True,
        'PILOT_CLOSURE_ORIGINAL_NOT_SETTLED')
    window=original_result.get('original_pilot_window')
    require(type(window) is dict and decision.get('pilot_control',{}).get('configuration_sha256')==
        window['configuration_sha256'],'PILOT_CLOSURE_ORIGINAL_WINDOW')
    feedback=original_result.get('feedback')
    require(type(feedback) is dict and feedback.get('status') in
        ('BAR_STRATEGY_FEEDBACK_RETAINED','STRATEGY_FEEDBACK_RETAINED') and
        feedback.get('submission_enabled') is False and feedback.get('economic_authority') is False,
        'PILOT_CLOSURE_FEEDBACK')
    for key in ('artifact_sha256','feedback_sha256'):_hash(feedback.get(key))
    value=dict(schema=SCHEMA,source_commit=source,runtime=deepcopy(runtime),
        original_result=deepcopy(original_result),original_result_sha256=_digest(original_result),
        original_decision_sha256=original_result['decision_sha256'],
        artifact_sha256=feedback['artifact_sha256'],feedback_sha256=feedback['feedback_sha256'],
        original_pilot_window=deepcopy(window))
    require(len(canonical(value))<=MAX_INPUT_BYTES,'PILOT_CLOSURE_INPUT_BUDGET')
    return value


def _request(value,source,account):
    if type(value) is dict and value.get('schema')==IDLE_SCHEMA:
        require(value==idle_request(value.get('runtime'),value.get('original_pilot_window'))
            and value['source_commit']==source and value['original_pilot_window']['account_ref_sha256']==account,
            'PILOT_IDLE_REQUEST_CHANGED')
        return value
    _fields(value,'schema source_commit runtime original_result original_result_sha256 original_decision_sha256 '
        'artifact_sha256 feedback_sha256 original_pilot_window')
    require(value==request(value['runtime'],value['original_result']) and value['source_commit']==source
        and value['original_result']['decision']['account_ref_sha256']==account,'PILOT_CLOSURE_REQUEST_CHANGED')
    return value


def validate_result(result,original_result):
    """Pure binding check; the executor and fixed intent prove custody separately."""
    _fields(result,'schema status source_commit bundle_sha256 decision_sha256 decision submission_enabled '
        'economic_authority original_pilot_window closure_request closure_intent_sha256 original_result_provenance')
    require(result['original_result_provenance'] in ('EXECUTOR_ORIGINAL','CONTROLLER_ORIGINAL'),
        'PILOT_CLOSURE_PROVENANCE')
    req=result['closure_request']
    require(type(req) is dict and req==request(req.get('runtime'),original_result),'PILOT_CLOSURE_RESULT_REQUEST')
    from tools.gold_strategy_controller import validate_result as worker_result
    decision=worker_result(result,source_commit=original_result['source_commit'],
        bundle_sha256=_digest(req['runtime']),account=original_result['decision']['account_ref_sha256'])
    _fields(decision,DECISION_FIELDS)
    require(decision['status']=='PILOT_STOPPED' and decision['position_flat'] is True
        and decision['execution_history_empty'] is False and decision['cash_reconciled'] is False
        and all(decision[k] is None for k in ('action','draft','execution_constraints'))
        and decision['closure_of_result_sha256']==_digest(original_result)
        and decision['closure_artifact_sha256']==req['artifact_sha256']
        and decision['closure_feedback_sha256']==req['feedback_sha256']
        and decision['execution_state_sha256']==original_result['decision']['execution_state_sha256']
        and result['original_pilot_window']==original_result['original_pilot_window'],
        'PILOT_CLOSURE_RESULT_BINDING')
    stop=decision['pilot_control']
    _fields(stop,'schema status reason configuration_sha256 account_ref_sha256 observed_at_ms observed_equity '
        'origin_sha256 submission_enabled')
    window=result['original_pilot_window']
    require(stop['schema']=='GOLD_DEMO_PILOT_STOP_V1' and stop['status']=='STOPPED'
        and stop['reason'] in ('LOSS_LIMIT','DURATION_COMPLETE') and stop['submission_enabled'] is False
        and stop['configuration_sha256']==window['configuration_sha256']
        and stop['account_ref_sha256']==window['account_ref_sha256']
        and type(decision['observed_at_ms']) is int and type(stop['observed_at_ms']) is int
        and window['started_at_ms']<=stop['observed_at_ms']<=decision['observed_at_ms']
        <window['ends_at_ms']+CLOSING_MS,'PILOT_CLOSURE_STOP_BINDING')
    for item in (result['closure_intent_sha256'],decision['origin_sha256'],stop['origin_sha256']):_hash(item)
    require(type(stop['observed_equity']) is str and Decimal(stop['observed_equity']).is_finite(),
        'PILOT_CLOSURE_STOP_EQUITY')
    require(len(canonical(result))<=MAX_INPUT_BYTES,'PILOT_CLOSURE_RESULT_BUDGET')
    return decision


def _file(root,path,uid,maximum=MAX_INPUT_BYTES):
    require(root==path or root in path.parents,'PILOT_CLOSURE_PATH')
    _check_path(root,uid,directory=True)
    current=root
    for part in path.parent.relative_to(root).parts:
        current=current/part;_check_path(current,uid,directory=True)
    return _read(path,uid,maximum)


def _configuration(root,req,uid):
    from live.execution import demo_gold_pilot_enrollment as enrollment
    request_value=decode(_file(root,root/'gold-pilot-request.json',uid,32768))
    enrollment._request_check(request_value,source_commit=req['source_commit'],
        account=req['original_pilot_window']['account_ref_sha256'])
    area=root/'gold-pilot'
    config=decode(_file(root,area/'configuration.json',uid,32768))
    receipt=decode(_file(root,area/'enrollment.json',uid,32768))
    intent_path=area/'enrollment-intent.json'
    intent=decode(_file(root,intent_path,uid,enrollment.INTENT_MAXIMUM)) if intent_path.exists() or intent_path.is_symlink() else None
    require(enrollment.completed_configuration_records(configuration=config,receipt=receipt,
        intent=intent,request=request_value)==config,'PILOT_CLOSURE_ENROLLMENT')
    window=req['original_pilot_window']
    require(_digest(request_value)==window['request_sha256'] and _digest(config)==window['configuration_sha256']
        and all(config[k]==window[k] for k in ('started_at_ms','ends_at_ms')),'PILOT_CLOSURE_WINDOW_CHANGED')
    return config


def _artifact(root,req,uid,now):
    from tools import demo_feedback_publish as publication
    original=req['original_result'];bar=original['feedback']['status']=='BAR_STRATEGY_FEEDBACK_RETAINED'
    path=root/('strategy-bar-feedback' if bar else 'strategy-feedback')/original['bundle_sha256']/'research-feedback'/(req['artifact_sha256']+'.json')
    raw=_file(root,path,uid,publication.MAXIMUM);value=decode(raw)
    require(_digest(value)==req['artifact_sha256'] and canonical(value)==raw,'PILOT_CLOSURE_ARTIFACT_CHANGED')
    if bar:
        publication.validate_bar_export(value,source_commit=req['source_commit'],feedback_sha256=req['feedback_sha256'],
            now=now,cycle=original['bundle_sha256'])
        cash=value['account_report'] if value['schema'] in publication.REPORTED_BAR_SCHEMAS else value['cash_feedback']
    else:
        publication.validate_export(value,source_commit=req['source_commit'],feedback_sha256=req['feedback_sha256'],now=now)
        cash=value
    require(cash['account_ref_sha256']==req['original_pilot_window']['account_ref_sha256'],
        'PILOT_CLOSURE_CASH_ACCOUNT')
    return Decimal(cash['canonical_wallet']),raw


def _prefix(store,checkpoint,*,allow_empty=False):
    _fields(checkpoint,'schema capacity_sha256 frames records bytes claim_bytes audit_bytes submission_enabled')
    require(all(type(checkpoint[k]) is int and checkpoint[k]>=0 for k in
        ('frames','records','bytes','claim_bytes','audit_bytes')),'PILOT_CLOSURE_CHECKPOINT')
    frames=list(store.conn.execute('SELECT ordinal,claim FROM frames WHERE ordinal<=? ORDER BY ordinal',(checkpoint['frames'],)))
    records=list(store.conn.execute('SELECT sequence,kind,ordinal,identity,LENGTH(payload) FROM records WHERE sequence<=? ORDER BY sequence',(checkpoint['records'],)))
    require(len(frames)==checkpoint['frames'] and len(records)==checkpoint['records']
        and all(row[2]<=checkpoint['frames'] for row in records),'PILOT_CLOSURE_PREFIX')
    audit=sum(len(canonical(store._event('claim',n,_digest(decode(body)),len(body)))) for n,body in frames)
    audit+=sum(len(canonical(store._event(kind,n,identity,size))) for _,kind,n,identity,size in records)
    expected=dict(schema='GOLD_PILOT_RETENTION_HEAD_V1',capacity_sha256=store.capacity_sha256,
        frames=len(frames),records=len(records),bytes=sum(row[4] for row in records),
        claim_bytes=sum(len(row[1]) for row in frames),audit_bytes=audit,submission_enabled=False)
    require(checkpoint==expected and (allow_empty and not frames or
        any(k=='result' and n==len(frames) for _,k,n,_,_ in records)),
        'PILOT_CLOSURE_PREFIX_CHANGED')


def _original(root,req,uid,store,through=None):
    result=req['original_result'];decision=result['decision']
    if store is None:
        require(not active(root) and _digest(req['runtime'])==result['bundle_sha256'],'PILOT_CLOSURE_LEGACY_SCOPE')
        require(_file(root,root/'strategy-decisions'/(req['original_decision_sha256']+'.json'),uid)==canonical(decision),
            'PILOT_CLOSURE_LEGACY_DECISION_CHANGED')
        return None
    require(store.read('result',req['original_result_sha256'])==canonical(result),'PILOT_CLOSURE_NATIVE_RESULT_CHANGED')
    claim=store.reference('result',req['original_result_sha256'])
    require(store.read_claim(claim,'decision')==canonical(decision),'PILOT_CLOSURE_NATIVE_DECISION_CHANGED')
    base=store.binding['base_runtime'];runtime=req['runtime']
    ignored={'episode_id'} if base['schema']=='GOLD_BAR_AUTO_EPISODE_INPUT_V1' else set()
    require({k:v for k,v in runtime.items() if k not in ignored}=={k:v for k,v in base.items() if k not in ignored},
        'PILOT_CLOSURE_RUNTIME_CHANGED')
    end=store.metadata()['frames'] if through is None else through
    for _,body in store.conn.execute('SELECT ordinal,claim FROM frames WHERE ordinal>? AND ordinal<=? ORDER BY ordinal',(claim['ordinal'],end)):
        later=decode(body);raw=store.read_claim(later,'result')
        require(raw is not None,'PILOT_CLOSURE_PRIOR_PARTIAL')
        row=decode(raw);d=row.get('decision',{})
        require('closure_request' not in row and 'closure_of_result_sha256' not in d,
            'PILOT_CLOSURE_INTENT_MISSING')
        from tools.gold_strategy_controller import validate_result as validate_worker
        validate_worker(row,source_commit=req['source_commit'],bundle_sha256=later['bundle_sha256'],account=decision['account_ref_sha256'])
        require(store.read_claim(later,'decision')==canonical(d) and d.get('status') in ('NO_ENTRY_SIGNAL','PILOT_STOPPED')
            and d.get('position_flat') is True and all(d.get(k) is None for k in ('action','draft','execution_constraints'))
            and d.get('execution_state_sha256')==decision['execution_state_sha256']
            and row.get('original_pilot_window')==req['original_pilot_window'],'PILOT_CLOSURE_LATER_ACTIVITY')
    return claim


def _origin(raw,req,store=None,claim=None):
    from tools import demo_executor_origin as capture
    value=decode(raw);capture.original.validate(value)
    require(canonical(value)==raw and value['status']=='CAPTURED' and value['symbol']=='XAUUSDT'
        and value['account_ref_sha256']==req['original_pilot_window']['account_ref_sha256']
        and value['source_sha256']==capture.source_hash(),'PILOT_CLOSURE_ORIGIN')
    if claim is not None:
        require(claim['admitted_at_ms']<=value['observed_at_ms']<=value['completed_at_ms']<
            req['original_pilot_window']['ends_at_ms']+CLOSING_MS,'PILOT_CLOSURE_ORIGIN_TIME')
    return value


def _decision(req,origin,control):
    return dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1',status='PILOT_STOPPED',source_commit=req['source_commit'],
        bundle_sha256=_digest(req['runtime']),account_ref_sha256=req['original_pilot_window']['account_ref_sha256'],
        observed_at_ms=max(origin['completed_at_ms'],control['observed_at_ms']),origin_sha256=_digest(origin),
        execution_state_sha256=req['original_result']['decision']['execution_state_sha256'],pilot_control=control,
        position_flat=True,execution_history_empty=False,action=None,draft=None,execution_constraints=None,
        cash_reconciled=False,submission_enabled=False,economic_authority=False,
        closure_of_result_sha256=req['original_result_sha256'],closure_artifact_sha256=req['artifact_sha256'],
        closure_feedback_sha256=req['feedback_sha256'])


def _result(req,decision,intent_sha256,*,native):
    return dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1',status='DECISION_RETAINED',source_commit=req['source_commit'],
        bundle_sha256=_digest(req['runtime']),decision_sha256=_digest(decision),decision=decision,submission_enabled=False,
        economic_authority=False,original_pilot_window=deepcopy(req['original_pilot_window']),
        closure_request=deepcopy(req),closure_intent_sha256=intent_sha256,
        original_result_provenance='EXECUTOR_ORIGINAL' if native else 'CONTROLLER_ORIGINAL')


def _stopped_decision(root,req,origin,config,uid,intent_sha256,*,native):
    from live.execution import demo_gold_pilot as pilot
    path=root/'gold-pilot/stopped.json'
    if not path.exists() and not path.is_symlink():return None
    stop=decode(_file(root,path,uid,32768))
    decision=_decision(req,origin,stop)
    pilot.validate_stop(stop,config=config,now_ms=decision['observed_at_ms'])
    validate_result(_result(req,decision,intent_sha256,native=native),req['original_result'])
    return decision


def read_intent(root,*,retention,runtime,source_commit,account,executor_uid):
    """Read one fixed intent under the caller's existing executor store lease."""
    root=Path(root);area=root/'gold-pilot';path=area/INTENT
    from tools.gold_pilot_reobservation import present_reobservation
    require(not present_reobservation(root),'PILOT_REOBSERVATION_REQUIRES_TYPED_READER')
    _check_path(root,executor_uid,directory=True);_check_path(area,executor_uid,directory=True)
    if not path.exists() and not path.is_symlink():
        require(not any(p.exists() or p.is_symlink() for p in
            (area/'closure-origin.json',area/'closure-result.json')),'PILOT_CLOSURE_INTENT_MISSING')
        return None
    raw=_file(root,path,executor_uid);value=decode(raw)
    _fields(value,'schema request request_sha256 original_claim original_checkpoint observation_claim closing_deadline_ms '
        'submission_enabled economic_authority')
    req=_request(value['request'],source_commit,account)
    require(value['schema']==INTENT_SCHEMA and canonical(value)==raw and req['runtime']==runtime
        and value['request_sha256']==_digest(req) and value['submission_enabled'] is False
        and value['economic_authority'] is False and value['closing_deadline_ms']==
        req['original_pilot_window']['ends_at_ms']+CLOSING_MS,'PILOT_CLOSURE_INTENT')
    if req['schema']==IDLE_SCHEMA:
        return _read_idle_intent(root,value,raw,retention=retention,runtime=runtime,
            source_commit=source_commit,account=account,uid=executor_uid)
    config=_configuration(root,req,executor_uid)
    claim=value['observation_claim'];result=None;origin=None;decision=None;status='INTENT_ONLY'
    if retention is not None:
        require(retention.root==root and retention.role=='executor' and retention.uid==executor_uid,
            'PILOT_CLOSURE_RETENTION')
        checkpoint=value['original_checkpoint'];_prefix(retention,checkpoint)
        require(_original(root,req,executor_uid,retention,through=checkpoint['frames'])==value['original_claim'],
            'PILOT_CLOSURE_ORIGINAL_CLAIM')
        retention._check_claim(claim)
        require(claim['ordinal']==checkpoint['frames']+1 and claim['bundle_sha256']==_digest(runtime),
            'PILOT_CLOSURE_TAIL_CLAIM')
        current=retention.metadata()
        require(current['frames'] in (checkpoint['frames'],claim['ordinal']),'PILOT_CLOSURE_EXTRA_FRAME')
        tail=list(retention.conn.execute('SELECT ordinal FROM records WHERE sequence>?',(checkpoint['records'],)))
        require(all(row[0]==claim['ordinal'] for row in tail),'PILOT_CLOSURE_PREFIX_APPEND')
        if current['frames']==checkpoint['frames']:
            require(current==checkpoint,'PILOT_CLOSURE_CHECKPOINT_CHANGED')
        else:
            origin=retention.read_claim(claim,'origin')
            decision=retention.read_claim(claim,'decision');result=retention.read_claim(claim,'result')
            require(origin is not None or decision is result is None,'PILOT_CLOSURE_PARTIAL_ORDER')
            if origin is not None:_origin(origin,req,retention,claim)
            if decision is not None:
                expected=_stopped_decision(root,req,decode(origin),config,executor_uid,_digest(value),native=retention is not None)
                require(expected is not None and decision==canonical(expected),'PILOT_CLOSURE_PARTIAL_DECISION')
                decision=expected
            require(result is None or decision is not None,'PILOT_CLOSURE_PARTIAL_ORDER')
            status='PENDING'
    else:
        require(value['original_claim'] is value['original_checkpoint'] is claim is None,'PILOT_CLOSURE_LEGACY_INTENT')
        _original(root,req,executor_uid,None)
        origin_path=area/'closure-origin.json';result_path=area/'closure-result.json'
        if origin_path.exists() or origin_path.is_symlink():
            origin=_file(root,origin_path,executor_uid);_origin(origin,req);status='PENDING'
            expected=_stopped_decision(root,req,decode(origin),config,executor_uid,_digest(value),native=retention is not None)
            if expected is not None:
                path=root/'strategy-decisions'/(_digest(expected)+'.json')
                if path.exists() or path.is_symlink():
                    require(_file(root,path,executor_uid)==canonical(expected),'PILOT_CLOSURE_LEGACY_DECISION_CHANGED')
                    decision=expected
        if result_path.exists() or result_path.is_symlink():result=_file(root,result_path,executor_uid)
    if result is not None:
        result=decode(result);d=validate_result(result,req['original_result'])
        require(result['original_result_provenance']==('EXECUTOR_ORIGINAL' if retention is not None else 'CONTROLLER_ORIGINAL')
            and decision is not None and d==decision and result['closure_request']==req and result['closure_intent_sha256']==_digest(value)
            and origin is not None and d['origin_sha256']==_digest(decode(origin)), 'PILOT_CLOSURE_COMPLETION_CHANGED')
        from live.execution import demo_gold_pilot as pilot
        stop=decode(_file(root,area/'stopped.json',executor_uid,32768))
        require(pilot.validate_stop(stop,config=config,now_ms=d['observed_at_ms'])==d['pilot_control'],
            'PILOT_CLOSURE_STOP_CHANGED')
        status='COMPLETE'
    require(_file(root,area/INTENT,executor_uid)==raw,'PILOT_CLOSURE_INTENT_CHANGED')
    return dict(intent=value,intent_sha256=_digest(value),status=status,claim=claim,result=result,decision=decision)


def _write_intent(root,value):
    from live.execution.demo_gold_pilot import retain_once
    require(retain_once(root/'gold-pilot'/INTENT,value,maximum=MAX_INPUT_BYTES)==value,'PILOT_CLOSURE_INTENT_CONFLICT')


def _stores(stack,root):
    from live.execution.demo_dispatch_store import DemoDispatchStore
    from live.execution.order_recovery import SqliteOrderRecoveryStore
    from live.execution.position_ledger import LiveLedger
    result={}
    for name,factory in (('dispatch',DemoDispatchStore),('recovery',SqliteOrderRecoveryStore),('oms',LiveLedger)):
        store=factory(str(root/(name+'.sqlite3')),read_only=True)
        stack.callback(store.close);result[name]=store
    return result


def _historical_origin(root,req,view,store,config,uid,now):
    """Only an actual matching STOP witnesses a past observation's freshness."""
    path=root/'gold-pilot/stopped.json'
    if not path.exists() and not path.is_symlink():return None
    raw=store.read_claim(view['claim'],'origin') if store is not None else (
        _file(root,root/'gold-pilot/closure-origin.json',uid)
        if (root/'gold-pilot/closure-origin.json').exists() else None)
    if raw is None:return None
    origin=_origin(raw,req,store,view['claim']);stop=decode(_file(root,path,uid,32768))
    from live.execution.demo_gold_pilot import validate_stop
    validate_stop(stop,config=config,now_ms=now)
    if stop['origin_sha256']!=_digest(origin):return None
    require(origin['completed_at_ms']<=stop['observed_at_ms']<view['intent']['closing_deadline_ms'],
        'PILOT_CLOSURE_HISTORICAL_TIME')
    _require_origin(origin,now_ms=stop['observed_at_ms'])
    return origin,stop


def _persist(root,view,store,decision,result,uid):
    from live.execution.demo_gold_pilot import retain_once
    if store is not None:
        store.put(view['claim'],'decision',decision);store.put(view['claim'],'result',result)
        require(store.read_claim(view['claim'],'decision')==canonical(decision)
            and store.read_claim(view['claim'],'result')==canonical(result),'PILOT_CLOSURE_RESULT_NOT_RETAINED')
    else:
        _retain(root/'strategy-decisions',decision)
        require(retain_once(root/'gold-pilot/closure-result.json',result,maximum=MAX_INPUT_BYTES)==result,
            'PILOT_CLOSURE_RESULT_CONFLICT')
        require(_file(root,root/'strategy-decisions'/(_digest(decision)+'.json'),uid)==canonical(decision)
            and _file(root,root/'gold-pilot/closure-result.json',uid)==canonical(result),
            'PILOT_CLOSURE_RESULT_NOT_RETAINED')
    require(_file(root,root/'gold-pilot'/INTENT,uid)==canonical(view['intent']),'PILOT_CLOSURE_INTENT_CHANGED')


def execute(root,req,*,source_commit,account,executor_uid,clock,sleep):
    """Executor-only worker branch: account observation and original stop only."""
    root=Path(root);_request(req,source_commit,account)
    from tools.gold_pilot_reobservation import present_reobservation
    require(not present_reobservation(root),'PILOT_REOBSERVATION_REQUIRES_TYPED_REQUEST')
    if req['schema']==IDLE_SCHEMA:
        return _execute_idle(root,req,source_commit=source_commit,account=account,
            uid=executor_uid,clock=clock,sleep=sleep)
    _check_path(root,executor_uid,directory=True)
    from tools.gold_pilot_retention import executor_retention
    from tools.gold_strategy_worker import capture_origin
    from live.execution import demo_gold_pilot as pilot
    runtime=req['runtime'];original=req['original_result'];start=clock()
    with ExitStack() as stack:
        store=executor_retention(root,runtime,source_commit=source_commit,account=account,uid=executor_uid)
        if store is not None:stack.callback(store.close)
        stores=_stores(stack,root)
        with _preparation_lease(stores['dispatch']):
            state=_state(**stores)
            require(_digest(state)==original['decision']['execution_state_sha256'],'PILOT_CLOSURE_EXECUTION_CHANGED')
            wallet,artifact=_artifact(root,req,executor_uid,start)
            config=_configuration(root,req,executor_uid)
            view=read_intent(root,retention=store,runtime=runtime,source_commit=source_commit,
                account=account,executor_uid=executor_uid)
            if view is not None:
                require(view['intent']['request']==req,'PILOT_CLOSURE_REQUEST_CONFLICT')
                if view['status']=='COMPLETE':
                    require(clock()>=view['result']['decision']['observed_at_ms'],'PILOT_CLOSURE_CLOCK')
                    require(_state(**stores)==state and _artifact(root,req,executor_uid,clock())[1]==artifact
                        and _configuration(root,req,executor_uid)==config,'PILOT_CLOSURE_ORIGINAL_CHANGED')
                    return view['result']
                historical=_historical_origin(root,req,view,store,config,executor_uid,clock())
                if historical is not None:
                    origin,stop=historical
                    fresh=_require_origin(origin,now_ms=stop['observed_at_ms'])
                    require(Decimal(fresh['wallet'])==wallet==Decimal(stop['observed_equity']),
                        'PILOT_CLOSURE_CASH_CHANGED')
                    decision=_decision(req,origin,stop)
                    result=_result(req,decision,view['intent_sha256'],native=store is not None)
                    validate_result(result,original)
                    require(_state(**stores)==state and _artifact(root,req,executor_uid,clock())[1]==artifact
                        and _configuration(root,req,executor_uid)==config,'PILOT_CLOSURE_ORIGINAL_CHANGED')
                    _persist(root,view,store,decision,result,executor_uid)
                    return result
            deadline=config['ends_at_ms']+CLOSING_MS
            stop_path=root/'gold-pilot/stopped.json'
            stop=pilot.read(stop_path) if stop_path.exists() or stop_path.is_symlink() else None
            require(config['started_at_ms']<=start<deadline and
                (start>=config['ends_at_ms'] or stop is not None),'PILOT_CLOSURE_NOT_DUE')
            if stop is not None:pilot.validate_stop(stop,config=config,now_ms=start)
            if view is None:
                original_claim=_original(root,req,executor_uid,store)
                checkpoint=None;claim=None
                if store is not None:
                    checkpoint=store.metadata()
                    last=decode(store.conn.execute('SELECT claim FROM frames ORDER BY ordinal DESC LIMIT 1').fetchone()[0])
                    wait=last['admitted_at_ms']+store.binding['minimum_interval_ms']-clock()
                    if wait>0:sleep(wait/1000)
                    admitted=clock();require(start<=admitted<deadline,'PILOT_CLOSURE_CLOCK')
                    claim=dict(schema='GOLD_PILOT_OBSERVATION_CLAIM_V1',ordinal=checkpoint['frames']+1,
                        admitted_at_ms=admitted,bundle_sha256=_digest(runtime),capacity_sha256=store.capacity_sha256,
                        submission_enabled=False)
                intent=dict(schema=INTENT_SCHEMA,request=deepcopy(req),request_sha256=_digest(req),
                    original_claim=original_claim,original_checkpoint=checkpoint,observation_claim=claim,
                    closing_deadline_ms=deadline,submission_enabled=False,economic_authority=False)
                _write_intent(root,intent)
                view=read_intent(root,retention=store,runtime=runtime,source_commit=source_commit,
                    account=account,executor_uid=executor_uid)
            claim=view['claim'];intent=view['intent']
            before_capture=clock();require(start<=before_capture<deadline,'PILOT_CLOSURE_CLOCK')
            if store is not None:
                if view['status']=='INTENT_ONLY':
                    require(store.claim(bundle=runtime,now_ms=claim['admitted_at_ms'])==claim,'PILOT_CLOSURE_CLAIM_CHANGED')
                origin=capture_origin(root=root,source_commit=source_commit,retention=store,claim=claim)
            else:
                path=root/'gold-pilot/closure-origin.json'
                if path.exists() or path.is_symlink():origin=_origin(_file(root,path,executor_uid),req)
                else:
                    origin=capture_origin(root=root,source_commit=source_commit)
                    require(pilot.retain_once(path,origin,maximum=MAX_INPUT_BYTES)==origin,'PILOT_CLOSURE_ORIGIN_CONFLICT')
            now=clock();require(start<=now<deadline,'PILOT_CLOSURE_CLOCK')
            fresh=_require_origin(origin,now_ms=now)
            require(origin['account_ref_sha256']==account and Decimal(fresh['wallet'])==wallet,'PILOT_CLOSURE_CASH_CHANGED')
            control=pilot.observe(dict(account_ref_sha256=account,equity=fresh['wallet']),now_ms=now,origin_sha256=_digest(origin))
            require(control is not None and control['status']=='STOPPED','PILOT_CLOSURE_STOP_REQUIRED')
            decision=_decision(req,origin,control)
            result=_result(req,decision,view['intent_sha256'],native=store is not None)
            validate_result(result,original)
            require(_state(**stores)==state and _artifact(root,req,executor_uid,now)[1]==artifact
                and _configuration(root,req,executor_uid)==config,'PILOT_CLOSURE_ORIGINAL_CHANGED')
            finished=clock();require(now<=finished<deadline,'PILOT_CLOSURE_CLOCK')
            _require_origin(origin,now_ms=finished)
            if store is not None:
                store.put(claim,'decision',decision);store.put(claim,'result',result)
            else:
                _retain(root/'strategy-decisions',decision)
                require(pilot.retain_once(root/'gold-pilot/closure-result.json',result,maximum=MAX_INPUT_BYTES)==result,
                    'PILOT_CLOSURE_RESULT_CONFLICT')
            # The original full audit and prefix check precede capture under the
            # same exclusive lease. Re-read only these newly committed slots;
            # another full-prefix replay here would age the fresh observation.
            require(_file(root,root/'gold-pilot'/INTENT,executor_uid)==canonical(intent),
                'PILOT_CLOSURE_INTENT_CHANGED')
            if store is not None:
                require(store.read_claim(claim,'decision')==canonical(decision)
                    and store.read_claim(claim,'result')==canonical(result),'PILOT_CLOSURE_RESULT_NOT_RETAINED')
            else:
                require(_file(root,root/'strategy-decisions'/(_digest(decision)+'.json'),executor_uid)==canonical(decision)
                    and _file(root,root/'gold-pilot/closure-result.json',executor_uid)==canonical(result),
                    'PILOT_CLOSURE_RESULT_NOT_RETAINED')
            returned=clock();require(finished<=returned<deadline,'PILOT_CLOSURE_CLOCK')
            _require_origin(origin,now_ms=returned)
            return result


def idle_request(runtime,original_window):
    """A separate non-order request; no invented settled result or feedback."""
    require(type(original_window) is dict,'PILOT_IDLE_WINDOW')
    source=original_window.get('source_commit');_runtime(runtime,source)
    from tools.gold_strategy_controller import validate_original_pilot_window
    validate_original_pilot_window(dict(original_pilot_window=original_window,decision=dict(
        pilot_control=dict(status='ACTIVE',configuration_sha256=original_window.get('configuration_sha256'),
            submission_enabled=False))),source_commit=source,account=original_window.get('account_ref_sha256'))
    value=dict(schema=IDLE_SCHEMA,source_commit=source,runtime=deepcopy(runtime),
        original_pilot_window=deepcopy(original_window))
    require(len(canonical(value))<=MAX_INPUT_BYTES,'PILOT_CLOSURE_INPUT_BUDGET')
    return value


def validate_idle_result(result,req):
    """Validate executor evidence as evidence, never as execution permission."""
    _fields(result,'schema status source_commit bundle_sha256 decision_sha256 decision submission_enabled '
        'economic_authority original_pilot_window closure_request closure_intent_sha256 idle_evidence')
    req=_request(req,req['source_commit'],req['original_pilot_window']['account_ref_sha256'])
    require(req['schema']==IDLE_SCHEMA and result['closure_request']==req,'PILOT_IDLE_RESULT_REQUEST')
    from tools.gold_strategy_controller import validate_result as worker_result
    d=worker_result(result,source_commit=req['source_commit'],bundle_sha256=_digest(req['runtime']),
        account=req['original_pilot_window']['account_ref_sha256'])
    fields=DECISION_FIELDS.split();fields=[k for k in fields if not k.startswith('closure_')]+['closure_mode']
    _fields(d,' '.join(fields))
    require(d['status']=='PILOT_STOPPED' and d['position_flat'] is True
        and type(d['execution_history_empty']) is bool and d['cash_reconciled'] is False
        and all(d[k] is None for k in ('action','draft','execution_constraints'))
        and d['closure_mode']=='NO_EXECUTION_SINCE_ENROLLMENT'
        and result['original_pilot_window']==req['original_pilot_window'],'PILOT_IDLE_DECISION')
    e=result['idle_evidence'];w=req['original_pilot_window'];stop=d['pilot_control']
    _fields(e,'schema mode source_commit account_ref_sha256 request_sha256 configuration_sha256 '
        'enrollment_intent_sha256 enrollment_receipt_sha256 opening_origin_sha256 origin_sha256 '
        'execution_state_sha256 market_partition_sha256 started_at_ms ends_at_ms observed_at_ms '
        'position_flat observed_equity submission_enabled economic_authority')
    require(e['schema']=='GOLD_PILOT_IDLE_EVIDENCE_V1' and e['mode']==d['closure_mode']
        and all(e[k]==w[k] for k in ('source_commit','account_ref_sha256','request_sha256',
            'configuration_sha256','started_at_ms','ends_at_ms'))
        and e['origin_sha256']==d['origin_sha256'] and e['position_flat'] is True
        and e['submission_enabled'] is e['economic_authority'] is False
        and type(e['observed_at_ms']) is int and w['ends_at_ms']<=e['observed_at_ms']==d['observed_at_ms']
        <w['ends_at_ms']+CLOSING_MS,'PILOT_IDLE_EVIDENCE_BINDING')
    _fields(stop,'schema status reason configuration_sha256 account_ref_sha256 observed_at_ms observed_equity '
        'origin_sha256 submission_enabled')
    require(stop['schema']=='GOLD_DEMO_PILOT_STOP_V1' and stop['status']=='STOPPED'
        and stop['reason']=='DURATION_COMPLETE' and stop['submission_enabled'] is False
        and stop['configuration_sha256']==w['configuration_sha256'] and stop['account_ref_sha256']==w['account_ref_sha256']
        and type(stop['observed_at_ms']) is int and w['ends_at_ms']<=stop['observed_at_ms']<=d['observed_at_ms']
        and stop['observed_equity']==e['observed_equity'],'PILOT_IDLE_STOP_BINDING')
    for k,v in e.items():
        if k.endswith('_sha256') and (k!='market_partition_sha256' or v is not None):_hash(v)
    for value in (result['closure_intent_sha256'],d['execution_state_sha256']):_hash(value)
    _hash(stop['origin_sha256'])
    require(type(e['observed_equity']) is str and Decimal(e['observed_equity']).is_finite(),
        'PILOT_IDLE_EQUITY')
    require(len(canonical(result))<=MAX_INPUT_BYTES,'PILOT_CLOSURE_RESULT_BUDGET')
    return d


def _idle_records(root,req,uid):
    config=_configuration(root,req,uid)
    from live.execution import demo_gold_pilot_enrollment as enrollment
    request_value=decode(_file(root,root/'gold-pilot-request.json',uid,32768))
    receipt=decode(_file(root,root/'gold-pilot/enrollment.json',uid,32768))
    intent=decode(_file(root,root/'gold-pilot/enrollment-intent.json',uid,enrollment.INTENT_MAXIMUM))
    return dict(request=request_value,configuration=config,receipt=receipt,intent=intent)


def _idle_inputs(root,req,uid,state,now,*,require_unchanged=True):
    records=_idle_records(root,req,uid)
    from live.execution import demo_market_partition_runtime as partition
    loaded=partition._load(role='executor',as_of_ms=now)
    record=None if loaded is None else loaded[0]
    committed=dict(state)
    if record is not None:committed['market_partition']=partition._descriptor(record)
    require(records['intent']['prepared']['execution_state_sha256']==records['receipt']['execution_state_sha256'],
        'PILOT_IDLE_ENROLLMENT_CHANGED')
    if require_unchanged:
        require(_digest(committed)==records['intent']['prepared']['execution_state_sha256'],'PILOT_IDLE_EXECUTION_CHANGED')
    return dict(**records,state=state,partition_record=record,source_commit=req['source_commit'])


def _idle_prior(root,req,uid,store,*,through=None,state=None):
    from tools.gold_strategy_controller import validate_result as validate_worker
    if store is None:
        from tools.gold_feedback_legacy import _scan,_not_native,_decision as validate_decision
        _not_native(root);snapshot=_scan(root,'strategy-decisions',uid)
        rows=[] if snapshot is None else [r['value'] for r in snapshot['records'].values()]
    else:
        base=store.binding['base_runtime'];ignored={'episode_id'} if base['schema']=='GOLD_BAR_AUTO_EPISODE_INPUT_V1' else set()
        require({k:v for k,v in req['runtime'].items() if k not in ignored}==
            {k:v for k,v in base.items() if k not in ignored},'PILOT_IDLE_RUNTIME_CHANGED')
        end=store.metadata()['frames'] if through is None else through;rows=[]
        for _,body in store.conn.execute('SELECT ordinal,claim FROM frames WHERE ordinal<=? ORDER BY ordinal',(end,)):
            claim=decode(body);raw=store.read_claim(claim,'result')
            require(raw is not None,'PILOT_IDLE_PRIOR_PARTIAL')
            result=decode(raw);d=validate_worker(result,source_commit=req['source_commit'],
                bundle_sha256=claim['bundle_sha256'],account=req['original_pilot_window']['account_ref_sha256'])
            require(store.read_claim(claim,'decision')==canonical(d) and 'feedback' not in result
                and 'closure_request' not in result and result.get('original_pilot_window')==req['original_pilot_window'],
                'PILOT_IDLE_PRIOR_RESULT')
            rows.append(d)
        from tools.gold_feedback_legacy import _decision as validate_decision
    for d in rows:
        validate_decision(d,source_commit=req['source_commit'],account=req['original_pilot_window']['account_ref_sha256'])
        require(d['status']!='STRATEGY_EPISODE_SETTLED' and 'closure_mode' not in d
            and 'closure_of_result_sha256' not in d,'PILOT_IDLE_PRIOR_COMPLETION')
        if state is not None:require(d.get('execution_state_sha256')==_digest(state),'PILOT_IDLE_PRIOR_EXECUTION')


def _idle_result(req,origin,stop,evidence,state,intent_sha):
    d=dict(schema='GOLD_STRATEGY_CYCLE_DECISION_V1',status='PILOT_STOPPED',source_commit=req['source_commit'],
        bundle_sha256=_digest(req['runtime']),account_ref_sha256=req['original_pilot_window']['account_ref_sha256'],
        observed_at_ms=evidence['observed_at_ms'],origin_sha256=_digest(origin),execution_state_sha256=_digest(state),
        pilot_control=stop,position_flat=True,execution_history_empty=all(not v for v in state.values()),
        action=None,draft=None,execution_constraints=None,cash_reconciled=False,submission_enabled=False,
        economic_authority=False,closure_mode='NO_EXECUTION_SINCE_ENROLLMENT')
    value=dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1',status='DECISION_RETAINED',source_commit=req['source_commit'],
        bundle_sha256=_digest(req['runtime']),decision_sha256=_digest(d),decision=d,submission_enabled=False,
        economic_authority=False,original_pilot_window=deepcopy(req['original_pilot_window']),
        closure_request=deepcopy(req),closure_intent_sha256=intent_sha,idle_evidence=evidence)
    validate_idle_result(value,req)
    return value


def _read_idle_intent(root,value,raw,*,retention,runtime,source_commit,account,uid):
    req=value['request'];records=_idle_records(root,req,uid);claim=value['observation_claim']
    require(value['original_claim'] is None,'PILOT_IDLE_ORIGINAL_CLAIM')
    status='INTENT_ONLY';origin=None;decision=None;result=None
    if retention is not None:
        require(retention.root==root and retention.role=='executor' and retention.uid==uid,'PILOT_CLOSURE_RETENTION')
        cp=value['original_checkpoint'];_prefix(retention,cp,allow_empty=True)
        _idle_prior(root,req,uid,retention,through=cp['frames'])
        retention._check_claim(claim)
        require(claim['ordinal']==cp['frames']+1 and claim['bundle_sha256']==_digest(runtime),'PILOT_CLOSURE_TAIL_CLAIM')
        current=retention.metadata()
        require(current['frames'] in (cp['frames'],claim['ordinal']),'PILOT_CLOSURE_EXTRA_FRAME')
        require(all(row[0]==claim['ordinal'] for row in retention.conn.execute(
            'SELECT ordinal FROM records WHERE sequence>?',(cp['records'],))),'PILOT_CLOSURE_PREFIX_APPEND')
        if current['frames']==cp['frames']:require(current==cp,'PILOT_CLOSURE_CHECKPOINT_CHANGED')
        else:
            status='PENDING';origin=retention.read_claim(claim,'origin')
            decision=retention.read_claim(claim,'decision');result=retention.read_claim(claim,'result')
            require(origin is not None or decision is result is None,'PILOT_CLOSURE_PARTIAL_ORDER')
    else:
        require(value['original_checkpoint'] is claim is None,'PILOT_CLOSURE_LEGACY_INTENT')
        path=root/'gold-pilot/closure-origin.json'
        if path.exists() or path.is_symlink():origin=_file(root,path,uid);status='PENDING'
    if origin is not None:origin=_origin(origin,req,retention,claim)
    saved_path=root/'gold-pilot/closure-result.json';saved=None
    if saved_path.exists() or saved_path.is_symlink():
        saved_raw=_file(root,saved_path,uid);saved=decode(saved_raw);d=validate_idle_result(saved,req)
        require(canonical(saved)==saved_raw and saved['closure_intent_sha256']==_digest(value)
            and origin is not None and d['origin_sha256']==_digest(origin),'PILOT_IDLE_SAVED_RESULT')
        e=saved['idle_evidence'];config=records['configuration']
        require(e['enrollment_intent_sha256']==_digest(records['intent'])
            and e['enrollment_receipt_sha256']==_digest(records['receipt'])
            and e['opening_origin_sha256']==config['opening_origin_sha256']
            and e['execution_state_sha256']==records['intent']['prepared']['execution_state_sha256'],
            'PILOT_IDLE_ENROLLMENT_CHANGED')
        from live.execution.demo_gold_pilot import validate_stop
        stop=decode(_file(root,root/'gold-pilot/stopped.json',uid,32768))
        require(validate_stop(stop,config=config,now_ms=e['observed_at_ms'])==d['pilot_control']
            and Decimal(e['observed_equity'])==Decimal(config['initial_equity']), 'PILOT_IDLE_STOP_CHANGED')
        _require_origin(origin,now_ms=e['observed_at_ms'])
        if retention is None:
            path=root/'strategy-decisions'/(_digest(d)+'.json')
            if path.exists() or path.is_symlink():decision=_file(root,path,uid)
        if decision is not None:
            require(decision==canonical(d),'PILOT_CLOSURE_PARTIAL_DECISION');decision=d
        if result is not None:
            require(result==saved_raw and decision is not None,'PILOT_IDLE_RESULT_CHANGED');result=saved;status='COMPLETE'
        elif retention is None and decision is not None:result=saved;status='COMPLETE'
        else:status='PENDING'
    else:require(decision is result is None,'PILOT_IDLE_SAVED_RESULT_MISSING')
    require(_file(root,root/'gold-pilot'/INTENT,uid)==raw,'PILOT_CLOSURE_INTENT_CHANGED')
    return dict(intent=value,intent_sha256=_digest(value),status=status,claim=claim,result=result,decision=decision)


def _execute_idle(root,req,*,source_commit,account,uid,clock,sleep):
    from tools.gold_pilot_retention import executor_retention
    from tools.gold_strategy_worker import capture_origin
    from tools.gold_pilot_idle_closure import validate_idle
    from live.execution import demo_gold_pilot as pilot
    _check_path(root,uid,directory=True);start=clock();runtime=req['runtime']
    with ExitStack() as stack:
        stores=_stores(stack,root)
        with _preparation_lease(stores['dispatch']):
            state=_state(**stores);inputs=_idle_inputs(root,req,uid,state,start,require_unchanged=False)
            config=inputs['configuration'];deadline=config['ends_at_ms']+CLOSING_MS
            committed=dict(state)
            if inputs['partition_record'] is not None:
                from live.execution.demo_market_partition_runtime import _descriptor
                committed['market_partition']=_descriptor(inputs['partition_record'])
            if _digest(committed)!=inputs['intent']['prepared']['execution_state_sha256']:
                require(not any((root/'gold-pilot'/name).exists() or (root/'gold-pilot'/name).is_symlink()
                    for name in (INTENT,'closure-origin.json','closure-result.json')),'PILOT_IDLE_EXECUTION_CHANGED')
                require(config['ends_at_ms']<=start<deadline,'PILOT_CLOSURE_NOT_DUE')
                require(_state(**stores)==state and _idle_inputs(root,req,uid,state,clock(),require_unchanged=False)==inputs,
                    'PILOT_IDLE_ORIGINAL_CHANGED')
                require(start<=clock()<deadline,'PILOT_CLOSURE_CLOCK')
                return dict(schema='GOLD_PILOT_IDLE_CLOSURE_RESULT_V1',status='NOT_APPLICABLE',
                    reason='EXECUTION_SINCE_ENROLLMENT',source_commit=source_commit,bundle_sha256=_digest(runtime),
                    request_sha256=_digest(req),original_pilot_window=deepcopy(req['original_pilot_window']),
                    submission_enabled=False,economic_authority=False)
        store=executor_retention(root,runtime,source_commit=source_commit,account=account,uid=uid)
        if store is not None:stack.callback(store.close)
        with _preparation_lease(stores['dispatch']):
            require(_state(**stores)==state and _idle_inputs(root,req,uid,state,clock())==inputs,
                'PILOT_IDLE_ORIGINAL_CHANGED')
            view=read_intent(root,retention=store,runtime=runtime,source_commit=source_commit,account=account,executor_uid=uid)
            if view is not None:
                require(view['intent']['request']==req,'PILOT_CLOSURE_REQUEST_CONFLICT')
                if view['status']=='COMPLETE':
                    result=view['result'];observed=result['idle_evidence']['observed_at_ms']
                    require(clock()>=observed,'PILOT_CLOSURE_CLOCK')
                    raw=store.read_claim(view['claim'],'origin') if store is not None else _file(root,root/'gold-pilot/closure-origin.json',uid)
                    require(validate_idle(**inputs,origin=decode(raw),now_ms=observed)==result['idle_evidence'],
                        'PILOT_IDLE_EVIDENCE_CHANGED')
                    require(_state(**stores)==state and _idle_inputs(root,req,uid,state,clock())==inputs,
                        'PILOT_IDLE_ORIGINAL_CHANGED')
                    return result
                historical=_historical_origin(root,req,view,store,config,uid,clock())
            else:historical=None
            if historical is None:require(config['ends_at_ms']<=start<deadline,'PILOT_CLOSURE_NOT_DUE')
            if view is None:
                _idle_prior(root,req,uid,store,state=state)
                cp=None;claim=None
                if store is not None:
                    cp=store.metadata();row=store.conn.execute('SELECT claim FROM frames ORDER BY ordinal DESC LIMIT 1').fetchone()
                    if row:
                        wait=decode(row[0])['admitted_at_ms']+store.binding['minimum_interval_ms']-clock()
                        if wait>0:sleep(wait/1000)
                    admitted=clock();require(start<=admitted<deadline,'PILOT_CLOSURE_CLOCK')
                    claim=dict(schema='GOLD_PILOT_OBSERVATION_CLAIM_V1',ordinal=cp['frames']+1,admitted_at_ms=admitted,
                        bundle_sha256=_digest(runtime),capacity_sha256=store.capacity_sha256,submission_enabled=False)
                value=dict(schema=INTENT_SCHEMA,request=deepcopy(req),request_sha256=_digest(req),original_claim=None,
                    original_checkpoint=cp,observation_claim=claim,closing_deadline_ms=deadline,
                    submission_enabled=False,economic_authority=False)
                _write_intent(root,value)
                view=read_intent(root,retention=store,runtime=runtime,source_commit=source_commit,account=account,executor_uid=uid)
            if historical is not None:origin,stop=historical;observed=stop['observed_at_ms']
            else:
                before=clock();require(start<=before<deadline,'PILOT_CLOSURE_CLOCK')
                if store is not None:
                    if view['status']=='INTENT_ONLY':require(store.claim(bundle=runtime,now_ms=view['claim']['admitted_at_ms'])==view['claim'],
                        'PILOT_CLOSURE_CLAIM_CHANGED')
                    origin=capture_origin(root=root,source_commit=source_commit,retention=store,claim=view['claim'])
                else:
                    path=root/'gold-pilot/closure-origin.json'
                    if path.exists() or path.is_symlink():origin=_origin(_file(root,path,uid),req)
                    else:
                        origin=capture_origin(root=root,source_commit=source_commit)
                        require(pilot.retain_once(path,origin,maximum=MAX_INPUT_BYTES)==origin,'PILOT_CLOSURE_ORIGIN_CONFLICT')
                observed=clock();require(before<=observed<deadline,'PILOT_CLOSURE_CLOCK')
                stop=None
            evidence=validate_idle(**inputs,origin=origin,now_ms=observed)
            require(_state(**stores)==state and _idle_inputs(root,req,uid,state,clock())==inputs,'PILOT_IDLE_ORIGINAL_CHANGED')
            if historical is None:
                finished=clock();require(observed<=finished<deadline,'PILOT_CLOSURE_CLOCK')
                _require_origin(origin,now_ms=finished)
                stop=pilot.observe(dict(account_ref_sha256=account,equity=evidence['observed_equity']),
                    now_ms=observed,origin_sha256=_digest(origin))
            result=_idle_result(req,origin,stop,evidence,state,view['intent_sha256'])
            require(pilot.retain_once(root/'gold-pilot/closure-result.json',result,maximum=MAX_INPUT_BYTES)==result,
                'PILOT_CLOSURE_RESULT_CONFLICT')
            _persist(root,view,store,result['decision'],result,uid)
            if historical is None:
                returned=clock();require(finished<=returned<deadline,'PILOT_CLOSURE_CLOCK')
                _require_origin(origin,now_ms=returned)
            return result
