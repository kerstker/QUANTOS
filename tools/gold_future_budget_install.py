"""Two independently confirmed issuer administrative operations; never orders.

Before any APPLY, root durably records an immutable attempt. Every recovery of
that attempt queries the original issuer chain. Missing history remains UNKNOWN
and can never trigger an automatic initialize, re-sign or fabricated receipt.
"""
import argparse
from copy import deepcopy
import json
import os
import re
import select
import subprocess
import time
import uuid

from live.execution import demo_gold_continuity_materials as public
from live.execution.demo_gold_continuity_materials import canonical,digest
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_operating_bundle as installer, demo_supervisor as supervisor
from tools import demo_successor_setup as successor, gold_future_budget_materials as materials
from tools.gold_strategy_policy_install import local_operator

MAX_PRIVATE=1048576
ATTEMPTS={'GROUP_CONTINUITY':'group-attempt.json','DELEGATION_V2_SIGN':'delegation-sign-attempt.json'}
PHASE_NAMES={'candidate.json','group-confirmation.json','group-operation.json','previous-policy.json',
    'previous-dispatcher.json','group-response.json','template-install.json','sign-subject.json',
    'sign-confirmation.json','sign-operation.json','sign-response.json','installation.json','failure.json',*ATTEMPTS.values()}


def read(path,maximum=MAX_PRIVATE): return decode(installer.read_private(path,maximum=maximum))


def retain(path,value):
    body=canonical(value);require(len(body)<=MAX_PRIVATE,'FUTURE_INSTALL_PRIVATE_BUDGET')
    if path.exists() or path.is_symlink():require(installer.read_private(path,maximum=MAX_PRIVATE)==body,'FUTURE_INSTALL_RETAIN_CHANGED')
    else:installer.write_new(path,body)
    require(installer.read_private(path,maximum=MAX_PRIVATE)==body,'FUTURE_INSTALL_READBACK_CHANGED')


def admin_command(*,source_commit,registry,purpose,unit):
    require(purpose in ATTEMPTS,'FUTURE_ADMIN_PURPOSE')
    command=supervisor.child_command('issuer',source_commit=source_commit,registry=registry,
        purpose='submission',unit=unit)
    require(command[command.index('-m')+1]=='tools.demo_issuer','FUTURE_ADMIN_ORIGINAL_LAUNCHER')
    command[command.index('-m')+1]='tools.demo_issuer_group_admin'
    command[command.index('--purpose')+1]=purpose
    return command


def invoke_admin(*,source_commit,registry,purpose,operation_sha256,mode):
    require(mode in ('APPLY','QUERY'),'FUTURE_ADMIN_MODE')
    body=canonical(dict(schema='DEMO_GOLD_ISSUER_ADMIN_INPUT_V1',source_commit=source_commit,
        purpose=purpose,operation_sha256=operation_sha256,mode=mode))
    command=admin_command(source_commit=source_commit,registry=registry,purpose=purpose,
        unit='quantos-demo-issuer-'+uuid.uuid4().hex[:20])
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        close_fds=True,start_new_session=True)
    deadline=time.monotonic()+60
    try:
        supervisor._send(process.stdin,body,deadline);process.stdin.close()
        raw=supervisor.read_frame(process.stdout.fileno(),maximum=393216,deadline=deadline)
        result=decode(raw)
        require(type(result) is dict and set(result)==set('schema source_commit purpose operation_sha256 status '
            'record event_sha256 anchor artifact submission_enabled economic_authority'.split())
            and result['schema']=='DEMO_GOLD_ISSUER_ADMIN_RESULT_V1' and result['source_commit']==source_commit
            and result['purpose']==purpose and result['operation_sha256']==operation_sha256
            and result['status'] in ('COMMITTED','FOUND','UNKNOWN','REJECTED')
            and result['submission_enabled'] is False and result['economic_authority'] is False,'FUTURE_ADMIN_RESULT')
        remaining=deadline-time.monotonic();require(remaining>0,'FUTURE_ADMIN_TIMEOUT')
        try:code=process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:raise ValueError('FUTURE_ADMIN_TIMEOUT') from None
        require(code==(0 if result['status'] in ('COMMITTED','FOUND') else 3),'FUTURE_ADMIN_EXIT_STATUS')
        if select.select([process.stdout.fileno()],[],[],0)[0]:
            require(os.read(process.stdout.fileno(),1)==b'','FUTURE_ADMIN_EXTRA_OUTPUT')
        return result
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
        if process.stdin and not process.stdin.closed:process.stdin.close()
        if process.stdout:process.stdout.close()


