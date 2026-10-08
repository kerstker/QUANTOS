"""Bounded observation of original legacy feedback files, without native claims.

The caller owns the original controller/executor read window. Two full reads
reject observed changes; they are not an atomic snapshot or deletion witness.
Legacy executor storage contains decisions, not complete worker result mirrors.
"""
from itertools import islice
import os
from pathlib import Path
import re

from live.execution.demo_batch_risk import _digest, _fields
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_protected_publish as custody, demo_supervisor as supervisor
from tools import gold_bar_pilot_capacity as capacity
from tools.demo_resident_recovery import _check_path
from tools.gold_pilot_retention_store import active, canonical, present

MAX_RECORDS=1000
MAX_TOTAL_BYTES=64*1024*1024
MAX_RECORD_BYTES=65536
BAR_SCHEMAS=('GOLD_BAR_RETAINED_EPISODE_INPUT_V1','GOLD_BAR_FRESH_EPISODE_INPUT_V1','GOLD_BAR_AUTO_EPISODE_INPUT_V1')
STATUSES={'RECOVERY_REQUIRED','PILOT_CONFIGURATION_REQUIRED','STRATEGY_EPISODE_SETTLED',
    'HOLDING','OTHER_POSITION_REQUIRES_RECONCILIATION','PILOT_STOPPED','NO_ENTRY_SIGNAL','DECISION_REQUIRES_APPROVAL'}


def _identity(info):
    return tuple(getattr(info,key) for key in ('st_dev','st_ino','st_uid','st_gid','st_mode',
        'st_nlink','st_size','st_mtime_ns','st_ctime_ns'))


def _hash(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None,'LEGACY_FEEDBACK_HASH')


def _members(fd,maximum=MAX_RECORDS):
    with os.scandir(fd) as entries:names=[entry.name for entry in islice(entries,maximum+1)]
    require(len(names)<=maximum,'LEGACY_FEEDBACK_RECORD_BUDGET')
    require(all(re.fullmatch('[a-f0-9]{64}\\.json',name) for name in names),'LEGACY_FEEDBACK_MEMBER')
    return tuple(sorted(names))


def _scan(root,name,uid,*,record_limit=MAX_RECORDS,maximum=MAX_RECORD_BYTES):
    path=root/name
    if not present(path):return None
    _check_path(path,uid,directory=True)
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        before=_identity(os.fstat(fd));require(_identity(path.lstat())==before,'LEGACY_FEEDBACK_CHANGED')
        names=_members(fd,record_limit);records={};total=0
        for leaf in names:
            info=os.stat(leaf,dir_fd=fd,follow_symlinks=False)
            raw=custody._read(fd,leaf,uid,maximum)
            after=os.stat(leaf,dir_fd=fd,follow_symlinks=False)
            require(_identity(info)==_identity(after),'LEGACY_FEEDBACK_CHANGED')
            total+=len(raw);require(total<=MAX_TOTAL_BYTES,'LEGACY_FEEDBACK_BYTE_BUDGET')
            value=decode(raw)
            require(type(value) is dict and raw==canonical(value) and _digest(value)==leaf[:-5],
                'LEGACY_FEEDBACK_ORIGINAL_HASH')
            records[leaf[:-5]]=dict(raw=raw,value=value,identity=_identity(after))
        require(names==_members(fd,record_limit) and before==_identity(os.fstat(fd))==_identity(path.lstat()),
            'LEGACY_FEEDBACK_CHANGED')
        return dict(identity=before,records=records)
    finally:os.close(fd)


def _not_native(root):
    require(not active(root) and not capacity.state_present(root),
        'LEGACY_FEEDBACK_NATIVE_HISTORY_PRESENT')


