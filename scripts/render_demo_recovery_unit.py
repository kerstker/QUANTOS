"""Render a disabled, exact-release Demo recovery unit for independent review.

No service installation, account creation, credential read, trust-root write,
kill-switch initialization or order operation is performed by this command.
This unit is separate from the research/L2 workers: it reads private original-ID
queries and writes protected OMS recovery, and must use the executor identity.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re


def render(*, release_commit, executor_uid):
    if type(release_commit) is not str or re.fullmatch('[a-f0-9]{40}', release_commit) is None:
        raise ValueError('DEMO_UNIT_RELEASE_REQUIRED')
    if type(executor_uid) is not int or not 1000 <= executor_uid <= 2147483647:
        raise ValueError('DEMO_UNIT_SEPARATE_EXECUTOR_UID_REQUIRED')
    release = '/opt/quantos/releases/'+release_commit
    state = '/var/lib/quantos-demo-executor'
    return f'''[Unit]
Description=QUANTOS Binance Demo original-ID recovery (no submission)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5
ConditionPathExists=/etc/quantos/demo-submission/authority.json
ConditionPathExists={state}/dispatch.sqlite3

[Service]
Type=simple
User={executor_uid}
UMask=0077
Environment=LIVE_TRADING_ENABLED=false
Environment=REAL_BROKER_ENABLED=false
Environment=ALLOW_ORDER_SUBMISSION=false
Environment=ALLOW_AI_DIRECT_TRADING=false
Environment=REQUIRE_HUMAN_APPROVAL=true
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONPATH={release}
WorkingDirectory={state}
ExecStart=/opt/quantos/venvs/{release_commit}/bin/python -P -m tools.demo_resident_recovery --source-commit {release_commit} --serve
Restart=on-failure
RestartSec=30s
TimeoutStopSec=75s
KillMode=control-group
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
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
SystemCallArchitectures=native
CapabilityBoundingSet=
AmbientCapabilities=
ReadOnlyPaths={release} /etc/quantos/demo-submission
ReadWritePaths={state}
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6

# Review artifact only: no [Install] section and no automatic enablement.
'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-commit', required=True)
    parser.add_argument('--executor-uid', type=int, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    body = render(release_commit=args.release_commit, executor_uid=args.executor_uid).encode()
    args.output_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    path = args.output_dir/'quantos-demo-recovery.service'
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(body); handle.flush(); os.fsync(handle.fileno())
    receipt = {'schema':'DEMO_RECOVERY_UNIT_REVIEW_V1', 'release_commit':args.release_commit,
        'executor_uid':args.executor_uid, 'unit_sha256':hashlib.sha256(body).hexdigest(),
        'installed':False, 'enabled':False, 'started':False, 'new_order_submission_enabled':False,
        'independent_custody_verified':False, 'software_acceptance_verified':False}
    (args.output_dir/'review.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