def deliver_once(*,stage,operation,manifest_sha256,registry,source_commit,purpose):
    require(purpose in ATTEMPTS,'FUTURE_ADMIN_PURPOSE')
    supervisor._root_private_directory(stage)
    identity=digest(operation);path=stage/ATTEMPTS[purpose]
    stable=dict(schema='GOLD_FUTURE_BUDGET_ADMIN_ATTEMPT_V1',source_commit=source_commit,purpose=purpose,
        operation_sha256=identity,subject_sha256=digest(operation['subject']),
        confirmation_sha256=digest(operation['confirmation']),public_manifest_sha256=manifest_sha256,
        expected_head=operation['subject']['expected_head'],orders_submitted=False,economic_authority=False)
    if path.exists() or path.is_symlink():
        attempt=read(path)
        require(set(attempt)==set(stable)|{'created_at_ms'} and type(attempt['created_at_ms']) is int
            and attempt['created_at_ms']>0 and {k:v for k,v in attempt.items() if k!='created_at_ms'}==stable,
            'FUTURE_ADMIN_ATTEMPT_CHANGED')
        mode='QUERY'
    else:
        retain(path,{**stable,'created_at_ms':supervisor.now_ms()});mode='APPLY'
    return invoke_admin(source_commit=source_commit,registry=registry,purpose=purpose,operation_sha256=identity,mode=mode)


def confirmation(subject,documents,operator):
    from live.execution.demo_issuer_group_continuity import operation_check
    require(local_operator()==operator,'FUTURE_OPERATOR_CHANGED')
    print(json.dumps(dict(subject=subject,documents=documents),ensure_ascii=False,indent=2))
    print('有限同一期初行政变更；不生成订单授权，不重启期初。退出下单仅在原密钥、mandate和实际exit_until期限内允许；收尾观察余量不延长权限。')
    prefix='INSTALL GOLD FUTURE BUDGET ' if subject['purpose']=='GROUP_CONTINUITY' else 'SIGN GOLD FUTURE DELEGATION '
    expected=prefix+digest(subject)
    require(input('请输入 '+expected+'\n').strip()==expected,'FUTURE_NOT_CONFIRMED')
    require(local_operator()==operator,'FUTURE_OPERATOR_CHANGED')
    result=dict(schema='GOLD_FUTURE_BUDGET_OPERATOR_CONFIRMATION_V1',purpose=subject['purpose'],
        subject_sha256=digest(subject),operator_id=operator,approved_at_ms=supervisor.now_ms(),
        consumer_enabled=False,submission_enabled=False,economic_authority=False)
    operation_check(dict(subject=subject,confirmation=result),purpose=subject['purpose'],at_ms=supervisor.now_ms())
    return result


def _public_root():
    path=public.PUBLIC_ROOT
    if not path.exists() and not path.is_symlink():
        for parent in path.parents:public._parent_metadata(parent.lstat())
        installer.directory(path,0,0);os.chmod(path,0o755,follow_symlinks=False)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)


