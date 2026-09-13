"""Read fixed backup cycle metadata and systemd timers; never decrypt or restore."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

from live.execution.demo_authority import _require_protected
from services.research import demo_backup_status as model
from tools import demo_checkpoint_cycle as cycle
from tools.demo_checkpoint_cycle import verify_release
from tools.demo_protected_publish import configuration, run as publish

SPOOL = Path('/var/lib/quantos-demo-checkpoint-captures')
BACKUP = Path('/mnt/quantos-backup/demo-namespaces')
OUTPUT = Path('/var/lib/quantos-demo-checkpoints')


def now_ms():
    return time.time_ns() // 1_000_000


def volume_available():
    return Path('/mnt/quantos-backup').is_mount()


def timer_status(namespace, role):
    model.require(namespace in model.NAMESPACES and role in ('capture', 'seal'), 'BACKUP_TIMER_SCOPE')
    try:
        result = subprocess.run(['/usr/bin/systemctl', 'show', f'quantos-demo-checkpoint-{namespace}-{role}.timer',
            '-p', 'LoadState', '-p', 'ActiveState'], capture_output=True, text=True, timeout=3, check=False)
        if result.returncode != 0:
            return 'UNKNOWN'
        fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if fields.get('LoadState') == 'not-found':
            return 'NOT_INSTALLED'
        if fields.get('LoadState') != 'loaded':
            return 'UNKNOWN'
        return {'active': 'ACTIVE', 'inactive': 'INACTIVE'}.get(fields.get('ActiveState'), 'UNKNOWN')
    except (OSError, subprocess.TimeoutExpired):
        return 'UNKNOWN'


def observe(cfg, *, source_commit):
    owners = dict(research=cfg['research_uid'], executor=cfg['executor_uid'], issuer=cfg['issuer_uid'], controller=0)
    mounted = volume_available()
    rows = []
    for namespace in model.NAMESPACES:
        row = dict(namespace=namespace, summary=None, status='UNREADABLE',
            capture_timer=timer_status(namespace, 'capture'), seal_timer=timer_status(namespace, 'seal'))
        spool, backup = SPOOL/namespace, BACKUP/namespace
        try:
            if not mounted:
                row['status'] = 'BACKUP_VOLUME_UNAVAILABLE'
            elif not spool.exists() and not spool.is_symlink():
                row['status'] = 'NO_SPOOL'
            elif not backup.exists() and not backup.is_symlink():
                row['status'] = 'NO_BACKUP_DIRECTORY'
            else:
                at = now_ms()
                summary = cycle.summarize(spool, backup, expected_uid=owners[namespace],
                    namespace=namespace, now_ms=at, rpo_seconds=86400)
                model.validate_summary(summary, namespace=namespace, observed_at_ms=at)
                row.update(status='OBSERVED', summary=summary)
        except Exception:
            row.update(status='UNREADABLE', summary=None)
        rows.append(row)
    return model.validate(dict(schema=model.SCHEMA, environment='BINANCE_USDM_DEMO', scope='TECHNICAL_VALIDATION_ONLY',
        source_commit=source_commit, observed_at_ms=now_ms(), phase=model.phase(rows), components=rows, **cycle.SAFETY))


def protected_output(root):
    for parent in reversed(root.parents):
        _require_protected(parent.lstat(), directory=True)
    if not root.exists() and not root.is_symlink():
        root.mkdir(mode=0o700)
    _require_protected(root.lstat(), directory=True)
    if (root/'views').exists() or (root/'views').is_symlink():
        _require_protected((root/'views').lstat(), directory=True)


def run(*, source_commit, publish_display=False):
    verify_release(source_commit)
    model.require(os.geteuid() == 0, 'BACKUP_OBSERVER_ROOT_REQUIRED')
    cfg = configuration(source_commit)
    raw = observe(cfg, source_commit=source_commit)
    protected_output(OUTPUT)
    model.save(OUTPUT/'views', raw)
    if publish_display:
        publish(source_commit=source_commit, kind='backup')
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args(argv)
    previous = os.umask(0o077)
    try:
        raw = run(source_commit=args.source_commit, publish_display=args.publish)
        print(json.dumps(dict(status='BACKUP_STATUS_OBSERVED', phase=raw['phase'],
                              observed_at_ms=raw['observed_at_ms'], **cycle.SAFETY)))
        return 0
    except Exception:
        print(json.dumps(dict(status='BACKUP_STATUS_NOT_COMPLETED', **cycle.SAFETY)))
        return 3
    finally:
        os.umask(previous)


if __name__ == '__main__':
    raise SystemExit(main())
