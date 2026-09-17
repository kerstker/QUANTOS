"""Explicit finite successor enrollment without replacing the original book.

An existing expired enrollment, a verified flat continuation and a new local
operator confirmation are required. Staged activation retains exact preimages
and can resume an interrupted install without replacing append-only stores.
"""
import argparse
import json
import os
from pathlib import Path
import re

from live.execution.demo_authority import _read_root_owned_json
from live.execution.demo_issuer_policy import require, validate_policy
from services.governance.json_integrity import load_json_without_duplicate_keys
from services.validation.demo_submission import validate_registry, registry_sha256
from tools import demo_operating_bundle as installer, demo_operator_setup as setup
from tools.demo_issuer import verify_source
from tools.demo_cycle_checkpoint import cycle_lock

SETUP=Path('/etc/quantos/demo-operator-setup')


def read(path):return load_json_without_duplicate_keys(_read_root_owned_json(path))


def active_setup(*,root=None):
    root=SETUP if root is None else root
    pointer=root/'active.json'
    if not pointer.exists() and not pointer.is_symlink():return root
    value=read(pointer)
    require(type(value) is dict and set(value)=={'schema','enrollment_sha256','result_sha256'} and
        value['schema']=='DEMO_ACTIVE_ENROLLMENT_V1' and
        re.fullmatch('[a-f0-9]{64}',value['enrollment_sha256']) and
        re.fullmatch('[a-f0-9]{64}',value['result_sha256']),'SUCCESSOR_ACTIVE_POINTER')
    stage=root/'cycles'/value['enrollment_sha256']
    result=read(stage/'result.json')
    require(setup.digest(result)==value['result_sha256'] and
        result['enrollment_sha256']==value['enrollment_sha256'],'SUCCESSOR_ACTIVE_BINDING')
    return stage


def cycle_area(stage,cycle):
    root=installer.CONTROLLER/'functional-cycle'
    return root if stage==SETUP else root/'cycles'/cycle


def _replace(path,body,*,gid=0,mode=0o600):
    temporary=path.parent/('.successor-'+os.urandom(8).hex())
    installer.write_new(temporary,body,gid=gid,mode=mode)
    os.replace(temporary,path)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)


def install_successor(materials,files,*,registry_preimage,stage):
    """Activation is last; mixed preimages/new bytes are resumable, never erased."""
    registry=materials['registry'];old=validate_registry(load_json_without_duplicate_keys(registry_preimage))
    require(old['keys'] and all(k['valid_until_ms']<=installer.now_ms() for k in old['keys']),
            'SUCCESSOR_PRIOR_POLICY_STILL_VALID')
    require(registry['keys'][:-1]==old['keys'] and len(registry['keys'])==len(old['keys'])+1,
            'SUCCESSOR_REGISTRY_EXTENSION')
    require(setup._stop_image(path=installer.EXECUTOR/'kill-switch.sqlite3')==files['kill-switch.sqlite3'],
            'SUCCESSOR_STOP_CHANGED')
    current=_read_root_owned_json(installer.REGISTRY)
    require(current in (registry_preimage,installer.canonical(registry)),'SUCCESSOR_REGISTRY_CHANGED')
    previous=stage/'previous-files'
    if not previous.exists():previous.mkdir(mode=0o700)
    targets={'signing-key.json':'signing-key.json','profile.json':'profile.json',
             'policy.json':'policy.json','authority.json':'registry.json'}
    # Retain all old files before changing any of them. Retried operations may
    # observe either the exact retained old bytes or the exact staged new bytes.
    for name in targets:
        path=previous/name
        if not path.exists():installer.write_new(path,_read_root_owned_json(installer.CONFIG/name))
    old_policy=validate_policy(read(previous/'policy.json'))
    require(old_policy['valid_until_ms']<=installer.now_ms() and
        old_policy['registry_sha256']==registry_sha256(old),'SUCCESSOR_PRIOR_POLICY_BINDING')
    for name,key in targets.items():
        value=_read_root_owned_json(installer.CONFIG/name)
        require(value in (_read_root_owned_json(previous/name),files[key]),'SUCCESSOR_CONFIG_CHANGED')
    for task in materials['tasks']:
        path=installer.CONTROLLER/'tasks'/(setup.digest(task)+'.json');body=installer.canonical(task)
        if path.exists():require(_read_root_owned_json(path)==body,'SUCCESSOR_TASK_CHANGED')
        else:installer.write_new(path,body)
    for name,key in targets.items():
        path=installer.CONFIG/name
        if _read_root_owned_json(path)!=files[key]:_replace(path,files[key],gid=registry['issuer_uid'],mode=0o640)
    require(_read_root_owned_json(installer.REGISTRY) in (registry_preimage,installer.canonical(registry)),
            'SUCCESSOR_REGISTRY_CHANGED')
    if _read_root_owned_json(installer.REGISTRY)!=installer.canonical(registry):
        _replace(installer.REGISTRY,installer.canonical(registry),gid=registry['executor_uid'],mode=0o640)
    require(all(_read_root_owned_json(installer.CONFIG/name)==files[key] for name,key in targets.items()) and
        _read_root_owned_json(installer.REGISTRY)==installer.canonical(registry),'SUCCESSOR_ACTIVATION_INCOMPLETE')
    result=read(stage/'result.json')
    pointer=dict(schema='DEMO_ACTIVE_ENROLLMENT_V1',enrollment_sha256=result['enrollment_sha256'],result_sha256=setup.digest(result))
    _replace(SETUP/'active.json',installer.canonical(pointer))
    pending=SETUP/'pending.json'
    if pending.exists():
        require(read(pending)=={'schema':'DEMO_PENDING_ENROLLMENT_V1','enrollment_sha256':result['enrollment_sha256']},
                'SUCCESSOR_PENDING_CHANGED')
        destination=stage/'activation.json'
        require(not destination.exists(),'SUCCESSOR_ACTIVATION_ALREADY_RETAINED')
        os.rename(pending,destination)
        for directory in (SETUP,stage):
            fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(fd)
            finally:os.close(fd)


