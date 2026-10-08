"""Offline day replay of a frozen Demo account evidence bundle; no execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from services.governance.json_integrity import load_json_without_duplicate_keys
from services.research.demo_account_recovery import reconcile_days

MAX_INPUT_BYTES = 32 * 1024**2
FIELDS = {'opening', 'closing', 'statements', 'windows', 'system_orders',
          'adv_notional', 'participation_rate'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    fd = os.open(args.input, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_INPUT_BYTES:
            raise ValueError('ACCOUNT_DAYS_INPUT_SIZE_OR_TYPE')
        body = source.read(MAX_INPUT_BYTES + 1)
        after = os.fstat(source.fileno())
        if len(body) != before.st_size or any(getattr(before, k) != getattr(after, k)
                for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')):
            raise ValueError('ACCOUNT_DAYS_INPUT_CHANGED')
    bundle = load_json_without_duplicate_keys(body)
    if type(bundle) is not dict or set(bundle) != FIELDS:
        raise ValueError('ACCOUNT_DAYS_INPUT_FIELDS')
    report, _ = reconcile_days(**bundle)
    report = dict(report, input_sha256=hashlib.sha256(body).hexdigest(),
                  scope='TECHNICAL_VALIDATION_ONLY', authoritative=False)
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False).encode() + b'\n'
    output = args.output.absolute()
    if any(p.is_symlink() for p in output.parents):
        raise ValueError('ACCOUNT_DAYS_OUTPUT_PARENT')
    # The destination is new and fully fsynced; no old report can be replaced.
    fd, name = tempfile.mkstemp(prefix='.account-days-', dir=output.parent)
    try:
        with os.fdopen(fd, 'wb') as target:
            target.write(payload); target.flush(); os.fsync(target.fileno())
        os.link(name, output)
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(name)
    print(json.dumps({'status': report['status'], 'days': len(report['daily_cash']),
                      'scope': report['scope'], 'submission_enabled': False}))
    return 0 if report['status'] == 'REPORTED_ACCOUNT_MATCHED' else 2


if __name__ == '__main__':
    raise SystemExit(main())