def publish(operation,documents,*,source_commit,registry):
    purpose=operation['subject']['purpose'];identity=digest(operation)
    require(set(documents)==public.DOCUMENTS[purpose],'FUTURE_PUBLIC_DOCUMENT_SCOPE')
    files={'operation.json':canonical(operation),**{name.replace('_','-')+'.json':canonical(value) for name,value in documents.items()}}
    require(all(len(raw)<=public.DOCUMENT_MAXIMUM for raw in files.values()),'FUTURE_PUBLIC_DOCUMENT_BUDGET')
    manifest=dict(schema=public.MANIFEST_SCHEMA,source_commit=source_commit,purpose=purpose,operation_sha256=identity,
        files={name:public.hashlib.sha256(raw).hexdigest() for name,raw in files.items()},submission_enabled=False,economic_authority=False)
    complete=dict(schema=public.COMPLETE_SCHEMA,source_commit=source_commit,purpose=purpose,operation_sha256=identity,
        manifest_sha256=digest(manifest),submission_enabled=False,economic_authority=False)
    files.update({'manifest.json':canonical(manifest),'complete.json':canonical(complete)})
    require(sum(map(len,files.values()))<=public.OPERATION_MAXIMUM,'FUTURE_PUBLIC_TOTAL_BUDGET')
    _public_root()
    with public.public_lease(exclusive=True) as fd:
        area=public.PUBLIC_ROOT/identity
        if not area.exists() and not area.is_symlink():
            require(len(os.listdir(fd))<public.MAX_OPERATIONS,'FUTURE_PUBLIC_HISTORY_BUDGET')
            installer.directory(area,0,0);os.chmod(area,0o755,follow_symlinks=False)
            for name in sorted(set(files)-{'complete.json'}):installer.write_new(area/name,files[name],mode=0o444)
            installer.write_new(area/'complete.json',files['complete.json'],mode=0o444)
        result=public._load_locked(fd,identity,source_commit=source_commit,purpose=purpose)
        require(result==dict(operation=operation,documents=documents,manifest_sha256=digest(manifest)),
            'FUTURE_PUBLIC_READBACK_CHANGED')
    return digest(manifest)


def stage_for(identity,*,create=False):
    require(type(identity) is str and re.fullmatch('[a-f0-9]{64}',identity),'FUTURE_INSTALL_IDENTITY')
    root=supervisor.STATE/'gold-future-budget-installations'
    if create and not root.exists() and not root.is_symlink():installer.directory(root,0,0)
    supervisor._root_private_directory(root)
    entries=os.listdir(root);require(len(entries)<=4096 and all(re.fullmatch('[a-f0-9]{64}',x) for x in entries),'FUTURE_INSTALL_HISTORY')
    stage=root/identity
    if create and not stage.exists() and not stage.is_symlink():installer.directory(stage,0,0)
    supervisor._root_private_directory(stage)
    require(set(os.listdir(stage))<=PHASE_NAMES,'FUTURE_INSTALL_STAGE_SCOPE')
    return stage


def _new_install_history(candidate):
    """An old root witness cannot be bypassed with a newly confirmed hash.

    This deliberately includes incomplete publication before an attempt. Only
    explicit recovery may examine the original operation; missing issuer rows
    never turn the same pilot/group into an unused first installation.
    """
    root=supervisor.STATE/'gold-future-budget-installations'
    if not root.exists() and not root.is_symlink():return
    supervisor._root_private_directory(root)
    entries=os.listdir(root)
    require(len(entries)<=4096,'FUTURE_INSTALL_HISTORY')
    current=candidate['documents'];previous=current['previous_authorization']
    for name in entries:
        stage=stage_for(name)
        require((stage/'candidate.json').is_file() and (stage/'group-operation.json').is_file(),
            'UNKNOWN_NEEDS_INDEPENDENT_CONTINUITY')
        original=read(stage/'candidate.json');operation=read(stage/'group-operation.json')
        require(digest(operation)==name and operation['subject']==original['subject']
            and digest(original['subject'])==original['subject_sha256'],
            'UNKNOWN_NEEDS_INDEPENDENT_CONTINUITY')
        docs=original['documents']
        require(type(docs) is dict and set(docs)==public.DOCUMENTS['GROUP_CONTINUITY']
            and {key:digest(value) for key,value in docs.items()}==operation['subject']['documents_sha256'],
            'FUTURE_INSTALL_HISTORY_DOCUMENTS_CHANGED')
        old=docs['previous_authorization']
        materials.pilot.completed_configuration_records(**docs['pilot'])
        same_group=(old['account_ref_sha256'],old['budget_group']['group_id'])==(
            previous['account_ref_sha256'],previous['budget_group']['group_id'])
        same_pilot=(old['account_ref_sha256'],old['executor_release_commit'])==(
            previous['account_ref_sha256'],previous['executor_release_commit']) and (
            digest(docs['pilot']['request'])==digest(current['pilot']['request'])
            or digest(docs['pilot']['configuration'])==digest(current['pilot']['configuration'])
            or all(docs['pilot']['configuration'][key]==current['pilot']['configuration'][key]
                for key in ('opening_origin_sha256','started_at_ms','ends_at_ms')))
        require(not same_group and not same_pilot,'UNKNOWN_NEEDS_INDEPENDENT_CONTINUITY')


