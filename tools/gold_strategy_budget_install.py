"""Human-only first-day scope activation through the original successor installer.

No service, pilot baseline, task, grant or order is created. Interrupted writes
resume the same complete seed, key and window; unproven activation stays UNKNOWN.
"""
import argparse
import hashlib
import json
import os
import re

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from live.execution import demo_gold_budget_scope as scope_contract
from live.execution.demo_issuer_policy import require
from live.execution.demo_market_partition_runtime import validate_gold_budget_review
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.validation.demo_submission import _decode, validate_registry, registry_sha256
from tools import demo_operating_bundle as installer, demo_operator_setup as setup
from tools import demo_successor_setup as successor, demo_supervisor as supervisor
from tools import gold_strategy_budget_enrollment as preparation, gold_strategy_budget_materials as compiler
from tools.demo_first_gold_install import retain, read_seed
from tools.gold_strategy_policy_install import local_operator

SEEDS = {'spec.json','operator-enrollment.json','registry-preimage.json','assembly.json',
    'seed-signing-key.json','seed-stop.sqlite3'}
SPEC_SCHEMA = 'DEMO_GOLD_BUDGET_SCOPE_INSTALL_SPEC_V1'
INTENT_SCHEMA = 'DEMO_GOLD_BUDGET_SCOPE_INSTALL_INTENT_V1'


def sha(body):return hashlib.sha256(body).hexdigest()


def operator_confirmation(request, cycle, operator):
    require(local_operator()==operator,'GOLD_SCOPE_OPERATOR_CHANGED')
    print(json.dumps(dict(request=request,duration_seconds=86400,installation_allowance_seconds=300,
        original_cycle_sha256=cycle),ensure_ascii=False,indent=2))
    print('仅确认首次黄金 Demo 范围：24小时、300秒安装余量及原有限预算。精确订单仍需独立批准。')
    expected='APPROVE GOLD SCOPE '+request['request_sha256']
    require(input('请输入 '+expected+'\n').strip()==expected,'GOLD_SCOPE_NOT_CONFIRMED')
    require(local_operator()==operator,'GOLD_SCOPE_OPERATOR_CHANGED')
    return dict(schema='DEMO_GOLD_BUDGET_SCOPE_OPERATOR_V1',purpose=request['purpose'],
        request_sha256=request['request_sha256'],original_cycle_sha256=cycle,operator_id=operator,
        approved_at_ms=installer.now_ms(),duration_seconds=86400,installation_allowance_seconds=300,
        execution_approval_granted=False,economic_authority=False)


def pending_stage():
    pending=successor.SETUP/'pending.json'
    if pending.exists() or pending.is_symlink():
        value=successor.read(pending)
        require(type(value) is dict and set(value)=={'schema','enrollment_sha256'}
            and value['schema']=='DEMO_PENDING_ENROLLMENT_V1'
            and type(value['enrollment_sha256']) is str and re.fullmatch('[a-f0-9]{64}',value['enrollment_sha256']),
            'GOLD_SCOPE_PENDING_IDENTITY')
        stage=successor.SETUP/'cycles'/value['enrollment_sha256']
        require((stage/'scope-intent.json').exists(),'GOLD_SCOPE_OTHER_PENDING_ENROLLMENT')
        return stage
    root=successor.SETUP/'cycles'
    if not root.exists() and not root.is_symlink():return None
    require(root.is_dir() and not root.is_symlink(),'GOLD_SCOPE_STAGE_DIRECTORY')
    paths=list(root.iterdir());require(len(paths)<=128,'GOLD_SCOPE_STAGE_BUDGET')
    incomplete=[]
    for stage in paths:
        require(stage.is_dir() and not stage.is_symlink() and re.fullmatch('[a-f0-9]{64}',stage.name),
            'GOLD_SCOPE_STAGE_IDENTITY')
        spec=successor.read(stage/'spec.json')
        if spec.get('schema')!=SPEC_SCHEMA:continue
        if not (stage/'activation.json').exists():incomplete.append(stage)
    require(len(incomplete)<=1,'GOLD_SCOPE_MULTIPLE_INCOMPLETE_STAGES')
    return incomplete[0] if incomplete else None