def _pilot_original(root,*,runtime,uid,source_commit,account):
    """Non-bar enrollment is compatible; its original baseline stays bound."""
    from live.execution import demo_gold_pilot_enrollment as enrollment
    from tools.gold_pilot_install import _read_target
    if runtime.get('schema') in BAR_SCHEMAS:
        original=capacity.original_context(root,runtime,uid=uid,account=account,allow_pending=True)
        require(original is None,'LEGACY_FEEDBACK_NATIVE_HISTORY_PRESENT')
        return None
    request_path=root/'gold-pilot-request.json';area=root/'gold-pilot'
    if not present(request_path):
        require(not present(area),'LEGACY_FEEDBACK_PILOT_REQUEST_MISSING')
        return None
    def fixed(path,maximum):
        before=_identity(path.lstat());raw=_read_target(path,uid=uid,maximum=maximum)
        require(before==_identity(path.lstat()),'LEGACY_FEEDBACK_CHANGED')
        return dict(identity=before,raw=raw,value=decode(raw))
    request=fixed(request_path,32768)
    enrollment._request_check(request['value'],source_commit=source_commit,account=account)
    result=dict(request=request,configuration=None,area=None,records={})
    if not present(area):return result
    _check_path(area,uid,directory=True);before=_identity(area.lstat())
    closure_names={'closure-intent.json','closure-origin.json','closure-result.json'}
    from tools.gold_pilot_reobservation import FILES as reobservation_files, INTENT as reobservation_intent
    allowed={'configuration.json','enrollment.json','enrollment-intent.json','binding.json','stopped.json','enrollment-reviews'}|closure_names|set(reobservation_files)
    names=tuple(sorted(entry.name for entry in islice(area.iterdir(),len(allowed)+1)))
    require(set(names)<=allowed and {'configuration.json','enrollment.json'}<=set(names),
        'LEGACY_FEEDBACK_PILOT_PARTIAL')
    require(not set(names)&closure_names or 'closure-intent.json' in names,'LEGACY_FEEDBACK_CLOSURE_INTENT_MISSING')
    require(not set(names)&set(reobservation_files) or
        {'closure-intent.json',reobservation_intent}<=set(names),'LEGACY_FEEDBACK_REOBSERVATION_INTENT_MISSING')
    from tools.demo_issuer import MAX_INPUT_BYTES
    records={name:fixed(area/name,reobservation_files[name] if name in reobservation_files
        else enrollment.INTENT_MAXIMUM if name=='enrollment-intent.json'
        else MAX_INPUT_BYTES if name in closure_names else 32768)
        for name in names if name!='enrollment-reviews'}
    reviews=_scan(area,'enrollment-reviews',uid,record_limit=32,maximum=32768)
    require(names==tuple(sorted(entry.name for entry in islice(area.iterdir(),len(allowed)+1)))
        and before==_identity(area.lstat()),'LEGACY_FEEDBACK_CHANGED')
    config=enrollment.completed_configuration_records(configuration=records['configuration.json']['value'],
        receipt=records['enrollment.json']['value'],
        intent=None if 'enrollment-intent.json' not in records else records['enrollment-intent.json']['value'],
        request=request['value'])
    result.update(configuration=config,area=before,records=records,reviews=reviews)
    return result


def _snapshot(*,root,executor_root,executor_uid,base_runtime,source_commit,account):
    supervisor._root_private_directory(root)
    for parent in reversed(executor_root.parents):supervisor._require_protected(parent.lstat(),directory=True)
    _check_path(executor_root,executor_uid,directory=True)
    require(root.resolve()==root and executor_root.resolve()==executor_root,'LEGACY_FEEDBACK_ROOT')
    for location in (root,executor_root):_not_native(location)
    require(not any(present(root/name) for name in ('gold-pilot-request.json','gold-pilot')),
        'LEGACY_FEEDBACK_NATIVE_HISTORY_PRESENT')
    before=(_identity(root.lstat()),_identity(executor_root.lstat()))
    result=dict(controller=_scan(root,'strategy-decisions',os.geteuid()),
        executor=_scan(executor_root,'strategy-decisions',executor_uid),
        deliveries=_scan(root,'strategy-feedback-deliveries',os.geteuid()),
        pilot=_pilot_original(executor_root,runtime=base_runtime,uid=executor_uid,
            source_commit=source_commit,account=account))
    require(before==(_identity(root.lstat()),_identity(executor_root.lstat())),'LEGACY_FEEDBACK_CHANGED')
    result['roots']=before
    return result