def verified_response(result,*,operation,registry,authorization,purpose):
    from live.execution import demo_issuer_group_continuity as continuity
    require(result['status'] in ('COMMITTED','FOUND'),'UNKNOWN_NEEDS_INDEPENDENT_CONTINUITY')
    rows=materials.issuer_rows(registry)
    matches=[row for row in rows if row['proposal_sha256']==digest(operation)
        and row['kind'] in (('GROUP_INITIALIZED','GROUP_EXIT_RESERVE_MIGRATED') if purpose=='GROUP_CONTINUITY' else ('DELEGATION_V2_SIGNED',))]
    require(len(matches)==1 and matches[0]==result['record'] and result['event_sha256']==matches[0]['record_hash'],
        'FUTURE_ADMIN_ORIGINAL_EVENT_REQUIRED')
    row=matches[0]
    require(row['payload']['operation']==operation,'FUTURE_ADMIN_OPERATION_CHANGED')
    continuity.verify_history_prefix(result['anchor'],rows,authorization=authorization)
    if purpose=='GROUP_CONTINUITY':
        continuity.verify_group_change_history(row);require(result['artifact'] is None,'FUTURE_ADMIN_ARTIFACT')
    else: require(row['payload']['artifact']==result['artifact'],'FUTURE_ADMIN_ARTIFACT')
    return row


def _same_originals(documents,*,source_commit,operator):
    require(local_operator()==operator,'FUTURE_OPERATOR_CHANGED')
    registry,_=materials.observation_configuration(source_commit)
    require(registry==documents['registry'],'FUTURE_REGISTRY_CHANGED')
    previous=documents['previous_authorization']
    current=materials.current_documents(source_commit=source_commit,authorization=previous,registry=registry)
    require(all(current[name]==documents[name] for name in current),'FUTURE_ORIGINALS_CHANGED')
    require(materials.authorization_ancestry(previous,registry,source_commit)==documents['ancestry'],
        'FUTURE_ANCESTRY_CHANGED')
    stop=materials.setup.verified_stop_snapshot(materials.setup._stop_image(path=installer.EXECUTOR/'kill-switch.sqlite3'),
        now_ms=supervisor.now_ms())['state'].model_dump(mode='json')
    require(stop==documents['dispatcher']['kill_switch'],'FUTURE_STOP_CHANGED')


def _replace_exact(path,*,before,after,gid=0):
    current=supervisor._read_root_owned_json(path) if path.exists() or path.is_symlink() else None
    require(current in (before,after),'FUTURE_INSTALL_PREIMAGE_CHANGED')
    if current==after:return
    if before is None:installer.write_new(path,after,gid=gid,mode=0o640 if gid else 0o600)
    else:
        temporary=path.parent/('.gold-future-'+uuid.uuid4().hex)
        installer.write_new(temporary,after,gid=gid,mode=0o640 if gid else 0o600)
        require(supervisor._read_root_owned_json(path)==before,'FUTURE_INSTALL_PREIMAGE_CHANGED')
        os.replace(temporary,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)
    require(supervisor._read_root_owned_json(path)==after,'FUTURE_INSTALL_READBACK_CHANGED')


