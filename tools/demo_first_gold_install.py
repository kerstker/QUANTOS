"""Human-only activation of the separately approved first Gold functional scope.

No supervisor dispatch, service start or authorization-window renewal. Partial
writes resume only from the same complete protected seed and retained intent.
The source/identity/custody checks belong to the actual local operator runtime.
"""
import argparse
import hashlib
import json
import os
import re

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from live.execution.demo_issuer_policy import require
from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from services.validation.demo_submission import validate_registry,registry_sha256,_decode
from tools import demo_successor_setup as successor
from tools import demo_operator_setup as setup,demo_operating_bundle as installer
from tools.gold_strategy_policy_install import local_operator

SEEDS={'spec.json','operator-enrollment.json','registry-preimage.json','assembly.json',
    'seed-signing-key.json','seed-stop.sqlite3'}


def sha(value):return hashlib.sha256(value).hexdigest()


def read_seed(stage,name):
    maximum=64*1024*1024 if name=='seed-stop.sqlite3' else 16000000 if name=='assembly.json' else 65536
    return installer.read_private(stage/name,maximum=maximum)


def retain(path,body,*,maximum=65536):
    if path.exists() or path.is_symlink():
        require(installer.read_private(path,maximum=maximum)==body,'FIRST_GOLD_RETAINED_BYTES_CHANGED')
    else:installer.write_new(path,body)


def activation_review_for(source_commit,observed,legacy,expected,cycle,original_review):
    from tools.demo_functional_cycle import Runtime
    from live.execution.demo_market_partition_runtime import validate_first_gold_activation_review
    current,origin=setup._runtime_inputs(source_commit,continuation=True,symbol='XAUUSDT')
    require(current==observed,'FIRST_GOLD_ACTIVATION_CAPTURE_REGISTRY_CHANGED')
    identity=sha((json.dumps(origin,sort_keys=True,indent=2)+'\n').encode())
    runtime=Runtime(source_commit,observed,cycle)
    try:
        result=runtime.invoke('context',payload={'first_gold_activation_origin_sha256':identity,
            'expected_new_registry':expected},timeout=60)
    finally:runtime.close()
    require(result['status']=='FIRST_GOLD_ACTIVATION_REVIEW','FIRST_GOLD_ACTIVATION_REVIEW_UNAVAILABLE')
    report=validate_first_gold_activation_review(result['review'],origin=origin,
        legacy_registry=legacy,expected_registry=expected,observed_registry=observed,
        original_review=original_review,source_commit=source_commit,as_of_ms=installer.now_ms())
    return report


def pending_stage(original_stage):
    pending=successor.SETUP/'pending.json'
    if pending.exists() or pending.is_symlink():
        value=successor.read(pending)
        require(type(value) is dict and set(value)=={'schema','enrollment_sha256'}
            and value['schema']=='DEMO_PENDING_ENROLLMENT_V1'
            and type(value['enrollment_sha256']) is str
            and re.fullmatch('[a-f0-9]{64}',value['enrollment_sha256']),'FIRST_GOLD_PENDING_IDENTITY')
        return successor.SETUP/'cycles'/value['enrollment_sha256']
    stages=successor.SETUP/'cycles'
    if not stages.exists() and not stages.is_symlink():return None
    require(stages.is_dir() and not stages.is_symlink(),'FIRST_GOLD_STAGE_DIRECTORY')
    paths=list(stages.iterdir());require(len(paths)<=128,'FIRST_GOLD_STAGE_BUDGET')
    incomplete=[]
    for stage in paths:
        require(stage.is_dir() and not stage.is_symlink()
            and re.fullmatch('[a-f0-9]{64}',stage.name),'FIRST_GOLD_STAGE_IDENTITY')
        if stage==original_stage:continue
        if not (stage/'spec.json').exists():raise ValueError('FIRST_GOLD_ORPHAN_STAGE_INCOMPLETE')
        spec=successor.read(stage/'spec.json')
        if 'first_gold_enrollment' not in spec:continue
        if not (stage/'activation.json').exists():incomplete.append(stage)
    require(len(incomplete)<=1,'FIRST_GOLD_MULTIPLE_INCOMPLETE_STAGES')
    return incomplete[0] if incomplete else None


