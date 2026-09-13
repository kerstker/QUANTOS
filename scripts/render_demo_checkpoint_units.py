"""Render separate, disabled source-owner capture and secret-holding seal units."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def render(*, release_commit, namespace, source_uid, source_dir):
    if type(release_commit) is not str or re.fullmatch('[a-f0-9]{40}', release_commit) is None:
        raise ValueError('CYCLE_UNIT_RELEASE')
    if namespace not in ('research', 'executor', 'issuer', 'controller'):
        raise ValueError('CYCLE_UNIT_NAMESPACE')
    if type(source_uid) is not int or not (source_uid == 0 if namespace == 'controller' else 1000 <= source_uid <= 2147483647):
        raise ValueError('CYCLE_UNIT_UID')
    if type(source_dir) is not str or re.fullmatch('/[A-Za-z0-9_./-]+', source_dir) is None or any(p in ('.', '..') for p in source_dir.split('/')[1:]):
        raise ValueError('CYCLE_UNIT_SOURCE')
    if Path(source_dir).as_posix() != source_dir:
        raise ValueError('CYCLE_UNIT_SOURCE')
    if namespace == 'research':
        if not source_dir.startswith('/home/') or not source_dir.endswith('/state/data/cache'):
            raise ValueError('CYCLE_UNIT_RESEARCH_SOURCE')
    elif source_dir != '/var/lib/quantos-demo-' + namespace:
        raise ValueError('CYCLE_UNIT_FIXED_SOURCE')
    release = '/opt/quantos/releases/' + release_commit
    python = '/opt/quantos/venvs/' + release_commit + '/bin/python'
    spool = '/var/lib/quantos-demo-checkpoint-captures/' + namespace
    backup = '/mnt/quantos-backup/demo-namespaces/' + namespace
    prefix = 'quantos-demo-checkpoint-' + namespace
    environment = f'''Environment=PYTHONPATH={release}
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=LIVE_TRADING_ENABLED=false
Environment=REAL_BROKER_ENABLED=false
Environment=ALLOW_ORDER_SUBMISSION=false
Environment=ALLOW_AI_DIRECT_TRADING=false
Environment=REQUIRE_HUMAN_APPROVAL=true
'''
    common = f'''UMask=0077
{environment}ExecStartPre={python} -P -m tools.demo_checkpoint_cycle verify-release --source-commit {release_commit}
KillMode=control-group
TimeoutStopSec=15s
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
AmbientCapabilities=
'''
    capture = f'''[Unit]
Description=QUANTOS Demo {namespace} private capture (no backup secret)

[Service]
Type=oneshot
User={source_uid}
Group={source_uid}
{common}WorkingDirectory={spool}
CapabilityBoundingSet=
ReadWritePaths={source_dir} {spool}
InaccessiblePaths=-/etc/quantos -/run/credentials
TimeoutStartSec=1h
ExecStart={python} -P -m tools.demo_checkpoint_cycle capture --namespace {namespace} --source-dir {source_dir} --source-commit {release_commit} --spool {spool}{' --observation-prefix' if namespace == 'research' else ''}
'''
    seal = f'''[Unit]
Description=QUANTOS Demo {namespace} encrypt and fully restore pending captures
RequiresMountsFor=/mnt/quantos-backup
ConditionPathIsMountPoint=/mnt/quantos-backup

[Service]
Type=oneshot
User=0
Group=0
{common}WorkingDirectory={backup}
CapabilityBoundingSet=CAP_DAC_READ_SEARCH
ReadOnlyPaths={spool}
ReadWritePaths={backup}
LoadCredential=l2-backup-passphrase:/etc/quantos/credentials/l2-backup-passphrase
InaccessiblePaths=-/etc/quantos/demo-submission -/etc/quantos/demo-issuer
TimeoutStartSec=2h
ExecStart={python} -P -m tools.demo_checkpoint_cycle seal --namespace {namespace} --spool {spool} --backup {backup} --source-uid {source_uid} --pending
ExecStartPost={python} -P -m tools.demo_checkpoint_cycle status --namespace {namespace} --spool {spool} --backup {backup} --source-uid {source_uid} --rpo-seconds 86400
'''
    def timer(role, first, period):
        return f'''[Unit]
Description=QUANTOS Demo {namespace} checkpoint {role} schedule

[Timer]
OnActiveSec={first}
OnUnitInactiveSec={period}
AccuracySec=5s
Unit={prefix}-{role}.service
'''
    return {prefix + '-capture.service': capture, prefix + '-seal.service': seal,
            prefix + '-capture.timer': timer('capture', '1min', '12h'),
            prefix + '-seal.timer': timer('seal', '3min', '15min')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-commit', required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--source-uid', type=int, required=True)
    parser.add_argument('--source-dir', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    units = render(release_commit=args.release_commit, namespace=args.namespace,
                   source_uid=args.source_uid, source_dir=args.source_dir)
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    hashes = {}
    for name, body in units.items():
        with (args.output_dir / name).open('x') as handle:
            handle.write(body)
        hashes[name] = hashlib.sha256(body.encode()).hexdigest()
    receipt = dict(schema='DEMO_CHECKPOINT_UNITS_V1', release_commit=args.release_commit,
                   namespace=args.namespace, source_uid=args.source_uid, unit_sha256=hashes,
                   installed=False, started=False, enabled=False, source_full_acceptance=False,
                   submission_enabled=False, economic_authority=False)
    (args.output_dir / 'review.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
