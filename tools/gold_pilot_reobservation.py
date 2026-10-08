"""One bounded successor to a durable, expired closure account observation.

This protocol grants no order or account authority. The original failed frame,
cash, enrollment and execution history remain immutable. Reader states describe
actual retained slots, never an assumed current account or wall clock.
"""
from contextlib import ExitStack
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from live.execution.demo_pre_submit import _state, _require_origin, _preparation_lease, _retain
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import gold_pilot_closure as closure
from tools.gold_pilot_retention_store import canonical, present, CLOSING_MS, MAX_RECORD_BYTES

SCHEMA='GOLD_PILOT_CLOSURE_REOBSERVATION_INPUT_V1'
CANDIDATE_SCHEMA='GOLD_PILOT_CLOSURE_STALE_CANDIDATE_V1'
INTENT_SCHEMA='GOLD_PILOT_CLOSURE_REOBSERVATION_INTENT_V1'
VIEW_SCHEMA='GOLD_PILOT_CLOSURE_REOBSERVATION_VIEW_V1'
DECISION_SCHEMA='GOLD_PILOT_CLOSURE_EXPIRED_DECISION_V1'
RESULT_SCHEMA='GOLD_PILOT_CLOSURE_EXPIRED_RESULT_V1'
INTENT='closure-reobservation-intent.json'
ADMISSION='closure-reobservation-admission.json'
CAPTURE='closure-reobservation-capture.json'
CAPTURE_WITNESS='closure-reobservation-capture-witness.json'
EXPIRED='closure-expired-result.json'
ORIGIN='closure-reobservation-origin.json'
FILES={INTENT:16384,ADMISSION:4096,CAPTURE:4096,CAPTURE_WITNESS:4096,EXPIRED:16384,ORIGIN:MAX_RECORD_BYTES}
INPUT_FIELDS='schema source_commit runtime original_pilot_window original_closure_intent_sha256 original_closure_request_sha256 original_origin_sha256 attempt_index maximum_additional_attempts submission_enabled economic_authority'
SUBJECT_FIELDS='source_commit account_ref_sha256 runtime_sha256 original_pilot_window original_closure_intent_sha256 original_closure_request_sha256 original_origin_sha256 failed_claim original_completed_checkpoint failed_origin_checkpoint legacy_snapshot_sha256 execution_state_sha256 next_ordinal capacity_sha256 not_before_ms closing_deadline_ms created_at_ms attempt_index maximum_additional_attempts submission_enabled economic_authority'
CANDIDATE_FIELDS='schema status source_commit runtime_sha256 original_pilot_window original_closure_intent_sha256 original_closure_request_sha256 original_origin_sha256 original_origin_observed_at_ms original_origin_completed_at_ms inspected_at_ms execution_state_verified submission_enabled economic_authority'
DECISION_FIELDS='schema status source_commit account_ref_sha256 bundle_sha256 original_closure_intent_sha256 original_closure_request_sha256 origin_sha256 failed_claim_sha256 intent_subject_sha256 original_origin_observed_at_ms original_origin_completed_at_ms expiry_detected_at_ms original_pilot_window execution_state_sha256 current_account_verified pilot_stopped action draft execution_constraints submission_enabled economic_authority'


def _same(left,right):
    return canonical(left)==canonical(right)


def _time(value):
    require(type(value) is int and 0<value<=2**63-1,'REOBSERVATION_TIME')
    return value


def _path(root,name):
    return root/'gold-pilot'/name


def _read(root,name,uid,maximum=None):
    path=_path(root,name)
    raw=closure._file(root,path,uid,maximum or FILES.get(name,closure.MAX_INPUT_BYTES))
    value=decode(raw)
    require(raw==canonical(value),'REOBSERVATION_CANONICAL')
    return value


def _save(root,name,value):
    from live.execution.demo_gold_pilot import retain_once
    require(_same(retain_once(_path(root,name),value,maximum=FILES.get(name,closure.MAX_INPUT_BYTES)),value),
        'REOBSERVATION_FILE_CONFLICT')


def present_reobservation(root):
    return any(present(_path(Path(root),name)) for name in FILES)


def _base(runtime,base):
    ignored={'episode_id'} if base.get('schema')=='GOLD_BAR_AUTO_EPISODE_INPUT_V1' else set()
    require(_same({k:v for k,v in runtime.items() if k not in ignored},
        {k:v for k,v in base.items() if k not in ignored}),'REOBSERVATION_BASE')


def _old(root,store,base,source,account,uid):
    old=_read(root,closure.INTENT,uid)
    _fields(old,'schema request request_sha256 original_claim original_checkpoint observation_claim closing_deadline_ms submission_enabled economic_authority')
    req=closure._request(old['request'],source,account);_base(req['runtime'],base)
    require(old['schema']==closure.INTENT_SCHEMA and old['request_sha256']==_digest(req)
        and old['closing_deadline_ms']==req['original_pilot_window']['ends_at_ms']+CLOSING_MS
        and old['submission_enabled'] is old['economic_authority'] is False,'REOBSERVATION_ORIGINAL_INTENT')
    closure._configuration(root,req,uid)
    claim=old['observation_claim']
    if store is not None:
        require(store.root==root and store.role=='executor' and store.uid==uid
            and store.verify_history is True and store.lock is not None
            and _same(store.binding['base_runtime'],base) and store.binding['source_commit']==source
            and store.binding['account_ref_sha256']==account,'REOBSERVATION_AUDITED_STORE')
        cp=old['original_checkpoint'];idle=req['schema']==closure.IDLE_SCHEMA
        closure._prefix(store,cp,allow_empty=idle)
        if idle:
            require(old['original_claim'] is None,'REOBSERVATION_IDLE_CLAIM')
            closure._idle_prior(root,req,uid,store,through=cp['frames'])
        else:require(closure._original(root,req,uid,store,through=cp['frames'])==old['original_claim'],
            'REOBSERVATION_ORIGINAL_CLAIM')
        store._check_claim(claim)
        require(claim['ordinal']==cp['frames']+1 and claim['bundle_sha256']==_digest(req['runtime']),
            'REOBSERVATION_FAILED_CLAIM')
        raw=store.read_claim(claim,'origin')
        require(raw is not None,'REOBSERVATION_ORIGINAL_ORIGIN_MISSING')
    else:
        from tools.gold_feedback_legacy import _not_native
        _not_native(root)
        require(claim is old['original_checkpoint'] is old['original_claim'] is None,'REOBSERVATION_LEGACY_CLAIM')
        if req['schema']!=closure.IDLE_SCHEMA:closure._original(root,req,uid,None)
        raw=closure._file(root,_path(root,'closure-origin.json'),uid)
    origin=closure._origin(raw,req,store,claim)
    _require_origin(origin,now_ms=origin['completed_at_ms'])
    return old,req,origin


