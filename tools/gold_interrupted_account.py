"""Reproduce an interrupted producer's last published account for postmortem use.

The original process must have exited. Missing market intervals stay missing;
this neither resumes an account nor resets its cumulative loss state.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat

from services.governance.json_integrity import load_json_without_duplicate_keys as decode
from tools.gold_account_archive import archive_review, _save
from tools.gold_account_chain import _history
from tools.gold_session_io import read


def _exited(monitor):
    pid, tick = monitor.get('pid'), monitor.get('start_ticks')
    if type(pid) is not int or pid <= 0 or not isinstance(tick, str) or not tick.isdigit():
        raise ValueError('INVALID_PRODUCER_IDENTITY')
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    except FileNotFoundError:
        return
    if fields[19] == tick and fields[0] != 'Z':
        raise ValueError('PRODUCER_STILL_RUNNING')


def finalize(*, chain, monitor, terminal, output):
    chain, monitor, terminal, output = (Path(p).absolute() for p in
                                        (chain, monitor, terminal, output))
    if any(p.resolve() != p for p in (chain, monitor, terminal, output)):
        raise ValueError('INTERRUPTED_ACCOUNT_UNSAFE_PATH')
    if output == chain or chain in output.parents or output in chain.parents:
        raise ValueError('INTERRUPTED_ACCOUNT_OVERLAP')
    monitor_raw, terminal_raw = read(monitor.parent, monitor.name), read(terminal.parent, terminal.name)
    process, failure = decode(monitor_raw), decode(terminal_raw)
    _exited(process)
    if (failure.get('status') != 'INCOMPLETE' or failure.get('submission_enabled') is not False
            or not isinstance(failure.get('reason'), str)
            or not isinstance(failure.get('error_type'), str)):
        raise ValueError('INTERRUPTED_TERMINAL_REQUIRED')
    descriptor = os.open(chain/'writer.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError('INTERRUPTED_ACCOUNT_LOCK_TYPE')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        directories, head = _history(chain)
        sequences = failure.get('completed_sequences')
        if (not directories or type(sequences) is not list
                or any(type(n) is not int for n in sequences)
                or sequences != list(range(1, len(directories)+1))):
            raise ValueError('INTERRUPTED_SEQUENCE_MISMATCH')
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        for name, raw in [('producer-monitor.json', monitor_raw), ('producer-terminal.json', terminal_raw)]:
            with (output/name).open('xb') as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
        _save(output/'binding.json', dict(chain=str(chain), monitor=str(monitor), terminal=str(terminal),
            monitor_sha256=hashlib.sha256(monitor_raw).hexdigest(),
            terminal_sha256=hashlib.sha256(terminal_raw).hexdigest(), reviewed_head=head))
        try:
            reproduction = archive_review(chain, output/'archive')
            if (read(monitor.parent, monitor.name) != monitor_raw
                    or read(terminal.parent, terminal.name) != terminal_raw
                    or _history(chain)[1] != head):
                raise ValueError('INTERRUPTED_EVIDENCE_CHANGED')
            _exited(process)
            report = decode(read(output/'archive/inputs'/directories[-1].name, 'report.json'))
            result = dict(schema='GOLD_INTERRUPTED_ACCOUNT_REVIEW_V1',
                status='INTERRUPTED_ACCOUNT_REPRODUCED', producer_status='INCOMPLETE',
                interruption_reason=failure['reason'], reviewed_head=head,
                published_sequences=len(directories), as_of_ms=decode(read(
                    directories[-1], 'transition.json'))['as_of_ms'],
                reproduction=reproduction, loss_control=report['loss_control'],
                account_resumed=False, economic_eligible=False, submission_enabled=False)
            _save(output/'summary.json', result)
            return result
        except BaseException as exc:
            _save(output/'failure.json', dict(status='INCOMPLETE', error_type=type(exc).__name__,
                reason=str(exc), producer_status='INCOMPLETE', submission_enabled=False))
            raise
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('chain', 'monitor', 'terminal', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    print(json.dumps(finalize(**vars(parser.parse_args()))), flush=True)


if __name__ == '__main__':
    main()
