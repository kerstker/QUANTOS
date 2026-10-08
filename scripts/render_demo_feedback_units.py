"""Render disabled feedback retry units and their exact maintenance enrollment.

Review artifacts only: never installs, starts, enables, or reads credentials.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat


def render(*,release_commit,research_state_root,executor_uid=None,research_uid=None,research_gid=None):
    if type(release_commit) is not str or re.fullmatch('[a-f0-9]{40}',release_commit) is None:
        raise ValueError('FEEDBACK_UNIT_RELEASE')
    root=research_state_root
    if (type(root) is not str or re.fullmatch('/[A-Za-z0-9_./-]+',root) is None or
        not root.startswith(('/home/','/srv/')) or not root.endswith('/state') or any(p in ('.','..') for p in root.split('/')[1:]) or Path(root).as_posix()!=root):
        raise ValueError('FEEDBACK_UNIT_RESEARCH_ROOT')
    release='/opt/quantos/releases/'+release_commit
    python='/opt/quantos/venvs/'+release_commit+'/bin/python'
    service=f'''[Unit]
Description=QUANTOS completed Demo feedback delivery (no orders)
ConditionPathExists=/etc/quantos/demo-observation/feedback-automation.json
ConditionPathExists=/etc/quantos/demo-observation/research-feedback.json

[Service]
Type=oneshot
User=0
Group=0
UMask=0077
Environment=PYTHONPATH={release}
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=LIVE_TRADING_ENABLED=false
Environment=REAL_BROKER_ENABLED=false
Environment=ALLOW_ORDER_SUBMISSION=false
Environment=ALLOW_AI_DIRECT_TRADING=false
Environment=REQUIRE_HUMAN_APPROVAL=true
WorkingDirectory=/var/lib/quantos-demo-controller
ExecStart={python} -P -m tools.demo_functional_cycle --source-commit {release_commit} --feedback-pending
TimeoutStartSec=4min
TimeoutStopSec=15s
KillMode=control-group
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
ReadOnlyPaths={release} /var/lib/quantos-demo-executor
ReadWritePaths=/var/lib/quantos-demo-controller {root}
InaccessiblePaths=-/etc/quantos/demo-submission/credentials.json -/etc/quantos/demo-issuer/signing-key.json

# Review artifact only; enrollment and installation must precede activation.
'''
    timer='''[Unit]
Description=QUANTOS pending Demo feedback retry
ConditionPathExists=/etc/quantos/demo-observation/feedback-automation.json

[Timer]
OnBootSec=2min
OnUnitInactiveSec=5min
AccuracySec=10s
Unit=quantos-demo-feedback.service

# Review artifact only; no automatic enablement.
'''
    from tools.demo_maintenance_services import FEEDBACK_UNITS
    profile={'schema':'DEMO_FEEDBACK_AUTOMATION_V1','source_commit':release_commit,'units':list(FEEDBACK_UNITS)}
    files={'quantos-demo-feedback.service':service,'quantos-demo-feedback.timer':timer,
        'feedback-automation.json':json.dumps(profile,sort_keys=True,indent=2)+'\n'}
    if any(value is not None for value in (executor_uid,research_uid,research_gid)):
        from tools.demo_feedback_publish import validate_configuration
        publication=validate_configuration({'schema':'DEMO_RESEARCH_FEEDBACK_PUBLICATION_V1',
            'source_commit':release_commit,'executor_uid':executor_uid,'research_uid':research_uid,
            'research_gid':research_gid,'research_state_root':root},source_commit=release_commit)
        files['research-feedback.json']=json.dumps(publication,sort_keys=True,indent=2)+'\n'
    return files


def upgrade_preimages(directory):
    """Read supplied review copies, never claim protected host custody."""
    from tools.demo_feedback_upgrade import MAXIMUM, TARGETS
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    result = {}
    try:
        for name in TARGETS:
            try:
                leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            except FileNotFoundError:
                result[name] = None
                continue
            with os.fdopen(leaf, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_size > MAXIMUM:
                    raise ValueError('FEEDBACK_UPGRADE_PREIMAGE_FILE')
                body = stream.read(MAXIMUM + 1)
                after = os.fstat(stream.fileno())
                if len(body) != before.st_size or any(getattr(before, key) != getattr(after, key)
                        for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
                    raise ValueError('FEEDBACK_UPGRADE_PREIMAGE_CHANGED')
                result[name] = body
    finally:
        os.close(descriptor)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-commit',required=True);parser.add_argument('--research-state-root',required=True)
    for name in ('executor-uid','research-uid','research-gid'):parser.add_argument('--'+name,type=int)
    parser.add_argument('--upgrade-preimages-dir', type=Path,
        help='Supplied prior-file copies; missing entries claim absence. Host custody remains unverified.')
    parser.add_argument('--output-dir',type=Path,required=True);args=parser.parse_args(argv)
    files=render(release_commit=args.release_commit,research_state_root=args.research_state_root,
        executor_uid=args.executor_uid,research_uid=args.research_uid,research_gid=args.research_gid)
    upgrade = None
    if args.upgrade_preimages_dir is not None:
        from tools.demo_feedback_upgrade import review
        upgrade = review(files={name: body.encode() for name, body in files.items()},
                         preimages=upgrade_preimages(args.upgrade_preimages_dir),
                         release_commit=args.release_commit)
        files['upgrade-review.json'] = json.dumps(upgrade, sort_keys=True, indent=2) + '\n'
    args.output_dir.mkdir(mode=0o700,parents=True,exist_ok=False)
    for name,value in files.items():
        fd=os.open(args.output_dir/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as handle:handle.write(value.encode());handle.flush();os.fsync(handle.fileno())
    report={'schema':'DEMO_FEEDBACK_UNIT_REVIEW_V1','release_commit':args.release_commit,
        'files':{name:hashlib.sha256(value.encode()).hexdigest() for name,value in files.items()},
        'publication_profile_included':'research-feedback.json' in files,
        'installed':False,'enabled':False,'started':False,'submission_enabled':False}
    (args.output_dir/'review.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    print(json.dumps(report,sort_keys=True));return 0


if __name__=='__main__':raise SystemExit(main())