def context_for(source_commit,registry,cycle):
    from tools.demo_functional_cycle import Runtime
    runtime=Runtime(source_commit,registry,cycle)
    try:
        result=runtime.invoke('context',timeout=60)
        require(result['status']=='CANONICAL_CONTEXT','SUCCESSOR_CONTEXT_UNAVAILABLE')
        if result['context']['day_id']!=installer.now_ms()//86400000:
            result=runtime.invoke('day-checkpoint',timeout=415)
            require(result['status']=='CANONICAL_CONTEXT' and
                    result['context']['day_id']==installer.now_ms()//86400000,'SUCCESSOR_DAY_CHECKPOINT_REQUIRED')
        return result['context']
    finally:runtime.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--enroll',action='store_true')
    parser.add_argument('--run-cycle',action='store_true')
    args=parser.parse_args(argv);previous=os.umask(0o077);phase='PREFLIGHT'
    try:
        verify_source(args.source_commit);require(os.geteuid()==0,'SUCCESSOR_ROOT_REQUIRED')
        require(not args.run_cycle or args.enroll,'SUCCESSOR_ENROLLMENT_REQUIRED')
        with cycle_lock(installer.CONTROLLER/'functional-cycle'):
            original_stage=active_setup();installed=read(original_stage/'result.json');cycle=setup.digest(installed)
            pending=SETUP/'pending.json'
            if pending.exists() or pending.is_symlink():
                record=read(pending)
                require(set(record)=={'schema','enrollment_sha256'} and record['schema']=='DEMO_PENDING_ENROLLMENT_V1' and
                    re.fullmatch('[a-f0-9]{64}',record['enrollment_sha256']),'SUCCESSOR_PENDING_RECORD')
                stage=SETUP/'cycles'/record['enrollment_sha256']
                if not args.enroll:
                    print(json.dumps(dict(status='STAGED_ENROLLMENT_PENDING',orders_submitted=False,economic_authority=False)));return 0
                # A completed pointer is not another permission to repeat it.
                require(stage!=original_stage or not cycle_area(stage,setup.digest(read(stage/'result.json'))).exists(),
                        'SUCCESSOR_ALREADY_ACTIVE_USE_RESUME')
                approval=read(stage/'operator-enrollment.json');spec=setup.spec_check(read(stage/'spec.json'))
                setup._approval_check(approval,spec,installer.now_ms())
                require(spec['source_commit']==args.source_commit,'SUCCESSOR_PENDING_SOURCE')
                registry_preimage=_read_root_owned_json(stage/'registry-preimage.json')
            else:
                registry,origin=setup._runtime_inputs(args.source_commit,continuation=True)
                context=context_for(args.source_commit,registry,cycle)
                current,origin=setup._runtime_inputs(args.source_commit,continuation=True)
                require(current==registry,'SUCCESSOR_REGISTRY_CHANGED')
                spec=setup.propose(origin=origin,registry=registry,source_commit=args.source_commit,
                    now_ms=installer.now_ms(),position_context=context)
                if not args.enroll:
                    print(json.dumps(dict(status='UNSIGNED_SUCCESSOR_PROPOSAL',spec=spec,spec_sha256=setup.digest(spec),
                        original_equity=context['initial_equity'],current_wallet=context['canonical_wallet'],
                        orders_submitted=False,economic_authority=False)));return 0
                phase='OPERATOR_ENROLLMENT';approval=setup.operator_confirmation(spec)
                phase='FRESH_INPUTS';current,origin=setup._runtime_inputs(args.source_commit,continuation=True)
                require(current==registry,'SUCCESSOR_REGISTRY_CHANGED')
                context=context_for(args.source_commit,registry,cycle)
                current,origin=setup._runtime_inputs(args.source_commit,continuation=True)
                require(current==registry,'SUCCESSOR_REGISTRY_CHANGED')
                stop=setup._stop_image(path=installer.EXECUTOR/'kill-switch.sqlite3')
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                private=Ed25519PrivateKey.generate()
                manifest,files=setup.assemble(spec=spec,approval=approval,origin=origin,registry=registry,
                    private_key=private,stop_image=stop,now_ms=installer.now_ms(),position_context=context)
                del private
                stages=SETUP/'cycles'
                if not stages.exists():stages.mkdir(mode=0o700)
                stage=stages/setup.digest(approval);stage.mkdir(mode=0o700)
                registry_preimage=_read_root_owned_json(installer.REGISTRY)
                installer.write_new(stage/'registry-preimage.json',registry_preimage)
                for name,value in (('spec.json',spec),('operator-enrollment.json',approval)):
                    installer.write_new(stage/name,installer.canonical(value))
                bundle=stage/'bundle';bundle.mkdir(mode=0o700)
                for name,body in files.items():installer.write_new(bundle/name,body)
                installer.write_new(bundle/'manifest.json',installer.canonical(manifest))
                result=dict(schema='DEMO_OPERATOR_SETUP_RESULT_V1',status='INSTALLED_PENDING_CYCLE',
                    spec_sha256=setup.digest(spec),enrollment_sha256=setup.digest(approval),source_commit=args.source_commit,
                    task_sha256=manifest['tasks'][0],orders_submitted=False,economic_authority=False)
                installer.write_new(stage/'result.json',installer.canonical(result))
                installer.write_new(pending,installer.canonical(dict(schema='DEMO_PENDING_ENROLLMENT_V1',
                    enrollment_sha256=setup.digest(approval))))
            phase='ACTIVATION'
            manifest=read(stage/'bundle/manifest.json')
            manifest,files=installer.read_materials(stage/'bundle',setup.digest(manifest))
            registry=validate_registry(load_json_without_duplicate_keys(registry_preimage))
            materials=installer.validate_materials(manifest=manifest,files=files,existing_registry=registry,
                source_commit=args.source_commit,account_ref=spec['account_ref_sha256'],now_ms=installer.now_ms())
            install_successor(materials,files,registry_preimage=registry_preimage,stage=stage)
            print(json.dumps(read(stage/'result.json')),flush=True)
        if args.run_cycle:
            from tools.demo_functional_cycle import main as cycle_main
            return cycle_main(['--source-commit',args.source_commit])
        return 0
    except Exception:
        print(json.dumps(dict(schema='DEMO_SUCCESSOR_SETUP_RESULT_V1',status='NOT_COMPLETED',phase=phase,
                              orders_submitted=False,economic_authority=False)));return 3
    finally:os.umask(previous)


if __name__=='__main__':raise SystemExit(main())