def _scope(root,base,current,source_commit,account):
    require(type(base) is dict and type(current) is dict and base.get('source_commit')==source_commit,
        'LEGACY_FEEDBACK_RUNTIME')
    schema=base.get('schema')
    if schema in BAR_SCHEMAS:
        from tools.gold_strategy_budget_enrollment import runtime_check
        runtime_check(base,source_commit)
    elif schema=='GOLD_STRATEGY_WORKER_INPUT_V1':
        _fields(base,'schema source_commit trial_manifest_sha256 observation_sha256')
        _hash(base['trial_manifest_sha256']);_hash(base['observation_sha256'])
    else:
        _fields(base,'schema source_commit trial_manifest_sha256 plan')
        require(schema=='GOLD_STRATEGY_FRESH_OBSERVATION_INPUT_V1' and type(base['plan']) is dict,
            'LEGACY_FEEDBACK_RUNTIME')
        _hash(base['trial_manifest_sha256'])
    if schema=='GOLD_BAR_AUTO_EPISODE_INPUT_V1':
        from tools.gold_bar_rotation import feedback_scope
        return feedback_scope(root=root,base=base,account=account,current=current)
    require(current==base,'LEGACY_FEEDBACK_RUNTIME_CHANGED')
    return dict(current=dict(base),previous=None,rotation_result=None,rotation_sha256=None)


def _decision(value,*,source_commit,account):
    require(type(value) is dict and value.get('schema')=='GOLD_STRATEGY_CYCLE_DECISION_V1'
        and value.get('status') in STATUSES and value.get('source_commit')==source_commit
        and value.get('account_ref_sha256')==account and value.get('submission_enabled') is False
        and value.get('economic_authority') is False,'LEGACY_FEEDBACK_DECISION_BINDING')
    _hash(value.get('bundle_sha256'))


def _export(result):
    value=result.get('feedback');settled=result['decision']['status']=='STRATEGY_EPISODE_SETTLED'
    require(value is None or settled,'LEGACY_FEEDBACK_EXPORT_BINDING')
    if value is None:return False
    require(type(value) is dict,'LEGACY_FEEDBACK_EXPORT_BINDING')
    retained=value.get('status') in ('STRATEGY_FEEDBACK_RETAINED','BAR_STRATEGY_FEEDBACK_RETAINED')
    if retained:
        require(value.get('submission_enabled') is False and value.get('economic_authority') is False,
            'LEGACY_FEEDBACK_EXPORT_BINDING')
        _hash(value.get('artifact_sha256'));_hash(value.get('feedback_sha256'))
        require(value['status']!='BAR_STRATEGY_FEEDBACK_RETAINED' or value.get('cash_reconciled') is True,
            'LEGACY_FEEDBACK_EXPORT_BINDING')
    else:
        require(value.get('submission_enabled') is False and (
            value.get('status')=='BAR_CASH_FEEDBACK_REQUIRED' or
            value.get('status')=='WAITING_STRATEGY_CASH_WINDOW' and value.get('economic_authority') is False),
            'LEGACY_FEEDBACK_EXPORT_BINDING')
    return retained


def _records(snapshot,name):
    return {} if snapshot[name] is None else snapshot[name]['records']


def _closure(snapshot,*,executor_root,current_runtime,source_commit,account,executor_uid):
    pilot=snapshot['pilot']
    if pilot is None or 'closure-intent.json' not in pilot['records']:
        return None
    from tools.gold_pilot_closure import read_intent
    view=read_intent(executor_root,retention=None,runtime=current_runtime,source_commit=source_commit,
        account=account,executor_uid=executor_uid)
    require(type(view) is dict and view.get('status') in ('INTENT_ONLY','PENDING','COMPLETE')
        and view.get('claim') is None and canonical(view.get('intent'))==pilot['records']['closure-intent.json']['raw']
        and view.get('intent_sha256')==_digest(view['intent']),'LEGACY_FEEDBACK_CLOSURE_PROOF')
    if view['status']=='COMPLETE':
        require('closure-result.json' in pilot['records'] and
            pilot['records']['closure-result.json']['raw']==canonical(view['result']),
            'LEGACY_FEEDBACK_CLOSURE_RESULT_CHANGED')
    return view