def _finish_template(stage,candidate,operation,response,*,source_commit,operator):
    from live.execution.demo_gold_future_template import template_group_binding
    documents=candidate['documents'];registry=documents['registry'];template=documents['template']
    verified_response(response,operation=operation,registry=registry,authorization=template,purpose='GROUP_CONTINUITY')
    _same_originals(documents,source_commit=source_commit,operator=operator)
    policy_before=installer.read_private(stage/'previous-policy.json',maximum=65536)
    preimage=read(stage/'previous-dispatcher.json')
    require(set(preimage)=={'schema','present','body_hex'} and preimage['schema']=='GOLD_FUTURE_DISPATCHER_RETAINED_PREIMAGE_V1'
        and type(preimage['present']) is bool and type(preimage['body_hex']) is str,'FUTURE_DISPATCHER_PREIMAGE')
    before=bytes.fromhex(preimage['body_hex']) if preimage['present'] else None
    require((decode(before) if before is not None else None)==documents['dispatcher']['configuration']
        and preimage['present']==documents['dispatcher']['present'],'FUTURE_DISPATCHER_PREIMAGE')
    dispatch=dict(schema='GOLD_STRATEGY_APPROVED_POLICY_DISPATCH_V1',source_commit=source_commit,
        account_ref_sha256=template['account_ref_sha256'],budget_group_binding_sha256=template_group_binding(template),
        kill_switch=documents['dispatcher']['kill_switch'])
    require(decode(policy_before)==documents['previous_authorization'],'FUTURE_POLICY_PREIMAGE')
    # The group event already moved the original issuer's active binding. Both
    # interrupted root states remain nonexecutable until independent SIGN+ENABLE.
    _replace_exact(supervisor.ISSUER_POLICY,before=policy_before,after=canonical(template),gid=registry['issuer_uid'])
    _same_originals(documents,source_commit=source_commit,operator=operator)
    _replace_exact(supervisor.STATE/'strategy-dispatch.json',before=before,after=canonical(dispatch))
    _same_originals(documents,source_commit=source_commit,operator=operator)
    verified_response(response,operation=operation,registry=registry,authorization=template,purpose='GROUP_CONTINUITY')
    result=dict(schema='GOLD_FUTURE_BUDGET_TEMPLATE_INSTALLATION_V1',
        status='FUTURE_TEMPLATE_INSTALLED_REQUIRES_SEPARATE_SIGNING',source_commit=source_commit,
        group_operation_sha256=digest(operation),continuity_event_sha256=response['event_sha256'],
        future_template_sha256=digest(template),dispatcher_sha256=digest(dispatch),
        consumer_enabled=False,submission_enabled=False,economic_authority=False)
    retain(stage/'template-install.json',result)
    return result


def install_approved(*,source_commit,exit_reserve=None,resume_operation_sha256=None):
    operator=local_operator()
    with supervisor.controller_lease(maintenance=True):
        if resume_operation_sha256 is None:
            require(exit_reserve is not None,'FUTURE_EXIT_RESERVE_REQUIRED')
            candidate=materials.prepare(source_commit=source_commit,exit_reserve=exit_reserve)
            _new_install_history(candidate)
            approval=confirmation(candidate['subject'],candidate['documents'],operator)
            operation=dict(subject=candidate['subject'],confirmation=approval)
            from live.execution.demo_issuer_group_continuity import operation_check
            operation_check(operation,purpose='GROUP_CONTINUITY',at_ms=supervisor.now_ms())
            require(local_operator()==operator and materials.capture(source_commit=source_commit,exit_reserve=exit_reserve)['documents']
                ==candidate['documents'],'FUTURE_ORIGINALS_CHANGED')
            stage=stage_for(digest(operation),create=True)
            require(not list(stage.iterdir()),'FUTURE_INSTALL_USE_EXPLICIT_RECOVERY')
            retain(stage/'candidate.json',candidate);retain(stage/'group-confirmation.json',approval)
            retain(stage/'group-operation.json',operation)
            installer.write_new(stage/'previous-policy.json',supervisor._read_root_owned_json(supervisor.ISSUER_POLICY))
            dispatcher=supervisor.STATE/'strategy-dispatch.json'
            present=dispatcher.exists() or dispatcher.is_symlink()
            raw=supervisor._read_root_owned_json(dispatcher) if present else b''
            retain(stage/'previous-dispatcher.json',dict(schema='GOLD_FUTURE_DISPATCHER_RETAINED_PREIMAGE_V1',
                present=present,body_hex=raw.hex()))
            manifest=publish(operation,candidate['documents'],source_commit=source_commit,registry=candidate['documents']['registry'])
            _same_originals(candidate['documents'],source_commit=source_commit,operator=operator)
            operation_check(operation,purpose='GROUP_CONTINUITY',at_ms=supervisor.now_ms())
        else:
            require(exit_reserve is None,'FUTURE_RECOVERY_NO_NEW_PARAMETERS')
            stage=stage_for(resume_operation_sha256);candidate=read(stage/'candidate.json');operation=read(stage/'group-operation.json')
            require(digest(operation)==resume_operation_sha256 and operation['subject']==candidate['subject']
                and operation['confirmation']==read(stage/'group-confirmation.json')
                and operation['confirmation']['operator_id']==operator
                and operation['subject']['source_commit']==source_commit,'FUTURE_RECOVERY_ORIGINALS')
            # An interrupted publication without an attempt also stays UNKNOWN.
            # It cannot be treated as proof that no APPLY reached the issuer.
            attempt=read(stage/ATTEMPTS['GROUP_CONTINUITY']);manifest=attempt['public_manifest_sha256']
        response=deliver_once(stage=stage,operation=operation,manifest_sha256=manifest,
            registry=candidate['documents']['registry'],source_commit=source_commit,purpose='GROUP_CONTINUITY')
        verified_response(response,operation=operation,registry=candidate['documents']['registry'],
            authorization=candidate['documents']['template'],purpose='GROUP_CONTINUITY')
        saved=stage/'group-response.json'
        if not saved.exists():retain(saved,response)
        else:
            previous=read(saved)
            require(all(previous[k]==response[k] for k in ('record','event_sha256','artifact')),'FUTURE_RECOVERY_EVENT_CHANGED')
        return _finish_template(stage,candidate,operation,response,source_commit=source_commit,operator=operator)


