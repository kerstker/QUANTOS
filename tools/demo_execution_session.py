"""One finite protected Demo submission or cancellation handoff over local stdio.

The independently installed executor identity and original stores are required.
Input is one complete request JSON line, followed only after the proposal by one
signed grant JSON line. EOF, invalid material, timeout or restart stops this
session; recovery uses the original persisted IDs. No network listener, signer,
credential override, implicit store initialization or automatic retry is added.
"""
import argparse
from contextlib import ExitStack
import json
import hashlib
import os
from pathlib import Path
import re
import select
import sys
import time

from live.execution.demo_batch_risk import _fields
from live.execution.demo_execution_session import ExecutionSession
from live.execution.demo_cancellation_session import CancellationSession
from live.execution.demo_cancel_store import DemoCancelStore
from live.execution.demo_pre_submit import _retain
from live.execution.demo_prepared_execution import now_ms
from live.execution.demo_session_inputs import MAX_INPUT_BYTES
from services.governance.json_integrity import load_json_without_duplicate_keys
from tools.demo_resident_recovery import require_custody, _check_path, _open


def read_frame(fd, *, maximum, deadline):
    chunks = bytearray()
    while True:
        remaining = deadline-time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise ValueError('SESSION_INPUT_TIMEOUT')
        chunk = os.read(fd, min(65536, maximum+1-len(chunks)))
        if not chunk:
            raise ValueError('SESSION_INPUT_EOF')
        chunks.extend(chunk)
        if len(chunks) > maximum:
            raise ValueError('SESSION_FRAME_BUDGET')
        if b'\n' in chunks:
            body, extra = chunks.split(b'\n', 1)
            if extra or not body:
                raise ValueError('SESSION_ONE_FRAME_REQUIRED')
            return bytes(body)


def _prefix(purpose):
    if purpose not in ('submission','cancellation'):raise ValueError('SESSION_PURPOSE')
    return 'DEMO_EXECUTION_SESSION' if purpose=='submission' else 'DEMO_CANCELLATION_SESSION'


def _open_cancel(stack,root):
    path=root/'cancellations.sqlite3'
    _check_path(path,root.stat().st_uid,directory=False)
    for suffix in ('-wal','-shm','-journal'):
        sidecar=root/('cancellations.sqlite3'+suffix)
        if sidecar.exists() or sidecar.is_symlink():_check_path(sidecar,root.stat().st_uid,directory=False)
    store=DemoCancelStore(path)
    stack.callback(store.close)
    return store


def parse_grant(raw,*,purpose='submission'):
    prefix=_prefix(purpose)
    if type(raw) is not bytes or len(raw) > 65536:
        raise ValueError('SESSION_GRANT_BUDGET')
    data = load_json_without_duplicate_keys(raw)
    _fields(data, 'schema proposal_sha256 envelope')
    if (data['schema'] != prefix+'_GRANT_V1' or
        type(data['proposal_sha256']) is not str or
        re.fullmatch('[a-f0-9]{64}', data['proposal_sha256']) is None or type(data['envelope']) is not dict):
        raise ValueError('SESSION_GRANT_SHAPE')
    return data['proposal_sha256'], json.dumps(data['envelope'], separators=(',', ':'), allow_nan=False).encode()


def run(*, source_commit, input_fd=None, output=None,purpose='submission'):
    prefix=_prefix(purpose)
    if type(source_commit) is not str or re.fullmatch('[a-f0-9]{40}', source_commit) is None:
        raise ValueError('SESSION_SOURCE_ID')
    previous = os.umask(0o077)
    current = None
    destination = None
    phase = 'CUSTODY'
    request_hash = proposal_hash = None
    channel = sys.stdout if output is None else output
    try:
        root = require_custody()
        destination = root/('session-results' if purpose=='submission' else 'cancellation-session-results')
        destination.mkdir(mode=0o700, exist_ok=True)
        _check_path(destination, root.stat().st_uid, directory=True)
        count = size = 0
        for entry in destination.iterdir():
            _check_path(entry, root.stat().st_uid, directory=False)
            count += 1; size += entry.stat().st_size
            if count >= 1000 or size >= 64*1024*1024:
                raise ValueError('SESSION_RESULT_BUDGET')
        fd = sys.stdin.fileno() if input_fd is None else input_fd
        with ExitStack() as stack:
            stores = _open(stack, root)
            session_stores={k:stores[k] for k in ('dispatch','recovery','oms')}
            if purpose=='cancellation':session_stores['cancel_store']=_open_cancel(stack,root)
            factory=ExecutionSession if purpose=='submission' else CancellationSession
            current = factory(**session_stores,evidence_directory=root/'evidence')
            phase = 'WAITING_INPUT'
            raw = read_frame(fd, maximum=MAX_INPUT_BYTES if purpose=='submission' else 16384, deadline=time.monotonic()+30)
            request_hash = hashlib.sha256(raw).hexdigest()
            phase = 'PREPARING_PROPOSAL'
            proposal = current.propose(raw, now_ms=now_ms())
            proposal_hash = proposal['proposal_sha256']
            phase = 'WAITING_INDEPENDENT_GRANT'
            print(json.dumps(proposal, sort_keys=True, separators=(',', ':')), file=channel, flush=True)
            grant = read_frame(fd, maximum=65536, deadline=time.monotonic()+30)
            identity, envelope = parse_grant(grant,purpose=purpose)
            require_custody()
            phase = 'CONSUMING_AND_EXECUTING'
            result = current.submit(identity, envelope)
            result.update(source_commit=source_commit, observed_at_ms=now_ms())
            _retain(destination, result)
            print(json.dumps(result, sort_keys=True, separators=(',', ':')), file=channel, flush=True)
            return 0
    except Exception:
        # Lost output after a possible POST/DELETE is unresolved, never an
        # instruction to retry. Original dispatch/cancellation stores own it.
        result = {'schema': prefix+'_STOP_V1',
            'status': 'INPUTS_OR_EXECUTION_UNRESOLVED', 'phase': phase,
            'source_commit': source_commit, 'observed_at_ms': now_ms(),
            'request_sha256': request_hash, 'proposal_sha256': proposal_hash,
            'retry_submission': False, 'cash_reconciled': False, 'economic_authority': False,
            'stop_record_persisted': False}
        if destination is not None and phase != 'CUSTODY':
            try:
                record = dict(result, stop_record_persisted=True)
                _retain(destination, record)
                result = record
            except Exception:
                pass
        print(json.dumps(result, sort_keys=True, separators=(',', ':')), file=channel, flush=True)
        return 3
    finally:
        if current is not None:
            current.close()
        os.umask(previous)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', required=True, help='Full source provenance; not approval evidence.')
    parser.add_argument('--purpose',choices=('submission','cancellation'),default='submission')
    args = parser.parse_args(argv)
    try:
        return run(source_commit=args.source_commit,purpose=args.purpose)
    except Exception:
        print(json.dumps({'schema': _prefix(args.purpose)+'_STOP_V1',
            'status': 'INPUTS_OR_EXECUTION_UNRESOLVED', 'reason': 'SESSION_STOPPED_INSPECT_ORIGINAL_RESERVATIONS',
            'retry_submission': False, 'cash_reconciled': False, 'economic_authority': False}), flush=True)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