def create_stage(*,source_commit,cycle,registry,operator):
    report,origin=successor.first_gold_review_for(source_commit,registry,cycle,with_origin=True)
    spec=setup.propose(origin=origin,registry=registry,source_commit=source_commit,
        now_ms=installer.now_ms(),symbol='XAUUSDT',first_gold_review=report)
    approval=setup.operator_confirmation(spec)
    require(approval['operator_id']==operator,'FIRST_GOLD_OPERATOR_CHANGED')
    report,origin=successor.first_gold_review_for(source_commit,registry,cycle,with_origin=True)
    stop=setup._stop_image(path=installer.EXECUTOR/'kill-switch.sqlite3')
    assembled=installer.now_ms();private=Ed25519PrivateKey.generate()
    manifest,files=setup.assemble(spec=spec,approval=approval,origin=origin,registry=registry,
        private_key=private,stop_image=stop,now_ms=assembled,first_gold_review=report)
    del private
    preimage=successor._read_root_owned_json(installer.REGISTRY)
    require(registry_sha256(validate_registry(decode(preimage)))==spec['registry_sha256'],
        'FIRST_GOLD_STAGE_REGISTRY_CHANGED')
    assembly=dict(schema='DEMO_FIRST_GOLD_ASSEMBLY_INPUTS_V1',assembled_at_ms=assembled,
        origin=origin,review=report)
    body=installer.canonical(assembly);require(len(body)<=16000000,'FIRST_GOLD_ASSEMBLY_BUDGET')
    seeds={'spec.json':installer.canonical(spec),'operator-enrollment.json':installer.canonical(approval),
        'registry-preimage.json':preimage,'assembly.json':body,
        'seed-signing-key.json':files['signing-key.json'],'seed-stop.sqlite3':stop}
    stages=successor.SETUP/'cycles'
    if not stages.exists():stages.mkdir(mode=0o700)
    stage=stages/setup.digest(approval);stage.mkdir(mode=0o700)
    for name,raw in seeds.items():installer.write_new(stage/name,raw)
    intent=dict(schema='DEMO_FIRST_GOLD_INSTALL_INTENT_V1',source_commit=source_commit,
        enrollment_sha256=setup.digest(approval),spec_sha256=setup.digest(spec),original_cycle_sha256=cycle,
        seed_files={name:sha(raw) for name,raw in seeds.items()},manifest_sha256=setup.digest(manifest),
        orders_submitted=False,economic_authority=False)
    installer.write_new(stage/'first-gold-intent.json',installer.canonical(intent))
    return stage


def reconstruct(stage,*,source_commit,operator):
    """Replay the original compiler as an integrity check, never as a new grant."""
    intent=successor.read(stage/'first-gold-intent.json')
    require(type(intent) is dict and set(intent)==set('schema source_commit enrollment_sha256 spec_sha256 '
        'original_cycle_sha256 seed_files manifest_sha256 orders_submitted economic_authority'.split())
        and intent['schema']=='DEMO_FIRST_GOLD_INSTALL_INTENT_V1' and intent['source_commit']==source_commit
        and intent['enrollment_sha256']==stage.name and intent['orders_submitted'] is False
        and intent['economic_authority'] is False,'FIRST_GOLD_INTENT_FIELDS')
    for name in ('enrollment_sha256','spec_sha256','original_cycle_sha256','manifest_sha256'):
        require(type(intent[name]) is str and re.fullmatch('[a-f0-9]{64}',intent[name]),'FIRST_GOLD_INTENT_HASH')
    require(type(intent['seed_files']) is dict and set(intent['seed_files'])==SEEDS,'FIRST_GOLD_SEED_SCOPE')
    seeds={name:read_seed(stage,name) for name in SEEDS}
    require(all(sha(raw)==intent['seed_files'][name] for name,raw in seeds.items()),'FIRST_GOLD_SEED_CHANGED')
    spec=setup.spec_check(decode(seeds['spec.json']));approval=decode(seeds['operator-enrollment.json'])
    require(spec['schema']==setup.GOLD_SCHEMA and 'first_gold_enrollment' in spec
        and setup.digest(spec)==intent['spec_sha256'] and setup.digest(approval)==intent['enrollment_sha256']
        and approval['operator_id']==operator,'FIRST_GOLD_ENROLLMENT_BINDING')
    setup._approval_check(approval,spec,installer.now_ms())
    require(spec['source_commit']==source_commit,'FIRST_GOLD_STAGED_SOURCE')
    registry=validate_registry(decode(seeds['registry-preimage.json']))
    assembly=decode(seeds['assembly.json'])
    require(type(assembly) is dict and set(assembly)=={'schema','assembled_at_ms','origin','review'}
        and assembly['schema']=='DEMO_FIRST_GOLD_ASSEMBLY_INPUTS_V1'
        and type(assembly['assembled_at_ms']) is int
        and approval['approved_at_ms']<=assembly['assembled_at_ms']<=installer.now_ms(),'FIRST_GOLD_ASSEMBLY_INPUTS')
    secret=decode(seeds['seed-signing-key.json'])
    require(type(secret) is dict and set(secret)=={'schema','authority_id','key_id','private_key_base64url'}
        and secret['schema']=='DEMO_ISSUER_KEY_V1','FIRST_GOLD_SEED_KEY')
    private=Ed25519PrivateKey.from_private_bytes(_decode(secret['private_key_base64url'],32))
    manifest,files=setup.assemble(spec=spec,approval=approval,origin=assembly['origin'],registry=registry,
        private_key=private,stop_image=seeds['seed-stop.sqlite3'],now_ms=assembly['assembled_at_ms'],
        first_gold_review=assembly['review'])
    del private,secret
    require(files['signing-key.json']==seeds['seed-signing-key.json']
        and setup.digest(manifest)==intent['manifest_sha256'],'FIRST_GOLD_COMPILED_MATERIALS_CHANGED')
    require(setup._stop_image(path=installer.EXECUTOR/'kill-switch.sqlite3')==seeds['seed-stop.sqlite3'],
        'FIRST_GOLD_ORIGINAL_STOP_CHANGED')
    return intent,spec,approval,registry,manifest,files,seeds['registry-preimage.json']