def _sign_documents(stage,*,source_commit,operator):
    candidate=read(stage/'candidate.json');documents=candidate['documents'];template=documents['template']
    operation=read(stage/'group-operation.json');response=read(stage/'group-response.json')
    installed=read(stage/'template-install.json')
    require(installed['source_commit']==source_commit and installed['group_operation_sha256']==digest(operation)
        and installed['future_template_sha256']==digest(template),'FUTURE_SIGN_INSTALLED_BINDING')
    row=verified_response(response,operation=operation,registry=documents['registry'],authorization=template,purpose='GROUP_CONTINUITY')
    _same_originals(documents,source_commit=source_commit,operator=operator)
    registry,policy=materials.observation_configuration(source_commit)
    require(policy==template and registry==documents['registry'],'FUTURE_SIGN_INSTALLED_TEMPLATE_CHANGED')
    dispatch=materials._read(supervisor.STATE/'strategy-dispatch.json')
    require(digest(dispatch)==installed['dispatcher_sha256'],'FUTURE_SIGN_DISPATCHER_CHANGED')
    result={name:deepcopy(documents[name]) for name in ('registry','template','profile','pilot','runtime','definition')}
    result.update(dispatcher=dispatch,continuity_event=row)
    return result


def prepare_sign(*,source_commit,group_operation_sha256,opening_until_ms,exit_until_ms,max_holding_ms,operator):
    from live.execution import demo_issuer_group_continuity as continuity
    stage=stage_for(group_operation_sha256);documents=_sign_documents(stage,source_commit=source_commit,operator=operator)
    template=documents['template'];runtime=documents['runtime'];config=documents['pilot']['configuration']
    previous=documents['continuity_event']['payload']['previous_authorization']
    documents['delegation_intent']=dict(original_budget_group_binding_sha256=continuity.authorization_binding(previous),
        active_budget_group_binding_sha256=continuity.authorization_binding(template),runtime_sha256=digest(runtime),
        strategy_source_commit=runtime['archive_source_commit'],trial_manifest_sha256=runtime['trial_manifest_sha256'],
        pilot_request_sha256=template['pilot']['request_sha256'],pilot_configuration=config,
        opening_until_ms=opening_until_ms,exit_until_ms=exit_until_ms,max_holding_ms=max_holding_ms,
        entry_timeout_ms=documents['definition']['size_and_timing']['entry_timeout_ms'])
    now=supervisor.now_ms()
    require(all(type(x) is int and x>0 for x in (opening_until_ms,exit_until_ms,max_holding_ms))
        and config['started_at_ms']<=now<opening_until_ms<=config['ends_at_ms']-5000
        and config['ends_at_ms']<=exit_until_ms<=template['valid_until_ms']
        and max_holding_ms==documents['definition']['rules']['max_holding_bars']*900000<=86400000,
        'FUTURE_SIGN_EXPLICIT_WINDOW')
    subject=continuity.make_sign_subject(documents=documents,rows=materials.issuer_rows(documents['registry']),
        created_at_ms=now,expires_at_ms=now+300000)
    return subject,documents