def _reobservation(snapshot,*,executor_root,base_runtime,current_runtime,source_commit,account,executor_uid):
    from tools import gold_pilot_reobservation as retry
    pilot=snapshot['pilot']
    if pilot is None or not set(pilot['records'])&set(retry.FILES):return None
    view=retry.read_reobservation(executor_root,retention=None,base_runtime=base_runtime,
        expected_runtime=current_runtime,source_commit=source_commit,account=account,executor_uid=executor_uid)
    require(type(view) is dict and view.get('schema')==retry.VIEW_SCHEMA
        and view['failed_claim'] is view['claim'] is None
        and canonical(view['intent'])==pilot['records'][retry.INTENT]['raw']
        and view['intent_sha256']==_digest(view['intent'])
        and view['original_intent_sha256']==_digest(pilot['records']['closure-intent.json']['value'])
        and view['request']==pilot['records']['closure-intent.json']['value']['request'],
        'LEGACY_FEEDBACK_REOBSERVATION_PROOF')
    for field,name in (('admission',retry.ADMISSION),('capture_start',retry.CAPTURE),
        ('expired_result',retry.EXPIRED),('completion_packet','closure-result.json')):
        value=view[field];record=pilot['records'].get(name)
        require((value is None and record is None) or
            (type(value) is dict and record is not None and canonical(value)==record['raw']),
            'LEGACY_FEEDBACK_REOBSERVATION_CHANGED')
    return view


