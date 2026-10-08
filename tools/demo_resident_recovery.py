"""Run protected original-ID recovery; no new orders, cancellations or signing.

The fixed authority and original state must already be installed independently.
Missing stores are never silently created or initialized. --serve keeps a single
process alive; all query attempts remain bounded and paced across restarts.
"""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import signal
import stat
import threading

from live.execution import demo_authority, demo_prepared_execution as executor
from live.execution.demo_dispatch_store import DemoDispatchStore
from live.execution.demo_resident_recovery import RecoveryLoop
from live.execution.order_event_store import SqliteOrderEventStore
from live.execution.order_recovery import SqliteOrderRecoveryStore
from live.execution.position_ledger import LiveLedger
from services.research.demo_user_stream import UserStreamJournal
from services.research.demo_resident_status import build_status, save as save_status

STATE_FILES = ('dispatch.sqlite3', 'recovery.sqlite3', 'oms.sqlite3', 'order-events.sqlite3', 'user-stream.sqlite3')
STATE_DIRECTORIES = ('evidence', 'resident-recovery')


def _check_path(path, uid, *, directory):
    info = path.lstat()
    if (info.st_uid != uid or info.st_mode & 0o022 or
        not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) or
        not directory and info.st_nlink != 1):
        raise ValueError('RESIDENT_STATE_CUSTODY')


def require_custody():
    registry = demo_authority.load_registry_for_executor()
    root = executor._STATE_ROOT
    for parent in reversed(root.parents):
        demo_authority._require_protected(parent.lstat(), directory=True)
    _check_path(root, registry['executor_uid'], directory=True)
    for name in STATE_DIRECTORIES:
        _check_path(root/name, registry['executor_uid'], directory=True)
    for name in STATE_FILES:
        _check_path(root/name, registry['executor_uid'], directory=False)
        for suffix in ('-wal', '-shm', '-journal'):
            path = root/(name+suffix)
            if path.exists() or path.is_symlink():
                _check_path(path, registry['executor_uid'], directory=False)
    return root


def _open(stack, root):
    values = {}
    for name, factory in (('dispatch', DemoDispatchStore), ('recovery', SqliteOrderRecoveryStore),
                          ('oms', LiveLedger), ('event_store', SqliteOrderEventStore)):
        filename = 'order-events' if name == 'event_store' else name
        store = factory(str(root/(filename+'.sqlite3')))
        stack.callback(store.close)
        values[name] = store
    values['stream'] = stack.enter_context(UserStreamJournal(root/'user-stream.sqlite3', read_only=True))
    return values


def run(*, source_commit, serve, stopped):
    if type(source_commit) is not str or re.fullmatch('[a-f0-9]{40}', source_commit) is None:
        raise ValueError('RESIDENT_SOURCE_ID')
    # Do not change the original files. Any new reports/SQLite sidecars stay
    # private to the executor identity; no credential path is opened here.
    previous = os.umask(0o077)
    try:
        root = require_custody()
        with ExitStack() as stack:
            stores = _open(stack, root)
            loop = RecoveryLoop(**stores, evidence_directory=root/'evidence',
                output_directory=root/'resident-recovery', source_commit=source_commit)
            last = None
            published_at = None
            def publish(report):
                nonlocal published_at
                summary = {k: report[k] for k in ('schema', 'status', 'reason', 'reservation_sha256',
                    'new_order_submission_enabled', 'cash_reconciled', 'economic_authority')}
                if summary != last or published_at is None or report['observed_at_ms']-published_at >= 15000:
                    view = build_status(report, executor_boot_sha256=demo_authority.executor_boot_sha256(), source_commit=source_commit)
                    save_status(root/'resident-recovery/views', view)
                    if report['query'] is not None or report['reason'] == 'RESIDENT_QUERY_OR_PROJECTION_STOPPED':
                        save_status(root/'resident-recovery/outcomes', view)
                    published_at = report['observed_at_ms']
            while not stopped.is_set():
                require_custody()
                report = loop.tick(publish=publish)
                summary = {k: report[k] for k in ('schema', 'status', 'reason', 'reservation_sha256',
                    'new_order_submission_enabled', 'cash_reconciled', 'economic_authority')}
                changed = summary != last
                if changed:
                    print(json.dumps(summary, sort_keys=True), flush=True)
                    last = summary
                if not serve:
                    return 2 if report['status'] in ('INPUTS_UNRESOLVED', 'RESIDENT_BUSY') else 0
                stopped.wait(1)
        return 0
    finally:
        os.umask(previous)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True, help='Full frozen source identity, provenance only.')
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args(argv)
    stopped = threading.Event()
    previous = {}
    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, lambda *_: stopped.set())
        return run(source_commit=args.source_commit, serve=args.serve, stopped=stopped)
    except Exception:
        print(json.dumps({'status': 'INPUTS_UNRESOLVED', 'reason': 'RESIDENT_START_OR_CUSTODY_FAILED',
            'new_order_submission_enabled': False, 'cash_reconciled': False}), flush=True)
        return 3
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == '__main__':
    raise SystemExit(main())