def _failed_cut(store,old,origin):
    cp=old['original_checkpoint'];claim=old['observation_claim'];n=claim['ordinal']
    rows=store.conn.execute('SELECT sequence,kind,ordinal,identity,LENGTH(payload) FROM records WHERE sequence>? ORDER BY sequence LIMIT 1',
        (cp['records'],)).fetchall()
    body=canonical(origin);claim_body=canonical(claim)
    require(rows==[(cp['records']+1,'origin',n,_digest(origin),len(body))],'REOBSERVATION_ORIGIN_CUT')
    return dict(cp,frames=n,records=cp['records']+1,bytes=cp['bytes']+len(body),
        claim_bytes=cp['claim_bytes']+len(claim_body),audit_bytes=cp['audit_bytes']+
        len(canonical(store._event('claim',n,_digest(claim),len(claim_body))))+
        len(canonical(store._event('origin',n,_digest(origin),len(body)))))


def _legacy_snapshot(root,uid,*,exclude=(),originally_absent=False):
    from tools.gold_feedback_legacy import _scan
    return legacy_snapshot_commitment(_scan(root,'strategy-decisions',uid),
        exclude=exclude,originally_absent=originally_absent)


def legacy_snapshot_commitment(snap,*,exclude=(),originally_absent=False):
    """Pure original metadata commitment, never evidence of current custody."""
    if snap is None:return _digest(dict(directory=None,records={}))
    records={key:dict(identity=list(row['identity']),sha256=_digest(row['value']))
        for key,row in snap['records'].items() if key not in exclude}
    if originally_absent:
        require(not records and set(snap['records'])<=set(exclude),'REOBSERVATION_LEGACY_ORIGINAL_ABSENCE')
    return _digest(dict(directory=None if originally_absent else list(snap['identity'][:6]),records=records))


def inspect_stale_candidate(root,*,retention,base_runtime,runtime,source_commit,account,executor_uid,now_ms):
    root=Path(root);_time(now_ms)
    require(not present_reobservation(root),'REOBSERVATION_ALREADY_STARTED')
    old,req,origin=_old(root,retention,base_runtime,source_commit,account,executor_uid)
    require(_same(req['runtime'],runtime),'REOBSERVATION_RUNTIME')
    if present(_path(root,'stopped.json')):
        from live.execution.demo_gold_pilot import validate_stop
        stop=_read(root,'stopped.json',executor_uid,32768)
        validate_stop(stop,config=closure._configuration(root,req,executor_uid),now_ms=now_ms)
        require(stop['origin_sha256']==_digest(origin),'REOBSERVATION_EXISTING_STOP')
        _require_origin(origin,now_ms=stop['observed_at_ms'])
        return None
    if present(_path(root,'closure-result.json')):
        closure.read_intent(root,retention=retention,runtime=runtime,source_commit=source_commit,
            account=account,executor_uid=executor_uid)
        return None
    if retention is not None:
        claim=old['observation_claim']
        require(retention.read_claim(claim,'decision') is retention.read_claim(claim,'result') is None,
            'REOBSERVATION_NOT_ORIGIN_ONLY')
        require(retention.metadata()==_failed_cut(retention,old,origin),'REOBSERVATION_EXTRA_FRAME')
    elif req['schema']==closure.IDLE_SCHEMA:closure._idle_prior(root,req,executor_uid,None)
    require(now_ms>=origin['completed_at_ms'],'REOBSERVATION_CLOCK')
    if now_ms-origin['observed_at_ms']<=30000:return None
    require(req['original_pilot_window']['ends_at_ms']<=now_ms<old['closing_deadline_ms'],'REOBSERVATION_CLOCK')
    return dict(schema=CANDIDATE_SCHEMA,status='ORIGIN_ONLY_STALE',source_commit=source_commit,
        runtime_sha256=_digest(runtime),original_pilot_window=deepcopy(req['original_pilot_window']),
        original_closure_intent_sha256=_digest(old),original_closure_request_sha256=_digest(req),
        original_origin_sha256=_digest(origin),original_origin_observed_at_ms=origin['observed_at_ms'],
        original_origin_completed_at_ms=origin['completed_at_ms'],inspected_at_ms=now_ms,
        execution_state_verified=False,submission_enabled=False,economic_authority=False)


def request(runtime,original_request,evidence):
    require(type(evidence) is dict,'REOBSERVATION_EVIDENCE')
    req=closure._request(original_request,original_request['source_commit'],
        original_request['original_pilot_window']['account_ref_sha256'])
    require(_same(req['runtime'],runtime),'REOBSERVATION_RUNTIME')
    if evidence.get('schema')==CANDIDATE_SCHEMA:
        _fields(evidence,CANDIDATE_FIELDS)
        require(evidence['status']=='ORIGIN_ONLY_STALE' and evidence['execution_state_verified'] is False
            and evidence['submission_enabled'] is evidence['economic_authority'] is False,'REOBSERVATION_CANDIDATE')
        bound=evidence
    else:
        require(evidence.get('schema')==VIEW_SCHEMA and _same(evidence.get('request'),req),'REOBSERVATION_VIEW')
        bound=evidence['intent']['subject']
    require(bound['source_commit']==req['source_commit'] and bound['runtime_sha256']==_digest(runtime)
        and _same(bound['original_pilot_window'],req['original_pilot_window'])
        and bound['original_closure_request_sha256']==_digest(req),'REOBSERVATION_EVIDENCE_BINDING')
    value=dict(schema=SCHEMA,source_commit=req['source_commit'],runtime=deepcopy(runtime),
        original_pilot_window=deepcopy(req['original_pilot_window']),
        **{k:bound[k] for k in ('original_closure_intent_sha256','original_closure_request_sha256','original_origin_sha256')},
        attempt_index=1,maximum_additional_attempts=1,submission_enabled=False,economic_authority=False)
    for k in ('original_closure_intent_sha256','original_closure_request_sha256','original_origin_sha256'):closure._hash(value[k])
    require(len(canonical(value))<=closure.MAX_INPUT_BYTES,'REOBSERVATION_INPUT_BUDGET')
    return value