def create_stage(*,source_commit,original,operator):
    captured=preparation.capture_original_inputs(source_commit=source_commit)
    cycle=setup.digest(original)
    require(captured['original_cycle_sha256']==cycle,'GOLD_SCOPE_ORIGINAL_CYCLE_CHANGED')
    approval=operator_confirmation(captured['request'],cycle,operator)
    require(approval['operator_id']==operator,'GOLD_SCOPE_OPERATOR_CHANGED')
    args={key:captured[key] for key in ('request','review','origin','registry','policy','profile','definition','runtime')}
    stop=setup._stop_image(path=installer.EXECUTOR/'kill-switch.sqlite3');at=installer.now_ms()
    private=Ed25519PrivateKey.generate()
    manifest,files=compiler.assemble(**args,approval=approval,private_key=private,stop_image=stop,now_ms=at)
    del private
    preimage=successor._read_root_owned_json(installer.REGISTRY)
    require(registry_sha256(validate_registry(decode(preimage)))==args['request']['registry_sha256'],
        'GOLD_SCOPE_STAGE_REGISTRY_CHANGED')
    spec=dict(schema=SPEC_SCHEMA,source_commit=source_commit,account_ref_sha256=args['request']['account_ref_sha256'],
        request_sha256=args['request']['request_sha256'],original_cycle_sha256=cycle,
        original_spec_sha256=original['spec_sha256'],orders_submitted=False,economic_authority=False)
    assembly=dict(schema='DEMO_GOLD_BUDGET_SCOPE_ASSEMBLY_INPUTS_V1',assembled_at_ms=at,inputs=args)
    raw=installer.canonical(assembly);require(len(raw)<=16000000,'GOLD_SCOPE_ASSEMBLY_BUDGET')
    seeds={'spec.json':installer.canonical(spec),'operator-enrollment.json':installer.canonical(approval),
        'registry-preimage.json':preimage,'assembly.json':raw,'seed-signing-key.json':files['signing-key.json'],
        'seed-stop.sqlite3':stop}
    root=successor.SETUP/'cycles'
    if not root.exists():root.mkdir(mode=0o700)
    stage=root/setup.digest(approval);stage.mkdir(mode=0o700)
    for name,body in seeds.items():installer.write_new(stage/name,body)
    intent=dict(schema=INTENT_SCHEMA,source_commit=source_commit,enrollment_sha256=setup.digest(approval),
        spec_sha256=setup.digest(spec),original_cycle_sha256=cycle,seed_files={name:sha(body) for name,body in seeds.items()},
        manifest_sha256=setup.digest(manifest),orders_submitted=False,economic_authority=False)
    installer.write_new(stage/'scope-intent.json',installer.canonical(intent))
    return stage


