"""Prepare disabled gold pilot service and executor enrollment request.

No installation, service action, credential access, approval or policy change.
The existing root controller launches separately isolated executor/issuer units.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re


def render(*,release_commit,account_ref_sha256,research_state_root,execute_approved=False,
           budget_scope=None,scope_registry=None):
    if type(release_commit) is not str or re.fullmatch('[a-f0-9]{40}',release_commit) is None:
        raise ValueError('GOLD_PILOT_UNIT_RELEASE')
    if type(account_ref_sha256) is not str or re.fullmatch('[a-f0-9]{64}',account_ref_sha256) is None:
        raise ValueError('GOLD_PILOT_UNIT_ACCOUNT')
    root=research_state_root
    if (type(root) is not str or re.fullmatch('/(?:home|srv)/[A-Za-z0-9_./-]+/state',root) is None
            or Path(root).as_posix()!=root or any(p in ('.','..') for p in root.split('/'))):
        raise ValueError('GOLD_PILOT_UNIT_RESEARCH_STATE')
    if type(execute_approved) is not bool:raise ValueError('GOLD_PILOT_UNIT_EXECUTION_MODE')
    release='/opt/quantos/releases/'+release_commit
    python='/opt/quantos/venvs/'+release_commit+'/bin/python'
    command=f'{python} -P -B -m tools.gold_pilot_run --source-commit {release_commit} --max-seconds 86400 --interval-seconds 10'
    if execute_approved:command+=' --execute-approved'
    service=f'''[Unit]
Description=QUANTOS finite gold Demo pilot with original independent approval
After=network-online.target
Wants=network-online.target
ConditionPathExists=/etc/quantos/demo-submission/authority.json
ConditionPathExists=/etc/quantos/demo-issuer/policy.json
ConditionPathExists=/var/lib/quantos-demo-controller/strategy-runtime.json
ConditionPathExists=/var/lib/quantos-demo-executor/gold-pilot-request.json

[Service]
Type=simple
User=0
Group=0
UMask=0077
WorkingDirectory=/var/lib/quantos-demo-controller
Environment=PYTHONPATH={release}
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=LIVE_TRADING_ENABLED=false
Environment=REAL_BROKER_ENABLED=false
Environment=ALLOW_ORDER_SUBMISSION=false
Environment=ALLOW_AI_DIRECT_TRADING=false
Environment=REQUIRE_HUMAN_APPROVAL=true
ExecStart={command}
Restart=no
KillSignal=SIGINT
TimeoutStopSec=infinity
SendSIGKILL=no
KillMode=mixed
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=false
PrivateNetwork=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictRealtime=true
RestrictNamespaces=true
RestrictAddressFamilies=AF_UNIX
SystemCallArchitectures=native
CapabilityBoundingSet=CAP_SETUID CAP_SETGID CAP_DAC_READ_SEARCH
AmbientCapabilities=
ReadOnlyPaths={release} /etc/quantos/demo-submission /etc/quantos/demo-issuer
ReadWritePaths=/var/lib/quantos-demo-controller {root}
InaccessiblePaths=-/etc/quantos/demo-submission/credentials.json -/etc/quantos/demo-issuer/signing-key.json

# Review artifact only; no automatic enablement or restart.
'''
    request=dict(schema='GOLD_DEMO_PILOT_ENROLLMENT_REQUEST_V1',source_commit=release_commit,
        account_ref_sha256=account_ref_sha256,duration_seconds=86400,
        max_loss_fraction='0.05',submission_enabled=False)
    if budget_scope is not None or scope_registry is not None:
        from live.execution.demo_gold_pilot_enrollment import scope_request
        request=scope_request(scope=budget_scope,registry=scope_registry,source_commit=release_commit,
            account_ref_sha256=account_ref_sha256)
    return dict(service=service,enrollment_request=request)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-commit',required=True)
    parser.add_argument('--account-ref-sha256',required=True)
    parser.add_argument('--research-state-root',required=True)
    parser.add_argument('--execute-approved',action='store_true')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=vars(parser.parse_args(argv));output=args.pop('output_dir')
    result=render(**args)
    old=os.umask(0o077)
    try:
        output.mkdir(parents=True,exist_ok=False,mode=0o700)
        files={'quantos-gold-pilot.service':result['service'].encode(),
            'gold-pilot-request.json':(json.dumps(result['enrollment_request'],sort_keys=True)+'\n').encode()}
        for name,raw in files.items():
            with (output/name).open('xb') as handle:handle.write(raw)
        review=dict(schema='GOLD_PILOT_UNIT_REVIEW_V1',status='PREPARED_NOT_APPLIED',
            release_commit=args['release_commit'],account_ref_sha256=args['account_ref_sha256'],
            files={name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()},
            execution_requested=args['execute_approved'],installed=False,started=False,
            software_acceptance_verified=False,independent_policy_verified=False,
            remaining=['Verify accepted artifact and existing independent policy scope',
                'Install request with original executor custody without replacing existing pilot',
                'Review service isolation with original controller and feedback publisher',
                'Authorize installation and activation separately'])
        with (output/'review.json').open('x') as handle:json.dump(review,handle,indent=2)
    finally:os.umask(old)
    print(json.dumps(review,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
