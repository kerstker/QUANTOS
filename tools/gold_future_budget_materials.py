"""Read original public Scope/BUDGET and pilot material for finite continuity.

No signer, new pilot, reservation, account collector or service action exists
here. The issuer will independently check the exact material and original head.
"""
import argparse
from contextlib import closing
from copy import deepcopy
import json
import re

from live.execution import demo_gold_budget_scope as scope_contract
from live.execution import demo_gold_future_template as template_contract
from live.execution import demo_gold_pilot_enrollment as pilot
from live.execution import demo_gold_scope_ancestry as ancestry
from live.execution.demo_gold_continuity_materials import canonical, digest, DOCUMENT_MAXIMUM
from live.execution.demo_issuer_policy import require, GOLD_BUDGET_POLICY_SCHEMAS, current_key
from live.execution.demo_issuer_store import IssuerStore, MAX_BYTES
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools import demo_operating_bundle as installer, demo_operator_setup as setup
from tools import demo_successor_setup as successor, demo_supervisor as supervisor, demo_issuer
from tools import gold_strategy_budget_enrollment as strategy
from tools.gold_strategy_controller import observation_configuration
from tools.gold_pilot_install import _read_target
from tools.demo_resident_recovery import _check_path


def _read(path): return decode(supervisor._read_root_owned_json(path))


def _original_setup(identity):
    roots=[successor.SETUP]
    cycles=successor.SETUP/'cycles'
    if cycles.exists() or cycles.is_symlink():
        supervisor._root_private_directory(cycles)
        entries=list(cycles.iterdir());require(len(entries)<=128,'FUTURE_ANCESTRY_HISTORY_BUDGET')
        roots+=entries
    matched=[]
    for area in roots:
        path=area/'result.json'
        if path.exists() or path.is_symlink():
            result=_read(path)
            if digest(result)==identity: matched.append(dict(result=result,spec=_read(area/'spec.json')))
    require(len(matched)==1,'FUTURE_ANCESTRY_ORIGINAL_SETUP_UNIQUE')
    return matched[0]


def _scope_records(stage,scope,*,result,spec,pointer,activation):
    first=scope.get('schema')==scope_contract.SCHEMA_V2
    return dict(schema=ancestry.SCHEMA_V2 if first else ancestry.SCHEMA,active_pointer=pointer,activation=activation,result=result,spec=spec,
        intent=_read(stage/('first-scope-intent.json' if first else 'scope-intent.json')),manifest=_read(stage/'bundle/manifest.json'),
        operator_enrollment=_read(stage/'operator-enrollment.json'),registry_preimage=_read(stage/'registry-preimage.json'),
        original_setup=_original_setup(scope['scope_enrollment']['original_cycle_sha256']),
        public_documents={name:_read(stage/'bundle'/name) for name in ancestry.PUBLIC_DOCUMENTS})


def _exact_installation(authorization):
    root=supervisor.ISSUER_POLICY.parent/'strategy-approvals';supervisor._root_private_directory(root)
    entries=list(root.iterdir());require(len(entries)<=4096,'FUTURE_EXACT_HISTORY_BUDGET')
    matches=[]
    for stage in entries:
        require(re.fullmatch('[a-f0-9]{64}',stage.name),'FUTURE_EXACT_HISTORY_IDENTITY')
        supervisor._root_private_directory(stage)
        result_path=stage/'result.json'
        if not result_path.exists() and not result_path.is_symlink():continue
        result=_read(result_path)
        if result.get('installed_policy_sha256')!=digest(authorization):continue
        previous=_read(stage/'previous-policy.json');scoped=scope_contract.is_scope(previous)
        names={'previous-policy.json','candidate.json','operator-approval.json','result.json'}
        if scoped:
            marker=stage/'previous-dispatch.absent'
            absent=marker.exists() or marker.is_symlink()
            names|={'dispatcher-candidate.json','previous-dispatch.absent' if absent else 'previous-dispatch.json'}
            if absent:require(supervisor._read_root_owned_json(marker)==b'ABSENT\n','FUTURE_EXACT_DISPATCH_PREIMAGE')
            dispatcher=None if absent else _read(stage/'previous-dispatch.json')
        else:dispatcher=None
        require(set(p.name for p in stage.iterdir())==names,'FUTURE_EXACT_HISTORY_SCOPE')
        matches.append(dict(stage_sha256=stage.name,previous_policy=previous,candidate=_read(stage/'candidate.json'),
            operator_approval=_read(stage/'operator-approval.json'),result=result,previous_dispatcher=dispatcher,
            dispatcher_candidate=_read(stage/'dispatcher-candidate.json') if scoped else None))
    require(len(matches)==1,'FUTURE_EXACT_INSTALLATION_UNIQUE')
    return matches[0]