def reconstruct(stage,*,source_commit,operator):
    intent=successor.read(stage/'scope-intent.json')
    require(type(intent) is dict and set(intent)==set('schema source_commit enrollment_sha256 spec_sha256 '
        'original_cycle_sha256 seed_files manifest_sha256 orders_submitted economic_authority'.split())
        and intent['schema']==INTENT_SCHEMA and intent['source_commit']==source_commit
        and intent['enrollment_sha256']==stage.name and intent['orders_submitted'] is False
        and intent['economic_authority'] is False,'GOLD_SCOPE_INSTALL_INTENT')
    for key in ('enrollment_sha256','spec_sha256','original_cycle_sha256','manifest_sha256'):
        require(type(intent[key]) is str and re.fullmatch('[a-f0-9]{64}',intent[key]),'GOLD_SCOPE_INTENT_HASH')
    require(type(intent['seed_files']) is dict and set(intent['seed_files'])==SEEDS,'GOLD_SCOPE_SEED_SCOPE')
    seeds={name:read_seed(stage,name) for name in SEEDS}
    require(all(sha(body)==intent['seed_files'][name] for name,body in seeds.items()),'GOLD_SCOPE_SEED_CHANGED')
    spec=decode(seeds['spec.json']);approval=decode(seeds['operator-enrollment.json']);assembly=decode(seeds['assembly.json'])
    require(type(spec) is dict and set(spec)==set('schema source_commit account_ref_sha256 request_sha256 '
        'original_cycle_sha256 original_spec_sha256 orders_submitted economic_authority'.split())
        and spec['schema']==SPEC_SCHEMA and spec['source_commit']==source_commit
        and spec['orders_submitted'] is False and spec['economic_authority'] is False
        and setup.digest(spec)==intent['spec_sha256'] and setup.digest(approval)==stage.name
        and approval['operator_id']==operator and approval['original_cycle_sha256']==intent['original_cycle_sha256']
        and spec['original_cycle_sha256']==intent['original_cycle_sha256'],'GOLD_SCOPE_STAGED_BINDING')
    require(type(assembly) is dict and set(assembly)=={'schema','assembled_at_ms','inputs'}
        and assembly['schema']=='DEMO_GOLD_BUDGET_SCOPE_ASSEMBLY_INPUTS_V1'
        and type(assembly['assembled_at_ms']) is int
        and approval['approved_at_ms']<=assembly['assembled_at_ms']<=installer.now_ms(),'GOLD_SCOPE_ASSEMBLY_INPUTS')
    args=assembly['inputs'];require(type(args) is dict and set(args)==set(
        'request review origin registry policy profile definition runtime'.split()),'GOLD_SCOPE_INPUT_FIELDS')
    registry=validate_registry(decode(seeds['registry-preimage.json']))
    require(registry==args['registry'] and args['request']['source_commit']==source_commit
        and args['request']['request_sha256']==spec['request_sha256']
        and args['request']['account_ref_sha256']==spec['account_ref_sha256'],'GOLD_SCOPE_ORIGINAL_INPUT_BINDING')
    secret=decode(seeds['seed-signing-key.json'])
    require(type(secret) is dict and set(secret)=={'schema','authority_id','key_id','private_key_base64url'}
        and secret['schema']=='DEMO_ISSUER_KEY_V1','GOLD_SCOPE_SEED_KEY')
    private=Ed25519PrivateKey.from_private_bytes(_decode(secret['private_key_base64url'],32))
    manifest,files=compiler.assemble(**args,approval=approval,private_key=private,
        stop_image=seeds['seed-stop.sqlite3'],now_ms=assembly['assembled_at_ms'])
    del private,secret
    require(files['signing-key.json']==seeds['seed-signing-key.json']
        and setup.digest(manifest)==intent['manifest_sha256'],'GOLD_SCOPE_COMPILED_BYTES_CHANGED')
    require(setup._stop_image(path=installer.EXECUTOR/'kill-switch.sqlite3')==seeds['seed-stop.sqlite3'],
        'GOLD_SCOPE_ORIGINAL_STOP_CHANGED')
    return intent,args,registry,manifest,files,seeds['registry-preimage.json']


