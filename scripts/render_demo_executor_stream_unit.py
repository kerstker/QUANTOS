"""Render an uninstalled protected Demo event collector for independent review."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from scripts.render_demo_recovery_unit import render as recovery_unit


def render(*,release_commit,executor_uid):
    original=recovery_unit(release_commit=release_commit,executor_uid=executor_uid)
    changes={
        'Description=QUANTOS Binance Demo original-ID recovery (no submission)':
            'Description=QUANTOS Binance Demo protected private event capture (no submission)',
        'ConditionPathExists=/var/lib/quantos-demo-executor/dispatch.sqlite3':
            'ConditionPathExists=/var/lib/quantos-demo-executor/user-stream.sqlite3',
        f'-m tools.demo_resident_recovery --source-commit {release_commit} --serve':
            f'-m tools.demo_executor_stream --source-commit {release_commit}',
        'Restart=on-failure\n':'Restart=always\nRestartPreventExitStatus=78\nRuntimeMaxSec=21700\n',
    }
    for old,new in changes.items():
        if original.count(old)!=1:raise ValueError('DEMO_STREAM_UNIT_TEMPLATE_CHANGED')
        original=original.replace(old,new)
    return original


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-commit',required=True)
    parser.add_argument('--executor-uid',required=True,type=int)
    parser.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args(argv)
    body=render(release_commit=args.release_commit,executor_uid=args.executor_uid).encode()
    args.output_dir.mkdir(parents=True,exist_ok=False,mode=0o700)
    fd=os.open(args.output_dir/'quantos-demo-executor-stream.service',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as handle:handle.write(body);handle.flush();os.fsync(handle.fileno())
    report={'schema':'DEMO_EXECUTOR_STREAM_UNIT_REVIEW_V1','release_commit':args.release_commit,
        'executor_uid':args.executor_uid,'unit_sha256':hashlib.sha256(body).hexdigest(),
        'installed':False,'enabled':False,'started':False,'software_acceptance_verified':False,
        'independent_custody_verified':False,'new_order_submission_enabled':False}
    (args.output_dir/'review.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report));return 0


if __name__=='__main__':raise SystemExit(main())