def _classify(snapshot,*,scope,source_commit,account,closure=None,reobservation=None):
    from tools.gold_strategy_controller import validate_result
    executor=_records(snapshot,'executor');controller=_records(snapshot,'controller')
    pilot=snapshot['pilot']
    require(pilot is None or pilot['configuration'] is not None or
        not executor and not controller and not _records(snapshot,'deliveries'),'LEGACY_FEEDBACK_PILOT_PARTIAL')
    linked={};completions={};retained={}
    expired=None if reobservation is None else reobservation['expired_decision']
    expired_id=None if expired is None else _digest(expired)
    if reobservation is not None:
        require(closure is None,'LEGACY_FEEDBACK_TWO_CLOSURE_PROOFS')
        # Internal completion projection from the independently read new chain;
        # this is never persisted or presented as an original V1 intent.
        closure=dict(intent={'request':reobservation['request']},
            intent_sha256=reobservation['intent_sha256'],claim=None,
            status='COMPLETE' if reobservation['status']=='COMPLETE' else 'PENDING',
            decision=reobservation['decision'],result=reobservation['result'])
    for identity,record in executor.items():
        if identity==expired_id:
            require(record['raw']==canonical(expired),'LEGACY_FEEDBACK_EXPIRED_DECISION_CHANGED')
        else:_decision(record['value'],source_commit=source_commit,account=account)
    for identity,record in controller.items():
        result=record['value'];_hash(result.get('bundle_sha256'))
        if result.get('schema')=='GOLD_PILOT_CLOSURE_EXPIRED_RESULT_V1':
            from tools.gold_pilot_reobservation import validate_expired_result
            require(reobservation is not None and reobservation['expired_result'] is not None
                and record['raw']==canonical(reobservation['expired_result']),
                'LEGACY_FEEDBACK_EXPIRED_RESULT_CHANGED')
            decision=validate_expired_result(result,intent=reobservation['intent'])
        else:decision=validate_result(result,source_commit=source_commit,bundle_sha256=result['bundle_sha256'],account=account)
        if 'original_pilot_window' in result:
            require(pilot is not None and pilot['configuration'] is not None,'LEGACY_FEEDBACK_PILOT_ORIGINAL_MISSING')
            config=pilot['configuration'];window=result['original_pilot_window']
            require(window['request_sha256']==_digest(pilot['request']['value'])
                and window['configuration_sha256']==_digest(config)
                and all(window[key]==config[key] for key in ('started_at_ms','ends_at_ms')),
                'LEGACY_FEEDBACK_PILOT_WINDOW')
        if _digest(decision)!=expired_id:_decision(decision,source_commit=source_commit,account=account)
        did=_digest(decision)
        require(did in executor and executor[did]['raw']==canonical(decision),'LEGACY_FEEDBACK_EXECUTOR_ORIGINAL_MISSING')
        require(did not in linked,'LEGACY_FEEDBACK_AMBIGUOUS_RESULT')
        require('research_feedback' not in result,'LEGACY_FEEDBACK_RESULT_ENRICHED')
        linked[did]=identity;retained[identity]=_export(result)
        if decision['status']=='STRATEGY_EPISODE_SETTLED':
            require(decision.get('position_flat') is True,'LEGACY_FEEDBACK_RESULT_NOT_SETTLED')
            bundle=result['bundle_sha256']
            require(bundle not in completions,'LEGACY_FEEDBACK_AMBIGUOUS_COMPLETION')
            completions[bundle]=identity
    allowed=set()
    if expired_id is not None:
        require(expired_id in executor and executor[expired_id]['raw']==canonical(expired),
            'LEGACY_FEEDBACK_EXPIRED_DECISION_MISSING')
        allowed.add(expired_id)
    idle=False
    if closure is not None:
        req=closure['intent']['request']
        from tools.gold_pilot_closure import IDLE_SCHEMA
        idle=req['schema']==IDLE_SCHEMA
        identity=None
        if idle:
            require(not completions,'LEGACY_FEEDBACK_IDLE_HAS_COMPLETION')
        else:
            original=req['original_result'];identity=req['original_result_sha256']
            require(identity==_digest(original) and identity in controller
                and controller[identity]['raw']==canonical(original),'LEGACY_FEEDBACK_CLOSURE_ORIGINAL_MISSING')
        decision=closure.get('decision')
        if decision is not None:
            did=_digest(decision)
            require(did in executor and executor[did]['raw']==canonical(decision)
                and (decision.get('closure_mode')=='NO_EXECUTION_SINCE_ENROLLMENT' if idle
                    else decision.get('closure_of_result_sha256')==identity),'LEGACY_FEEDBACK_CLOSURE_DECISION_MISSING')
            allowed.add(did)
            if did in linked:
                require(closure['status']=='COMPLETE' and controller[linked[did]]['raw']==canonical(closure['result']),
                    'LEGACY_FEEDBACK_CLOSURE_MIRROR_CHANGED')
    require(set(linked)|allowed==set(executor),'LEGACY_FEEDBACK_CONTROLLER_ORIGINAL_MISSING')
    require(all(not any(k.startswith('closure_') for k in record['value']) or
        closure is not None and identity in allowed for identity,record in executor.items()),
        'LEGACY_FEEDBACK_CLOSURE_INTENT_MISSING')
    deliveries={}
    for record in _records(snapshot,'deliveries').values():
        value=record['value']
        require(set(value)==set('status cycle_sha256 feedback_sha256 source_verified submission_enabled economic_authority'.split())
            and value.get('status')=='RESEARCH_FEEDBACK_DELIVERED'
            and all(value.get(k) is False for k in ('source_verified','submission_enabled','economic_authority')),
            'LEGACY_FEEDBACK_DELIVERY_BINDING')
        _hash(value['cycle_sha256']);_hash(value['feedback_sha256'])
        identity=completions.get(value['cycle_sha256'])
        require(identity is not None and retained[identity]
            and controller[identity]['value']['feedback']['feedback_sha256']==value['feedback_sha256'],
            'LEGACY_FEEDBACK_DELIVERY_ORIGINAL_MISSING')
        require(identity not in deliveries,'LEGACY_FEEDBACK_AMBIGUOUS_DELIVERY')
        deliveries[identity]=record
    selected={}
    for relation,runtime in (('CURRENT',scope['current']),('PREVIOUS',scope['previous'])):
        if runtime is None:continue
        bundle=_digest(runtime);identity=completions.get(bundle)
        if identity is None:continue
        record=controller[identity];result=record['value'];delivery=deliveries.get(identity)
        if retained[identity]:
            expected='BAR_STRATEGY_FEEDBACK_RETAINED' if runtime['schema'] in BAR_SCHEMAS else 'STRATEGY_FEEDBACK_RETAINED'
            require(result['feedback']['status']==expected,'LEGACY_FEEDBACK_EXPORT_SCOPE')
        selected[relation]=dict(status='DELIVERED' if delivery is not None else
            ('PENDING_DELIVERY' if retained[identity] else 'FEEDBACK_NOT_RETAINED'),mode='LEGACY_DISCOVERED',
            claim=None,result_provenance='CONTROLLER_ORIGINAL',decision_provenance='EXECUTOR_ORIGINAL',
            result_sha256=identity,result_bytes=record['raw'],result=result,bundle_sha256=bundle,relation=relation,
            delivery=None if delivery is None else delivery['value'],
            delivery_sha256=None if delivery is None else _digest(delivery['value']),
            delivery_bytes=None if delivery is None else delivery['raw'],
            delivery_state='ABSENCE_OBSERVED' if delivery is None else 'PRESENT',
            rotation_sha256=scope['rotation_sha256'],submission_enabled=False)
    rotated=scope['rotation_result']
    if rotated is not None and rotated['decision']['status']=='STRATEGY_EPISODE_SETTLED':
        previous=selected.get('PREVIOUS')
        require(previous is not None and previous['delivery'] is not None and
            dict(previous['result'],research_feedback=previous['delivery'])==rotated,
            'LEGACY_FEEDBACK_ROTATION_ORIGINAL_MISSING')
    found=selected.get('CURRENT') or selected.get('PREVIOUS')
    if idle:
        require(found is None and not completions and not deliveries,'LEGACY_FEEDBACK_IDLE_HAS_COMPLETION')
        return dict(status='IDLE_CLOSURE',mode='IDLE',result=None,delivery=None,
            closure={k:closure[k] for k in ('intent_sha256','status','claim','result')},
            closure_request=closure['intent']['request'],controller_checkpoint=None,
            executor_checkpoint=None,submission_enabled=False)
    if closure is not None:
        require(found is not None and found['status']=='DELIVERED'
            and found['result_sha256']==closure['intent']['request']['original_result_sha256'],
            'LEGACY_FEEDBACK_CLOSURE_DELIVERY_MISSING')
        found=dict(found,closure={k:closure[k] for k in ('intent_sha256','status','claim','result')})
    return found