def _diagnostic(subject,origin):
    decision=dict(schema=DECISION_SCHEMA,status='EXPIRED_BEFORE_STOP',source_commit=subject['source_commit'],
        account_ref_sha256=subject['account_ref_sha256'],bundle_sha256=subject['runtime_sha256'],
        original_closure_intent_sha256=subject['original_closure_intent_sha256'],
        original_closure_request_sha256=subject['original_closure_request_sha256'],origin_sha256=subject['original_origin_sha256'],
        failed_claim_sha256=None if subject['failed_claim'] is None else _digest(subject['failed_claim']),
        intent_subject_sha256=_digest(subject),original_origin_observed_at_ms=origin['observed_at_ms'],
        original_origin_completed_at_ms=origin['completed_at_ms'],expiry_detected_at_ms=subject['created_at_ms'],
        original_pilot_window=deepcopy(subject['original_pilot_window']),execution_state_sha256=subject['execution_state_sha256'],
        current_account_verified=False,pilot_stopped=False,action=None,draft=None,execution_constraints=None,
        submission_enabled=False,economic_authority=False)
    result=dict(schema=RESULT_SCHEMA,status='EXPIRED_BEFORE_STOP',source_commit=subject['source_commit'],
        bundle_sha256=subject['runtime_sha256'],decision_sha256=_digest(decision),decision=decision,
        original_pilot_window=deepcopy(subject['original_pilot_window']),intent_subject_sha256=_digest(subject),
        submission_enabled=False,economic_authority=False)
    return decision,result


def validate_expired_result(result,*,intent):
    _fields(intent,'schema subject subject_sha256 expired_decision_sha256 expired_result_sha256')
    subject=intent['subject'];_fields(subject,SUBJECT_FIELDS)
    _fields(result,'schema status source_commit bundle_sha256 decision_sha256 decision original_pilot_window intent_subject_sha256 submission_enabled economic_authority')
    d=result['decision'];_fields(d,DECISION_FIELDS)
    expected_d,expected_r=_diagnostic(subject,dict(observed_at_ms=d['original_origin_observed_at_ms'],
        completed_at_ms=d['original_origin_completed_at_ms']))
    require(intent['schema']==INTENT_SCHEMA and intent['subject_sha256']==_digest(subject)
        and _same(result,expected_r) and _same(d,expected_d) and _digest(result)==intent['expired_result_sha256']
        and _digest(d)==intent['expired_decision_sha256'],'REOBSERVATION_DIAGNOSTIC')
    require(_time(d['original_origin_observed_at_ms'])<=_time(d['original_origin_completed_at_ms'])<=
        _time(d['expiry_detected_at_ms']) and d['expiry_detected_at_ms']-d['original_origin_observed_at_ms']>30000,
        'REOBSERVATION_DIAGNOSTIC_TIME')
    require(len(canonical(result))<=FILES[EXPIRED],'REOBSERVATION_DIAGNOSTIC_BUDGET')
    return d


def budget_bounds(original_request):
    """Canonical upper bounds from the exact request and existing scalar limits."""
    req=closure._request(original_request,original_request['source_commit'],
        original_request['original_pilot_window']['account_ref_sha256'])
    h='f'*64;t=2**63-1;w=req['original_pilot_window']
    stop=dict(schema='GOLD_DEMO_PILOT_STOP_V1',status='STOPPED',reason='DURATION_COMPLETE',
        configuration_sha256=h,account_ref_sha256=h,observed_at_ms=t,observed_equity='9'*100,
        origin_sha256=h,submission_enabled=False)
    if req['schema']==closure.IDLE_SCHEMA:
        d={k:None for k in closure.DECISION_FIELDS.split() if not k.startswith('closure_')}
        d.update(schema='GOLD_STRATEGY_CYCLE_DECISION_V1',status='PILOT_STOPPED',source_commit=req['source_commit'],
            bundle_sha256=h,account_ref_sha256=h,observed_at_ms=t,origin_sha256=h,execution_state_sha256=h,
            pilot_control=stop,position_flat=True,execution_history_empty=False,cash_reconciled=False,
            submission_enabled=False,economic_authority=False,closure_mode='NO_EXECUTION_SINCE_ENROLLMENT')
        e=dict(schema='GOLD_PILOT_IDLE_EVIDENCE_V1',mode=d['closure_mode'],source_commit=req['source_commit'],
            account_ref_sha256=h,request_sha256=h,configuration_sha256=h,enrollment_intent_sha256=h,
            enrollment_receipt_sha256=h,opening_origin_sha256=h,origin_sha256=h,execution_state_sha256=h,
            market_partition_sha256=h,started_at_ms=t,ends_at_ms=t,observed_at_ms=t,position_flat=True,
            observed_equity='9'*100,submission_enabled=False,economic_authority=False)
        result=dict(schema='GOLD_STRATEGY_WORKER_RESULT_V1',status='DECISION_RETAINED',source_commit=req['source_commit'],
            bundle_sha256=h,decision_sha256=h,decision=d,submission_enabled=False,economic_authority=False,
            original_pilot_window=w,closure_request=req,closure_intent_sha256=h,idle_evidence=e)
    else:
        d=closure._decision(req,dict(completed_at_ms=t),stop)
        result=closure._result(req,d,h,native=False)
    subject={k:h for k in SUBJECT_FIELDS.split()}
    subject.update(source_commit=req['source_commit'],runtime_sha256=_digest(req['runtime']),original_pilot_window=w,
        failed_claim=dict(a=h),created_at_ms=t)
    expired_d,expired_r=_diagnostic(subject,dict(observed_at_ms=t,completed_at_ms=t))
    return dict(expired_decision=len(canonical(expired_d)),expired_result=len(canonical(expired_r)),
        origin=MAX_RECORD_BYTES,decision=len(canonical(d)),result=len(canonical(result)))