def finish_stage(stage,manifest,files,intent,spec):
    bundle=stage/'bundle'
    if not bundle.exists():bundle.mkdir(mode=0o700)
    require(bundle.is_dir() and not bundle.is_symlink(),'FIRST_GOLD_BUNDLE_DIRECTORY')
    require({path.name for path in bundle.iterdir()}<=set(files)|{'manifest.json'},'FIRST_GOLD_BUNDLE_FILE_SCOPE')
    for name,body in files.items():
        retain(bundle/name,body,maximum=64*1024*1024 if name=='kill-switch.sqlite3' else 65536)
    retain(bundle/'manifest.json',installer.canonical(manifest))
    result=dict(schema='DEMO_OPERATOR_SETUP_RESULT_V1',status='INSTALLED_PENDING_CYCLE',
        spec_sha256=intent['spec_sha256'],enrollment_sha256=intent['enrollment_sha256'],
        source_commit=intent['source_commit'],task_sha256=manifest['tasks'][0],
        orders_submitted=False,economic_authority=False)
    retain(stage/'result.json',installer.canonical(result))
    pending=dict(schema='DEMO_PENDING_ENROLLMENT_V1',enrollment_sha256=intent['enrollment_sha256'])
    retain(successor.SETUP/'pending.json',installer.canonical(pending))
    return result


def retain_review(stage,report):
    from live.execution.demo_market_partition_runtime import _FIRST_ACTIVATION_FIELDS
    files=list(stage.glob('activation-review-*.json'))
    require(len(files)<=32,'FIRST_GOLD_REVIEW_HISTORY_BUDGET')
    for path in files:
        value=successor.read(path)
        require(path.name=='activation-review-'+setup.digest(value)+'.json'
            and type(value) is dict and set(value)==_FIRST_ACTIVATION_FIELDS
            and all(value[key]==report[key] for key in ('schema','status','source_commit','account_ref_sha256',
                'legacy_registry_sha256','expected_registry_sha256','partition_binding','original_cut_at_ms',
                'legacy_state_sha256','sealed_usdt_wallet','gold_position_context','collateral_conversion'))
            and all(value[key] is False for key in ('configuration_activation_verified','approval_granted',
                'source_verified','submission_enabled','economic_authority')),'FIRST_GOLD_REVIEW_HISTORY_CHANGED')
    path=stage/('activation-review-'+setup.digest(report)+'.json')
    require(path.exists() or len(files)<32,'FIRST_GOLD_REVIEW_HISTORY_BUDGET')
    retain(path,installer.canonical(report))