def discover_feedback(*,root,executor_root,base_runtime,current_runtime,source_commit,account,executor_uid):
    """Observe original current/previous completion; never write or claim work."""
    require(type(source_commit) is str and re.fullmatch('[a-f0-9]{40}',source_commit) is not None,
        'LEGACY_FEEDBACK_SOURCE')
    _hash(account)
    require(type(executor_uid) is int and 0<=executor_uid<2**31,'LEGACY_FEEDBACK_EXECUTOR_UID')
    root=Path(root);executor_root=Path(executor_root)
    require(root.is_absolute() and executor_root.is_absolute() and root!=executor_root
        and '..' not in root.parts and '..' not in executor_root.parts,'LEGACY_FEEDBACK_ROOT')
    try:
        arguments=dict(root=root,executor_root=executor_root,executor_uid=executor_uid,
            base_runtime=base_runtime,source_commit=source_commit,account=account)
        first=_snapshot(**arguments)
        scope=_scope(root,base_runtime,current_runtime,source_commit,account)
        reobservation=_reobservation(first,executor_root=executor_root,base_runtime=base_runtime,
            current_runtime=current_runtime,source_commit=source_commit,account=account,executor_uid=executor_uid)
        closure=None if reobservation is not None else _closure(first,executor_root=executor_root,
            current_runtime=current_runtime,source_commit=source_commit,account=account,executor_uid=executor_uid)
        found=_classify(first,scope=scope,source_commit=source_commit,account=account,
            closure=closure,reobservation=reobservation)
        require(first==_snapshot(**arguments) and
            scope==_scope(root,base_runtime,current_runtime,source_commit,account),'LEGACY_FEEDBACK_CHANGED')
        return found
    except OSError:raise ValueError('LEGACY_FEEDBACK_UNAVAILABLE') from None


