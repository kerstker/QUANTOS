"""Bounded public quote collection with separate, auditable recovery segments.

A segment returns or raises before another may start. Recovery never joins tapes,
restarts accounts, erases gaps, or changes the original collector's retry budget.
The deadline prevents new starts; an in-flight bounded HTTP request may finish later.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import ssl
import time
from urllib.error import HTTPError, URLError

from tools.gold_quote_session import run as run_segment
from tools.perpetual_replay_research import source_identity


def _retryable(exc):
    if isinstance(exc, HTTPError):
        return False
    reason = exc.reason if isinstance(exc, URLError) else exc
    return (isinstance(reason, (TimeoutError, ConnectionError, ssl.SSLEOFError))
            or isinstance(reason, socket.gaierror) and reason.errno == socket.EAI_AGAIN)


def _save(path, data):
    raw = (json.dumps(data, sort_keys=True, allow_nan=False) + '\n').encode()
    with path.open('xb') as handle:
        os.chmod(handle.fileno(), 0o600)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return hashlib.sha256(raw).hexdigest()


def run(output, *, seconds=86400, segment_seconds=3600, interval_seconds=10,
        max_segments=32, retry_seconds=30):
    bounds = ((seconds, 30, 86400), (segment_seconds, 30, 86400),
              (interval_seconds, 1, 10), (max_segments, 1, 96),
              (retry_seconds, 1, 300))
    if any(type(value) is not int or not low <= value <= high
           for value, low, high in bounds):
        raise ValueError('INVALID_QUOTE_CAMPAIGN_BUDGET')
    output = Path(output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    identity = source_identity()
    start = time.monotonic()
    deadline = start + seconds
    plan = dict(schema='GOLD_PUBLIC_QUOTE_CAMPAIGN_PLAN_V1', seconds=seconds,
        segment_seconds=segment_seconds, interval_seconds=interval_seconds,
        max_segments=max_segments, retry_seconds=retry_seconds, source=identity,
        started_at_ms=time.time_ns() // 1000000, submission_enabled=False)
    _save(output/'plan.json', plan)
    segments = []
    reason = 'UNEXPECTED_FAILURE'
    try:
        for index in range(1, max_segments + 1):
            remaining = math.ceil(deadline - time.monotonic())
            if remaining < 30:
                reason = 'DURATION_COMPLETE'
                break
            if source_identity() != identity:
                reason = 'SOURCE_CHANGED'
                raise ValueError('CAMPAIGN_SOURCE_CHANGED')
            directory = output/f'segment-{index:04d}'
            duration = min(segment_seconds, remaining)
            started = time.time_ns() // 1000000
            _save(output/f'segment-{index:04d}-started.json', dict(
                directory=directory.name, seconds=duration, started_at_ms=started,
                submission_enabled=False))
            error = None
            try:
                run_segment(directory, seconds=duration, interval_seconds=interval_seconds)
            except BaseException as exc:
                error = exc
            terminal = directory/('failure.json' if error else 'summary.json')
            raw = terminal.read_bytes()  # missing terminal evidence stops recovery
            data = json.loads(raw)
            expected = 'INCOMPLETE' if error else 'RAW_QUOTES_COLLECTED'
            if (data.get('status') != expected or data.get('source') != identity
                    or data.get('submission_enabled') is not False):
                raise ValueError('QUOTE_SEGMENT_TERMINAL_MISMATCH')
            row = dict(directory=directory.name, started_at_ms=started,
                ended_at_ms=time.time_ns() // 1000000, status=expected,
                terminal=terminal.name, terminal_sha256=hashlib.sha256(raw).hexdigest(),
                error_type=type(error).__name__ if error else None,
                retryable=_retryable(error) if error else False)
            _save(output/f'segment-{index:04d}-result.json', row)
            segments.append(row)
            if error and not row['retryable']:
                reason = 'INTERRUPTED' if isinstance(error, (KeyboardInterrupt, SystemExit)) else 'NONRECOVERABLE_FAILURE'
                raise error
            if index == max_segments:
                reason = 'SEGMENT_LIMIT'
                break
            if error:
                time.sleep(min(retry_seconds, max(0, deadline - time.monotonic())))
        if time.monotonic() >= deadline:
            reason = 'DURATION_COMPLETE'
    finally:
        result = dict(schema='GOLD_PUBLIC_QUOTE_CAMPAIGN_SUMMARY_V1',
            status='SEGMENTS_OBSERVED' if segments and all(
                row['status'] == 'RAW_QUOTES_COLLECTED' for row in segments)
                and reason == 'DURATION_COMPLETE' else 'PARTIAL_OBSERVATIONS',
            reason=reason, segments=segments, source=identity,
            elapsed_seconds=time.monotonic() - start,
            unstarted_tail_seconds=max(0, deadline - time.monotonic()),
            continuous=False, replay_ready=False, submission_enabled=False)
        _save(output/'summary.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=86400)
    parser.add_argument('--segment-seconds', type=int, default=3600)
    parser.add_argument('--max-segments', type=int, default=32)
    parser.add_argument('--retry-seconds', type=int, default=30)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.output, seconds=args.seconds,
        segment_seconds=args.segment_seconds, max_segments=args.max_segments,
        retry_seconds=args.retry_seconds)), flush=True)


if __name__ == '__main__':
    main()