def _slot(store,claim,kind):
    raw=store.read_claim(claim,kind)
    return None if raw is None else decode(raw)


def _check_subject(subject,old,req,origin,root,store,uid):
    _fields(subject,SUBJECT_FIELDS)
    for key in ('source_commit','original_pilot_window'):
        require(_same(subject[key],req[key]),'REOBSERVATION_SUBJECT')
    require(subject['account_ref_sha256']==req['original_pilot_window']['account_ref_sha256']
        and subject['runtime_sha256']==_digest(req['runtime'])
        and subject['original_closure_intent_sha256']==_digest(old)
        and subject['original_closure_request_sha256']==_digest(req)
        and subject['original_origin_sha256']==_digest(origin)
        and _same(subject['failed_claim'],old['observation_claim'])
        and _same(subject['original_completed_checkpoint'],old['original_checkpoint'])
        and subject['closing_deadline_ms']==old['closing_deadline_ms']
        and type(subject['attempt_index']) is int and subject['attempt_index']==1
        and type(subject['maximum_additional_attempts']) is int and subject['maximum_additional_attempts']==1
        and subject['submission_enabled'] is subject['economic_authority'] is False,'REOBSERVATION_SUBJECT')
    closure._hash(subject['execution_state_sha256'])
    require(req['original_pilot_window']['ends_at_ms']<=_time(subject['created_at_ms'])<old['closing_deadline_ms']
        and subject['created_at_ms']-origin['observed_at_ms']>30000
        and origin['completed_at_ms']<=subject['created_at_ms']<=_time(subject['not_before_ms'])<old['closing_deadline_ms'],
        'REOBSERVATION_SUBJECT_TIME')
    if store is not None:
        require(_same(subject['failed_origin_checkpoint'],_failed_cut(store,old,origin))
            and type(subject['next_ordinal']) is int and subject['next_ordinal']==old['observation_claim']['ordinal']+1
            and subject['capacity_sha256']==store.capacity_sha256 and subject['legacy_snapshot_sha256'] is None,
            'REOBSERVATION_SUBJECT_CUT')
    else:
        require(subject['failed_origin_checkpoint'] is subject['next_ordinal'] is subject['capacity_sha256'] is None,
            'REOBSERVATION_LEGACY_SUBJECT')
        closure._hash(subject['legacy_snapshot_sha256'])


def _new_origin(root,req,uid,store,claim):
    from tools import demo_executor_origin as capture
    raw=store.read_claim(claim,'origin') if store is not None and claim is not None else (
        closure._file(root,_path(root,ORIGIN),uid) if store is None and present(_path(root,ORIGIN)) else None)
    if raw is None:return None
    value=decode(raw);capture.original.validate(value)
    require(raw==canonical(value) and value['status'] in ('CAPTURED','FAILED') and value['symbol']=='XAUUSDT'
        and value['source_sha256']==capture.source_hash()
        and value['account_ref_sha256'] in (None,req['original_pilot_window']['account_ref_sha256'])
        and (value['status']=='FAILED' or value['account_ref_sha256']==req['original_pilot_window']['account_ref_sha256']),
        'REOBSERVATION_ORIGIN')
    if claim is not None:require(claim['admitted_at_ms']<=value['observed_at_ms'],'REOBSERVATION_ORIGIN_TIME')
    return value


def _final_check(root,req,uid,origin,result,intent_sha,state_sha):
    require(_same(result['closure_request'],req) and result['closure_intent_sha256']==intent_sha,
        'REOBSERVATION_FINAL_BINDING')
    idle=req['schema']==closure.IDLE_SCHEMA
    d=closure.validate_idle_result(result,req) if idle else closure.validate_result(result,req['original_result'])
    require(d['origin_sha256']==_digest(origin) and d['execution_state_sha256']==state_sha,
        'REOBSERVATION_FINAL_STATE')
    from live.execution.demo_gold_pilot import validate_stop
    stop=_read(root,'stopped.json',uid,32768);config=closure._configuration(root,req,uid)
    require(validate_stop(stop,config=config,now_ms=d['observed_at_ms'])==d['pilot_control']
        and stop['origin_sha256']==_digest(origin) and origin['completed_at_ms']<=stop['observed_at_ms']
        <=d['observed_at_ms']<config['ends_at_ms']+CLOSING_MS,'REOBSERVATION_FINAL_STOP')
    fresh=_require_origin(origin,now_ms=stop['observed_at_ms'])
    require(Decimal(fresh['wallet'])==Decimal(stop['observed_equity']),'REOBSERVATION_FINAL_EQUITY')
    if idle:
        records=closure._idle_records(root,req,uid);e=result['idle_evidence']
        require(e['enrollment_intent_sha256']==_digest(records['intent'])
            and e['enrollment_receipt_sha256']==_digest(records['receipt'])
            and e['opening_origin_sha256']==config['opening_origin_sha256']
            and e['execution_state_sha256']==records['intent']['prepared']['execution_state_sha256']
            and Decimal(e['observed_equity'])==Decimal(config['initial_equity']),'REOBSERVATION_IDLE_RECORDS')
    return d