def inspect_reobservation(*,root,executor_root,base_runtime,current_runtime,source_commit,account,executor_uid,now_ms):
    """Read retained retry facts without allocating, capturing or mirroring."""
    require(type(now_ms) is int and 0<now_ms<=2**63-1,'LEGACY_REOBSERVATION_CLOCK')
    arguments=dict(root=Path(root),executor_root=Path(executor_root),base_runtime=base_runtime,
        current_runtime=current_runtime,source_commit=source_commit,account=account,executor_uid=executor_uid)
    # Apply the ordinary scope, custody, both histories and original-delivery
    # checks before treating any fixed retry witness as a recoverable state.
    discover_feedback(**arguments)
    snapshot_args={k:v for k,v in arguments.items() if k!='current_runtime'}
    first=_snapshot(**snapshot_args)
    scope=_scope(Path(root),base_runtime,current_runtime,source_commit,account)
    chain_args={k:v for k,v in arguments.items() if k!='root'}
    view=_reobservation(first,**chain_args)
    closure_args={k:v for k,v in chain_args.items() if k!='base_runtime'}
    original=None if view is not None else _closure(first,**closure_args)
    _classify(first,scope=scope,source_commit=source_commit,account=account,
        closure=original,reobservation=view)
    candidate=None
    if view is None and original is not None and original['status']!='COMPLETE' and \
            'closure-origin.json' in first['pilot']['records']:
        from tools.gold_pilot_reobservation import inspect_stale_candidate
        candidate=inspect_stale_candidate(Path(executor_root),retention=None,base_runtime=base_runtime,
            runtime=current_runtime,source_commit=source_commit,account=account,executor_uid=executor_uid,now_ms=now_ms)
    require(first==_snapshot(**snapshot_args) and
        scope==_scope(Path(root),base_runtime,current_runtime,source_commit,account),'LEGACY_FEEDBACK_CHANGED')
    if candidate is None and view is None:return None
    return dict(schema='GOLD_PILOT_REOBSERVATION_INSPECTION_V1',candidate=candidate,reobservation=view,
        controller_checkpoint=None,executor_checkpoint=None,submission_enabled=False)


def preflight_reobservation(*,root,executor_root,base_runtime,current_runtime,source_commit,account,executor_uid,now_ms,request):
    """Check missing legacy mirrors against the original finite reader limits.

    Legacy files have no native claims, SQLite pages, journal or audit frames.
    This does not reserve filesystem space; original writes still report errors.
    """
    from tools import gold_pilot_reobservation as retry
    arguments=dict(root=Path(root),executor_root=Path(executor_root),base_runtime=base_runtime,
        current_runtime=current_runtime,source_commit=source_commit,account=account,executor_uid=executor_uid)
    inspection=inspect_reobservation(**arguments,now_ms=now_ms)
    require(inspection is not None,'LEGACY_REOBSERVATION_NOT_APPLICABLE')
    snapshot_args={k:v for k,v in arguments.items() if k!='current_runtime'}
    first=_snapshot(**snapshot_args)
    original_request=first['pilot']['records']['closure-intent.json']['value']['request']
    evidence=inspection['candidate'] or inspection['reobservation']
    expected=retry.request(current_runtime,original_request,evidence)
    require(_digest(request)==_digest(expected),'LEGACY_REOBSERVATION_REQUEST_CHANGED')
    bounds=None
    records=_records(first,'controller');view=inspection['reobservation'];missing=[]
    for field,bound in (('expired_result','expired_result'),('result','result')):
        value=None if view is None else view[field]
        if value is not None and _digest(value) in records:
            require(records[_digest(value)]['raw']==canonical(value),'LEGACY_REOBSERVATION_MIRROR_CHANGED')
            continue
        if value is None:
            if bounds is None:bounds=retry.budget_bounds(original_request)
            size=bounds[bound]
        else:size=len(canonical(value))
        require(type(size) is int and 0<size<=MAX_RECORD_BYTES,'LEGACY_REOBSERVATION_RECORD_BUDGET')
        missing.append(size)
    require(len(records)+len(missing)<=MAX_RECORDS and
        sum(len(row['raw']) for row in records.values())+sum(missing)<=MAX_TOTAL_BYTES,
        'LEGACY_REOBSERVATION_MIRROR_BUDGET')
    require(first==_snapshot(**snapshot_args) and inspection==inspect_reobservation(**arguments,now_ms=now_ms),
        'LEGACY_FEEDBACK_CHANGED')
    required=dict(claims=0,records=len(missing),audit_events=0,payload_bytes=sum(missing),claim_bytes=0,
        audit_bytes=0,database_bytes=0,journal_bytes=0,audit_allocated_bytes=0,
        directory_bytes=sum(((size+4095)//4096)*4096 for size in missing))
    return dict(schema='GOLD_PILOT_REOBSERVATION_CAPACITY_V1',status='CAPACITY_AVAILABLE',
        source_commit=source_commit,account_ref_sha256=account,runtime_sha256=_digest(current_runtime),
        request_sha256=_digest(expected),controller_checkpoint=None,required=required,checked_at_ms=now_ms,
        submission_enabled=False,economic_authority=False)