def _retain_signed(stage,response,operation,*,source_commit):
    artifact=response['artifact'];require(type(artifact) is dict,'FUTURE_SIGN_ARTIFACT_REQUIRED')
    root=supervisor.STATE/'gold-execution-delegation-materials'
    if not root.exists() and not root.is_symlink():installer.directory(root,0,0)
    supervisor._root_private_directory(root)
    for kind,value in (('delegations',artifact),('operators',operation['confirmation'])):
        parent=root/kind
        if not parent.exists() and not parent.is_symlink():installer.directory(parent,0,0)
        supervisor._root_private_directory(parent)
        entries=os.listdir(parent)
        require(len(entries)<4096 and all(re.fullmatch('[a-f0-9]{64}\\.json',name) for name in entries),'FUTURE_SIGN_MATERIAL_BUDGET')
        require(len(canonical(value))<=65536,'FUTURE_SIGN_ORIGINAL_MATERIAL_BUDGET')
        retain(parent/(digest(value)+'.json'),value)
    result=dict(schema='GOLD_FUTURE_BUDGET_SIGNED_INSTALLATION_V1',
        status='FUTURE_DELEGATION_SIGNED_REQUIRES_DISABLED_REGISTRATION',source_commit=source_commit,
        group_operation_sha256=stage.name,sign_operation_sha256=digest(operation),
        sign_event_sha256=response['event_sha256'],continuity_event_sha256=artifact['continuity_event_sha256'],
        delegation_sha256=digest(artifact),operator_material_sha256=digest(operation['confirmation']),
        consumer_enabled=False,submission_enabled=False,economic_authority=False)
    retain(stage/'installation.json',result)
    return result