def _read_reobservation_content(root,*,retention,base_runtime,source_commit,account,executor_uid,expected_runtime=None):
    """Internal content verifier; legacy inode commitment is NOT checked here.

    Production callers use read_reobservation. Only the isolated checkpoint
    verifier combines this content check with authenticated capture metadata;
    this function alone is not a verified reobservation view or authority.
    """
    root=Path(root);uid=executor_uid;store=retention
    if not present(_path(root,INTENT)):
        require(not present_reobservation(root),'REOBSERVATION_INTENT_MISSING')
        return None
    intent=_read(root,INTENT,uid);_fields(intent,'schema subject subject_sha256 expired_decision_sha256 expired_result_sha256')
    old,req,original_origin=_old(root,store,base_runtime,source_commit,account,uid)
    if expected_runtime is not None:require(_same(req['runtime'],expected_runtime),'REOBSERVATION_RUNTIME')
    s=intent['subject'];_check_subject(s,old,req,original_origin,root,store,uid)
    expired_d,expired_r=_diagnostic(s,original_origin);validate_expired_result(expired_r,intent=intent)
    claim=None;admission=None;capture=None;origin=None;packet=None;decision=None;result=None
    d=None;r=None;status='INTENT_ONLY'
    if store is not None:
        failed=old['observation_claim'];n=failed['ordinal'];head=store.metadata()
        require(head['frames'] in (n,n+1),'REOBSERVATION_EXTRA_FRAME')
        d=_slot(store,failed,'decision');r=_slot(store,failed,'result')
        if head['frames']==n+1:
            require(_same(r,expired_r),'REOBSERVATION_PREDECESSOR_INCOMPLETE')
            claim=decode(store.conn.execute('SELECT claim FROM frames WHERE ordinal=?',(n+1,)).fetchone()[0])
            store._check_claim(claim)
            require(claim['bundle_sha256']==s['runtime_sha256'] and s['not_before_ms']<=claim['admitted_at_ms']<s['closing_deadline_ms'],
                'REOBSERVATION_SUCCESSOR_CLAIM')
        actual=list(store.conn.execute('SELECT kind,ordinal FROM records WHERE sequence>? ORDER BY sequence',
            (s['failed_origin_checkpoint']['records'],)))
        permitted=[('decision',n),('result',n),('origin',n+1),('decision',n+1),('result',n+1)]
        require(actual==permitted[:len(actual)],'REOBSERVATION_TAIL_ORDER')
    else:
        path=root/'strategy-decisions'/(_digest(expired_d)+'.json')
        if present(path):d=decode(closure._file(root,path,uid,16384))
        if present(_path(root,EXPIRED)):r=_read(root,EXPIRED,uid)
    if d is not None:
        require(_same(d,expired_d),'REOBSERVATION_EXPIRED_DECISION');status='EXPIRED_DECISION_RETAINED'
    if r is not None:
        require(d is not None and _same(r,expired_r),'REOBSERVATION_EXPIRED_RESULT');status='EXPIRED_RETAINED'
    if claim is not None:status='CLAIMED_UNBOUND'
    if present(_path(root,ADMISSION)):
        admission=_read(root,ADMISSION,uid)
        _fields(admission,'schema reobservation_intent_sha256 claim claim_sha256 admitted_at_ms submission_enabled economic_authority')
        require(r is not None and admission['schema']=='GOLD_PILOT_CLOSURE_REOBSERVATION_ADMISSION_V1'
            and admission['reobservation_intent_sha256']==_digest(intent) and _same(admission['claim'],claim)
            and admission['claim_sha256']==(None if claim is None else _digest(claim))
            and s['not_before_ms']<=_time(admission['admitted_at_ms'])<s['closing_deadline_ms']
            and (claim is None or admission['admitted_at_ms']==claim['admitted_at_ms'])
            and admission['submission_enabled'] is admission['economic_authority'] is False,
            'REOBSERVATION_ADMISSION')
        require(store is None or claim is not None,'REOBSERVATION_CLAIM_MISSING');status='ADMITTED'
    if present(_path(root,CAPTURE)) or present(_path(root,CAPTURE_WITNESS)):
        require(present(_path(root,CAPTURE)) and present(_path(root,CAPTURE_WITNESS)),
            'REOBSERVATION_UNKNOWN_CAPTURE_OUTCOME')
        capture=_read(root,CAPTURE,uid)
        require(_same(capture,_read(root,CAPTURE_WITNESS,uid)),'REOBSERVATION_CAPTURE_WITNESS_CHANGED')
        _fields(capture,'schema reobservation_intent_sha256 admission_sha256 started_at_ms maximum_capture_attempts submission_enabled economic_authority')
        require(admission is not None and capture['schema']=='GOLD_PILOT_CLOSURE_REOBSERVATION_CAPTURE_V1'
            and capture['reobservation_intent_sha256']==_digest(intent) and capture['admission_sha256']==_digest(admission)
            and admission['admitted_at_ms']<=_time(capture['started_at_ms'])<s['closing_deadline_ms']
            and type(capture['maximum_capture_attempts']) is int and capture['maximum_capture_attempts']==1
            and capture['submission_enabled'] is capture['economic_authority'] is False,'REOBSERVATION_CAPTURE')
        status='CAPTURE_STARTED'
    origin=_new_origin(root,req,uid,store,claim)
    if origin is not None:
        require(capture is not None and capture['started_at_ms']<=origin['observed_at_ms'],'REOBSERVATION_CAPTURE_MISSING')
        status='ORIGIN_RETAINED' if origin['status']=='CAPTURED' else 'ORIGIN_FAILED'
    stop=None
    if present(_path(root,'stopped.json')):
        stop=_read(root,'stopped.json',uid,32768)
        from live.execution.demo_gold_pilot import validate_stop
        config=closure._configuration(root,req,uid)
        validate_stop(stop,config=config,now_ms=stop['observed_at_ms'])
        require(origin is not None and origin['status']=='CAPTURED' and stop['origin_sha256']==_digest(origin)
            and origin['completed_at_ms']<=stop['observed_at_ms']<s['closing_deadline_ms'],'REOBSERVATION_STOP')
        _require_origin(origin,now_ms=stop['observed_at_ms']);status='STOP_RETAINED'
    if present(_path(root,'closure-result.json')):
        require(store is None or req['schema']==closure.IDLE_SCHEMA,'REOBSERVATION_UNEXPECTED_PACKET')
        packet=_read(root,'closure-result.json',uid)
        require(stop is not None,'REOBSERVATION_PACKET_WITHOUT_STOP')
        _final_check(root,req,uid,origin,packet,_digest(intent),s['execution_state_sha256'])
        status='FINAL_PACKET_RETAINED'
    if store is not None and claim is not None:
        decision=_slot(store,claim,'decision');result=_slot(store,claim,'result')
    elif store is None and stop is not None:
        expected=packet['decision'] if packet is not None else (
            closure._decision(req,origin,stop) if req['schema']!=closure.IDLE_SCHEMA else None)
        if expected is not None:
            path=root/'strategy-decisions'/(_digest(expected)+'.json')
            if present(path):decision=decode(closure._file(root,path,uid))
        if decision is not None:result=packet
    if decision is not None:
        require(stop is not None,'REOBSERVATION_DECISION_WITHOUT_STOP')
        if req['schema']==closure.IDLE_SCHEMA:
            require(packet is not None and _same(packet['decision'],decision),'REOBSERVATION_IDLE_PACKET_MISSING')
            candidate=packet
        else:candidate=closure._result(req,decision,_digest(intent),native=store is not None)
        _final_check(root,req,uid,origin,candidate,_digest(intent),s['execution_state_sha256'])
        status='FINAL_DECISION_RETAINED'
    if result is not None:
        require(decision is not None and _same(result['decision'],decision),'REOBSERVATION_FINAL_DECISION_MISSING')
        _final_check(root,req,uid,origin,result,_digest(intent),s['execution_state_sha256'])
        if req['schema']!=closure.IDLE_SCHEMA:
            require(result['original_result_provenance']==('EXECUTOR_ORIGINAL' if store is not None else 'CONTROLLER_ORIGINAL'),
                'REOBSERVATION_PROVENANCE')
        status='COMPLETE'
    require(_same(_read(root,INTENT,uid),intent) and _same(_read(root,closure.INTENT,uid),old),'REOBSERVATION_INTENT_CHANGED')
    return dict(schema=VIEW_SCHEMA,status=status,intent=intent,intent_sha256=_digest(intent),
        original_intent_sha256=_digest(old),request=req,admission=admission,capture_start=capture,
        failed_claim=old['observation_claim'],claim=claim,expired_decision=d,expired_result=r,
        origin_sha256=None if origin is None else _digest(origin),origin_status=None if origin is None else origin['status'],
        completion_packet=packet,decision=decision,result=result)