def fresh_review(intent,args,files,observed):
    from tools.demo_functional_cycle import Runtime
    expected=validate_registry(decode(files['registry.json']))
    require(observed in (args['registry'],expected),'GOLD_SCOPE_ACTIVATION_REGISTRY_CHANGED')
    scope=decode(files['policy.json']);profile=decode(files['profile.json'])
    scope_contract.pilot_window(scope,registry=expected,now_ms=installer.now_ms())
    runtime_raw=supervisor._read_root_owned_json(supervisor.STATE/'strategy-runtime.json')
    require(decode(runtime_raw)==args['runtime'],'GOLD_SCOPE_RUNTIME_CHANGED')
    definition=preparation.load_definition(installer.EXECUTOR,args['runtime'],
        source_commit=intent['source_commit'],uid=expected['executor_uid'])
    require(setup.digest(definition)==setup.digest(args['definition']),'GOLD_SCOPE_DEFINITION_CHANGED')
    current,origin=setup._runtime_inputs(intent['source_commit'],continuation=True,symbol='XAUUSDT')
    require(current==observed,'GOLD_SCOPE_ACTIVATION_CAPTURE_CHANGED')
    identity=sha((json.dumps(origin,sort_keys=True,indent=2)+'\n').encode())
    channel=Runtime(intent['source_commit'],observed,intent['original_cycle_sha256'])
    try:result=channel.invoke('context',payload={'gold_budget_scope_origin_sha256':identity},timeout=60)
    finally:channel.close()
    require(result.get('status')=='GOLD_BUDGET_SCOPE_REVIEW'
        and result.get('submission_enabled') is False and result.get('economic_authority') is False,
        'GOLD_SCOPE_ACTIVATION_REVIEW_UNAVAILABLE')
    report=validate_gold_budget_review(result['review'],origin=origin,registry=observed,
        source_commit=intent['source_commit'],as_of_ms=installer.now_ms())
    excluded={'registry_sha256','origin_sha256','reviewed_at_ms','valid_until_ms'}
    require(setup.digest({k:v for k,v in report.items() if k not in excluded})
        ==setup.digest({k:v for k,v in args['review'].items() if k not in excluded}), 'GOLD_SCOPE_BOOK_OR_RISK_CHANGED')
    preparation.verify_strategy_parameters(origin=origin,profile=profile,definition=definition,
        runtime=args['runtime'],source_commit=intent['source_commit'],scope=scope['scope'])
    require(successor.read(installer.REGISTRY)==observed
        and supervisor._read_root_owned_json(supervisor.STATE/'strategy-runtime.json')==runtime_raw
        and setup.digest(preparation.load_definition(installer.EXECUTOR,args['runtime'],
            source_commit=intent['source_commit'],uid=expected['executor_uid']))==setup.digest(definition)
        and setup._stop_image(path=installer.EXECUTOR/'kill-switch.sqlite3')==files['kill-switch.sqlite3'],
        'GOLD_SCOPE_ACTIVATION_INPUTS_CHANGED')
    scope_contract.pilot_window(scope,registry=expected,now_ms=installer.now_ms())
    return report


def retain_review(stage,report):
    history=list(stage.glob('scope-review-*.json'));require(len(history)<=32,'GOLD_SCOPE_REVIEW_BUDGET')
    excluded={'registry_sha256','origin_sha256','reviewed_at_ms','valid_until_ms'}
    expected={k:v for k,v in report.items() if k not in excluded}
    for path in history:
        value=successor.read(path)
        require(path.name=='scope-review-'+setup.digest(value)+'.json'
            and set(value)==set(report) and setup.digest({k:v for k,v in value.items() if k not in excluded})
            ==setup.digest(expected),'GOLD_SCOPE_REVIEW_HISTORY_CHANGED')
    path=stage/('scope-review-'+setup.digest(report)+'.json')
    require(path.exists() or len(history)<32,'GOLD_SCOPE_REVIEW_BUDGET')
    retain(path,installer.canonical(report))


def finish_stage(stage,intent,manifest,files):
    bundle=stage/'bundle'
    if not bundle.exists():bundle.mkdir(mode=0o700)
    require(bundle.is_dir() and not bundle.is_symlink()
        and {p.name for p in bundle.iterdir()}<=set(files)|{'manifest.json'},'GOLD_SCOPE_BUNDLE_SCOPE')
    for name,body in files.items():retain(bundle/name,body,maximum=64*1024*1024 if name=='kill-switch.sqlite3' else 65536)
    retain(bundle/'manifest.json',installer.canonical(manifest))
    result=dict(schema='DEMO_OPERATOR_SETUP_RESULT_V1',status='INSTALLED_SCOPE_ONLY',
        spec_sha256=intent['spec_sha256'],enrollment_sha256=intent['enrollment_sha256'],source_commit=intent['source_commit'],
        task_sha256=None,orders_submitted=False,economic_authority=False)
    retain(stage/'result.json',installer.canonical(result))
    retain(successor.SETUP/'pending.json',installer.canonical(dict(schema='DEMO_PENDING_ENROLLMENT_V1',enrollment_sha256=stage.name)))
    return result


