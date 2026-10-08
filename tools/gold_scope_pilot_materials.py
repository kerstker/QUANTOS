"""Prepare an original disabled pilot handoff from an installed signed Scope.

Only public scope/registry metadata is retained. No approval, installation,
baseline, service, key, grant, credential or order operation is performed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

from live.execution.demo_batch_risk import _digest
from live.execution.demo_gold_budget_scope import pilot_window
from scripts.render_gold_pilot_unit import render
from tools import demo_supervisor as supervisor
from tools.gold_strategy_controller import observation_configuration

SCHEMA='GOLD_SCOPE_PILOT_UNIT_REVIEW_V1'


def assemble(*,scope,registry,source_commit,research_state_root,execute_approved,now_ms):
    pilot_window(scope,registry=registry,now_ms=now_ms)
    result=render(release_commit=source_commit,account_ref_sha256=scope['account_ref_sha256'],
        research_state_root=research_state_root,execute_approved=execute_approved,
        budget_scope=scope,scope_registry=registry)
    files={'quantos-gold-pilot.service':result['service'].encode(),
        'gold-pilot-request.json':(json.dumps(result['enrollment_request'],sort_keys=True,allow_nan=False)+'\n').encode()}
    manifest=dict(schema=SCHEMA,status='PREPARED_NOT_APPLIED',release_commit=source_commit,
        account_ref_sha256=scope['account_ref_sha256'],files={name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()},
        execution_requested=execute_approved,installed=False,started=False,software_acceptance_verified=False,
        independent_policy_verified=False,budget_scope_sha256=_digest(scope),scope_registry_sha256=_digest(registry),
        remaining=['Verify accepted artifact and installed signed budget Scope',
            'Install request without replacing an existing pilot baseline',
            'Retain actual executor opening within the original 300 second window',
            'Authorize installation and activation separately; exact orders require approval'])
    if any(len(raw)>65536 for raw in [*files.values(),json.dumps(manifest,allow_nan=False).encode()]):
        raise ValueError('GOLD_SCOPE_PILOT_MATERIAL_BUDGET')
    return manifest,files


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit',required=True)
    parser.add_argument('--research-state-root',required=True)
    parser.add_argument('--execute-approved',action='store_true')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv);old=os.umask(0o077)
    try:
        with supervisor.controller_lease(maintenance=True):
            registry,scope=observation_configuration(args.source_commit)
            manifest,files=assemble(scope=scope,registry=registry,source_commit=args.source_commit,
                research_state_root=args.research_state_root,execute_approved=args.execute_approved,now_ms=supervisor.now_ms())
            if observation_configuration(args.source_commit)!=(registry,scope):raise ValueError('GOLD_SCOPE_PILOT_INPUTS_CHANGED')
            pilot_window(scope,registry=registry,now_ms=supervisor.now_ms())
            args.output_dir.mkdir(parents=True,exist_ok=False,mode=0o700)
            for name,raw in files.items():
                with (args.output_dir/name).open('xb') as out:out.write(raw)
            with (args.output_dir/'review.json').open('x') as out:json.dump(manifest,out,sort_keys=True,allow_nan=False)
        print(json.dumps(dict(status='PREPARED_NOT_APPLIED',review=manifest,installed=False,started=False,economic_authority=False)));return 0
    except Exception as error:
        print(json.dumps(dict(status='NOT_PREPARED',error_type=type(error).__name__,installed=False,started=False,economic_authority=False)));return 3
    finally:os.umask(old)


if __name__=='__main__':raise SystemExit(main())