def verify_legacy_snapshot(view,snapshot):
    """Check the original permanent commitment against an actual captured scan.

    This is pure historical evidence validation. It cannot make copied inodes
    current or enable recovery in a production namespace.
    """
    require(view['failed_claim'] is None,'REOBSERVATION_LEGACY_SUBJECT')
    subject=view['intent']['subject'];expected=subject['legacy_snapshot_sha256']
    closure._hash(expected)
    # Compute the fixed diagnostic even before its slot was written.
    # The intent already binds this digest through the complete content reader.
    exclude=[view['intent']['expired_decision_sha256']]
    if view['decision'] is not None:exclude.append(_digest(view['decision']))
    observed=legacy_snapshot_commitment(snapshot,exclude=exclude)
    if observed!=expected:
        require(expected==_digest(dict(directory=None,records={}))
            and legacy_snapshot_commitment(snapshot,exclude=exclude,originally_absent=True)==expected,
            'REOBSERVATION_LEGACY_CHANGED')
    return expected


def read_reobservation(root,*,retention,base_runtime,source_commit,account,executor_uid,expected_runtime=None):
    """Read fixed lineage plus actual metadata under the original caller lease."""
    view=_read_reobservation_content(root,retention=retention,base_runtime=base_runtime,
        source_commit=source_commit,account=account,executor_uid=executor_uid,
        expected_runtime=expected_runtime)
    if view is not None and retention is None:
        from tools.gold_feedback_legacy import _scan
        verify_legacy_snapshot(view,_scan(Path(root),'strategy-decisions',executor_uid))
        require(_same(_read(Path(root),INTENT,executor_uid),view['intent'])
            and _digest(_read(Path(root),closure.INTENT,executor_uid))==view['original_intent_sha256'],
            'REOBSERVATION_INTENT_CHANGED')
    return view

def _executor_budget(store,req,view=None):
    from tools.gold_pilot_retention_store import PAGE_BYTES,ROW_METADATA_BYTES
    bounds=budget_bounds(req);head=store.metadata();binding=store.binding
    slots=[bounds[k] for k in ('expired_decision','expired_result','origin','decision','result')]
    missing_claim=1
    if view is not None:
        actual=[view['expired_decision'],view['expired_result'],view['origin_sha256'],view['decision'],view['result']]
        slots=[size for size,value in zip(slots,actual) if value is None]
        missing_claim=int(view['claim'] is None)
    records=len(slots);events=records+missing_claim
    require(head['frames']+missing_claim<=binding['max_frames']
        and all(size<=binding['max_record_bytes'] for size in slots)
        and head['bytes']+sum(slots)<=binding['max_total_bytes']
        and head['records']+records<=binding['max_frames']*binding['role_records_per_frame']['executor']
        and head['records']+head['frames']+events<=binding['max_audit_events']
        and head['audit_bytes']+events*binding['event_max_bytes']<=binding['max_audit_bytes'],
        'REOBSERVATION_CAPACITY')
    store._physical()
    db=store.area/'records.sqlite3';stat=db.stat()
    occupied=max(stat.st_size,stat.st_blocks*512,store.conn.execute('PRAGMA page_count').fetchone()[0]*PAGE_BYTES)
    growth=sum(slots)+(records+missing_claim)*(PAGE_BYTES+ROW_METADATA_BYTES+binding['claim_max_bytes'])
    require(occupied+growth<=binding['max_database_bytes'],'REOBSERVATION_PHYSICAL_CAPACITY')
    audit_used=sum(max(p.stat().st_size,p.stat().st_blocks*512) for p in (store.audit/'events').iterdir())
    require(audit_used+events*PAGE_BYTES<=binding['max_audit_allocated_bytes'],'REOBSERVATION_AUDIT_CAPACITY')