def authorization_ancestry(authorization,registry,source_commit):
    stage=successor.active_setup();result=_read(stage/'result.json');spec=_read(stage/'spec.json')
    pointer=_read(successor.SETUP/'active.json') if stage!=successor.SETUP else None
    activation=_read(stage/'activation.json') if stage!=successor.SETUP else None
    if scope_contract.is_scope(authorization):
        records=_scope_records(stage,authorization,result=result,spec=spec,pointer=pointer,activation=activation)
    elif result.get('status')=='INSTALLED_SCOPE_ONLY':
        scope=_read(stage/'bundle/policy.json')
        records=dict(schema='DEMO_GOLD_SCOPE_BUDGET_INSTALLATION_ANCESTRY_V1',result=result,spec=spec,
            scope_ancestry=_scope_records(stage,scope,result=result,spec=spec,pointer=pointer,activation=activation),
            policy_installation=_exact_installation(authorization))
    else:
        records=dict(schema='DEMO_GOLD_BUDGET_INSTALLATION_ANCESTRY_V1',active_pointer=pointer,
            activation=activation,result=result,spec=spec,policy=authorization,profile=_read(installer.CONFIG/'profile.json'))
    ancestry.validate_authorization_ancestry(records,authorization=authorization,registry=registry,source_commit=source_commit)
    return records


def issuer_rows(registry):
    root=installer.ISSUER;demo_issuer._state_custody(root,registry['issuer_uid'])
    path=root/'issuance.sqlite3'
    for suffix in ('-wal','-shm','-journal'):
        extra=root/('issuance.sqlite3'+suffix)
        require(not extra.exists() and not extra.is_symlink(),'FUTURE_ISSUER_BUSY')
    raw=_read_target(path,uid=registry['issuer_uid'],maximum=MAX_BYTES);info=path.lstat()
    require(raw[:16]==b'SQLite format 3\x00' and raw[18:20]==b'\x01\x01','FUTURE_ISSUER_ORIGINAL_DATABASE')
    with closing(IssuerStore(path,read_only=True)) as store: rows=store.snapshot()
    require(_read_target(path,uid=registry['issuer_uid'],maximum=MAX_BYTES)==raw
        and (info.st_dev,info.st_ino)==(path.lstat().st_dev,path.lstat().st_ino),'FUTURE_ISSUER_CHANGED')
    demo_issuer._state_custody(root,registry['issuer_uid'])
    return rows


def pilot_records(registry,authorization,source_commit):
    root=installer.EXECUTOR;uid=registry['executor_uid'];area=root/'gold-pilot'
    _check_path(root,uid,directory=True);_check_path(area,uid,directory=True)
    require(not (area/'stopped.json').exists() and not (area/'stopped.json').is_symlink(),'FUTURE_PILOT_STOPPED')
    read=lambda path,maximum=32768:decode(_read_target(path,uid=uid,maximum=maximum))
    request=read(root/'gold-pilot-request.json')
    pilot._request_check(request,source_commit=source_commit,account=authorization['account_ref_sha256'])
    records=dict(request=request,configuration=read(area/'configuration.json'),receipt=read(area/'enrollment.json'),
        intent=read(area/'enrollment-intent.json',pilot.INTENT_MAXIMUM)
        if (area/'enrollment-intent.json').exists() or (area/'enrollment-intent.json').is_symlink() else None)
    pilot.completed_configuration_records(**records)
    return records


def current_documents(*,source_commit,authorization,registry,definition_session=None):
    profile=_read(installer.CONFIG/'profile.json');runtime=_read(supervisor.STATE/'strategy-runtime.json')
    strategy.runtime_check(runtime,source_commit)
    definition=strategy.load_definition(installer.EXECUTOR,runtime,source_commit=source_commit,uid=registry['executor_uid'],
        definition_session=definition_session)
    ancestry._profile_binding(profile,authorization)
    require(definition['definition_hash']==authorization['budget_group']['strategy_definition_sha256'],
        'FUTURE_DEFINITION_CHANGED')
    return dict(registry=registry,profile=profile,runtime=runtime,definition=definition,
        pilot=pilot_records(registry,authorization,source_commit))