def completed(stage,*,source_commit,operator):
    intent,args,legacy,manifest,files,_=reconstruct(stage,source_commit=source_commit,operator=operator)
    require(successor.read(stage/'activation.json')=={'schema':'DEMO_PENDING_ENROLLMENT_V1','enrollment_sha256':stage.name},
        'GOLD_SCOPE_COMPLETION_MARKER')
    saved_manifest,saved_files=installer.read_materials(stage/'bundle',intent['manifest_sha256'])
    require(saved_manifest==manifest and saved_files==files,'GOLD_SCOPE_COMPLETED_BUNDLE_CHANGED')
    targets={'signing-key.json':'signing-key.json','profile.json':'profile.json','policy.json':'policy.json','authority.json':'registry.json'}
    require(all(successor._read_root_owned_json(installer.CONFIG/name)==files[key] for name,key in targets.items())
        and successor._read_root_owned_json(installer.REGISTRY)==files['registry.json'],'GOLD_SCOPE_COMPLETED_CONFIG_CHANGED')
    scope_contract.historical_scope_binding(decode(files['policy.json']),decode(files['registry.json']))


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True);parser.add_argument('--enroll-approved',action='store_true')
    args=parser.parse_args(argv);previous=os.umask(0o077);phase='PREFLIGHT';unknown=False
    try:
        require(args.enroll_approved,'GOLD_SCOPE_USE_UNSIGNED_PREPARATION')
        operator=local_operator();successor.verify_source(args.source_commit)
        with successor.cycle_lock(installer.CONTROLLER/'functional-cycle',maintenance=True):
            original_stage=successor.active_setup();original=successor.read(original_stage/'result.json')
            stage=pending_stage();unknown=stage is not None
            if stage is None and (original_stage/'scope-intent.json').exists():
                unknown=True;completed(original_stage,source_commit=args.source_commit,operator=operator)
                print(json.dumps(dict(status='GOLD_BUDGET_SCOPE_ALREADY_INSTALLED',budget_scope_installed=True,
                    authorization_current_verified=False,orders_submitted=False,services_started=False,economic_authority=False)));return 0
            if stage is None:
                phase='OPERATOR_ENROLLMENT'
                stage=create_stage(source_commit=args.source_commit,original=original,operator=operator)
            unknown=True;phase='STAGED_REVIEW'
            intent,inputs,legacy,manifest,files,preimage=reconstruct(stage,source_commit=args.source_commit,operator=operator)
            require(local_operator()==operator,'GOLD_SCOPE_OPERATOR_CHANGED')
            observed=successor.read(installer.REGISTRY)
            retain_review(stage,fresh_review(intent,inputs,files,observed))
            result=finish_stage(stage,intent,manifest,files)
            manifest,files=installer.read_materials(stage/'bundle',intent['manifest_sha256'])
            materials=installer.validate_materials(manifest=manifest,files=files,existing_registry=legacy,
                source_commit=args.source_commit,account_ref=inputs['request']['account_ref_sha256'],now_ms=installer.now_ms())
            retain_review(stage,fresh_review(intent,inputs,files,successor.read(installer.REGISTRY)))
            require(local_operator()==operator,'GOLD_SCOPE_OPERATOR_CHANGED')
            phase='ACTIVATION';successor.install_successor(materials,files,registry_preimage=preimage,stage=stage)
            require(successor.active_setup()==stage,'GOLD_SCOPE_ACTIVE_POINTER_CHANGED')
            print(json.dumps(dict(status='GOLD_BUDGET_SCOPE_INSTALLED',source_commit=args.source_commit,result=result,
                budget_scope_installed=True,orders_submitted=False,services_started=False,pilot_started=False,economic_authority=False)));return 0
    except Exception as error:
        print(json.dumps(dict(status='NOT_COMPLETED',phase=phase,reason=str(error),budget_scope_installed=None if unknown else False,
            orders_submitted=False,services_started=False,economic_authority=False)));return 3
    finally:os.umask(previous)


if __name__=='__main__':raise SystemExit(main())