def _legacy_budget(root,uid,req,view=None):
    from tools.gold_feedback_legacy import _scan,MAX_RECORDS,MAX_TOTAL_BYTES
    snap=_scan(root,'strategy-decisions',uid);rows={} if snap is None else snap['records']
    sizes=budget_bounds(req)
    missing=[sizes['expired_decision'],sizes['decision']]
    if view is not None:missing=[size for size,v in zip(missing,(view['expired_decision'],view['decision'])) if v is None]
    require(len(rows)+len(missing)<=MAX_RECORDS and sum(len(v['raw']) for v in rows.values())+sum(missing)<=MAX_TOTAL_BYTES,
        'REOBSERVATION_LEGACY_CAPACITY')


def _original_state(root,req,uid,stores,now):
    state=_state(**stores)
    if req['schema']==closure.IDLE_SCHEMA:
        inputs=closure._idle_inputs(root,req,uid,state,now)
        return state,inputs,None
    require(_digest(state)==req['original_result']['decision']['execution_state_sha256'],'REOBSERVATION_EXECUTION_CHANGED')
    wallet,artifact=closure._artifact(root,req,uid,now)
    return state,dict(configuration=closure._configuration(root,req,uid)),(wallet,artifact)


def _subject(root,old,req,origin,store,uid,state,now):
    claim=old['observation_claim']
    return dict(source_commit=req['source_commit'],account_ref_sha256=req['original_pilot_window']['account_ref_sha256'],
        runtime_sha256=_digest(req['runtime']),original_pilot_window=deepcopy(req['original_pilot_window']),
        original_closure_intent_sha256=_digest(old),original_closure_request_sha256=_digest(req),
        original_origin_sha256=_digest(origin),failed_claim=claim,original_completed_checkpoint=old['original_checkpoint'],
        failed_origin_checkpoint=None if store is None else _failed_cut(store,old,origin),
        legacy_snapshot_sha256=_legacy_snapshot(root,uid) if store is None else None,
        execution_state_sha256=_digest(state),next_ordinal=None if claim is None else claim['ordinal']+1,
        capacity_sha256=None if store is None else store.capacity_sha256,not_before_ms=now,
        closing_deadline_ms=old['closing_deadline_ms'],created_at_ms=now,attempt_index=1,
        maximum_additional_attempts=1,submission_enabled=False,economic_authority=False)