def capture(*, source_commit, exit_reserve):
    registry,authorization=observation_configuration(source_commit);now=supervisor.now_ms()
    is_scope=scope_contract.is_scope(authorization)
    require(is_scope or authorization['schema'] in GOLD_BUDGET_POLICY_SCHEMAS,'FUTURE_CONTINUITY_ORIGINAL_TYPE')
    (scope_contract.current_scope_key if is_scope else current_key)(authorization,registry,now)
    records=authorization_ancestry(authorization,registry,source_commit)
    documents=current_documents(source_commit=source_commit,authorization=authorization,registry=registry)
    config=documents['pilot']['configuration']
    values={name:deepcopy(authorization[name]) for name in template_contract.PERMISSION_FIELDS.split()}
    values['bindings'].pop('execution_constraints_sha256',None)
    values['budget_group']['exit_reserve']=deepcopy(exit_reserve)
    values.update(schema=template_contract.SCHEMA,template_id='gold-future-'+digest(authorization),
        origin=dict(kind='SCOPE' if is_scope else 'BUDGET',document_sha256=digest(authorization),
            profile_sha256=digest(documents['profile']),setup_sha256=digest(records['result']),spec_sha256=digest(records['spec'])),
        pilot=dict(request_sha256=digest(documents['pilot']['request']),configuration_sha256=digest(config),
            opening_origin_sha256=config['opening_origin_sha256'],started_at_ms=config['started_at_ms'],ends_at_ms=config['ends_at_ms']),
        executable=False,submission_enabled=False,economic_authority=False)
    template_contract.validate_template(values)
    path=supervisor.STATE/'strategy-dispatch.json';present=path.exists() or path.is_symlink()
    require(not present if is_scope else present,'FUTURE_DISPATCHER_ORIGINAL_REQUIRED')
    stop=setup.verified_stop_snapshot(setup._stop_image(path=installer.EXECUTOR/'kill-switch.sqlite3'),now_ms=now)
    kill=stop['state'].model_dump(mode='json')
    documents.update(previous_authorization=authorization,template=values,ancestry=records,
        dispatcher=dict(schema='GOLD_FUTURE_DISPATCHER_PREIMAGE_V1',present=present,
            configuration=_read(path) if present else None,kill_switch=kill))
    require(all(len(canonical(value))<=DOCUMENT_MAXIMUM for value in documents.values()),'FUTURE_MATERIAL_DOCUMENT_BUDGET')
    rows=issuer_rows(registry)
    original_scope=authorization if is_scope else records.get('scope_ancestry',{}).get('public_documents',{}).get('policy.json')
    if original_scope is not None and original_scope.get('schema')==scope_contract.SCHEMA_V2:
        from live.execution.demo_gold_first_scope_history import verify_prefix
        verify_prefix(original_scope['scope_enrollment']['original_issuer_history'],rows)
    return dict(documents=documents,rows=rows)


def prepare(*,source_commit,exit_reserve,valid_for_ms=300000):
    from live.execution.demo_issuer_group_continuity import make_group_subject
    require(type(valid_for_ms) is int and 0<valid_for_ms<=300000,'FUTURE_ADMIN_WINDOW')
    captured=capture(source_commit=source_commit,exit_reserve=exit_reserve);now=supervisor.now_ms()
    subject=make_group_subject(**captured,created_at_ms=now,expires_at_ms=now+valid_for_ms)
    require(capture(source_commit=source_commit,exit_reserve=exit_reserve)==captured,'FUTURE_ORIGINALS_CHANGED')
    return dict(schema='GOLD_FUTURE_BUDGET_CANDIDATE_V1',subject=subject,documents=captured['documents'],
        subject_sha256=digest(subject),consumer_enabled=False,submission_enabled=False,economic_authority=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--exit-reserve-grants',type=int,required=True)
    parser.add_argument('--exit-reserve-orders',type=int,required=True)
    parser.add_argument('--exit-reserve-notional',required=True)
    args=parser.parse_args(argv)
    try:
        with supervisor.controller_lease(maintenance=True):
            result=prepare(source_commit=args.source_commit,exit_reserve=dict(grants=args.exit_reserve_grants,
                orders=args.exit_reserve_orders,notional=args.exit_reserve_notional))
        print(json.dumps(result,sort_keys=True));return 0
    except Exception:
        print(json.dumps(dict(status='FUTURE_BUDGET_MATERIALS_REJECTED',consumer_enabled=False,
            submission_enabled=False,economic_authority=False)));return 3


if __name__=='__main__': raise SystemExit(main())