def completed(stage,source_commit):
    intent=successor.read(stage/'first-gold-intent.json')
    require(intent['source_commit']==source_commit and intent['enrollment_sha256']==stage.name,
        'FIRST_GOLD_COMPLETED_SOURCE')
    require(successor.read(stage/'activation.json')=={
        'schema':'DEMO_PENDING_ENROLLMENT_V1','enrollment_sha256':stage.name},'FIRST_GOLD_COMPLETED_MARKER')
    manifest,files=installer.read_materials(stage/'bundle',intent['manifest_sha256'])
    require(manifest['source_commit']==source_commit,'FIRST_GOLD_COMPLETED_MANIFEST')
    targets={'signing-key.json':'signing-key.json','profile.json':'profile.json',
        'policy.json':'policy.json','authority.json':'registry.json'}
    require(all(successor._read_root_owned_json(installer.CONFIG/name)==files[key] for name,key in targets.items())
        and successor._read_root_owned_json(installer.REGISTRY)==files['registry.json'],
        'FIRST_GOLD_COMPLETED_CONFIGURATION_CHANGED')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--enroll-approved',action='store_true')
    args=parser.parse_args(argv);previous=os.umask(0o077);phase='PREFLIGHT';activation_unknown=False
    try:
        require(args.enroll_approved,'FIRST_GOLD_USE_UNSIGNED_PROPOSAL')
        operator=local_operator();successor.verify_source(args.source_commit)
        with successor.cycle_lock(installer.CONTROLLER/'functional-cycle',maintenance=True):
            original_stage=successor.active_setup();original=successor.read(original_stage/'result.json')
            old_spec=setup.spec_check(successor.read(original_stage/'spec.json'))
            require(setup.digest(old_spec)==original['spec_sha256']
                and old_spec['source_commit']==original['source_commit'],'FIRST_GOLD_ORIGINAL_SPEC')
            stage=pending_stage(original_stage)
            activation_unknown=stage is not None or old_spec['symbol']=='XAUUSDT'
            if old_spec['symbol']=='XAUUSDT':
                require('first_gold_enrollment' in old_spec,'FIRST_GOLD_OTHER_ACTIVE_ENROLLMENT')
                if stage is None:
                    require((original_stage/'activation.json').exists(),'FIRST_GOLD_COMPLETION_UNPROVEN')
                    completed(original_stage,args.source_commit)
                    print(json.dumps(dict(status='FIRST_GOLD_ALREADY_ENROLLED',source_commit=args.source_commit,
                        gold_enrollment_installed=True,authorization_current_verified=False,
                        orders_submitted=False,services_started=False,economic_authority=False)));return 0
                require(stage==original_stage,'FIRST_GOLD_PENDING_ACTIVE_MISMATCH')
            else:require(old_spec['symbol']=='BTCUSDC','FIRST_GOLD_LEGACY_MARKET_REQUIRED')
            if stage is None:
                phase='OPERATOR_ENROLLMENT'
                registry=successor.read(installer.REGISTRY)
                stage=create_stage(source_commit=args.source_commit,cycle=setup.digest(original),
                    registry=registry,operator=operator)
            phase='STAGED_REVIEW'
            intent,spec,approval,legacy,manifest,files,preimage=reconstruct(stage,
                source_commit=args.source_commit,operator=operator)
            observed=successor.read(installer.REGISTRY);expected=validate_registry(decode(files['registry.json']))
            report=activation_review_for(args.source_commit,observed,legacy,expected,
                intent['original_cycle_sha256'],spec['first_gold_enrollment'])
            retain_review(stage,report)
            result=finish_stage(stage,manifest,files,intent,spec)
            manifest,files=installer.read_materials(stage/'bundle',setup.digest(manifest))
            materials=installer.validate_materials(manifest=manifest,files=files,existing_registry=legacy,
                source_commit=args.source_commit,account_ref=spec['account_ref_sha256'],now_ms=installer.now_ms())
            current=successor.read(installer.REGISTRY)
            report=activation_review_for(args.source_commit,current,legacy,expected,
                intent['original_cycle_sha256'],spec['first_gold_enrollment'])
            retain_review(stage,report)
            phase='ACTIVATION'
            successor.install_successor(materials,files,registry_preimage=preimage,stage=stage)
            require(successor.active_setup()==stage,'FIRST_GOLD_ACTIVE_POINTER_CHANGED')
            print(json.dumps(dict(status='FIRST_GOLD_ENROLLMENT_INSTALLED',source_commit=args.source_commit,
                result=result,gold_enrollment_installed=True,authorization_window_seconds=spec['window_seconds'],
                orders_submitted=False,services_started=False,economic_authority=False)));return 0
    except Exception:
        print(json.dumps(dict(schema='DEMO_FIRST_GOLD_INSTALL_RESULT_V1',status='NOT_COMPLETED',phase=phase,
            gold_enrollment_installed=None if activation_unknown or phase=='ACTIVATION' else False,
            orders_submitted=False,services_started=False,economic_authority=False)));return 3
    finally:os.umask(previous)


if __name__=='__main__':raise SystemExit(main())