def execute(root,req,*,source_commit,account,executor_uid,clock,sleep):
    """Executor-only: preserve the stale attempt, then observe once afresh."""
    root=Path(root);uid=executor_uid;_fields(req,INPUT_FIELDS)
    require(req['schema']==SCHEMA and req['source_commit']==source_commit
        and type(req['attempt_index']) is int and req['attempt_index']==1
        and type(req['maximum_additional_attempts']) is int and req['maximum_additional_attempts']==1
        and req['submission_enabled'] is req['economic_authority'] is False,'REOBSERVATION_INPUT')
    old=_read(root,closure.INTENT,uid);base_request=closure._request(old['request'],source_commit,account)
    require(_same(base_request['runtime'],req['runtime']),'REOBSERVATION_RUNTIME')
    from tools.gold_pilot_retention_store import active
    require(old['observation_claim'] is None or active(root),'REOBSERVATION_HISTORY_MISSING')
    from tools.gold_pilot_retention import executor_retention
    from live.execution import demo_gold_pilot as pilot
    from tools import demo_executor_origin as capture
    start=_time(clock())
    with ExitStack() as stack:
        store=executor_retention(root,req['runtime'],source_commit=source_commit,account=account,uid=uid)
        if store is not None:stack.callback(store.close)
        base=req['runtime'] if store is None else store.binding['base_runtime']
        stores=closure._stores(stack,root)
        with _preparation_lease(stores['dispatch']):
            state,inputs,cash=_original_state(root,base_request,uid,stores,start)
            def original_unchanged():
                require(_original_state(root,base_request,uid,stores,clock())==(state,inputs,cash),
                    'REOBSERVATION_ORIGINAL_CHANGED')
                require(_same(_read(root,closure.INTENT,uid),old),'REOBSERVATION_OLD_INTENT_CHANGED')
            def read():
                return read_reobservation(root,retention=store,base_runtime=base,source_commit=source_commit,
                    account=account,executor_uid=uid,expected_runtime=req['runtime'])
            view=read()
            if view is None:
                candidate=inspect_stale_candidate(root,retention=store,base_runtime=base,runtime=req['runtime'],
                    source_commit=source_commit,account=account,executor_uid=uid,now_ms=clock())
                require(candidate is not None and _same(req,request(req['runtime'],base_request,candidate)),'REOBSERVATION_NOT_ELIGIBLE')
                old_checked,_,origin=_old(root,store,base,source_commit,account,uid)
                require(_same(old_checked,old),'REOBSERVATION_OLD_INTENT_CHANGED')
                # Historical validation proves eligibility only; it does not create STOP.
                if base_request['schema']==closure.IDLE_SCHEMA:
                    from tools.gold_pilot_idle_closure import validate_idle
                    validate_idle(**inputs,origin=origin,now_ms=origin['completed_at_ms'])
                else:require(Decimal(_require_origin(origin,now_ms=origin['completed_at_ms'])['wallet'])==cash[0],
                    'REOBSERVATION_OLD_CASH_CHANGED')
                if store is not None:_executor_budget(store,base_request)
                else:_legacy_budget(root,uid,base_request)
                now=clock();require(start<=now<old['closing_deadline_ms'],'REOBSERVATION_CLOCK')
                original_unchanged()
                subject=_subject(root,old,base_request,origin,store,uid,state,now)
                diagnostic,diagnostic_result=_diagnostic(subject,origin)
                intent=dict(schema=INTENT_SCHEMA,subject=subject,subject_sha256=_digest(subject),
                    expired_decision_sha256=_digest(diagnostic),expired_result_sha256=_digest(diagnostic_result))
                validate_expired_result(diagnostic_result,intent=intent)
                _save(root,INTENT,intent);view=read()
            require(_same(req,request(req['runtime'],base_request,view)),'REOBSERVATION_REQUEST_CHANGED')
            intent=view['intent'];subject=intent['subject'];deadline=subject['closing_deadline_ms']
            require(subject['execution_state_sha256']==_digest(state),'REOBSERVATION_EXECUTION_CHANGED')
            if view['status']=='COMPLETE':
                require(clock()>=view['result']['decision']['observed_at_ms'],'REOBSERVATION_CLOCK')
                original_unchanged();return view['result']
            historical=present(_path(root,'stopped.json'))
            if not historical:
                require(subject['created_at_ms']<=start<=clock()<deadline,'REOBSERVATION_CLOCK')
                if store is not None:_executor_budget(store,base_request,view)
                else:_legacy_budget(root,uid,base_request,view)
            original_origin=_old(root,store,base,source_commit,account,uid)[2]
            diagnostic,diagnostic_result=_diagnostic(subject,original_origin)
            if view['expired_decision'] is None:
                if store is None:_retain(root/'strategy-decisions',diagnostic)
                else:store.put(view['failed_claim'],'decision',diagnostic)
            if view['expired_result'] is None:
                if store is None:_save(root,EXPIRED,diagnostic_result)
                else:store.put(view['failed_claim'],'result',diagnostic_result)
            if view['claim'] is None and store is not None:
                before=clock();wait=old['observation_claim']['admitted_at_ms']+store.binding['minimum_interval_ms']-before
                if wait>0:sleep(wait/1000)
                now=clock();require(start<=before<=now<deadline,'REOBSERVATION_CLOCK')
                claim=store.claim(bundle=req['runtime'],now_ms=now)
            else:claim=view['claim']
            if view['admission'] is None:
                admitted=clock() if claim is None else claim['admitted_at_ms']
                require(subject['not_before_ms']<=admitted<deadline,'REOBSERVATION_CLOCK')
                _save(root,ADMISSION,dict(schema='GOLD_PILOT_CLOSURE_REOBSERVATION_ADMISSION_V1',
                    reobservation_intent_sha256=_digest(intent),claim=claim,
                    claim_sha256=None if claim is None else _digest(claim),admitted_at_ms=admitted,
                    submission_enabled=False,economic_authority=False))
            view=read();origin=_new_origin(root,base_request,uid,store,claim)
            if origin is None:
                require(view['capture_start'] is None,'REOBSERVATION_UNKNOWN_CAPTURE_OUTCOME')
                def before_capture():
                    original_unchanged();now=clock()
                    require(start<=now<deadline,'REOBSERVATION_CLOCK')
                    require(not present(_path(root,CAPTURE)) and not present(_path(root,CAPTURE_WITNESS)),
                        'REOBSERVATION_UNKNOWN_CAPTURE_OUTCOME')
                    witness=dict(schema='GOLD_PILOT_CLOSURE_REOBSERVATION_CAPTURE_V1',
                        reobservation_intent_sha256=_digest(intent),admission_sha256=_digest(view['admission']),
                        started_at_ms=now,maximum_capture_attempts=1,submission_enabled=False,economic_authority=False)
                    _save(root,CAPTURE,witness)
                    require(now<=clock()<deadline,'REOBSERVATION_CLOCK')
                    _save(root,CAPTURE_WITNESS,witness)
                    require(_same(_read(root,CAPTURE,uid),witness) and _same(_read(root,CAPTURE_WITNESS,uid),witness),
                        'REOBSERVATION_CAPTURE_WITNESS_CHANGED')
                    require(now<=clock()<deadline,'REOBSERVATION_CLOCK')
                if store is not None:
                    origin=capture.capture_for_pilot(symbol='XAUUSDT',source_commit=source_commit,
                        retention=store,claim=claim,before_capture=before_capture)
                else:
                    captured=capture.run(symbol='XAUUSDT',source_commit=source_commit,before_capture=before_capture)
                    path=Path(captured['path'])
                    require(path.parent==root/'execution-origins/XAUUSDT','REOBSERVATION_ORIGIN_PATH')
                    origin=decode(closure._file(root,path,uid));_save(root,ORIGIN,origin)
            require(origin['status']=='CAPTURED','REOBSERVATION_CAPTURE_FAILED')
            stop=_read(root,'stopped.json',uid,32768) if historical else None
            observed=stop['observed_at_ms'] if historical else clock()
            require(origin['completed_at_ms']<=observed<=clock(),'REOBSERVATION_CLOCK')
            if not historical:
                require(start<=observed<deadline,'REOBSERVATION_CLOCK')
                require(observed-origin['observed_at_ms']<=30000,'REOBSERVATION_EXHAUSTED')
            fresh=_require_origin(origin,now_ms=observed)
            if base_request['schema']==closure.IDLE_SCHEMA:
                from tools.gold_pilot_idle_closure import validate_idle
                evidence=validate_idle(**inputs,origin=origin,now_ms=observed)
                wallet=evidence['observed_equity']
            else:
                require(Decimal(fresh['wallet'])==cash[0],'REOBSERVATION_CASH_CHANGED');wallet=fresh['wallet']
            original_unchanged()
            if not historical:
                finished=clock();require(observed<=finished<deadline,'REOBSERVATION_CLOCK')
                _require_origin(origin,now_ms=finished)
                stop=pilot.observe(dict(account_ref_sha256=account,equity=wallet),now_ms=finished,origin_sha256=_digest(origin))
                observed=finished
                if base_request['schema']==closure.IDLE_SCHEMA:evidence=validate_idle(**inputs,origin=origin,now_ms=observed)
            if base_request['schema']==closure.IDLE_SCHEMA:
                result=closure._idle_result(base_request,origin,stop,evidence,state,_digest(intent))
                _save(root,'closure-result.json',result)
            else:
                result=closure._result(base_request,closure._decision(base_request,origin,stop),_digest(intent),native=store is not None)
            _final_check(root,base_request,uid,origin,result,_digest(intent),subject['execution_state_sha256'])
            if store is not None:
                store.put(claim,'decision',result['decision']);store.put(claim,'result',result)
            else:
                _retain(root/'strategy-decisions',result['decision']);_save(root,'closure-result.json',result)
            original_unchanged();done=read()
            require(done['status']=='COMPLETE' and _same(done['result'],result),'REOBSERVATION_COMPLETION')
            if not historical:
                returned=clock();require(observed<=returned<deadline,'REOBSERVATION_CLOCK')
                _require_origin(origin,now_ms=returned)
            return result