def sign_approved(*,source_commit,group_operation_sha256,opening_until_ms=None,exit_until_ms=None,max_holding_ms=None,resume=False):
    operator=local_operator()
    with supervisor.controller_lease(maintenance=True):
        stage=stage_for(group_operation_sha256)
        if not resume:
            require(not (stage/'sign-operation.json').exists() and not (stage/'sign-operation.json').is_symlink(),
                'FUTURE_SIGN_USE_EXPLICIT_RECOVERY')
            subject,documents=prepare_sign(source_commit=source_commit,group_operation_sha256=group_operation_sha256,
                opening_until_ms=opening_until_ms,exit_until_ms=exit_until_ms,max_holding_ms=max_holding_ms,operator=operator)
            approval=confirmation(subject,documents,operator);operation=dict(subject=subject,confirmation=approval)
            from live.execution.demo_issuer_group_continuity import operation_check
            operation_check(operation,purpose='DELEGATION_V2_SIGN',at_ms=supervisor.now_ms())
            require(_sign_documents(stage,source_commit=source_commit,operator=operator)
                =={k:v for k,v in documents.items() if k!='delegation_intent'},'FUTURE_SIGN_ORIGINALS_CHANGED')
            retain(stage/'sign-subject.json',subject);retain(stage/'sign-confirmation.json',approval)
            retain(stage/'sign-operation.json',operation)
            manifest=publish(operation,documents,source_commit=source_commit,registry=documents['registry'])
            require(_sign_documents(stage,source_commit=source_commit,operator=operator)
                =={k:v for k,v in documents.items() if k!='delegation_intent'},'FUTURE_SIGN_ORIGINALS_CHANGED')
            operation_check(operation,purpose='DELEGATION_V2_SIGN',at_ms=supervisor.now_ms())
        else:
            require(all(value is None for value in (opening_until_ms,exit_until_ms,max_holding_ms)),
                'FUTURE_SIGN_RECOVERY_NO_NEW_PARAMETERS')
            operation=read(stage/'sign-operation.json');approval=read(stage/'sign-confirmation.json')
            require(operation['subject']==read(stage/'sign-subject.json') and operation['confirmation']==approval
                and approval['operator_id']==operator and operation['subject']['source_commit']==source_commit,
                'FUTURE_SIGN_RECOVERY_ORIGINALS')
            manifest=read(stage/ATTEMPTS['DELEGATION_V2_SIGN'])['public_manifest_sha256']
            # Query before current authorization/documents checks. An expired
            # old administrative subject never authorizes another signing try.
            documents=read(stage/'candidate.json')['documents']
        response=deliver_once(stage=stage,operation=operation,manifest_sha256=manifest,registry=documents['registry'],
            source_commit=source_commit,purpose='DELEGATION_V2_SIGN')
        verified_response(response,operation=operation,registry=documents['registry'],authorization=documents['template'],
            purpose='DELEGATION_V2_SIGN')
        current=_sign_documents(stage,source_commit=source_commit,operator=operator)
        require(response['artifact']['future_template']==current['template']
            and response['artifact']['continuity_event_sha256']==current['continuity_event']['record_hash'],
            'FUTURE_SIGN_ARTIFACT_CHANGED')
        if not (stage/'sign-response.json').exists():retain(stage/'sign-response.json',response)
        else:
            saved=read(stage/'sign-response.json')
            require(all(saved[k]==response[k] for k in ('record','event_sha256','artifact')),'FUTURE_SIGN_RECOVERY_CHANGED')
        result=_retain_signed(stage,response,operation,source_commit=source_commit)
        require(_sign_documents(stage,source_commit=source_commit,operator=operator)==current,'FUTURE_SIGN_ORIGINALS_CHANGED')
        return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--install-approved',action='store_true');modes.add_argument('--resume-group')
    modes.add_argument('--sign-approved',action='store_true');modes.add_argument('--resume-sign',action='store_true')
    parser.add_argument('--group-operation-sha256')
    parser.add_argument('--exit-reserve-grants',type=int);parser.add_argument('--exit-reserve-orders',type=int)
    parser.add_argument('--exit-reserve-notional')
    parser.add_argument('--opening-until-ms',type=int);parser.add_argument('--exit-until-ms',type=int)
    parser.add_argument('--max-holding-ms',type=int)
    args=parser.parse_args(argv);old=os.umask(0o077)
    try:
        if args.sign_approved or args.resume_sign:
            require(all(x is None for x in (args.exit_reserve_grants,args.exit_reserve_orders,args.exit_reserve_notional)),
                'FUTURE_SIGN_NO_NEW_BUDGET')
            result=sign_approved(source_commit=args.source_commit,group_operation_sha256=args.group_operation_sha256,
                opening_until_ms=args.opening_until_ms,exit_until_ms=args.exit_until_ms,max_holding_ms=args.max_holding_ms,
                resume=args.resume_sign)
        else:
            require(all(x is None for x in (args.group_operation_sha256,args.opening_until_ms,args.exit_until_ms,args.max_holding_ms)),
                'FUTURE_GROUP_NO_SIGN_PARAMETERS')
            require(not args.resume_group or all(x is None for x in
                (args.exit_reserve_grants,args.exit_reserve_orders,args.exit_reserve_notional)),
                'FUTURE_RECOVERY_NO_NEW_PARAMETERS')
            reserve=None if args.resume_group else dict(grants=args.exit_reserve_grants,orders=args.exit_reserve_orders,notional=args.exit_reserve_notional)
            result=install_approved(source_commit=args.source_commit,exit_reserve=reserve,resume_operation_sha256=args.resume_group)
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        print(json.dumps(dict(status='UNKNOWN_NEEDS_INDEPENDENT_CONTINUITY',consumer_enabled=False,
            submission_enabled=False,economic_authority=False)));return 3
    finally:os.umask(old)


if __name__=='__main__':raise SystemExit(main())
